from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from obsidian_wiki.cache import compute_hash
from obsidian_wiki.config import PortableConfig
from obsidian_wiki.safe_files import stable_directory_identity

_SUPPORTS_DIRECTORY_FSYNC = os.name != "nt"


def _manifest_dirfd_supported() -> bool:
    return (
        sys.platform.startswith("linux")
        and _renameat2_available()
        and
        _SUPPORTS_DIRECTORY_FSYNC
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.mkdir in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
        and os.link in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
    )


def _renameat2_available() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return False
    return getattr(libc, "renameat2", None) is not None


@dataclass(frozen=True)
class _ReservedShard:
    name: str
    content_hash: str
    identity: tuple[int, int]


def _rename_noreplace(
    source: str, target: str, *, source_fd: int, target_fd: int
) -> None:
    """Atomically move one directory entry only when the target is absent."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        os.rename(source, target, src_dir_fd=source_fd, dst_dir_fd=target_fd)
        return
    if not sys.platform.startswith("linux"):
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace rename is unavailable on this platform",
        )
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOTSUP, "renameat2 is unavailable")
    result = renameat2(
        source_fd,
        os.fsencode(source),
        target_fd,
        os.fsencode(target),
        1,  # RENAME_NOREPLACE
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _require_noreplace_supported(parent_fd: int) -> None:
    """Probe the actual directory filesystem before any live entry is moved."""
    source = f".noreplace-probe-{secrets.token_hex(16)}"
    target = f".noreplace-probe-{secrets.token_hex(16)}"
    descriptors: list[int] = []
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptors.append(os.open(source, flags, 0o600, dir_fd=parent_fd))
        descriptors.append(os.open(target, flags, 0o600, dir_fd=parent_fd))
        try:
            _rename_noreplace(
                source, target, source_fd=parent_fd, target_fd=parent_fd
            )
        except FileExistsError:
            return
        raise ManifestError(
            "safe manifest no-replace mutation is unavailable on this filesystem"
        )
    except ManifestError:
        raise
    except OSError as exc:
        raise ManifestError(
            "safe manifest no-replace mutation is unavailable on this filesystem"
        ) from exc
    finally:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
        for name in (source, target):
            try:
                os.unlink(name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


class ManifestError(ValueError):
    pass


class ManifestPreconditionError(ManifestError):
    pass


_UNSET_PREIMAGE = object()


@dataclass(frozen=True)
class ManifestEntry:
    source_id: str
    content_hash: str
    pages: tuple[str, ...]
    compiled_at: str


class ShardedManifest:
    def __init__(self, config: PortableConfig) -> None:
        if len(config.sources) != 1:
            raise ManifestError("manifest v2 schema 1 requires exactly one source root")
        self.config = config
        self.source_root = config.sources[0]
        self.marker_path = config.vault / ".manifest.json"
        self.entries_root = config.vault / ".manifest" / "sources"
        self._verify_repository_identity()
        self._vault_identity = self._capture_vault_identity()
        self._validate_marker()

    def _verify_repository_identity(self) -> None:
        with self._bound_repository_root():
            pass

    @contextmanager
    def _bound_repository_root(self) -> Iterator[int]:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(self.config.root, flags)
            opened = os.fstat(descriptor)
        except OSError as exc:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise ManifestError("configured repository root is unsafe") from exc
        if stable_directory_identity(opened) != self.config.root_identity:
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = None
            raise ManifestError(
                "configured repository root changed since configuration was read"
            )
        body_error: BaseException | None = None
        try:
            yield descriptor
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    if body_error is None:
                        raise

    def _validate_root_attachment(self, root_fd: int) -> None:
        try:
            attached = self.config.root.lstat()
            opened = os.fstat(root_fd)
        except OSError as exc:
            raise ManifestError("configured repository root is unsafe") from exc
        if stable_directory_identity(attached) != stable_directory_identity(opened):
            raise ManifestError(
                "configured repository root changed during manifest mutation"
            )

    def _validate_vault_attachment(self) -> None:
        try:
            vault_metadata = self.config.vault.lstat()
        except OSError as exc:
            raise ManifestError("configured vault is unsafe") from exc
        if stable_directory_identity(vault_metadata) != self._vault_identity:
            raise ManifestError("configured vault changed during manifest mutation")

    @staticmethod
    def _same_directory(left: os.stat_result, right: os.stat_result) -> bool:
        return (
            stat.S_ISDIR(left.st_mode)
            and stat.S_ISDIR(right.st_mode)
            and stable_directory_identity(left) == stable_directory_identity(right)
        )

    def _capture_vault_identity(self) -> tuple[int, int]:
        try:
            relative = self.config.vault.relative_to(self.config.root)
        except ValueError as exc:
            raise ManifestError("configured vault escapes repository") from exc
        if not _manifest_dirfd_supported():
            try:
                metadata = self.config.vault.lstat()
            except OSError as exc:
                raise ManifestError("configured vault is unsafe") from exc
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ManifestError("configured vault is unsafe")
            return stable_directory_identity(metadata)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptors: list[int] = []
        with self._bound_repository_root() as root_fd:
            descriptors.append(os.dup(root_fd))
            try:
                for part in relative.parts:
                    parent_fd = descriptors[-1]
                    observed = os.stat(
                        part, dir_fd=parent_fd, follow_symlinks=False
                    )
                    child = os.open(part, flags, dir_fd=parent_fd)
                    descriptors.append(child)
                    opened = os.fstat(child)
                    attached = os.stat(
                        part, dir_fd=parent_fd, follow_symlinks=False
                    )
                    if not self._same_directory(observed, opened) or not (
                        self._same_directory(opened, attached)
                    ):
                        raise ManifestError(
                            "configured vault changed while being bound"
                        )
                self._validate_root_attachment(root_fd)
                return stable_directory_identity(os.fstat(descriptors[-1]))
            except OSError as exc:
                raise ManifestError("configured vault is unsafe") from exc
            finally:
                self._close_directories(descriptors)

    def _validate_bound_parent(
        self,
        root_fd: int,
        target: Path,
        descriptors: list[int],
        *,
        complete: bool = True,
    ) -> None:
        relative = target.parent.relative_to(self.config.root)
        if len(descriptors) > len(relative.parts) + 1 or (
            complete and len(descriptors) != len(relative.parts) + 1
        ):
            raise ManifestError("manifest shard parent binding is incomplete")
        self._validate_root_attachment(root_fd)
        current_parts: list[str] = []
        vault_relative = self.config.vault.relative_to(self.config.root)
        opened_parts = relative.parts[: len(descriptors) - 1]
        for index, part in enumerate(opened_parts, start=1):
            current_parts.append(part)
            try:
                attached = os.stat(
                    part, dir_fd=descriptors[index - 1], follow_symlinks=False
                )
                opened = os.fstat(descriptors[index])
            except OSError as exc:
                raise ManifestError(
                    "manifest shard parent changed during mutation"
                ) from exc
            if not self._same_directory(opened, attached):
                raise ManifestError(
                    "manifest shard parent changed during mutation"
                )
            if tuple(current_parts) == vault_relative.parts and (
                stable_directory_identity(opened) != self._vault_identity
            ):
                raise ManifestError(
                    "configured vault changed during manifest mutation"
                )

    def _open_bound_parent(
        self, root_fd: int, target: Path, *, create: bool
    ) -> list[int]:
        try:
            relative = target.parent.relative_to(self.config.root)
        except ValueError as exc:
            raise ManifestError("manifest shard directory escapes repository") from exc
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptors: list[int] = [os.dup(root_fd)]
        try:
            for part in relative.parts:
                parent_fd = descriptors[-1]
                self._validate_root_attachment(root_fd)
                if create:
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=parent_fd)
                        os.fsync(parent_fd)
                    except FileExistsError:
                        pass
                child = os.open(part, flags, dir_fd=parent_fd)
                descriptors.append(child)
                metadata = os.fstat(child)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ManifestError(
                        "manifest shard directory must be ordinary"
                    )
                self._validate_bound_parent(
                    root_fd, target, descriptors, complete=False
                )
            self._validate_bound_parent(root_fd, target, descriptors)
            return descriptors
        except BaseException:
            self._close_directories(descriptors)
            raise

    @staticmethod
    def _close_directories(descriptors: list[int]) -> None:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _validate_marker(self) -> None:
        try:
            metadata = self.marker_path.lstat()
        except OSError as exc:
            raise ManifestError("invalid manifest v2 marker") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise ManifestError(
                "manifest v2 marker must be a single-link ordinary file"
            )
        try:
            payload = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"invalid manifest v2 marker: {exc}") from exc
        if payload != {
            "entries": ".manifest/sources",
            "schema_version": 2,
            "storage": "sharded",
        }:
            raise ManifestError("invalid manifest v2 marker")

    def _repo_relative(self, path: Path, label: str) -> str:
        try:
            return (
                path.resolve(strict=False)
                .relative_to(self.config.root.resolve())
                .as_posix()
            )
        except ValueError as exc:
            raise ManifestError(f"{label} escapes the repository") from exc

    def source_id(self, source: Path) -> str:
        candidate = Path(source)
        try:
            relative = candidate.resolve(strict=False).relative_to(
                self.source_root.resolve(strict=False)
            )
        except ValueError as exc:
            raise ManifestError("source is outside the configured source root") from exc
        if not relative.parts:
            raise ManifestError(
                "source must be a file below the configured source root"
            )
        return f"{self._repo_relative(self.source_root, 'source root')}/{relative.as_posix()}"

    def validated_source_id(self, source: Path) -> str:
        """Return the Source ID after physical containment and file validation."""
        source_path = Path(source)
        source_id = self.source_id(source_path)
        self._validate_source_file(source_path)
        return source_id

    def source_path(self, source_id: str) -> Path:
        self._validate_source_id(source_id)
        return self.config.root / PurePosixPath(source_id)

    def _validate_source_id(self, source_id: str) -> None:
        if not isinstance(source_id, str) or not source_id or "\\" in source_id:
            raise ManifestError(f"invalid Source ID: {source_id!r}")
        path = PurePosixPath(source_id)
        windows_path = PureWindowsPath(source_id)
        prefix = self._repo_relative(self.source_root, "source root")
        if (
            path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or ".." in path.parts
            or "." in path.parts
            or path.as_posix() != source_id
            or source_id == prefix
        ):
            raise ManifestError(f"invalid Source ID: {source_id!r}")
        if not source_id.startswith(prefix + "/"):
            raise ManifestError(
                f"Source ID is outside the configured source root: {source_id}"
            )

    def entry_path(self, source_id: str) -> Path:
        self._validate_source_id(source_id)
        prefix = self._repo_relative(self.source_root, "source root")
        relative = PurePosixPath(source_id).relative_to(PurePosixPath(prefix))
        candidate = self.entries_root / relative.parent / f"{relative.name}.json"
        try:
            candidate.resolve(strict=False).relative_to(
                self.entries_root.resolve(strict=False)
            )
        except ValueError as exc:
            raise ManifestError(
                f"Source ID escapes the manifest shard root: {source_id}"
            ) from exc
        return candidate

    def load(self, source_id: str) -> ManifestEntry | None:
        path = self.entry_path(source_id)
        if not path.exists() and not path.is_symlink():
            return None
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ManifestError("manifest shard is unreadable") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise ManifestError("manifest shard must be a single-link ordinary file")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"invalid manifest shard: {exc}") from exc
        if not isinstance(payload, dict):
            raise ManifestError("manifest shard must be an object")
        expected = {"compiled_at", "content_hash", "pages", "source_id"}
        if set(payload) != expected or payload.get("source_id") != source_id:
            raise ManifestError("manifest shard has invalid fields or source_id")
        content_hash = payload.get("content_hash")
        if (
            not isinstance(content_hash, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash) is None
        ):
            raise ManifestError(
                "manifest shard content_hash must be sha256 lowercase hex"
            )
        compiled_at = payload.get("compiled_at")
        if not isinstance(compiled_at, str):
            raise ManifestError("manifest shard compiled_at must be a string")
        pages = payload.get("pages")
        if not isinstance(pages, list) or any(
            not isinstance(page, str) for page in pages
        ):
            raise ManifestError("manifest pages must be a string list")
        canonical_pages = self._normalize_pages(pages, self.config.vault)
        if tuple(pages) != canonical_pages:
            raise ManifestError(
                "manifest pages must be safe, normalized, unique, and sorted"
            )
        return ManifestEntry(
            source_id=source_id,
            content_hash=content_hash,
            pages=canonical_pages,
            compiled_at=compiled_at,
        )

    def iter_entries(self) -> list[ManifestEntry]:
        if not self.entries_root.exists() and not self.entries_root.is_symlink():
            return []
        try:
            root_metadata = self.entries_root.lstat()
        except OSError as exc:
            raise ManifestError("manifest shard root is unreadable") from exc
        if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(
            root_metadata.st_mode
        ):
            raise ManifestError(
                "manifest shard root must be an ordinary directory, not a symlink"
            )
        entries: list[ManifestEntry] = []
        seen: set[str] = set()
        shard_paths: list[Path] = []
        for directory, dirnames, filenames in os.walk(
            self.entries_root, followlinks=False
        ):
            current = Path(directory)
            for name in sorted(dirnames):
                child = current / name
                try:
                    metadata = child.lstat()
                except OSError as exc:
                    raise ManifestError(
                        "manifest shard directory is unreadable"
                    ) from exc
                if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    raise ManifestError(
                        "manifest shard directory must be an ordinary directory"
                    )
                if name.endswith(".json"):
                    raise ManifestError("manifest shard must be an ordinary file")
            for name in sorted(filenames):
                path = current / name
                try:
                    metadata = path.lstat()
                except OSError as exc:
                    raise ManifestError("manifest shard is unreadable") from exc
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                    or metadata.st_nlink != 1
                ):
                    raise ManifestError(
                        "manifest shard must be a single-link ordinary file"
                    )
                if not name.endswith(".json"):
                    raise ManifestError("manifest shard file must use the .json suffix")
                shard_paths.append(path)
        for path in sorted(shard_paths):
            relative = path.relative_to(self.entries_root)
            source_prefix = self._repo_relative(self.source_root, "source root")
            source_id = f"{source_prefix}/{relative.with_suffix('').as_posix()}"
            if source_id in seen:
                raise ManifestError(f"duplicate manifest Source ID: {source_id}")
            seen.add(source_id)
            entry = self.load(source_id)
            if entry is None:
                raise ManifestError(f"missing manifest shard: {path}")
            entries.append(entry)
        return sorted(entries, key=lambda entry: entry.source_id)

    @staticmethod
    def _normalize_pages(pages: list[str] | None, vault: Path) -> tuple[str, ...]:
        values: set[str] = set()
        for page in pages or []:
            path = PurePosixPath(page)
            windows_path = PureWindowsPath(page)
            if (
                path.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or ".." in path.parts
                or "\\" in page
            ):
                raise ManifestError(f"invalid manifest page path: {page!r}")
            normalized = path.as_posix()
            if normalized in ("", ".") or normalized != page:
                raise ManifestError(f"invalid manifest page path: {page!r}")
            values.add(normalized)
        return tuple(sorted(values))

    def upsert(
        self,
        source: Path,
        *,
        pages: list[str] | None = None,
        compiled_at: str | None = None,
        expected_preimage: str | None | object = _UNSET_PREIMAGE,
    ) -> ManifestEntry:
        with self._bound_repository_root() as root_fd:
            source_path = Path(source)
            source_id = self.validated_source_id(source_path)
            entry = ManifestEntry(
                source_id=source_id,
                content_hash=f"sha256:{compute_hash(source_path)}",
                pages=self._normalize_pages(pages, self.config.vault),
                compiled_at=compiled_at
                or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            payload = {
                "compiled_at": entry.compiled_at,
                "content_hash": entry.content_hash,
                "pages": list(entry.pages),
                "source_id": entry.source_id,
            }
            target = self.entry_path(source_id)
            if not _manifest_dirfd_supported():
                self._upsert_without_dirfd(
                    root_fd,
                    target,
                    payload,
                    expected_preimage=expected_preimage,
                )
                return entry
            descriptors: list[int] = []
            temporary = ""
            try:
                descriptors = self._open_bound_parent(root_fd, target, create=True)
                parent_fd = descriptors[-1]
                _require_noreplace_supported(parent_fd)
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                for _attempt in range(16):
                    temporary = f".{target.name}.{secrets.token_hex(16)}"
                    try:
                        descriptor = os.open(
                            temporary, flags, 0o600, dir_fd=parent_fd
                        )
                    except FileExistsError:
                        continue
                    break
                else:
                    raise OSError("could not reserve manifest temporary file")
                with os.fdopen(
                    descriptor, "w", encoding="utf-8", newline="\n"
                ) as handle:
                    handle.write(
                        json.dumps(
                            payload, ensure_ascii=False, indent=2, sort_keys=True
                        )
                        + "\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                self._validate_bound_parent(root_fd, target, descriptors)
                reserved = self._reserve_shard(
                    parent_fd, target.name, expected_preimage
                )
                if reserved is not None:
                    self._archive_reserved_shard(root_fd, parent_fd, reserved)
                self._validate_bound_parent(root_fd, target, descriptors)
                try:
                    _rename_noreplace(
                        temporary,
                        target.name,
                        source_fd=parent_fd,
                        target_fd=parent_fd,
                    )
                except FileExistsError as exc:
                    raise ManifestPreconditionError(
                        "manifest shard changed concurrently during install"
                    ) from exc
                temporary = ""
                os.fsync(parent_fd)
            except ManifestError:
                raise
            except OSError as exc:
                raise ManifestError("cannot durably write manifest shard") from exc
            finally:
                if temporary and descriptors:
                    try:
                        os.unlink(temporary, dir_fd=descriptors[-1])
                    except FileNotFoundError:
                        pass
                self._close_directories(descriptors)
            return entry

    def _upsert_without_dirfd(
        self,
        root_fd: int,
        target: Path,
        payload: dict[str, object],
        *,
        expected_preimage: str | None | object,
    ) -> None:
        """Fail closed where the mutation cannot remain descriptor-bound."""
        self._validate_root_attachment(root_fd)
        self._validate_vault_attachment()
        del target, payload, expected_preimage
        raise ManifestError(
            "safe manifest mutation requires descriptor-relative filesystem support"
        )

    @staticmethod
    def _bound_shard_proof(parent_fd: int, name: str) -> _ReservedShard:
        descriptor = -1
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise ManifestPreconditionError(
                    "manifest shard changed after transaction began"
                )
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino)
                != (metadata.st_dev, metadata.st_ino)
            ):
                raise ManifestPreconditionError(
                    "manifest shard changed after transaction began"
                )
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 65536):
                digest.update(chunk)
            final = os.fstat(descriptor)
            stable = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(opened, field) != getattr(final, field) for field in stable):
                raise ManifestPreconditionError(
                    "manifest shard changed after transaction began"
                )
            return _ReservedShard(
                name=name,
                content_hash=f"sha256:{digest.hexdigest()}",
                identity=(opened.st_dev, opened.st_ino),
            )
        except OSError as exc:
            raise ManifestPreconditionError(
                "manifest shard changed after transaction began"
            ) from exc
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @classmethod
    def _reserve_shard(
        cls, parent_fd: int, name: str, expected: str | None | object
    ) -> _ReservedShard | None:
        if expected is not _UNSET_PREIMAGE and expected is not None and (
            not isinstance(expected, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected) is None
        ):
            raise ManifestError("manifest shard expected preimage is invalid")
        try:
            before = cls._bound_shard_proof(parent_fd, name)
        except ManifestPreconditionError:
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if expected is not _UNSET_PREIMAGE and expected is not None:
                    raise ManifestPreconditionError(
                        "manifest shard changed after transaction began"
                    )
                return None
            raise
        if expected is not _UNSET_PREIMAGE and before.content_hash != expected:
            raise ManifestPreconditionError(
                "manifest shard changed after transaction began"
            )
        reservation = f".{name}.reserved-{secrets.token_hex(16)}"
        try:
            os.rename(
                name,
                reservation,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            after = cls._bound_shard_proof(parent_fd, reservation)
        except OSError as exc:
            raise ManifestPreconditionError(
                "manifest shard changed after transaction began"
            ) from exc
        reserved = _ReservedShard(
            name=reservation,
            content_hash=after.content_hash,
            identity=after.identity,
        )
        if before.identity != after.identity or before.content_hash != after.content_hash:
            raise ManifestPreconditionError(
                "manifest shard changed concurrently while being reserved; "
                f"preserved evidence in {reservation}"
            )
        return reserved

    def _archive_reserved_shard(
        self,
        root_fd: int,
        source_parent_fd: int,
        reserved: _ReservedShard,
    ) -> tuple[Path, _ReservedShard]:
        archive = (
            self.config.root
            / ".obsidian-wiki/local/manifest-reservations"
            / f"shard-{secrets.token_hex(16)}"
        )
        directories = self._open_bound_parent(root_fd, archive, create=True)
        try:
            archive_parent_fd = directories[-1]
            _rename_noreplace(
                reserved.name,
                archive.name,
                source_fd=source_parent_fd,
                target_fd=archive_parent_fd,
            )
            archived = self._bound_shard_proof(archive_parent_fd, archive.name)
            if (
                archived.identity != reserved.identity
                or archived.content_hash != reserved.content_hash
            ):
                raise ManifestPreconditionError(
                    "manifest reservation changed concurrently; preserved evidence"
                )
            os.fsync(archive_parent_fd)
            return archive, archived
        finally:
            self._close_directories(directories)

    @staticmethod
    def _validate_source_file(source_path: Path) -> None:
        try:
            metadata = source_path.lstat()
        except OSError as exc:
            raise ManifestError("source must be a single-link ordinary file") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise ManifestError("source must be a single-link ordinary file")

    def remove(self, source_id: str) -> None:
        with self._bound_repository_root() as root_fd:
            target = self.entry_path(source_id)
            if not _manifest_dirfd_supported():
                self._remove_without_dirfd(root_fd, target)
                return
            descriptors: list[int] = []
            try:
                descriptors = self._open_bound_parent(
                    root_fd, target, create=False
                )
                parent_fd = descriptors[-1]
                _require_noreplace_supported(parent_fd)
                self._validate_bound_parent(root_fd, target, descriptors)
                reserved = self._reserve_shard(
                    parent_fd, target.name, _UNSET_PREIMAGE
                )
                if reserved is None:
                    return
                archive, _archived = self._archive_reserved_shard(
                    root_fd, parent_fd, reserved
                )
                try:
                    os.stat(
                        target.name, dir_fd=parent_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    pass
                else:
                    raise ManifestPreconditionError(
                        "manifest shard changed concurrently during remove; "
                        f"preserved prior bytes in {archive}"
                    )
                self._validate_bound_parent(root_fd, target, descriptors)
                os.fsync(parent_fd)
            except ManifestError:
                raise
            except OSError as exc:
                raise ManifestError("cannot durably remove manifest shard") from exc
            finally:
                self._close_directories(descriptors)

    def _remove_without_dirfd(self, root_fd: int, target: Path) -> None:
        self._validate_root_attachment(root_fd)
        self._validate_vault_attachment()
        del target
        raise ManifestError(
            "safe manifest mutation requires descriptor-relative filesystem support"
        )

    def status(self) -> dict[str, list[str]]:
        tracked = {entry.source_id: entry for entry in self.iter_entries()}
        current: dict[str, Path] = {}
        if self.source_root.exists():
            for path in self.source_root.rglob("*"):
                relative = path.relative_to(self.source_root)
                if (
                    any(part.startswith(".") for part in relative.parts)
                    or path.name == ".gitkeep"
                ):
                    continue
                try:
                    metadata = path.lstat()
                except OSError as exc:
                    raise ManifestError("source cannot be inspected") from exc
                if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(
                    metadata.st_mode
                ):
                    continue
                current[self.validated_source_id(path)] = path
        result = {"new": [], "modified": [], "unchanged": [], "missing": []}
        for source_id, path in sorted(current.items()):
            entry = tracked.get(source_id)
            if entry is None:
                result["new"].append(source_id)
            elif entry.content_hash != f"sha256:{compute_hash(path)}":
                result["modified"].append(source_id)
            else:
                result["unchanged"].append(source_id)
        result["missing"] = sorted(set(tracked) - set(current))
        return result

    def status_for(self, source_paths: list[Path]) -> dict[str, list[str]]:
        """Classify only *source_paths*, preserving v1 cache semantics."""
        tracked = {entry.source_id: entry for entry in self.iter_entries()}
        result = {"new": [], "modified": [], "unchanged": [], "missing": []}
        selected: set[str] = set()
        for raw in source_paths:
            path = Path(raw)
            source_id = self.source_id(path)
            selected.add(source_id)
            try:
                path.lstat()
            except FileNotFoundError:
                result["missing"].append(source_id)
                continue
            except OSError as exc:
                raise ManifestError("source cannot be inspected") from exc
            source_id = self.validated_source_id(path)
            entry = tracked.get(source_id)
            if entry is None:
                result["new"].append(source_id)
            elif entry.content_hash != f"sha256:{compute_hash(path)}":
                result["modified"].append(source_id)
            else:
                result["unchanged"].append(source_id)
        for source_id in sorted(set(tracked) - selected):
            source = self.source_path(source_id)
            self.source_id(source)
            try:
                source.lstat()
            except FileNotFoundError:
                result["missing"].append(source_id)
            except OSError as exc:
                raise ManifestError("source cannot be inspected") from exc
            else:
                self.validated_source_id(source)
        return result

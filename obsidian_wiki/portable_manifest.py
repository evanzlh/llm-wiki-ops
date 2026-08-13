from __future__ import annotations

import errno
import json
import hashlib
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
_SUPPORTS_MANIFEST_DIRFD = all(
    function in os.supports_dir_fd
    for function in (os.open, os.stat, os.mkdir, os.rename, os.link, os.unlink)
) and os.stat in os.supports_follow_symlinks
_SIDECAR = ".obsidian-wiki-manifest-mutation"
_WAL_FILES = frozenset(
    {"journal.json", ".journal.tmp", "pre.bin", ".pre.tmp", "post.bin", ".post.tmp"}
)
_MAX_SHARD_BYTES = 16 * 1024 * 1024

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None


def _manifest_fault_point(_step: str) -> None:
    """Test-only crash boundary; deliberately a no-op in production."""


def _manifest_mutation_supported() -> bool:
    return (
        os.name == "posix"
        and _fcntl is not None
        and _SUPPORTS_DIRECTORY_FSYNC
        and _SUPPORTS_MANIFEST_DIRFD
    )


class ManifestError(ValueError):
    pass


class ManifestPreconditionError(ManifestError):
    pass


class _MissingManifestDirectory(ManifestError):
    pass


_UNSET_PREIMAGE = object()


@dataclass(frozen=True)
class ManifestEntry:
    source_id: str
    content_hash: str
    pages: tuple[str, ...]
    compiled_at: str


@dataclass(frozen=True)
class _FileProof:
    content_hash: str
    identity: tuple[int, int]
    links: int
    data: bytes


class ShardedManifest:
    def __init__(self, config: PortableConfig) -> None:
        if len(config.sources) != 1:
            raise ManifestError("manifest v2 schema 1 requires exactly one source root")
        self.config = config
        self.source_root = config.sources[0]
        self.marker_path = config.vault / ".manifest.json"
        self.entries_root = config.vault / ".manifest" / "sources"
        self.wal_root = config.local_state / "manifest-mutation"
        self.lock_path = self.wal_root / "manifest.lock"
        self._session: tuple[int, int, list[int]] | None = None
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
            if (
                stable_directory_identity(os.fstat(descriptor))
                != self.config.root_identity
            ):
                raise ManifestError("configured repository root changed since configuration was read")
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
                descriptor = None
            raise ManifestError("configured repository root is unsafe") from exc
        try:
            yield descriptor
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _same_directory(left: os.stat_result, right: os.stat_result) -> bool:
        return stat.S_ISDIR(left.st_mode) and stat.S_ISDIR(right.st_mode) and (
            stable_directory_identity(left) == stable_directory_identity(right)
        )

    def _capture_vault_identity(self) -> tuple[int, int]:
        if not _manifest_mutation_supported():
            try:
                metadata = self.config.vault.lstat()
            except OSError as exc:
                raise ManifestError("configured vault is unsafe") from exc
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ManifestError("configured vault is unsafe")
            return stable_directory_identity(metadata)
        with self._bound_repository_root() as root_fd:
            descriptors = self._open_directory(root_fd, self.config.vault, create=False)
            try:
                return stable_directory_identity(os.fstat(descriptors[-1]))
            finally:
                self._close_fds(descriptors)

    def _validate_root_attachment(self, root_fd: int) -> None:
        try:
            attached = self.config.root.lstat()
            opened = os.fstat(root_fd)
        except OSError as exc:
            raise ManifestError("configured repository root is unsafe") from exc
        if stable_directory_identity(attached) != stable_directory_identity(opened):
            raise ManifestError("configured repository root changed during manifest mutation")

    def _open_directory(self, root_fd: int, path: Path, *, create: bool) -> list[int]:
        try:
            relative = path.relative_to(self.config.root)
        except ValueError as exc:
            raise ManifestError("manifest control path escapes repository") from exc
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptors = [os.dup(root_fd)]
        try:
            for part in relative.parts:
                parent_fd = descriptors[-1]
                self._validate_root_attachment(root_fd)
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=parent_fd)
                        os.fsync(parent_fd)
                    except FileExistsError:
                        pass
                before = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                child = os.open(part, flags, dir_fd=parent_fd)
                descriptors.append(child)
                after = os.fstat(child)
                attached = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                if not self._same_directory(before, after) or not self._same_directory(after, attached):
                    raise ManifestError("manifest directory changed while being bound")
                opened_relative = Path(*relative.parts[: len(descriptors) - 1])
                vault_relative = self.config.vault.relative_to(self.config.root)
                if opened_relative == vault_relative and hasattr(self, "_vault_identity"):
                    if stable_directory_identity(after) != self._vault_identity:
                        raise ManifestError("configured vault changed during manifest mutation")
            self._validate_root_attachment(root_fd)
            return descriptors
        except FileNotFoundError as exc:
            self._close_fds(descriptors)
            if create:
                raise ManifestError(
                    "cannot durably create manifest directory"
                ) from exc
            raise _MissingManifestDirectory(
                "manifest directory is absent"
            ) from exc
        except OSError as exc:
            self._close_fds(descriptors)
            raise ManifestError(
                "cannot sync or durably create manifest directory"
            ) from exc
        except BaseException:
            self._close_fds(descriptors)
            raise

    @staticmethod
    def _close_fds(descriptors: list[int]) -> None:
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
        proof = self._read_live_shard(source_id, path)
        if proof is None:
            return None
        try:
            payload = json.loads(proof.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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
            if _SIDECAR in dirnames:
                sidecar = current / _SIDECAR
                try:
                    sidecar_metadata = sidecar.lstat()
                except OSError as exc:
                    raise ManifestError("manifest sidecar is unreadable") from exc
                if not stat.S_ISDIR(sidecar_metadata.st_mode) or stat.S_ISLNK(
                    sidecar_metadata.st_mode
                ):
                    raise ManifestError("manifest sidecar must be an ordinary directory")
                dirnames.remove(_SIDECAR)
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
                if name.startswith("."):
                    raise ManifestError("unexpected manifest shard directory")
            for name in sorted(filenames):
                path = current / name
                try:
                    metadata = path.lstat()
                except OSError as exc:
                    raise ManifestError("manifest shard is unreadable") from exc
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                ):
                    raise ManifestError(
                        "manifest shard must be an ordinary file"
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
        data = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        with self._auto_session():
            self._mutate(source_id, "upsert", data, expected_preimage)
        return entry

    @staticmethod
    def _require_preimage(target: Path, expected: str | None | object) -> None:
        if expected is not None and (
            not isinstance(expected, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected) is None
        ):
            raise ManifestError("manifest shard expected preimage is invalid")
        if not target.exists() and not target.is_symlink():
            current = None
        else:
            try:
                metadata = target.lstat()
            except OSError as exc:
                raise ManifestPreconditionError(
                    "manifest shard changed after transaction began"
                ) from exc
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise ManifestPreconditionError(
                    "manifest shard changed after transaction began"
                )
            try:
                current = f"sha256:{compute_hash(target)}"
            except OSError as exc:
                raise ManifestPreconditionError(
                    "manifest shard changed after transaction began"
                ) from exc
        if current != expected:
            raise ManifestPreconditionError(
                "manifest shard changed after transaction began"
            )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if not _SUPPORTS_DIRECTORY_FSYNC:
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise OSError("manifest shard parent is not a directory")
            os.fsync(descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def mutation_session(self) -> Iterator[ShardedManifest]:
        """Serialize cooperating manifest writers and recover the fixed WAL."""
        if self._session is not None:
            yield self
            return
        if not _manifest_mutation_supported():
            raise ManifestError(
                "safe manifest mutation requires POSIX descriptor-relative filesystem support"
            )
        with self._bound_repository_root() as root_fd:
            directories = self._open_directory(root_fd, self.wal_root, create=True)
            wal_fd = directories[-1]
            lock_fd = -1
            entered_body = False
            try:
                flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                flags |= getattr(os, "O_CLOEXEC", 0)
                lock_fd = os.open("manifest.lock", flags, 0o600, dir_fd=wal_fd)
                lock_metadata = os.fstat(lock_fd)
                if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_nlink != 1:
                    raise ManifestError("manifest mutation lock must be a single-link ordinary file")
                _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
                self._session = (root_fd, wal_fd, directories)
                self._validate_wal_directory()
                self._recover_wal()
                entered_body = True
                yield self
            except ManifestError:
                raise
            except OSError as exc:
                if entered_body:
                    raise
                raise ManifestError(
                    "cannot safely recover or lock manifest mutations"
                ) from exc
            finally:
                self._session = None
                if lock_fd >= 0:
                    try:
                        _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
                    finally:
                        os.close(lock_fd)
                self._close_fds(directories)

    @contextmanager
    def _auto_session(self) -> Iterator[None]:
        if self._session is not None:
            yield
        else:
            with self.mutation_session():
                yield

    @staticmethod
    def _digest(data: bytes) -> str:
        return "sha256:" + hashlib.sha256(data).hexdigest()

    @staticmethod
    def _read_proof(
        parent_fd: int,
        name: str,
        *,
        links: frozenset[int] = frozenset({1}),
        size_limit: int = _MAX_SHARD_BYTES,
    ) -> _FileProof | None:
        descriptor = -1
        try:
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ManifestError("manifest artifact is unreadable") from exc
        if not stat.S_ISREG(before.st_mode) or before.st_nlink not in links:
            raise ManifestError("manifest artifact must be an ordinary file with safe links")
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(name, flags, dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink not in links
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > size_limit
            ):
                raise ManifestError("manifest artifact changed while being read")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(65536, size_limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > size_limit:
                    raise ManifestError("manifest artifact exceeds the size limit")
            final = os.fstat(descriptor)
            stable_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(opened, field) != getattr(final, field) for field in stable_fields):
                raise ManifestError("manifest artifact changed while being read")
            data = b"".join(chunks)
            return _FileProof(
                content_hash=ShardedManifest._digest(data),
                identity=(opened.st_dev, opened.st_ino),
                links=opened.st_nlink,
                data=data,
            )
        except OSError as exc:
            raise ManifestError("manifest artifact is unreadable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _write_fixed(parent_fd: int, name: str, data: bytes) -> None:
        temporary = f".{name.rsplit('.', 1)[0]}.tmp"
        existing = ShardedManifest._read_proof(parent_fd, temporary)
        if existing is not None:
            os.unlink(temporary, dir_fd=parent_fd)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        current = ShardedManifest._read_proof(parent_fd, name)
        os.rename(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)

    def _write_journal(self, payload: dict[str, object]) -> None:
        assert self._session is not None
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self._write_fixed(self._session[1], "journal.json", data)

    def _journal(self) -> dict[str, object] | None:
        assert self._session is not None
        proof = self._read_proof(
            self._session[1], "journal.json", size_limit=64 * 1024
        )
        if proof is None:
            self._clean_idle_wal()
            return None
        try:
            payload = json.loads(proof.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError("manifest mutation journal is invalid") from exc
        keys = {
            "schema_version", "state", "op_id", "action", "source_id", "target",
            "sidecar", "pre", "post", "candidate_identity",
        }
        if not isinstance(payload, dict) or set(payload) != keys:
            raise ManifestError("manifest mutation journal has invalid fields")
        if payload["schema_version"] != 1 or payload["state"] not in {"PREPARED", "APPLIED", "CONFLICT"}:
            raise ManifestError("manifest mutation journal has invalid schema or state")
        if payload["action"] not in {"upsert", "remove"} or (
            not isinstance(payload["op_id"], str)
            or re.fullmatch(r"[0-9a-f]{32}", payload["op_id"]) is None
        ):
            raise ManifestError("manifest mutation journal has invalid operation")
        source_id = payload["source_id"]
        if not isinstance(source_id, str):
            raise ManifestError("manifest mutation journal has invalid source_id")
        target = self.entry_path(source_id).relative_to(self.config.root).as_posix()
        sidecar = (self.entry_path(source_id).parent / _SIDECAR).relative_to(self.config.root).as_posix()
        if payload["target"] != target or payload["sidecar"] != sidecar:
            raise ManifestError("manifest mutation journal has unsafe paths")
        for key in ("pre", "post"):
            image = payload[key]
            if not isinstance(image, dict) or set(image) != {"present", "hash", "identity"}:
                raise ManifestError("manifest mutation journal has invalid image proof")
            if not isinstance(image["present"], bool):
                raise ManifestError("manifest mutation journal has invalid image presence")
            if image["present"]:
                if not isinstance(image["hash"], str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image["hash"]) is None:
                    raise ManifestError("manifest mutation journal has invalid image hash")
            elif image["hash"] is not None:
                raise ManifestError("manifest mutation journal has invalid absent image")
            identity = image["identity"]
            if identity is not None and (
                not isinstance(identity, list)
                or len(identity) != 2
                or any(type(value) is not int or value < 0 for value in identity)
            ):
                raise ManifestError("manifest mutation journal has invalid image identity")
        candidate_identity = payload["candidate_identity"]
        if candidate_identity is not None and (
            not isinstance(candidate_identity, list)
            or len(candidate_identity) != 2
            or any(type(value) is not int or value < 0 for value in candidate_identity)
        ):
            raise ManifestError("manifest mutation journal has invalid candidate identity")
        if (payload["action"] == "upsert") != bool(payload["post"]["present"]):
            raise ManifestError("manifest mutation journal action disagrees with postimage")
        if payload["state"] == "PREPARED" and payload["action"] == "remove" and candidate_identity is not None:
            raise ManifestError("remove journal cannot name a candidate")
        return payload

    def _validate_wal_directory(self) -> None:
        assert self._session is not None
        try:
            names = set(os.listdir(self._session[1]))
        except OSError as exc:
            raise ManifestError("manifest WAL directory is unreadable") from exc
        allowed = _WAL_FILES | {"manifest.lock"}
        if names - allowed:
            raise ManifestError("manifest WAL directory contains unexpected entries")
        for name in names:
            try:
                metadata = os.stat(
                    name, dir_fd=self._session[1], follow_symlinks=False
                )
            except OSError as exc:
                raise ManifestError("manifest WAL entry is unreadable") from exc
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ManifestError("manifest WAL entries must be single-link ordinary files")

    def _clean_idle_wal(self) -> None:
        assert self._session is not None
        wal_fd = self._session[1]
        for name in _WAL_FILES - {"journal.json"}:
            proof = self._read_proof(wal_fd, name)
            if proof is not None:
                os.unlink(name, dir_fd=wal_fd)
        os.fsync(wal_fd)

    def _target_directories(self, *, create: bool) -> tuple[Path, list[int]]:
        assert self._session is not None
        journal = self._journal()
        if journal is None:
            raise ManifestError("manifest mutation journal is missing")
        target = self.config.root / PurePosixPath(journal["target"])
        return target, self._open_directory(self._session[0], target.parent, create=create)

    @staticmethod
    def _matches(proof: _FileProof | None, image: dict[str, object]) -> bool:
        if not image["present"]:
            return proof is None
        if proof is None or proof.content_hash != image["hash"]:
            return False
        identity = image["identity"]
        return identity is None or tuple(identity) == proof.identity

    def _ensure_sidecar(self, parent_fd: int) -> tuple[int, bool]:
        created = False
        try:
            os.mkdir(_SIDECAR, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
            created = True
        except FileExistsError:
            pass
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        side_fd = os.open(_SIDECAR, flags, dir_fd=parent_fd)
        metadata = os.fstat(side_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(side_fd)
            raise ManifestError("manifest sidecar must be an ordinary directory")
        try:
            unknown = set(os.listdir(side_fd)) - {"candidate", "reserved"}
        except OSError as exc:
            os.close(side_fd)
            raise ManifestError("manifest sidecar is unreadable") from exc
        if unknown:
            os.close(side_fd)
            raise ManifestError("manifest sidecar contains unexpected entries")
        return side_fd, created

    def _conflict(self, journal: dict[str, object], message: str) -> None:
        journal["state"] = "CONFLICT"
        self._write_journal(journal)
        raise ManifestPreconditionError(message)

    def _recover_wal(self) -> None:
        journal = self._journal()
        if journal is None:
            return
        if journal["state"] == "CONFLICT":
            raise ManifestPreconditionError("manifest mutation conflict requires owner resolution")
        if journal["state"] == "APPLIED":
            self._cleanup_applied(journal)
            return
        self._validate_wal_blobs(journal)
        self._resume_prepared(journal)

    def _validate_wal_blobs(self, journal: dict[str, object]) -> None:
        assert self._session is not None
        for name, image in (("pre.bin", journal["pre"]), ("post.bin", journal["post"])):
            proof = self._read_proof(self._session[1], name)
            if proof is None:
                raise ManifestError("manifest mutation WAL blob is missing")
            expected = image["hash"] if image["present"] else self._digest(b"")
            if proof.content_hash != expected:
                raise ManifestError("manifest mutation WAL blob is corrupt")

    def _resume_prepared(self, journal: dict[str, object]) -> None:
        target, directories = self._target_directories(create=False)
        parent_fd = directories[-1]
        side_fd = -1
        try:
            live = self._read_proof(parent_fd, target.name, links=frozenset({1, 2}))
            try:
                side_fd, _ = self._ensure_sidecar(parent_fd)
            except FileNotFoundError:
                side_fd = -1
            reserved = (
                self._read_proof(side_fd, "reserved", links=frozenset({1, 2}))
                if side_fd >= 0
                else None
            )
            candidate = self._read_proof(side_fd, "candidate", links=frozenset({1, 2})) if side_fd >= 0 else None
            if self._matches(live, journal["post"]):
                if live is not None and live.links == 2:
                    if candidate is None or candidate.identity != live.identity or candidate.content_hash != live.content_hash:
                        self._conflict(journal, "manifest link window conflicts with WAL")
                    os.unlink("candidate", dir_fd=side_fd)
                    os.fsync(side_fd)
                    os.fsync(parent_fd)
                journal["state"] = "APPLIED"
                self._write_journal(journal)
                self._cleanup_applied(journal)
                return
            if reserved is not None:
                if not self._matches(reserved, journal["pre"]):
                    if live is None and reserved.links == 1:
                        try:
                            os.link("reserved", target.name, src_dir_fd=side_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                            os.fsync(parent_fd)
                            os.unlink("reserved", dir_fd=side_fd)
                            self._abort_wal(side_fd, parent_fd)
                        except FileExistsError:
                            self._conflict(journal, "manifest concurrent owner conflict")
                        raise ManifestPreconditionError("manifest shard changed concurrently and was restored")
                    self._conflict(journal, "manifest reserved bytes conflict with WAL")
                if live is not None and (
                    live.links == 2
                    and reserved.links == 2
                    and live.identity == reserved.identity
                    and live.content_hash == reserved.content_hash
                ):
                    self._validate_root_attachment(self._session[0])
                    os.unlink(target.name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                    live = None
                if live is not None:
                    self._conflict(journal, "manifest target was created concurrently")
            elif not self._matches(live, journal["pre"]):
                self._conflict(journal, "manifest shard changed concurrently")
            self._apply_prepared(journal, parent_fd, target.name, side_fd)
        finally:
            if side_fd >= 0:
                os.close(side_fd)
            self._close_fds(directories)

    def _apply_prepared(
        self, journal: dict[str, object], parent_fd: int, target_name: str, side_fd: int = -1
    ) -> None:
        if side_fd < 0:
            side_fd, _ = self._ensure_sidecar(parent_fd)
            close_side = True
        else:
            close_side = False
        try:
            post = journal["post"]
            if journal["action"] == "upsert":
                candidate = self._read_proof(side_fd, "candidate", links=frozenset({1, 2}))
                if candidate is None:
                    assert self._session is not None
                    post_blob = self._read_proof(self._session[1], "post.bin")
                    assert post_blob is not None
                    self._write_exclusive(side_fd, "candidate", post_blob.data)
                    os.fsync(side_fd)
                    candidate = self._read_proof(side_fd, "candidate")
                if candidate is None or candidate.content_hash != post["hash"]:
                    self._conflict(journal, "manifest candidate conflicts with WAL")
                journal["candidate_identity"] = list(candidate.identity)
                self._write_journal(journal)
            pre = journal["pre"]
            reserved = self._read_proof(side_fd, "reserved")
            if pre["present"] and reserved is None:
                live = self._read_proof(parent_fd, target_name)
                if not self._matches(live, pre):
                    self._conflict(journal, "manifest shard changed before reservation")
                self._validate_root_attachment(self._session[0])
                try:
                    os.link(
                        target_name,
                        "reserved",
                        src_dir_fd=parent_fd,
                        dst_dir_fd=side_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    self._conflict(journal, "manifest reservation conflicts with concurrent owner")
                linked = self._read_proof(side_fd, "reserved", links=frozenset({2}))
                current = self._read_proof(parent_fd, target_name, links=frozenset({2}))
                if (
                    linked is None
                    or current is None
                    or linked.identity != current.identity
                    or not self._matches(linked, pre)
                ):
                    self._conflict(journal, "manifest shard changed while being reserved")
                os.fsync(side_fd)
                _manifest_fault_point("reserved_linked")
                self._validate_root_attachment(self._session[0])
                os.unlink(target_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
                os.fsync(side_fd)
                self._validate_root_attachment(self._session[0])
                reserved = self._read_proof(side_fd, "reserved")
                if not self._matches(reserved, pre):
                    if reserved is not None:
                        try:
                            os.link("reserved", target_name, src_dir_fd=side_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                            os.fsync(parent_fd)
                            os.unlink("reserved", dir_fd=side_fd)
                            self._abort_wal(side_fd, parent_fd)
                        except FileExistsError:
                            self._conflict(journal, "manifest concurrent owner conflict")
                    raise ManifestPreconditionError("manifest shard changed concurrently and was restored")
            elif not pre["present"] and self._read_proof(parent_fd, target_name) is not None:
                self._conflict(journal, "manifest target was created concurrently")
            _manifest_fault_point("reserved")
            if journal["action"] == "upsert":
                self._validate_root_attachment(self._session[0])
                try:
                    os.link("candidate", target_name, src_dir_fd=side_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                except FileExistsError:
                    self._conflict(journal, "manifest target was created concurrently")
                os.fsync(parent_fd)
                self._validate_root_attachment(self._session[0])
                _manifest_fault_point("linked")
                os.unlink("candidate", dir_fd=side_fd)
                os.fsync(side_fd)
                os.fsync(parent_fd)
            elif self._read_proof(parent_fd, target_name) is not None:
                self._conflict(journal, "manifest target was created concurrently during remove")
            _manifest_fault_point("installed")
            journal["state"] = "APPLIED"
            self._write_journal(journal)
            _manifest_fault_point("applied")
            self._cleanup_applied(journal, side_fd=side_fd, parent_fd=parent_fd)
            self._validate_root_attachment(self._session[0])
        finally:
            if close_side:
                os.close(side_fd)

    @staticmethod
    def _write_exclusive(parent_fd: int, name: str, data: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _cleanup_applied(
        self, journal: dict[str, object], *, side_fd: int = -1, parent_fd: int = -1
    ) -> None:
        own_descriptors: list[int] = []
        own_side = False
        if side_fd < 0 or parent_fd < 0:
            target, own_descriptors = self._target_directories(create=False)
            parent_fd = own_descriptors[-1]
            try:
                side_fd, _ = self._ensure_sidecar(parent_fd)
                own_side = True
            except FileNotFoundError:
                side_fd = -1
        try:
            if side_fd >= 0:
                for name, image in (("candidate", journal["post"]), ("reserved", journal["pre"])):
                    proof = self._read_proof(side_fd, name, links=frozenset({1, 2}))
                    if proof is None:
                        continue
                    if proof.content_hash != image["hash"]:
                        self._conflict(journal, "manifest cleanup artifact conflicts with WAL")
                    self._unlink_matching(side_fd, name, proof, journal)
                os.fsync(side_fd)
                try:
                    os.rmdir(_SIDECAR, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except OSError as exc:
                    if exc.errno not in (errno.ENOTEMPTY, errno.ENOENT):
                        raise
            assert self._session is not None
            wal_fd = self._session[1]
            for name, image in (("pre.bin", journal["pre"]), ("post.bin", journal["post"])):
                proof = self._read_proof(wal_fd, name)
                if proof is None:
                    continue
                expected = image["hash"] if image["present"] else self._digest(b"")
                if proof.content_hash != expected:
                    self._conflict(journal, "manifest WAL cleanup artifact conflicts with journal")
                self._unlink_matching(wal_fd, name, proof, journal)
            os.fsync(wal_fd)
            os.unlink("journal.json", dir_fd=wal_fd)
            os.fsync(wal_fd)
            self._clean_idle_wal()
        finally:
            if own_side and side_fd >= 0:
                os.close(side_fd)
            self._close_fds(own_descriptors)

    def _unlink_matching(
        self,
        parent_fd: int,
        name: str,
        expected: _FileProof,
        journal: dict[str, object],
    ) -> None:
        """Best-effort conditional cleanup; a changed path becomes fixed conflict evidence."""
        current = self._read_proof(parent_fd, name, links=frozenset({1, 2}))
        if current is None:
            return
        if current.identity != expected.identity or current.content_hash != expected.content_hash:
            self._conflict(journal, "manifest cleanup artifact changed concurrently")
        assert self._session is not None
        self._validate_root_attachment(self._session[0])
        os.unlink(name, dir_fd=parent_fd)

    def _abort_wal(self, side_fd: int, parent_fd: int) -> None:
        candidate = self._read_proof(side_fd, "candidate", links=frozenset({1, 2}))
        if candidate is not None:
            os.unlink("candidate", dir_fd=side_fd)
        os.fsync(side_fd)
        try:
            os.rmdir(_SIDECAR, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.ENOENT):
                raise
        assert self._session is not None
        wal_fd = self._session[1]
        for name in ("pre.bin", "post.bin", "journal.json"):
            if self._read_proof(wal_fd, name) is not None:
                os.unlink(name, dir_fd=wal_fd)
        os.fsync(wal_fd)

    def _mutate(
        self, source_id: str, action: str, post_data: bytes | None, expected: str | None | object
    ) -> None:
        assert self._session is not None
        target = self.entry_path(source_id)
        directories = self._open_directory(self._session[0], target.parent, create=True)
        try:
            parent_fd = directories[-1]
            pre = self._read_proof(parent_fd, target.name)
            if expected is not _UNSET_PREIMAGE:
                if expected is not None and (
                    not isinstance(expected, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", expected) is None
                ):
                    raise ManifestError("manifest shard expected preimage is invalid")
                current = pre.content_hash if pre is not None else None
                if current != expected:
                    raise ManifestPreconditionError("manifest shard changed after transaction began")
            pre_data = pre.data if pre is not None else b""
            resolved_post = post_data if post_data is not None else b""
            wal_fd = self._session[1]
            self._write_fixed(wal_fd, "pre.bin", pre_data)
            self._write_fixed(wal_fd, "post.bin", resolved_post)
            journal: dict[str, object] = {
                "schema_version": 1,
                "state": "PREPARED",
                "op_id": secrets.token_hex(16),
                "action": action,
                "source_id": source_id,
                "target": target.relative_to(self.config.root).as_posix(),
                "sidecar": (target.parent / _SIDECAR).relative_to(self.config.root).as_posix(),
                "pre": {"present": pre is not None, "hash": pre.content_hash if pre else None, "identity": list(pre.identity) if pre else None},
                "post": {"present": post_data is not None, "hash": self._digest(post_data) if post_data is not None else None, "identity": None},
                "candidate_identity": None,
            }
            self._write_journal(journal)
            _manifest_fault_point("prepared")
            self._apply_prepared(journal, parent_fd, target.name)
        except ManifestError:
            raise
        except OSError as exc:
            raise ManifestError("cannot sync or durably write manifest shard") from exc
        finally:
            self._close_fds(directories)

    def _read_live_shard(self, source_id: str, path: Path) -> _FileProof | None:
        if not _manifest_mutation_supported():
            if not path.exists() and not path.is_symlink():
                return None
            try:
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ManifestError("manifest shard must be a single-link ordinary file")
                data = path.read_bytes()
            except OSError as exc:
                raise ManifestError("manifest shard is unreadable") from exc
            return _FileProof(self._digest(data), (metadata.st_dev, metadata.st_ino), 1, data)
        with self._bound_repository_root() as root_fd:
            try:
                directories = self._open_directory(
                    root_fd, path.parent, create=False
                )
            except _MissingManifestDirectory:
                self._validate_root_attachment(root_fd)
                return None
            try:
                parent_fd = directories[-1]
                proof = self._read_proof(parent_fd, path.name, links=frozenset({1, 2}))
                if proof is None:
                    return None
                if proof.links == 1:
                    return proof
                if self._link_window_matches_fd(root_fd, parent_fd, source_id, path, proof):
                    return proof
                raise ManifestError("manifest shard must be a single-link ordinary file")
            finally:
                self._close_fds(directories)

    def _link_window_matches_fd(
        self,
        root_fd: int,
        parent_fd: int,
        source_id: str,
        path: Path,
        target: _FileProof,
    ) -> bool:
        wal_descriptors: list[int] = []
        side_fd = -1
        try:
            wal_descriptors = self._open_directory(root_fd, self.wal_root, create=False)
            journal = self._read_proof(
                wal_descriptors[-1], "journal.json", size_limit=64 * 1024
            )
            if journal is None:
                return False
            payload = json.loads(journal.data.decode("utf-8"))
            expected_keys = {
                "schema_version", "state", "op_id", "action", "source_id",
                "target", "sidecar", "pre", "post", "candidate_identity",
            }
            expected_sidecar = (path.parent / _SIDECAR).relative_to(self.config.root).as_posix()
            if (
                not isinstance(payload, dict)
                or set(payload) != expected_keys
                or payload.get("schema_version") != 1
                or payload.get("state") != "PREPARED"
                or payload.get("action") != "upsert"
                or payload.get("source_id") != source_id
                or payload.get("target") != path.relative_to(self.config.root).as_posix()
                or payload.get("sidecar") != expected_sidecar
                or payload.get("candidate_identity") != list(target.identity)
                or not isinstance(payload.get("post"), dict)
                or payload["post"].get("hash") != target.content_hash
            ):
                return False
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            side_fd = os.open(_SIDECAR, flags, dir_fd=parent_fd)
            candidate = self._read_proof(side_fd, "candidate", links=frozenset({2}))
            return candidate is not None and candidate.identity == target.identity and candidate.content_hash == target.content_hash
        except (OSError, ManifestError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return False
        finally:
            if side_fd >= 0:
                os.close(side_fd)
            self._close_fds(wal_descriptors)

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
        self.entry_path(source_id)
        with self._auto_session():
            self._mutate(source_id, "remove", None, _UNSET_PREIMAGE)

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

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from obsidian_wiki.cache import compute_hash
from obsidian_wiki.config import PortableConfig

_SUPPORTS_DIRECTORY_FSYNC = os.name != "nt"


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
        self._validate_marker()

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
        source_path = Path(source)
        self._validate_source_file(source_path)
        source_id = self.source_id(source_path)
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
        try:
            self._ensure_directory_tree(target.parent)
        except OSError as exc:
            raise ManifestError(
                "cannot sync or durably create manifest shard directory"
            ) from exc
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            if expected_preimage is not _UNSET_PREIMAGE:
                self._require_preimage(target, expected_preimage)
            temporary_path.replace(target)
            self._fsync_directory(target.parent)
        except OSError as exc:
            raise ManifestError("cannot durably write manifest shard") from exc
        finally:
            temporary_path.unlink(missing_ok=True)
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

    def _ensure_directory_tree(self, target: Path) -> None:
        try:
            relative = target.relative_to(self.config.vault)
        except ValueError as exc:
            raise ManifestError("manifest shard directory escapes vault") from exc
        current = self.config.vault
        self._require_directory(current)
        for part in relative.parts:
            child = current / part
            if not child.exists() and not child.is_symlink():
                child.mkdir()
                self._fsync_directory(current)
            self._require_directory(child)
            current = child

    @staticmethod
    def _require_directory(path: Path) -> None:
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ManifestError("manifest shard directory must be ordinary")

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
        self.entry_path(source_id).unlink(missing_ok=True)

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
                self._validate_source_file(path)
                current[self.source_id(path)] = path
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
            self._validate_source_file(path)
            entry = tracked.get(source_id)
            if entry is None:
                result["new"].append(source_id)
            elif entry.content_hash != f"sha256:{compute_hash(path)}":
                result["modified"].append(source_id)
            else:
                result["unchanged"].append(source_id)
        for source_id in sorted(set(tracked) - selected):
            source = self.source_path(source_id)
            try:
                source.lstat()
            except FileNotFoundError:
                result["missing"].append(source_id)
            except OSError as exc:
                raise ManifestError("source cannot be inspected") from exc
            else:
                self._validate_source_file(source)
        return result

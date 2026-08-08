from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from obsidian_wiki.cache import compute_hash
from obsidian_wiki.config import PortableConfig


class ManifestError(ValueError):
    pass


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
            payload = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"{self.marker_path}: invalid manifest v2 marker: {exc}") from exc
        if payload != {
            "entries": ".manifest/sources",
            "schema_version": 2,
            "storage": "sharded",
        }:
            raise ManifestError(f"{self.marker_path}: invalid manifest v2 marker")

    def _repo_relative(self, path: Path, label: str) -> str:
        try:
            return path.resolve(strict=False).relative_to(self.config.root.resolve()).as_posix()
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
            raise ManifestError("source must be a file below the configured source root")
        return f"{self._repo_relative(self.source_root, 'source root')}/{relative.as_posix()}"

    def source_path(self, source_id: str) -> Path:
        self._validate_source_id(source_id)
        prefix = self._repo_relative(self.source_root, "source root")
        return self.config.root / PurePosixPath(source_id)

    def _validate_source_id(self, source_id: str) -> None:
        if not isinstance(source_id, str) or not source_id or "\\" in source_id:
            raise ManifestError(f"invalid Source ID: {source_id!r}")
        path = PurePosixPath(source_id)
        prefix = self._repo_relative(self.source_root, "source root")
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ManifestError(f"invalid Source ID: {source_id!r}")
        if source_id != prefix and not source_id.startswith(prefix + "/"):
            raise ManifestError(f"Source ID is outside the configured source root: {source_id}")

    def entry_path(self, source_id: str) -> Path:
        self._validate_source_id(source_id)
        prefix = self._repo_relative(self.source_root, "source root")
        relative = PurePosixPath(source_id).relative_to(PurePosixPath(prefix))
        candidate = self.entries_root / relative.parent / f"{relative.name}.json"
        try:
            candidate.resolve(strict=False).relative_to(self.entries_root.resolve(strict=False))
        except ValueError as exc:
            raise ManifestError(f"Source ID escapes the manifest shard root: {source_id}") from exc
        return candidate

    def load(self, source_id: str) -> ManifestEntry | None:
        path = self.entry_path(source_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"{path}: invalid manifest shard: {exc}") from exc
        if not isinstance(payload, dict):
            raise ManifestError(f"{path}: manifest shard must be an object")
        expected = {"compiled_at", "content_hash", "pages", "source_id"}
        if set(payload) != expected or payload.get("source_id") != source_id:
            raise ManifestError(f"{path}: manifest shard has invalid fields")
        pages = payload.get("pages")
        if not isinstance(pages, list) or any(not isinstance(page, str) for page in pages):
            raise ManifestError(f"{path}: manifest pages must be a string list")
        return ManifestEntry(
            source_id=source_id,
            content_hash=str(payload["content_hash"]),
            pages=tuple(pages),
            compiled_at=str(payload["compiled_at"]),
        )

    def iter_entries(self) -> list[ManifestEntry]:
        if not self.entries_root.exists():
            return []
        entries: list[ManifestEntry] = []
        seen: set[str] = set()
        for path in sorted(self.entries_root.rglob("*.json")):
            if not path.is_file() or path.is_symlink():
                raise ManifestError(f"{path}: manifest shard must be an ordinary file")
            relative = path.relative_to(self.entries_root)
            source_id = f"{self._repo_relative(self.source_root, 'source root')}/{relative.with_suffix('').as_posix()}"
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
            if path.is_absolute() or ".." in path.parts or "\\" in page:
                raise ManifestError(f"invalid manifest page path: {page!r}")
            normalized = path.as_posix()
            if normalized in ("", "."):
                raise ManifestError(f"invalid manifest page path: {page!r}")
            values.add(normalized)
        return tuple(sorted(values))

    def upsert(
        self,
        source: Path,
        *,
        pages: list[str] | None = None,
        compiled_at: str | None = None,
    ) -> ManifestEntry:
        source_path = Path(source)
        if not source_path.is_file() or source_path.is_symlink():
            raise ManifestError(f"source must be an ordinary file: {source_path}")
        source_id = self.source_id(source_path)
        entry = ManifestEntry(
            source_id=source_id,
            content_hash=f"sha256:{compute_hash(source_path)}",
            pages=self._normalize_pages(pages, self.config.vault),
            compiled_at=compiled_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        payload = {
            "compiled_at": entry.compiled_at,
            "content_hash": entry.content_hash,
            "pages": list(entry.pages),
            "source_id": entry.source_id,
        }
        target = self.entry_path(source_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(fd)
        temporary_path = Path(temporary)
        try:
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)
        return entry

    def remove(self, source_id: str) -> None:
        self.entry_path(source_id).unlink(missing_ok=True)

    def status(self) -> dict[str, list[str]]:
        tracked = {entry.source_id: entry for entry in self.iter_entries()}
        current: dict[str, Path] = {}
        if self.source_root.exists():
            for path in self.source_root.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(self.source_root)
                if any(part.startswith(".") for part in relative.parts) or path.name == ".gitkeep":
                    continue
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
            if not path.exists():
                result["missing"].append(str(path))
                continue
            source_id = self.source_id(path)
            selected.add(source_id)
            entry = tracked.get(source_id)
            if entry is None:
                result["new"].append(source_id)
            elif entry.content_hash != f"sha256:{compute_hash(path)}":
                result["modified"].append(source_id)
            else:
                result["unchanged"].append(source_id)
        for source_id in sorted(set(tracked) - selected):
            source = self.source_path(source_id)
            if not source.exists():
                result["missing"].append(source_id)
        return result

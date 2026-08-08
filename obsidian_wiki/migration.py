"""Read-only analysis for migrating a co-located legacy vault to portable mode."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from obsidian_wiki.frontmatter import FrontmatterError, parse_frontmatter

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_KNOWLEDGE_CATEGORIES = frozenset(
    {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
)
_SKIP_DIRECTORIES = frozenset(
    {".manifest", ".obsidian", "_archives", "_meta", "_raw", "_readouts", "_staging"}
)


@dataclass(frozen=True)
class MigrationBlocker:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class MigrationPlan:
    root: Path
    vault: Path
    source_root: Path
    source_mappings: tuple[tuple[str, str], ...]
    page_updates: tuple[str, ...]
    manifest_entries: tuple[str, ...]
    blockers: tuple[MigrationBlocker, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "root": _relative_display(self.root, self.root),
            "vault": _relative_display(self.vault, self.root),
            "source_root": _relative_display(self.source_root, self.root),
            "source_mappings": [list(mapping) for mapping in self.source_mappings],
            "page_updates": list(self.page_updates),
            "manifest_entries": list(self.manifest_entries),
            "blockers": [
                {
                    "code": blocker.code,
                    "source": blocker.source,
                    "message": blocker.message,
                }
                for blocker in self.blockers
            ],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _LegacyEntry:
    old_source: str
    content_hash: str
    pages: tuple[str, ...]
    ingested_at: str

    def normalized_payload(self) -> tuple[str, tuple[str, ...], str]:
        content_hash = self.content_hash
        if content_hash.lower().startswith("sha256:"):
            content_hash = content_hash.split(":", 1)[1]
        return (content_hash, tuple(sorted(set(self.pages))), self.ingested_at)


def _relative_display(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.as_posix()
    return relative.as_posix() if relative.parts else "."


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _ordinary_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _has_symlink_component(path: Path, *, below: Path) -> bool:
    lexical_path = _lexical_absolute(path)
    lexical_base = _lexical_absolute(below)
    try:
        relative = lexical_path.relative_to(lexical_base)
    except ValueError:
        return True
    current = lexical_base
    for part in relative.parts:
        current /= part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _iter_manifest_entries(sources: object) -> Iterator[_LegacyEntry]:
    raw_entries: Iterator[tuple[object, object]]
    if isinstance(sources, dict):
        raw_entries = iter(sources.items())
    elif isinstance(sources, list):
        raw_entries = (
            (entry.get("path") or entry.get("source_id"), entry)
            for entry in sources
            if isinstance(entry, dict)
        )
    else:
        return

    for raw_key, raw_entry in raw_entries:
        if not isinstance(raw_key, str) or not isinstance(raw_entry, dict):
            continue
        raw_pages = raw_entry.get("pages_produced")
        if raw_pages is None:
            raw_pages = raw_entry.get("pages", ())
        if not isinstance(raw_pages, list) or any(
            not isinstance(page, str) for page in raw_pages
        ):
            continue
        pages = tuple(raw_pages)
        content_hash = raw_entry.get("content_hash")
        ingested_at = raw_entry.get("last_ingested")
        if ingested_at is None:
            ingested_at = raw_entry.get("ingested_at")
        yield _LegacyEntry(
            old_source=raw_key,
            content_hash=content_hash if isinstance(content_hash, str) else "",
            pages=tuple(sorted(set(pages))),
            ingested_at=ingested_at if isinstance(ingested_at, str) else "",
        )


def _manifest_entry_errors(sources: object) -> tuple[MigrationBlocker, ...]:
    errors: list[MigrationBlocker] = []
    if isinstance(sources, dict):
        entries = tuple((str(key), key, value) for key, value in sources.items())
    elif isinstance(sources, list):
        entries = tuple(
            (f"sources[{index}]", None, value) for index, value in enumerate(sources)
        )
    else:
        return ()

    for label, dict_key, raw_entry in entries:
        if not isinstance(raw_entry, dict):
            errors.append(
                _blocker(
                    "manifest-invalid",
                    label,
                    "legacy manifest source entry must be an object",
                )
            )
            continue
        raw_key = dict_key
        if raw_key is None:
            raw_key = raw_entry.get("path") or raw_entry.get("source_id")
        if not isinstance(raw_key, str) or not raw_key:
            errors.append(
                _blocker(
                    "manifest-invalid",
                    label,
                    "legacy manifest source entry needs a string key, path, or source_id",
                )
            )

        raw_pages = raw_entry.get("pages_produced")
        if raw_pages is None:
            raw_pages = raw_entry.get("pages", [])
        if not isinstance(raw_pages, list) or any(
            not isinstance(page, str) for page in raw_pages
        ):
            errors.append(
                _blocker(
                    "manifest-invalid",
                    label,
                    "legacy manifest pages must be an array of strings",
                )
            )

        ingested_at = raw_entry.get("last_ingested")
        if ingested_at is None:
            ingested_at = raw_entry.get("ingested_at")
        if ingested_at is not None and not isinstance(ingested_at, str):
            errors.append(
                _blocker(
                    "manifest-invalid",
                    label,
                    "legacy manifest ingest timestamp must be a string when present",
                )
            )
        content_hash = raw_entry.get("content_hash")
        if content_hash is not None and not isinstance(content_hash, str):
            errors.append(
                _blocker(
                    "manifest-invalid",
                    label,
                    "legacy manifest content_hash must be a string when present",
                )
            )
    return tuple(errors)


def _path_kind(raw: str) -> str:
    windows = PureWindowsPath(raw)
    if windows.is_absolute() or windows.drive:
        return "path"
    if _URL_RE.match(raw):
        return "url"
    if _SCHEME_RE.match(raw):
        return "pseudo"
    return "path"


def _safe_page_path(raw: str) -> PurePosixPath | None:
    if not raw or "\\" in raw or "\x00" in raw:
        return None
    path = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        path.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or path.suffix != ".md"
        or not path.parts
        or path.parts[0] not in _KNOWLEDGE_CATEGORIES
        or path.parts[:2] == ("journal", "operations")
        or "." in path.parts
        or ".." in path.parts
        or any(part.startswith(".") for part in path.parts)
        or path.as_posix() != raw
    ):
        return None
    return path


def _knowledge_pages(vault: Path) -> Iterator[tuple[str, Path]]:
    if not vault.is_dir():
        return
    for path in sorted(vault.rglob("*.md")):
        try:
            relative = path.relative_to(vault)
        except ValueError:
            continue
        if not relative.parts or relative.parts[0] not in _KNOWLEDGE_CATEGORIES:
            continue
        if relative.parts[:2] == ("journal", "operations"):
            continue
        if any(part in _SKIP_DIRECTORIES for part in relative.parts[:-1]):
            continue
        yield relative.as_posix(), path


def _blocker(code: str, source: str, message: str) -> MigrationBlocker:
    return MigrationBlocker(code=code, source=source, message=message)


def _invalid_root_plan(
    *,
    root: Path,
    vault: Path,
    source_root: Path,
    blockers: list[MigrationBlocker],
) -> MigrationPlan:
    return MigrationPlan(
        root=root,
        vault=vault,
        source_root=source_root,
        source_mappings=(),
        page_updates=(),
        manifest_entries=(),
        blockers=tuple(sorted(blockers, key=lambda item: (item.code, item.source))),
        warnings=(),
    )


def analyze_migration(*, root: Path, vault: Path, source_root: Path) -> MigrationPlan:
    """Analyze a legacy manifest and its pages without changing the repository."""

    root = Path(root).expanduser().resolve(strict=False)
    vault = Path(vault).expanduser().resolve(strict=False)
    source_root = Path(source_root).expanduser().resolve(strict=False)
    blockers: list[MigrationBlocker] = []

    for label, path in (("vault", vault), ("source-root", source_root)):
        if not _contained(path, root):
            blockers.append(
                _blocker(
                    "outside-root",
                    str(path),
                    f"{label} must remain inside the migration repository",
                )
            )
    if _contained(vault, source_root) or _contained(source_root, vault):
        blockers.append(
            _blocker(
                "path-overlap",
                f"{vault} :: {source_root}",
                "vault and source root must not overlap",
            )
        )
    if blockers:
        return _invalid_root_plan(
            root=root,
            vault=vault,
            source_root=source_root,
            blockers=blockers,
        )

    manifest_path = vault / ".manifest.json"
    if not _ordinary_file(manifest_path):
        blockers.append(
            _blocker(
                "manifest-missing",
                _relative_display(manifest_path, root),
                "legacy manifest is missing or is not an ordinary file",
            )
        )
        return _invalid_root_plan(
            root=root,
            vault=vault,
            source_root=source_root,
            blockers=blockers,
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        blockers.append(
            _blocker(
                "manifest-invalid",
                _relative_display(manifest_path, root),
                f"legacy manifest cannot be read: {exc}",
            )
        )
        return _invalid_root_plan(
            root=root,
            vault=vault,
            source_root=source_root,
            blockers=blockers,
        )
    if (
        not isinstance(manifest, dict)
        or "sources" not in manifest
        or not isinstance(manifest["sources"], (dict, list))
    ):
        blockers.append(
            _blocker(
                "manifest-invalid",
                _relative_display(manifest_path, root),
                "legacy manifest sources must be an object or array",
            )
        )
        return _invalid_root_plan(
            root=root,
            vault=vault,
            source_root=source_root,
            blockers=blockers,
        )

    sources_data = manifest.get("sources", {})
    blockers.extend(_manifest_entry_errors(sources_data))

    mappings: list[tuple[str, str]] = []
    mapping_by_old: dict[str, str] = {}
    mapping_by_path: dict[Path, str] = {}
    payload_by_source_id: dict[str, tuple[str, tuple[str, ...], str]] = {}
    manifest_entries: set[str] = set()
    listed_pages: set[str] = set()

    for entry in _iter_manifest_entries(sources_data):
        old_source = entry.old_source
        for raw_page in entry.pages:
            safe_page = _safe_page_path(raw_page)
            if safe_page is None:
                blockers.append(
                    _blocker(
                        "unsafe-page",
                        raw_page,
                        "manifest page path must be a safe vault-relative Markdown path",
                    )
                )
                continue
            page_path = vault / safe_page
            if _has_symlink_component(page_path, below=vault):
                blockers.append(
                    _blocker(
                        "unsafe-page",
                        raw_page,
                        "manifest page path contains a symbolic-link component",
                    )
                )
                continue
            resolved_page = page_path.resolve(strict=False)
            if not _contained(resolved_page, vault):
                blockers.append(
                    _blocker(
                        "unsafe-page",
                        raw_page,
                        "manifest page path resolves outside the legacy vault",
                    )
                )
                continue
            if not _ordinary_file(page_path):
                blockers.append(
                    _blocker(
                        "missing-page",
                        raw_page,
                        "manifest page is missing or is not an ordinary file",
                    )
                )
                continue
            listed_pages.add(safe_page.as_posix())

        kind = _path_kind(old_source)
        if kind == "url":
            blockers.append(
                _blocker(
                    "live-url-source",
                    old_source,
                    "live URL sources must be captured below the configured source root",
                )
            )
            continue
        if kind == "pseudo":
            blockers.append(
                _blocker(
                    "pseudo-source",
                    old_source,
                    "pseudo-sources cannot become portable Source IDs",
                )
            )
            continue

        old_path = Path(old_source).expanduser()
        candidate = old_path if old_path.is_absolute() else vault / old_path
        lexical_candidate = _lexical_absolute(candidate)
        if not _contained(lexical_candidate, source_root):
            blockers.append(
                _blocker(
                    "external-source",
                    old_source,
                    "source is outside the configured source root",
                )
            )
            continue
        if _has_symlink_component(lexical_candidate, below=source_root):
            blockers.append(
                _blocker(
                    "unsafe-source",
                    old_source,
                    "source path contains a symbolic-link component",
                )
            )
            continue
        resolved = candidate.resolve(strict=False)
        if not _contained(resolved, source_root):
            blockers.append(
                _blocker(
                    "external-source",
                    old_source,
                    "source is outside the configured source root",
                )
            )
            continue
        if not _ordinary_file(resolved):
            blockers.append(
                _blocker(
                    "missing-source",
                    old_source,
                    "source is missing or is not an ordinary file",
                )
            )
            continue

        source_id = resolved.relative_to(root).as_posix()
        mappings.append((old_source, source_id))
        mapping_by_old[old_source] = source_id
        mapping_by_path[resolved] = source_id
        manifest_entries.add(source_id)
        payload = entry.normalized_payload()
        existing = payload_by_source_id.get(source_id)
        if existing is not None and existing != payload:
            blockers.append(
                _blocker(
                    "source-id-collision",
                    source_id,
                    "legacy entries map to one Source ID with different payloads",
                )
            )
        else:
            payload_by_source_id[source_id] = payload

    page_updates: set[str] = set()
    for relative, page_path in _knowledge_pages(vault):
        if _has_symlink_component(page_path, below=vault):
            blockers.append(
                _blocker(
                    "unsafe-page",
                    relative,
                    "knowledge page path contains a symbolic-link component",
                )
            )
            continue
        if not _contained(page_path.resolve(strict=False), vault):
            blockers.append(
                _blocker(
                    "unsafe-page",
                    relative,
                    "knowledge page path resolves outside the legacy vault",
                )
            )
            continue
        try:
            frontmatter = parse_frontmatter(page_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, FrontmatterError) as exc:
            blockers.append(
                _blocker(
                    "page-frontmatter-invalid",
                    relative,
                    f"knowledge page frontmatter cannot be read: {exc}",
                )
            )
            continue
        page_sources = frontmatter.lists.get("sources")
        if page_sources is None:
            scalar_source = frontmatter.scalars.get("sources")
            page_sources = (scalar_source,) if scalar_source is not None else ()
            if scalar_source is not None:
                page_updates.add(relative)
        for old_source in page_sources:
            mapped = mapping_by_old.get(old_source)
            if mapped is not None:
                if old_source != mapped:
                    page_updates.add(relative)
                continue
            if old_source in manifest_entries:
                continue
            kind = _path_kind(old_source)
            if kind == "url":
                blockers.append(
                    _blocker(
                        "live-url-source",
                        f"{relative}: {old_source}",
                        "page contains a live URL with no portable manifest mapping",
                    )
                )
                continue
            if kind == "pseudo":
                blockers.append(
                    _blocker(
                        "pseudo-source",
                        f"{relative}: {old_source}",
                        "page contains a pseudo-source with no portable manifest mapping",
                    )
                )
                continue
            old_path = Path(old_source).expanduser()
            windows_path = PureWindowsPath(old_source)
            if windows_path.is_absolute() or windows_path.drive:
                resolved = (
                    old_path.resolve(strict=False) if old_path.is_absolute() else None
                )
            else:
                candidate = old_path if old_path.is_absolute() else vault / old_path
                resolved = candidate.resolve(strict=False)
            if resolved is None:
                blockers.append(
                    _blocker(
                        "unmapped-page-source",
                        f"{relative}: {old_source}",
                        "page contains an absolute source with no manifest mapping",
                    )
                )
                continue
            mapped = mapping_by_path.get(resolved)
            if mapped is not None:
                mappings.append((old_source, mapped))
                mapping_by_old[old_source] = mapped
                page_updates.add(relative)
                continue
            blockers.append(
                _blocker(
                    "unmapped-page-source",
                    f"{relative}: {old_source}",
                    "page contains a source with no manifest mapping",
                )
            )

    warnings: list[str] = []
    unlisted_pages = sorted(page_updates - listed_pages)
    if unlisted_pages:
        warnings.append(
            "page source replacements are not listed by a legacy manifest entry: "
            + ", ".join(unlisted_pages)
        )

    unique_blockers = {
        (item.code, item.source, item.message): item for item in blockers
    }
    return MigrationPlan(
        root=root,
        vault=vault,
        source_root=source_root,
        source_mappings=tuple(sorted(set(mappings))),
        page_updates=tuple(sorted(page_updates)),
        manifest_entries=tuple(sorted(manifest_entries)),
        blockers=tuple(
            unique_blockers[key]
            for key in sorted(
                unique_blockers, key=lambda item: (item[0], item[1], item[2])
            )
        ),
        warnings=tuple(warnings),
    )

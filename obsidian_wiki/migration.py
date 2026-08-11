"""Read-only analysis for migrating a co-located legacy vault to portable mode."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.frontmatter import FrontmatterError, parse_frontmatter
from obsidian_wiki.operations import (
    OperationChange,
    operation_path,
    render_operation,
)
from obsidian_wiki.portable import (
    _BOOTSTRAP_REFERENCES,
    MANAGED_SKILLS_INVENTORY,
    PORTABLE_VAULT_DIRS,
    PROJECT_AGENT_DIRS,
    _managed_inventory_for_collection,
    _materialize_complete_skill_trees,
    _snapshot_bundled_skills,
    ensure_portable_gitattributes,
    ensure_portable_gitignore,
    install_portable_bootstrap,
    scaffold_portable_vault,
    write_portable_config,
)
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.skill_inventory import render_inventory

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
class _MigrationRecord:
    source_id: str
    pages: tuple[str, ...]
    ingested_at: str


@dataclass(frozen=True)
class _CandidateArtifact:
    data: bytes
    mode: int


@dataclass(frozen=True)
class _AppliedMutation:
    data: bytes | None
    identity: tuple[int, int] | None
    mode: int | None


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
    _records: tuple[_MigrationRecord, ...] = field(
        default=(), repr=False, compare=False
    )
    _preimages: tuple[tuple[str, str], ...] = field(
        default=(), repr=False, compare=False
    )
    _page_inventory: tuple[str, ...] = field(default=(), repr=False, compare=False)

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


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationResult:
    changed_files: tuple[str, ...]
    removed_files: tuple[str, ...]
    backup_dir: Path


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


def _digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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
    managed_paths = (
        root / ".obsidian-wiki",
        root / ".skills",
        root / "AGENTS.md",
        root / ".gitattributes",
        root / ".gitignore",
        *(root / relative for relative in _BOOTSTRAP_REFERENCES),
        *(root / relative for relative, _label in PROJECT_AGENT_DIRS),
    )
    for label, configured in (("source root", source_root), ("vault", vault)):
        for managed in managed_paths:
            if _contained(managed, configured) or _contained(configured, managed):
                blockers.append(
                    _blocker(
                        "managed-path-overlap",
                        _relative_display(configured, root),
                        f"{label} overlaps a framework-managed portable path",
                    )
                )
                break
    if blockers:
        return _invalid_root_plan(
            root=root,
            vault=vault,
            source_root=source_root,
            blockers=blockers,
        )

    portable_manifest_dir = vault / ".manifest"
    try:
        metadata = portable_manifest_dir.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        blockers.append(
            _blocker(
                "portable-artifact-conflict",
                _relative_display(portable_manifest_dir, root),
                f"existing portable manifest state cannot be inspected: {exc}",
            )
        )
    else:
        try:
            has_entries = stat.S_ISDIR(metadata.st_mode) and any(
                portable_manifest_dir.iterdir()
            )
        except OSError as exc:
            blockers.append(
                _blocker(
                    "portable-artifact-conflict",
                    _relative_display(portable_manifest_dir, root),
                    f"existing portable manifest state cannot be inspected: {exc}",
                )
            )
        else:
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                blockers.append(
                    _blocker(
                        "portable-artifact-conflict",
                        _relative_display(portable_manifest_dir, root),
                        "portable manifest path already exists and is not a directory",
                    )
                )
            elif has_entries:
                blockers.append(
                    _blocker(
                        "portable-artifact-conflict",
                        _relative_display(portable_manifest_dir, root),
                        "portable manifest artifacts already exist; clean or reconcile them before migration",
                    )
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
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
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
    preimages: dict[str, str] = {
        _relative_display(manifest_path, root): _digest_bytes(manifest_bytes)
    }

    mappings: list[tuple[str, str]] = []
    mapping_by_old: dict[str, str] = {}
    mapping_by_path: dict[Path, str] = {}
    payload_by_source_id: dict[str, tuple[str, tuple[str, ...], str]] = {}
    records_by_source_id: dict[str, _MigrationRecord] = {}
    page_edges: dict[str, set[str]] = {}
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
        try:
            source_bytes = resolved.read_bytes()
        except OSError as exc:
            blockers.append(
                _blocker(
                    "missing-source",
                    old_source,
                    f"source cannot be read: {exc}",
                )
            )
            continue

        source_id = resolved.relative_to(root).as_posix()
        preimages[source_id] = _digest_bytes(source_bytes)
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
            records_by_source_id[source_id] = _MigrationRecord(
                source_id=source_id,
                pages=(),
                ingested_at=entry.ingested_at,
            )
            page_edges.setdefault(source_id, set())

    page_updates: set[str] = set()
    page_inventory: set[str] = set()
    for relative, page_path in _knowledge_pages(vault):
        page_inventory.add(relative)
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
            page_bytes = page_path.read_bytes()
            frontmatter = parse_frontmatter(page_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, FrontmatterError) as exc:
            blockers.append(
                _blocker(
                    "page-frontmatter-invalid",
                    relative,
                    f"knowledge page frontmatter cannot be read: {exc}",
                )
            )
            continue
        preimages[_relative_display(page_path, root)] = _digest_bytes(page_bytes)
        page_sources = frontmatter.lists.get("sources")
        if page_sources is None:
            scalar_source = frontmatter.scalars.get("sources")
            page_sources = (scalar_source,) if scalar_source is not None else ()
            if scalar_source is not None:
                page_updates.add(relative)
        for old_source in page_sources:
            mapped = mapping_by_old.get(old_source)
            if mapped is not None:
                page_edges.setdefault(mapped, set()).add(relative)
                if old_source != mapped:
                    page_updates.add(relative)
                continue
            if old_source in manifest_entries:
                page_edges.setdefault(old_source, set()).add(relative)
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
                page_edges.setdefault(mapped, set()).add(relative)
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
        _records=tuple(
            _MigrationRecord(
                source_id=source_id,
                pages=tuple(sorted(page_edges.get(source_id, set()))),
                ingested_at=records_by_source_id[source_id].ingested_at,
            )
            for source_id in sorted(records_by_source_id)
        ),
        _preimages=tuple(sorted(preimages.items())),
        _page_inventory=tuple(sorted(page_inventory)),
    )


def _repo_relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise MigrationError(f"migration path escapes repository: {path}") from exc


def _verify_preimages(plan: MigrationPlan) -> None:
    if not plan._preimages:
        raise MigrationError("migration plan has no analysis preimages")
    for relative, expected in plan._preimages:
        path = plan.root / PurePosixPath(relative)
        if _has_symlink_component(path, below=plan.root) or not _ordinary_file(path):
            raise MigrationError(f"{relative} changed since analysis")
        try:
            current = _digest_bytes(path.read_bytes())
        except OSError as exc:
            raise MigrationError(f"{relative} changed since analysis: {exc}") from exc
        if current != expected:
            raise MigrationError(f"{relative} changed since analysis")
    current_inventory = tuple(
        sorted(relative for relative, _path in _knowledge_pages(plan.vault))
    )
    if current_inventory != plan._page_inventory:
        raise MigrationError("knowledge page set changed since analysis")
    portable_manifest_dir = plan.vault / ".manifest"
    try:
        metadata = portable_manifest_dir.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise MigrationError(
            f"portable manifest artifacts changed since analysis: {exc}"
        ) from exc
    try:
        has_entries = stat.S_ISDIR(metadata.st_mode) and any(
            portable_manifest_dir.iterdir()
        )
    except OSError as exc:
        raise MigrationError(
            f"portable manifest artifacts changed since analysis: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or has_entries:
        raise MigrationError("portable manifest artifacts changed since analysis")


def _frontmatter_source_values(data: bytes) -> tuple[str, ...]:
    try:
        parsed = parse_frontmatter(data.decode("utf-8"))
    except (UnicodeDecodeError, FrontmatterError) as exc:
        raise MigrationError(f"cannot rewrite page frontmatter: {exc}") from exc
    values = parsed.lists.get("sources")
    if values is not None:
        return values
    scalar = parsed.scalars.get("sources")
    if scalar is None:
        raise MigrationError("cannot rewrite page frontmatter without sources")
    return (scalar,)


def _line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _split_yaml_comment(raw: str) -> tuple[str, str]:
    quote: str | None = None
    inline = raw.lstrip().startswith("[")
    index = 0
    while index < len(raw):
        char = raw[index]
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif quote == "'":
            if char == quote:
                if index + 1 < len(raw) and raw[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in "'\"" and _starts_yaml_quote(raw, index, inline=inline):
            quote = char
        elif char == "#" and (index == 0 or raw[index - 1].isspace()):
            start = index
            while start > 0 and raw[start - 1] in " \t":
                start -= 1
            return raw[:start], raw[start:]
        index += 1
    return raw, ""


def _starts_yaml_quote(value: str, index: int, *, inline: bool) -> bool:
    previous = index - 1
    while previous >= 0 and value[previous].isspace():
        previous -= 1
    return previous < 0 or (inline and value[previous] in "[,")


def _source_field_bounds(lines: list[str]) -> tuple[int, int, bool]:
    if not lines or lines[0].strip() != "---":
        raise MigrationError("cannot rewrite page without leading frontmatter")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise MigrationError("cannot rewrite page with unclosed frontmatter")
    matches = [
        index
        for index, line in enumerate(lines[1:closing], 1)
        if line and not line[0].isspace() and line.split(":", 1)[0].strip() == "sources"
    ]
    if len(matches) != 1:
        raise MigrationError("cannot rewrite ambiguous frontmatter sources field")
    start = matches[0]
    content, _newline = _line_ending(lines[start])
    raw_value, _comment = _split_yaml_comment(content.split(":", 1)[1])
    is_block = raw_value.strip() == ""
    end = start + 1
    if is_block:
        while end < closing:
            line = lines[end]
            if not line.strip() or line.lstrip().startswith("#") or line[0].isspace():
                end += 1
                continue
            break
    return start, end, is_block


def _render_source_like(original: str, mapped: str) -> str:
    leading = original[: len(original) - len(original.lstrip())]
    trailing = original[len(original.rstrip()) :]
    token = original.strip()
    if token.startswith('"') and token.endswith('"'):
        rendered = json.dumps(mapped, ensure_ascii=False)
    elif token.startswith("'") and token.endswith("'"):
        rendered = "'" + mapped.replace("'", "''") + "'"
    elif re.fullmatch(r"[A-Za-z0-9_./:@+-]+", mapped):
        rendered = mapped
    else:
        rendered = json.dumps(mapped, ensure_ascii=False)
    return f"{leading}{rendered}{trailing}"


def _inline_segments(body: str) -> list[str]:
    segments: list[str] = []
    start = 0
    quote: str | None = None
    index = 0
    while index < len(body):
        char = body[index]
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif quote == "'":
            if char == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in "'\"" and _starts_yaml_quote(body, index, inline=True):
            quote = char
        elif char == ",":
            segments.append(body[start:index])
            start = index + 1
        index += 1
    segments.append(body[start:])
    return segments


def _rewrite_page_sources(
    data: bytes,
    *,
    mappings: dict[str, str],
    source_ids: set[str],
) -> bytes:
    values = _frontmatter_source_values(data)
    rewritten: list[str] = []
    for value in values:
        mapped = mappings.get(value, value if value in source_ids else None)
        if mapped is None:
            raise MigrationError(
                f"cannot rewrite frontmatter source without exact mapping: {value}"
            )
        rewritten.append(mapped)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - parsed above
        raise MigrationError("cannot rewrite non-UTF-8 page") from exc
    lines = text.splitlines(keepends=True)
    start, end, is_block = _source_field_bounds(lines)
    header, newline = _line_ending(lines[start])
    colon = header.index(":")
    header_prefix = header[: colon + 1]
    raw_header = header[colon + 1 :]
    value_part, comment = _split_yaml_comment(raw_header)

    if is_block:
        rewritten_lines = list(lines[start:end])
        value_index = 0
        for index in range(1, len(rewritten_lines)):
            content, item_newline = _line_ending(rewritten_lines[index])
            if not content.startswith("  - "):
                continue
            raw_item, item_comment = _split_yaml_comment(content[4:])
            if value_index >= len(rewritten):
                raise MigrationError("frontmatter sources list is ambiguous")
            rewritten_lines[index] = (
                "  - "
                + _render_source_like(raw_item, rewritten[value_index])
                + item_comment
                + item_newline
            )
            value_index += 1
        if value_index != len(rewritten):
            raise MigrationError("frontmatter sources list is ambiguous")
        field_lines = rewritten_lines
    elif value_part.strip().startswith("["):
        left = value_part.index("[")
        right = value_part.rfind("]")
        segments = _inline_segments(value_part[left + 1 : right])
        if len(segments) != len(rewritten):
            raise MigrationError("inline frontmatter sources list is ambiguous")
        rendered_body = ",".join(
            _render_source_like(segment, mapped)
            for segment, mapped in zip(segments, rewritten)
        )
        rendered_value = f"{value_part[: left + 1]}{rendered_body}{value_part[right:]}"
        field_lines = [f"{header_prefix}{rendered_value}{comment}{newline}"]
    else:
        if len(rewritten) != 1:
            raise MigrationError("scalar frontmatter sources field is ambiguous")
        scalar_style = value_part
        rendered_scalar = _render_source_like(scalar_style, rewritten[0]).strip()
        header_comment = comment
        field_lines = [f"{header_prefix}{header_comment}{newline}"]
        field_lines.append(f"  - {rendered_scalar}{newline}")
    return "".join([*lines[:start], *field_lines, *lines[end:]]).encode("utf-8")


_UNSET_PREIMAGE = object()


def _open_parent_fd(root: Path, path: Path) -> int:
    try:
        relative = path.parent.relative_to(root)
    except ValueError as exc:
        raise MigrationError(f"migration target escapes repository: {path}") from exc
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(root, flags)
    try:
        for part in relative.parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _ensure_directory_path(root: Path, directory: Path) -> None:
    try:
        relative = directory.relative_to(root)
    except ValueError as exc:
        raise MigrationError(
            f"migration directory escapes repository: {directory}"
        ) from exc
    if os.name == "nt":
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise MigrationError(
                    f"migration directory contains a symbolic link: {current}"
                )
            current.mkdir(exist_ok=True)
            if not current.is_dir():
                raise MigrationError(
                    f"migration directory is not ordinary: {current}"
                )
        return

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(root, flags)
    try:
        for part in relative.parts:
            created = False
            try:
                os.mkdir(part, mode=0o755, dir_fd=descriptor)
                created = True
                os.fsync(descriptor)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=descriptor)
            try:
                if created:
                    os.fchmod(child, 0o755)
                    os.fsync(child)
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _read_ordinary_at(parent_fd: int, name: str) -> bytes | None:
    image = _ordinary_image_at(parent_fd, name)
    return None if image is None else image[0]


def _ordinary_image_at(
    parent_fd: int, name: str
) -> tuple[bytes, int, tuple[int, int]] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise MigrationError("migration target changed to a non-ordinary file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return (
            b"".join(chunks),
            stat.S_IMODE(metadata.st_mode),
            (metadata.st_dev, metadata.st_ino),
        )
    finally:
        os.close(descriptor)


def _require_expected_at(
    parent_fd: int, name: str, expected: bytes | None | object
) -> None:
    if expected is _UNSET_PREIMAGE:
        return
    try:
        current = _read_ordinary_at(parent_fd, name)
    except OSError as exc:
        raise MigrationError("migration target changed during apply") from exc
    if current != expected:
        raise MigrationError("migration target changed during apply")


def _replace_at(
    parent_fd: int,
    name: str,
    data: bytes,
    *,
    mode: int,
    exclusive: bool,
    on_mutation: Callable[[tuple[int, int]], None] | None,
) -> tuple[int, int]:
    temporary_name = f".{name}.migration-{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(temporary_name, flags, 0o644, dir_fd=parent_fd)
        os.fchmod(descriptor, mode)
        metadata = os.fstat(descriptor)
        installed_identity = (metadata.st_dev, metadata.st_ino)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if on_mutation is not None:
                on_mutation(installed_identity)
            os.unlink(temporary_name, dir_fd=parent_fd)
        else:
            os.replace(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            if on_mutation is not None:
                on_mutation(installed_identity)
        installed = _ordinary_image_at(parent_fd, name)
        if installed is None:  # pragma: no cover - requires a concurrent unlink
            raise MigrationError("migration target disappeared during apply")
        if installed[2] != installed_identity:
            raise MigrationError("migration target ownership changed during apply")
        return installed_identity
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _unlink_owned_at(
    parent_fd: int,
    name: str,
    owned_identity: tuple[int, int],
    *,
    owned_data: bytes | None,
    owned_mode: int | None,
) -> None:
    tombstone = f".{name}.migration-owned-{secrets.token_hex(8)}"
    try:
        os.rename(
            name,
            tombstone,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return
    moved = _ordinary_image_at(parent_fd, tombstone)
    if moved is None:  # pragma: no cover - rename made the entry present
        raise MigrationError("migration-owned target disappeared during cleanup")
    if (
        moved[2] != owned_identity
        or (owned_data is not None and moved[0] != owned_data)
        or (owned_mode is not None and moved[1] != owned_mode)
    ):
        restored = False
        try:
            os.link(
                tombstone,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            restored = True
        except FileExistsError:
            pass
        if restored:
            os.unlink(tombstone, dir_fd=parent_fd)
        raise MigrationError(
            "migration target ownership changed during local restoration"
        )
    os.unlink(tombstone, dir_fd=parent_fd)


def _parent_fd_matches(root: Path, path: Path, parent_fd: int) -> bool:
    try:
        current_fd = _open_parent_fd(root, path)
    except (OSError, MigrationError):
        return False
    try:
        opened = os.fstat(parent_fd)
        current = os.fstat(current_fd)
        return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
    finally:
        os.close(current_fd)


def _restore_open_parent(
    parent_fd: int,
    name: str,
    installed_identity: tuple[int, int],
    installed_data: bytes,
    installed_mode: int,
    original: bytes | None,
    original_mode: int,
) -> None:
    _unlink_owned_at(
        parent_fd,
        name,
        installed_identity,
        owned_data=installed_data,
        owned_mode=installed_mode,
    )
    if original is not None:
        _replace_at(
            parent_fd,
            name,
            original,
            mode=original_mode,
            exclusive=True,
            on_mutation=None,
        )
    _fsync_open_parent(parent_fd)


def _fsync_open_parent(parent_fd: int) -> None:
    if os.name != "nt":
        os.fsync(parent_fd)


def _atomic_replace_bytes(
    path: Path,
    data: bytes,
    *,
    root: Path | None = None,
    expected: bytes | None | object = _UNSET_PREIMAGE,
    mode: int = 0o644,
    on_mutation: Callable[[tuple[int, int]], None] | None = None,
    on_revert: Callable[[], None] | None = None,
) -> None:
    if root is None or os.name == "nt":
        path.parent.mkdir(parents=True, exist_ok=True)
        if root is not None:
            current = _target_preimage(path, root=root)
            if expected is not _UNSET_PREIMAGE and current != expected:
                raise MigrationError("migration target changed during apply")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.migration-", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                if os.name != "nt":
                    os.fchmod(handle.fileno(), mode)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if root is not None and expected is None:
                temporary.rename(path)
            else:
                os.replace(temporary, path)
            if on_mutation is not None:
                metadata = path.lstat()
                on_mutation((metadata.st_dev, metadata.st_ino))
        finally:
            temporary.unlink(missing_ok=True)
        return

    parent_fd = _open_parent_fd(root, path)
    try:
        original_image = _ordinary_image_at(parent_fd, path.name)
        original = None if original_image is None else original_image[0]
        original_mode = 0o644 if original_image is None else original_image[1]
        _require_expected_at(parent_fd, path.name, expected)
        installed_identity = _replace_at(
            parent_fd,
            path.name,
            data,
            mode=mode,
            exclusive=original is None,
            on_mutation=on_mutation,
        )
        _fsync_open_parent(parent_fd)
        if not _parent_fd_matches(root, path, parent_fd):
            try:
                _restore_open_parent(
                    parent_fd,
                    path.name,
                    installed_identity,
                    data,
                    mode,
                    original,
                    original_mode,
                )
                if on_revert is not None:
                    on_revert()
            except BaseException as exc:
                raise MigrationError(
                    "migration target parent changed during apply and local restoration failed: "
                    f"{exc}"
                ) from exc
            raise MigrationError("migration target parent changed during apply")
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _write_exclusive_bytes(
    path: Path,
    data: bytes,
    *,
    root: Path,
    mode: int = 0o644,
    on_mutation: Callable[[tuple[int, int]], None] | None = None,
    on_revert: Callable[[], None] | None = None,
) -> None:
    _atomic_replace_bytes(
        path,
        data,
        root=root,
        expected=None,
        mode=mode,
        on_mutation=on_mutation,
        on_revert=on_revert,
    )


def _unlink_expected(
    path: Path,
    *,
    root: Path,
    expected: bytes | None,
    mode: int = 0o644,
    on_mutation: Callable[[None], None] | None = None,
    on_revert: Callable[[], None] | None = None,
) -> None:
    if os.name == "nt":
        current = _target_preimage(path, root=root)
        if current != expected:
            raise MigrationError("migration target changed during apply")
        path.unlink()
        if on_mutation is not None:
            on_mutation(None)
        return
    parent_fd = _open_parent_fd(root, path)
    try:
        _require_expected_at(parent_fd, path.name, expected)
        os.unlink(path.name, dir_fd=parent_fd)
        if on_mutation is not None:
            on_mutation(None)
        _fsync_open_parent(parent_fd)
        if not _parent_fd_matches(root, path, parent_fd):
            try:
                if expected is not None:
                    _replace_at(
                        parent_fd,
                        path.name,
                        expected,
                        mode=mode,
                        exclusive=True,
                        on_mutation=None,
                    )
                    if on_revert is not None:
                        on_revert()
            except BaseException as exc:
                raise MigrationError(
                    "migration target parent changed during apply and local restoration failed: "
                    f"{exc}"
                ) from exc
            raise MigrationError("migration target parent changed during apply")
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _seed_owner_file(
    root: Path, candidate_root: Path, relative: str
) -> bytes | None:
    source = root / PurePosixPath(relative)
    if not source.exists() and not source.is_symlink():
        return None
    if _has_symlink_component(source, below=root) or not _ordinary_file(source):
        raise MigrationError(
            f"managed migration target is not an ordinary file: {relative}"
        )
    data = source.read_bytes()
    destination = candidate_root / PurePosixPath(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    destination.chmod(stat.S_IMODE(source.lstat().st_mode))
    return data


def _verify_candidate_dependencies(
    root: Path, dependencies: dict[str, bytes | None]
) -> None:
    for relative, expected in sorted(dependencies.items()):
        try:
            current = _target_preimage(
                root / PurePosixPath(relative), root=root
            )
        except MigrationError as exc:
            raise MigrationError(
                f"{relative} changed while building candidates: {exc}"
            ) from exc
        if current != expected:
            raise MigrationError(f"{relative} changed while building candidates")


def _verify_source_dependencies(
    plan: MigrationPlan, dependencies: dict[str, bytes | None]
) -> None:
    _verify_candidate_dependencies(
        plan.root,
        {
            source_id: dependencies[source_id]
            for source_id in plan.manifest_entries
        },
    )


def _is_below(relative: PurePosixPath, prefix: PurePosixPath) -> bool:
    try:
        relative.relative_to(prefix)
    except ValueError:
        return False
    return True


def _build_migration_candidates(
    plan: MigrationPlan,
    *,
    candidate_root: Path,
    installed_version: str,
    source_skills: Path,
    completed_at: str,
    operation_suffix: str,
) -> tuple[
    dict[str, _CandidateArtifact],
    tuple[str, ...],
    str,
    dict[str, bytes | None],
]:
    root = plan.root
    vault_relative = PurePosixPath(_repo_relative(root, plan.vault))
    source_relative = PurePosixPath(_repo_relative(root, plan.source_root))
    candidate_root.mkdir(parents=True)

    owner_dependencies = {
        relative: _seed_owner_file(root, candidate_root, relative)
        for relative in (
            "AGENTS.md",
            ".gitattributes",
            ".gitignore",
            *_BOOTSTRAP_REFERENCES,
        )
    }

    write_portable_config(
        candidate_root,
        version=installed_version,
        vault=vault_relative.as_posix(),
        sources=(source_relative.as_posix(),),
    )
    candidate_vault = candidate_root / vault_relative
    owner_settings = tuple(
        (vault_relative / PurePosixPath(relative)).as_posix()
        for relative in (".obsidian/app.json", ".obsidian/appearance.json")
    )
    preserved_owner_settings: set[str] = set()
    for relative in owner_settings:
        owner_data = _seed_owner_file(root, candidate_root, relative)
        owner_dependencies[relative] = owner_data
        if owner_data is not None:
            preserved_owner_settings.add(relative)
    scaffold_portable_vault(candidate_vault)

    for record in plan._records:
        source = root / PurePosixPath(record.source_id)
        destination = candidate_root / PurePosixPath(record.source_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_data = source.read_bytes()
        owner_dependencies[record.source_id] = source_data
        destination.write_bytes(source_data)

    bundled_skills = _snapshot_bundled_skills(source_skills)
    canonical_skills = _materialize_complete_skill_trees(candidate_root, bundled_skills)
    install_portable_bootstrap(candidate_root)
    ensure_portable_gitattributes(candidate_root)
    ensure_portable_gitignore(candidate_root, vault_relative.as_posix())
    inventory = candidate_root / MANAGED_SKILLS_INVENTORY
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(
        render_inventory(
            _managed_inventory_for_collection(installed_version, canonical_skills)
        ),
        encoding="utf-8",
        newline="\n",
    )

    mappings = dict(plan.source_mappings)
    source_ids = set(plan.manifest_entries)
    for relative in plan.page_updates:
        original = plan.vault / PurePosixPath(relative)
        rewritten = _rewrite_page_sources(
            original.read_bytes(), mappings=mappings, source_ids=source_ids
        )
        target = candidate_vault / PurePosixPath(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(rewritten)
        target.chmod(stat.S_IMODE(original.lstat().st_mode))

    config = load_portable_config(
        candidate_root / ".obsidian-wiki/config.toml",
        installed_version=installed_version,
        implementation=IMPLEMENTATION_ID,
    )
    manifest = ShardedManifest(config)
    for record in plan._records:
        manifest.upsert(
            candidate_root / PurePosixPath(record.source_id),
            pages=list(record.pages),
            compiled_at=record.ingested_at or completed_at,
        )

    operation = OperationChange(
        transaction_id=f"migration-{completed_at[:10].replace('-', '')}-{operation_suffix}",
        completed_at=completed_at,
        source_ids=tuple(record.source_id for record in plan._records),
        created=(),
        updated=plan.page_updates,
        removed=(),
    )
    operation_target = operation_path(
        candidate_vault, operation, suffix=operation_suffix
    )
    operation_target.parent.mkdir(parents=True, exist_ok=True)
    operation_target.write_text(
        render_operation(operation), encoding="utf-8", newline="\n"
    )
    operation_relative = _repo_relative(candidate_root, operation_target)

    candidates: dict[str, _CandidateArtifact] = {}
    candidate_directories: list[str] = []
    local_prefix = PurePosixPath(".obsidian-wiki/local")
    for path in sorted(candidate_root.rglob("*")):
        relative = PurePosixPath(path.relative_to(candidate_root).as_posix())
        if _is_below(relative, source_relative) or _is_below(relative, local_prefix):
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise MigrationError(
                f"migration candidate contains a symbolic link: {relative}"
            )
        if stat.S_ISDIR(metadata.st_mode):
            candidate_directories.append(relative.as_posix())
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise MigrationError(f"migration candidate is not ordinary: {relative}")
        if relative.as_posix() in preserved_owner_settings:
            continue
        candidates[relative.as_posix()] = _CandidateArtifact(
            data=path.read_bytes(),
            mode=stat.S_IMODE(metadata.st_mode),
        )
    return (
        candidates,
        tuple(sorted(candidate_directories)),
        operation_relative,
        owner_dependencies,
    )


def _target_preimage(path: Path, *, root: Path) -> bytes | None:
    if _has_symlink_component(path, below=root):
        raise MigrationError(f"migration target contains a symbolic link: {path}")
    if not path.exists() and not path.is_symlink():
        return None
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MigrationError(f"cannot inspect migration target: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise MigrationError(f"migration target must be an ordinary file: {path}")
    return path.read_bytes()


def _target_mode(path: Path, *, root: Path) -> int | None:
    if _target_preimage(path, root=root) is None:
        return None
    return stat.S_IMODE(path.lstat().st_mode)


def _target_identity(path: Path, *, root: Path) -> tuple[int, int] | None:
    if _target_preimage(path, root=root) is None:
        return None
    metadata = path.lstat()
    return (metadata.st_dev, metadata.st_ino)


def _current_shard_paths(root: Path, vault: Path) -> tuple[str, ...]:
    shard_root = vault / ".manifest/sources"
    if not shard_root.exists() and not shard_root.is_symlink():
        return ()
    if _has_symlink_component(shard_root, below=root) or not shard_root.is_dir():
        raise MigrationError("portable manifest shard tree is unsafe")
    paths: list[str] = []
    for path in sorted(shard_root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise MigrationError("portable manifest shard tree contains a symbolic link")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise MigrationError("portable manifest shard tree contains a special file")
        paths.append(_repo_relative(root, path))
    return tuple(paths)


def _verify_page_promotion_state(
    plan: MigrationPlan, candidates: dict[str, _CandidateArtifact]
) -> None:
    current_inventory = tuple(
        sorted(relative for relative, _path in _knowledge_pages(plan.vault))
    )
    if current_inventory != plan._page_inventory:
        raise MigrationError("knowledge page set changed during apply")
    preimages = dict(plan._preimages)
    updated = set(plan.page_updates)
    for relative, page in _knowledge_pages(plan.vault):
        repository_relative = _repo_relative(plan.root, page)
        current = _target_preimage(page, root=plan.root)
        assert current is not None
        if relative in updated:
            artifact = candidates[repository_relative]
            if current != artifact.data or _target_mode(
                page, root=plan.root
            ) != artifact.mode:
                raise MigrationError(
                    f"knowledge page changed during apply: {relative}"
                )
        elif _digest_bytes(current) != preimages[repository_relative]:
            raise MigrationError(
                f"knowledge page changed during apply: {relative}"
            )


def _verify_promoted_postimages(
    root: Path,
    changed: dict[str, _CandidateArtifact],
    applied: dict[str, _AppliedMutation],
    *,
    operation_relative: str,
    include_operation: bool,
    removed_relative: str | None,
) -> None:
    for relative, artifact in changed.items():
        if relative == operation_relative and not include_operation:
            continue
        mutation = applied.get(relative)
        if mutation is None:
            raise MigrationError(f"migration postimage is missing: {relative}")
        target = root / PurePosixPath(relative)
        if (
            _target_preimage(target, root=root) != artifact.data
            or _target_mode(target, root=root) != artifact.mode
            or _target_identity(target, root=root) != mutation.identity
        ):
            raise MigrationError(f"migration postimage changed: {relative}")
    if removed_relative is not None:
        if removed_relative not in applied:
            raise MigrationError(
                f"migration deletion postimage is missing: {removed_relative}"
            )
        if _target_preimage(
            root / PurePosixPath(removed_relative), root=root
        ) is not None:
            raise MigrationError(
                f"migration deletion postimage changed: {removed_relative}"
            )


def _missing_parents(root: Path, paths: Iterator[Path]) -> tuple[Path, ...]:
    missing: set[Path] = set()
    for path in paths:
        parent = path.parent
        while parent != root:
            if parent.exists() or parent.is_symlink():
                break
            missing.add(parent)
            parent = parent.parent
    return tuple(sorted(missing, key=lambda item: len(item.parts)))


def _write_snapshot_manifest(
    migration_root: Path,
    snapshots: Path,
    originals: dict[str, bytes | None],
    original_modes: dict[str, int | None],
    created_parents: tuple[Path, ...],
    *,
    root: Path,
) -> None:
    snapshots.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for index, relative in enumerate(sorted(originals)):
        data = originals[relative]
        snapshot_name = f"{index:06d}.bin" if data is not None else None
        if data is not None:
            (snapshots / snapshot_name).write_bytes(data)
        records.append(
            {
                "originally_present": data is not None,
                "original_mode": original_modes[relative],
                "path": relative,
                "snapshot": snapshot_name,
            }
        )
    payload = {
        "created_parents": [_repo_relative(root, path) for path in created_parents],
        "implementation": IMPLEMENTATION_ID,
        "records": records,
        "schema_version": 1,
        "status": "prepared",
    }
    (migration_root / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _rollback_targets(
    root: Path,
    originals: dict[str, bytes | None],
    original_modes: dict[str, int | None],
    created_parents: tuple[Path, ...],
    applied: dict[str, _AppliedMutation],
) -> tuple[str, ...]:
    errors: list[str] = []
    for relative in reversed(tuple(applied)):
        target = root / PurePosixPath(relative)
        original = originals[relative]
        mutation = applied[relative]
        try:
            current = _target_preimage(target, root=root)
            current_mode = _target_mode(target, root=root)
            if mutation.data is None:
                if current is not None:
                    raise MigrationError("migration target changed before rollback")
            elif (
                current is None
                or _target_identity(target, root=root) != mutation.identity
                or current != mutation.data
                or current_mode != mutation.mode
            ):
                raise MigrationError("migration target changed before rollback")
            if original is None:
                if mutation.data is not None:
                    _unlink_expected(
                        target,
                        root=root,
                        expected=mutation.data,
                    )
            else:
                original_mode = original_modes[relative]
                assert original_mode is not None
                _atomic_replace_bytes(
                    target,
                    original,
                    root=root,
                    expected=mutation.data,
                    mode=original_mode,
                )
        except BaseException as exc:  # noqa: BLE001 - rollback is best-effort for interrupts too
            errors.append(f"{relative}: {exc}")
    for directory in sorted(
        created_parents, key=lambda item: len(item.parts), reverse=True
    ):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            if directory.exists() or directory.is_symlink():
                errors.append(f"{_repo_relative(root, directory)}: {exc}")
    return tuple(errors)


def _cleanup_failed_workspace(
    migration_root: Path, workspace_parents: tuple[tuple[Path, bool], ...]
) -> None:
    if migration_root.exists() and migration_root.is_dir():
        shutil.rmtree(migration_root)
    for path, existed in reversed(workspace_parents):
        if existed:
            continue
        try:
            path.rmdir()
        except OSError:
            pass


def _mark_migration_status(
    migration_root: Path,
    status: str,
    *,
    forward_error: str,
    rollback_errors: tuple[str, ...],
) -> None:
    manifest_path = migration_root / "manifest.json"
    if not manifest_path.is_file():
        return
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["status"] = status
    payload["forward_error"] = forward_error
    payload["rollback_errors"] = list(rollback_errors)
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def apply_migration(
    plan: MigrationPlan,
    *,
    installed_version: str,
    source_skills: Path,
) -> MigrationResult:
    """Apply one analyzed migration, retaining local snapshots after success."""
    if plan.blockers:
        raise MigrationError(
            f"migration plan has {len(plan.blockers)} blocker(s); apply refused"
        )
    _verify_preimages(plan)

    root = plan.root
    workspace_paths = (
        root / ".obsidian-wiki",
        root / ".obsidian-wiki/local",
        root / ".obsidian-wiki/local/migrations",
    )
    workspace_parents = tuple(
        (path, path.exists() or path.is_symlink()) for path in workspace_paths
    )
    migrations = workspace_paths[-1]
    if _has_symlink_component(migrations, below=root):
        raise MigrationError("migration workspace contains a symbolic link")
    try:
        migrations.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        for path, existed in reversed(workspace_parents):
            if not existed:
                try:
                    path.rmdir()
                except OSError:
                    pass
        raise MigrationError(f"cannot create migration workspace: {exc}") from exc
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for _attempt in range(128):
        operation_suffix = secrets.token_hex(4)
        migration_root = migrations / f"migration-{stamp}-{operation_suffix}"
        try:
            migration_root.mkdir()
        except FileExistsError:
            continue
        break
    else:  # pragma: no cover - requires 128 random collisions
        raise MigrationError("cannot allocate a migration workspace")
    candidate_root = migration_root / "candidates/repository"
    snapshots = migration_root / "snapshots"
    originals: dict[str, bytes | None] = {}
    original_modes: dict[str, int | None] = {}
    applied: dict[str, _AppliedMutation] = {}
    created_parents: tuple[Path, ...] = ()

    try:
        completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        (
            candidates,
            candidate_directories,
            operation_relative,
            candidate_dependencies,
        ) = _build_migration_candidates(
            plan,
            candidate_root=candidate_root,
            installed_version=installed_version,
            source_skills=Path(source_skills),
            completed_at=completed_at,
            operation_suffix=operation_suffix,
        )
        _verify_preimages(plan)
        _verify_candidate_dependencies(root, candidate_dependencies)

        hot_relative = _repo_relative(root, plan.vault / "hot.md")
        hot_path = root / PurePosixPath(hot_relative)
        removed_files = (
            (hot_relative,) if hot_path.exists() or hot_path.is_symlink() else ()
        )
        vault_prefix = PurePosixPath(_repo_relative(root, plan.vault))
        shard_prefix = vault_prefix / ".manifest/sources"

        changed: dict[str, _CandidateArtifact] = {}
        for relative, artifact in candidates.items():
            target = root / PurePosixPath(relative)
            current = _target_preimage(target, root=root)
            if (
                _is_below(PurePosixPath(relative), shard_prefix)
                and current is not None
            ):
                raise MigrationError(
                    f"planned manifest shard appeared during apply: {relative}"
                )
            dependency = candidate_dependencies.get(relative, _UNSET_PREIMAGE)
            if dependency is not _UNSET_PREIMAGE and current != dependency:
                raise MigrationError(
                    f"{relative} changed while building candidates"
                )
            if relative == operation_relative and current is not None:
                raise MigrationError(
                    f"migration operation path already exists: {relative}"
                )
            if current != artifact.data or (
                current is not None
                and _target_mode(target, root=root) != artifact.mode
            ):
                changed[relative] = artifact
                originals[relative] = current
                original_modes[relative] = _target_mode(target, root=root)
        if removed_files and hot_relative not in originals:
            originals[hot_relative] = _target_preimage(hot_path, root=root)
            original_modes[hot_relative] = _target_mode(hot_path, root=root)

        required_directories = {
            plan.vault / PurePosixPath(relative) for relative in PORTABLE_VAULT_DIRS
        }
        required_directories.add(plan.vault / ".manifest/sources")
        required_directories.update(
            root / PurePosixPath(relative) for relative in candidate_directories
        )
        for directory in required_directories:
            if _has_symlink_component(directory, below=root):
                raise MigrationError(
                    f"portable structural path contains a symbolic link: {directory}"
                )
            if directory.exists() and not directory.is_dir():
                raise MigrationError(
                    f"portable structural path is not a directory: {directory}"
                )
        all_targets = [
            *(root / PurePosixPath(relative) for relative in originals),
            *(
                directory / ".migration-directory-placeholder"
                for directory in required_directories
            ),
        ]
        created_parents = _missing_parents(root, iter(all_targets))
        _write_snapshot_manifest(
            migration_root,
            snapshots,
            originals,
            original_modes,
            created_parents,
            root=root,
        )

        directories_to_create = {
            *required_directories,
            *(root / PurePosixPath(relative).parent for relative in originals),
        }
        for directory in sorted(
            directories_to_create, key=lambda item: len(item.parts)
        ):
            _ensure_directory_path(root, directory)

        page_targets = {
            _repo_relative(root, plan.vault / PurePosixPath(relative))
            for relative in plan.page_updates
        }
        marker_relative = (vault_prefix / ".manifest.json").as_posix()
        index_relative = (vault_prefix / "index.md").as_posix()
        log_relative = (vault_prefix / "log.md").as_posix()
        planned_shards = tuple(
            sorted(
                relative
                for relative in candidates
                if _is_below(PurePosixPath(relative), shard_prefix)
            )
        )

        def priority(relative: str) -> tuple[int, str]:
            if relative == ".obsidian-wiki/config.toml":
                return (0, relative)
            if relative in (".gitattributes", ".gitignore"):
                return (1, relative)
            if relative == operation_relative:
                return (100, relative)
            if relative in page_targets:
                return (30, relative)
            if _is_below(PurePosixPath(relative), shard_prefix):
                return (40, relative)
            if relative == marker_relative:
                return (50, relative)
            if relative in (index_relative, log_relative):
                return (60, relative)
            return (20, relative)

        ordinary_changes = [
            relative for relative in changed if relative != operation_relative
        ]
        for relative in sorted(ordinary_changes, key=priority):
            if _is_below(PurePosixPath(relative), shard_prefix):
                _verify_source_dependencies(plan, candidate_dependencies)
            if relative == marker_relative:
                _verify_source_dependencies(plan, candidate_dependencies)
                _verify_page_promotion_state(plan, candidates)
                if _current_shard_paths(root, plan.vault) != planned_shards:
                    raise MigrationError(
                        "portable manifest shard tree changed during apply"
                    )
            artifact = changed[relative]
            _atomic_replace_bytes(
                root / PurePosixPath(relative),
                artifact.data,
                root=root,
                expected=originals[relative],
                mode=artifact.mode,
                on_mutation=lambda identity, relative=relative, data=artifact.data, mode=artifact.mode: applied.__setitem__(
                    relative,
                    _AppliedMutation(
                        data=data, identity=identity, mode=mode
                    ),
                ),
                on_revert=lambda relative=relative: applied.pop(relative, None),
            )
        if removed_files:
            hot_original = originals[hot_relative]
            assert hot_original is not None
            hot_mode = original_modes[hot_relative]
            assert hot_mode is not None
            _unlink_expected(
                hot_path,
                root=root,
                expected=hot_original,
                mode=hot_mode,
                on_mutation=lambda _identity: applied.__setitem__(
                    hot_relative,
                    _AppliedMutation(data=None, identity=None, mode=None),
                ),
                on_revert=lambda: applied.pop(hot_relative, None),
            )
        if operation_relative in changed:
            _verify_source_dependencies(plan, candidate_dependencies)
            _verify_page_promotion_state(plan, candidates)
            if _current_shard_paths(root, plan.vault) != planned_shards:
                raise MigrationError(
                    "portable manifest shard tree changed during apply"
                )
            _verify_promoted_postimages(
                root,
                changed,
                applied,
                operation_relative=operation_relative,
                include_operation=False,
                removed_relative=hot_relative if removed_files else None,
            )
            operation_artifact = changed[operation_relative]
            _write_exclusive_bytes(
                root / PurePosixPath(operation_relative),
                operation_artifact.data,
                root=root,
                mode=operation_artifact.mode,
                on_mutation=lambda identity: applied.__setitem__(
                    operation_relative,
                    _AppliedMutation(
                        data=operation_artifact.data,
                        identity=identity,
                        mode=operation_artifact.mode,
                    ),
                ),
                on_revert=lambda: applied.pop(operation_relative, None),
            )
            _verify_source_dependencies(plan, candidate_dependencies)
            _verify_page_promotion_state(plan, candidates)
            if _current_shard_paths(root, plan.vault) != planned_shards:
                raise MigrationError(
                    "portable manifest shard tree changed during apply"
                )
            _verify_promoted_postimages(
                root,
                changed,
                applied,
                operation_relative=operation_relative,
                include_operation=True,
                removed_relative=hot_relative if removed_files else None,
            )

        manifest_payload = json.loads((migration_root / "manifest.json").read_text())
        manifest_payload["status"] = "committed"
        (migration_root / "manifest.json").write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        shutil.rmtree(candidate_root.parent)
        return MigrationResult(
            changed_files=tuple(sorted(changed)),
            removed_files=removed_files,
            backup_dir=snapshots,
        )
    except BaseException as exc:
        rollback_errors = _rollback_targets(
            root, originals, original_modes, created_parents, applied
        )
        if rollback_errors:
            forward_error = f"{type(exc).__name__}: {exc}"
            try:
                _mark_migration_status(
                    migration_root,
                    "rollback-failed",
                    forward_error=forward_error,
                    rollback_errors=rollback_errors,
                )
            except BaseException as status_error:  # noqa: BLE001 - retain all rollback failures
                rollback_errors = (
                    *rollback_errors,
                    f"cannot update migration status: {status_error}",
                )
            details = "; ".join(rollback_errors)
            raise MigrationError(
                "migration failed and rollback was incomplete: "
                f"{exc}; {details}; backups retained at {snapshots}"
            ) from exc
        _cleanup_failed_workspace(migration_root, workspace_parents)
        raise MigrationError(f"migration failed and was rolled back: {exc}") from exc

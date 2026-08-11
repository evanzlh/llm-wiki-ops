"""Read-only deterministic validation for portable repositories."""

from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from . import IMPLEMENTATION_ID, __version__
from .cache import compute_hash
from .config import ConfigError, PortableConfig, load_portable_config
from .frontmatter import FrontmatterError, parse_frontmatter
from .git_support import discover_git_root, tracked_paths
from .lint import lint_vault
from .operations import OperationError, validate_operation
from .portable import (
    _BOOTSTRAP_REFERENCES,
    _INDEX,
    _LOG,
    _PORTABLE_AGENT_INSTRUCTIONS,
    MANAGED_END,
    MANAGED_SKILLS_INVENTORY,
    MANAGED_START,
    PROJECT_AGENT_DIRS,
    _bootstrap_body,
    render_portable_gitattributes,
)
from .portable_manifest import ManifestEntry, ManifestError, ShardedManifest
from .skill_inventory import (
    LegacyManagedSkillsInventory,
    ManagedSkillsInventory,
    read_inventory,
)
from .skill_trees import (
    SkillCollection,
    SkillEntry,
    discover_skill_collection,
    snapshot_ordinary_tree_with_unsafe,
)


@dataclass(frozen=True)
class CheckIssue:
    code: str
    path: str
    message: str
    severity: Literal["warning", "error"] = "error"


_REQUIRED_FIELDS = {"title", "category", "tags", "sources", "created", "updated"}
_KNOWLEDGE_CATEGORIES = (
    "concepts",
    "entities",
    "skills",
    "references",
    "synthesis",
    "journal",
    "projects",
)
_LFS_SIGNATURE = b"version https://git-lfs.github.com/spec/v1"


def _rel(root: Path, path: Path) -> str:
    """Return one lexical repository-relative path without following symlinks."""
    root_absolute = Path(os.path.abspath(os.fspath(root)))
    path_absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError:
        return "."
    value = relative.as_posix()
    return value if value else "."


def _scrub(root: Path, message: object) -> str:
    """Remove clone-specific absolute roots from diagnostics."""
    text = str(message)
    candidates = {
        str(root),
        str(Path(os.path.abspath(os.fspath(root)))),
        str(root.resolve(strict=False)),
    }
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate:
            text = text.replace(candidate, ".")
    return text


def _ordinary_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_nlink == 1
    )


def _ordinary_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _has_symlink_component(root: Path, path: Path) -> bool:
    try:
        relative = Path(os.path.abspath(os.fspath(path))).relative_to(
            Path(os.path.abspath(os.fspath(root)))
        )
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _safe_page_path(config: PortableConfig, page_name: str) -> Path | None:
    posix = PurePosixPath(page_name)
    windows = PureWindowsPath(page_name)
    if (
        not page_name
        or page_name == "."
        or "\\" in page_name
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or posix.as_posix() != page_name
    ):
        return None
    candidate = config.vault.joinpath(*posix.parts)
    try:
        candidate.resolve(strict=False).relative_to(config.vault.resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _report(issues: list[CheckIssue]) -> dict[str, object]:
    issues.sort(key=lambda item: (item.severity, item.code, item.path, item.message))
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    return {
        "status": "fail" if errors else ("warn" if warnings else "pass"),
        "errors": errors,
        "warnings": warnings,
        "issues": [asdict(issue) for issue in issues],
    }


def _reload_config(
    config: PortableConfig, issues: list[CheckIssue]
) -> PortableConfig | None:
    root = config.root
    config_path = root / ".obsidian-wiki/config.toml"
    if (
        not _ordinary_file(config_path)
        or _has_symlink_component(root, config_path)
        or config.path != config_path
    ):
        issues.append(
            CheckIssue(
                "config-invalid",
                ".obsidian-wiki/config.toml",
                "portable configuration must be an ordinary contained file",
            )
        )
        return None
    try:
        loaded = load_portable_config(
            config_path,
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
    except ConfigError as exc:
        issues.append(
            CheckIssue(
                "config-invalid",
                ".obsidian-wiki/config.toml",
                _scrub(root, exc),
            )
        )
        return None
    if loaded.root != root or loaded.path != config_path:
        issues.append(
            CheckIssue(
                "config-invalid",
                ".obsidian-wiki/config.toml",
                "portable configuration escapes the repository",
            )
        )
        return None
    for label, path in (
        ("vault", loaded.vault),
        ("skills", loaded.skills),
        ("local state", loaded.local_state),
        *(("source", source) for source in loaded.sources),
    ):
        try:
            path.relative_to(root)
        except ValueError:
            issues.append(
                CheckIssue(
                    "config-invalid",
                    ".obsidian-wiki/config.toml",
                    f"configured {label} path escapes the repository",
                )
            )
            return None
    if loaded.skills != root / ".skills":
        issues.append(
            CheckIssue(
                "config-invalid",
                ".obsidian-wiki/config.toml",
                "portable canonical skills path must be .skills",
            )
        )
        return None
    return loaded


def _load_manifest(
    config: PortableConfig, issues: list[CheckIssue]
) -> tuple[ShardedManifest | None, list[ManifestEntry]]:
    try:
        store = ShardedManifest(config)
        entries = store.iter_entries()
    except ManifestError as exc:
        issues.append(
            CheckIssue(
                "manifest-invalid",
                _rel(config.root, config.vault / ".manifest.json"),
                _scrub(config.root, exc),
            )
        )
        return None, []
    return store, entries


def _source_files(
    store: ShardedManifest, issues: list[CheckIssue]
) -> tuple[dict[str, Path], set[str]]:
    current: dict[str, Path] = {}
    invalid: set[str] = set()
    if not _ordinary_directory(store.source_root):
        return current, invalid
    for path in sorted(store.source_root.rglob("*")):
        relative = path.relative_to(store.source_root)
        if (
            any(part.startswith(".") for part in relative.parts)
            or path.name == ".gitkeep"
        ):
            continue
        try:
            metadata = path.lstat()
        except OSError:
            issues.append(
                CheckIssue(
                    "source-invalid",
                    _rel(store.config.root, path),
                    "source cannot be inspected as an ordinary file",
                )
            )
            continue
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            continue
        try:
            source_id = store.source_id(path)
        except ManifestError:
            source_id = _rel(store.config.root, path)
        current[source_id] = path
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or _has_symlink_component(store.source_root, path)
        ):
            invalid.add(source_id)
            issues.append(
                CheckIssue(
                    "source-invalid",
                    source_id,
                    "source must be a single-link ordinary contained file",
                )
            )
    return current, invalid


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(len(_LFS_SIGNATURE)) == _LFS_SIGNATURE
    except OSError:
        return False


def _check_sources(
    store: ShardedManifest,
    entries: list[ManifestEntry],
    issues: list[CheckIssue],
) -> None:
    tracked = {entry.source_id: entry for entry in entries}
    current, invalid = _source_files(store, issues)
    for source_id, path in sorted(current.items()):
        if source_id in invalid:
            continue
        entry = tracked.get(source_id)
        lfs_pointer = _is_lfs_pointer(path)
        if lfs_pointer:
            issues.append(
                CheckIssue(
                    "unsupported-git-lfs-pointer",
                    source_id,
                    "Git LFS pointer is not authoritative source content",
                )
            )
            continue
        if entry is None:
            issues.append(
                CheckIssue(
                    "source-new", source_id, "source is not present in the manifest"
                )
            )
            continue
        try:
            current_hash = f"sha256:{compute_hash(path)}"
        except OSError as exc:
            issues.append(
                CheckIssue("source-invalid", source_id, _scrub(store.config.root, exc))
            )
            continue
        if entry.content_hash != current_hash:
            issues.append(
                CheckIssue(
                    "source-stale",
                    source_id,
                    "source content differs from the manifest",
                )
            )
    for source_id in sorted(set(tracked) - set(current)):
        issues.append(
            CheckIssue("source-orphaned", source_id, "manifest source does not exist")
        )


def _knowledge_pages(config: PortableConfig) -> list[Path]:
    pages: list[Path] = []
    for category in _KNOWLEDGE_CATEGORIES:
        category_root = config.vault / category
        if not _ordinary_directory(category_root):
            continue
        for page in sorted(category_root.rglob("*.md")):
            relative = page.relative_to(config.vault)
            if relative.parts[:2] == ("journal", "operations"):
                continue
            pages.append(page)
    return sorted(set(pages))


def _source_is_absolute(source_id: str) -> bool:
    posix = PurePosixPath(source_id)
    windows = PureWindowsPath(source_id)
    return posix.is_absolute() or windows.is_absolute() or bool(windows.drive)


def _check_pages(
    config: PortableConfig,
    store: ShardedManifest | None,
    entries: list[ManifestEntry],
    issues: list[CheckIssue],
) -> None:
    entries_by_id = {entry.source_id: entry for entry in entries}
    page_sources: dict[str, tuple[str, ...]] = {}

    for page in _knowledge_pages(config):
        repo_path = _rel(config.root, page)
        vault_path = page.relative_to(config.vault).as_posix()
        if not _ordinary_file(page) or _has_symlink_component(config.vault, page):
            issues.append(
                CheckIssue(
                    "knowledge-page-invalid",
                    repo_path,
                    "knowledge page must be an ordinary contained file",
                )
            )
            continue
        try:
            parsed = parse_frontmatter(page.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, FrontmatterError) as exc:
            issues.append(
                CheckIssue("frontmatter-invalid", repo_path, _scrub(config.root, exc))
            )
            continue
        fields = parsed.fields
        missing = sorted(_REQUIRED_FIELDS - fields)
        if missing:
            issues.append(
                CheckIssue(
                    "frontmatter-missing",
                    repo_path,
                    "missing: " + ", ".join(missing),
                )
            )
        if "sources" in parsed.scalars:
            issues.append(
                CheckIssue(
                    "frontmatter-invalid",
                    repo_path,
                    "sources must be a list of portable Source IDs",
                )
            )
            sources: tuple[str, ...] = ()
        else:
            sources = parsed.lists.get("sources", ())
        page_sources[vault_path] = sources
        if store is None:
            continue
        for source_id in sources:
            if _source_is_absolute(source_id):
                issues.append(
                    CheckIssue(
                        "absolute-page-source",
                        repo_path,
                        "page source must be repository-relative",
                    )
                )
                continue
            try:
                store.source_path(source_id)
            except ManifestError:
                prefix = _rel(config.root, store.source_root)
                code = (
                    "page-source-outside-root"
                    if source_id != prefix and not source_id.startswith(prefix + "/")
                    else "page-source-invalid"
                )
                issues.append(
                    CheckIssue(
                        code,
                        repo_path,
                        f"invalid portable Source ID: {source_id}",
                    )
                )
                continue
            entry = entries_by_id.get(source_id)
            if entry is None:
                issues.append(
                    CheckIssue(
                        "page-source-unknown",
                        repo_path,
                        f"page source has no manifest entry: {source_id}",
                    )
                )
            elif vault_path not in entry.pages:
                issues.append(
                    CheckIssue(
                        "page-manifest-edge-missing",
                        repo_path,
                        f"manifest entry does not list this page: {source_id}",
                    )
                )

    for entry in entries:
        for page_name in entry.pages:
            page = _safe_page_path(config, page_name)
            repo_path = (
                _rel(config.root, config.vault / page_name)
                if page is not None
                else _rel(config.root, config.vault)
            )
            if page is None:
                issues.append(
                    CheckIssue(
                        "manifest-page-invalid",
                        repo_path,
                        f"unsafe manifest page path: {page_name}",
                    )
                )
                continue
            if not page.exists() and not page.is_symlink():
                issues.append(
                    CheckIssue(
                        "manifest-page-missing",
                        repo_path,
                        "manifest page does not exist",
                    )
                )
                continue
            if not _ordinary_file(page) or _has_symlink_component(config.vault, page):
                issues.append(
                    CheckIssue(
                        "manifest-page-invalid",
                        repo_path,
                        "manifest page must be an ordinary contained file",
                    )
                )
                continue
            if entry.source_id not in page_sources.get(page_name, ()):
                issues.append(
                    CheckIssue(
                        "manifest-page-source-missing",
                        repo_path,
                        f"page does not list manifest source: {entry.source_id}",
                    )
                )


def _check_operations(
    config: PortableConfig,
    store: ShardedManifest | None,
    issues: list[CheckIssue],
) -> None:
    root = config.vault / "journal" / "operations"
    if not root.exists() and not root.is_symlink():
        return
    if not _ordinary_directory(root) or _has_symlink_component(config.vault, root):
        issues.append(
            CheckIssue(
                "operation-invalid",
                _rel(config.root, root),
                "operation journal must be an ordinary contained directory",
            )
        )
        return

    transactions: dict[str, str] = {}
    for directory, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(directory)
        safe_directories: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            if _ordinary_directory(child) and not _has_symlink_component(root, child):
                safe_directories.append(name)
            else:
                issues.append(
                    CheckIssue(
                        "operation-invalid",
                        _rel(config.root, child),
                        "operation directory must be ordinary and contained",
                    )
                )
        dirnames[:] = safe_directories
        for name in sorted(filenames):
            operation = current / name
            issue_path = _rel(config.root, operation)
            if (
                operation.suffix != ".md"
                or not _ordinary_file(operation)
                or _has_symlink_component(root, operation)
            ):
                issues.append(
                    CheckIssue(
                        "operation-invalid",
                        issue_path,
                        "operation page must be a Markdown single-link ordinary file",
                    )
                )
                continue
            try:
                change = validate_operation(operation, vault=config.vault)
                if store is not None:
                    for source_id in change.source_ids:
                        store.source_path(source_id)
            except (ManifestError, OperationError) as exc:
                issues.append(
                    CheckIssue(
                        "operation-invalid",
                        issue_path,
                        _scrub(config.root, exc),
                    )
                )
                continue
            previous = transactions.get(change.transaction_id)
            if previous is not None:
                issues.append(
                    CheckIssue(
                        "operation-duplicate-transaction",
                        issue_path,
                        "duplicate transaction ID also used by " + previous,
                    )
                )
            else:
                transactions[change.transaction_id] = issue_path


def _lint_path(config: PortableConfig, page: str) -> str:
    return _rel(config.root, config.vault / PurePosixPath(page))


def _operation_finding(finding: object) -> bool:
    if not isinstance(finding, dict):
        return False
    page = finding.get("page")
    return isinstance(page, str) and (
        page == "journal/operations" or page.startswith("journal/operations/")
    )


def _lint_page_topology_is_safe(config: PortableConfig) -> bool:
    """Return whether legacy lint can inspect the vault without following links."""
    if not _ordinary_directory(config.vault) or _has_symlink_component(
        config.root, config.vault
    ):
        return False
    try:
        pages = config.vault.rglob("*.md")
        return all(
            _ordinary_file(page) and not _has_symlink_component(config.vault, page)
            for page in pages
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _check_lint(config: PortableConfig, issues: list[CheckIssue]) -> None:
    if not _lint_page_topology_is_safe(config):
        issues.append(
            CheckIssue(
                "lint-invalid",
                ".",
                "lint skipped because the vault Markdown topology is unsafe",
            )
        )
        return
    try:
        report = lint_vault(
            config.vault,
            require_trust_ledger=False,
            strict_trust=False,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        issues.append(CheckIssue("lint-invalid", ".", _scrub(config.root, exc)))
        return
    findings = report.get("findings", {})
    if not isinstance(findings, dict):
        issues.append(CheckIssue("lint-invalid", ".", "lint returned invalid findings"))
        return
    for finding in findings.get("broken_links", []):
        if _operation_finding(finding) or not isinstance(finding, dict):
            continue
        page = finding.get("page", ".")
        target = finding.get("target", "")
        path = _lint_path(config, page) if isinstance(page, str) else "."
        issues.append(
            CheckIssue("lint-broken-link", path, f"broken link target: {target}")
        )
    for finding in findings.get("missing_frontmatter", []):
        if _operation_finding(finding) or not isinstance(finding, dict):
            continue
        page = finding.get("page", ".")
        missing = finding.get("missing", [])
        path = _lint_path(config, page) if isinstance(page, str) else "."
        detail = ", ".join(str(item) for item in missing)
        issues.append(
            CheckIssue("lint-missing-frontmatter", path, f"missing: {detail}")
        )
    for finding in findings.get("trust_metadata_errors", []):
        if _operation_finding(finding) or not isinstance(finding, dict):
            continue
        page = finding.get("page", ".")
        detail = finding.get("issue", "invalid trust metadata")
        path = _lint_path(config, page) if isinstance(page, str) else "."
        issues.append(
            CheckIssue("lint-trust-metadata", path, _scrub(config.root, detail))
        )


def _check_git(config: PortableConfig, issues: list[CheckIssue]) -> None:
    git_root = discover_git_root(config.vault)
    if git_root is None:
        issues.append(
            CheckIssue(
                "git-unavailable",
                ".",
                "Git is unavailable or the repository is not a worktree",
                "warning",
            )
        )
        return
    if git_root != config.root:
        issues.append(
            CheckIssue(
                "git-root-mismatch",
                ".",
                "vault is enclosed by a different Git worktree than the portable root",
            )
        )
        return
    tracked = tracked_paths(git_root)
    hot = _rel(config.root, config.vault / "hot.md")
    local = _rel(config.root, config.local_state)
    canonical_local = ".obsidian-wiki/local"
    for path in tracked:
        parts = PurePosixPath(path).parts
        if (
            path == hot
            or path == local
            or path.startswith(local + "/")
            or path == canonical_local
            or path.startswith(canonical_local + "/")
            or any(part in {".locks", ".snapshots", ".transactions"} for part in parts)
        ):
            issues.append(
                CheckIssue(
                    "tracked-local-state",
                    path,
                    "mutable local state must not be tracked",
                )
            )


def _load_canonical_skills(
    config: PortableConfig, issues: list[CheckIssue]
) -> SkillCollection | None:
    try:
        return discover_skill_collection(config.skills)
    except (OSError, ValueError) as exc:
        issues.append(
            CheckIssue(
                "canonical-skill-invalid",
                _rel(config.root, config.skills),
                _skill_tree_error(config.root, exc),
            )
        )
        return None


def _load_managed_inventory(
    config: PortableConfig,
    canonical: SkillCollection,
    issues: list[CheckIssue],
) -> bool:
    try:
        inventory = read_inventory(config.root, allow_legacy=True)
    except (OSError, ValueError) as exc:
        issues.append(
            CheckIssue(
                "managed-skills-invalid",
                MANAGED_SKILLS_INVENTORY,
                _scrub(config.root, exc),
            )
        )
        return False
    if isinstance(inventory, LegacyManagedSkillsInventory):
        issues.append(
            CheckIssue(
                "managed-skills-legacy",
                MANAGED_SKILLS_INVENTORY,
                "legacy managed skill adapters require `obsidian-wiki repo "
                "upgrade-skills`",
            )
        )
        return False
    assert isinstance(inventory, ManagedSkillsInventory)

    try:
        skills_version = Version(inventory.skills_version)
        required = SpecifierSet(config.requires_cli)
    except (InvalidVersion, InvalidSpecifier) as exc:
        issues.append(
            CheckIssue(
                "managed-skills-invalid",
                MANAGED_SKILLS_INVENTORY,
                _scrub(
                    config.root,
                    f"managed skills inventory has an invalid version: {exc}",
                ),
            )
        )
        return False
    if not required.contains(skills_version, prereleases=True):
        issues.append(
            CheckIssue(
                "managed-skills-invalid",
                MANAGED_SKILLS_INVENTORY,
                "managed skills inventory skills_version is not accepted by "
                "requires_cli",
            )
        )
        return False

    canonical_by_name = canonical.by_name()
    missing = tuple(
        name for name in inventory.managed_skills if name not in canonical_by_name
    )
    if missing:
        issues.append(
            CheckIssue(
                "managed-skills-invalid",
                MANAGED_SKILLS_INVENTORY,
                "managed skills are missing from canonical .skills: "
                + ", ".join(missing),
            )
        )
        return False
    for name in inventory.managed_skills:
        actual = canonical_by_name[name].digest
        expected = inventory.managed_skill_digests[name]
        if actual != expected:
            issues.append(
                CheckIssue(
                    "managed-canonical-modified",
                    f".skills/{name}",
                    "managed canonical skill differs from the installed inventory "
                    "digest",
                    "warning",
                )
            )
    return True


def _skill_tree_error(root: Path, error: object) -> str:
    message = _scrub(root, error)
    lowered = message.lower()
    if "symbolic link" in lowered:
        return "symlink detected: " + message
    if "multiply-linked" in lowered:
        return "hard link detected: " + message
    return message


def _canonical_root_entries(canonical: SkillCollection) -> tuple[SkillEntry, ...]:
    entries: list[SkillEntry] = []
    for skill in canonical.skills:
        entries.append(SkillEntry(skill.name, "directory", False, b""))
        entries.extend(
            SkillEntry(
                f"{skill.name}/{entry.path}",
                entry.kind,
                entry.executable,
                entry.content,
            )
            for entry in skill.entries
        )
    return tuple(sorted(entries, key=lambda entry: entry.path))


_UNSAFE_SKILL_MESSAGES = {
    "symlink": "symlink detected in agent mirror",
    "hard-link": "hard link detected in agent mirror",
    "special": "special filesystem entry detected in agent mirror",
    "changed": "agent mirror entry changed during inspection",
    "read-error": "agent mirror entry could not be read safely",
}


def _check_skill_mirrors(
    config: PortableConfig,
    canonical: SkillCollection,
    issues: list[CheckIssue],
) -> None:
    canonical_entries = {
        entry.path: entry for entry in _canonical_root_entries(canonical)
    }
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        mirror_root = config.root / agent_relative
        if _has_symlink_component(config.root, mirror_root):
            issues.append(
                CheckIssue(
                    "skill-mirror-unsafe",
                    agent_relative,
                    "agent mirror path contains a symlink component",
                )
            )
            continue
        try:
            mirror_snapshot, unsafe_findings = snapshot_ordinary_tree_with_unsafe(
                mirror_root, anchor=config.root
            )
        except (OSError, ValueError) as exc:
            issues.append(
                CheckIssue(
                    "skill-mirror-unsafe",
                    agent_relative,
                    _skill_tree_error(config.root, exc),
                )
            )
            continue
        if _has_symlink_component(config.root, mirror_root):
            issues.append(
                CheckIssue(
                    "skill-mirror-unsafe",
                    agent_relative,
                    "agent mirror path gained a symlink component during scan",
                )
            )
            continue
        for unsafe in unsafe_findings:
            path = (
                agent_relative
                if unsafe.path == "."
                else (PurePosixPath(agent_relative) / unsafe.path).as_posix()
            )
            issues.append(
                CheckIssue(
                    "skill-mirror-unsafe",
                    path,
                    _UNSAFE_SKILL_MESSAGES[unsafe.reason],
                )
            )

        unsafe_paths = tuple(unsafe.path for unsafe in unsafe_findings)

        def blocked(path: str) -> bool:
            return any(
                unsafe == "."
                or path == unsafe
                or path.startswith(unsafe + "/")
                for unsafe in unsafe_paths
            )

        mirror_entries = {entry.path: entry for entry in mirror_snapshot}
        for path in sorted(set(canonical_entries) - set(mirror_entries)):
            if blocked(path):
                continue
            issues.append(
                CheckIssue(
                    "skill-mirror-missing",
                    (PurePosixPath(agent_relative) / path).as_posix(),
                    "canonical skill entry is missing from the agent mirror",
                )
            )
        for path in sorted(set(canonical_entries) & set(mirror_entries)):
            if blocked(path):
                continue
            if canonical_entries[path] != mirror_entries[path]:
                issues.append(
                    CheckIssue(
                        "skill-mirror-changed",
                        (PurePosixPath(agent_relative) / path).as_posix(),
                        "agent mirror entry differs from the canonical skill entry",
                    )
                )
        for path in sorted(set(mirror_entries) - set(canonical_entries)):
            if blocked(path):
                continue
            issues.append(
                CheckIssue(
                    "skill-mirror-extra",
                    (PurePosixPath(agent_relative) / path).as_posix(),
                    "agent mirror entry is absent from canonical skills",
                )
            )


def _check_managed_skills(config: PortableConfig, issues: list[CheckIssue]) -> None:
    canonical = _load_canonical_skills(config, issues)
    if canonical is None:
        return
    if not _load_managed_inventory(config, canonical, issues):
        return
    _check_skill_mirrors(config, canonical, issues)


def check_portable_skills(config: PortableConfig) -> dict[str, object]:
    """Validate canonical skills, inventory ownership, and every full mirror."""
    issues: list[CheckIssue] = []
    _check_managed_skills(config, issues)
    return _report(issues)


def _check_bootstrap(config: PortableConfig, issues: list[CheckIssue]) -> None:
    attributes = config.root / ".gitattributes"
    if not _ordinary_file(attributes) or _has_symlink_component(
        config.root, attributes
    ):
        attributes_valid = False
    else:
        try:
            attributes_text = attributes.read_text(encoding="utf-8")
            attributes_valid = (
                render_portable_gitattributes(attributes_text) == attributes_text
            )
        except (OSError, UnicodeDecodeError, ValueError):
            attributes_valid = False
    if not attributes_valid:
        issues.append(
            CheckIssue(
                "managed-gitattributes-invalid",
                ".gitattributes",
                "portable byte-stability attributes are missing or stale",
            )
        )
    targets: list[tuple[str, str]] = [
        ("AGENTS.md", _PORTABLE_AGENT_INSTRUCTIONS),
        *(
            (relative, _bootstrap_body(reference))
            for relative, reference in _BOOTSTRAP_REFERENCES.items()
        ),
    ]
    for relative, expected_body in targets:
        path = config.root / relative
        if not _ordinary_file(path) or _has_symlink_component(config.root, path):
            issues.append(
                CheckIssue(
                    "managed-bootstrap-invalid",
                    relative,
                    "managed bootstrap must be an ordinary contained file",
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if text.count(MANAGED_START) != 1 or text.count(MANAGED_END) != 1:
            issues.append(
                CheckIssue(
                    "managed-bootstrap-invalid",
                    relative,
                    "managed bootstrap must contain exactly one marker pair",
                )
            )
            continue
        start = text.index(MANAGED_START)
        end = text.index(MANAGED_END)
        if end < start:
            issues.append(
                CheckIssue(
                    "managed-bootstrap-invalid",
                    relative,
                    "managed bootstrap marker order is invalid",
                )
            )
            continue
        managed = text[start + len(MANAGED_START) : end]
        expected = "\n" + expected_body.rstrip() + "\n"
        if managed != expected:
            issues.append(
                CheckIssue(
                    "managed-bootstrap-invalid",
                    relative,
                    "managed bootstrap region is stale",
                )
            )


def _check_stable_views(config: PortableConfig, issues: list[CheckIssue]) -> None:
    for name, expected in (("index.md", _INDEX), ("log.md", _LOG)):
        path = config.vault / name
        try:
            actual = path.read_text(encoding="utf-8") if _ordinary_file(path) else None
        except (OSError, UnicodeDecodeError):
            actual = None
        if actual != expected or _has_symlink_component(config.vault, path):
            issues.append(
                CheckIssue(
                    "stable-view-modified",
                    _rel(config.root, path),
                    "portable stable view differs from its canonical template",
                )
            )


def check_portable_repo(config: PortableConfig) -> dict[str, object]:
    """Validate a portable repository deterministically without writing to it."""
    issues: list[CheckIssue] = []
    loaded = _reload_config(config, issues)
    if loaded is None:
        return _report(issues)

    store, entries = _load_manifest(loaded, issues)
    if store is not None:
        _check_sources(store, entries, issues)
    _check_pages(loaded, store, entries, issues)
    _check_operations(loaded, store, issues)
    _check_lint(loaded, issues)
    _check_git(loaded, issues)
    _check_managed_skills(loaded, issues)
    _check_bootstrap(loaded, issues)
    _check_stable_views(loaded, issues)
    return _report(issues)

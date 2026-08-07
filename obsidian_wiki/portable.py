"""Clone-ready portable repository scaffolding.

Portable repositories carry their configuration, canonical skills, agent
adapters, and Obsidian vault together.  This module deliberately has no global
configuration or Git side effects.
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterable

from packaging.version import InvalidVersion, Version

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import PortableConfig, load_portable_config


MANAGED_START = "<!-- obsidian-wiki:managed:start -->"
MANAGED_END = "<!-- obsidian-wiki:managed:end -->"
MANIFEST_MARKER = {
    "schema_version": 2,
    "storage": "sharded",
    "entries": ".manifest/sources",
}
PORTABLE_VAULT_DIRS = (
    "concepts",
    "entities",
    "skills",
    "references",
    "synthesis",
    "journal/operations",
    "projects",
    "_meta",
    "_raw",
    "_readouts",
    ".obsidian",
)
PORTABLE_ROOT_IGNORE = (".obsidian-wiki/local/",)


# Shared by the legacy project install in ``cli.py`` and portable adapters.
# Keeping the labels here preserves the existing public CLI constant while
# avoiding a portable -> CLI import cycle.
PROJECT_AGENT_DIRS = [
    (".claude/skills", "Claude Code"),
    (".cursor/skills", "Cursor"),
    (".windsurf/skills", "Windsurf"),
    (".agents/skills", "OpenCode / generic"),
    (".pi/skills", "Pi"),
    (".kiro/skills", "Kiro"),
]


_INDEX = '''---
title: Wiki Index
---

# Wiki Index

```query
path:"concepts" OR path:"entities" OR path:"skills" OR path:"references" OR path:"synthesis" OR path:"projects"
```
'''

_LOG = '''---
title: Wiki Operation Log
---

# Wiki Operation Log

```query
path:"journal/operations"
```
'''

_PORTABLE_AGENT_INSTRUCTIONS = """# Portable Obsidian Wiki Repository

- Discover this repository's configuration at `.obsidian-wiki/config.toml`; resolve every configured path relative to the repository root.
- Route user intent through `.skills/<name>/SKILL.md`, which is the repository-canonical skill location.
- Read `wiki/AGENTS.md` when it exists and apply its owner-specific conventions after these repository rules.
- Treat vault changes as transaction-only writes: inspect and validate the complete write set before applying it.
- Never automatically commit, push, or open a pull request. Those source-control actions require an explicit user request.
"""

_TEAM_CONVENTIONS = """## Team conventions

Maintainers may add repository-specific terminology, writing style, scope, and review rules below this heading.
"""

_BOOTSTRAP_REFERENCES = {
    "CLAUDE.md": "AGENTS.md",
    "GEMINI.md": "AGENTS.md",
    ".hermes.md": "AGENTS.md",
    ".agent/rules/obsidian-wiki.md": "../../AGENTS.md",
    ".agent/workflows/obsidian-wiki.md": "../../AGENTS.md",
    ".cursor/rules/obsidian-wiki.mdc": "../../AGENTS.md",
    ".windsurf/rules/obsidian-wiki.md": "../../AGENTS.md",
    ".kiro/steering/obsidian-wiki.md": "../../AGENTS.md",
    ".github/copilot-instructions.md": "../AGENTS.md",
}

_SOURCE_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".AppleDouble",
        ".LSOverride",
        ".Spotlight-V100",
        ".Trashes",
    }
)
_SOURCE_IGNORED_FILES = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}
)


def _absolute_no_resolve(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _safe_root(root: Path) -> Path:
    """Resolve a repository root only after rejecting a root symlink."""
    requested = _absolute_no_resolve(Path(root).expanduser())
    if requested.is_symlink():
        raise ValueError(f"portable repository root must not be a symlink: {requested}")
    return requested.resolve(strict=False)


def _assert_safe_managed_path(root: Path, path: Path) -> None:
    """Reject escaping paths and every symlink component below *root*."""
    root = _safe_root(root)
    candidate = _absolute_no_resolve(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"managed path escapes portable repository: {candidate}") from exc

    current = root
    if current.is_symlink():
        raise ValueError(f"managed path contains symlink: {current}")
    for index, part in enumerate(relative.parts):
        current = current / part
        if current.is_symlink():
            raise ValueError(f"managed path contains symlink: {current}")
        if current.exists() and index < len(relative.parts) - 1 and not current.is_dir():
            raise ValueError(f"managed parent is not an ordinary directory: {current}")

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"managed path escapes portable repository: {candidate}") from exc


def _assert_managed_tree(root: Path, tree: Path) -> None:
    _assert_safe_managed_path(root, tree)
    if not tree.exists():
        return
    if not tree.is_dir():
        return
    for descendant in tree.rglob("*"):
        if descendant.is_symlink():
            raise ValueError(f"managed tree contains symlink: {descendant}")


def _assert_ordinary_file(root: Path, path: Path, label: str) -> None:
    _assert_safe_managed_path(root, path)
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"portable {label} must be an ordinary file: {path}")


def _assert_directory(root: Path, path: Path, label: str) -> None:
    _assert_safe_managed_path(root, path)
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"portable {label} must be an ordinary directory: {path}")


def _source_file_is_ignored(name: str) -> bool:
    return (
        name in _SOURCE_IGNORED_FILES
        or name.startswith("._")
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith((".pyc", ".pyo"))
    )


def _validate_source_tree(skill: Path) -> None:
    for directory, dirnames, filenames in os.walk(skill, followlinks=False):
        current = Path(directory)
        for name in (*dirnames, *filenames):
            candidate = current / name
            if candidate.is_symlink():
                raise ValueError(f"canonical skill source contains symlink: {candidate}")


def _discover_source_skills(source_skills: Path) -> tuple[Path, tuple[str, ...]]:
    source = _absolute_no_resolve(Path(source_skills).expanduser())
    if source.is_symlink():
        raise ValueError(f"canonical skills source must not be a symlink: {source}")
    if not source.is_dir():
        raise FileNotFoundError(f"canonical skills directory not found: {source}")

    names: list[str] = []
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            raise ValueError(f"canonical skills source contains top-level symlink: {entry}")
        if entry.name in _SOURCE_IGNORED_DIRS:
            continue
        if not entry.is_dir():
            continue
        skill_file = entry / "SKILL.md"
        if skill_file.is_symlink():
            raise ValueError(f"canonical skill SKILL.md must not be a symlink: {skill_file}")
        if not skill_file.is_file():
            continue
        _validate_source_tree(entry)
        names.append(entry.name)
    return source, tuple(names)


def _ignore_source_artifacts(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in _SOURCE_IGNORED_DIRS or _source_file_is_ignored(name)
    }


def _write_text_if_changed(path: Path, text: str, *, root: Path) -> None:
    """Write UTF-8 *text* only when the ordinary file content differs."""
    _assert_safe_managed_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    elif path.exists():
        raise IsADirectoryError(f"expected a file, found directory: {path}")
    path.write_text(text, encoding="utf-8")


def _write_text_if_missing(path: Path, text: str, *, root: Path) -> None:
    """Create a stable UTF-8 file without modifying an existing path."""
    _assert_safe_managed_path(root, path)
    if path.exists() or path.is_symlink():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def compatible_cli_spec(version: str) -> str:
    """Return the portable repository's compatible CLI requirement.

    Stable CalVer releases accept patches within the same calendar month.
    Development, prerelease, postrelease, local, and non-CalVer releases are
    pinned to their public PEP 440 version so generated repositories never
    depend on a machine-specific local version segment.
    """
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        raise ValueError(f"invalid CLI version {version!r}: {exc}") from exc

    release = parsed.release
    is_stable_calver = (
        parsed.epoch == 0
        and not parsed.is_prerelease
        and not parsed.is_devrelease
        and not parsed.is_postrelease
        and parsed.local is None
        and len(release) in (2, 3)
        and release[0] >= 2000
        and 1 <= release[1] <= 12
    )
    if not is_stable_calver:
        return f"=={parsed.public}"

    year, month = release[:2]
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f">={year}.{month},<{next_year}.{next_month}"


def _load_canonical_portable_config(root: Path, *, version: str) -> PortableConfig:
    path = root / ".obsidian-wiki" / "config.toml"
    config = load_portable_config(
        path,
        installed_version=version,
        implementation=IMPLEMENTATION_ID,
    )
    expected = (
        (root / "wiki").resolve(strict=False),
        ((root / "sources").resolve(strict=False),),
        (root / ".skills").resolve(strict=False),
        (root / ".obsidian-wiki/local").resolve(strict=False),
    )
    actual = (config.vault, config.sources, config.skills, config.local_state)
    if actual != expected:
        raise ValueError(
            "portable configuration must use canonical portable paths: "
            "vault=wiki, sources=[sources], skills=.skills, "
            "local_state=.obsidian-wiki/local"
        )
    return config


def merge_managed_block(existing: str, managed_content: str) -> str:
    """Insert or replace one managed block while preserving owner text.

    Malformed, reversed, or duplicate markers are rejected before any caller
    writes a file, preventing accidental deletion of owner-maintained content.
    """
    if MANAGED_START in managed_content or MANAGED_END in managed_content:
        raise ValueError("managed content must not contain managed markers")
    start_count = existing.count(MANAGED_START)
    end_count = existing.count(MANAGED_END)
    if start_count != end_count or start_count > 1:
        raise ValueError("malformed obsidian-wiki managed markers")

    block = f"{MANAGED_START}\n{managed_content.rstrip()}\n{MANAGED_END}"
    if start_count == 0:
        return f"{block}\n\n{existing}" if existing else f"{block}\n"

    start = existing.index(MANAGED_START)
    end = existing.index(MANAGED_END)
    if end < start:
        raise ValueError("malformed obsidian-wiki managed markers: reversed order")
    end += len(MANAGED_END)
    return f"{existing[:start]}{block}{existing[end:]}"


def write_portable_config(
    root: Path,
    *,
    version: str,
    implementation: str = IMPLEMENTATION_ID,
    vault: str = "wiki",
    sources: tuple[str, ...] = ("sources",),
    skills: str = ".skills",
    local_state: str = ".obsidian-wiki/local",
) -> Path:
    """Write the minimal repository-relative portable TOML configuration."""
    root = _safe_root(Path(root))
    config_dir = root / ".obsidian-wiki"
    _assert_managed_tree(root, config_dir)
    if config_dir.exists() and not config_dir.is_dir():
        raise ValueError(f"portable config parent must be an ordinary directory: {config_dir}")
    path = config_dir / "config.toml"
    _assert_safe_managed_path(root, path)
    if path.exists():
        _load_canonical_portable_config(root, version=version)
        return path

    source_values = ", ".join(json.dumps(source) for source in sources)
    text = (
        "schema_version = 1\n"
        f"implementation = {json.dumps(implementation)}\n"
        f"requires_cli = {json.dumps(compatible_cli_spec(version))}\n"
        "\n"
        "[paths]\n"
        f"vault = {json.dumps(vault)}\n"
        f"sources = [{source_values}]\n"
        f"skills = {json.dumps(skills)}\n"
        f"local_state = {json.dumps(local_state)}\n"
        "\n"
        "[settings]\n"
        'OBSIDIAN_CATEGORIES = "concepts,entities,skills,references,synthesis,journal,projects"\n'
        "OBSIDIAN_MAX_PAGES_PER_INGEST = 15\n"
        'OBSIDIAN_LINK_FORMAT = "wikilink"\n'
        'OBSIDIAN_RAW_DIR = "_raw"\n'
        "OBSIDIAN_TRUST_STRICT = false\n"
    )
    _write_text_if_changed(path, text, root=root)
    return path


def scaffold_portable_vault(vault: Path) -> None:
    """Create the portable vault layout and stable initial metadata."""
    vault = _safe_root(Path(vault))
    _assert_managed_tree(vault, vault)
    for relative in (*PORTABLE_VAULT_DIRS, ".manifest/sources"):
        directory = vault / relative
        _assert_safe_managed_path(vault, directory)
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"portable vault directory collision: {directory}")
        directory.mkdir(parents=True, exist_ok=True)

    _write_text_if_missing(vault / "index.md", _INDEX, root=vault)
    _write_text_if_missing(vault / "log.md", _LOG, root=vault)
    _write_text_if_missing(
        vault / ".manifest.json",
        json.dumps(MANIFEST_MARKER, indent=2) + "\n",
        root=vault,
    )
    _write_text_if_missing(
        vault / ".obsidian/app.json",
        json.dumps(
            {
                "strictLineBreaks": False,
                "showFrontmatter": False,
                "defaultViewMode": "preview",
                "livePreview": True,
            },
            indent=2,
        )
        + "\n",
        root=vault,
    )
    _write_text_if_missing(
        vault / ".obsidian/appearance.json",
        json.dumps({"baseFontSize": 16}, indent=2) + "\n",
        root=vault,
    )


def copy_canonical_skills(source_skills: Path, root: Path) -> tuple[str, ...]:
    """Copy canonical skill entries into ``root/.skills`` without symlinks.

    Existing ordinary canonical entries and unrelated owner entries are left
    intact. Source and destination symlinks are rejected so the resulting
    repository is self-contained without materializing external content.
    """
    source, names = _discover_source_skills(source_skills)
    root = _safe_root(Path(root))
    destination = root / ".skills"
    _assert_managed_tree(root, destination)
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"portable canonical skills path must be a directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    for name in names:
        entry = source / name
        target = destination / name
        if entry.resolve() == target.resolve(strict=False):
            continue
        _assert_safe_managed_path(root, target)
        if target.exists():
            if not target.is_dir() or not (target / "SKILL.md").is_file():
                raise ValueError(f"portable canonical skill collision: {target}")
            continue

        shutil.copytree(
            entry,
            target,
            symlinks=False,
            ignore=_ignore_source_artifacts,
        )
    return names


def _adapter_text(skill_name: str, relative_target: str) -> str:
    return (
        "---\n"
        f"name: {skill_name}\n"
        f"description: Portable adapter for the repository-canonical {skill_name} skill.\n"
        "---\n\n"
        "# Portable skill adapter\n\n"
        f"Read and follow `{relative_target}` from this repository. Resolve that path "
        "from this adapter file, never from the process working directory.\n"
    )


def write_agent_adapters(
    root: Path,
    skill_names: Iterable[str],
    *,
    agent_dirs: Iterable[tuple[str, str]] = PROJECT_AGENT_DIRS,
) -> None:
    """Write ordinary per-agent adapter files pointing to canonical skills."""
    root = _safe_root(Path(root))
    planned: list[tuple[Path, str]] = []
    for agent_relative, _label in agent_dirs:
        agent_root = root / agent_relative
        _assert_managed_tree(root, agent_root)
        if agent_root.exists() and not agent_root.is_dir():
            raise ValueError(f"portable agent skills path must be a directory: {agent_root}")
        for skill_name in sorted(skill_names):
            adapter_relative = PurePosixPath(agent_relative) / skill_name / "SKILL.md"
            canonical_relative = PurePosixPath(".skills") / skill_name / "SKILL.md"
            target = root.joinpath(*adapter_relative.parts)
            _assert_safe_managed_path(root, target)
            if target.parent.exists() and not target.parent.is_dir():
                raise ValueError(f"portable adapter directory collision: {target.parent}")
            if target.exists():
                if not target.is_file():
                    raise ValueError(f"portable adapter collision: {target}")
                continue
            relative_target = posixpath.relpath(
                canonical_relative.as_posix(),
                adapter_relative.parent.as_posix(),
            )
            planned.append((target, _adapter_text(skill_name, relative_target)))
    for target, text in planned:
        _write_text_if_changed(target, text, root=root)


def _bootstrap_body(relative_agents: str) -> str:
    return (
        "# Obsidian Wiki Agent Instructions\n\n"
        f"Read and follow `{relative_agents}` from this repository.\n"
    )


def _legacy_bootstrap_text(relative_agents: str) -> str:
    return "<!-- obsidian-wiki:portable-bootstrap -->\n" + _bootstrap_body(relative_agents)


def _planned_bootstrap_text(existing: str, relative_agents: str) -> str | None:
    body = _bootstrap_body(relative_agents)
    if not existing:
        return merge_managed_block("", body)
    if MANAGED_START in existing or MANAGED_END in existing:
        return merge_managed_block(existing, body)
    if existing == _legacy_bootstrap_text(relative_agents):
        return merge_managed_block("", body)
    return None


def _portable_bootstrap_plans(root: Path) -> list[tuple[Path, str]]:
    agents_path = root / "AGENTS.md"
    _assert_safe_managed_path(root, agents_path)
    if agents_path.exists() and not agents_path.is_file():
        raise ValueError(f"portable AGENTS.md must be an ordinary file: {agents_path}")
    existing = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
    if not existing:
        existing = _TEAM_CONVENTIONS
    elif MANAGED_START not in existing and "## Team conventions" not in existing:
        existing = f"{_TEAM_CONVENTIONS}\n{existing}"
    merged = merge_managed_block(existing, _PORTABLE_AGENT_INSTRUCTIONS)

    plans: list[tuple[Path, str]] = [(agents_path, merged)]
    for relative, agents_reference in _BOOTSTRAP_REFERENCES.items():
        target = root / relative
        _assert_safe_managed_path(root, target)
        _assert_managed_tree(root, target.parent)
        if target.exists() and not target.is_file():
            raise ValueError(f"portable bootstrap destination collision: {target}")
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        planned = _planned_bootstrap_text(current, agents_reference)
        if planned is not None:
            plans.append((target, planned))
    return plans


def install_portable_bootstrap(root: Path) -> None:
    """Install dedicated portable agent discovery and bootstrap Markdown."""
    root = _safe_root(Path(root))
    plans = _portable_bootstrap_plans(root)

    for target, text in plans:
        _write_text_if_changed(target, text, root=root)


def _vault_relative_posix(root: Path, vault: str | Path) -> str:
    root_resolved = _safe_root(Path(root))
    vault_path = Path(vault)
    candidate = vault_path if vault_path.is_absolute() else root_resolved / vault_path
    _assert_safe_managed_path(root_resolved, candidate)
    try:
        relative = candidate.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("portable vault path must remain inside the repository") from exc
    return relative.as_posix()


def _escape_gitignore_path(value: str) -> str:
    """Escape a repository-relative path as one literal gitignore pattern."""
    return "".join(f"\\{char}" if char in "\\ #![]*?" else char for char in value)


def ensure_portable_gitignore(root: Path, vault: str | Path = "wiki") -> None:
    """Append portable local-state ignores while preserving existing entries."""
    root = _safe_root(Path(root))
    vault_relative = _vault_relative_posix(root, vault)
    escaped_vault = _escape_gitignore_path(vault_relative)
    prefix = "" if escaped_vault == "." else f"{escaped_vault}/"
    required = (
        *PORTABLE_ROOT_IGNORE,
        f"{prefix}hot.md",
        f"{prefix}.obsidian/workspace.json",
        f"{prefix}.obsidian/workspace-mobile.json",
        f"{prefix}.trash/",
    )
    path = root / ".gitignore"
    _assert_safe_managed_path(root, path)
    if path.exists() and not path.is_file():
        raise ValueError(f"portable .gitignore must be an ordinary file: {path}")
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [entry for entry in required if entry not in present]
    if not missing:
        return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    appended = "\n".join(missing)
    _write_text_if_changed(path, f"{existing}{separator}{appended}\n", root=root)


def _preflight_managed_destinations(root: Path) -> None:
    managed_trees = (
        root / ".obsidian-wiki",
        root / "sources",
        root / "wiki",
        root / ".skills",
        *(root / relative for relative, _label in PROJECT_AGENT_DIRS),
    )
    for tree in managed_trees:
        _assert_managed_tree(root, tree)

    managed_files = (
        root / "AGENTS.md",
        root / ".gitignore",
        *(root / relative for relative in _BOOTSTRAP_REFERENCES),
    )
    for path in managed_files:
        _assert_safe_managed_path(root, path)


def _validate_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"portable {label} is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"portable {label} must contain a JSON object")
    return value


def _preflight_existing_portable(
    root: Path,
    *,
    version: str,
    skill_names: Iterable[str],
) -> None:
    """Validate every existing artifact that a portable rerun may touch."""
    _preflight_managed_destinations(root)
    config_path = root / ".obsidian-wiki/config.toml"
    if not config_path.is_file():
        raise ValueError(
            f"target is nonempty but not a portable repository: missing {config_path}"
        )
    _assert_ordinary_file(root, config_path, "configuration")
    _load_canonical_portable_config(root, version=version)

    required_directories = (
        root / "sources",
        root / "wiki",
        root / ".skills",
        *(root / "wiki" / relative for relative in PORTABLE_VAULT_DIRS),
        root / "wiki/.manifest/sources",
    )
    for directory in required_directories:
        _assert_directory(root, directory, "structural path")

    stable_files = {
        root / "wiki/index.md": _INDEX.encode("utf-8"),
        root / "wiki/log.md": _LOG.encode("utf-8"),
    }
    for path, expected in stable_files.items():
        _assert_ordinary_file(root, path, path.name)
        if path.read_bytes() != expected:
            raise ValueError(f"portable stable file has unexpected content: {path}")

    manifest_path = root / "wiki/.manifest.json"
    _assert_ordinary_file(root, manifest_path, "manifest marker")
    if _validate_json_object(manifest_path, "manifest marker") != MANIFEST_MARKER:
        raise ValueError(f"portable manifest marker has unexpected content: {manifest_path}")

    for relative in (".obsidian/app.json", ".obsidian/appearance.json"):
        path = root / "wiki" / relative
        _assert_ordinary_file(root, path, relative)
        _validate_json_object(path, relative)

    for skill_name in skill_names:
        canonical = root / ".skills" / skill_name
        if canonical.exists():
            _assert_directory(root, canonical, f"canonical skill {skill_name}")
            _assert_ordinary_file(root, canonical / "SKILL.md", f"canonical skill {skill_name}")
        for agent_relative, _label in PROJECT_AGENT_DIRS:
            adapter = root / agent_relative / skill_name / "SKILL.md"
            _assert_safe_managed_path(root, adapter)
            if adapter.parent.exists() and not adapter.parent.is_dir():
                raise ValueError(f"portable adapter directory collision: {adapter.parent}")
            if adapter.exists() and not adapter.is_file():
                raise ValueError(f"portable adapter collision: {adapter}")

    _portable_bootstrap_plans(root)
    gitignore = root / ".gitignore"
    _assert_safe_managed_path(root, gitignore)
    if gitignore.exists() and not gitignore.is_file():
        raise ValueError(f"portable .gitignore must be an ordinary file: {gitignore}")
    if gitignore.is_file():
        try:
            gitignore.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"portable .gitignore is invalid: {exc}") from exc


def _populate_portable_repo(root: Path, *, version: str, source_skills: Path) -> None:
    write_portable_config(root, version=version)
    sources = root / "sources"
    _assert_safe_managed_path(root, sources)
    if sources.exists() and not sources.is_dir():
        raise ValueError(f"portable sources path must be a directory: {sources}")
    sources.mkdir(parents=True, exist_ok=True)
    scaffold_portable_vault(root / "wiki")
    skill_names = copy_canonical_skills(source_skills, root)
    write_agent_adapters(root, skill_names)
    install_portable_bootstrap(root)
    ensure_portable_gitignore(root, "wiki")


def setup_portable_repo(
    root: Path,
    *,
    version: str,
    source_skills: Path,
) -> Path:
    """Scaffold a clone-ready portable repository and return its resolved root."""
    compatible_cli_spec(version)
    source, skill_names = _discover_source_skills(source_skills)
    requested = _absolute_no_resolve(Path(root).expanduser())
    if requested.is_symlink():
        raise ValueError(f"portable repository root must not be a symlink: {requested}")
    root = requested.resolve(strict=False)
    if root.exists() and not root.is_dir():
        raise ValueError(f"portable repository target must be a directory: {root}")

    target_existed = root.is_dir()
    target_is_empty = target_existed and not any(root.iterdir())
    if target_existed and not target_is_empty:
        _preflight_existing_portable(root, version=version, skill_names=skill_names)
        _populate_portable_repo(root, version=version, source_skills=source)
        return root

    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.obsidian-wiki-", dir=root.parent)
    )
    removed_empty_target = False
    try:
        _populate_portable_repo(staging, version=version, source_skills=source)
        _preflight_existing_portable(staging, version=version, skill_names=skill_names)
        if target_is_empty:
            root.rmdir()
            removed_empty_target = True
        staging.replace(root)
    except BaseException:
        if staging.exists() and staging.parent == root.parent:
            shutil.rmtree(staging)
        if target_is_empty and removed_empty_target and not root.exists():
            root.mkdir()
        raise
    return root

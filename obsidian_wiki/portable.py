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
from pathlib import Path, PurePosixPath
from typing import Iterable

from packaging.version import InvalidVersion, Version

from obsidian_wiki import IMPLEMENTATION_ID


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


def _write_text_if_changed(path: Path, text: str) -> None:
    """Write UTF-8 *text* only when the ordinary file content differs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    elif path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    elif path.exists():
        raise IsADirectoryError(f"expected a file, found directory: {path}")
    path.write_text(text, encoding="utf-8")


def _write_text_if_missing(path: Path, text: str) -> None:
    """Create a stable UTF-8 file without modifying an existing path."""
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
        not parsed.is_prerelease
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


def merge_managed_block(existing: str, managed_content: str) -> str:
    """Insert or replace one managed block while preserving owner text.

    Malformed, reversed, or duplicate markers are rejected before any caller
    writes a file, preventing accidental deletion of owner-maintained content.
    """
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
    path = Path(root) / ".obsidian-wiki" / "config.toml"
    _write_text_if_changed(path, text)
    return path


def scaffold_portable_vault(vault: Path) -> None:
    """Create the portable vault layout and stable initial metadata."""
    vault = Path(vault)
    for relative in (*PORTABLE_VAULT_DIRS, ".manifest/sources"):
        (vault / relative).mkdir(parents=True, exist_ok=True)

    _write_text_if_missing(vault / "index.md", _INDEX)
    _write_text_if_missing(vault / "log.md", _LOG)
    _write_text_if_missing(
        vault / ".manifest.json",
        json.dumps(MANIFEST_MARKER, indent=2) + "\n",
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
    )
    _write_text_if_missing(
        vault / ".obsidian/appearance.json",
        json.dumps({"baseFontSize": 16}, indent=2) + "\n",
    )


def copy_canonical_skills(source_skills: Path, root: Path) -> tuple[str, ...]:
    """Copy canonical skill entries into ``root/.skills`` without symlinks.

    Existing ordinary canonical entries and unrelated owner entries are left
    intact. A legacy symlink at a canonical destination is replaced by a real
    copy so the resulting repository is self-contained.
    """
    source = Path(source_skills)
    if not source.is_dir():
        raise FileNotFoundError(f"canonical skills directory not found: {source}")
    destination = Path(root) / ".skills"
    destination.mkdir(parents=True, exist_ok=True)

    names: list[str] = []
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if entry.name == ".git":
            continue
        target = destination / entry.name
        names.append(entry.name)
        if entry.resolve() == target.resolve(strict=False):
            continue
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            continue

        if entry.is_dir():
            shutil.copytree(
                entry,
                target,
                symlinks=False,
                ignore=shutil.ignore_patterns(".git"),
            )
        elif entry.is_file():
            shutil.copy2(entry, target, follow_symlinks=True)
    return tuple(names)


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
    root = Path(root)
    for agent_relative, _label in agent_dirs:
        for skill_name in sorted(skill_names):
            adapter_relative = PurePosixPath(agent_relative) / skill_name / "SKILL.md"
            canonical_relative = PurePosixPath(".skills") / skill_name / "SKILL.md"
            target = root.joinpath(*adapter_relative.parts)
            relative_target = posixpath.relpath(
                canonical_relative.as_posix(),
                adapter_relative.parent.as_posix(),
            )
            _write_text_if_changed(target, _adapter_text(skill_name, relative_target))


def _bootstrap_text(relative_agents: str) -> str:
    return (
        "<!-- obsidian-wiki:portable-bootstrap -->\n"
        "# Obsidian Wiki Agent Instructions\n\n"
        f"Read and follow `{relative_agents}` from this repository.\n"
    )


def install_portable_bootstrap(root: Path) -> None:
    """Install dedicated portable agent discovery and bootstrap Markdown."""
    root = Path(root)
    agents_path = root / "AGENTS.md"
    if agents_path.is_symlink():
        agents_path.unlink()
    existing = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
    if not existing:
        existing = _TEAM_CONVENTIONS
    elif MANAGED_START not in existing and "## Team conventions" not in existing:
        existing = f"{_TEAM_CONVENTIONS}\n{existing}"
    merged = merge_managed_block(existing, _PORTABLE_AGENT_INSTRUCTIONS)
    _write_text_if_changed(agents_path, merged)

    for relative, agents_reference in _BOOTSTRAP_REFERENCES.items():
        _write_text_if_changed(root / relative, _bootstrap_text(agents_reference))


def _vault_relative_posix(root: Path, vault: str | Path) -> str:
    root_resolved = Path(root).resolve(strict=False)
    vault_path = Path(vault)
    candidate = vault_path if vault_path.is_absolute() else root_resolved / vault_path
    try:
        relative = candidate.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("portable vault path must remain inside the repository") from exc
    return relative.as_posix()


def ensure_portable_gitignore(root: Path, vault: str | Path = "wiki") -> None:
    """Append portable local-state ignores while preserving existing entries."""
    root = Path(root)
    vault_relative = _vault_relative_posix(root, vault)
    prefix = "" if vault_relative == "." else f"{vault_relative}/"
    required = (
        *PORTABLE_ROOT_IGNORE,
        f"{prefix}hot.md",
        f"{prefix}.obsidian/workspace.json",
        f"{prefix}.obsidian/workspace-mobile.json",
        f"{prefix}.trash/",
    )
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [entry for entry in required if entry not in present]
    if not missing:
        return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    _write_text_if_changed(path, f"{existing}{separator}{os.linesep.join(missing)}\n")


def setup_portable_repo(
    root: Path,
    *,
    version: str,
    source_skills: Path,
) -> Path:
    """Scaffold a clone-ready portable repository and return its resolved root."""
    root = Path(root).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    write_portable_config(root, version=version)
    (root / "sources").mkdir(parents=True, exist_ok=True)
    scaffold_portable_vault(root / "wiki")
    skill_names = copy_canonical_skills(source_skills, root)
    canonical_skill_names = tuple(
        name
        for name in skill_names
        if (root / ".skills" / name / "SKILL.md").is_file()
    )
    write_agent_adapters(root, canonical_skill_names)
    install_portable_bootstrap(root)
    ensure_portable_gitignore(root, "wiki")
    return root

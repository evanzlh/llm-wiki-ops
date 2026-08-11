"""Clone-ready portable repository scaffolding.

Portable repositories carry their configuration, canonical skills, agent
skill mirrors, and Obsidian vault together.  This module deliberately has no global
configuration or Git side effects.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import stat
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - repository upgrades are Unix-first
    fcntl = None  # type: ignore[assignment]

from packaging.version import InvalidVersion, Version

from obsidian_wiki import IMPLEMENTATION_ID, SOURCE_REINSTALL_COMMAND
from obsidian_wiki.config import PortableConfig, load_portable_config
from obsidian_wiki.skill_inventory import (
    MANAGED_SKILLS_INVENTORY,
    LegacyManagedSkillsInventory,
    ManagedSkillsInventory,
    read_inventory,
    render_inventory,
)
from obsidian_wiki.skill_trees import (
    SkillCollection,
    SkillEntry,
    discover_skill_collection,
    materialize_skill_collection,
    snapshot_ordinary_tree_with_unsafe,
)

MANAGED_START = "<!-- obsidian-wiki:managed:start -->"
MANAGED_END = "<!-- obsidian-wiki:managed:end -->"
GITATTRIBUTES_START = "# obsidian-wiki:gitattributes:start"
GITATTRIBUTES_END = "# obsidian-wiki:gitattributes:end"
_PORTABLE_GITATTRIBUTES = """# Preserve authoritative working-tree bytes across clones.
* -text

# Keep common knowledge and configuration formats reviewable and mergeable.
*.md diff merge
*.json diff merge
*.toml diff merge
*.yaml diff merge
*.yml diff merge
*.txt diff merge
*.base diff merge
"""
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
_PORTABLE_SKILLS_LOCK = ".obsidian-wiki/local/portable-skills.lock"
_UPGRADE_TRANSACTIONS = ".obsidian-wiki/local/skill-upgrades"
_UPGRADE_JOURNAL = "journal.json"
_UPGRADE_JOURNAL_SCHEMA = 3
_INVENTORY_KEYS = {"implementation", "skills", "skills_version"}
_SAFE_SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SUPPORTS_BOUND_INVENTORY_DIRECTORIES = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
)


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


def _assert_single_link_ordinary_file(
    root: Path, path: Path, label: str
) -> os.stat_result:
    """Require a managed file whose inode cannot be mutated through another path."""
    _assert_safe_managed_path(root, path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"portable {label} must be an ordinary file: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"portable {label} must be an ordinary file: {path}")
    if metadata.st_nlink != 1:
        raise ValueError(
            f"portable {label} has multiple links (hard link): {path}"
        )
    return metadata


def _stat_timestamp_ns(metadata: os.stat_result, name: str) -> int:
    nanoseconds = getattr(metadata, name + "_ns", None)
    if nanoseconds is not None:
        return int(nanoseconds)
    return int(getattr(metadata, name) * 1_000_000_000)


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_nlink,
        left.st_size,
        _stat_timestamp_ns(left, "st_mtime"),
        _stat_timestamp_ns(left, "st_ctime"),
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_nlink,
        right.st_size,
        _stat_timestamp_ns(right, "st_mtime"),
        _stat_timestamp_ns(right, "st_ctime"),
    )


def _is_reparse_point(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _require_inventory_file_metadata(
    metadata: os.stat_result, path: Path, label: str
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
        or metadata.st_nlink != 1
    ):
        raise ValueError(
            f"portable {label} must be a single-link ordinary non-reparse file: {path}"
        )


def _inventory_file_flags() -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_BINARY", 0)
    return flags


def _inventory_directory_flags() -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _directory_is_bound(
    opened: os.stat_result, attached: os.stat_result
) -> bool:
    return (
        stat.S_ISDIR(opened.st_mode)
        and stat.S_ISDIR(attached.st_mode)
        and not stat.S_ISLNK(attached.st_mode)
        and not _is_reparse_point(opened)
        and not _is_reparse_point(attached)
        and opened.st_dev == attached.st_dev
        and opened.st_ino == attached.st_ino
        and opened.st_mode == attached.st_mode
    )


# Lexical path, child name within the previous descriptor, FD, opened metadata.
_BoundDirectory = tuple[Path, str, int, os.stat_result]


def _close_inventory_directories(
    directories: list[_BoundDirectory], failure
):
    """Close every bound directory in reverse order without masking failure."""
    for _path, _name, descriptor, _metadata in reversed(directories):
        try:
            os.close(descriptor)
        except OSError as exc:
            if failure is None:
                failure = exc
    return failure


def _raise_inventory_read_failure(
    failure: BaseException, path: Path, label: str
) -> None:
    if isinstance(failure, (OSError, ValueError)):
        raise ValueError(  # noqa: TRY004 - normalize safe-read filesystem failures
            f"portable {label} is unsafe or changed at {path}: {failure}"
        ) from failure
    raise failure


def _open_posix_inventory_file(
    root: Path, path: Path, label: str
) -> tuple[int, os.stat_result, list[_BoundDirectory], list[int]]:
    """Bind root-to-parent with dir_fds and open the final file relative to it."""
    _assert_safe_managed_path(root, path)
    candidate = _absolute_no_resolve(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"portable {label} escapes repository: {path}") from exc
    if len(relative.parts) < 2 or ".." in relative.parts:
        raise ValueError(f"portable {label} must be below repository root: {path}")

    directories: list[_BoundDirectory] = []
    failure = None
    pending_directory = None
    file_descriptor = None
    try:
        pending_directory = os.open(root, _inventory_directory_flags())
        metadata = os.fstat(pending_directory)
        directories.append((root, "", pending_directory, metadata))
        pending_directory = None
        if not _directory_is_bound(metadata, metadata):
            raise ValueError(f"portable repository root is unsafe: {root}")

        current = root
        for part in relative.parent.parts:
            parent_descriptor = directories[-1][2]
            pending_directory = os.open(
                part, _inventory_directory_flags(), dir_fd=parent_descriptor
            )
            metadata = os.fstat(pending_directory)
            current = current / part
            directories.append((current, part, pending_directory, metadata))
            pending_directory = None
            if not _directory_is_bound(metadata, metadata):
                raise ValueError(f"portable {label} parent is unsafe: {current}")

        parent_descriptor = directories[-1][2]
        observed = os.stat(
            relative.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        _require_inventory_file_metadata(observed, path, label)
        file_descriptor = os.open(
            relative.name, _inventory_file_flags(), dir_fd=parent_descriptor
        )
        opened = os.fstat(file_descriptor)
        if not _same_file_snapshot(observed, opened):
            raise ValueError(f"portable {label} changed while being opened: {path}")
        _require_inventory_file_metadata(opened, path, label)
        return file_descriptor, opened, directories, []
    except BaseException as exc:  # noqa: BLE001 - closes preserve primary failure
        failure = exc
    if file_descriptor is not None:
        try:
            os.close(file_descriptor)
        except OSError as exc:
            if failure is None:
                failure = exc
    if pending_directory is not None:
        try:
            os.close(pending_directory)
        except OSError as exc:
            if failure is None:
                failure = exc
    failure = _close_inventory_directories(directories, failure)
    assert failure is not None
    _raise_inventory_read_failure(failure, path, label)
    raise AssertionError("unreachable")


def _open_windows_inventory_file(
    root: Path, path: Path, label: str
) -> tuple[int, os.stat_result, list[_BoundDirectory], list[int]]:
    """Open an inventory while no-delete-share handles guard its ancestors."""
    # Reuse the repository's established no-delete-share directory handles.
    # The lazy private import keeps this Task 4 change local; these helpers can
    # move to a shared safe-FS module when more consumers need them.
    from obsidian_wiki.local_state import (
        LocalStateError,
        _close_windows_handles,
        _windows_directory_guard,
    )

    _assert_safe_managed_path(root, path)
    handles: list[int] = []
    descriptor = None
    failure = None
    try:
        handles = _windows_directory_guard(root, (path.parent,))
        observed = path.lstat()
        _require_inventory_file_metadata(observed, path, label)
        descriptor = os.open(path, _inventory_file_flags())
        opened = os.fstat(descriptor)
        if not _same_file_snapshot(observed, opened):
            raise ValueError(f"portable {label} changed while being opened: {path}")
        _require_inventory_file_metadata(opened, path, label)
        return descriptor, opened, [], handles
    except BaseException as exc:  # noqa: BLE001 - closes preserve primary failure
        failure = exc
    if descriptor is not None:
        try:
            os.close(descriptor)
        except OSError as exc:
            if failure is None:
                failure = exc
    try:
        _close_windows_handles(handles)
    except BaseException as exc:  # noqa: BLE001 - preserve earlier open failure
        if failure is None:
            failure = exc
    assert failure is not None
    if isinstance(failure, LocalStateError):
        failure = ValueError(str(failure))
    _raise_inventory_read_failure(failure, path, label)
    raise AssertionError("unreachable")


def _open_bound_inventory_file(
    root: Path, path: Path, label: str
) -> tuple[int, os.stat_result, list[_BoundDirectory], list[int]]:
    """Open an inventory only through the platform's bound-directory mechanism."""
    root = _safe_root(root)
    if _SUPPORTS_BOUND_INVENTORY_DIRECTORIES and os.name != "nt":
        return _open_posix_inventory_file(root, path, label)
    if os.name == "nt":
        return _open_windows_inventory_file(root, path, label)
    raise ValueError(
        f"portable {label} cannot be read safely on this platform: {path}"
    )


def _validate_bound_inventory_after_close(
    root: Path,
    path: Path,
    label: str,
    final: os.stat_result,
    directories: list[_BoundDirectory],
    windows_handles: list[int],
) -> None:
    """Validate the file and every attachment while directory guards remain open."""
    if directories:
        parent_descriptor = directories[-1][2]
        current_file = os.stat(
            path.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        _require_inventory_file_metadata(current_file, path, label)
        if not _same_file_snapshot(final, current_file):
            raise ValueError(f"portable {label} changed while being read: {path}")

        for index, (directory, name, descriptor, opened) in enumerate(directories):
            current_opened = os.fstat(descriptor)
            if not _directory_is_bound(opened, current_opened):
                raise ValueError(
                    f"portable {label} directory changed while being read: {directory}"
                )
            if index == 0:
                attached = directory.lstat()
            else:
                attached = os.stat(
                    name,
                    dir_fd=directories[index - 1][2],
                    follow_symlinks=False,
                )
            if not _directory_is_bound(current_opened, attached):
                raise ValueError(
                    f"portable {label} directory detached while being read: {directory}"
                )
        return

    if windows_handles:
        _assert_safe_managed_path(root, path)
        current_file = path.lstat()
        _require_inventory_file_metadata(current_file, path, label)
        if not _same_file_snapshot(final, current_file):
            raise ValueError(f"portable {label} changed while being read: {path}")
        return

    raise ValueError(f"portable {label} has no bound parent guard: {path}")


def _validate_inventory_after_guards_closed(
    root: Path,
    path: Path,
    label: str,
    final: os.stat_result,
    directories: list[_BoundDirectory],
) -> None:
    """Catch replacements injected while the directory guards were closing."""
    if directories:
        for directory, _name, _descriptor, opened in directories:
            attached = directory.lstat()
            if not _directory_is_bound(opened, attached):
                raise ValueError(
                    f"portable {label} directory detached during close: {directory}"
                )
    else:
        _assert_safe_managed_path(root, path)

    current_file = path.lstat()
    _require_inventory_file_metadata(current_file, path, label)
    if not _same_file_snapshot(final, current_file):
        raise ValueError(f"portable {label} changed during guard close: {path}")


def _read_single_link_ordinary_bytes_once(
    root: Path, path: Path, label: str
) -> bytes:
    """Perform one complete contained, no-follow ordinary-file read."""
    root = _safe_root(root)
    descriptor, opened, directories, windows_handles = _open_bound_inventory_file(
        root, path, label
    )

    content = None
    final = None
    failure = None
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)

        final = os.fstat(descriptor)
        if (
            not stat.S_ISREG(final.st_mode)
            or final.st_nlink != 1
            or not _same_file_snapshot(opened, final)
        ):
            raise ValueError(f"portable {label} changed while being read: {path}")
        content = b"".join(chunks)
        if len(content) != final.st_size:
            raise ValueError(f"portable {label} changed while being read: {path}")
    except BaseException as exc:  # noqa: BLE001 - close must not mask any primary failure
        failure = exc
    try:
        os.close(descriptor)
    except OSError as exc:
        if failure is None:
            failure = exc
    if failure is None:
        assert content is not None
        assert final is not None
        try:
            _validate_bound_inventory_after_close(
                root,
                path,
                label,
                final,
                directories,
                windows_handles,
            )
        except BaseException as exc:  # noqa: BLE001 - closes preserve primary failure
            failure = exc

    failure = _close_inventory_directories(directories, failure)
    if windows_handles:
        from obsidian_wiki.local_state import _close_windows_handles

        try:
            _close_windows_handles(windows_handles)
        except BaseException as exc:  # noqa: BLE001 - preserve earlier file failure
            if failure is None:
                failure = exc
    if failure is None:
        assert final is not None
        try:
            _validate_inventory_after_guards_closed(
                root, path, label, final, directories
            )
        except BaseException as exc:  # noqa: BLE001 - validation is first failure
            failure = exc
    if failure is not None:
        _raise_inventory_read_failure(failure, path, label)

    assert content is not None
    return content


def _read_single_link_ordinary_bytes(root: Path, path: Path, label: str) -> bytes:
    """Return bytes confirmed by two independent safe reads of the live path.

    A rewrite completed before the first byte and then stable across both passes is
    intentionally accepted as the final live content.  Without cooperative locking,
    that case cannot be distinguished portably from an unchanged file.
    """
    first = _read_single_link_ordinary_bytes_once(root, path, label)
    second = _read_single_link_ordinary_bytes_once(root, path, label)
    if first != second:
        raise ValueError(
            f"portable {label} changed between independent reads: {path}"
        )
    return second


def _assert_single_link_managed_tree(root: Path, tree: Path, label: str) -> None:
    """Validate one exact inventory-owned tree without scanning its siblings."""
    _assert_safe_managed_path(root, tree)
    try:
        root_metadata = tree.lstat()
    except OSError as exc:
        raise ValueError(f"portable {label} must be an ordinary directory: {tree}") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise ValueError(f"portable {label} must be an ordinary directory: {tree}")

    for directory, dirnames, filenames in os.walk(tree, followlinks=False):
        current = Path(directory)
        for name in (*dirnames, *filenames):
            entry = current / name
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise ValueError(
                    f"portable {label} contains an unreadable entry: {entry}"
                ) from exc
            mode = metadata.st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"portable {label} contains a symlink: {entry}")
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(
                    f"portable {label} contains a special filesystem entry: {entry}"
                )
            if metadata.st_nlink != 1:
                raise ValueError(
                    f"portable {label} regular file has multiple links "
                    f"(hard link): {entry}"
                )


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


def _source_entry_kind(path: Path, *, missing_ok: bool = False) -> str | None:
    """Classify a source entry without following links."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except OSError as exc:
        raise ValueError(f"cannot inspect canonical skill source entry {path}: {exc}") from exc
    mode = metadata.st_mode
    if stat.S_ISLNK(mode):
        raise ValueError(f"canonical skill source contains symlink: {path}")
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        if metadata.st_nlink > 1:
            raise ValueError(
                "canonical skill source regular file has multiple links (hard link): "
                f"{path}. Reinstall from a framework clone with "
                f"`{SOURCE_REINSTALL_COMMAND}`."
            )
        return "file"
    raise ValueError(f"canonical skill source entry must be an ordinary file or directory: {path}")


def _validate_source_tree(skill: Path) -> None:
    if _source_entry_kind(skill) != "directory":
        raise ValueError(f"canonical skill source must be a directory: {skill}")
    for directory, dirnames, filenames in os.walk(skill, followlinks=False):
        current = Path(directory)
        for name in (*dirnames, *filenames):
            _source_entry_kind(current / name)


def _discover_source_skills(source_skills: Path) -> tuple[Path, tuple[str, ...]]:
    source = _absolute_no_resolve(Path(source_skills).expanduser())
    try:
        source_kind = _source_entry_kind(source)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"canonical skills directory not found: {source}") from exc
    if source_kind != "directory":
        raise ValueError(f"canonical skills source must be an ordinary directory: {source}")

    names: list[str] = []
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        kind = _source_entry_kind(entry)
        if entry.name in _SOURCE_IGNORED_DIRS:
            continue
        if kind != "directory":
            continue
        if _SAFE_SKILL_NAME.fullmatch(entry.name) is None or entry.name in (".", ".."):
            raise ValueError(f"canonical skill name is not a safe path component: {entry.name!r}")
        skill_file = entry / "SKILL.md"
        skill_kind = _source_entry_kind(skill_file, missing_ok=True)
        if skill_kind is None:
            raise ValueError(
                f"canonical skill directory is malformed; missing SKILL.md: {entry}"
            )
        if skill_kind != "file":
            raise ValueError(f"canonical skill SKILL.md must be an ordinary file: {skill_file}")
        _validate_source_tree(entry)
        names.append(entry.name)
    if not names:
        raise ValueError(f"canonical skills bundle is empty: {source}")
    return source, tuple(names)


def render_managed_skills_inventory(version: str, skill_names: Iterable[str]) -> str:
    """Render the canonical managed-skills inventory without writing it."""
    payload = {
        "implementation": IMPLEMENTATION_ID,
        "skills": sorted(skill_names),
        "skills_version": version,
    }
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def _write_managed_skills_inventory(
    root: Path, *, version: str, skill_names: Iterable[str]
) -> None:
    _atomic_replace_text(
        root / MANAGED_SKILLS_INVENTORY,
        render_managed_skills_inventory(version, skill_names),
        root=root,
    )


def _read_managed_skills_inventory_file(
    root: Path, path: Path
) -> tuple[str, tuple[str, ...]]:
    _assert_safe_managed_path(root, path)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"portable managed skills inventory is missing: {path}") from exc
    except OSError as exc:
        raise ValueError(
            f"portable managed skills inventory is invalid: {path}: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(
            f"portable managed skills inventory must not be a symlink: {path}"
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            f"portable managed skills inventory must be an ordinary file: {path}"
        )
    if metadata.st_nlink != 1:
        raise ValueError(
            f"portable managed skills inventory has multiple links (hard link): {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"portable managed skills inventory is invalid at {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != _INVENTORY_KEYS:
        raise ValueError(
            f"portable managed skills inventory {path} must contain exactly "
            "implementation, skills, and skills_version"
        )
    if payload["implementation"] != IMPLEMENTATION_ID:
        raise ValueError(
            f"portable managed skills inventory {path} has wrong implementation"
        )
    version = payload["skills_version"]
    if not isinstance(version, str) or not version:
        raise ValueError(
            f"portable managed skills inventory {path} skills_version must be a non-empty string"
        )
    skills = payload["skills"]
    if not isinstance(skills, list) or any(not isinstance(name, str) for name in skills):
        raise ValueError(
            f"portable managed skills inventory {path} skills must be a list of strings"
        )
    if skills != sorted(skills) or len(skills) != len(set(skills)):
        raise ValueError(
            f"portable managed skills inventory {path} skills must be unique and sorted"
        )
    for name in skills:
        if (
            not name
            or name in (".", "..")
            or _SAFE_SKILL_NAME.fullmatch(name) is None
            or "/" in name
            or "\\" in name
        ):
            raise ValueError(
                f"portable managed skills inventory {path} contains unsafe skill name {name!r}"
            )
    return version, tuple(skills)


def _read_managed_skills_inventory(root: Path) -> tuple[str, tuple[str, ...]]:
    return _read_managed_skills_inventory_file(
        root, root / MANAGED_SKILLS_INVENTORY
    )


def _ignore_source_artifacts(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in _SOURCE_IGNORED_DIRS or _source_file_is_ignored(name)
    }


def _atomic_replace_text(path: Path, text: str, *, root: Path) -> None:
    """Atomically replace *path* without truncating its existing inode."""
    _assert_safe_managed_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_managed_path(root, path)

    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.obsidian-wiki-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        _assert_safe_managed_path(root, temporary)
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_safe_managed_path(root, path)
        _assert_safe_managed_path(root, temporary)
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


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
    _atomic_replace_text(path, text, root=root)


def _write_text_if_missing(path: Path, text: str, *, root: Path) -> None:
    """Create a stable UTF-8 file without modifying an existing path."""
    _assert_safe_managed_path(root, path)
    if path.exists() or path.is_symlink():
        return
    _atomic_replace_text(path, text, root=root)


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


def render_portable_config(
    *,
    version: str,
    implementation: str = IMPLEMENTATION_ID,
    vault: str = "wiki",
    sources: tuple[str, ...] = ("sources",),
    skills: str = ".skills",
    local_state: str = ".obsidian-wiki/local",
) -> str:
    """Render repository-relative portable TOML without filesystem access."""
    source_values = ", ".join(json.dumps(source) for source in sources)
    return (
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


def render_stable_index() -> str:
    """Render the clone-stable built-in-query index."""
    return _INDEX


def render_stable_log() -> str:
    """Render the clone-stable operation log."""
    return _LOG


def render_manifest_marker() -> str:
    """Render the canonical sharded-manifest marker."""
    return json.dumps(MANIFEST_MARKER, indent=2) + "\n"


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

    text = render_portable_config(
        version=version,
        implementation=implementation,
        vault=vault,
        sources=sources,
        skills=skills,
        local_state=local_state,
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

    _write_text_if_missing(vault / "index.md", render_stable_index(), root=vault)
    _write_text_if_missing(vault / "log.md", render_stable_log(), root=vault)
    _write_text_if_missing(
        vault / ".manifest.json",
        render_manifest_marker(),
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


def _copy_missing_managed_skills(
    source_skills: Path, root: Path, skill_names: Iterable[str]
) -> None:
    """Repair only absent skills already named by a valid inventory."""
    source, available_names = _discover_source_skills(source_skills)
    available = set(available_names)
    destination = root / ".skills"
    missing = tuple(
        name for name in skill_names if not (destination / name).exists()
    )
    for name in missing:
        if name not in available:
            raise ValueError(
                f"managed canonical skill {name!r} is missing and is not in the bundled source"
            )
    for name in missing:
        target = destination / name
        _assert_safe_managed_path(root, target)
        shutil.copytree(
            source / name,
            target,
            symlinks=False,
            ignore=_ignore_source_artifacts,
        )


def _skill_tree_snapshot(
    path: Path, *, source: bool
) -> tuple[tuple[str, str, bytes], ...]:
    """Return an exact ordinary-tree snapshot, optionally applying source ignores."""
    _validate_source_tree(path)
    entries: list[tuple[str, str, bytes]] = []
    for directory, dirnames, filenames in os.walk(path, followlinks=False):
        current = Path(directory)
        if source:
            dirnames[:] = sorted(
                name for name in dirnames if name not in _SOURCE_IGNORED_DIRS
            )
            filenames = sorted(
                name for name in filenames if not _source_file_is_ignored(name)
            )
        else:
            dirnames[:] = sorted(dirnames)
            filenames = sorted(filenames)
        for name in dirnames:
            child = current / name
            _source_entry_kind(child)
            entries.append((child.relative_to(path).as_posix(), "directory", b""))
        for name in filenames:
            child = current / name
            if _source_entry_kind(child) != "file":
                raise ValueError(f"skill tree entry must be an ordinary file: {child}")
            entries.append((child.relative_to(path).as_posix(), "file", child.read_bytes()))
    return tuple(entries)


def _validate_pre_inventory_migration(
    root: Path, source: Path, skill_names: Iterable[str]
) -> None:
    """Adopt a Task-3 portable repository only when managed artifacts are exact."""
    for name in skill_names:
        canonical = root / ".skills" / name
        if not canonical.is_dir() or canonical.is_symlink():
            raise ValueError(
                "portable managed skills inventory migration requires exact canonical skills; "
                "run repo upgrade-skills after an explicit migration"
            )
        if _skill_tree_snapshot(source / name, source=True) != _skill_tree_snapshot(
            canonical, source=False
        ):
            raise ValueError(
                "portable managed skills inventory migration found changed canonical skills; "
                "run repo upgrade-skills after an explicit migration"
            )
        for agent_relative, _label in PROJECT_AGENT_DIRS:
            adapter_dir = root / agent_relative / name
            adapter = adapter_dir / "SKILL.md"
            adapter_relative = PurePosixPath(agent_relative) / name / "SKILL.md"
            canonical_relative = PurePosixPath(".skills") / name / "SKILL.md"
            expected = _legacy_adapter_text(
                name,
                posixpath.relpath(
                    canonical_relative.as_posix(), adapter_relative.parent.as_posix()
                ),
            ).encode("utf-8")
            if (
                not adapter.is_file()
                or adapter.is_symlink()
                or _skill_tree_snapshot(adapter_dir, source=False)
                != (("SKILL.md", "file", expected),)
            ):
                raise ValueError(
                    "portable managed skills inventory migration requires exact adapters; "
                    "run repo upgrade-skills after an explicit migration"
                )


def _legacy_adapter_text(skill_name: str, relative_target: str) -> str:
    return (
        "---\n"
        f"name: {skill_name}\n"
        f"description: Portable adapter for the repository-canonical {skill_name} skill.\n"
        "---\n\n"
        "# Portable skill adapter\n\n"
        f"Read and follow `{relative_target}` from this repository. Resolve that path "
        "from this adapter file, never from the process working directory.\n"
    )


def write_agent_skill_mirrors(
    root: Path,
    collection: SkillCollection,
    *,
    agent_dirs: Iterable[tuple[str, str]] = PROJECT_AGENT_DIRS,
) -> None:
    """Materialize complete ordinary-file mirrors for every supported agent."""
    root = _safe_root(Path(root))
    for agent_relative, _label in agent_dirs:
        target = root / agent_relative
        _assert_safe_managed_path(root, target)
        if target.exists() or target.is_symlink():
            raise ValueError(f"portable agent skills path already exists: {target}")
        materialize_skill_collection(collection, target)


def _validate_agent_skill_mirrors(
    root: Path,
    canonical: SkillCollection,
    *,
    remediation: bool = False,
) -> None:
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        target = root / agent_relative
        try:
            mirror = discover_skill_collection(target)
        except (OSError, ValueError) as exc:
            suffix = (
                "; run `obsidian-wiki repo sync-skills --apply`" if remediation else ""
            )
            raise ValueError(
                f"portable skill mirror is invalid at {target}: {exc}{suffix}"
            ) from exc
        if mirror != canonical:
            suffix = (
                "; run `obsidian-wiki repo sync-skills --apply`" if remediation else ""
            )
            raise ValueError(
                f"portable skill mirror differs from canonical .skills: {target}{suffix}"
            )


@dataclass(frozen=True)
class SkillMirrorChange:
    """One target-relative, deterministic mirror synchronization plan."""

    path: str
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unsafe: tuple[str, ...]


@dataclass(frozen=True)
class SkillSyncReport:
    """Read-only comparison of canonical skills and every supported mirror."""

    status: Literal["clean", "drift", "applied"]
    canonical_skills: tuple[str, ...]
    targets: tuple[SkillMirrorChange, ...]
    warnings: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "warnings",
            tuple(MappingProxyType(dict(warning)) for warning in self.warnings),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "canonical_skills": list(self.canonical_skills),
            "targets": [
                {
                    "path": target.path,
                    "added": list(target.added),
                    "changed": list(target.changed),
                    "removed": list(target.removed),
                    "unsafe": list(target.unsafe),
                }
                for target in self.targets
            ],
            "warnings": [dict(warning) for warning in self.warnings],
        }


def _canonical_skill_entries(collection: SkillCollection) -> tuple[SkillEntry, ...]:
    entries: list[SkillEntry] = []
    for skill in collection.skills:
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


def _minimal_changed_paths(
    paths: Iterable[str], *, blocking_ancestors: Iterable[str] = ()
) -> tuple[str, ...]:
    selected: list[str] = []
    blockers = set(blocking_ancestors)
    candidates = set(paths)
    if "." in blockers:
        return ()
    if "." in candidates:
        return (".",)
    for path in sorted(candidates):
        if path in blockers:
            continue
        ancestors = PurePosixPath(path).parents
        if any(
            ancestor.as_posix() in blockers or ancestor.as_posix() in selected
            for ancestor in ancestors
            if ancestor.as_posix() != "."
        ):
            continue
        selected.append(path)
    return tuple(selected)


def _plan_one_skill_mirror(
    root: Path,
    target_relative: str,
    canonical_entries: tuple[SkillEntry, ...],
) -> SkillMirrorChange:
    target = root / target_relative
    try:
        _assert_safe_managed_path(root, target)
        if target.exists():
            mirror_snapshot, unsafe = snapshot_ordinary_tree_with_unsafe(
                target, anchor=root
            )
        else:
            mirror_snapshot, unsafe = (), ()
        _assert_safe_managed_path(root, target)
    except (OSError, ValueError):
        return SkillMirrorChange(
            path=target_relative,
            added=(),
            changed=(),
            removed=(),
            unsafe=(".",),
        )

    canonical = {entry.path: entry for entry in canonical_entries}
    mirror = {entry.path: entry for entry in mirror_snapshot}
    unsafe = _minimal_changed_paths(unsafe)
    changed = _minimal_changed_paths(
        (
            path
            for path in set(canonical) & set(mirror)
            if canonical[path] != mirror[path]
        ),
        blocking_ancestors=unsafe,
    )
    blockers = (*changed, *unsafe)
    added = _minimal_changed_paths(
        set(canonical) - set(mirror), blocking_ancestors=blockers
    )
    removed = _minimal_changed_paths(
        set(mirror) - set(canonical), blocking_ancestors=blockers
    )
    return SkillMirrorChange(
        path=target_relative,
        added=added,
        changed=changed,
        removed=removed,
        unsafe=unsafe,
    )


def plan_portable_skill_sync(root: Path) -> SkillSyncReport:
    """Validate canonical skills and report every mirror difference without writes."""
    root = _safe_root(Path(root))
    canonical_root = root / ".skills"
    try:
        _assert_safe_managed_path(root, canonical_root)
        canonical = discover_skill_collection(canonical_root)
        _assert_safe_managed_path(root, canonical_root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"portable canonical skills are invalid: {exc}") from exc

    try:
        inventory = read_inventory(root, allow_legacy=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"portable managed skill inventory is invalid: {exc}") from exc
    if isinstance(inventory, LegacyManagedSkillsInventory):
        raise ValueError(  # noqa: TRY004 - a legacy schema is an invalid value here
            "portable managed skill inventory is legacy; run "
            "`obsidian-wiki repo upgrade-skills`"
        )
    assert isinstance(inventory, ManagedSkillsInventory)

    canonical_by_name = canonical.by_name()
    missing_managed = tuple(
        name for name in inventory.managed_skills if name not in canonical_by_name
    )
    if missing_managed:
        raise ValueError(
            "portable managed skill inventory names missing canonical skills: "
            + ", ".join(missing_managed)
        )
    warnings = tuple(
        {
            "code": "managed-canonical-modified",
            "path": f".skills/{name}",
            "message": (
                "managed canonical skill differs from the installed inventory digest"
            ),
        }
        for name in inventory.managed_skills
        if canonical_by_name[name].digest != inventory.managed_skill_digests[name]
    )

    canonical_entries = _canonical_skill_entries(canonical)
    targets = tuple(
        _plan_one_skill_mirror(root, relative, canonical_entries)
        for relative, _label in PROJECT_AGENT_DIRS
    )
    drift = any(
        target.added or target.changed or target.removed or target.unsafe
        for target in targets
    )
    return SkillSyncReport(
        status="drift" if drift else "clean",
        canonical_skills=canonical.names,
        targets=targets,
        warnings=warnings,
    )


def _materialize_complete_skill_trees(
    root: Path, bundled: SkillCollection
) -> SkillCollection:
    canonical_root = root / ".skills"
    _assert_safe_managed_path(root, canonical_root)
    materialize_skill_collection(bundled, canonical_root)
    canonical = discover_skill_collection(canonical_root)
    write_agent_skill_mirrors(root, canonical)
    _validate_agent_skill_mirrors(root, canonical)
    return canonical


def _managed_inventory_for_collection(
    version: str, collection: SkillCollection
) -> ManagedSkillsInventory:
    return ManagedSkillsInventory(
        skills_version=version,
        managed_skills=collection.names,
        managed_skill_digests={skill.name: skill.digest for skill in collection.skills},
    )


def _snapshot_bundled_skills(source: Path) -> SkillCollection:
    try:
        return discover_skill_collection(source, ignore_source_artifacts=True)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"canonical skill source is invalid: {exc}. Reinstall from a framework "
            f"clone with `{SOURCE_REINSTALL_COMMAND}`."
        ) from exc


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


def _render_bootstrap_target(root: Path, target: Path, existing: str) -> str:
    """Render one fixed bootstrap target from its authoritative old content."""
    relative = _repo_relative_path(root, target)
    return render_portable_bootstrap(relative, existing)


def render_portable_bootstrap(relative: str, existing: str) -> str:
    """Render one managed bootstrap file while preserving owner-maintained text."""
    if relative == "AGENTS.md":
        if not existing:
            existing = _TEAM_CONVENTIONS
        elif MANAGED_START not in existing and "## Team conventions" not in existing:
            existing = f"{_TEAM_CONVENTIONS}\n{existing}"
        return merge_managed_block(existing, _PORTABLE_AGENT_INSTRUCTIONS)

    try:
        agents_reference = _BOOTSTRAP_REFERENCES[relative]
    except KeyError as exc:  # pragma: no cover - callers use the fixed target set
        raise ValueError(f"unexpected portable bootstrap target: {relative}") from exc
    planned = _planned_bootstrap_text(existing, agents_reference)
    return existing if planned is None else planned


def _portable_bootstrap_plans(root: Path) -> list[tuple[Path, str]]:
    agents_path = root / "AGENTS.md"
    _assert_safe_managed_path(root, agents_path)
    if agents_path.exists() and not agents_path.is_file():
        raise ValueError(f"portable AGENTS.md must be an ordinary file: {agents_path}")
    if agents_path.exists():
        _assert_single_link_ordinary_file(root, agents_path, "AGENTS.md")
    existing = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
    plans: list[tuple[Path, str]] = [
        (agents_path, _render_bootstrap_target(root, agents_path, existing))
    ]
    for relative in _BOOTSTRAP_REFERENCES:
        target = root / relative
        _assert_safe_managed_path(root, target)
        _assert_safe_managed_path(root, target.parent)
        if target.parent.exists() and not target.parent.is_dir():
            raise ValueError(
                f"portable bootstrap parent must be an ordinary directory: {target.parent}"
            )
        if target.exists() and not target.is_file():
            raise ValueError(f"portable bootstrap destination collision: {target}")
        if target.exists():
            _assert_single_link_ordinary_file(
                root, target, f"bootstrap target {relative}"
            )
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        plans.append((target, _render_bootstrap_target(root, target, current)))
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


def render_portable_gitignore(existing: str, vault_relative: str = "wiki") -> str:
    """Render required local-state ignores while preserving existing entries."""
    escaped_vault = _escape_gitignore_path(vault_relative)
    prefix = "" if escaped_vault == "." else f"{escaped_vault}/"
    required = (
        *PORTABLE_ROOT_IGNORE,
        f"{prefix}hot.md",
        f"{prefix}.obsidian/workspace.json",
        f"{prefix}.obsidian/workspace-mobile.json",
        f"{prefix}.trash/",
    )
    present = {line.strip() for line in existing.splitlines()}
    missing = [entry for entry in required if entry not in present]
    if not missing:
        return existing
    separator = "" if not existing or existing.endswith("\n") else "\n"
    appended = "\n".join(missing)
    return f"{existing}{separator}{appended}\n"


def ensure_portable_gitignore(root: Path, vault: str | Path = "wiki") -> None:
    """Append portable local-state ignores while preserving existing entries."""
    root = _safe_root(Path(root))
    vault_relative = _vault_relative_posix(root, vault)
    path = root / ".gitignore"
    _assert_safe_managed_path(root, path)
    if path.exists() and not path.is_file():
        raise ValueError(f"portable .gitignore must be an ordinary file: {path}")
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    rendered = render_portable_gitignore(existing, vault_relative)
    if rendered == existing:
        return
    _write_text_if_changed(path, rendered, root=root)


def render_portable_gitattributes(existing: str) -> str:
    """Render byte-stability attributes last while retaining owner rules."""
    start_count = existing.count(GITATTRIBUTES_START)
    end_count = existing.count(GITATTRIBUTES_END)
    if start_count != end_count or start_count > 1:
        raise ValueError("malformed obsidian-wiki gitattributes markers")
    owner = existing
    if start_count:
        start = existing.index(GITATTRIBUTES_START)
        end = existing.index(GITATTRIBUTES_END)
        if end < start:
            raise ValueError("malformed obsidian-wiki gitattributes marker order")
        end += len(GITATTRIBUTES_END)
        owner = f"{existing[:start]}{existing[end:]}"
    owner = owner.strip("\n")
    block = (
        f"{GITATTRIBUTES_START}\n"
        f"{_PORTABLE_GITATTRIBUTES.rstrip()}\n"
        f"{GITATTRIBUTES_END}\n"
    )
    return f"{owner}\n\n{block}" if owner else block


def ensure_portable_gitattributes(root: Path) -> None:
    """Install tracked rules that prevent clone-specific byte conversion."""
    root = _safe_root(Path(root))
    path = root / ".gitattributes"
    _assert_safe_managed_path(root, path)
    if path.exists() and not path.is_file():
        raise ValueError(f"portable .gitattributes must be an ordinary file: {path}")
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    rendered = render_portable_gitattributes(existing)
    if rendered == existing:
        return
    _write_text_if_changed(path, rendered, root=root)


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
        root / ".gitattributes",
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
    _assert_single_link_ordinary_file(root, config_path, "configuration")
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
            _assert_single_link_managed_tree(
                root, canonical, f"canonical skill {skill_name}"
            )
            _assert_ordinary_file(root, canonical / "SKILL.md", f"canonical skill {skill_name}")
        for agent_relative, _label in PROJECT_AGENT_DIRS:
            adapter = root / agent_relative / skill_name / "SKILL.md"
            _assert_safe_managed_path(root, adapter)
            if adapter.parent.exists() and not adapter.parent.is_dir():
                raise ValueError(f"portable adapter directory collision: {adapter.parent}")
            if adapter.parent.exists():
                _assert_single_link_managed_tree(
                    root, adapter.parent, f"adapter skill {skill_name}"
                )
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
    gitattributes = root / ".gitattributes"
    _assert_safe_managed_path(root, gitattributes)
    if gitattributes.exists() and not gitattributes.is_file():
        raise ValueError(
            f"portable .gitattributes must be an ordinary file: {gitattributes}"
        )
    if gitattributes.is_file():
        try:
            render_portable_gitattributes(
                gitattributes.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"portable .gitattributes is invalid: {exc}") from exc


def _populate_portable_repo(
    root: Path, *, version: str, bundled_skills: SkillCollection
) -> None:
    write_portable_config(root, version=version)
    _ensure_portable_lock_file(root)
    sources = root / "sources"
    _assert_safe_managed_path(root, sources)
    if sources.exists() and not sources.is_dir():
        raise ValueError(f"portable sources path must be a directory: {sources}")
    sources.mkdir(parents=True, exist_ok=True)
    scaffold_portable_vault(root / "wiki")
    canonical = _materialize_complete_skill_trees(root, bundled_skills)
    install_portable_bootstrap(root)
    ensure_portable_gitattributes(root)
    ensure_portable_gitignore(root, "wiki")
    (root / MANAGED_SKILLS_INVENTORY).write_text(
        render_inventory(_managed_inventory_for_collection(version, canonical)),
        encoding="utf-8",
    )


def _repair_existing_portable_repo(root: Path) -> None:
    """Repair missing non-skill managed artifacts without upgrading skill bytes."""
    install_portable_bootstrap(root)
    ensure_portable_gitattributes(root)
    ensure_portable_gitignore(root, "wiki")


def _ensure_portable_lock_file(root: Path) -> Path:
    path = root / _PORTABLE_SKILLS_LOCK
    _assert_safe_managed_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_managed_path(root, path)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError(
                f"portable skills lock must be a single-link ordinary file: {path}"
            )
        os.fchmod(descriptor, 0o600)
    except OSError as exc:
        raise ValueError(f"cannot open portable skills lock {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return path


@contextmanager
def _portable_skills_lock(root: Path) -> Iterator[None]:
    if fcntl is None:  # pragma: no cover - Linux/macOS are the supported hosts
        raise RuntimeError("portable skill upgrades require fcntl.flock")
    path = _ensure_portable_lock_file(root)
    flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                f"portable repository skills are locked by another upgrade: {root}"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _repo_relative_path(root: Path, path: Path) -> str:
    candidate = _absolute_no_resolve(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"transaction path escapes portable repository: {path}") from exc
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError(f"transaction path is not safely repository-relative: {path}")
    return PurePosixPath(*relative.parts).as_posix()


def _journal_repo_path(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError(f"upgrade journal {label} must be a repository-relative path")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError(f"upgrade journal {label} must remain inside the repository")
    candidate = root.joinpath(*relative.parts)
    _assert_safe_managed_path(root, candidate)
    return candidate


def _journal_target_is_managed(relative: str) -> bool:
    if relative == MANAGED_SKILLS_INVENTORY or relative == "AGENTS.md":
        return True
    if relative in _BOOTSTRAP_REFERENCES:
        return True
    parts = PurePosixPath(relative).parts
    if (
        len(parts) == 2
        and parts[0] == ".skills"
        and _SAFE_SKILL_NAME.fullmatch(parts[1]) is not None
    ):
        return True
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        agent_parts = PurePosixPath(agent_relative).parts
        if (
            len(parts) == len(agent_parts) + 1
            and parts[: len(agent_parts)] == agent_parts
            and _SAFE_SKILL_NAME.fullmatch(parts[-1]) is not None
        ):
            return True
    return False


def _parent_path_order(root: Path, path: Path) -> tuple[int, str]:
    relative = PurePosixPath(_repo_relative_path(root, path))
    return len(relative.parts), relative.as_posix()


def _validate_journal_created_parents(
    root: Path,
    records: list[tuple[Path, Path, Path | None, bool]],
    raw_parents: object,
) -> tuple[Path, ...]:
    if not isinstance(raw_parents, list) or any(
        not isinstance(raw, str) for raw in raw_parents
    ):
        raise ValueError("portable upgrade journal created_parents must be a list of paths")
    parents = tuple(
        _journal_repo_path(root, raw, "created parent") for raw in raw_parents
    )
    if len(parents) != len(set(parents)) or list(parents) != sorted(
        parents, key=lambda path: _parent_path_order(root, path)
    ):
        raise ValueError(
            "portable upgrade journal created parents must be unique and canonically ordered"
        )

    allowed: set[Path] = set()
    for target, _backup, _staged, _had_target in records:
        parent = target.parent
        while parent != root:
            allowed.add(parent)
            parent = parent.parent
    unexpected = [parent for parent in parents if parent not in allowed]
    if unexpected:
        raise ValueError(
            "portable upgrade journal created parent is not an ancestor of a fixed "
            f"replacement target: {unexpected[0]}"
        )
    return parents


def _stage_text_for_replacement(
    root: Path, transaction: Path, staged: Path, target: Path, text: str
) -> None:
    _atomic_replace_text(staged, text, root=transaction)
    mode = 0o644
    if target.exists():
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"managed replacement target must be an ordinary file: {target}")
        mode = stat.S_IMODE(metadata.st_mode)
    os.chmod(staged, mode)


def _write_upgrade_journal(root: Path, transaction: Path, payload: dict[str, object]) -> None:
    journal = transaction / _UPGRADE_JOURNAL
    _atomic_replace_text(
        journal,
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        root=root,
    )
    os.chmod(journal, 0o600)


def _journal_records(
    root: Path, transaction: Path, payload: object
) -> tuple[str, list[tuple[Path, Path, Path | None, bool]], tuple[Path, ...]]:
    if not isinstance(payload, dict) or set(payload) != {
        "created_parents",
        "implementation",
        "replacements",
        "schema_version",
        "status",
    }:
        raise ValueError(f"invalid portable upgrade journal: {transaction / _UPGRADE_JOURNAL}")
    if (
        payload["schema_version"] != _UPGRADE_JOURNAL_SCHEMA
        or payload["implementation"] != IMPLEMENTATION_ID
    ):
        raise ValueError(f"invalid portable upgrade journal identity: {transaction}")
    status_value = payload["status"]
    if status_value not in ("prepared", "committed"):
        raise ValueError(f"invalid portable upgrade journal status: {transaction}")
    raw_records = payload["replacements"]
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError(f"invalid portable upgrade journal replacements: {transaction}")

    transaction_relative = _repo_relative_path(root, transaction)
    records: list[tuple[Path, Path, Path | None, bool]] = []
    target_names: set[str] = set()
    backup_names: set[str] = set()
    staged_names: set[str] = set()
    install_names: set[str] = set()
    for index, raw_record in enumerate(raw_records):
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "backup",
            "had_target",
            "install",
            "staged",
            "target",
        }:
            raise ValueError(f"invalid portable upgrade journal record {index}: {transaction}")
        target_raw = raw_record["target"]
        backup_raw = raw_record["backup"]
        install_raw = raw_record["install"]
        staged_raw = raw_record["staged"]
        had_target = raw_record["had_target"]
        if not isinstance(target_raw, str) or not _journal_target_is_managed(target_raw):
            raise ValueError(f"unsafe portable upgrade journal target: {target_raw!r}")
        expected_backup = f"{transaction_relative}/backups/{index}"
        if backup_raw != expected_backup:
            raise ValueError(f"unsafe portable upgrade journal backup: {backup_raw!r}")
        if type(had_target) is not bool:
            raise ValueError(f"invalid portable upgrade journal had_target: {transaction}")
        target = _journal_repo_path(root, target_raw, "target")
        backup = _journal_repo_path(root, backup_raw, "backup")
        staged: Path | None = None
        if staged_raw is not None:
            if (
                not isinstance(staged_raw, str)
                or not staged_raw.startswith(f"{transaction_relative}/staged/")
            ):
                raise ValueError(f"unsafe portable upgrade journal staged path: {staged_raw!r}")
            staged = _journal_repo_path(root, staged_raw, "staged")
        expected_install = (
            f"{transaction_relative}/install/{index}" if staged is not None else None
        )
        if install_raw != expected_install:
            raise ValueError(
                f"unsafe portable upgrade journal install candidate: {install_raw!r}"
            )
        if install_raw is not None:
            _journal_repo_path(root, install_raw, "install candidate")
        if target_raw in target_names or backup_raw in backup_names:
            raise ValueError(f"duplicate portable upgrade journal mapping: {transaction}")
        if staged_raw is not None and staged_raw in staged_names:
            raise ValueError(f"duplicate portable upgrade journal staged path: {transaction}")
        if install_raw is not None and install_raw in install_names:
            raise ValueError(
                f"duplicate portable upgrade journal install path: {transaction}"
            )
        target_names.add(target_raw)
        backup_names.add(str(backup_raw))
        if staged_raw is not None:
            staged_names.add(str(staged_raw))
        if install_raw is not None:
            install_names.add(str(install_raw))
        records.append((target, backup, staged, had_target))
    if _repo_relative_path(root, records[-1][0]) != MANAGED_SKILLS_INVENTORY:
        raise ValueError(f"portable upgrade journal inventory mapping must be last: {transaction}")
    created_parents = _validate_journal_created_parents(
        root, records, payload["created_parents"]
    )
    return str(status_value), records, created_parents


def _load_upgrade_journal(
    root: Path, transaction: Path
) -> tuple[
    dict[str, object],
    list[tuple[Path, Path, Path | None, bool]],
    tuple[Path, ...],
]:
    transactions = root / _UPGRADE_TRANSACTIONS
    _assert_safe_managed_path(root, transaction)
    if transaction.parent != transactions or not transaction.name.startswith("txn-"):
        raise ValueError(f"unsafe portable upgrade transaction path: {transaction}")
    _assert_directory(root, transaction, "upgrade transaction")
    _assert_managed_tree(root, transaction)
    journal = transaction / _UPGRADE_JOURNAL
    try:
        metadata = journal.lstat()
    except OSError as exc:
        raise ValueError(f"portable upgrade journal is missing or invalid: {journal}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise ValueError(f"portable upgrade journal must be an ordinary file: {journal}")
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"portable upgrade journal is invalid: {journal}: {exc}") from exc
    status, records, created_parents = _journal_records(root, transaction, payload)
    assert isinstance(payload, dict)
    payload["status"] = status
    return payload, records, created_parents


def _journal_skill_name(root: Path, target: Path) -> str | None:
    parts = PurePosixPath(_repo_relative_path(root, target)).parts
    if len(parts) == 2 and parts[0] == ".skills":
        return parts[1]
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        agent_parts = PurePosixPath(agent_relative).parts
        if len(parts) == len(agent_parts) + 1 and parts[: len(agent_parts)] == agent_parts:
            return parts[-1]
    return None


def _replacement_snapshot(
    root: Path, path: Path
) -> tuple[tuple[str, str, int, bytes], ...]:
    """Return exact replacement content and modes without following links."""
    _assert_safe_managed_path(root, path)
    snapshot: list[tuple[str, str, int, bytes]] = []

    def visit(current: Path, relative: PurePosixPath) -> None:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ValueError(
                f"portable upgrade recovery proof is missing or unreadable: {current}: {exc}"
            ) from exc
        mode = metadata.st_mode
        relative_name = "" if relative == PurePosixPath(".") else relative.as_posix()
        if stat.S_ISLNK(mode):
            raise ValueError(
                f"portable upgrade recovery proof contains a symlink: {current}"
            )
        if stat.S_ISREG(mode):
            if metadata.st_nlink != 1:
                raise ValueError(
                    "portable upgrade recovery proof contains a multiply-linked file: "
                    f"{current}"
                )
            try:
                content = current.read_bytes()
            except OSError as exc:
                raise ValueError(
                    f"portable upgrade recovery proof is unreadable: {current}: {exc}"
                ) from exc
            snapshot.append(
                (relative_name, "file", stat.S_IMODE(mode), content)
            )
            return
        if not stat.S_ISDIR(mode):
            raise ValueError(
                "portable upgrade recovery proof contains a special filesystem entry: "
                f"{current}"
            )
        snapshot.append((relative_name, "directory", stat.S_IMODE(mode), b""))
        try:
            children = sorted(current.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise ValueError(
                f"portable upgrade recovery proof directory is unreadable: {current}: {exc}"
            ) from exc
        for child in children:
            visit(child, relative / child.name)

    visit(path, PurePosixPath("."))
    return tuple(snapshot)


def _source_replacement_snapshot(
    path: Path, *, root_mode: int | None = None
) -> tuple[tuple[str, str, int, bytes], ...]:
    """Return the exact canonical tree that source staging will copy."""
    _validate_source_tree(path)
    snapshot: list[tuple[str, str, int, bytes]] = []

    def visit(current: Path, relative: PurePosixPath) -> None:
        metadata = current.lstat()
        relative_name = "" if relative == PurePosixPath(".") else relative.as_posix()
        if stat.S_ISDIR(metadata.st_mode):
            snapshot.append(
                (
                    relative_name,
                    "directory",
                    (
                        root_mode
                        if relative_name == "" and root_mode is not None
                        else stat.S_IMODE(metadata.st_mode)
                    ),
                    b"",
                )
            )
            for child in sorted(current.iterdir(), key=lambda entry: entry.name):
                if child.is_dir() and child.name in _SOURCE_IGNORED_DIRS:
                    continue
                if child.is_file() and _source_file_is_ignored(child.name):
                    continue
                visit(child, relative / child.name)
            return
        snapshot.append(
            (
                relative_name,
                "file",
                stat.S_IMODE(metadata.st_mode),
                current.read_bytes(),
            )
        )

    visit(path, PurePosixPath("."))
    return tuple(snapshot)


def _copy_staged_replacement(root: Path, staged: Path, target: Path) -> None:
    """Install a new target while retaining staged content as crash proof."""
    expected = _replacement_snapshot(root, staged)
    if staged.is_dir():
        shutil.copytree(staged, target, symlinks=False, copy_function=shutil.copy2)
    elif staged.is_file() and not staged.is_symlink():
        shutil.copy2(staged, target, follow_symlinks=False)
    else:  # pragma: no cover - snapshot classification rejects this first
        raise ValueError(f"portable upgrade staged replacement is invalid: {staged}")
    if _replacement_snapshot(root, target) != expected:
        raise OSError(
            f"portable upgrade could not verify copied replacement: {target}"
        )


def _authorize_upgrade_recovery(
    root: Path,
    transaction: Path,
    records: list[tuple[Path, Path, Path | None, bool]],
    *,
    rollback: bool,
    version: str,
    source: Path,
    current_names: tuple[str, ...],
) -> set[Path]:
    def validate_artifacts(
        index: int,
        staged: Path,
        expected: tuple[tuple[str, str, int, bytes], ...],
        label: str,
    ) -> None:
        if _replacement_snapshot(root, staged) != expected:
            raise ValueError(
                f"portable upgrade recovery {label} proof does not match trusted content"
            )
        install = transaction / "install" / str(index)
        if (install.exists() or install.is_symlink()) and _replacement_snapshot(
            root, install
        ) != expected:
            raise ValueError(
                f"portable upgrade recovery {label} install candidate does not "
                "match its retained proof"
            )

    inventory_target, inventory_backup, inventory_staged, inventory_had_target = (
        records[-1]
    )
    if inventory_target != root / MANAGED_SKILLS_INVENTORY:
        raise ValueError("portable upgrade journal has an inconsistent inventory target")
    if not inventory_had_target or inventory_staged is None:
        raise ValueError(
            "portable upgrade journal inventory mapping must replace an existing inventory"
        )
    expected_inventory_staged = transaction / "staged/inventory/managed-skills.json"
    if inventory_staged != expected_inventory_staged:
        raise ValueError(
            "portable upgrade recovery inventory has an unexpected staged layout"
        )

    if inventory_backup.exists():
        old_inventory = inventory_backup
    else:
        old_inventory = inventory_target
        if not old_inventory.exists():
            raise ValueError(
                "portable upgrade journal old inventory and backup are both missing"
            )
    _old_version, old_names = _read_managed_skills_inventory_file(
        root, old_inventory
    )
    old_skills = set(old_names)
    current_skills = set(current_names)
    expected_inventory = (
        (
            "",
            "file",
            stat.S_IMODE(old_inventory.lstat().st_mode),
            render_managed_skills_inventory(version, current_names).encode("utf-8"),
        ),
    )
    validate_artifacts(
        len(records) - 1,
        inventory_staged,
        expected_inventory,
        "inventory",
    )

    expected_skill_records: dict[
        Path, tuple[Path | None, bool, str, int | None]
    ] = {}
    for name in sorted(old_skills | current_skills):
        expected_skill_records[root / ".skills" / name] = (
            transaction / "staged/canonical" / name
            if name in current_skills
            else None,
            name in old_skills,
            name,
            None,
        )
        for agent_index, (agent_relative, _label) in enumerate(PROJECT_AGENT_DIRS):
            expected_skill_records[root / agent_relative / name] = (
                transaction / "staged/adapters" / str(agent_index) / name
                if name in current_skills
                else None,
                name in old_skills,
                name,
                agent_index,
            )
    actual_skill_records = {
        target: (index, backup, staged, had_target)
        for index, (target, backup, staged, had_target) in enumerate(records[:-1])
        if _journal_skill_name(root, target) is not None
    }
    if set(actual_skill_records) != set(expected_skill_records):
        missing = sorted(
            _repo_relative_path(root, target)
            for target in set(expected_skill_records) - set(actual_skill_records)
        )
        unexpected = sorted(
            _repo_relative_path(root, target)
            for target in set(actual_skill_records) - set(expected_skill_records)
        )
        raise ValueError(
            "portable upgrade recovery skill record plan is incomplete or unexpected; "
            f"missing={missing}, unexpected={unexpected}"
        )

    for target, (expected_staged, expected_had_target, name, agent_index) in (
        expected_skill_records.items()
    ):
        index, backup, staged, had_target = actual_skill_records[target]
        if staged != expected_staged or had_target != expected_had_target:
            raise ValueError(
                "portable upgrade recovery skill record has an unexpected staged layout "
                f"or ownership flag: {target}"
            )
        if staged is None:
            continue
        original = backup if backup.exists() else target
        if had_target and not original.exists():
            raise ValueError(
                f"portable upgrade recovery original skill is missing: {target}"
            )
        root_mode = (
            stat.S_IMODE(original.lstat().st_mode) if had_target else 0o755
        )
        if agent_index is None:
            trusted_snapshot = _source_replacement_snapshot(
                source / name, root_mode=root_mode
            )
            validate_artifacts(index, staged, trusted_snapshot, f"canonical {target}")
        else:
            agent_relative = PROJECT_AGENT_DIRS[agent_index][0]
            adapter_relative = PurePosixPath(agent_relative) / name / "SKILL.md"
            canonical_relative = PurePosixPath(".skills") / name / "SKILL.md"
            trusted_adapter = _legacy_adapter_text(
                name,
                posixpath.relpath(
                    canonical_relative.as_posix(),
                    adapter_relative.parent.as_posix(),
                ),
            ).encode("utf-8")
            skill_mode = 0o644
            if had_target:
                skill_file = original / "SKILL.md"
                try:
                    skill_metadata = skill_file.lstat()
                except OSError as exc:
                    raise ValueError(
                        f"portable upgrade recovery original adapter is missing: {target}"
                    ) from exc
                if not stat.S_ISREG(skill_metadata.st_mode) or stat.S_ISLNK(
                    skill_metadata.st_mode
                ):
                    raise ValueError(
                        f"portable upgrade recovery original adapter is invalid: {target}"
                    )
                skill_mode = stat.S_IMODE(skill_metadata.st_mode)
            trusted_snapshot = (
                ("", "directory", root_mode, b""),
                ("SKILL.md", "file", skill_mode, trusted_adapter),
            )
            validate_artifacts(index, staged, trusted_snapshot, f"adapter {target}")

    fixed_bootstrap_targets = (root / "AGENTS.md",) + tuple(
        root / relative for relative in _BOOTSTRAP_REFERENCES
    )
    actual_bootstrap_records = {
        target: (index, backup, staged, had_target)
        for index, (target, backup, staged, had_target) in enumerate(records[:-1])
        if _journal_skill_name(root, target) is None
    }
    if set(actual_bootstrap_records) != set(fixed_bootstrap_targets):
        missing = sorted(
            _repo_relative_path(root, target)
            for target in set(fixed_bootstrap_targets) - set(actual_bootstrap_records)
        )
        unexpected = sorted(
            _repo_relative_path(root, target)
            for target in set(actual_bootstrap_records) - set(fixed_bootstrap_targets)
        )
        raise ValueError(
            "portable upgrade recovery bootstrap record plan is incomplete or "
            f"unexpected; missing={missing}, unexpected={unexpected}"
        )
    for bootstrap_index, target in enumerate(fixed_bootstrap_targets):
        index, backup, staged, had_target = actual_bootstrap_records[target]
        expected_staged = transaction / "staged/bootstrap" / str(bootstrap_index)
        if staged != expected_staged:
            raise ValueError(
                f"portable upgrade recovery bootstrap staged layout is invalid: {target}"
            )
        original = backup if backup.exists() else target
        if had_target:
            try:
                metadata = original.lstat()
            except OSError as exc:
                raise ValueError(
                    f"portable upgrade recovery bootstrap authority is missing: {target}"
                ) from exc
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise ValueError(
                    f"portable upgrade recovery bootstrap authority is invalid: {target}"
                )
            try:
                original_text = original.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ValueError(
                    f"portable upgrade recovery bootstrap authority is unreadable: {target}"
                ) from exc
            mode = stat.S_IMODE(metadata.st_mode)
        else:
            if backup.exists() or backup.is_symlink():
                raise ValueError(
                    "portable upgrade journal has a bootstrap backup for an originally "
                    f"absent target: {target}"
                )
            original_text = ""
            mode = 0o644
        expected_bootstrap = (
            (
                "",
                "file",
                mode,
                _render_bootstrap_target(root, target, original_text).encode("utf-8"),
            ),
        )
        validate_artifacts(
            index, staged, expected_bootstrap, f"bootstrap {target}"
        )

    removable_new_targets: set[Path] = set()

    for target, backup, staged, had_target in records[:-1]:
        skill_name = _journal_skill_name(root, target)
        if had_target and skill_name is not None and skill_name not in old_skills:
            raise ValueError(
                f"portable upgrade journal cannot claim unlisted owner skill "
                f"{skill_name!r}: {target}"
            )
        if not had_target and backup.exists():
            raise ValueError(
                f"portable upgrade journal has a backup for an originally absent target: "
                f"{target}"
            )
        if rollback and not had_target and (target.exists() or target.is_symlink()):
            if staged is None:
                raise ValueError(
                    "portable upgrade recovery has no staged proof for an originally "
                    f"absent target; preserved without mutation: {target}"
                )
            if _replacement_snapshot(root, target) != _replacement_snapshot(root, staged):
                raise ValueError(
                    "portable upgrade recovery staged proof does not match an originally "
                    f"absent target; preserved without mutation: {target}"
                )
            removable_new_targets.add(target)

    if not rollback:
        for target, _backup, staged, _had_target in records:
            if staged is None:
                if target.exists() or target.is_symlink():
                    raise ValueError(
                        f"portable committed upgrade retained a removed target: {target}"
                    )
            elif not target.exists() or (
                _replacement_snapshot(root, target)
                != _replacement_snapshot(root, staged)
            ):
                raise ValueError(
                    f"portable committed upgrade target diverged from proof: {target}"
                )
    return removable_new_targets


def _remove_transaction_target(root: Path, path: Path) -> None:
    _assert_safe_managed_path(root, path)
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        raise ValueError(f"managed path contains symlink: {path}")
    if path.is_dir():
        _assert_managed_tree(root, path)
        shutil.rmtree(path)
    else:
        path.unlink()


def _rollback_upgrade_records(
    root: Path,
    records: list[tuple[Path, Path, Path | None, bool]],
    *,
    removable_new_targets: set[Path] | None = None,
) -> list[str]:
    removable = removable_new_targets or set()
    errors: list[str] = []
    for target, backup, staged, had_target in reversed(records):
        try:
            if backup.exists():
                backup_snapshot = _replacement_snapshot(root, backup)
                if not target.exists() and not target.is_symlink():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    backup.replace(target)
                    continue
                target_snapshot = _replacement_snapshot(root, target)
                if target_snapshot == backup_snapshot:
                    _remove_transaction_target(root, backup)
                    continue
                if staged is not None and target_snapshot == _replacement_snapshot(
                    root, staged
                ):
                    _remove_transaction_target(root, target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    backup.replace(target)
                    continue
                raise OSError(
                    "owner-diverged target cannot be overwritten during rollback"
                )
            elif not had_target:
                if target.exists() and target not in removable:
                    raise OSError(
                        "refusing to remove an originally absent target without "
                        f"runtime transaction proof: {target}"
                    )
                _remove_transaction_target(root, target)
            elif not target.exists():
                raise OSError(f"original target and backup are both missing: {target}")
        except BaseException as exc:
            errors.append(f"{target}: {exc}")
    return errors


def _rollback_created_parents(root: Path, created_parents: tuple[Path, ...]) -> list[str]:
    errors: list[str] = []
    for parent in reversed(created_parents):
        try:
            _assert_safe_managed_path(root, parent)
            if not parent.exists() and not parent.is_symlink():
                continue
            metadata = parent.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise OSError(f"created parent is no longer an ordinary directory: {parent}")
            parent.rmdir()
        except BaseException as exc:
            errors.append(f"created parent {parent}: {exc}")
    return errors


def _cleanup_upgrade_transaction(root: Path, transaction: Path) -> None:
    _assert_safe_managed_path(root, transaction)
    _assert_managed_tree(root, transaction)
    shutil.rmtree(transaction)
    try:
        transaction.parent.rmdir()
    except OSError:
        pass


def _recover_upgrade_transactions(
    root: Path,
    *,
    version: str,
    source: Path,
    current_names: tuple[str, ...],
) -> None:
    transactions = root / _UPGRADE_TRANSACTIONS
    _assert_safe_managed_path(root, transactions)
    if not transactions.exists():
        return
    _assert_directory(root, transactions, "upgrade transactions")
    for transaction in sorted(transactions.iterdir(), key=lambda path: path.name):
        journal = transaction / _UPGRADE_JOURNAL
        if not journal.exists() and not journal.is_symlink():
            _assert_safe_managed_path(root, transaction)
            try:
                metadata = transaction.lstat()
            except OSError as exc:
                raise ValueError(
                    f"portable upgrade transaction is unreadable: {transaction}"
                ) from exc
            if (
                transaction.parent != transactions
                or not transaction.name.startswith("txn-")
                or not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
            ):
                raise ValueError(f"unsafe portable upgrade transaction path: {transaction}")
            # A journal is the mutation boundary.  Without one, every entry is
            # disposable preparation state; rmtree unlinks internal symlinks
            # without following them.
            shutil.rmtree(transaction)
            continue
        payload, records, created_parents = _load_upgrade_journal(root, transaction)
        removable_new_targets = _authorize_upgrade_recovery(
            root,
            transaction,
            records,
            rollback=payload["status"] == "prepared",
            version=version,
            source=source,
            current_names=current_names,
        )
        if payload["status"] == "committed":
            _cleanup_upgrade_transaction(root, transaction)
            continue
        errors = _rollback_upgrade_records(
            root, records, removable_new_targets=removable_new_targets
        )
        errors.extend(_rollback_created_parents(root, created_parents))
        if errors:
            raise OSError(
                "portable skill upgrade recovery is incomplete; preserved journal and "
                f"backups at {transaction}: {'; '.join(errors)}"
            )
        _cleanup_upgrade_transaction(root, transaction)


def _preflight_upgrade_paths(
    root: Path,
    *,
    previous_names: tuple[str, ...],
    current_names: tuple[str, ...],
) -> list[tuple[Path, str]]:
    """Validate all managed and potentially owner-owned upgrade targets."""
    previous = set(previous_names)
    current = set(current_names)
    _assert_directory(root, root / ".skills", "canonical skills path")
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        agent_root = root / agent_relative
        _assert_directory(root, agent_root, "agent skills path")

    for name in sorted(previous | current):
        targets = [root / ".skills" / name] + [
            root / agent_relative / name
            for agent_relative, _label in PROJECT_AGENT_DIRS
        ]
        for target in targets:
            _assert_safe_managed_path(root, target)
            if not target.exists():
                continue
            if name not in previous:
                raise ValueError(
                    f"new bundled skill {name!r} collides with an unlisted owner "
                    f"directory: {target}"
                )
            _assert_directory(root, target, f"managed skill directory {name}")
            _assert_single_link_managed_tree(
                root, target, f"managed skill directory {name}"
            )

    return _portable_bootstrap_plans(root)


def _prepare_upgrade_journal(
    root: Path,
    transaction: Path,
    replacements: list[tuple[Path, Path | None]],
) -> tuple[dict[str, object], list[tuple[Path, Path, Path | None, bool]]]:
    (transaction / "backups").mkdir()
    (transaction / "install").mkdir()
    transaction_relative = _repo_relative_path(root, transaction)
    raw_records: list[dict[str, object]] = []
    for index, (target, staged) in enumerate(replacements):
        install: Path | None = None
        if staged is not None:
            install = transaction / "install" / str(index)
            _copy_staged_replacement(root, staged, install)
        raw_records.append(
            {
                "backup": f"{transaction_relative}/backups/{index}",
                "had_target": target.exists(),
                "install": (
                    _repo_relative_path(root, install) if install is not None else None
                ),
                "staged": _repo_relative_path(root, staged) if staged is not None else None,
                "target": _repo_relative_path(root, target),
            }
        )
    created_parents: set[Path] = set()
    for target, _staged in replacements:
        parent = target.parent
        while parent != root and not parent.exists():
            _assert_safe_managed_path(root, parent)
            created_parents.add(parent)
            parent = parent.parent
    payload: dict[str, object] = {
        "created_parents": [
            _repo_relative_path(root, parent)
            for parent in sorted(
                created_parents, key=lambda path: _parent_path_order(root, path)
            )
        ],
        "implementation": IMPLEMENTATION_ID,
        "replacements": raw_records,
        "schema_version": _UPGRADE_JOURNAL_SCHEMA,
        "status": "prepared",
    }
    _status, records, _created_parents = _journal_records(root, transaction, payload)
    _write_upgrade_journal(root, transaction, payload)
    return payload, records


def _apply_journaled_upgrade(
    root: Path,
    transaction: Path,
    payload: dict[str, object],
    records: list[tuple[Path, Path, Path | None, bool]],
) -> None:
    created_targets: set[Path] = set()
    _status, _validated_records, created_parents = _journal_records(
        root, transaction, payload
    )
    try:
        for index, (target, backup, staged, had_target) in enumerate(records):
            install = transaction / "install" / str(index)
            if staged is not None:
                if not install.exists() or install.is_symlink():
                    raise OSError(
                        f"portable upgrade install candidate disappeared: {install}"
                    )
                if _replacement_snapshot(root, install) != _replacement_snapshot(
                    root, staged
                ):
                    raise OSError(
                        f"portable upgrade install candidate diverged from proof: {install}"
                    )
            if had_target:
                if not target.exists():
                    raise OSError(f"managed upgrade target disappeared: {target}")
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup)
            elif target.exists() or target.is_symlink():
                raise OSError(f"managed upgrade target appeared during transaction: {target}")
            if staged is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not had_target:
                    created_targets.add(target)
                install.replace(target)
        payload["status"] = "committed"
        _write_upgrade_journal(root, transaction, payload)
    except BaseException as forward_error:
        rollback_errors = _rollback_upgrade_records(
            root, records, removable_new_targets=created_targets
        )
        rollback_errors.extend(_rollback_created_parents(root, created_parents))
        if rollback_errors:
            raise OSError(
                f"portable skill upgrade failed ({forward_error}); rollback is incomplete; "
                f"journal and backups preserved at {transaction}: "
                f"{'; '.join(rollback_errors)}"
            ) from forward_error
        try:
            _cleanup_upgrade_transaction(root, transaction)
        except BaseException as cleanup_error:
            raise OSError(
                f"portable skill upgrade failed ({forward_error}); rollback completed but "
                f"transaction cleanup failed at {transaction}: {cleanup_error}"
            ) from forward_error
        raise
    _cleanup_upgrade_transaction(root, transaction)


def upgrade_portable_skills(
    root: Path,
    *,
    version: str,
    source_skills: Path,
) -> tuple[str, ...]:
    """Upgrade only repository-managed skills, adapters, and bootstrap blocks.

    The inventory is the ownership boundary. Unlisted directories are never
    adopted, replaced, or removed, and the new inventory is committed last.
    """
    compatible_cli_spec(version)
    root = _safe_root(Path(root))
    if not root.is_dir():
        raise ValueError(f"portable repository root is not a directory: {root}")
    with _portable_skills_lock(root):
        config_path = root / ".obsidian-wiki/config.toml"
        _assert_ordinary_file(root, config_path, "configuration")
        _assert_single_link_ordinary_file(root, config_path, "configuration")
        _load_canonical_portable_config(root, version=version)
        source, current_names = _discover_source_skills(source_skills)
        _recover_upgrade_transactions(
            root,
            version=version,
            source=source,
            current_names=current_names,
        )
        _previous_version, previous_names = _read_managed_skills_inventory(root)
        bootstrap_plans = _preflight_upgrade_paths(
            root,
            previous_names=previous_names,
            current_names=current_names,
        )

        transactions = root / _UPGRADE_TRANSACTIONS
        _assert_safe_managed_path(root, transactions)
        transactions.mkdir(parents=True, exist_ok=True)
        transaction = Path(tempfile.mkdtemp(prefix="txn-", dir=transactions))
        journal_written = False
        try:
            staged_root = transaction / "staged"
            staged_canonical = staged_root / "canonical"
            staged_adapters = staged_root / "adapters"
            staged_bootstrap = staged_root / "bootstrap"
            for name in current_names:
                canonical_target = root / ".skills" / name
                shutil.copytree(
                    source / name,
                    staged_canonical / name,
                    symlinks=False,
                    ignore=_ignore_source_artifacts,
                )
                os.chmod(
                    staged_canonical / name,
                    (
                        stat.S_IMODE(canonical_target.lstat().st_mode)
                        if canonical_target.exists()
                        else 0o755
                    ),
                )
                for agent_index, (agent_relative, _label) in enumerate(
                    PROJECT_AGENT_DIRS
                ):
                    adapter = staged_adapters / str(agent_index) / name / "SKILL.md"
                    adapter_relative = (
                        PurePosixPath(agent_relative) / name / "SKILL.md"
                    )
                    canonical_relative = PurePosixPath(".skills") / name / "SKILL.md"
                    _stage_text_for_replacement(
                        root,
                        transaction,
                        adapter,
                        root / agent_relative / name / "SKILL.md",
                        _legacy_adapter_text(
                            name,
                            posixpath.relpath(
                                canonical_relative.as_posix(),
                                adapter_relative.parent.as_posix(),
                            ),
                        ),
                    )
                    adapter_target = root / agent_relative / name
                    os.chmod(
                        adapter.parent,
                        (
                            stat.S_IMODE(adapter_target.lstat().st_mode)
                            if adapter_target.exists()
                            else 0o755
                        ),
                    )

            bootstrap_staged: dict[Path, Path] = {}
            for index, (target, text) in enumerate(bootstrap_plans):
                staged = staged_bootstrap / str(index)
                _stage_text_for_replacement(
                    root, transaction, staged, target, text
                )
                bootstrap_staged[target] = staged

            replacements: list[tuple[Path, Path | None]] = []
            previous = set(previous_names)
            current = set(current_names)
            for name in sorted(previous | current):
                replacements.append(
                    (
                        root / ".skills" / name,
                        staged_canonical / name if name in current else None,
                    )
                )
                for agent_index, (agent_relative, _label) in enumerate(
                    PROJECT_AGENT_DIRS
                ):
                    replacements.append(
                        (
                            root / agent_relative / name,
                            staged_adapters / str(agent_index) / name
                            if name in current
                            else None,
                        )
                    )
            replacements.extend(
                (target, bootstrap_staged[target])
                for target, _text in bootstrap_plans
            )
            inventory = root / MANAGED_SKILLS_INVENTORY
            staged_inventory = staged_root / "inventory/managed-skills.json"
            _stage_text_for_replacement(
                root,
                transaction,
                staged_inventory,
                inventory,
                render_managed_skills_inventory(version, current_names),
            )
            replacements.append((inventory, staged_inventory))
            payload, records = _prepare_upgrade_journal(
                root, transaction, replacements
            )
            journal_written = True
        except BaseException:
            if not journal_written:
                _cleanup_upgrade_transaction(root, transaction)
            raise
        _apply_journaled_upgrade(root, transaction, payload, records)
        return current_names


class _PortableSetupRollbackError(OSError):
    """A staged setup failed and could not restore every moved entry."""


def _commit_staged_git_only_repo(root: Path, staging: Path) -> None:
    """Move a validated staged tree around an existing opaque ``.git`` tree."""
    if not _is_git_only_target(root):
        raise ValueError(
            "portable repository target changed before staged commit; expected only "
            f"an ordinary .git directory: {root}"
        )

    moved: list[tuple[Path, Path]] = []
    try:
        for staged_entry in sorted(staging.iterdir(), key=lambda path: path.name):
            target = root / staged_entry.name
            if target.exists() or target.is_symlink():
                raise FileExistsError(
                    f"portable staged setup collides with existing target: {target}"
                )
            staged_entry.replace(target)
            moved.append((target, staged_entry))
        staging.rmdir()
    except BaseException as original_error:
        rollback_errors: list[str] = []
        for target, staged_entry in reversed(moved):
            try:
                if staged_entry.exists() or staged_entry.is_symlink():
                    raise OSError(f"rollback destination already exists: {staged_entry}")
                if not target.exists() and not target.is_symlink():
                    raise OSError(f"moved target disappeared: {target}")
                target.replace(staged_entry)
            except BaseException as rollback_error:
                rollback_errors.append(f"{target}: {rollback_error}")
        if rollback_errors:
            raise _PortableSetupRollbackError(
                "portable setup rollback is incomplete; preserved staged evidence at "
                f"{staging}; original error: {original_error}; rollback errors: "
                + "; ".join(rollback_errors)
            ) from original_error
        raise


def _is_git_only_target(root: Path) -> bool:
    """Return whether *root* contains exactly one ordinary ``.git`` directory."""
    try:
        entries = tuple(root.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return False
    if len(entries) != 1 or entries[0].name != ".git":
        return False

    git_entry = entries[0]
    try:
        metadata = git_entry.lstat()
    except OSError as exc:
        raise ValueError(
            f"portable repository .git must be an ordinary directory: {git_entry}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(
            f"portable repository .git must be an ordinary directory: {git_entry}"
        )
    return True


def setup_portable_repo(
    root: Path,
    *,
    version: str,
    source_skills: Path,
) -> Path:
    """Scaffold a clone-ready portable repository and return its resolved root."""
    compatible_cli_spec(version)
    source = _absolute_no_resolve(Path(source_skills).expanduser())
    bundled_skills = _snapshot_bundled_skills(source)
    skill_names = bundled_skills.names
    requested = _absolute_no_resolve(Path(root).expanduser())
    if requested.is_symlink():
        raise ValueError(f"portable repository root must not be a symlink: {requested}")
    root = requested.resolve(strict=False)
    if root.exists() and not root.is_dir():
        raise ValueError(f"portable repository target must be a directory: {root}")

    target_existed = root.is_dir()
    target_is_empty = target_existed and not any(root.iterdir())
    target_is_git_only = (
        target_existed and not target_is_empty and _is_git_only_target(root)
    )
    if target_existed and not target_is_empty and not target_is_git_only:
        config_path = root / ".obsidian-wiki/config.toml"
        if not config_path.exists() and not config_path.is_symlink():
            raise ValueError(
                f"existing target is not a portable repository: {root}; accepted "
                "initial states are missing/empty or only an ordinary .git directory"
            )
        _assert_ordinary_file(root, config_path, "configuration")
        _assert_single_link_ordinary_file(root, config_path, "configuration")
        _load_canonical_portable_config(root, version=version)
        with _portable_skills_lock(root):
            _recover_upgrade_transactions(
                root,
                version=version,
                source=source,
                current_names=skill_names,
            )
            inventory_path = root / MANAGED_SKILLS_INVENTORY
            if inventory_path.exists() or inventory_path.is_symlink():
                inventory = read_inventory(root, allow_legacy=True)
                if isinstance(inventory, LegacyManagedSkillsInventory):
                    raise ValueError(
                        "portable repository uses legacy skill adapters; run "
                        "`obsidian-wiki repo upgrade-skills`"
                    )
                assert isinstance(inventory, ManagedSkillsInventory)
                canonical = discover_skill_collection(root / ".skills")
                _preflight_existing_portable(
                    root, version=version, skill_names=canonical.names
                )
                _validate_agent_skill_mirrors(root, canonical, remediation=True)
                _repair_existing_portable_repo(root)
            else:
                raise ValueError(
                    "portable repository has no managed skill inventory; run "
                    "`obsidian-wiki repo upgrade-skills`"
                )
        return root

    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.obsidian-wiki-", dir=root.parent)
    )
    removed_empty_target = False
    try:
        _populate_portable_repo(staging, version=version, bundled_skills=bundled_skills)
        _preflight_existing_portable(staging, version=version, skill_names=skill_names)
        if target_is_git_only:
            _commit_staged_git_only_repo(root, staging)
        else:
            if target_is_empty:
                root.rmdir()
                removed_empty_target = True
            staging.replace(root)
    except _PortableSetupRollbackError:
        raise
    except BaseException as original_error:
        cleanup_errors: list[str] = []
        if staging.exists() and staging.parent == root.parent:
            try:
                shutil.rmtree(staging)
            except BaseException as cleanup_error:
                cleanup_errors.append(f"staging {staging}: {cleanup_error}")
        if target_is_empty and removed_empty_target and not root.exists():
            try:
                root.mkdir()
            except BaseException as cleanup_error:
                cleanup_errors.append(f"empty target {root}: {cleanup_error}")
        if cleanup_errors:
            evidence = (
                f"; preserved staged evidence at {staging}"
                if staging.exists() or staging.is_symlink()
                else ""
            )
            raise OSError(
                "portable setup cleanup is incomplete"
                f"{evidence}; cleanup errors: {'; '.join(cleanup_errors)}"
            ) from original_error
        raise
    return root

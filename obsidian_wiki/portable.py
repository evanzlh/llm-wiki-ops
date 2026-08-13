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
import secrets
import shutil
import stat
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - repository upgrades are Unix-first
    fcntl = None  # type: ignore[assignment]

from packaging.version import InvalidVersion, Version

from obsidian_wiki import IMPLEMENTATION_ID, SOURCE_REINSTALL_COMMAND, __version__
from obsidian_wiki.config import PortableConfig, load_portable_config
from obsidian_wiki.safe_files import stable_directory_identity
from obsidian_wiki.skill_inventory import (
    MANAGED_SKILLS_INVENTORY,
    LegacyManagedSkillsInventory,
    ManagedSkillsInventory,
    parse_inventory_text,
    read_inventory,
    render_inventory,
)
from obsidian_wiki.skill_names import is_safe_skill_name
from obsidian_wiki.skill_trees import (
    SkillCollection,
    SkillEntry,
    discover_anchored_skill_collection,
    discover_skill_collection,
    materialize_skill_collection,
    snapshot_ordinary_tree_with_unsafe,
)
from obsidian_wiki.skill_trees import (
    _digest as _skill_tree_digest,
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
    ".obsidian",
)
UNSUPPORTED_PERSONAL_VAULT_PATHS = (
    "_archives",
    "_raw",
    "_readouts",
    "_staging",
)
PORTABLE_ROOT_IGNORE = (".obsidian-wiki/local/",)
_PORTABLE_SKILLS_LOCK = ".obsidian-wiki/local/portable-skills.lock"
_UPGRADE_TRANSACTIONS = ".obsidian-wiki/local/skill-upgrades"
_UPGRADE_JOURNAL = "journal.json"
_LEGACY_UPGRADE_JOURNAL_SCHEMA = 3
_REPLACEMENT_JOURNAL_SCHEMA = 4
_INVENTORY_KEYS = {"implementation", "skills", "skills_version"}
_LEGACY_SKILL_DIGEST_CATALOG = (
    Path(__file__).parent / "_data/legacy-skill-digests-v1.json"
)
_SKILL_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
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


@dataclass(frozen=True)
class ReplacementOperation:
    """Authorization boundary for one recoverable replacement transaction."""

    name: Literal["upgrade", "sync"]
    transactions_relative: str
    inventory_must_be_last: bool


UPGRADE_OPERATION = ReplacementOperation(
    "upgrade", _UPGRADE_TRANSACTIONS, True
)
SYNC_OPERATION = ReplacementOperation(
    "sync", ".obsidian-wiki/local/skill-syncs", False
)


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

_BUNDLED_BOOTSTRAP_DIR = Path(__file__).parent / "_data/bootstrap"
_BOOTSTRAP_ASSET_TARGETS = {
    "AGENTS.md": "AGENTS.md",
    ".agent/rules/obsidian-wiki.md": "agent/rules/obsidian-wiki.md",
    ".agent/workflows/obsidian-wiki.md": "agent/workflows/obsidian-wiki.md",
    ".cursor/rules/obsidian-wiki.mdc": "cursor/rules/obsidian-wiki.mdc",
    ".windsurf/rules/obsidian-wiki.md": "windsurf/rules/obsidian-wiki.md",
    ".kiro/steering/obsidian-wiki.md": "kiro/steering/obsidian-wiki.md",
    ".github/copilot-instructions.md": "github/copilot-instructions.md",
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


_ACTIVE_MUTATION_ROOTS: ContextVar[tuple[tuple[Path, int], ...]] = ContextVar(
    "portable_active_mutation_roots", default=()
)


@contextmanager
def _bound_portable_mutation_root(
    root: Path, expected_root_identity: tuple[int, ...]
) -> Iterator[Path]:
    """Keep the loaded repository inode open for an entire public mutation."""
    requested = _absolute_no_resolve(root)
    flags = _inventory_directory_flags()
    descriptor = -1
    token = None
    try:
        descriptor = os.open(requested, flags)
        opened = os.fstat(descriptor)
    except OSError as exc:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise ValueError(f"portable repository root is unsafe: {requested}: {exc}") from exc
    if stable_directory_identity(opened) != expected_root_identity:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise ValueError(
            "portable repository root changed since configuration was read"
        )
    body_error: BaseException | None = None
    try:
        token = _ACTIVE_MUTATION_ROOTS.set(
            (*_ACTIVE_MUTATION_ROOTS.get(), (requested, descriptor))
        )
        yield requested
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        if token is not None:
            _ACTIVE_MUTATION_ROOTS.reset(token)
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                if body_error is None:
                    raise


def _active_mutation_root_descriptor(root: Path) -> int | None:
    requested = _absolute_no_resolve(root)
    for bound_root, descriptor in reversed(_ACTIVE_MUTATION_ROOTS.get()):
        if bound_root == requested:
            return descriptor
    return None


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
        if not is_safe_skill_name(entry.name):
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
            or not is_safe_skill_name(name)
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


def _read_inventory_file(
    root: Path, path: Path
) -> ManagedSkillsInventory | LegacyManagedSkillsInventory:
    content = _read_single_link_ordinary_bytes(root, path, "managed skills inventory")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"portable managed-skills.json is not valid UTF-8: {path}"
        ) from exc
    try:
        return parse_inventory_text(text, allow_legacy=True)
    except ValueError as exc:
        raise ValueError(f"portable managed-skills.json is invalid: {exc}") from exc


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


def _reject_legacy_catalog_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"legacy skill digest catalog has duplicate key {key!r}")
        result[key] = value
    return result


def _load_legacy_skill_digest_catalog() -> tuple[Mapping[str, str], ...]:
    path = _LEGACY_SKILL_DIGEST_CATALOG
    try:
        content = _read_single_link_ordinary_bytes(
            path.parent.parent, path, "legacy skill digest catalog"
        )
        text = content.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_legacy_catalog_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"legacy skill digest catalog is invalid: {path}: {exc}") from exc
    if type(payload) is not dict or set(payload) != {"schema_version", "collections"}:
        raise ValueError("legacy skill digest catalog has an unknown schema")
    if payload["schema_version"] != 1 or type(payload["collections"]) is not list:
        raise ValueError("legacy skill digest catalog has an unknown schema")
    collections: list[Mapping[str, str]] = []
    labels: set[str] = set()
    for raw in payload["collections"]:
        if type(raw) is not dict or set(raw) != {"label", "skills"}:
            raise ValueError("legacy skill digest catalog collection is invalid")
        label = raw["label"]
        skills = raw["skills"]
        if (
            type(label) is not str
            or not label
            or label in labels
            or type(skills) is not dict
            or not skills
        ):
            raise ValueError("legacy skill digest catalog collection is invalid")
        labels.add(label)
        copied: dict[str, str] = {}
        for name, digest in skills.items():
            if (
                type(name) is not str
                or not is_safe_skill_name(name)
                or type(digest) is not str
                or _SKILL_DIGEST.fullmatch(digest) is None
            ):
                raise ValueError("legacy skill digest catalog collection is invalid")
            copied[name] = digest
        if tuple(copied) != tuple(sorted(copied)):
            raise ValueError("legacy skill digest catalog skills must be sorted")
        collections.append(MappingProxyType(copied))
    if not collections:
        raise ValueError("legacy skill digest catalog is empty")
    return tuple(collections)


def _validate_known_legacy_repository(
    root: Path,
    inventory: LegacyManagedSkillsInventory,
    canonical: SkillCollection,
) -> None:
    canonical_by_name = canonical.by_name()
    managed = inventory.managed_skills
    observed = {
        name: canonical_by_name[name].digest
        for name in managed
        if name in canonical_by_name
    }
    recognized = any(
        tuple(collection) == managed and dict(collection) == observed
        for collection in _load_legacy_skill_digest_catalog()
    )
    if not recognized:
        raise ValueError(
            "portable legacy canonical baseline is not recognized; preserve or "
            "reconcile owner changes before migrating (no force mode is available)"
        )

    custom_names = set(canonical.names) - set(managed)
    custom_presence = {name: 0 for name in custom_names}
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        agent_root = root / agent_relative
        mirror = discover_anchored_skill_collection(agent_root, anchor=root)
        mirror_by_name = mirror.by_name()
        unexpected = set(mirror_by_name) - set(managed) - custom_names
        if unexpected:
            raise ValueError(
                "portable legacy adapters contain an untracked owner skill: "
                f"{sorted(unexpected)[0]}"
            )
        for name in managed:
            tree = mirror_by_name.get(name)
            adapter_relative = PurePosixPath(agent_relative) / name / "SKILL.md"
            canonical_relative = PurePosixPath(".skills") / name / "SKILL.md"
            expected = _legacy_adapter_text(
                name,
                posixpath.relpath(
                    canonical_relative.as_posix(), adapter_relative.parent.as_posix()
                ),
            ).encode("utf-8")
            if tree is None or tree.entries != (
                SkillEntry("SKILL.md", "file", False, expected),
            ):
                raise ValueError(
                    "portable legacy migration requires exact adapters; "
                    f"preserve and reconcile {agent_relative}/{name}"
                )
        for name in set(mirror_by_name) - set(managed):
            if mirror_by_name[name] != canonical_by_name[name]:
                raise ValueError(
                    "portable legacy custom mirror differs from canonical owner data: "
                    f"{agent_relative}/{name}"
                )
            custom_presence[name] += 1
    for name, count in custom_presence.items():
        if count not in (0, len(PROJECT_AGENT_DIRS)):
            raise ValueError(
                "portable legacy custom mirror must be absent everywhere or an exact "
                f"full copy across all agents: {name}"
            )


def _validate_v2_upgrade_repository(
    root: Path,
    inventory: ManagedSkillsInventory,
    canonical: SkillCollection,
) -> None:
    _validate_v2_inventory_ownership(inventory, canonical)
    canonical_by_name = canonical.by_name()
    for name in inventory.managed_skills:
        tree = canonical_by_name[name]
        if tree.digest != inventory.managed_skill_digests[name]:
            raise ValueError(
                "portable managed canonical skill digest is modified; preserve or "
                f"reconcile .skills/{name} before upgrading"
            )
    report = plan_portable_skill_sync(root)
    if report.status != "clean":
        raise ValueError(
            "portable skill mirrors have drift; run `obsidian-wiki repo "
            "sync-skills --apply` and review the result before upgrading"
        )


def _validate_v2_inventory_ownership(
    inventory: ManagedSkillsInventory, canonical: SkillCollection
) -> None:
    canonical_names = set(canonical.names)
    for name in inventory.managed_skills:
        if name not in canonical_names:
            raise ValueError(f"portable managed canonical skill is missing: {name}")


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
            mirror = discover_anchored_skill_collection(target, anchor=root)
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
            mirror_snapshot, unsafe_findings = snapshot_ordinary_tree_with_unsafe(
                target, anchor=root
            )
        else:
            mirror_snapshot, unsafe_findings = (), ()
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
    unsafe = _minimal_changed_paths(finding.path for finding in unsafe_findings)
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
        canonical = discover_anchored_skill_collection(canonical_root, anchor=root)
        _assert_safe_managed_path(root, canonical_root)
    except (OSError, ValueError) as exc:
        message = str(exc).replace(str(root), ".")
        raise ValueError(f"portable canonical skills are invalid: {message}") from exc

    try:
        inventory = _read_inventory_file(root, root / MANAGED_SKILLS_INVENTORY)
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
    canonical = discover_anchored_skill_collection(canonical_root, anchor=root)
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


def _bootstrap_source(source_bootstrap: Path | None) -> Path:
    source = _absolute_no_resolve(source_bootstrap or _BUNDLED_BOOTSTRAP_DIR)
    try:
        kind = _source_entry_kind(source)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"bundled bootstrap directory not found: {source}") from exc
    if kind != "directory":
        raise ValueError(f"bundled bootstrap source must be an ordinary directory: {source}")
    return source


def _read_bootstrap_asset(source: Path, relative: str) -> str:
    parts = PurePosixPath(relative).parts
    parent = source
    for part in parts[:-1]:
        parent /= part
        if _source_entry_kind(parent) != "directory":
            raise ValueError(
                f"bundled bootstrap asset parent must be an ordinary directory: {parent}"
            )
    path = parent / parts[-1]
    if _source_entry_kind(path) != "file":
        raise ValueError(f"bundled bootstrap asset must be an ordinary file: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"bundled bootstrap asset is unreadable: {path}: {exc}") from exc


def _split_bootstrap_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    boundary = text.find("\n---\n", 4)
    if boundary < 0:
        raise ValueError("bundled bootstrap asset has unclosed frontmatter")
    boundary += len("\n---\n")
    return text[:boundary], text[boundary:].lstrip("\n")


def _owner_around_managed_bootstrap(existing: str) -> tuple[str, str] | None:
    start_count = existing.count(MANAGED_START)
    end_count = existing.count(MANAGED_END)
    if start_count != end_count or start_count > 1:
        raise ValueError("malformed obsidian-wiki managed markers")
    if start_count == 0:
        return None
    start = existing.index(MANAGED_START)
    end = existing.index(MANAGED_END, start) + len(MANAGED_END)
    before = existing[:start]
    _frontmatter, before = _split_bootstrap_frontmatter(before)
    return before.strip("\n"), existing[end:].strip("\n")


def _render_asset_bootstrap(asset: str, existing: str) -> str:
    frontmatter, body = _split_bootstrap_frontmatter(asset)
    if not existing or existing == asset:
        owner_before = owner_after = ""
    else:
        owners = _owner_around_managed_bootstrap(existing)
        if owners is None:
            return existing
        owner_before, owner_after = owners

    parts = []
    if frontmatter:
        parts.append(frontmatter.rstrip("\n"))
    if owner_before:
        parts.append(owner_before)
    parts.append(merge_managed_block("", body).rstrip("\n"))
    if owner_after:
        parts.append(owner_after)
    return "\n\n".join(parts) + "\n"


def _legacy_bootstrap_text(relative_agents: str) -> str:
    return "<!-- obsidian-wiki:portable-bootstrap -->\n" + _bootstrap_body(relative_agents)


def _planned_reference_bootstrap_text(existing: str, relative_agents: str) -> str | None:
    body = _bootstrap_body(relative_agents)
    if not existing:
        return merge_managed_block("", body)
    if MANAGED_START in existing or MANAGED_END in existing:
        return merge_managed_block(existing, body)
    if existing == _legacy_bootstrap_text(relative_agents):
        return merge_managed_block("", body)
    return None


def _render_bootstrap_target(
    root: Path,
    target: Path,
    existing: str,
    *,
    source_bootstrap: Path | None = None,
) -> str:
    """Render one fixed bootstrap target from its authoritative old content."""
    relative = _repo_relative_path(root, target)
    return render_portable_bootstrap(
        relative, existing, source_bootstrap=source_bootstrap
    )


def render_portable_bootstrap(
    relative: str, existing: str, *, source_bootstrap: Path | None = None
) -> str:
    """Render one managed bootstrap file while preserving owner-maintained text."""
    source = _bootstrap_source(source_bootstrap)
    if relative == "AGENTS.md":
        asset = _read_bootstrap_asset(source, _BOOTSTRAP_ASSET_TARGETS[relative])
        if not existing:
            existing = _TEAM_CONVENTIONS
        elif MANAGED_START not in existing and "## Team conventions" not in existing:
            existing = f"{_TEAM_CONVENTIONS}\n{existing}"
        return merge_managed_block(existing, asset)

    asset_relative = _BOOTSTRAP_ASSET_TARGETS.get(relative)
    if asset_relative is not None:
        return _render_asset_bootstrap(
            _read_bootstrap_asset(source, asset_relative), existing
        )

    try:
        agents_reference = _BOOTSTRAP_REFERENCES[relative]
    except KeyError as exc:  # pragma: no cover - callers use the fixed target set
        raise ValueError(f"unexpected portable bootstrap target: {relative}") from exc
    planned = _planned_reference_bootstrap_text(existing, agents_reference)
    return existing if planned is None else planned


def _portable_bootstrap_plans(
    root: Path, *, source_bootstrap: Path | None = None
) -> list[tuple[Path, str]]:
    agents_path = root / "AGENTS.md"
    _assert_safe_managed_path(root, agents_path)
    if agents_path.exists() and not agents_path.is_file():
        raise ValueError(f"portable AGENTS.md must be an ordinary file: {agents_path}")
    if agents_path.exists():
        _assert_single_link_ordinary_file(root, agents_path, "AGENTS.md")
    existing = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
    plans: list[tuple[Path, str]] = [
        (
            agents_path,
            _render_bootstrap_target(
                root, agents_path, existing, source_bootstrap=source_bootstrap
            ),
        )
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
        plans.append(
            (
                target,
                _render_bootstrap_target(
                    root, target, current, source_bootstrap=source_bootstrap
                ),
            )
        )
    return plans


def install_portable_bootstrap(
    root: Path, *, source_bootstrap: Path | None = None
) -> None:
    """Install dedicated portable agent discovery and bootstrap Markdown."""
    root = _safe_root(Path(root))
    plans = _portable_bootstrap_plans(root, source_bootstrap=source_bootstrap)

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
        f"{prefix}.obsidian/",
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
    root: Path,
    *,
    version: str,
    bundled_skills: SkillCollection,
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


def _open_portable_lock(root: Path) -> tuple[int, list[_BoundDirectory]]:
    path = root / _PORTABLE_SKILLS_LOCK
    directories = _open_sync_directory_chain(
        root, path.parent, create=True, label="skills lock parent"
    )
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        parent_fd = directories[-1][2]
        _validate_sync_directory_chain(directories, label="skills lock parent")
        descriptor = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError(
                f"portable skills lock must be a single-link ordinary file: {path}"
            )
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        attached = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (metadata.st_dev, metadata.st_ino) != (attached.st_dev, attached.st_ino):
            raise ValueError(f"portable skills lock changed during open: {path}")
        _validate_sync_directory_chain(directories, label="skills lock parent")
    except BaseException as exc:
        if descriptor >= 0:
            os.close(descriptor)
        _close_sync_directory_chain(directories)
        if isinstance(exc, OSError):
            raise ValueError(  # noqa: TRY004 - normalize unsafe lock failures
                f"cannot open portable skills lock {path}: {exc}"
            ) from exc
        raise
    return descriptor, directories


def _ensure_portable_lock_file(root: Path) -> Path:
    if not _sync_dirfd_supported():
        path = root / _PORTABLE_SKILLS_LOCK
        _assert_safe_managed_path(root, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _assert_safe_managed_path(root, path)
        handles: list[int] = []
        descriptor = -1
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                from obsidian_wiki.local_state import _windows_directory_guard

                handles = _windows_directory_guard(root, (path.parent,))
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
            metadata = os.fstat(descriptor)
            attached = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino)
                != (attached.st_dev, attached.st_ino)
            ):
                raise ValueError(
                    f"portable skills lock must be a single-link ordinary file: {path}"
                )
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:  # pragma: no cover - Windows has path-based chmod only
                os.chmod(path, 0o600)
            os.fsync(descriptor)
        except OSError as exc:
            raise ValueError(f"cannot open portable skills lock {path}: {exc}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if handles:
                from obsidian_wiki.local_state import _close_windows_handles

                _close_windows_handles(handles)
        return path
    descriptor, directories = _open_portable_lock(root)
    os.close(descriptor)
    _close_sync_directory_chain(directories)
    return root / _PORTABLE_SKILLS_LOCK


@contextmanager
def _portable_skills_lock(root: Path) -> Iterator[None]:
    if fcntl is None:  # pragma: no cover - Linux/macOS are the supported hosts
        raise RuntimeError("portable skill upgrades require fcntl.flock")
    descriptor, directories = _open_portable_lock(root)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                f"portable repository skills are locked by another upgrade: {root}"
            ) from exc
        _validate_sync_directory_chain(directories, label="skills lock parent")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            _close_sync_directory_chain(directories)


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
        and is_safe_skill_name(parts[1])
    ):
        return True
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        agent_parts = PurePosixPath(agent_relative).parts
        if parts == agent_parts:
            return True
        if (
            len(parts) == len(agent_parts) + 1
            and parts[: len(agent_parts)] == agent_parts
            and is_safe_skill_name(parts[-1])
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


def _write_replacement_journal(
    root: Path, transaction: Path, payload: dict[str, object]
) -> None:
    journal = transaction / _UPGRADE_JOURNAL
    text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    _write_sync_text(root, journal, text, mode=0o600)


def _journal_records(
    root: Path,
    transaction: Path,
    operation: ReplacementOperation,
    payload: object,
) -> tuple[str, list[tuple[Path, Path, Path | None, bool]], tuple[Path, ...]]:
    if not isinstance(payload, dict):
        raise ValueError(  # noqa: TRY004 - invalid persisted data
            f"invalid portable replacement journal: {transaction / _UPGRADE_JOURNAL}"
        )
    schema = payload.get("schema_version")
    legacy_upgrade = schema == _LEGACY_UPGRADE_JOURNAL_SCHEMA
    expected_keys = {
        "created_parents",
        "implementation",
        "replacements",
        "schema_version",
        "status",
    }
    if not legacy_upgrade:
        expected_keys.add("operation")
    if set(payload) != expected_keys:
        raise ValueError(
            f"invalid portable replacement journal: {transaction / _UPGRADE_JOURNAL}"
        )
    if legacy_upgrade:
        if operation != UPGRADE_OPERATION:
            raise ValueError("legacy schema-3 journals are authorized only for upgrades")
    elif (
        schema != _REPLACEMENT_JOURNAL_SCHEMA
        or payload.get("operation") != operation.name
    ):
        raise ValueError(
            f"invalid portable {operation.name} journal operation identity: {transaction}"
        )
    if payload["implementation"] != IMPLEMENTATION_ID:
        raise ValueError(
            f"invalid portable {operation.name} journal implementation identity: "
            f"{transaction}"
        )
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
        if operation == SYNC_OPERATION:
            target_authorized = isinstance(target_raw, str) and target_raw in {
                relative for relative, _label in PROJECT_AGENT_DIRS
            }
        else:
            target_authorized = isinstance(target_raw, str) and _journal_target_is_managed(
                target_raw
            )
        if not isinstance(target_raw, str) or not target_authorized:
            raise ValueError(
                f"unsafe portable {operation.name} journal target: {target_raw!r}"
            )
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
    if operation.inventory_must_be_last and (
        _repo_relative_path(root, records[-1][0]) != MANAGED_SKILLS_INVENTORY
    ):
        raise ValueError(f"portable upgrade journal inventory mapping must be last: {transaction}")
    created_parents = _validate_journal_created_parents(
        root, records, payload["created_parents"]
    )
    return str(status_value), records, created_parents


def _load_replacement_journal(
    root: Path,
    transaction: Path,
    operation: ReplacementOperation,
    *,
    transaction_identity: tuple[int, int] | None = None,
) -> tuple[
    dict[str, object],
    list[tuple[Path, Path, Path | None, bool]],
    tuple[Path, ...],
]:
    transactions = root / operation.transactions_relative
    _assert_safe_managed_path(root, transaction)
    if transaction.parent != transactions or not transaction.name.startswith("txn-"):
        raise ValueError(
            f"unsafe portable {operation.name} transaction path: {transaction}"
        )
    _assert_directory(root, transaction, f"{operation.name} transaction")
    _assert_managed_tree(root, transaction)
    journal = transaction / _UPGRADE_JOURNAL
    if transaction_identity is None:
        transaction_identity = _sync_directory_identity(
            root, transaction, label="transaction"
        )
    raw_journal = _read_bound_sync_journal(
        root, transaction, transaction_identity
    )
    try:
        payload = json.loads(raw_journal.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"portable upgrade journal is invalid: {journal}: {exc}") from exc
    status, records, created_parents = _journal_records(
        root, transaction, operation, payload
    )
    assert isinstance(payload, dict)
    payload["status"] = status
    return payload, records, created_parents


def _load_upgrade_journal(
    root: Path, transaction: Path
) -> tuple[
    dict[str, object],
    list[tuple[Path, Path, Path | None, bool]],
    tuple[Path, ...],
]:
    """Compatibility wrapper retained for schema-3 upgrade characterization."""
    return _load_replacement_journal(root, transaction, UPGRADE_OPERATION)


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


def _bound_replacement_snapshot(
    root: Path, path: Path, *, label: str
) -> tuple[SkillEntry, ...]:
    """Snapshot an ordinary tree while binding every scan to the repository."""
    entries, unsafe = snapshot_ordinary_tree_with_unsafe(path, anchor=root)
    if unsafe:
        findings = ", ".join(
            f"{finding.path} ({finding.reason})" for finding in unsafe
        )
        raise ValueError(
            f"portable sync {label} changed or is unsafe: {path}: {findings}"
        )
    return entries


def _bound_directory_mode(root: Path, path: Path) -> int:
    """Read one directory mode through an anchor-bound, no-follow descriptor walk."""
    if (
        not _SUPPORTS_BOUND_INVENTORY_DIRECTORIES
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        return 0o755
    root = _absolute_no_resolve(root)
    path = _absolute_no_resolve(path)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"portable sync mode path escapes repository: {path}") from exc

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptors: list[int] = []
    bindings: list[tuple[int, str, int, os.stat_result]] = []

    def same_directory(left: os.stat_result, right: os.stat_result) -> bool:
        return (
            stat.S_ISDIR(left.st_mode)
            and stat.S_ISDIR(right.st_mode)
            and left.st_dev == right.st_dev
            and left.st_ino == right.st_ino
        )

    try:
        root_observed = root.lstat()
        current = os.open(root, flags)
        descriptors.append(current)
        root_opened = os.fstat(current)
        if not same_directory(root_observed, root_opened):
            raise ValueError("portable sync repository root changed while opening")
        for part in relative.parts:
            observed = os.stat(part, dir_fd=current, follow_symlinks=False)
            child = os.open(part, flags, dir_fd=current)
            descriptors.append(child)
            opened = os.fstat(child)
            if not same_directory(observed, opened):
                raise ValueError(
                    f"portable sync directory changed while opening: {path}"
                )
            bindings.append((current, part, child, opened))
            current = child

        final = os.fstat(current)
        root_attached = root.lstat()
        if not same_directory(root_opened, root_attached):
            raise ValueError("portable sync repository root changed during mode scan")
        for parent, name, child, opened in bindings:
            attached = os.stat(name, dir_fd=parent, follow_symlinks=False)
            child_final = os.fstat(child)
            if not same_directory(opened, attached) or not same_directory(
                opened, child_final
            ):
                raise ValueError(
                    f"portable sync directory changed during mode scan: {path}"
                )
        return stat.S_IMODE(final.st_mode)
    except OSError as exc:
        raise ValueError(
            f"portable sync directory mode could not be read safely: {path}: {exc}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


ReplacementState = tuple[int, tuple[SkillEntry, ...]]
ReplacementProof = tuple[tuple[int, int], ReplacementState]


def _bound_replacement_proof(
    root: Path, path: Path, *, label: str
) -> ReplacementProof:
    """Read one file/tree while binding its parent and final inode to *root*."""
    directories = _open_sync_directory_chain(
        root, path.parent, create=False, label=f"{label} parent"
    )
    descriptor = -1
    try:
        parent_fd = directories[-1][2]
        observed = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = (observed.st_dev, observed.st_ino)
        mode = stat.S_IMODE(observed.st_mode)
        if stat.S_ISDIR(observed.st_mode) and not stat.S_ISLNK(observed.st_mode):
            entries = _bound_replacement_snapshot(root, path, label=label)
        elif stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1:
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(path.name, flags, dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != identity
            ):
                raise ValueError(
                    f"portable sync {label} file changed while opening: {path}"
                )
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            final = os.fstat(descriptor)
            stable_fields = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if any(
                getattr(opened, field) != getattr(final, field)
                for field in stable_fields
            ):
                raise ValueError(
                    f"portable sync {label} file changed during read: {path}"
                )
            entries = (
                SkillEntry("", "file", bool(mode & 0o111), b"".join(chunks)),
            )
        else:
            raise ValueError(
                f"portable sync {label} is not an ordinary single-link file or "
                f"directory: {path}"
            )
        attached = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (attached.st_dev, attached.st_ino) != identity or (
            stat.S_ISREG(observed.st_mode)
            and any(
                getattr(opened, field) != getattr(attached, field)
                for field in stable_fields
            )
        ):
            raise ValueError(f"portable sync {label} changed during read: {path}")
        _validate_sync_directory_chain(directories, label=f"{label} parent")
        return identity, (mode, entries)
    except OSError as exc:
        raise ValueError(
            f"portable sync {label} is missing, unsafe, or changed: {path}: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _close_sync_directory_chain(directories)


def _bound_replacement_state(
    root: Path, path: Path, *, label: str
) -> ReplacementState:
    return _bound_replacement_proof(root, path, label=label)[1]


def _bound_optional_replacement_proof(
    root: Path, path: Path, *, label: str
) -> ReplacementProof | None:
    """Return a bound proof, or ``None`` only for an absent final entry."""
    root = _absolute_no_resolve(root)
    path = _absolute_no_resolve(path)
    try:
        relative_parent = path.parent.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"portable sync {label} escapes repository: {path}") from exc
    directories: list[_BoundDirectory] = []
    try:
        flags = _inventory_directory_flags()
        root_fd = os.open(root, flags)
        root_metadata = os.fstat(root_fd)
        directories.append((root, "", root_fd, root_metadata))
        current = root
        for part in relative_parent.parts:
            parent_fd = directories[-1][2]
            try:
                child = os.open(part, flags, dir_fd=parent_fd)
            except FileNotFoundError:
                _validate_sync_directory_chain(
                    directories, label=f"{label} existing parent"
                )
                return None
            child_metadata = os.fstat(child)
            current /= part
            directories.append((current, part, child, child_metadata))
        _validate_sync_directory_chain(directories, label=f"{label} parent")
        try:
            os.stat(path.name, dir_fd=directories[-1][2], follow_symlinks=False)
        except FileNotFoundError:
            _validate_sync_directory_chain(directories, label=f"{label} parent")
            return None
    except OSError as exc:
        raise ValueError(
            f"portable sync {label} parent is unsafe or changed: {path.parent}: {exc}"
        ) from exc
    finally:
        _close_sync_directory_chain(directories)
    return _bound_replacement_proof(root, path, label=label)


_SYNC_DIRFD_SUPPORTED = (
    os.name != "nt"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.rename in os.supports_dir_fd
    and os.rmdir in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


def _sync_dirfd_supported() -> bool:
    return _SYNC_DIRFD_SUPPORTED


def _validate_sync_directory_chain(
    directories: list[_BoundDirectory], *, label: str
) -> None:
    for index, (path, name, descriptor, opened) in enumerate(directories):
        current = os.fstat(descriptor)
        if index == 0:
            attached = path.lstat()
        else:
            attached = os.stat(
                name,
                dir_fd=directories[index - 1][2],
                follow_symlinks=False,
            )
        if not _directory_is_bound(opened, current) or not _directory_is_bound(
            current, attached
        ):
            raise ValueError(
                f"portable sync {label} directory changed or detached: {path}"
            )


def _close_sync_directory_chain(directories: list[_BoundDirectory]) -> None:
    failure = _close_inventory_directories(directories, None)
    if failure is not None:
        raise failure


def _open_sync_directory_chain(
    root: Path, directory: Path, *, create: bool, label: str
) -> list[_BoundDirectory]:
    if not _sync_dirfd_supported():
        raise RuntimeError(
            "portable sync apply requires descriptor-relative filesystem operations"
        )
    root = _absolute_no_resolve(root)
    directory = _absolute_no_resolve(directory)
    try:
        relative = directory.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"portable sync {label} escapes repository: {directory}") from exc
    flags = _inventory_directory_flags()
    directories: list[_BoundDirectory] = []
    try:
        active_root = _active_mutation_root_descriptor(root)
        descriptor = (
            os.dup(active_root) if active_root is not None else os.open(root, flags)
        )
        metadata = os.fstat(descriptor)
        directories.append((root, "", descriptor, metadata))
        current = root
        for part in relative.parts:
            parent_fd = directories[-1][2]
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=parent_fd)
            metadata = os.fstat(child)
            current /= part
            directories.append((current, part, child, metadata))
        _validate_sync_directory_chain(directories, label=label)
        return directories
    except BaseException as exc:
        _close_inventory_directories(directories, None)
        if isinstance(exc, OSError):
            raise ValueError(  # noqa: TRY004 - normalize unsafe path failures
                f"portable sync {label} directory is unsafe or changed: {directory}: "
                f"{exc}"
            ) from exc
        raise


def _ensure_sync_directory(
    root: Path, directory: Path, *, label: str
) -> tuple[int, int]:
    directories = _open_sync_directory_chain(
        root, directory, create=True, label=label
    )
    try:
        _validate_sync_directory_chain(directories, label=label)
        return _sync_chain_identity(directories, directory, label=label)
    finally:
        _close_sync_directory_chain(directories)


def _sync_chain_identity(
    directories: list[_BoundDirectory], path: Path, *, label: str
) -> tuple[int, int]:
    for opened_path, _name, descriptor, _opened in directories:
        if opened_path == path:
            metadata = os.fstat(descriptor)
            return metadata.st_dev, metadata.st_ino
    raise ValueError(f"portable sync {label} is outside the bound directory chain: {path}")


def _validate_sync_chain_binding(
    directories: list[_BoundDirectory],
    binding: tuple[Path, tuple[int, int]] | None,
    *,
    label: str,
) -> None:
    if binding is None:
        return
    path, expected = binding
    if _sync_chain_identity(directories, path, label=label) != expected:
        raise ValueError(f"portable sync {label} transaction identity changed: {path}")


def _sync_directory_identity(root: Path, path: Path, *, label: str) -> tuple[int, int]:
    directories = _open_sync_directory_chain(
        root, path, create=False, label=label
    )
    try:
        _validate_sync_directory_chain(directories, label=label)
        return _sync_chain_identity(directories, path, label=label)
    finally:
        _close_sync_directory_chain(directories)


def _assert_sync_directory_identity(
    root: Path, path: Path, expected: tuple[int, int], *, label: str
) -> None:
    if _sync_directory_identity(root, path, label=label) != expected:
        raise ValueError(f"portable sync {label} transaction identity changed: {path}")


def _assert_sync_transaction_binding(
    root: Path,
    binding: tuple[Path, tuple[int, int]] | None,
    *,
    label: str,
) -> None:
    if binding is None:
        return
    transaction, identity = binding
    _assert_sync_directory_identity(
        root, transaction, identity, label=label
    )


def _read_bound_sync_journal(
    root: Path, transaction: Path, transaction_identity: tuple[int, int]
) -> bytes:
    directories = _open_sync_directory_chain(
        root, transaction, create=False, label="journal transaction"
    )
    descriptor = -1
    try:
        binding = (transaction, transaction_identity)
        _validate_sync_chain_binding(
            directories, binding, label="journal transaction"
        )
        transaction_fd = directories[-1][2]
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(_UPGRADE_JOURNAL, flags, dir_fd=transaction_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError(
                "portable sync journal must be a single-link ordinary file"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        attached = os.stat(
            _UPGRADE_JOURNAL,
            dir_fd=transaction_fd,
            follow_symlinks=False,
        )
        if (metadata.st_dev, metadata.st_ino) != (attached.st_dev, attached.st_ino):
            raise ValueError("portable sync journal changed during read")
        _validate_sync_directory_chain(directories, label="journal transaction")
        _validate_sync_chain_binding(
            directories, binding, label="journal transaction"
        )
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError(f"portable sync journal is missing, unsafe, or changed: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _close_sync_directory_chain(directories)


def _bound_sync_transaction_candidates(
    root: Path, transactions: Path
) -> tuple[tuple[Path, tuple[int, int]], ...]:
    directories = _open_sync_directory_chain(
        root, transactions, create=False, label="transactions"
    )
    try:
        parent_fd = directories[-1][2]
        names = sorted(os.listdir(parent_fd))
        candidates: list[tuple[Path, tuple[int, int]]] = []
        for name in names:
            if not name.startswith("txn-") or "/" in name or name in (".", ".."):
                raise ValueError(f"unsafe portable sync transaction name: {name!r}")
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ValueError(f"unsafe portable sync transaction path: {transactions / name}")
            candidates.append(
                (transactions / name, (metadata.st_dev, metadata.st_ino))
            )
        _validate_sync_directory_chain(directories, label="transactions")
        return tuple(candidates)
    finally:
        _close_sync_directory_chain(directories)


def _bound_sync_transaction_has_journal(
    root: Path, transaction: Path, transaction_identity: tuple[int, int]
) -> bool:
    directories = _open_sync_directory_chain(
        root, transaction, create=False, label="transaction journal check"
    )
    try:
        binding = (transaction, transaction_identity)
        _validate_sync_chain_binding(
            directories, binding, label="transaction journal check"
        )
        try:
            os.stat(
                _UPGRADE_JOURNAL,
                dir_fd=directories[-1][2],
                follow_symlinks=False,
            )
        except FileNotFoundError:
            result = False
        else:
            result = True
        _validate_sync_directory_chain(
            directories, label="transaction journal check"
        )
        _validate_sync_chain_binding(
            directories, binding, label="transaction journal check"
        )
        return result
    finally:
        _close_sync_directory_chain(directories)


def _materialize_sync_snapshot(
    root: Path,
    target: Path,
    *,
    mode: int,
    entries: tuple[SkillEntry, ...],
    parent_directories: list[_BoundDirectory] | None = None,
) -> None:
    owns_directories = parent_directories is None
    directories = (
        _open_sync_directory_chain(
            root, target.parent, create=True, label="materialization parent"
        )
        if parent_directories is None
        else parent_directories
    )
    opened_directories: dict[tuple[str, ...], int] = {}
    root_fd = -1
    try:
        parent_fd = directories[-1][2]
        _validate_sync_directory_chain(directories, label="materialization parent")
        os.mkdir(target.name, mode=mode, dir_fd=parent_fd)
        root_fd = os.open(target.name, _inventory_directory_flags(), dir_fd=parent_fd)
        os.fchmod(root_fd, mode)
        opened_directories[()] = root_fd
        for entry in entries:
            relative = PurePosixPath(entry.path)
            if relative.is_absolute() or any(
                part in ("", ".", "..") for part in relative.parts
            ):
                raise ValueError(
                    f"portable sync snapshot contains an unsafe path: {entry.path}"
                )
            parent_parts = relative.parts[:-1]
            entry_parent = opened_directories.get(parent_parts)
            if entry_parent is None:
                raise ValueError(
                    f"portable sync snapshot parent is missing: {entry.path}"
                )
            name = relative.parts[-1]
            if entry.kind == "directory":
                os.mkdir(name, mode=0o755, dir_fd=entry_parent)
                child = os.open(name, _inventory_directory_flags(), dir_fd=entry_parent)
                os.fchmod(child, 0o755)
                opened_directories[relative.parts] = child
            else:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                flags |= getattr(os, "O_CLOEXEC", 0)
                descriptor = os.open(name, flags, 0o600, dir_fd=entry_parent)
                try:
                    view = memoryview(entry.content)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("short portable sync snapshot write")
                        view = view[written:]
                    os.fchmod(descriptor, 0o755 if entry.executable else 0o644)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        for descriptor in opened_directories.values():
            os.fsync(descriptor)
        attached = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(root_fd)
        if not _directory_is_bound(opened, attached):
            raise ValueError("portable sync materialized root changed during write")
        _validate_sync_directory_chain(directories, label="materialization parent")
    finally:
        for descriptor in reversed(tuple(opened_directories.values())):
            os.close(descriptor)
        if owns_directories:
            _close_sync_directory_chain(directories)


def _materialize_bound_replacement_state(
    root: Path,
    target: Path,
    state: ReplacementState,
    *,
    parent_directories: list[_BoundDirectory] | None = None,
) -> None:
    mode, entries = state
    if len(entries) != 1 or entries[0].path != "" or entries[0].kind != "file":
        _materialize_sync_snapshot(
            root,
            target,
            mode=mode,
            entries=entries,
            parent_directories=parent_directories,
        )
        return

    owns_directories = parent_directories is None
    directories = (
        _open_sync_directory_chain(
            root, target.parent, create=True, label="file materialization parent"
        )
        if parent_directories is None
        else parent_directories
    )
    descriptor = -1
    try:
        parent_fd = directories[-1][2]
        _validate_sync_directory_chain(
            directories, label="file materialization parent"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(target.name, flags, 0o600, dir_fd=parent_fd)
        installed = os.fstat(descriptor)
        view = memoryview(entries[0].content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short portable replacement file write")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        attached = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        if (installed.st_dev, installed.st_ino) != (
            attached.st_dev,
            attached.st_ino,
        ):
            raise ValueError("portable replacement file changed during write")
        _validate_sync_directory_chain(
            directories, label="file materialization parent"
        )
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if owns_directories:
            _close_sync_directory_chain(directories)


def _rename_sync_path(
    root: Path,
    source: Path,
    target: Path,
    *,
    transaction_binding: tuple[Path, tuple[int, int]] | None = None,
    expected_source_identity: tuple[int, int] | None = None,
    expected_source_parent_identity: tuple[int, int] | None = None,
    expected_target_parent_identity: tuple[int, int] | None = None,
) -> None:
    source_chain = _open_sync_directory_chain(
        root, source.parent, create=False, label="rename source"
    )
    target_chain: list[_BoundDirectory] = []
    try:
        target_chain = _open_sync_directory_chain(
            root, target.parent, create=True, label="rename target"
        )
        _validate_sync_directory_chain(source_chain, label="rename source")
        _validate_sync_directory_chain(target_chain, label="rename target")
        if (
            expected_source_parent_identity is not None
            and _sync_chain_identity(
                source_chain, source.parent, label="rename source parent"
            )
            != expected_source_parent_identity
        ):
            raise ValueError(
                f"portable sync rename source parent identity changed: {source.parent}"
            )
        if (
            expected_target_parent_identity is not None
            and _sync_chain_identity(
                target_chain, target.parent, label="rename target parent"
            )
            != expected_target_parent_identity
        ):
            raise ValueError(
                f"portable sync rename target parent identity changed: {target.parent}"
            )
        if transaction_binding is not None:
            transaction, _identity = transaction_binding
            if source.parent == transaction or source.parent.is_relative_to(transaction):
                _validate_sync_chain_binding(
                    source_chain, transaction_binding, label="rename source"
                )
            if target.parent == transaction or target.parent.is_relative_to(transaction):
                _validate_sync_chain_binding(
                    target_chain, transaction_binding, label="rename target"
                )
        source_parent = source_chain[-1][2]
        target_parent = target_chain[-1][2]
        observed = os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)
        source_is_directory = stat.S_ISDIR(observed.st_mode) and not stat.S_ISLNK(
            observed.st_mode
        )
        source_is_file = stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1
        if not source_is_directory and not source_is_file:
            raise ValueError(
                f"portable sync rename source is not an ordinary entry: {source}"
            )
        if expected_source_identity is not None and (
            observed.st_dev,
            observed.st_ino,
        ) != expected_source_identity:
            raise ValueError(f"portable sync rename source identity changed: {source}")
        try:
            os.stat(target.name, dir_fd=target_parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(f"portable sync rename target exists: {target}")
        if source_is_directory:
            os.mkdir(target.name, mode=0o700, dir_fd=target_parent)
        else:
            reservation_fd = os.open(
                target.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=target_parent,
            )
            os.close(reservation_fd)
        reservation = os.stat(
            target.name, dir_fd=target_parent, follow_symlinks=False
        )
        try:
            os.rename(
                source.name,
                target.name,
                src_dir_fd=source_parent,
                dst_dir_fd=target_parent,
            )
        except BaseException:
            try:
                attached = os.stat(
                    target.name, dir_fd=target_parent, follow_symlinks=False
                )
                if (reservation.st_dev, reservation.st_ino) == (
                    attached.st_dev,
                    attached.st_ino,
                ):
                    if source_is_directory:
                        os.rmdir(target.name, dir_fd=target_parent)
                    else:
                        os.unlink(target.name, dir_fd=target_parent)
            except (FileNotFoundError, OSError):
                pass
            raise
        moved = os.stat(target.name, dir_fd=target_parent, follow_symlinks=False)
        if (observed.st_dev, observed.st_ino) != (moved.st_dev, moved.st_ino):
            raise ValueError("portable sync renamed entry changed identity")
        _validate_sync_directory_chain(source_chain, label="rename source")
        _validate_sync_directory_chain(target_chain, label="rename target")
        if transaction_binding is not None:
            transaction, _identity = transaction_binding
            if source.parent == transaction or source.parent.is_relative_to(transaction):
                _validate_sync_chain_binding(
                    source_chain, transaction_binding, label="rename source"
                )
            if target.parent == transaction or target.parent.is_relative_to(transaction):
                _validate_sync_chain_binding(
                    target_chain, transaction_binding, label="rename target"
                )
        os.fsync(source_parent)
        os.fsync(target_parent)
    finally:
        if target_chain:
            _close_sync_directory_chain(target_chain)
        _close_sync_directory_chain(source_chain)


def _purge_bound_sync_directory(descriptor: int) -> None:
    for name in os.listdir(descriptor):
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            child = os.open(name, _inventory_directory_flags(), dir_fd=descriptor)
            try:
                _purge_bound_sync_directory(child)
                current = os.fstat(child)
                if (metadata.st_dev, metadata.st_ino) != (
                    current.st_dev,
                    current.st_ino,
                ):
                    raise ValueError(
                        "portable sync cleanup child identity changed"
                    )
            finally:
                os.close(child)
            attached = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (metadata.st_dev, metadata.st_ino) != (
                attached.st_dev,
                attached.st_ino,
            ):
                raise ValueError("portable sync cleanup child was replaced")
            os.rmdir(name, dir_fd=descriptor)
        elif stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            os.unlink(name, dir_fd=descriptor)
        else:
            raise ValueError("portable sync cleanup contains an unsafe entry")
    os.fsync(descriptor)


def _remove_sync_path(
    root: Path,
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
    expected_parent_identity: tuple[int, int] | None = None,
    transaction_binding: tuple[Path, tuple[int, int]] | None = None,
) -> None:
    directories = _open_sync_directory_chain(
        root, path.parent, create=False, label="removal parent"
    )
    authority_directories: list[_BoundDirectory] = []
    try:
        parent_fd = directories[-1][2]
        _validate_sync_directory_chain(directories, label="removal parent")
        if (
            expected_parent_identity is not None
            and _sync_chain_identity(
                directories, path.parent, label="removal parent"
            )
            != expected_parent_identity
        ):
            raise ValueError(
                f"portable sync removal parent identity changed: {path.parent}"
            )
        if transaction_binding is not None:
            transaction, _identity = transaction_binding
            if path.parent == transaction or path.parent.is_relative_to(transaction):
                _validate_sync_chain_binding(
                    directories, transaction_binding, label="removal parent"
                )
            else:
                authority_directories = _open_sync_directory_chain(
                    root,
                    transaction,
                    create=False,
                    label="removal authority",
                )
                _validate_sync_chain_binding(
                    authority_directories,
                    transaction_binding,
                    label="removal authority",
                )
        try:
            metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if expected_identity is not None and (
            metadata.st_dev,
            metadata.st_ino,
        ) != expected_identity:
            raise ValueError(
                f"portable sync removal transaction identity changed: {path}"
            )
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"portable sync removal target is a symlink: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            if expected_identity is not None:
                target_fd = os.open(
                    path.name, _inventory_directory_flags(), dir_fd=parent_fd
                )
                try:
                    opened = os.fstat(target_fd)
                    if (opened.st_dev, opened.st_ino) != expected_identity:
                        raise ValueError(
                            f"portable sync removal transaction identity changed: {path}"
                        )
                    _purge_bound_sync_directory(target_fd)
                finally:
                    os.close(target_fd)
                attached = os.stat(
                    path.name, dir_fd=parent_fd, follow_symlinks=False
                )
                if (attached.st_dev, attached.st_ino) != expected_identity:
                    raise ValueError(
                        f"portable sync removal transaction detached: {path}"
                    )
                os.rmdir(path.name, dir_fd=parent_fd)
            else:
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise RuntimeError("safe descriptor-relative rmtree is unavailable")
                shutil.rmtree(path.name, dir_fd=parent_fd)
        elif stat.S_ISREG(metadata.st_mode):
            os.unlink(path.name, dir_fd=parent_fd)
        else:
            raise ValueError(f"portable sync removal target is unsafe: {path}")
        if authority_directories:
            _validate_sync_directory_chain(
                authority_directories, label="removal authority"
            )
            _validate_sync_chain_binding(
                authority_directories,
                transaction_binding,
                label="removal authority",
            )
        _validate_sync_directory_chain(directories, label="removal parent")
        if transaction_binding is not None:
            transaction, _identity = transaction_binding
            if path.parent == transaction or path.parent.is_relative_to(transaction):
                _validate_sync_chain_binding(
                    directories, transaction_binding, label="removal parent"
                )
        os.fsync(parent_fd)
    finally:
        if authority_directories:
            _close_sync_directory_chain(authority_directories)
        _close_sync_directory_chain(directories)


def _rmdir_sync_path(
    root: Path,
    path: Path,
    *,
    expected_parent_identity: tuple[int, int] | None = None,
    transaction_binding: tuple[Path, tuple[int, int]] | None = None,
) -> None:
    directories = _open_sync_directory_chain(
        root, path.parent, create=False, label="directory removal parent"
    )
    authority_directories: list[_BoundDirectory] = []
    try:
        parent_fd = directories[-1][2]
        if (
            expected_parent_identity is not None
            and _sync_chain_identity(
                directories, path.parent, label="directory removal parent"
            )
            != expected_parent_identity
        ):
            raise ValueError(
                "portable sync directory removal parent identity changed: "
                f"{path.parent}"
            )
        if transaction_binding is not None:
            transaction, _identity = transaction_binding
            authority_directories = _open_sync_directory_chain(
                root,
                transaction,
                create=False,
                label="directory removal authority",
            )
            _validate_sync_chain_binding(
                authority_directories,
                transaction_binding,
                label="directory removal authority",
            )
        _validate_sync_directory_chain(
            directories, label="directory removal parent"
        )
        try:
            os.rmdir(path.name, dir_fd=parent_fd)
        except FileNotFoundError:
            return
        _validate_sync_directory_chain(
            directories, label="directory removal parent"
        )
        if authority_directories:
            _validate_sync_directory_chain(
                authority_directories, label="directory removal authority"
            )
            _validate_sync_chain_binding(
                authority_directories,
                transaction_binding,
                label="directory removal authority",
            )
        os.fsync(parent_fd)
    finally:
        if authority_directories:
            _close_sync_directory_chain(authority_directories)
        _close_sync_directory_chain(directories)


def _write_sync_text(root: Path, path: Path, text: str, *, mode: int) -> None:
    directories = _open_sync_directory_chain(
        root, path.parent, create=True, label="journal parent"
    )
    temporary = f".{path.name}.sync-{secrets.token_hex(16)}"
    descriptor = -1
    try:
        parent_fd = directories[-1][2]
        _validate_sync_directory_chain(directories, label="journal parent")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        installed = os.fstat(descriptor)
        data = text.encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short portable sync journal write")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        attached = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (installed.st_dev, installed.st_ino) != (
            attached.st_dev,
            attached.st_ino,
        ):
            raise ValueError("portable sync journal changed during replacement")
        _validate_sync_directory_chain(directories, label="journal parent")
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directories[-1][2])
        except FileNotFoundError:
            pass
        _close_sync_directory_chain(directories)


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


def _copy_staged_replacement(
    root: Path, staged: Path, target: Path, *, bound: bool = False
) -> None:
    """Install a new target while retaining staged content as crash proof."""
    target_parent_directories: list[_BoundDirectory] | None = None
    try:
        if bound:
            target_parent_directories = _open_sync_directory_chain(
                root,
                target.parent,
                create=True,
                label="install materialization parent",
            )
            expected_state = _bound_replacement_state(
                root, staged, label="staged copy source"
            )
            root_mode, expected = expected_state
        else:
            root_mode = 0o755
            expected = _replacement_snapshot(root, staged)
        if bound:
            assert target_parent_directories is not None
            _materialize_bound_replacement_state(
                root,
                target,
                expected_state,
                parent_directories=target_parent_directories,
            )
        elif staged.is_dir():
            shutil.copytree(staged, target, symlinks=True, copy_function=shutil.copy2)
        elif staged.is_file() and not staged.is_symlink():
            shutil.copy2(staged, target, follow_symlinks=False)
        else:  # pragma: no cover - snapshot classification rejects this first
            raise ValueError(f"portable upgrade staged replacement is invalid: {staged}")
    finally:
        if target_parent_directories is not None:
            _close_sync_directory_chain(target_parent_directories)
    actual = (
        _bound_replacement_state(root, target, label="install copy postimage")
        if bound
        else _replacement_snapshot(root, target)
    )
    expected_state = (root_mode, expected) if bound else expected
    if actual != expected_state:
        raise OSError(
            f"portable upgrade could not verify copied replacement: {target}"
        )


def _collection_replacement_entries(
    collection: SkillCollection,
) -> tuple[SkillEntry, ...]:
    return _canonical_skill_entries(collection)


def _entries_replacement_snapshot(
    entries: tuple[SkillEntry, ...], *, root_mode: int
) -> tuple[tuple[str, str, int, bytes], ...]:
    return (
        ("", "directory", root_mode, b""),
        *tuple(
            (
                entry.path,
                entry.kind,
                0o755
                if entry.kind == "directory" or entry.executable
                else 0o644,
                entry.content,
            )
            for entry in entries
        ),
    )


def _authorize_full_upgrade_recovery(
    root: Path,
    transaction: Path,
    records: list[tuple[Path, Path, Path | None, bool]],
    *,
    rollback: bool,
    version: str,
    source: Path,
    current_names: tuple[str, ...],
    old_inventory: ManagedSkillsInventory | LegacyManagedSkillsInventory,
) -> set[Path]:
    source_collection = _snapshot_bundled_skills(source)
    if source_collection.names != current_names:
        raise ValueError("portable upgrade recovery bundled skill names changed")
    old_names = old_inventory.managed_skills
    old_set = set(old_names)
    current_set = set(current_names)
    record_by_target = {
        target: (index, backup, staged, had_target)
        for index, (target, backup, staged, had_target) in enumerate(records)
    }

    expected_targets: list[Path] = [
        root / ".skills" / name for name in sorted(old_set | current_set)
    ]
    expected_targets.extend(root / relative for relative, _label in PROJECT_AGENT_DIRS)
    fixed_bootstraps = (root / "AGENTS.md",) + tuple(
        root / relative for relative in _BOOTSTRAP_REFERENCES
    )
    expected_targets.extend(fixed_bootstraps)
    expected_targets.append(root / MANAGED_SKILLS_INVENTORY)
    if [record[0] for record in records] != expected_targets:
        raise ValueError(
            "portable full-mirror upgrade recovery record plan is incomplete or unexpected"
        )

    def original_for(target: Path) -> tuple[int, Path, Path | None, bool]:
        index, backup, staged, had_target = record_by_target[target]
        original = backup if backup.exists() else target
        if had_target and (not original.exists() or original.is_symlink()):
            raise ValueError(
                f"portable upgrade recovery original target is missing or unsafe: {target}"
            )
        if not had_target and (backup.exists() or backup.is_symlink()):
            raise ValueError(
                f"portable upgrade recovery has an unexpected backup: {target}"
            )
        return index, original, staged, had_target

    old_managed_entries: dict[str, tuple[SkillEntry, ...]] = {}
    observed_old_digests: dict[str, str] = {}
    for name in old_names:
        target = root / ".skills" / name
        _index, original, _staged, had_target = original_for(target)
        if not had_target:
            raise ValueError(
                f"portable upgrade recovery lost managed ownership for {target}"
            )
        entries = _bound_replacement_snapshot(
            root, original, label="old managed canonical authority"
        )
        old_managed_entries[name] = entries
        observed_old_digests[name] = _skill_tree_digest(name, entries)
    if isinstance(old_inventory, ManagedSkillsInventory):
        if observed_old_digests != dict(old_inventory.managed_skill_digests):
            raise ValueError(
                "portable upgrade recovery managed canonical digest differs from inventory"
            )
    elif not any(
        tuple(collection) == old_names
        and dict(collection) == observed_old_digests
        for collection in _load_legacy_skill_digest_catalog()
    ):
        raise ValueError(
            "portable legacy canonical baseline is not recognized during recovery"
        )

    live_canonical = discover_anchored_skill_collection(root / ".skills", anchor=root)
    custom = tuple(
        tree
        for tree in live_canonical.skills
        if tree.name not in old_set | current_set
    )
    collisions = current_set & {tree.name for tree in custom}
    if collisions:
        raise ValueError(
            "portable upgrade recovery bundled skill collides with custom canonical: "
            f"{sorted(collisions)[0]}"
        )
    prospective = SkillCollection(
        tuple(sorted((*source_collection.skills, *custom), key=lambda tree: tree.name))
    )
    prospective_entries = _collection_replacement_entries(prospective)

    old_full_entries: list[SkillEntry] = []
    for name in old_names:
        old_full_entries.append(SkillEntry(name, "directory", False, b""))
        old_full_entries.extend(
            SkillEntry(
                f"{name}/{entry.path}",
                entry.kind,
                entry.executable,
                entry.content,
            )
            for entry in old_managed_entries[name]
        )
    for tree in custom:
        old_full_entries.append(SkillEntry(tree.name, "directory", False, b""))
        old_full_entries.extend(
            SkillEntry(
                f"{tree.name}/{entry.path}",
                entry.kind,
                entry.executable,
                entry.content,
            )
            for entry in tree.entries
        )
    old_full_entries_tuple = tuple(
        sorted(old_full_entries, key=lambda entry: entry.path)
    )

    def validate_proof(
        index: int,
        staged: Path,
        expected: tuple[tuple[str, str, int, bytes], ...],
        label: str,
    ) -> None:
        if _replacement_snapshot(root, staged) != expected:
            raise ValueError(
                f"portable full-mirror upgrade recovery {label} proof differs"
            )
        install = transaction / "install" / str(index)
        if (install.exists() or install.is_symlink()) and _replacement_snapshot(
            root, install
        ) != expected:
            raise ValueError(
                f"portable full-mirror upgrade recovery {label} install differs"
            )

    for name in sorted(old_set | current_set):
        target = root / ".skills" / name
        index, original, staged, had_target = original_for(target)
        expected_staged = (
            transaction / "staged/canonical" / name if name in current_set else None
        )
        if staged != expected_staged or had_target != (name in old_set):
            raise ValueError(
                f"portable full-mirror canonical recovery layout is invalid: {target}"
            )
        if staged is not None:
            root_mode = stat.S_IMODE(original.lstat().st_mode) if had_target else 0o755
            validate_proof(
                index,
                staged,
                _source_replacement_snapshot(source / name, root_mode=root_mode),
                f"canonical {name}",
            )

    for agent_index, (agent_relative, _label) in enumerate(PROJECT_AGENT_DIRS):
        target = root / agent_relative
        index, original, staged, had_target = original_for(target)
        expected_staged = transaction / "staged/mirrors" / str(agent_index)
        if staged != expected_staged or not had_target:
            raise ValueError(
                f"portable full-mirror recovery layout is invalid: {target}"
            )
        original_mode, original_entries = _bound_replacement_state(
            root, original, label="old agent mirror authority"
        )
        if isinstance(old_inventory, ManagedSkillsInventory):
            expected_old_entries = old_full_entries_tuple
            if original_entries != expected_old_entries:
                raise ValueError(
                    f"portable upgrade recovery original mirror is not trusted: {target}"
                )
        else:
            legacy_entries: list[SkillEntry] = []
            for name in old_names:
                legacy_entries.append(SkillEntry(name, "directory", False, b""))
                adapter_relative = (
                    PurePosixPath(agent_relative) / name / "SKILL.md"
                )
                canonical_relative = PurePosixPath(".skills") / name / "SKILL.md"
                legacy_entries.append(
                    SkillEntry(
                        f"{name}/SKILL.md",
                        "file",
                        False,
                        _legacy_adapter_text(
                            name,
                            posixpath.relpath(
                                canonical_relative.as_posix(),
                                adapter_relative.parent.as_posix(),
                            ),
                        ).encode("utf-8"),
                    )
                )
            expected_managed_entries = tuple(
                sorted(legacy_entries, key=lambda entry: entry.path)
            )
            actual_managed_entries = tuple(
                entry
                for entry in original_entries
                if entry.path.split("/", 1)[0] in old_set
            )
            if actual_managed_entries != expected_managed_entries:
                raise ValueError(
                    f"portable upgrade recovery original adapters are not trusted: {target}"
                )
            custom_by_name = {tree.name: tree for tree in custom}
            actual_extra_names = {
                entry.path.split("/", 1)[0]
                for entry in original_entries
                if entry.path.split("/", 1)[0] not in old_set
            }
            if not actual_extra_names <= set(custom_by_name):
                raise ValueError(
                    f"portable upgrade recovery original mirror has owner-only drift: {target}"
                )
            for name in actual_extra_names:
                expected_custom = tuple(
                    sorted(
                        (
                            SkillEntry(name, "directory", False, b""),
                            *tuple(
                                SkillEntry(
                                    f"{name}/{entry.path}",
                                    entry.kind,
                                    entry.executable,
                                    entry.content,
                                )
                                for entry in custom_by_name[name].entries
                            ),
                        ),
                        key=lambda entry: entry.path,
                    )
                )
                actual_custom = tuple(
                    entry
                    for entry in original_entries
                    if entry.path == name or entry.path.startswith(f"{name}/")
                )
                if actual_custom != expected_custom:
                    raise ValueError(
                        "portable upgrade recovery custom mirror differs from canonical: "
                        f"{target}/{name}"
                    )
        validate_proof(
            index,
            staged,
            _entries_replacement_snapshot(
                prospective_entries, root_mode=original_mode
            ),
            f"mirror {agent_relative}",
        )

    for bootstrap_index, target in enumerate(fixed_bootstraps):
        index, original, staged, had_target = original_for(target)
        expected_staged = transaction / "staged/bootstrap" / str(bootstrap_index)
        if staged != expected_staged:
            raise ValueError(
                f"portable full-mirror bootstrap recovery layout is invalid: {target}"
            )
        if had_target:
            metadata = original.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise ValueError(
                    f"portable upgrade recovery bootstrap is unsafe: {target}"
                )
            old_text = original.read_text(encoding="utf-8")
            mode = stat.S_IMODE(metadata.st_mode)
        else:
            old_text = ""
            mode = 0o644
        validate_proof(
            index,
            staged,
            (
                (
                    "",
                    "file",
                    mode,
                    _render_bootstrap_target(root, target, old_text).encode("utf-8"),
                ),
            ),
            f"bootstrap {target}",
        )

    inventory_target = root / MANAGED_SKILLS_INVENTORY
    inventory_index, inventory_original, inventory_staged, inventory_had_target = (
        original_for(inventory_target)
    )
    if (
        not inventory_had_target
        or inventory_staged
        != transaction / "staged/inventory/managed-skills.json"
    ):
        raise ValueError("portable full-mirror inventory recovery layout is invalid")
    inventory_mode = stat.S_IMODE(inventory_original.lstat().st_mode)
    next_inventory = ManagedSkillsInventory(
        skills_version=version,
        managed_skills=source_collection.names,
        managed_skill_digests={
            skill.name: skill.digest for skill in source_collection.skills
        },
    )
    validate_proof(
        inventory_index,
        inventory_staged,
        (
            (
                "",
                "file",
                inventory_mode,
                render_inventory(next_inventory).encode("utf-8"),
            ),
        ),
        "inventory",
    )

    removable_new_targets: set[Path] = set()
    for target, backup, staged, had_target in records[:-1]:
        if not had_target and backup.exists():
            raise ValueError(
                f"portable upgrade recovery has a backup for absent target: {target}"
            )
        if rollback and not had_target and (target.exists() or target.is_symlink()):
            if staged is None or _replacement_snapshot(
                root, target
            ) != _replacement_snapshot(root, staged):
                raise ValueError(
                    "portable upgrade recovery cannot prove a newly created target: "
                    f"{target}"
                )
            removable_new_targets.add(target)
    if not rollback:
        for target, _backup, staged, _had_target in records:
            if staged is None:
                if target.exists() or target.is_symlink():
                    raise ValueError(
                        f"portable committed upgrade retained removed target: {target}"
                    )
            elif not target.exists() or _replacement_snapshot(
                root, target
            ) != _replacement_snapshot(root, staged):
                raise ValueError(
                    f"portable committed upgrade target differs from proof: {target}"
                )
    return removable_new_targets


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
    parsed_old_inventory = _read_inventory_file(root, old_inventory)
    full_mirror_targets = {
        root / relative for relative, _label in PROJECT_AGENT_DIRS
    }
    if full_mirror_targets.issubset({record[0] for record in records}):
        return _authorize_full_upgrade_recovery(
            root,
            transaction,
            records,
            rollback=rollback,
            version=version,
            source=source,
            current_names=current_names,
            old_inventory=parsed_old_inventory,
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


def _authorize_sync_recovery(
    root: Path,
    transaction: Path,
    records: list[tuple[Path, Path, Path | None, bool]],
    *,
    rollback: bool,
) -> set[Path]:
    """Authorize a sync journal only against the live anchored canonical tree."""
    canonical = discover_anchored_skill_collection(root / ".skills", anchor=root)
    canonical_entries = _canonical_skill_entries(canonical)
    expected_targets = tuple(
        root / relative for relative, _label in PROJECT_AGENT_DIRS
    )
    if tuple(record[0] for record in records) != expected_targets:
        raise ValueError(
            "portable sync recovery record plan must contain exactly the six "
            "agent skills roots in canonical order"
        )

    removable_new_targets: set[Path] = set()
    for index, (target, backup, staged, had_target) in enumerate(records):
        expected_staged = transaction / "staged" / "mirrors" / str(index)
        if staged != expected_staged:
            raise ValueError(
                f"portable sync recovery staged layout is invalid: {target}"
            )
        staged_collection = discover_anchored_skill_collection(staged, anchor=root)
        if staged_collection != canonical:
            raise ValueError(
                f"portable sync recovery staged proof differs from canonical: {target}"
            )
        staged_mode, staged_snapshot = _bound_replacement_state(
            root, staged, label="staged proof"
        )
        if staged_snapshot != canonical_entries:
            raise ValueError(
                f"portable sync recovery staged proof differs from canonical: {target}"
            )
        install = transaction / "install" / str(index)
        if (install.exists() or install.is_symlink()) and _bound_replacement_state(
            root, install, label="install proof"
        ) != (staged_mode, staged_snapshot):
            raise ValueError(
                f"portable sync recovery install candidate diverged from proof: {target}"
            )

        original = backup if backup.exists() else target
        if had_target:
            if not original.exists() or original.is_symlink():
                raise ValueError(
                    f"portable sync recovery original mirror is missing or unsafe: {target}"
                )
            _bound_replacement_snapshot(
                root, original, label="original mirror authority"
            )
        elif backup.exists() or backup.is_symlink():
            raise ValueError(
                "portable sync journal has a backup for an originally absent target: "
                f"{target}"
            )

        target_present = target.exists() or target.is_symlink()
        if rollback and not had_target and target_present:
            if _bound_replacement_state(
                root, target, label="rollback postimage"
            ) != (staged_mode, staged_snapshot):
                raise ValueError(
                    "portable sync recovery target diverged from staged proof; "
                    f"preserved without mutation: {target}"
                )
            removable_new_targets.add(target)
        if not rollback and (
            not target_present
            or _bound_replacement_state(
                root, target, label="committed postimage"
            )
            != (staged_mode, staged_snapshot)
        ):
            raise ValueError(
                f"portable committed sync target diverged from proof: {target}"
            )
    return removable_new_targets


def _remove_transaction_target(
    root: Path,
    path: Path,
    *,
    operation: ReplacementOperation = UPGRADE_OPERATION,
    sync_transaction_binding: tuple[Path, tuple[int, int]] | None = None,
    sync_parent_identities: Mapping[Path, tuple[int, int]] | None = None,
) -> None:
    _assert_sync_transaction_binding(
        root,
        sync_transaction_binding,
        label="rollback removal authority",
    )
    _remove_sync_path(
        root,
        path,
        expected_parent_identity=(sync_parent_identities or {}).get(path.parent),
        transaction_binding=sync_transaction_binding,
    )


def _rollback_upgrade_records(
    root: Path,
    records: list[tuple[Path, Path, Path | None, bool]],
    *,
    operation: ReplacementOperation = UPGRADE_OPERATION,
    removable_new_targets: set[Path] | None = None,
    sync_transaction_binding: tuple[Path, tuple[int, int]] | None = None,
    sync_parent_identities: Mapping[Path, tuple[int, int]] | None = None,
) -> list[str]:
    removable = removable_new_targets or set()
    errors: list[str] = []

    def snapshot(path: Path, label: str):
        return _bound_replacement_state(root, path, label=label)

    for target, backup, staged, had_target in reversed(records):
        try:
            if backup.exists():
                backup_snapshot = snapshot(backup, "rollback backup")
                if not target.exists() and not target.is_symlink():
                    _rename_sync_path(
                        root,
                        backup,
                        target,
                        transaction_binding=sync_transaction_binding,
                        expected_source_parent_identity=(
                            sync_parent_identities or {}
                        ).get(backup.parent),
                        expected_target_parent_identity=(
                            sync_parent_identities or {}
                        ).get(target.parent),
                    )
                    continue
                target_snapshot = snapshot(target, "rollback target")
                if target_snapshot == backup_snapshot:
                    _remove_transaction_target(
                        root,
                        backup,
                        operation=operation,
                        sync_transaction_binding=sync_transaction_binding,
                        sync_parent_identities=sync_parent_identities,
                    )
                    continue
                if staged is not None and target_snapshot == snapshot(
                    staged, "rollback staged proof"
                ):
                    _remove_transaction_target(
                        root,
                        target,
                        operation=operation,
                        sync_transaction_binding=sync_transaction_binding,
                        sync_parent_identities=sync_parent_identities,
                    )
                    _rename_sync_path(
                        root,
                        backup,
                        target,
                        transaction_binding=sync_transaction_binding,
                        expected_source_parent_identity=(
                            sync_parent_identities or {}
                        ).get(backup.parent),
                        expected_target_parent_identity=(
                            sync_parent_identities or {}
                        ).get(target.parent),
                    )
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
                _remove_transaction_target(
                    root,
                    target,
                    operation=operation,
                    sync_transaction_binding=sync_transaction_binding,
                    sync_parent_identities=sync_parent_identities,
                )
            elif not target.exists():
                raise OSError(f"original target and backup are both missing: {target}")
        except BaseException as exc:
            errors.append(f"{target}: {exc}")
    return errors


def _rollback_created_parents(
    root: Path,
    created_parents: tuple[Path, ...],
    *,
    operation: ReplacementOperation = UPGRADE_OPERATION,
    sync_transaction_binding: tuple[Path, tuple[int, int]] | None = None,
    sync_parent_identities: Mapping[Path, tuple[int, int]] | None = None,
) -> list[str]:
    errors: list[str] = []
    for parent in reversed(created_parents):
        try:
            _assert_sync_transaction_binding(
                root,
                sync_transaction_binding,
                label="created-parent rollback authority",
            )
            _rmdir_sync_path(
                root,
                parent,
                expected_parent_identity=(sync_parent_identities or {}).get(
                    parent.parent
                ),
                transaction_binding=sync_transaction_binding,
            )
        except BaseException as exc:
            errors.append(f"created parent {parent}: {exc}")
    return errors


def _cleanup_replacement_transaction(
    root: Path,
    transaction: Path,
    operation: ReplacementOperation = UPGRADE_OPERATION,
    *,
    transaction_identity: tuple[int, int] | None = None,
) -> None:
    _remove_sync_path(
        root, transaction, expected_identity=transaction_identity
    )
    try:
        _rmdir_sync_path(root, transaction.parent)
    except OSError:
        pass


def _cleanup_upgrade_transaction(root: Path, transaction: Path) -> None:
    """Compatibility wrapper for existing upgrade recovery tests."""
    _cleanup_replacement_transaction(root, transaction)


def _recover_replacement_transactions(
    root: Path,
    operation: ReplacementOperation,
    *,
    version: str | None = None,
    source: Path | None = None,
    current_names: tuple[str, ...] | None = None,
) -> None:
    transactions = root / operation.transactions_relative
    _assert_safe_managed_path(root, transactions)
    if not transactions.exists():
        return
    _assert_directory(root, transactions, "upgrade transactions")
    candidates: tuple[tuple[Path, tuple[int, int] | None], ...] = tuple(
        (transaction, identity)
        for transaction, identity in _bound_sync_transaction_candidates(
            root, transactions
        )
    )
    for transaction, transaction_identity in candidates:
        assert transaction_identity is not None
        has_journal = _bound_sync_transaction_has_journal(
            root, transaction, transaction_identity
        )
        if not has_journal:
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
            _remove_sync_path(
                root,
                transaction,
                expected_identity=transaction_identity,
            )
            continue
        payload, records, created_parents = _load_replacement_journal(
            root,
            transaction,
            operation,
            transaction_identity=transaction_identity,
        )
        rollback = payload["status"] == "prepared"
        if operation == UPGRADE_OPERATION:
            if version is None or source is None or current_names is None:
                raise ValueError(
                    "portable upgrade recovery requires a trusted installed skill source"
                )
            removable_new_targets = _authorize_upgrade_recovery(
                root,
                transaction,
                records,
                rollback=rollback,
                version=version,
                source=source,
                current_names=current_names,
            )
        else:
            assert transaction_identity is not None
            removable_new_targets = _authorize_sync_recovery(
                root, transaction, records, rollback=rollback
            )
            _assert_sync_directory_identity(
                root,
                transaction,
                transaction_identity,
                label="authorized recovery transaction",
            )
        sync_parent_identities = (
            _capture_sync_parent_identities(
                root,
                transaction,
                records,
                created_parents=created_parents,
            )
            if payload["status"] == "prepared"
            else {}
        )
        if payload["status"] == "committed":
            _cleanup_replacement_transaction(
                root,
                transaction,
                operation,
                transaction_identity=transaction_identity,
            )
            continue
        errors = _rollback_upgrade_records(
            root,
            records,
            operation=operation,
            removable_new_targets=removable_new_targets,
            sync_transaction_binding=(transaction, transaction_identity)
            if transaction_identity is not None
            else None,
            sync_parent_identities=sync_parent_identities,
        )
        errors.extend(
            _rollback_created_parents(
                root,
                created_parents,
                operation=operation,
                sync_transaction_binding=(transaction, transaction_identity)
                if transaction_identity is not None
                else None,
                sync_parent_identities=sync_parent_identities,
            )
        )
        if errors:
            raise OSError(
                f"portable skill {operation.name} recovery is incomplete; preserved "
                "journal and "
                f"backups at {transaction}: {'; '.join(errors)}"
            )
        assert transaction_identity is not None
        _assert_sync_directory_identity(
            root,
            transaction,
            transaction_identity,
            label="recovered transaction",
        )
        _cleanup_replacement_transaction(
            root,
            transaction,
            operation,
            transaction_identity=transaction_identity,
        )


def _recover_upgrade_transactions(
    root: Path,
    *,
    version: str,
    source: Path,
    current_names: tuple[str, ...],
) -> None:
    """Compatibility wrapper for the former upgrade-only recovery helper."""
    _recover_replacement_transactions(
        root,
        UPGRADE_OPERATION,
        version=version,
        source=source,
        current_names=current_names,
    )


def _recover_skill_operations(
    root: Path,
    *,
    version: str | None = None,
    source: Path | None = None,
    current_names: tuple[str, ...] | None = None,
) -> None:
    """Recover every pending skill replacement operation while the lock is held."""
    upgrade_transactions = root / UPGRADE_OPERATION.transactions_relative
    if upgrade_transactions.exists() or upgrade_transactions.is_symlink():
        if version is None or source is None or current_names is None:
            installed_source = Path(__file__).parent / "_data" / "skills"
            source, current_names = _discover_source_skills(installed_source)
            version = __version__
        _recover_replacement_transactions(
            root,
            UPGRADE_OPERATION,
            version=version,
            source=source,
            current_names=current_names,
        )
    _recover_replacement_transactions(root, SYNC_OPERATION)


def recover_portable_skill_operations(
    root: Path,
    *,
    version: str,
    source_skills: Path,
    expected_root_identity: tuple[int, ...],
) -> None:
    """Recover pending replacements before a read-oriented CLI operation."""
    root = Path(os.path.abspath(os.fspath(root)))
    with _bound_portable_mutation_root(root, expected_root_identity):
        _recover_portable_skill_operations_bound(
            root, version=version, source_skills=source_skills
        )


def _recover_portable_skill_operations_bound(
    root: Path, *, version: str, source_skills: Path
) -> None:
    root = _safe_root(root)
    source, current_names = _discover_source_skills(source_skills)
    with _portable_skills_lock(root):
        _recover_skill_operations(
            root,
            version=version,
            source=source,
            current_names=current_names,
        )


def _preflight_upgrade_paths(
    root: Path,
    *,
    previous_names: tuple[str, ...],
    current_names: tuple[str, ...],
    source_bootstrap: Path | None = None,
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

    return _portable_bootstrap_plans(root, source_bootstrap=source_bootstrap)


def _prepare_replacement_journal(
    root: Path,
    transaction: Path,
    operation: ReplacementOperation,
    replacements: list[tuple[Path, Path | None]],
    *,
    precreated_parents: tuple[Path, ...] = (),
) -> tuple[dict[str, object], list[tuple[Path, Path, Path | None, bool]]]:
    _ensure_sync_directory(
        root, transaction / "backups", label="backup directory"
    )
    _ensure_sync_directory(
        root, transaction / "install", label="install directory"
    )
    transaction_relative = _repo_relative_path(root, transaction)
    raw_records: list[dict[str, object]] = []
    for index, (target, staged) in enumerate(replacements):
        install: Path | None = None
        if staged is not None:
            install = transaction / "install" / str(index)
            _copy_staged_replacement(
                root,
                staged,
                install,
                bound=True,
            )
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
    created_parents: set[Path] = set(precreated_parents)
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
        "operation": operation.name,
        "replacements": raw_records,
        "schema_version": _REPLACEMENT_JOURNAL_SCHEMA,
        "status": "prepared",
    }
    _status, records, _created_parents = _journal_records(
        root, transaction, operation, payload
    )
    _write_replacement_journal(root, transaction, payload)
    return payload, records


def _prepare_upgrade_journal(
    root: Path,
    transaction: Path,
    replacements: list[tuple[Path, Path | None]],
) -> tuple[dict[str, object], list[tuple[Path, Path, Path | None, bool]]]:
    return _prepare_replacement_journal(
        root, transaction, UPGRADE_OPERATION, replacements
    )


def _capture_sync_parent_identities(
    root: Path,
    transaction: Path,
    records: list[tuple[Path, Path, Path | None, bool]],
    *,
    created_parents: tuple[Path, ...],
    initial: Mapping[Path, tuple[int, int]] | None = None,
) -> dict[Path, tuple[int, int]]:
    """Bind every parent that a sync apply or rollback may mutate."""
    parents = {root, transaction / "install"}
    backup_root = transaction / "backups"
    if backup_root.exists() or backup_root.is_symlink():
        parents.add(backup_root)
    for target, backup, _staged, _had_target in records:
        parents.add(target.parent)
        if backup.parent.exists() or backup.parent.is_symlink():
            parents.add(backup.parent)
    allowed_creations = set(created_parents)
    identities = dict(initial or {})
    for parent, identity in identities.items():
        _assert_sync_directory_identity(
            root, parent, identity, label="initial mutation parent"
        )
    for parent in sorted(parents, key=lambda path: (len(path.parts), path.as_posix())):
        if parent in identities:
            continue
        if parent in allowed_creations:
            identities[parent] = _ensure_sync_directory(
                root, parent, label="mutation parent"
            )
        else:
            identities[parent] = _sync_directory_identity(
                root, parent, label="mutation parent"
            )
    return identities


def _capture_sync_agent_parent_identities(
    root: Path,
) -> tuple[dict[Path, tuple[int, int]], tuple[Path, ...]]:
    identities: dict[Path, tuple[int, int]] = {
        root: _sync_directory_identity(root, root, label="repository mutation root")
    }
    created: list[Path] = []
    try:
        for relative, _label in PROJECT_AGENT_DIRS:
            parent = (root / relative).parent
            existed = parent.exists() or parent.is_symlink()
            if not existed:
                created.append(parent)
            identities[parent] = _ensure_sync_directory(
                root, parent, label="agent mutation parent"
            )
    except BaseException as original_error:
        rollback_errors = _rollback_created_parents(
            root,
            tuple(sorted(created, key=lambda path: _parent_path_order(root, path))),
            operation=SYNC_OPERATION,
            sync_parent_identities=identities,
        )
        if rollback_errors:
            raise OSError(
                "portable skill sync parent preparation failed "
                f"({original_error}); rollback is incomplete: "
                + "; ".join(rollback_errors)
            ) from original_error
        raise
    return identities, tuple(
        sorted(created, key=lambda path: _parent_path_order(root, path))
    )


def _apply_journaled_replacements(
    root: Path,
    transaction: Path,
    operation: ReplacementOperation,
    payload: dict[str, object],
    records: list[tuple[Path, Path, Path | None, bool]],
    *,
    initial_sync_parent_identities: Mapping[Path, tuple[int, int]] | None = None,
    expected_preimages: Mapping[Path, ReplacementProof | None] | None = None,
) -> None:
    created_targets: set[Path] = set()
    transaction_identity = _sync_directory_identity(
        root, transaction, label="apply transaction"
    )
    sync_transaction_binding = (
        (transaction, transaction_identity)
        if transaction_identity is not None
        else None
    )
    _status, _validated_records, created_parents = _journal_records(
        root, transaction, operation, payload
    )
    sync_parent_identities: dict[Path, tuple[int, int]] = {}
    try:
        sync_parent_identities = _capture_sync_parent_identities(
            root,
            transaction,
            records,
            created_parents=created_parents,
            initial=initial_sync_parent_identities,
        )
        for index, (target, backup, staged, had_target) in enumerate(records):
            install = transaction / "install" / str(index)
            install_identity: tuple[int, int] | None = None
            if staged is not None:
                install_identity, install_snapshot = _bound_replacement_proof(
                    root, install, label="forward install proof"
                )
                staged_snapshot = _bound_replacement_state(
                    root, staged, label="forward staged proof"
                )
                if install_snapshot != staged_snapshot:
                    raise OSError(
                        f"portable upgrade install candidate diverged from proof: {install}"
                    )
            observed_target = _bound_optional_replacement_proof(
                root, target, label="forward target preimage"
            )
            if expected_preimages is not None:
                expected_target = expected_preimages.get(target)
                if observed_target != expected_target:
                    raise OSError(
                        f"managed upgrade target preimage changed: {target}"
                    )
            if had_target:
                if observed_target is None:
                    raise OSError(f"managed upgrade target disappeared: {target}")
                _rename_sync_path(
                    root,
                    target,
                    backup,
                    transaction_binding=sync_transaction_binding,
                    expected_source_identity=observed_target[0],
                    expected_source_parent_identity=sync_parent_identities[
                        target.parent
                    ],
                    expected_target_parent_identity=sync_parent_identities[
                        backup.parent
                    ],
                )
            elif observed_target is not None:
                raise OSError(f"managed upgrade target appeared during transaction: {target}")
            if staged is not None:
                if not had_target:
                    created_targets.add(target)
                assert install_identity is not None
                _rename_sync_path(
                    root,
                    install,
                    target,
                    transaction_binding=sync_transaction_binding,
                    expected_source_identity=install_identity,
                    expected_source_parent_identity=sync_parent_identities[
                        install.parent
                    ],
                    expected_target_parent_identity=sync_parent_identities[
                        target.parent
                    ],
                )
        if operation == SYNC_OPERATION and plan_portable_skill_sync(root).status != "clean":
            raise RuntimeError("portable skill synchronization verification failed")
        payload["status"] = "committed"
        _write_replacement_journal(root, transaction, payload)
    except BaseException as forward_error:
        rollback_errors = _rollback_upgrade_records(
            root,
            records,
            operation=operation,
            removable_new_targets=created_targets,
            sync_transaction_binding=sync_transaction_binding,
            sync_parent_identities=sync_parent_identities,
        )
        rollback_errors.extend(
            _rollback_created_parents(
                root,
                created_parents,
                operation=operation,
                sync_transaction_binding=sync_transaction_binding,
                sync_parent_identities=sync_parent_identities,
            )
        )
        if rollback_errors:
            raise OSError(
                f"portable skill {operation.name} failed ({forward_error}); rollback is "
                "incomplete; "
                f"journal and backups preserved at {transaction}: "
                f"{'; '.join(rollback_errors)}"
            ) from forward_error
        try:
            _cleanup_replacement_transaction(
                root,
                transaction,
                operation,
                transaction_identity=transaction_identity,
            )
        except BaseException as cleanup_error:
            raise OSError(
                f"portable skill {operation.name} failed ({forward_error}); rollback "
                "completed but "
                f"transaction cleanup failed at {transaction}: {cleanup_error}"
            ) from forward_error
        raise
    _cleanup_replacement_transaction(
        root,
        transaction,
        operation,
        transaction_identity=transaction_identity,
    )


def _apply_journaled_upgrade(
    root: Path,
    transaction: Path,
    payload: dict[str, object],
    records: list[tuple[Path, Path, Path | None, bool]],
    *,
    expected_preimages: Mapping[Path, ReplacementProof | None] | None = None,
) -> None:
    """Compatibility wrapper retaining the upgrade test seam."""
    _apply_journaled_replacements(
        root,
        transaction,
        UPGRADE_OPERATION,
        payload,
        records,
        expected_preimages=expected_preimages,
    )


def _create_replacement_transaction(
    root: Path, operation: ReplacementOperation
) -> Path:
    transactions = root / operation.transactions_relative
    directories = _open_sync_directory_chain(
        root, transactions, create=True, label="transactions"
    )
    try:
        parent_fd = directories[-1][2]
        for _attempt in range(16):
            name = f"txn-{secrets.token_hex(16)}"
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            transaction = transactions / name
            _validate_sync_directory_chain(directories, label="transactions")
            os.fsync(parent_fd)
            return transaction
        raise RuntimeError("could not reserve portable replacement transaction name")
    finally:
        _close_sync_directory_chain(directories)


def _stage_complete_agent_mirrors(
    root: Path,
    transaction: Path,
    canonical: SkillCollection,
    *,
    parent_identities: Mapping[Path, tuple[int, int]] | None = None,
) -> list[tuple[Path, Path | None]]:
    replacements: list[tuple[Path, Path | None]] = []
    canonical_entries = _canonical_skill_entries(canonical)
    for index, (relative, _label) in enumerate(PROJECT_AGENT_DIRS):
        target = root / relative
        expected_parent = (parent_identities or {}).get(target.parent)
        if expected_parent is not None:
            _assert_sync_directory_identity(
                root,
                target.parent,
                expected_parent,
                label="agent staging parent",
            )
        _assert_safe_managed_path(root, target)
        target_mode = 0o755
        if target.exists() or target.is_symlink():
            _assert_directory(root, target, "agent skills mirror")
            _bound_replacement_snapshot(
                root, target, label="agent mirror preflight"
            )
            target_mode = _bound_directory_mode(root, target)
        if expected_parent is not None:
            _assert_sync_directory_identity(
                root,
                target.parent,
                expected_parent,
                label="agent staging parent",
            )
        staged = transaction / "staged" / "mirrors" / str(index)
        _materialize_sync_snapshot(
            root, staged, mode=target_mode, entries=canonical_entries
        )
        staged_collection = discover_anchored_skill_collection(staged, anchor=root)
        if staged_collection != canonical:
            raise RuntimeError(
                f"portable staged skill mirror verification failed: {relative}"
            )
        replacements.append((target, staged))
    return replacements


def sync_portable_skill_mirrors(
    root: Path,
    *,
    apply: bool,
    expected_root_identity: tuple[int, ...],
) -> SkillSyncReport:
    """Check or transactionally rebuild all derived agent skill mirrors."""
    root = Path(os.path.abspath(os.fspath(root)))
    with _bound_portable_mutation_root(root, expected_root_identity):
        return _sync_portable_skill_mirrors_bound(root, apply=apply)


def _sync_portable_skill_mirrors_bound(
    root: Path, *, apply: bool
) -> SkillSyncReport:
    root = _safe_root(root)
    if not root.is_dir():
        raise ValueError(f"portable repository root is not a directory: {root}")
    with _portable_skills_lock(root):
        _recover_skill_operations(root)
        report = plan_portable_skill_sync(root)
        if not apply or report.status == "clean":
            return report

        canonical = discover_anchored_skill_collection(root / ".skills", anchor=root)
        transaction = _create_replacement_transaction(root, SYNC_OPERATION)
        journal_written = False
        parent_identities: dict[Path, tuple[int, int]] = {}
        precreated_parents: tuple[Path, ...] = ()
        try:
            parent_identities, precreated_parents = (
                _capture_sync_agent_parent_identities(root)
            )
            replacements = _stage_complete_agent_mirrors(
                root,
                transaction,
                canonical,
                parent_identities=parent_identities,
            )
            payload, records = _prepare_replacement_journal(
                root,
                transaction,
                SYNC_OPERATION,
                replacements,
                precreated_parents=precreated_parents,
            )
            journal_written = True
        except BaseException:
            if not journal_written:
                rollback_errors = _rollback_created_parents(
                    root,
                    precreated_parents,
                    operation=SYNC_OPERATION,
                    sync_parent_identities=parent_identities,
                )
                _cleanup_replacement_transaction(
                    root, transaction, SYNC_OPERATION
                )
                if rollback_errors:
                    raise OSError(
                        "portable skill sync preparation rollback is incomplete: "
                        + "; ".join(rollback_errors)
                    )
            raise
        _apply_journaled_replacements(
            root,
            transaction,
            SYNC_OPERATION,
            payload,
            records,
            initial_sync_parent_identities=parent_identities,
        )
        verified = plan_portable_skill_sync(root)
        if verified.status != "clean":
            raise RuntimeError("portable skill synchronization verification failed")
        return replace(verified, status="applied")


def upgrade_portable_skills(
    root: Path,
    *,
    version: str,
    source_skills: Path,
    expected_root_identity: tuple[int, ...],
    warning_sink: list[dict[str, str]] | None = None,
) -> tuple[str, ...]:
    """Upgrade managed canonical skills, full mirrors, and bootstrap blocks.

    The inventory is the ownership boundary. Unlisted directories are never
    adopted, replaced, or removed, and the new inventory is committed last.
    """
    compatible_cli_spec(version)
    root = Path(os.path.abspath(os.fspath(root)))
    with _bound_portable_mutation_root(root, expected_root_identity):
        return _upgrade_portable_skills_bound(
            root,
            version=version,
            source_skills=source_skills,
            warning_sink=warning_sink,
        )


def _upgrade_portable_skills_bound(
    root: Path,
    *,
    version: str,
    source_skills: Path,
    warning_sink: list[dict[str, str]] | None,
) -> tuple[str, ...]:
    root = _safe_root(root)
    if not root.is_dir():
        raise ValueError(f"portable repository root is not a directory: {root}")
    with _portable_skills_lock(root):
        config_path = root / ".obsidian-wiki/config.toml"
        _assert_ordinary_file(root, config_path, "configuration")
        _assert_single_link_ordinary_file(root, config_path, "configuration")
        _load_canonical_portable_config(root, version=version)
        source, current_names = _discover_source_skills(source_skills)
        _recover_skill_operations(
            root,
            version=version,
            source=source,
            current_names=current_names,
        )
        inventory = _read_inventory_file(root, root / MANAGED_SKILLS_INVENTORY)
        canonical = discover_anchored_skill_collection(root / ".skills", anchor=root)
        legacy_migration = isinstance(inventory, LegacyManagedSkillsInventory)
        if isinstance(inventory, ManagedSkillsInventory):
            _validate_v2_upgrade_repository(root, inventory, canonical)
        else:
            _validate_known_legacy_repository(root, inventory, canonical)
        previous_names = inventory.managed_skills
        bootstrap_plans = _preflight_upgrade_paths(
            root,
            previous_names=previous_names,
            current_names=current_names,
        )
        preimage_targets = [
            *(root / ".skills" / name for name in sorted(set(previous_names) | set(current_names))),
            *(root / agent_relative for agent_relative, _label in PROJECT_AGENT_DIRS),
            *(target for target, _text in bootstrap_plans),
            root / MANAGED_SKILLS_INVENTORY,
        ]
        expected_preimages = {
            target: _bound_optional_replacement_proof(
                root, target, label="upgrade target preimage"
            )
            for target in preimage_targets
        }
        source_collection = _snapshot_bundled_skills(source)
        previous = set(previous_names)
        current = set(current_names)
        custom = tuple(
            tree for tree in canonical.skills if tree.name not in previous
        )
        collisions = sorted(current & {tree.name for tree in custom})
        if collisions:
            raise ValueError(
                "new bundled skill collides with a custom canonical skill: "
                f"{collisions[0]}"
            )
        prospective = SkillCollection(
            tuple(sorted((*source_collection.skills, *custom), key=lambda tree: tree.name))
        )

        transaction = _create_replacement_transaction(root, UPGRADE_OPERATION)
        journal_written = False
        try:
            staged_root = transaction / "staged"
            staged_canonical = staged_root / "canonical"
            staged_mirrors = staged_root / "mirrors"
            staged_bootstrap = staged_root / "bootstrap"
            _materialize_sync_snapshot(
                root, staged_canonical, mode=0o755, entries=()
            )
            for tree in source_collection.skills:
                canonical_target = root / ".skills" / tree.name
                canonical_preimage = expected_preimages[canonical_target]
                canonical_mode = (
                    canonical_preimage[1][0]
                    if canonical_preimage is not None
                    else 0o755
                )
                _materialize_bound_replacement_state(
                    root,
                    staged_canonical / tree.name,
                    (canonical_mode, tree.entries),
                )

            staged_agent_roots: dict[Path, Path] = {}
            for agent_index, (agent_relative, _label) in enumerate(
                PROJECT_AGENT_DIRS
            ):
                target = root / agent_relative
                staged = staged_mirrors / str(agent_index)
                target_preimage = expected_preimages[target]
                if target_preimage is None:
                    raise OSError(f"managed upgrade target disappeared: {target}")
                _materialize_sync_snapshot(
                    root,
                    staged,
                    mode=target_preimage[1][0],
                    entries=_canonical_skill_entries(prospective),
                )
                if discover_anchored_skill_collection(staged, anchor=root) != prospective:
                    raise RuntimeError(
                        f"portable staged full mirror verification failed: {agent_relative}"
                    )
                staged_agent_roots[target] = staged

            bootstrap_staged: dict[Path, Path] = {}
            for index, (target, text) in enumerate(bootstrap_plans):
                staged = staged_bootstrap / str(index)
                target_preimage = expected_preimages[target]
                target_mode = (
                    target_preimage[1][0]
                    if target_preimage is not None
                    else 0o644
                )
                _materialize_bound_replacement_state(
                    root,
                    staged,
                    (
                        target_mode,
                        (
                            SkillEntry(
                                "",
                                "file",
                                bool(target_mode & 0o111),
                                text.encode("utf-8"),
                            ),
                        ),
                    ),
                )
                bootstrap_staged[target] = staged

            replacements: list[tuple[Path, Path | None]] = []
            for name in sorted(previous | current):
                replacements.append(
                    (
                        root / ".skills" / name,
                        staged_canonical / name if name in current else None,
                    )
                )
            replacements.extend(
                (root / agent_relative, staged_agent_roots[root / agent_relative])
                for agent_relative, _label in PROJECT_AGENT_DIRS
            )
            replacements.extend(
                (target, bootstrap_staged[target])
                for target, _text in bootstrap_plans
            )
            inventory = root / MANAGED_SKILLS_INVENTORY
            staged_inventory = staged_root / "inventory/managed-skills.json"
            next_inventory = ManagedSkillsInventory(
                skills_version=version,
                managed_skills=source_collection.names,
                managed_skill_digests={
                    skill.name: skill.digest for skill in source_collection.skills
                },
            )
            inventory_preimage = expected_preimages[inventory]
            inventory_mode = (
                inventory_preimage[1][0]
                if inventory_preimage is not None
                else 0o644
            )
            _materialize_bound_replacement_state(
                root,
                staged_inventory,
                (
                    inventory_mode,
                    (
                        SkillEntry(
                            "",
                            "file",
                            bool(inventory_mode & 0o111),
                            render_inventory(next_inventory).encode("utf-8"),
                        ),
                    ),
                ),
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
        _apply_journaled_upgrade(
            root,
            transaction,
            payload,
            records,
            expected_preimages=expected_preimages,
        )
        if legacy_migration and warning_sink is not None:
            warning_sink.append(
                {
                    "code": "legacy-adapters-migrated",
                    "message": (
                        "recognized legacy adapters were migrated to complete "
                        "agent skill mirrors"
                    ),
                }
            )
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
            _recover_skill_operations(
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
                canonical = discover_anchored_skill_collection(
                    root / ".skills", anchor=root
                )
                _validate_v2_inventory_ownership(inventory, canonical)
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
        _populate_portable_repo(
            staging,
            version=version,
            bundled_skills=bundled_skills,
        )
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

"""Canonical append-only operation log for portable repositories."""

from __future__ import annotations

import ctypes
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Tuple


class OperationError(ValueError):
    """Raised when an operation log or operation change is invalid."""


@dataclass(frozen=True)
class OperationChange:
    transaction_id: str
    completed_at: str
    source_ids: tuple[str, ...]
    created: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]


EMPTY_OPERATION_LOG = (
    "---\n"
    "title: Wiki Operation Log\n"
    "operation_log_schema: 1\n"
    "---\n\n"
    "# Wiki Operation Log\n"
)

_TRANSACTION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)
_KNOWLEDGE_CATEGORIES = frozenset(
    {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
)
_BLOCK_RE = re.compile(
    r"\n## (?P<completed_at>[^\n]+) · (?P<transaction_id>[^\n]+)\n\n"
    r"### Sources\n\n(?P<sources>(?:- `[^`\n]+`\n)+)\n"
    r"### Created\n\n(?P<created>(?:- \[\[[^\]\n]+\]\]\n)+|- None\n)\n"
    r"### Updated\n\n(?P<updated>(?:- \[\[[^\]\n]+\]\]\n)+|- None\n)\n"
    r"### Removed\n\n(?P<removed>(?:- \[\[[^\]\n]+\]\]\n)+|- None\n)"
)
_FileIdentity = Tuple[int, int]
_StableIdentity = Tuple[int, int, int, int, int, int, int]


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise OperationError(
            "operation completed_at must use YYYY-MM-DDTHH:MM:SSZ"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise OperationError("operation completed_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise OperationError("operation completed_at must be UTC")
    return parsed


def _safe_relative(raw: str, label: str, *, source: bool = False) -> str:
    if (
        not isinstance(raw, str)
        or not raw
        or "\\" in raw
        or "\x00" in raw
        or any(ord(character) < 32 for character in raw)
    ):
        raise OperationError(f"operation {label} is invalid")
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or "." in posix.parts
        or ".." in posix.parts
        or posix.as_posix() != raw
    ):
        raise OperationError(f"operation {label} must be a safe relative path")
    if source:
        if "`" in raw:
            raise OperationError("operation source ID cannot contain a backtick")
        return raw
    if (
        posix.suffix != ".md"
        or not posix.parts
        or posix.parts[0] not in _KNOWLEDGE_CATEGORIES
        or posix.parts[:2] == ("journal", "operations")
        or any(part.startswith(".") for part in posix.parts)
        or any(character in raw for character in "[]|#")
    ):
        raise OperationError(f"operation {label} must be a safe knowledge page path")
    return raw


def _canonical_change(change: OperationChange) -> OperationChange:
    if not isinstance(change, OperationChange):
        raise OperationError("operation change has the wrong type")
    if (
        not isinstance(change.transaction_id, str)
        or _TRANSACTION_ID_RE.fullmatch(change.transaction_id) is None
    ):
        raise OperationError("operation transaction_id is invalid")
    _timestamp(change.completed_at)
    try:
        sources = tuple(
            sorted(
                {
                    _safe_relative(item, "source ID", source=True)
                    for item in change.source_ids
                }
            )
        )
    except TypeError as exc:
        raise OperationError("operation source IDs are invalid") from exc
    if not sources:
        raise OperationError("operation requires at least one source ID")
    groups: list[tuple[str, ...]] = []
    for label, values in (
        ("created path", change.created),
        ("updated path", change.updated),
        ("removed path", change.removed),
    ):
        try:
            groups.append(
                tuple(sorted({_safe_relative(item, label) for item in values}))
            )
        except (OperationError, TypeError) as exc:
            raise OperationError(f"operation page path is invalid: {exc}") from exc
    created, updated, removed = groups
    if (
        set(created) & set(updated)
        or set(created) & set(removed)
        or set(updated) & set(removed)
    ):
        raise OperationError("operation change lists must be disjoint")
    return OperationChange(
        change.transaction_id,
        change.completed_at,
        sources,
        created,
        updated,
        removed,
    )


def _render_sources(sources: tuple[str, ...]) -> str:
    return "\n".join(f"- `{source}`" for source in sources)


def _render_pages(pages: tuple[str, ...]) -> str:
    if not pages:
        return "- None"
    return "\n".join(
        f"- [[{PurePosixPath(page).with_suffix('').as_posix()}]]" for page in pages
    )


def render_operation_block(change: OperationChange) -> str:
    """Render one canonical operation block, including its leading blank line."""

    item = _canonical_change(change)
    return (
        f"\n## {item.completed_at} · {item.transaction_id}\n\n"
        "### Sources\n\n"
        f"{_render_sources(item.source_ids)}\n\n"
        "### Created\n\n"
        f"{_render_pages(item.created)}\n\n"
        "### Updated\n\n"
        f"{_render_pages(item.updated)}\n\n"
        "### Removed\n\n"
        f"{_render_pages(item.removed)}\n"
    )


def render_operation_log(changes: tuple[OperationChange, ...]) -> str:
    """Render a complete canonical operation log."""

    try:
        canonical = tuple(_canonical_change(change) for change in changes)
    except TypeError as exc:
        raise OperationError("operation changes must be iterable") from exc
    seen: set[str] = set()
    previous: datetime | None = None
    for item in canonical:
        if item.transaction_id in seen:
            raise OperationError("operation transaction_id must be unique")
        seen.add(item.transaction_id)
        current = _timestamp(item.completed_at)
        if previous is not None and current < previous:
            raise OperationError("operation records are out of timestamp order")
        previous = current
    return EMPTY_OPERATION_LOG + "".join(
        render_operation_block(item) for item in canonical
    )


def _parse_sources(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in text.splitlines():
        match = re.fullmatch(r"- `([^`\n]+)`", line)
        if match is None:
            raise OperationError("operation source entry is invalid")
        values.append(match.group(1))
    return tuple(values)


def _parse_pages(text: str, label: str) -> tuple[str, ...]:
    if text == "- None\n":
        return ()
    values: list[str] = []
    for line in text.splitlines():
        match = re.fullmatch(r"- \[\[([^\[\]|#]+)\]\]", line)
        if match is None:
            raise OperationError(f"operation {label} entry is invalid")
        values.append(match.group(1) + ".md")
    return tuple(values)


def parse_operation_log(text: str) -> tuple[OperationChange, ...]:
    """Strictly parse a complete canonical operation log."""

    if not isinstance(text, str) or not text.startswith(EMPTY_OPERATION_LOG):
        raise OperationError("operation log header is invalid")
    if text == EMPTY_OPERATION_LOG:
        return ()
    records: list[OperationChange] = []
    position = len(EMPTY_OPERATION_LOG)
    while position < len(text):
        match = _BLOCK_RE.match(text, position)
        if match is None:
            raise OperationError("operation log block is malformed")
        records.append(
            OperationChange(
                match.group("transaction_id"),
                match.group("completed_at"),
                _parse_sources(match.group("sources")),
                _parse_pages(match.group("created"), "created"),
                _parse_pages(match.group("updated"), "updated"),
                _parse_pages(match.group("removed"), "removed"),
            )
        )
        position = match.end()
    result = tuple(records)
    try:
        canonical = render_operation_log(result)
    except OperationError as exc:
        raise OperationError(f"operation log is invalid: {exc}") from exc
    if canonical != text:
        raise OperationError("operation log is not canonical")
    return result


def append_operation_text(text: str, change: OperationChange) -> str:
    """Append one change to canonical log text."""

    records = parse_operation_log(text)
    item = _canonical_change(change)
    if any(record.transaction_id == item.transaction_id for record in records):
        raise OperationError("operation transaction_id already exists")
    if records and _timestamp(item.completed_at) < _timestamp(records[-1].completed_at):
        raise OperationError("operation timestamp is older than the log tail")
    return text + render_operation_block(item)


def _stable_identity(metadata: os.stat_result) -> _StableIdentity:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_nlink,
    )


def _ordinary_single_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        and metadata.st_nlink == 1
    )


def _open_parent(root: Path) -> tuple[int, _FileIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        lexical = root.lstat()
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise OperationError("operation log parent is unsafe or unreadable") from exc
    opened = os.fstat(descriptor)
    identity = (opened.st_dev, opened.st_ino)
    if (
        not stat.S_ISDIR(lexical.st_mode)
        or stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or (lexical.st_dev, lexical.st_ino) != identity
    ):
        os.close(descriptor)
        raise OperationError("operation log parent must be an ordinary directory")
    return descriptor, identity


def _verify_parent(root: Path, descriptor: int, identity: _FileIdentity) -> None:
    try:
        lexical = root.lstat()
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise OperationError("operation log parent changed during append") from exc
    if (
        not stat.S_ISDIR(lexical.st_mode)
        or stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or (lexical.st_dev, lexical.st_ino) != identity
        or (opened.st_dev, opened.st_ino) != identity
    ):
        raise OperationError("operation log parent changed during append")


def _read_preimage(parent_fd: int) -> tuple[str, int, _StableIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        lexical = os.stat("log.md", dir_fd=parent_fd, follow_symlinks=False)
        if not _ordinary_single_file(lexical):
            raise OperationError("operation log must be a single-link ordinary file")
        descriptor = os.open("log.md", flags, dir_fd=parent_fd)
    except OperationError:
        raise
    except OSError as exc:
        raise OperationError("operation log is unsafe or unreadable") from exc
    try:
        before = os.fstat(descriptor)
        if not _ordinary_single_file(before) or _stable_identity(
            before
        ) != _stable_identity(lexical):
            raise OperationError("operation log changed while being opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _stable_identity(after) != _stable_identity(before):
            raise OperationError("operation log changed while being read")
        try:
            text = b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OperationError("operation log must be UTF-8") from exc
        return text, descriptor, _stable_identity(before)
    except BaseException:
        os.close(descriptor)
        raise


def _verify_preimage(
    parent_fd: int, descriptor: int, identity: _StableIdentity
) -> None:
    try:
        opened = os.fstat(descriptor)
        named = os.stat("log.md", dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise OperationError("operation log changed during append") from exc
    if (
        not _ordinary_single_file(opened)
        or not _ordinary_single_file(named)
        or _stable_identity(opened) != identity
        or _stable_identity(named) != identity
    ):
        raise OperationError("operation log changed during append")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short operation log write")
        view = view[written:]


def _rename_noreplace(
    parent_fd: int,
    source: str,
    destination: str,
    bound_descriptor: int | None = None,
    expected_stable: _StableIdentity | None = None,
) -> None:
    """Rename within a bound directory without replacing an existing name."""

    if os.name != "posix":
        raise OperationError("safe operation log replacement is unsupported")
    if bound_descriptor is not None:
        if expected_stable is None or _stable_identity(
            os.fstat(bound_descriptor)
        ) != expected_stable:
            raise OperationError("operation log changed during append")
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise OperationError("safe operation log replacement is unsupported") from exc
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        parent_fd,
        os.fsencode(source),
        parent_fd,
        os.fsencode(destination),
        1,  # RENAME_NOREPLACE
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def _link_descriptor_noreplace_exact(
    parent_fd: int, descriptor: int, destination: str
) -> None:
    """Install the exact open inode at an absent name in a bound directory."""

    if os.name != "posix":
        raise OperationError("safe operation log replacement is unsupported")
    try:
        linkat = ctypes.CDLL(None, use_errno=True).linkat
    except AttributeError as exc:
        raise OperationError("safe operation log replacement is unsupported") from exc
    linkat.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    )
    linkat.restype = ctypes.c_int
    descriptor_path = os.fsencode(f"/proc/self/fd/{descriptor}")
    result = linkat(
        -100,  # AT_FDCWD
        descriptor_path,
        parent_fd,
        os.fsencode(destination),
        0x400,  # AT_SYMLINK_FOLLOW
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def _link_descriptor_noreplace(
    parent_fd: int, descriptor: int, destination: str
) -> None:
    _link_descriptor_noreplace_exact(parent_fd, descriptor, destination)


def _restore_noreplace(parent_fd: int, source: str, destination: str) -> bool:
    try:
        _rename_noreplace(parent_fd, source, destination)
    except (OSError, OperationError):
        return False
    return True


def _cleanup_owned(parent_fd: int, name: str, identity: _FileIdentity | None) -> None:
    if identity is None:
        return
    quarantine = ".log.md.cleanup-" + secrets.token_hex(16)
    try:
        _rename_noreplace(parent_fd, name, quarantine)
    except (OSError, OperationError):
        return
    try:
        moved = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return
    if (moved.st_dev, moved.st_ino) != identity:
        _restore_noreplace(parent_fd, quarantine, name)
        return
    # POSIX has no portable conditional unlink-by-inode operation.  Keep the
    # identity-verified quarantine rather than risk unlinking a substituted
    # external file after a separable pathname check.


def _verify_temp(
    parent_fd: int,
    name: str,
    descriptor: int,
    identity: _FileIdentity,
    expected: bytes,
    expected_stable: _StableIdentity | None = None,
) -> _StableIdentity:
    try:
        before = os.fstat(descriptor)
        named = (
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if name
            else None
        )
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise OperationError("operation log temporary file changed") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != (1 if name else 0)
        or (before.st_dev, before.st_ino) != identity
        or _stable_identity(after) != _stable_identity(before)
        or (
            expected_stable is not None
            and _stable_identity(before) != expected_stable
        )
        or (
            named is not None
            and (
                not _ordinary_single_file(named)
                or (named.st_dev, named.st_ino) != identity
            )
        )
        or b"".join(chunks) != expected
    ):
        raise OperationError("operation log temporary file changed")
    return _stable_identity(after)


def _verify_moved_preimage(
    parent_fd: int,
    name: str,
    descriptor: int,
    expected_identity: _StableIdentity,
    expected: bytes,
    *,
    allow_rename_ctime: bool = False,
) -> _StableIdentity:
    try:
        before = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise OperationError("operation log changed during append") from exc
    opened_identity = _stable_identity(after)
    unchanged_across_rename = (
        opened_identity[:5] + opened_identity[6:]
        == expected_identity[:5] + expected_identity[6:]
    )
    if (
        not _ordinary_single_file(before)
        or not _ordinary_single_file(named)
        or _stable_identity(before) != opened_identity
        or opened_identity != _stable_identity(named)
        or (
            opened_identity != expected_identity
            and not (allow_rename_ctime and unchanged_across_rename)
        )
        or b"".join(chunks) != expected
    ):
        raise OperationError("operation log changed during append")
    return opened_identity


def _read_open_stable(descriptor: int) -> tuple[bytes, _StableIdentity]:
    before = os.fstat(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    after = os.fstat(descriptor)
    if _stable_identity(after) != _stable_identity(before):
        raise OperationError("operation file changed while being read")
    return b"".join(chunks), _stable_identity(after)


def _read_exact_open_stable(
    descriptor: int, size: int
) -> tuple[bytes, _StableIdentity]:
    before = os.fstat(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = size + 1
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    after = os.fstat(descriptor)
    if _stable_identity(after) != _stable_identity(before):
        raise OperationError("operation file changed while being read")
    return b"".join(chunks), _stable_identity(after)


def _verify_installed_exact(
    parent_fd: int,
    descriptor: int,
    expected_stable: _StableIdentity | None,
    expected: bytes,
    *,
    allow_link_transition: bool = False,
) -> _StableIdentity:
    try:
        before = os.fstat(descriptor)
        named = os.stat("log.md", dir_fd=parent_fd, follow_symlinks=False)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise OperationError("installed operation log changed") from exc
    installed = _stable_identity(after)
    valid_link_transition = (
        expected_stable is not None
        and installed[:5] == expected_stable[:5]
        and expected_stable[6] == 0
        and installed[6] == 1
    )
    if (
        not _ordinary_single_file(before)
        or not _ordinary_single_file(named)
        or _stable_identity(before) != installed
        or _stable_identity(named) != installed
        or (
            expected_stable is not None
            and installed != expected_stable
            and not (allow_link_transition and valid_link_transition)
        )
        or b"".join(chunks) != expected
    ):
        raise OperationError("installed operation log changed")
    return installed


def _verify_installed(
    parent_fd: int,
    descriptor: int,
    expected_stable: _StableIdentity | None,
    expected: bytes,
    *,
    allow_link_transition: bool = False,
) -> _StableIdentity:
    return _verify_installed_exact(
        parent_fd,
        descriptor,
        expected_stable,
        expected,
        allow_link_transition=allow_link_transition,
    )


def _restore_preimage_copy(
    parent_fd: int,
    backup_descriptor: int,
    backup_stable_identity: _StableIdentity,
    backup_expected: bytes,
) -> bool:
    try:
        data, current = _read_exact_open_stable(
            backup_descriptor, len(backup_expected)
        )
        if (
            current != backup_stable_identity
            or data != backup_expected
        ):
            return False
        temporary_flag = getattr(os, "O_TMPFILE", 0)
        if not temporary_flag:
            return False
        descriptor = os.open(
            ".",
            os.O_RDWR | temporary_flag | getattr(os, "O_CLOEXEC", 0),
            0o644,
            dir_fd=parent_fd,
        )
        try:
            _write_all(descriptor, backup_expected)
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino)
            stable = _verify_temp(
                parent_fd, "", descriptor, identity, backup_expected
            )
            _link_descriptor_noreplace_exact(parent_fd, descriptor, "log.md")
            _verify_installed_exact(
                parent_fd,
                descriptor,
                stable,
                backup_expected,
                allow_link_transition=True,
            )
        finally:
            os.close(descriptor)
    except (OSError, OperationError):
        return False
    return True


def _verified_renamed_identity(
    descriptor: int,
    original: _StableIdentity,
    expected: bytes,
) -> _StableIdentity | None:
    try:
        data, current = _read_exact_open_stable(descriptor, len(expected))
    except (OSError, OperationError):
        return None
    if (
        data != expected
        or current[:5] + current[6:] != original[:5] + original[6:]
    ):
        return None
    return current


def _restore_from_initial_preimage(
    parent_fd: int,
    descriptor: int,
    original: _StableIdentity,
    expected: bytes,
) -> bool:
    try:
        data, current = _read_open_stable(descriptor)
    except (OSError, OperationError):
        return False
    unchanged = current[:5] + current[6:] == original[:5] + original[6:]
    if data != expected or not unchanged:
        return False
    return _restore_preimage_copy(parent_fd, descriptor, current, expected)


def append_operation(path: Path, change: OperationChange, *, root: Path) -> Path:
    """Atomically append one record to exactly ``root/log.md``."""

    path = Path(path)
    root = Path(root)
    if path != root / "log.md":
        raise OperationError("operation log path must be exactly root/log.md")
    parent_fd, parent_identity = _open_parent(root)
    preimage_fd = -1
    temp_fd = -1
    temp_name = ""
    temp_identity: _FileIdentity | None = None
    backup_name = ".log.md.backup-" + secrets.token_hex(16)
    backup_identity: _FileIdentity | None = None
    installed_stable_identity: _StableIdentity | None = None
    backup_stable_identity: _StableIdentity | None = None
    promoted = False
    try:
        _verify_parent(root, parent_fd, parent_identity)
        text, preimage_fd, preimage_identity = _read_preimage(parent_fd)
        preimage_bytes = text.encode("utf-8")
        updated = append_operation_text(text, change).encode("utf-8")
        temporary_flag = getattr(os, "O_TMPFILE", 0)
        if not temporary_flag:
            raise OperationError("safe operation log replacement is unsupported")
        flags = os.O_RDWR | temporary_flag | getattr(os, "O_CLOEXEC", 0)
        try:
            temp_fd = os.open(".", flags, 0o644, dir_fd=parent_fd)
            temp_metadata = os.fstat(temp_fd)
            temp_identity = (temp_metadata.st_dev, temp_metadata.st_ino)
            _write_all(temp_fd, updated)
            os.fsync(temp_fd)
            temp_stable_identity = _verify_temp(
                parent_fd, temp_name, temp_fd, temp_identity, updated
            )
            _verify_parent(root, parent_fd, parent_identity)
            _verify_preimage(parent_fd, preimage_fd, preimage_identity)
            _rename_noreplace(
                parent_fd,
                "log.md",
                backup_name,
                preimage_fd,
                preimage_identity,
            )
            try:
                backup_stable_identity = _verify_moved_preimage(
                    parent_fd,
                    backup_name,
                    preimage_fd,
                    preimage_identity,
                    preimage_bytes,
                    allow_rename_ctime=True,
                )
                backup_identity = backup_stable_identity[:2]
            except OperationError:
                recovered = _restore_from_initial_preimage(
                    parent_fd, preimage_fd, preimage_identity, preimage_bytes
                )
                backup_identity = None
                if not recovered:
                    raise OperationError(
                        "operation log recovery failed after moved verification"
                    )
                raise
            try:
                _verify_parent(root, parent_fd, parent_identity)
                _verify_temp(
                    parent_fd,
                    temp_name,
                    temp_fd,
                    temp_identity,
                    updated,
                    temp_stable_identity,
                )
                _link_descriptor_noreplace(parent_fd, temp_fd, "log.md")
                promoted = True
                installed_stable_identity = _verify_installed(
                    parent_fd,
                    temp_fd,
                    temp_stable_identity,
                    updated,
                    allow_link_transition=True,
                )
                os.fsync(parent_fd)
                _verify_installed(
                    parent_fd,
                    temp_fd,
                    installed_stable_identity,
                    updated,
                )
                _verify_parent(root, parent_fd, parent_identity)
                os.close(temp_fd)
                temp_fd = -1
            except BaseException:
                if not promoted and backup_identity is not None:
                    if _restore_preimage_copy(
                        parent_fd,
                        preimage_fd,
                        backup_stable_identity,
                        preimage_bytes,
                    ):
                        _cleanup_owned(
                            parent_fd, backup_name, backup_identity
                        )
                        backup_identity = None
                raise
            if backup_identity is not None:
                _cleanup_owned(parent_fd, backup_name, backup_identity)
                backup_identity = None
        except OperationError:
            raise
        except OSError as exc:
            raise OperationError("cannot write operation log") from exc
    finally:
        if temp_fd >= 0:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        if preimage_fd >= 0:
            os.close(preimage_fd)
        os.close(parent_fd)
    return path

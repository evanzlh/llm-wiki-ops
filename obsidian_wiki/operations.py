"""Canonical append-only operation log for portable repositories."""

from __future__ import annotations

import errno
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional, Tuple


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


def _contains_line_break(value: str) -> bool:
    return any(character in value for character in "\r\n\u0085\u2028\u2029")


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
        or _contains_line_break(raw)
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


def _ordinary_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    )


def _read_all(descriptor: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _open_parent(root: Path) -> tuple[Optional[int], _FileIdentity]:
    try:
        lexical = root.lstat()
    except OSError as exc:
        raise OperationError("operation log parent is unsafe or unreadable") from exc
    if not _ordinary_directory(lexical):
        raise OperationError("operation log parent must be an ordinary directory")
    identity = (lexical.st_dev, lexical.st_ino)
    if os.name == "nt":
        return None, identity

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(root, flags)
        opened = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise OperationError("operation log parent is unsafe or unreadable") from exc
    if not _ordinary_directory(opened) or (opened.st_dev, opened.st_ino) != identity:
        os.close(descriptor)
        raise OperationError("operation log parent changed while opening")
    return descriptor, identity


def _verify_parent(
    root: Path, descriptor: Optional[int], identity: _FileIdentity
) -> None:
    try:
        lexical = root.lstat()
        opened = os.fstat(descriptor) if descriptor is not None else lexical
    except OSError as exc:
        raise OperationError("operation log parent changed during append") from exc
    if (
        not _ordinary_directory(lexical)
        or not _ordinary_directory(opened)
        or (lexical.st_dev, lexical.st_ino) != identity
        or (opened.st_dev, opened.st_ino) != identity
    ):
        raise OperationError("operation log parent changed during append")


def _open_preimage(path: Path) -> tuple[int, _StableIdentity, int, bytes, str]:
    descriptor = None
    try:
        lexical = path.lstat()
        if not _ordinary_single_file(lexical):
            raise OperationError("operation log must be an ordinary single-link file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not _ordinary_single_file(before) or _stable_identity(before) != _stable_identity(
            lexical
        ):
            raise OperationError("operation log changed while opening")
        data = _read_all(descriptor)
        after = os.fstat(descriptor)
    except OperationError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise OperationError("operation log is unsafe or unreadable") from exc
    if _stable_identity(after) != _stable_identity(before):
        os.close(descriptor)
        raise OperationError("operation log changed while reading")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        os.close(descriptor)
        raise OperationError("operation log is not UTF-8") from exc
    return (
        descriptor,
        _stable_identity(before),
        stat.S_IMODE(before.st_mode),
        data,
        text,
    )


def _verify_preimage(
    path: Path,
    descriptor: int,
    expected_identity: _StableIdentity,
    expected_data: bytes,
    expected_mode: int,
) -> None:
    try:
        lexical = path.lstat()
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = _read_all(descriptor)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise OperationError("operation log changed during append") from exc
    if (
        not _ordinary_single_file(lexical)
        or not _ordinary_single_file(before)
        or _stable_identity(lexical) != expected_identity
        or _stable_identity(before) != expected_identity
        or _stable_identity(after) != expected_identity
        or stat.S_IMODE(before.st_mode) != expected_mode
        or data != expected_data
    ):
        raise OperationError("operation log changed during append")


def _temp_name() -> str:
    return ".log.md.tmp-" + secrets.token_hex(16)


def _open_temp(
    root: Path, parent_descriptor: Optional[int], mode: int
) -> tuple[str, Path, int]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    for _ in range(32):
        name = _temp_name()
        path = root / name
        try:
            if parent_descriptor is None:
                descriptor = os.open(path, flags, mode)
            else:
                descriptor = os.open(name, flags, mode, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        except OSError as exc:
            raise OperationError("cannot create operation log temporary file") from exc
        try:
            created_identity = _stable_identity(os.fstat(descriptor))
        except OSError as exc:
            retry_identity = None
            try:
                retry_identity = _stable_identity(os.fstat(descriptor))
            except OSError:
                pass
            _cleanup_owned_temp(path, name, parent_descriptor, retry_identity)
            os.close(descriptor)
            raise OperationError(
                "cannot inspect operation log temporary file"
            ) from exc
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, mode)
        except OSError as exc:
            _cleanup_owned_temp(path, name, parent_descriptor, created_identity)
            os.close(descriptor)
            raise OperationError("cannot prepare operation log temporary file") from exc
        return name, path, descriptor
    raise OperationError("cannot allocate operation log temporary file")


def _stat_temp(
    path: Path, name: str, parent_descriptor: Optional[int]
) -> os.stat_result:
    if parent_descriptor is None:
        return path.lstat()
    return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)


def _verify_temp(
    path: Path,
    name: str,
    parent_descriptor: Optional[int],
    descriptor: int,
    expected_identity: _StableIdentity,
    expected_data: bytes,
    expected_mode: int,
) -> None:
    try:
        lexical = _stat_temp(path, name, parent_descriptor)
        before = os.fstat(descriptor)
        if (
            not _ordinary_single_file(lexical)
            or not _ordinary_single_file(before)
            or _stable_identity(lexical) != expected_identity
            or _stable_identity(before) != expected_identity
            or stat.S_IMODE(before.st_mode) != expected_mode
        ):
            raise OperationError("operation log temporary file changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = _read_all(descriptor)
        after = os.fstat(descriptor)
    except OperationError:
        raise
    except OSError as exc:
        raise OperationError("operation log temporary file changed") from exc
    if data != expected_data or _stable_identity(after) != expected_identity:
        raise OperationError("operation log temporary file changed")


def _cleanup_owned_temp(
    path: Path,
    name: str,
    parent_descriptor: Optional[int],
    expected_identity: Optional[_StableIdentity],
) -> None:
    if expected_identity is None:
        return
    try:
        lexical = _stat_temp(path, name, parent_descriptor)
        if _stable_identity(lexical) != expected_identity:
            return
        if parent_descriptor is None:
            path.unlink()
        else:
            os.unlink(name, dir_fd=parent_descriptor)
    except OSError:
        return


def _write_fully(descriptor: int, data: bytes) -> None:
    position = 0
    while position < len(data):
        written = os.write(descriptor, data[position:])
        if written <= 0:
            raise OSError("short write")
        position += written


def _sync_parent(descriptor: Optional[int]) -> None:
    if descriptor is None:
        return
    try:
        os.fsync(descriptor)
    except OSError as exc:
        unsupported = {
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        }
        if exc.errno in unsupported:
            return
        raise


def _verify_installed(
    path: Path,
    descriptor: Optional[int],
    expected_data: bytes,
    expected_mode: int,
) -> None:
    opened_here = False
    try:
        lexical = path.lstat()
        if descriptor is None:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor = os.open(path, flags)
            opened_here = True
        before = os.fstat(descriptor)
        if (
            not _ordinary_single_file(lexical)
            or not _ordinary_single_file(before)
            or (lexical.st_dev, lexical.st_ino) != (before.st_dev, before.st_ino)
            or stat.S_IMODE(before.st_mode) != expected_mode
        ):
            raise OperationError("installed operation log changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = _read_all(descriptor)
        after = os.fstat(descriptor)
    except OperationError:
        raise
    except OSError as exc:
        raise OperationError("cannot verify installed operation log") from exc
    finally:
        if opened_here and descriptor is not None:
            os.close(descriptor)
    if _stable_identity(after) != _stable_identity(before) or data != expected_data:
        raise OperationError("installed operation log changed")
    try:
        parsed = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OperationError("installed operation log is not UTF-8") from exc
    parse_operation_log(parsed)


def append_operation(path: Path, change: OperationChange, *, root: Path) -> Path:
    """Atomically append one canonical record to root/log.md.

    Cooperative writers are serialized by the repository transaction lock. This
    function still detects target and parent changes before the atomic replacement.
    """

    path = Path(path)
    root = Path(root)
    if path != root / "log.md":
        raise OperationError("operation log path must be exactly root/log.md")

    parent_descriptor: Optional[int] = None
    preimage_descriptor: Optional[int] = None
    temp_descriptor: Optional[int] = None
    temp_name = ""
    temp_path = root / ".log.md.tmp-unallocated"
    temp_identity: Optional[_StableIdentity] = None
    replaced = False
    try:
        parent_descriptor, parent_identity = _open_parent(root)
        (
            preimage_descriptor,
            preimage_identity,
            mode,
            preimage_data,
            text,
        ) = _open_preimage(path)
        updated_text = append_operation_text(text, change)
        updated = updated_text.encode("utf-8")

        temp_name, temp_path, temp_descriptor = _open_temp(
            root, parent_descriptor, mode
        )
        _write_fully(temp_descriptor, updated)
        os.fsync(temp_descriptor)
        temp_identity = _stable_identity(os.fstat(temp_descriptor))

        _verify_temp(
            temp_path,
            temp_name,
            parent_descriptor,
            temp_descriptor,
            temp_identity,
            updated,
            mode,
        )
        _verify_parent(root, parent_descriptor, parent_identity)
        _verify_preimage(
            path,
            preimage_descriptor,
            preimage_identity,
            preimage_data,
            mode,
        )
        _verify_temp(
            temp_path,
            temp_name,
            parent_descriptor,
            temp_descriptor,
            temp_identity,
            updated,
            mode,
        )

        # Windows generally prevents replacing an open file. Both exact identities
        # were checked immediately above, so close only for the replacement syscall.
        if os.name == "nt":
            os.close(preimage_descriptor)
            preimage_descriptor = None
            os.close(temp_descriptor)
            temp_descriptor = None
        os.replace(temp_path, path)
        replaced = True
        _sync_parent(parent_descriptor)
        _verify_installed(path, temp_descriptor, updated, mode)
        return path
    except OperationError:
        raise
    except OSError as exc:
        raise OperationError("cannot append operation log") from exc
    finally:
        if not replaced and temp_name:
            if temp_descriptor is not None:
                try:
                    temp_identity = _stable_identity(os.fstat(temp_descriptor))
                except OSError:
                    pass
            _cleanup_owned_temp(
                temp_path, temp_name, parent_descriptor, temp_identity
            )
        for descriptor in (temp_descriptor, preimage_descriptor, parent_descriptor):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

"""Canonical append-only operation log for portable repositories."""

from __future__ import annotations

import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


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
_FileIdentity = tuple[int, int]
_StableIdentity = tuple[int, int, int, int, int, int]


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise OperationError(
            "operation completed_at must be a UTC timestamp ending in Z"
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


def _cleanup_temp(parent_fd: int, name: str, identity: _FileIdentity | None) -> None:
    if identity is None:
        return
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) == identity:
            os.unlink(name, dir_fd=parent_fd)
    except OSError:
        pass


def _verify_installed(parent_fd: int, identity: _FileIdentity) -> None:
    text, descriptor, installed = _read_preimage(parent_fd)
    try:
        if installed[:2] != identity:
            raise OperationError("operation log target changed after replacement")
        parse_operation_log(text)
    finally:
        os.close(descriptor)


def append_operation(path: Path, change: OperationChange, *, root: Path) -> Path:
    """Atomically append one record to exactly ``root/log.md``."""

    path = Path(path)
    root = Path(root)
    if path != root / "log.md":
        raise OperationError("operation log path must be exactly root/log.md")
    parent_fd, parent_identity = _open_parent(root)
    preimage_fd = -1
    temp_fd = -1
    temp_name = ".log.md.tmp-" + secrets.token_hex(16)
    temp_identity: _FileIdentity | None = None
    replaced = False
    try:
        _verify_parent(root, parent_fd, parent_identity)
        text, preimage_fd, preimage_identity = _read_preimage(parent_fd)
        updated = append_operation_text(text, change).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            temp_fd = os.open(temp_name, flags, 0o644, dir_fd=parent_fd)
            temp_metadata = os.fstat(temp_fd)
            temp_identity = (temp_metadata.st_dev, temp_metadata.st_ino)
            _write_all(temp_fd, updated)
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = -1
            _verify_parent(root, parent_fd, parent_identity)
            _verify_preimage(parent_fd, preimage_fd, preimage_identity)
            os.replace(
                temp_name,
                "log.md",
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            replaced = True
            os.fsync(parent_fd)
            _verify_installed(parent_fd, temp_identity)
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
        if not replaced:
            _cleanup_temp(parent_fd, temp_name, temp_identity)
        if preimage_fd >= 0:
            os.close(preimage_fd)
        os.close(parent_fd)
    return path

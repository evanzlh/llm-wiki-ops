"""Immutable operation journal pages for portable repositories."""

from __future__ import annotations

import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from .frontmatter import FrontmatterError, parse_frontmatter


class OperationError(ValueError):
    """Raised when an operation page or operation change is invalid."""


@dataclass(frozen=True)
class OperationChange:
    transaction_id: str
    completed_at: str
    source_ids: tuple[str, ...]
    created: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]


_TRANSACTION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SUFFIX_RE = re.compile(r"[0-9a-f]{4,}")
_FILENAME_RE = re.compile(r"(\d{8}T\d{6}Z)-([0-9a-f]{4,})\.md")
_SAFE_SCALAR_RE = re.compile(r"[A-Za-z0-9_./:@+-]+")
_KNOWLEDGE_CATEGORIES = frozenset(
    {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
)
_FRONTMATTER_FIELDS = frozenset(
    {
        "title",
        "category",
        "tags",
        "sources",
        "created",
        "updated",
        "transaction_id",
        "completed_at",
    }
)
_SUPPORTS_DIR_FD = all(
    function in os.supports_dir_fd
    for function in (os.link, os.mkdir, os.open, os.rename, os.stat, os.unlink)
)
_FileIdentity = tuple[int, int]


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
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
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
        return raw
    if (
        posix.suffix != ".md"
        or not posix.parts
        or posix.parts[0] not in _KNOWLEDGE_CATEGORIES
        or posix.parts[:2] == ("journal", "operations")
        or any(part.startswith(".") for part in posix.parts)
    ):
        raise OperationError(f"operation {label} must be a safe knowledge page path")
    return raw


def _canonical_change(change: OperationChange) -> OperationChange:
    if not isinstance(change, OperationChange):
        raise OperationError("operation change has the wrong type")
    if _TRANSACTION_ID_RE.fullmatch(change.transaction_id) is None:
        raise OperationError("operation transaction_id is invalid")
    _timestamp(change.completed_at)
    sources = tuple(
        sorted(
            {
                _safe_relative(item, "source ID", source=True)
                for item in change.source_ids
            }
        )
    )
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
        except OperationError as exc:
            raise OperationError(f"operation page path is invalid: {exc}") from exc
    created, updated, removed = groups
    if (
        set(created) & set(updated)
        or set(created) & set(removed)
        or set(updated) & set(removed)
    ):
        raise OperationError("operation change lists must be disjoint")
    return OperationChange(
        transaction_id=change.transaction_id,
        completed_at=change.completed_at,
        source_ids=sources,
        created=created,
        updated=updated,
        removed=removed,
    )


def _yaml_scalar(value: str) -> str:
    if _SAFE_SCALAR_RE.fullmatch(value):
        return value
    if any(ord(char) < 32 for char in value):
        raise OperationError("operation value contains a control character")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _section(title: str, pages: tuple[str, ...]) -> str:
    if not pages:
        return f"## {title}\n\n- None"
    links = "\n".join(
        f"- [[{PurePosixPath(page).with_suffix('').as_posix()}]]" for page in pages
    )
    return f"## {title}\n\n{links}"


def render_operation(change: OperationChange) -> str:
    """Render one canonical operation page without writing it."""
    change = _canonical_change(change)
    completed = _timestamp(change.completed_at)
    day = completed.strftime("%Y-%m-%d")
    source_lines = "\n".join(f"  - {_yaml_scalar(item)}" for item in change.source_ids)
    return (
        "---\n"
        f"title: Operation {change.transaction_id}\n"
        "category: journal\n"
        "tags:\n"
        "  - operation\n"
        "sources:\n"
        f"{source_lines}\n"
        f"created: {day}\n"
        f"updated: {day}\n"
        f"transaction_id: {change.transaction_id}\n"
        f"completed_at: {change.completed_at}\n"
        "---\n"
        f"# Operation {change.transaction_id}\n\n"
        f"{_section('Created', change.created)}\n\n"
        f"{_section('Updated', change.updated)}\n\n"
        f"{_section('Removed', change.removed)}\n"
    )


def operation_path(vault: Path, change: OperationChange, *, suffix: str) -> Path:
    """Return the canonical immutable path for an operation page."""
    change = _canonical_change(change)
    if _SUFFIX_RE.fullmatch(suffix) is None:
        raise OperationError(
            "operation suffix must be four or more lowercase hex characters"
        )
    completed = _timestamp(change.completed_at)
    filename = completed.strftime("%Y%m%dT%H%M%SZ") + f"-{suffix}.md"
    return (
        Path(vault)
        / "journal"
        / "operations"
        / completed.strftime("%Y")
        / completed.strftime("%m")
        / filename
    )


def _ordinary_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise OperationError("cannot sync operation directory") from exc


def _ensure_directory(path: Path, root: Path) -> tuple[tuple[Path, _FileIdentity], ...]:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise OperationError("operation directory escapes the vault") from exc
    current = root
    if not _ordinary_directory(current):
        raise OperationError("operation vault must be an ordinary directory")
    identities: list[tuple[Path, _FileIdentity]] = []
    root_metadata = current.lstat()
    identities.append((current, (root_metadata.st_dev, root_metadata.st_ino)))
    for part in relative.parts:
        current = current / part
        if not current.exists() and not current.is_symlink():
            try:
                current.mkdir()
                _fsync_directory(current.parent)
            except OSError as exc:
                raise OperationError("cannot create operation directory") from exc
        if not _ordinary_directory(current):
            raise OperationError("operation directory must not be a symlink")
        metadata = current.lstat()
        identities.append((current, (metadata.st_dev, metadata.st_ino)))
    return tuple(identities)


def _verify_directory_identities(
    identities: tuple[tuple[Path, _FileIdentity], ...],
) -> None:
    for path, expected in identities:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise OperationError("operation directory changed during write") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
            or (metadata.st_dev, metadata.st_ino) != expected
        ):
            raise OperationError("operation directory changed during write")


def _open_directory_at(parent_fd: int | None, path: str | Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        if parent_fd is None:
            descriptor = os.open(path, flags)
        else:
            descriptor = os.open(path, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise OperationError("operation directory is unsafe or unreadable") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise OperationError("operation directory must be ordinary")
    return descriptor


def _open_operation_parent(
    vault: Path, relative_parts: tuple[str, ...]
) -> tuple[int, tuple[tuple[Path, _FileIdentity], ...]]:
    descriptor = _open_directory_at(None, vault)
    root_metadata = os.fstat(descriptor)
    current = vault
    identities: list[tuple[Path, _FileIdentity]] = [
        (current, (root_metadata.st_dev, root_metadata.st_ino))
    ]
    try:
        for part in relative_parts:
            try:
                os.mkdir(part, mode=0o755, dir_fd=descriptor)
            except FileExistsError:
                pass
            except OSError as exc:
                raise OperationError("cannot create operation directory") from exc
            else:
                os.fsync(descriptor)
            child = _open_directory_at(descriptor, part)
            os.close(descriptor)
            descriptor = child
            current = current / part
            child_metadata = os.fstat(descriptor)
            identities.append((current, (child_metadata.st_dev, child_metadata.st_ino)))
        return descriptor, tuple(identities)
    except BaseException:
        os.close(descriptor)
        raise


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short operation write")
        view = view[written:]


def _preserve_quarantine_at(parent_fd: int, quarantine: str, original: str) -> None:
    try:
        os.link(
            quarantine,
            original,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        pass


def _cleanup_owned_at(
    parent_fd: int,
    name: str,
    identity: _FileIdentity,
    cleanup_fd: int,
) -> None:
    for _attempt in range(3):
        try:
            names = sorted(os.listdir(parent_fd))
        except OSError:
            return
        candidates = [name, *(item for item in names if item != name)]
        for candidate in candidates:
            try:
                current = os.stat(candidate, dir_fd=parent_fd, follow_symlinks=False)
            except OSError:
                continue
            if (current.st_dev, current.st_ino) != identity:
                continue
            quarantine = ".operation-cleanup-" + secrets.token_hex(16)
            try:
                os.rename(
                    candidate,
                    quarantine,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                moved = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            except OSError:
                continue
            if (moved.st_dev, moved.st_ino) != identity:
                _preserve_quarantine_at(parent_fd, quarantine, candidate)
                continue
            tombstone = ".operation-cleanup-" + secrets.token_hex(16) + ".tmp"
            try:
                os.rename(
                    quarantine,
                    tombstone,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=cleanup_fd,
                )
                quarantined = os.stat(
                    tombstone, dir_fd=cleanup_fd, follow_symlinks=False
                )
            except OSError:
                return
            if (quarantined.st_dev, quarantined.st_ino) == identity:
                try:
                    os.fsync(parent_fd)
                    os.fsync(cleanup_fd)
                except OSError:
                    pass
                return


def _write_exclusive_at(
    parent_fd: int, name: str, data: bytes, cleanup_fd: int
) -> _FileIdentity:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, 0o644, dir_fd=parent_fd)
    identity: _FileIdentity | None = None
    try:
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        _write_all(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.fsync(parent_fd)
        return identity
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if identity is not None:
            _cleanup_owned_at(parent_fd, name, identity, cleanup_fd)
        raise


def _cleanup_owned_path(
    target: Path,
    identity: _FileIdentity,
    cleanup_root: Path,
) -> None:
    parent = target.parent
    for _attempt in range(3):
        try:
            candidates = sorted(parent.iterdir(), key=lambda item: item.name)
        except OSError:
            return
        for candidate in candidates:
            try:
                current = candidate.lstat()
            except OSError:
                continue
            if (current.st_dev, current.st_ino) != identity:
                continue
            quarantine = parent / (".operation-cleanup-" + secrets.token_hex(16))
            try:
                candidate.rename(quarantine)
                moved = quarantine.lstat()
            except OSError:
                continue
            if (moved.st_dev, moved.st_ino) != identity:
                if not candidate.exists() and not candidate.is_symlink():
                    try:
                        os.link(quarantine, candidate, follow_symlinks=False)
                    except OSError:
                        pass
                continue
            tombstone = cleanup_root / (
                ".operation-cleanup-" + secrets.token_hex(16) + ".tmp"
            )
            try:
                quarantine.rename(tombstone)
                quarantined = tombstone.lstat()
            except OSError:
                return
            if (quarantined.st_dev, quarantined.st_ino) == identity:
                try:
                    _fsync_directory(parent)
                    if cleanup_root != parent:
                        _fsync_directory(cleanup_root)
                except OperationError:
                    pass
                return


def _write_exclusive_path(
    target: Path, data: bytes, vault: Path, cleanup_root: Path
) -> None:
    directory_identities = _ensure_directory(target.parent, vault)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(target, flags, 0o644)
    identity: _FileIdentity | None = None
    try:
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        _verify_directory_identities(directory_identities)
        _write_all(descriptor, data)
        os.fsync(descriptor)
        _verify_directory_identities(directory_identities)
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(target.parent)
        _verify_directory_identities(directory_identities)
        current = target.lstat()
        if (current.st_dev, current.st_ino) != identity:
            raise OperationError("operation target changed during write")
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if identity is not None:
            _cleanup_owned_path(target, identity, cleanup_root)
        raise


def _read_operation_text(path: Path) -> str:
    try:
        lexical = path.lstat()
    except OSError as exc:
        raise OperationError("operation page is missing") from exc
    if (
        not stat.S_ISREG(lexical.st_mode)
        or stat.S_ISLNK(lexical.st_mode)
        or bool(getattr(lexical, "st_file_attributes", 0) & 0x400)
        or lexical.st_nlink != 1
    ):
        raise OperationError("operation page must be a single-link ordinary file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OperationError("operation page is unsafe or unreadable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != (lexical.st_dev, lexical.st_ino)
        ):
            raise OperationError("operation page changed while being opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_nlink,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_nlink,
        ):
            raise OperationError("operation page changed while being read")
    finally:
        os.close(descriptor)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OperationError("operation page must be UTF-8") from exc


def write_operation(
    vault: Path,
    change: OperationChange,
    *,
    suffix: str | None = None,
    cleanup_root: Path | None = None,
) -> Path:
    """Create one immutable operation page, retrying filename collisions."""
    vault = Path(vault)
    resolved_cleanup_root = Path(cleanup_root) if cleanup_root is not None else vault
    _ensure_directory(resolved_cleanup_root, resolved_cleanup_root)
    data = render_operation(change).encode("utf-8")
    candidate_suffix = suffix
    for _attempt in range(128):
        resolved_suffix = candidate_suffix or secrets.token_hex(4)
        candidate_suffix = None
        target = operation_path(vault, change, suffix=resolved_suffix)
        try:
            if _SUPPORTS_DIR_FD:
                relative_parent = target.parent.relative_to(vault)
                parent_fd, directory_identities = _open_operation_parent(
                    vault, relative_parent.parts
                )
                cleanup_fd: int | None = None
                try:
                    cleanup_fd = _open_directory_at(None, resolved_cleanup_root)
                    identity = _write_exclusive_at(
                        parent_fd, target.name, data, cleanup_fd
                    )
                    try:
                        _verify_directory_identities(directory_identities)
                        current = target.lstat()
                    except (OSError, OperationError) as exc:
                        _cleanup_owned_at(parent_fd, target.name, identity, cleanup_fd)
                        raise OperationError(
                            "operation directory changed during write"
                        ) from exc
                    if (current.st_dev, current.st_ino) != identity:
                        _cleanup_owned_at(parent_fd, target.name, identity)
                        raise OperationError("operation target changed during write")
                finally:
                    try:
                        os.close(parent_fd)
                    except OSError:
                        pass
                    if cleanup_fd is not None:
                        try:
                            os.close(cleanup_fd)
                        except OSError:
                            pass
            else:
                _write_exclusive_path(target, data, vault, resolved_cleanup_root)
        except FileExistsError:
            continue
        except OSError as exc:
            raise OperationError("cannot write immutable operation page") from exc
        return target
    raise OperationError("cannot allocate a unique operation path")


def _frontmatter_body(text: str) -> str:
    lines = text.splitlines()
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise OperationError(
            "operation frontmatter closing delimiter is missing"
        ) from exc
    return "\n".join(lines[closing + 1 :]).strip() + "\n"


def _parse_sections(
    body: str, transaction_id: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    expected_header = f"# Operation {transaction_id}\n\n"
    if not body.startswith(expected_header):
        raise OperationError("operation heading does not match transaction_id")
    remainder = body[len(expected_header) :]
    headings = ("Created", "Updated", "Removed")
    groups: list[tuple[str, ...]] = []
    for index, heading in enumerate(headings):
        prefix = f"## {heading}\n\n"
        if not remainder.startswith(prefix):
            raise OperationError(f"operation {heading.lower()} section is missing")
        remainder = remainder[len(prefix) :]
        next_heading = (
            f"\n\n## {headings[index + 1]}\n\n" if index + 1 < len(headings) else None
        )
        if next_heading is None:
            section_text = remainder.rstrip("\n")
            remainder = ""
        else:
            if next_heading not in remainder:
                raise OperationError(
                    f"operation {headings[index + 1].lower()} section is missing"
                )
            section_text, remainder = remainder.split(next_heading, 1)
            remainder = f"## {headings[index + 1]}\n\n" + remainder
        lines = section_text.splitlines()
        if lines == ["- None"]:
            groups.append(())
            continue
        pages: list[str] = []
        for line in lines:
            match = re.fullmatch(r"- \[\[([^\[\]|#]+)\]\]", line)
            if match is None:
                raise OperationError(f"operation {heading.lower()} entry is invalid")
            pages.append(
                _safe_relative(match.group(1) + ".md", f"{heading.lower()} path")
            )
        canonical = tuple(sorted(set(pages)))
        if tuple(pages) != canonical:
            raise OperationError(
                f"operation {heading.lower()} paths must be unique and sorted"
            )
        groups.append(canonical)
    if remainder:
        raise OperationError("operation contains unexpected trailing content")
    return groups[0], groups[1], groups[2]


def validate_operation(path: Path, *, vault: Path | None = None) -> OperationChange:
    """Validate one operation page and return its canonical change record."""
    path = Path(path)
    if vault is None:
        parts = path.parts
        matches = [
            index
            for index in range(len(parts) - 1)
            if parts[index : index + 2] == ("journal", "operations")
        ]
        if len(matches) != 1:
            raise OperationError("operation path must be below journal/operations")
        vault = Path(*parts[: matches[0]])
    vault = Path(vault)
    try:
        relative = path.relative_to(vault)
    except ValueError as exc:
        raise OperationError("operation path escapes the vault") from exc
    if relative.parts[:2] != ("journal", "operations") or len(relative.parts) != 5:
        raise OperationError("operation path must use journal/operations/YYYY/MM")
    try:
        parent_relative = path.parent.relative_to(vault)
    except ValueError as exc:
        raise OperationError("operation path escapes the vault") from exc
    current = vault
    if not _ordinary_directory(current):
        raise OperationError("operation vault is unsafe")
    for part in parent_relative.parts:
        current = current / part
        if not _ordinary_directory(current):
            raise OperationError("operation page path is unsafe")
    if path.is_symlink():
        raise OperationError("operation page path is unsafe")
    match = _FILENAME_RE.fullmatch(path.name)
    if match is None:
        raise OperationError("operation filename is invalid")
    try:
        text = _read_operation_text(path)
        parsed = parse_frontmatter(text)
    except (OperationError, FrontmatterError) as exc:
        raise OperationError(f"operation frontmatter is invalid: {exc}") from exc
    fields = set(parsed.scalars) | set(parsed.lists)
    if fields != _FRONTMATTER_FIELDS:
        raise OperationError("operation frontmatter fields are invalid")
    if parsed.scalars.get("category") != "journal" or parsed.lists.get("tags") != (
        "operation",
    ):
        raise OperationError("operation category or operation tag is invalid")
    if "sources" in parsed.scalars:
        raise OperationError("operation sources must be a list")
    transaction_id = parsed.scalars.get("transaction_id", "")
    completed_at = parsed.scalars.get("completed_at", "")
    completed = _timestamp(completed_at)
    if parsed.scalars.get("title") != f"Operation {transaction_id}":
        raise OperationError("operation title does not match transaction_id")
    day = completed.strftime("%Y-%m-%d")
    if parsed.scalars.get("created") != day or parsed.scalars.get("updated") != day:
        raise OperationError("operation created and updated dates are invalid")
    if relative.parts[2:4] != (completed.strftime("%Y"), completed.strftime("%m")):
        raise OperationError("operation directory does not match completed_at")
    if match.group(1) != completed.strftime("%Y%m%dT%H%M%SZ"):
        raise OperationError("operation filename timestamp does not match completed_at")
    created, updated, removed = _parse_sections(_frontmatter_body(text), transaction_id)
    change = _canonical_change(
        OperationChange(
            transaction_id=transaction_id,
            completed_at=completed_at,
            source_ids=parsed.lists.get("sources", ()),
            created=created,
            updated=updated,
            removed=removed,
        )
    )
    if render_operation(change) != text:
        raise OperationError("operation page is not canonical")
    return change

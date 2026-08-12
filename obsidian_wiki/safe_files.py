"""Safe filesystem boundaries for read-only vault scanners."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Collection


class UnsafeVaultError(RuntimeError):
    """Raised when a vault tree cannot be scanned without following unsafe entries."""


@dataclass(frozen=True)
class MarkdownFile:
    path: Path
    relative: str
    content: bytes
    mtime_ns: int

    def text(self, *, errors: str = "strict") -> str:
        try:
            return self.content.decode("utf-8", errors=errors)
        except UnicodeError as exc:
            raise ValueError(f"page is not valid UTF-8: {self.relative}") from exc


_SUPPORTS_BOUND_SCAN = (
    os.name == "posix"
    and bool(getattr(os, "O_DIRECTORY", 0))
    and bool(getattr(os, "O_NOFOLLOW", 0))
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.listdir in os.supports_fd
)


def _reparse(metadata: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & flag)


def _identity(metadata: os.stat_result, *, directory: bool) -> tuple[int, ...]:
    values = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_ctime_ns,
    )
    if directory:
        return values
    return (*values, metadata.st_size, metadata.st_mtime_ns, metadata.st_nlink)


def _unsafe(relative: str, reason: str) -> UnsafeVaultError:
    return UnsafeVaultError(f"unsafe vault entry {relative or '.'}: {reason}")


def _validate_path_ancestry(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise _unsafe(".", f"vault path component is unavailable: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode) or _reparse(metadata):
            raise _unsafe(".", f"vault path component is a symlink: {current}")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_bound_root(root: Path) -> int:
    descriptor = os.open(root.anchor, _directory_flags())
    try:
        for part in root.parts[1:]:
            try:
                observed = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise _unsafe(".", f"vault path component is unavailable: {part}") from exc
            if stat.S_ISLNK(observed.st_mode) or _reparse(observed):
                raise _unsafe(".", f"vault path component is a symlink: {part}")
            if not stat.S_ISDIR(observed.st_mode):
                raise _unsafe(".", f"vault path component is not a directory: {part}")
            try:
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise _unsafe(".", f"vault path component changed or is unsafe: {part}") from exc
            opened = os.fstat(child)
            if _identity(opened, directory=True) != _identity(
                observed, directory=True
            ):
                os.close(child)
                raise _unsafe(".", f"vault path component changed while opening: {part}")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_bound_file(
    parent_descriptor: int,
    name: str,
    relative: str,
    observed: os.stat_result,
) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=parent_descriptor)
    except OSError as exc:
        raise _unsafe(relative, "file changed or could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise _unsafe(relative, "file is not a single-link ordinary file")
        if _identity(opened, directory=False) != _identity(observed, directory=False):
            raise _unsafe(relative, "file changed while being opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if _identity(final, directory=False) != _identity(opened, directory=False):
            raise _unsafe(relative, "file changed while being read")
        return b"".join(chunks), opened
    finally:
        os.close(descriptor)


def _scan_bound_directory(
    descriptor: int,
    root: Path,
    relative: str,
    snapshots: list[MarkdownFile],
    *,
    skip_dirs: Collection[str],
    skip_files: Collection[str],
) -> None:
    before = os.fstat(descriptor)
    try:
        names = sorted(os.listdir(descriptor))
    except OSError as exc:
        raise _unsafe(relative, "directory is unreadable") from exc
    for name in names:
        child_relative = f"{relative}/{name}" if relative else name
        try:
            observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            raise _unsafe(child_relative, "entry changed during scan") from exc
        mode = observed.st_mode
        if stat.S_ISLNK(mode):
            raise _unsafe(child_relative, "symlinks are not allowed")
        if stat.S_ISDIR(mode):
            if name in skip_dirs:
                continue
            try:
                child_descriptor = os.open(name, _directory_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise _unsafe(child_relative, "directory changed or is unsafe") from exc
            try:
                opened = os.fstat(child_descriptor)
                if _identity(opened, directory=True) != _identity(
                    observed, directory=True
                ):
                    raise _unsafe(child_relative, "directory changed while being opened")
                _scan_bound_directory(
                    child_descriptor,
                    root,
                    child_relative,
                    snapshots,
                    skip_dirs=skip_dirs,
                    skip_files=skip_files,
                )
                attached = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if _identity(attached, directory=True) != _identity(
                    opened, directory=True
                ):
                    raise _unsafe(child_relative, "directory changed during scan")
            finally:
                os.close(child_descriptor)
            continue
        if not name.endswith(".md"):
            continue
        if not stat.S_ISREG(mode):
            raise _unsafe(child_relative, "special files are not allowed")
        if observed.st_nlink != 1:
            raise _unsafe(child_relative, "hard-linked files are not allowed")
        if name in skip_files:
            continue
        content, opened = _read_bound_file(descriptor, name, child_relative, observed)
        attached = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if _identity(attached, directory=False) != _identity(opened, directory=False):
            raise _unsafe(child_relative, "file changed after being read")
        snapshots.append(
            MarkdownFile(root / child_relative, child_relative, content, opened.st_mtime_ns)
        )
    final = os.fstat(descriptor)
    if _identity(final, directory=True) != _identity(before, directory=True):
        raise _unsafe(relative, "directory changed during scan")


def scan_markdown_files(
    root: Path,
    *,
    skip_dirs: Collection[str] = (),
    skip_files: Collection[str] = (),
) -> tuple[MarkdownFile, ...]:
    """Snapshot validated Markdown files without following any filesystem links."""
    root = Path(os.path.abspath(os.fspath(root)))
    _validate_path_ancestry(root)
    try:
        observed = root.lstat()
    except OSError as exc:
        raise _unsafe(".", "vault root is unavailable") from exc
    if stat.S_ISLNK(observed.st_mode) or _reparse(observed):
        raise _unsafe(".", "vault root symlink is not allowed")
    if not stat.S_ISDIR(observed.st_mode):
        raise _unsafe(".", "vault root must be an ordinary directory")

    if not _SUPPORTS_BOUND_SCAN:
        raise _unsafe(".", "safe vault scanning is not supported on this platform")
    snapshots: list[MarkdownFile] = []
    descriptor = _open_bound_root(root)
    try:
        opened = os.fstat(descriptor)
        if _identity(opened, directory=True) != _identity(
            observed, directory=True
        ):
            raise _unsafe(".", "vault root changed while being opened")
        _scan_bound_directory(
            descriptor,
            root,
            "",
            snapshots,
            skip_dirs=skip_dirs,
            skip_files=skip_files,
        )
    finally:
        os.close(descriptor)
    return tuple(sorted(snapshots, key=lambda item: item.relative))

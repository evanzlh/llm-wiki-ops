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


@dataclass(frozen=True)
class MarkdownHeader:
    """Bounded metadata snapshot whose identity can guard a later full read."""

    root: Path
    path: Path
    relative: str
    content: bytes
    mtime_ns: int
    identity: tuple[int, ...]

    def text(self, *, errors: str = "strict") -> str:
        try:
            return self.content.decode("utf-8", errors=errors)
        except UnicodeError as exc:
            raise ValueError(f"page header is not valid UTF-8: {self.relative}") from exc


@dataclass(frozen=True)
class SafeFileSnapshot:
    """Content bound to the identities of both its file and containing root."""

    root: Path
    path: Path
    content: bytes
    root_identity: tuple[int, ...]
    file_identity: tuple[int, ...]


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


def _stat_at(
    descriptor: int,
    name: str,
    relative: str,
    *,
    missing_ok: bool = False,
) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        if missing_ok:
            return None
        raise _unsafe(relative, f"stat failed: {exc}") from exc
    except OSError as exc:
        raise _unsafe(relative, f"stat failed: {exc}") from exc


def _fstat(descriptor: int, relative: str) -> os.stat_result:
    try:
        return os.fstat(descriptor)
    except OSError as exc:
        raise _unsafe(relative, f"fstat failed: {exc}") from exc


def _open_at(descriptor: int, name: str, flags: int, relative: str) -> int:
    try:
        return os.open(name, flags, dir_fd=descriptor)
    except OSError as exc:
        raise _unsafe(relative, f"open failed: {exc}") from exc


def _read(descriptor: int, relative: str) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(descriptor, 1024 * 1024)
        except OSError as exc:
            raise _unsafe(relative, f"read failed: {exc}") from exc
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_markdown_header(
    descriptor: int,
    relative: str,
    *,
    max_bytes: int,
) -> bytes:
    """Read only a first line or a complete bounded YAML frontmatter block."""
    content = bytearray()
    frontmatter: bool | None = None
    line_start = 0
    swallow_lf = False
    while len(content) < max_bytes:
        try:
            chunk = os.read(descriptor, 1)
        except OSError as exc:
            raise _unsafe(relative, f"header read failed: {exc}") from exc
        if not chunk:
            final_line = bytes(content[line_start:])
            if frontmatter and final_line == b"---":
                return bytes(content)
            if frontmatter:
                raise _unsafe(relative, "unterminated YAML frontmatter")
            return bytes(content)
        content.extend(chunk)
        if swallow_lf:
            swallow_lf = False
            if chunk == b"\n":
                line_start = len(content)
                continue
        if chunk not in {b"\n", b"\r"}:
            continue
        line = bytes(content[line_start:])
        line_start = len(content)
        if chunk == b"\r":
            swallow_lf = True
        if frontmatter is None:
            frontmatter = line in {b"---\n", b"---\r"}
            if not frontmatter:
                return bytes(content)
        elif frontmatter and line in {b"---\n", b"---\r"}:
            return bytes(content)
    raise _unsafe(relative, f"Markdown header exceeds {max_bytes} bytes")


def _close(descriptor: int, relative: str) -> None:
    try:
        os.close(descriptor)
    except OSError as exc:
        raise _unsafe(relative, f"close failed: {exc}") from exc


def _handoff_descriptor(
    parent: int,
    child: int,
    *,
    parent_relative: str,
    child_relative: str,
) -> int:
    try:
        _close(parent, parent_relative)
    except UnsafeVaultError as initial:
        try:
            _close(child, child_relative)
        except UnsafeVaultError as cleanup:
            raise cleanup from initial
        raise
    return child


def _validate_path_ancestry(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise _unsafe(".", f"lstat failed for vault path component {current}: {exc}") from exc
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
    try:
        descriptor = os.open(root.anchor, _directory_flags())
    except OSError as exc:
        raise _unsafe(".", f"open failed for vault anchor {root.anchor}: {exc}") from exc
    try:
        component = root.anchor
        for part in root.parts[1:]:
            parent_component = component
            component = os.fspath(Path(component) / part)
            observed = _stat_at(descriptor, part, component)
            assert observed is not None
            if stat.S_ISLNK(observed.st_mode) or _reparse(observed):
                raise _unsafe(component, "vault path component is a symlink")
            if not stat.S_ISDIR(observed.st_mode):
                raise _unsafe(component, "vault path component is not a directory")
            child = _open_at(descriptor, part, _directory_flags(), component)
            try:
                opened = _fstat(child, component)
            except BaseException:
                _close(child, component)
                raise
            if _identity(opened, directory=True) != _identity(
                observed, directory=True
            ):
                _close(child, component)
                raise _unsafe(component, "vault path component changed while opening")
            parent = descriptor
            descriptor = _handoff_descriptor(
                parent,
                child,
                parent_relative=parent_component,
                child_relative=component,
            )
        return descriptor
    except BaseException:
        _close(descriptor, ".")
        raise


def _read_bound_file(
    parent_descriptor: int,
    name: str,
    relative: str,
    observed: os.stat_result,
) -> tuple[bytes, os.stat_result]:
    descriptor = _open_at(parent_descriptor, name, _file_flags(), relative)
    try:
        opened = _fstat(descriptor, relative)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise _unsafe(relative, "file is not a single-link ordinary file")
        if _identity(opened, directory=False) != _identity(observed, directory=False):
            raise _unsafe(relative, "file changed while being opened")
        content = _read(descriptor, relative)
        final = _fstat(descriptor, relative)
        if _identity(final, directory=False) != _identity(opened, directory=False):
            raise _unsafe(relative, "file changed while being read")
        return content, opened
    finally:
        _close(descriptor, relative)


def _scan_bound_directory(
    descriptor: int,
    root: Path,
    relative: str,
    snapshots: list[MarkdownFile],
    *,
    skip_dirs: Collection[str],
    skip_files: Collection[str],
) -> None:
    before = _fstat(descriptor, relative)
    try:
        names = sorted(os.listdir(descriptor))
    except OSError as exc:
        raise _unsafe(relative, f"listdir failed: {exc}") from exc
    for name in names:
        child_relative = f"{relative}/{name}" if relative else name
        observed = _stat_at(descriptor, name, child_relative)
        assert observed is not None
        mode = observed.st_mode
        if stat.S_ISLNK(mode):
            if Path(name).suffix and not name.endswith(".md"):
                continue
            raise _unsafe(child_relative, "symlinks are not allowed")
        if stat.S_ISDIR(mode):
            if name in skip_dirs:
                continue
            child_descriptor = _open_at(
                descriptor, name, _directory_flags(), child_relative
            )
            try:
                opened = _fstat(child_descriptor, child_relative)
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
                attached = _stat_at(descriptor, name, child_relative)
                assert attached is not None
                if _identity(attached, directory=True) != _identity(
                    opened, directory=True
                ):
                    raise _unsafe(child_relative, "directory changed during scan")
            finally:
                _close(child_descriptor, child_relative)
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
        attached = _stat_at(descriptor, name, child_relative)
        assert attached is not None
        if _identity(attached, directory=False) != _identity(opened, directory=False):
            raise _unsafe(child_relative, "file changed after being read")
        snapshots.append(
            MarkdownFile(root / child_relative, child_relative, content, opened.st_mtime_ns)
        )
    final = _fstat(descriptor, relative)
    if _identity(final, directory=True) != _identity(before, directory=True):
        raise _unsafe(relative, "directory changed during scan")


def _scan_bound_headers(
    descriptor: int,
    root: Path,
    relative: str,
    snapshots: list[MarkdownHeader],
    *,
    skip_dirs: Collection[str],
    skip_files: Collection[str],
    max_header_bytes: int,
) -> None:
    before = _fstat(descriptor, relative)
    try:
        names = sorted(os.listdir(descriptor))
    except OSError as exc:
        raise _unsafe(relative, f"listdir failed: {exc}") from exc
    for name in names:
        child_relative = f"{relative}/{name}" if relative else name
        observed = _stat_at(descriptor, name, child_relative)
        assert observed is not None
        mode = observed.st_mode
        if stat.S_ISLNK(mode):
            if Path(name).suffix and not name.endswith(".md"):
                continue
            raise _unsafe(child_relative, "symlinks are not allowed")
        if stat.S_ISDIR(mode):
            if name in skip_dirs:
                continue
            child = _open_at(descriptor, name, _directory_flags(), child_relative)
            try:
                opened = _fstat(child, child_relative)
                if _identity(opened, directory=True) != _identity(observed, directory=True):
                    raise _unsafe(child_relative, "directory changed while being opened")
                _scan_bound_headers(
                    child,
                    root,
                    child_relative,
                    snapshots,
                    skip_dirs=skip_dirs,
                    skip_files=skip_files,
                    max_header_bytes=max_header_bytes,
                )
                attached = _stat_at(descriptor, name, child_relative)
                assert attached is not None
                if _identity(attached, directory=True) != _identity(opened, directory=True):
                    raise _unsafe(child_relative, "directory changed during scan")
            finally:
                _close(child, child_relative)
            continue
        if not name.endswith(".md"):
            continue
        if not stat.S_ISREG(mode) or observed.st_nlink != 1:
            raise _unsafe(child_relative, "file is not a single-link ordinary file")
        if name in skip_files:
            continue
        file_descriptor = _open_at(descriptor, name, _file_flags(), child_relative)
        try:
            opened = _fstat(file_descriptor, child_relative)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise _unsafe(child_relative, "file is not a single-link ordinary file")
            if _identity(opened, directory=False) != _identity(observed, directory=False):
                raise _unsafe(child_relative, "file changed while being opened")
            content = _read_markdown_header(
                file_descriptor, child_relative, max_bytes=max_header_bytes
            )
            final_file = _fstat(file_descriptor, child_relative)
            if _identity(final_file, directory=False) != _identity(opened, directory=False):
                raise _unsafe(child_relative, "file changed while reading metadata")
        finally:
            _close(file_descriptor, child_relative)
        attached = _stat_at(descriptor, name, child_relative)
        assert attached is not None
        if _identity(attached, directory=False) != _identity(opened, directory=False):
            raise _unsafe(child_relative, "file changed after reading metadata")
        snapshots.append(
            MarkdownHeader(
                root,
                root / child_relative,
                child_relative,
                content,
                opened.st_mtime_ns,
                _identity(opened, directory=False),
            )
        )
    final = _fstat(descriptor, relative)
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
        raise _unsafe(".", f"vault root lstat failed: {exc}") from exc
    if stat.S_ISLNK(observed.st_mode) or _reparse(observed):
        raise _unsafe(".", "vault root symlink is not allowed")
    if not stat.S_ISDIR(observed.st_mode):
        raise _unsafe(".", "vault root must be an ordinary directory")

    if not _SUPPORTS_BOUND_SCAN:
        raise _unsafe(".", "safe vault scanning is not supported on this platform")
    snapshots: list[MarkdownFile] = []
    descriptor = _open_bound_root(root)
    try:
        opened = _fstat(descriptor, ".")
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
        _close(descriptor, ".")
    return tuple(sorted(snapshots, key=lambda item: item.relative))


def scan_markdown_headers(
    root: Path,
    *,
    skip_dirs: Collection[str] = (),
    skip_files: Collection[str] = (),
    max_header_bytes: int = 64 * 1024,
) -> tuple[MarkdownHeader, ...]:
    """Snapshot bounded metadata without reading Markdown page bodies."""
    if max_header_bytes < 4:
        raise ValueError("max_header_bytes must be at least 4")
    root = Path(os.path.abspath(os.fspath(root)))
    _validate_path_ancestry(root)
    try:
        observed = root.lstat()
    except OSError as exc:
        raise _unsafe(".", f"vault root lstat failed: {exc}") from exc
    if stat.S_ISLNK(observed.st_mode) or _reparse(observed):
        raise _unsafe(".", "vault root symlink is not allowed")
    if not stat.S_ISDIR(observed.st_mode):
        raise _unsafe(".", "vault root must be an ordinary directory")
    if not _SUPPORTS_BOUND_SCAN:
        raise _unsafe(".", "safe vault scanning is not supported on this platform")
    snapshots: list[MarkdownHeader] = []
    descriptor = _open_bound_root(root)
    try:
        opened = _fstat(descriptor, ".")
        if _identity(opened, directory=True) != _identity(observed, directory=True):
            raise _unsafe(".", "vault root changed while being opened")
        _scan_bound_headers(
            descriptor,
            root,
            "",
            snapshots,
            skip_dirs=skip_dirs,
            skip_files=skip_files,
            max_header_bytes=max_header_bytes,
        )
    finally:
        _close(descriptor, ".")
    return tuple(sorted(snapshots, key=lambda item: item.relative))


def read_markdown_snapshot(snapshot: MarkdownHeader) -> MarkdownFile:
    """Fully read a metadata snapshot only if its filesystem identity is unchanged."""
    content = read_safe_file(
        snapshot.root,
        snapshot.path,
        expected_identity=snapshot.identity,
    )
    assert content is not None
    return MarkdownFile(snapshot.path, snapshot.relative, content, snapshot.mtime_ns)


def read_safe_file_snapshot(
    root: Path,
    path: Path,
    *,
    missing_ok: bool = False,
    expected_identity: tuple[int, ...] | None = None,
    expected_root_identity: tuple[int, ...] | None = None,
) -> SafeFileSnapshot | None:
    """Read a file and retain identities needed to reject later root rebinding."""
    root = Path(os.path.abspath(os.fspath(root)))
    path = Path(os.path.abspath(os.fspath(path)))
    try:
        relative_path = path.relative_to(root)
    except ValueError as exc:
        raise _unsafe(str(path), "file is outside the vault root") from exc
    if not relative_path.parts:
        raise _unsafe(".", "file path names the vault root")
    relative = relative_path.as_posix()

    _validate_path_ancestry(root)
    try:
        root_observed = root.lstat()
    except OSError as exc:
        raise _unsafe(".", f"vault root lstat failed: {exc}") from exc
    if stat.S_ISLNK(root_observed.st_mode) or _reparse(root_observed):
        raise _unsafe(".", "vault root symlink is not allowed")
    if not stat.S_ISDIR(root_observed.st_mode):
        raise _unsafe(".", "vault root must be an ordinary directory")
    if not _SUPPORTS_BOUND_SCAN:
        raise _unsafe(".", "safe vault reading is not supported on this platform")

    descriptor = _open_bound_root(root)
    try:
        opened_root = _fstat(descriptor, ".")
        if _identity(opened_root, directory=True) != _identity(
            root_observed, directory=True
        ):
            raise _unsafe(".", "vault root changed while being opened")
        root_identity = _identity(opened_root, directory=True)
        if (
            expected_root_identity is not None
            and root_identity != expected_root_identity
        ):
            raise _unsafe(".", "root changed since file was read")
        traversed: list[str] = []
        for part in relative_path.parts[:-1]:
            parent_relative = "/".join(traversed)
            traversed.append(part)
            child_relative = "/".join(traversed)
            observed = _stat_at(
                descriptor, part, child_relative, missing_ok=missing_ok
            )
            if observed is None:
                return None
            if stat.S_ISLNK(observed.st_mode) or _reparse(observed):
                raise _unsafe(child_relative, "symlinks are not allowed")
            if not stat.S_ISDIR(observed.st_mode):
                raise _unsafe(child_relative, "path component is not a directory")
            child = _open_at(descriptor, part, _directory_flags(), child_relative)
            try:
                opened = _fstat(child, child_relative)
                if _identity(opened, directory=True) != _identity(
                    observed, directory=True
                ):
                    raise _unsafe(child_relative, "directory changed while being opened")
            except BaseException:
                _close(child, child_relative)
                raise
            parent = descriptor
            descriptor = _handoff_descriptor(
                parent,
                child,
                parent_relative=parent_relative,
                child_relative=child_relative,
            )

        name = relative_path.parts[-1]
        observed_file = _stat_at(
            descriptor, name, relative, missing_ok=missing_ok
        )
        if observed_file is None:
            return None
        if stat.S_ISLNK(observed_file.st_mode) or _reparse(observed_file):
            raise _unsafe(relative, "symlinks are not allowed")
        if not stat.S_ISREG(observed_file.st_mode) or observed_file.st_nlink != 1:
            raise _unsafe(relative, "file is not a single-link ordinary file")
        if expected_identity is not None and _identity(
            observed_file, directory=False
        ) != expected_identity:
            raise _unsafe(relative, "file changed since metadata was read")
        content, opened_file = _read_bound_file(
            descriptor, name, relative, observed_file
        )
        attached = _stat_at(descriptor, name, relative)
        assert attached is not None
        if _identity(attached, directory=False) != _identity(
            opened_file, directory=False
        ):
            raise _unsafe(relative, "file changed after being read")
        return SafeFileSnapshot(
            root,
            path,
            content,
            root_identity,
            _identity(opened_file, directory=False),
        )
    finally:
        _close(descriptor, relative)


def verify_safe_file_snapshot(snapshot: SafeFileSnapshot) -> None:
    """Re-open all path components and reject a rebound root or replaced file."""
    read_safe_file_snapshot(
        snapshot.root,
        snapshot.path,
        expected_identity=snapshot.file_identity,
        expected_root_identity=snapshot.root_identity,
    )


def read_safe_file(
    root: Path,
    path: Path,
    *,
    missing_ok: bool = False,
    expected_identity: tuple[int, ...] | None = None,
) -> bytes | None:
    """Read one single-link ordinary file beneath *root* without following links."""
    snapshot = read_safe_file_snapshot(
        root,
        path,
        missing_ok=missing_ok,
        expected_identity=expected_identity,
    )
    return None if snapshot is None else snapshot.content

"""Machine-local derived-state tracking for portable repositories."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from .config import PortableConfig


class LocalStateError(RuntimeError):
    """Raised when local derived state cannot be inspected safely."""


_HASH_PREFIX = "sha256:"
_SIDECAR_NAME = "hot-state.json"
_HOT_NAME = "hot.md"
_KNOWLEDGE_CATEGORIES = frozenset(
    {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
)
_GIT_ENV_OVERRIDES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)
_SUPPORTS_BOUND_DIRECTORIES = all(
    function in os.supports_dir_fd
    for function in (os.mkdir, os.open, os.rename, os.stat)
)
_FileIdentity = tuple[int, int]


def _is_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _identity(metadata: os.stat_result) -> _FileIdentity:
    return metadata.st_dev, metadata.st_ino


def _close_windows_handles(handles: list[int]) -> None:
    if not handles:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    for handle in reversed(handles):
        close_handle(handle)


def _open_windows_directory_handle(path: Path) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    share_read = 0x00000001
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    handle = create_file(
        str(path),
        0,
        share_read,
        None,
        open_existing,
        backup_semantics | open_reparse_point,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    handle_value = handle.value if hasattr(handle, "value") else handle
    if handle_value == invalid:
        error = ctypes.get_last_error()
        raise LocalStateError(
            f"cannot guard Windows directory against replacement: {path} ({error})"
        )
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    information = ByHandleFileInformation()
    if not get_information(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        _close_windows_handles([int(handle_value)])
        raise LocalStateError(
            f"cannot inspect guarded Windows directory: {path} ({error})"
        )
    directory_attribute = 0x00000010
    reparse_attribute = 0x00000400
    if not information.file_attributes & directory_attribute or (
        information.file_attributes & reparse_attribute
    ):
        _close_windows_handles([int(handle_value)])
        raise LocalStateError(
            f"guarded Windows path must be an ordinary directory: {path}"
        )
    try:
        _require_ordinary_directory(path, "guarded Windows directory")
    except BaseException:
        _close_windows_handles([int(handle_value)])
        raise
    return int(handle_value)


def _windows_directory_guard(
    root: Path, paths: tuple[Path, ...], *, create: bool = False
) -> list[int]:
    """Hold no-delete-share handles so Windows ancestors cannot be swapped."""

    if os.name != "nt":
        raise LocalStateError("Windows directory guards are unavailable")
    root = Path(os.path.abspath(root))
    guarded: dict[str, int] = {}
    handles: list[int] = []
    try:
        for target in paths:
            relative = _contained_relative(root, target, "guarded directory")
            anchor = Path(root.anchor)
            chain: list[Path] = [anchor]
            current = anchor
            for part in root.parts[1:]:
                current = current / part
                chain.append(current)
            for part in relative.parts:
                current = current / part
                chain.append(current)
            for directory in chain:
                key = os.path.normcase(os.path.abspath(directory))
                if key in guarded:
                    continue
                if (
                    create
                    and _relative_if_below(directory, root) is not None
                    and not directory.exists()
                    and not directory.is_symlink()
                ):
                    try:
                        directory.mkdir()
                    except FileExistsError:
                        pass
                handle = _open_windows_directory_handle(directory)
                guarded[key] = handle
                handles.append(handle)
        return handles
    except BaseException:
        _close_windows_handles(handles)
        raise


def _contained_relative(root: Path, path: Path, label: str) -> PurePosixPath:
    root = root.resolve(strict=False)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise LocalStateError(f"{label} escapes the portable repository") from exc
    if not relative.parts or ".." in relative.parts:
        raise LocalStateError(f"{label} must be below the portable repository")
    return PurePosixPath(relative.as_posix())


def _require_ordinary_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LocalStateError(f"{label} is unavailable: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
    ):
        raise LocalStateError(f"{label} must be an ordinary directory: {path}")


def _open_directory_at(parent_fd: int | None, path: str | Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        if parent_fd is None:
            descriptor = os.open(path, flags)
        else:
            descriptor = os.open(path, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise LocalStateError(f"directory is unsafe or unreadable: {path}") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(metadata):
        os.close(descriptor)
        raise LocalStateError(f"directory must be ordinary: {path}")
    return descriptor


def _open_bound_directory(root: Path, path: Path, *, create: bool = False) -> int:
    relative = _contained_relative(root, path, "bound directory")
    descriptor = _open_directory_at(None, root)
    try:
        for part in relative.parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise LocalStateError(
                        f"cannot create contained directory: {path}"
                    ) from exc
            child = _open_directory_at(descriptor, part)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _directory_identities(
    root: Path, path: Path
) -> tuple[tuple[Path, _FileIdentity], ...]:
    relative = _contained_relative(root, path, "directory")
    current = root
    identities: list[tuple[Path, _FileIdentity]] = []
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        _require_ordinary_directory(current, "contained directory")
        identities.append((current, _identity(current.lstat())))
    return tuple(identities)


def _verify_directory_identities(
    identities: tuple[tuple[Path, _FileIdentity], ...],
) -> None:
    for path, expected in identities:
        _require_ordinary_directory(path, "contained directory")
        if _identity(path.lstat()) != expected:
            raise LocalStateError(
                f"contained directory changed during operation: {path}"
            )


def _validate_vault(config: PortableConfig) -> None:
    root = config.root.resolve(strict=False)
    _require_ordinary_directory(root, "portable repository root")
    _contained_relative(root, config.vault, "vault")
    _require_ordinary_directory(config.vault, "vault")


def _ensure_local_state(config: PortableConfig) -> Path:
    root = config.root.resolve(strict=False)
    relative = _contained_relative(root, config.local_state, "local state")
    if _SUPPORTS_BOUND_DIRECTORIES:
        descriptor = _open_bound_directory(root, config.local_state, create=True)
        os.close(descriptor)
        return config.local_state
    if os.name == "nt":
        handles = _windows_directory_guard(root, (config.local_state,), create=True)
        _close_windows_handles(handles)
        return config.local_state
    current = root
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise LocalStateError(
                    f"cannot create local state directory: {current}"
                ) from exc
            try:
                metadata = current.lstat()
            except OSError as exc:
                raise LocalStateError(
                    f"local state directory is unavailable: {current}"
                ) from exc
        except OSError as exc:
            raise LocalStateError(
                f"local state directory is unavailable: {current}"
            ) from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse(metadata)
        ):
            raise LocalStateError(
                f"local state path must contain only ordinary directories: {current}"
            )
    return config.local_state


def _open_ordinary_file(
    path: Path, label: str, *, root: Path | None
) -> tuple[int, os.stat_result]:
    windows_handles: list[int] = []
    if root is not None and not _SUPPORTS_BOUND_DIRECTORIES and os.name == "nt":
        windows_handles = _windows_directory_guard(root, (path.parent,))
    try:
        lexical = path.lstat()
    except OSError as exc:
        _close_windows_handles(windows_handles)
        raise LocalStateError(
            f"{label} must be a readable ordinary file: {path}"
        ) from exc
    if (
        not stat.S_ISREG(lexical.st_mode)
        or stat.S_ISLNK(lexical.st_mode)
        or _is_reparse(lexical)
        or lexical.st_nlink != 1
    ):
        _close_windows_handles(windows_handles)
        raise LocalStateError(f"{label} must be a single-link ordinary file: {path}")

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    parent_fd: int | None = None
    try:
        if root is not None and _SUPPORTS_BOUND_DIRECTORIES:
            parent_fd = _open_bound_directory(root, path.parent)
            descriptor = os.open(path.name, flags, dir_fd=parent_fd)
        else:
            descriptor = os.open(path, flags)
    except OSError as exc:
        _close_windows_handles(windows_handles)
        raise LocalStateError(
            f"{label} must be a readable ordinary file: {path}"
        ) from exc
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or _is_reparse(opened)
        or opened.st_nlink != 1
        or _identity(opened) != _identity(lexical)
    ):
        os.close(descriptor)
        _close_windows_handles(windows_handles)
        raise LocalStateError(f"{label} changed while it was being opened: {path}")
    _close_windows_handles(windows_handles)
    return descriptor, opened


def _hash_ordinary_file(path: Path, label: str, *, root: Path | None = None) -> str:
    descriptor, before = _open_ordinary_file(path, label, root=root)
    digest = hashlib.sha256()
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
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
            raise LocalStateError(f"{label} changed while it was being read: {path}")
    finally:
        os.close(descriptor)
    return _HASH_PREFIX + digest.hexdigest()


def _relative_if_below(path: Path, parent: Path) -> Path | None:
    try:
        return path.relative_to(parent)
    except ValueError:
        return None


def _authoritative_directory(relative: Path) -> bool:
    if not relative.parts:
        return True
    if relative.parts[0] in _KNOWLEDGE_CATEGORIES:
        return True
    return relative.parts[:2] == (".manifest", "sources") or relative == Path(
        ".manifest"
    )


def _authoritative_files(config: PortableConfig) -> Iterator[Path]:
    vault = config.vault
    local_relative = _relative_if_below(config.local_state, vault)
    selected: list[Path] = []
    for directory, dirnames, filenames in os.walk(vault, followlinks=False):
        current = Path(directory)
        _require_ordinary_directory(current, "vault content directory")
        current_relative = current.relative_to(vault)

        kept_directories: list[str] = []
        for name in sorted(dirnames):
            child_relative = current_relative / name
            if not _authoritative_directory(child_relative):
                continue
            if local_relative is not None and (
                child_relative == local_relative
                or local_relative in child_relative.parents
            ):
                continue
            child = current / name
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise LocalStateError(
                    f"vault content directory is unavailable: {child}"
                ) from exc
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise LocalStateError(
                    f"vault content directory must be an ordinary directory: {child}"
                )
            if _is_reparse(metadata):
                raise LocalStateError(
                    f"vault content directory must not be a reparse point: {child}"
                )
            kept_directories.append(name)
        dirnames[:] = kept_directories

        for name in sorted(filenames):
            relative = current_relative / name
            if relative == Path(_HOT_NAME):
                continue
            if relative.parts[:1] == (".obsidian",):
                continue
            if local_relative is not None and (
                relative == local_relative or local_relative in relative.parents
            ):
                continue
            is_knowledge_page = (
                len(relative.parts) >= 2
                and relative.parts[0] in _KNOWLEDGE_CATEGORIES
                and relative.suffix == ".md"
            )
            is_manifest_marker = relative == Path(".manifest.json")
            is_manifest_shard = (
                relative.parts[:2] == (".manifest", "sources")
                and relative.suffix == ".json"
            )
            if is_knowledge_page or is_manifest_marker or is_manifest_shard:
                selected.append(current / name)

    yield from sorted(selected, key=lambda item: item.relative_to(vault).as_posix())


def _git_identity(root: Path) -> str | None:
    environment = {
        key: value for key, value in os.environ.items() if key not in _GIT_ENV_OVERRIDES
    }
    try:
        worktree = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if worktree.returncode != 0:
        return None
    discovered = Path(worktree.stdout.strip())
    try:
        if discovered.resolve(strict=True) != root.resolve(strict=True):
            return None
    except OSError:
        return None
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if branch.returncode != 0:
        return None
    name = branch.stdout.strip()
    if not name:
        return None
    if name != "HEAD":
        return f"branch:{name}"
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "detached:HEAD"
    value = commit.stdout.strip()
    if commit.returncode == 0 and value:
        return f"detached:{value}"
    return "detached:HEAD"


def authoritative_fingerprint(config: PortableConfig) -> str:
    """Hash branch identity and all authoritative portable-vault files."""

    _validate_vault(config)
    files = [
        [
            _contained_relative(config.root, path, "authoritative file").as_posix(),
            _hash_ordinary_file(path, "authoritative file", root=config.root),
        ]
        for path in _authoritative_files(config)
    ]
    payload = {"files": files, "git": _git_identity(config.root)}
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return _HASH_PREFIX + hashlib.sha256(canonical).hexdigest()


def _sidecar_payload(config: PortableConfig) -> dict[str, str] | None:
    path = config.local_state / _SIDECAR_NAME
    try:
        descriptor, before = _open_ordinary_file(
            path, "hot-state sidecar", root=config.root
        )
    except FileNotFoundError:
        return None
    except LocalStateError:
        return None
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 1024 * 1024:
                return None
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError:
        return None
    finally:
        os.close(descriptor)
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
        return None
    try:
        payload: Any = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"fingerprint", "hot_hash"}:
        return None
    fingerprint = payload.get("fingerprint")
    hot_hash = payload.get("hot_hash")
    if not isinstance(fingerprint, str) or not isinstance(hot_hash, str):
        return None
    return {"fingerprint": fingerprint, "hot_hash": hot_hash}


def _hot_metadata(config: PortableConfig) -> os.stat_result | None:
    hot = config.vault / _HOT_NAME
    _contained_relative(config.root, hot, "hot.md")
    vault_fd: int | None = None
    windows_handles: list[int] = []
    try:
        if _SUPPORTS_BOUND_DIRECTORIES:
            vault_fd = _open_bound_directory(config.root, config.vault)
            return os.stat(_HOT_NAME, dir_fd=vault_fd, follow_symlinks=False)
        if os.name == "nt":
            windows_handles = _windows_directory_guard(config.root, (config.vault,))
        metadata = hot.lstat()
        if _is_reparse(metadata):
            raise LocalStateError(f"hot.md must not be a reparse point: {hot}")
        return metadata
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LocalStateError(f"hot.md is unavailable: {hot}") from exc
    finally:
        if vault_fd is not None:
            os.close(vault_fd)
        _close_windows_handles(windows_handles)


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short local-state write")
        remaining = remaining[written:]


def _invalidated_name() -> str:
    return ".hot-invalidated-" + secrets.token_hex(16) + ".tmp"


def _restore_mismatched_hot_at(
    vault_fd: int,
    local_fd: int,
    tombstone: str,
    expected: _FileIdentity,
) -> None:
    source = -1
    target = -1
    try:
        source = os.open(
            tombstone,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=local_fd,
        )
        source_metadata = os.fstat(source)
        if (
            not stat.S_ISREG(source_metadata.st_mode)
            or _identity(source_metadata) != expected
        ):
            raise LocalStateError(
                "non-file hot.md replacement was preserved in local state"
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            target = os.open(_HOT_NAME, flags, 0o600, dir_fd=vault_fd)
        except FileExistsError:
            return
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            _write_all(target, chunk)
        os.fsync(target)
        target_identity = _identity(os.fstat(target))
        installed = os.stat(_HOT_NAME, dir_fd=vault_fd, follow_symlinks=False)
        if _identity(installed) != target_identity:
            raise LocalStateError(
                "a newer hot.md replaced the restored concurrent write"
            )
        os.fsync(vault_fd)
    except OSError as exc:
        raise LocalStateError(
            "hot.md replacement could not be restored from local quarantine"
        ) from exc
    finally:
        if source >= 0:
            os.close(source)
        if target >= 0:
            os.close(target)


def _restore_mismatched_hot_path(
    tombstone: Path, hot: Path, expected: _FileIdentity
) -> None:
    source = -1
    target = -1
    try:
        lexical = tombstone.lstat()
        if (
            not stat.S_ISREG(lexical.st_mode)
            or stat.S_ISLNK(lexical.st_mode)
            or _is_reparse(lexical)
            or _identity(lexical) != expected
        ):
            raise LocalStateError(
                "unsafe hot.md replacement was preserved in local state"
            )
        source = os.open(tombstone, os.O_RDONLY)
        source_metadata = os.fstat(source)
        if (
            not stat.S_ISREG(source_metadata.st_mode)
            or _identity(source_metadata) != expected
        ):
            raise LocalStateError(
                "non-file hot.md replacement was preserved in local state"
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            target = os.open(hot, flags, 0o600)
        except FileExistsError:
            return
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            _write_all(target, chunk)
        os.fsync(target)
        target_identity = _identity(os.fstat(target))
        if _identity(hot.lstat()) != target_identity:
            raise LocalStateError(
                "a newer hot.md replaced the restored concurrent write"
            )
    except OSError as exc:
        raise LocalStateError(
            "hot.md replacement could not be restored from local quarantine"
        ) from exc
    finally:
        if source >= 0:
            os.close(source)
        if target >= 0:
            os.close(target)


def _invalidate_hot(config: PortableConfig, expected: _FileIdentity) -> None:
    hot = config.vault / _HOT_NAME
    if not _SUPPORTS_BOUND_DIRECTORIES and os.name != "nt":
        raise LocalStateError(
            "safe hot invalidation requires directory-relative filesystem operations"
        )
    _ensure_local_state(config)
    tombstone = _invalidated_name()
    if _SUPPORTS_BOUND_DIRECTORIES:
        vault_fd = _open_bound_directory(config.root, config.vault)
        local_fd: int | None = None
        try:
            local_fd = _open_bound_directory(config.root, config.local_state)
            vault_identities = _directory_identities(config.root, config.vault)
            local_identities = _directory_identities(config.root, config.local_state)
            if _identity(os.fstat(vault_fd)) != vault_identities[-1][1]:
                raise LocalStateError("vault changed while it was being opened")
            if _identity(os.fstat(local_fd)) != local_identities[-1][1]:
                raise LocalStateError("local state changed while it was being opened")
            try:
                current = os.stat(_HOT_NAME, dir_fd=vault_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if _identity(current) != expected:
                return
            if not (stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode)):
                raise LocalStateError(f"hot.md must be a file or symlink: {hot}")
            try:
                os.rename(
                    _HOT_NAME,
                    tombstone,
                    src_dir_fd=vault_fd,
                    dst_dir_fd=local_fd,
                )
            except OSError as exc:
                raise LocalStateError(f"cannot invalidate hot.md: {hot}") from exc
            moved = os.stat(tombstone, dir_fd=local_fd, follow_symlinks=False)
            if _identity(moved) != expected:
                _restore_mismatched_hot_at(
                    vault_fd, local_fd, tombstone, _identity(moved)
                )
                os.fsync(local_fd)
                return
            os.fsync(vault_fd)
            os.fsync(local_fd)
            _verify_directory_identities(vault_identities)
            _verify_directory_identities(local_identities)
            return
        finally:
            os.close(vault_fd)
            if local_fd is not None:
                os.close(local_fd)

    handles = _windows_directory_guard(config.root, (config.vault, config.local_state))
    try:
        vault_identities = _directory_identities(config.root, config.vault)
        local_identities = _directory_identities(config.root, config.local_state)
        metadata = _hot_metadata(config)
        if metadata is None or _identity(metadata) != expected:
            return
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
            raise LocalStateError(f"hot.md must be a file or symlink: {hot}")
        target = config.local_state / tombstone
        _verify_directory_identities(vault_identities)
        _verify_directory_identities(local_identities)
        try:
            hot.replace(target)
        except OSError as exc:
            raise LocalStateError(f"cannot invalidate hot.md: {hot}") from exc
        moved = target.lstat()
        if _identity(moved) != expected:
            _restore_mismatched_hot_path(target, hot, _identity(moved))
            return
        _verify_directory_identities(vault_identities)
        _verify_directory_identities(local_identities)
    finally:
        _close_windows_handles(handles)


def hot_status(
    config: PortableConfig, *, invalidate: bool = False
) -> dict[str, object]:
    """Return whether local ``hot.md`` is stale, optionally removing it."""

    _validate_vault(config)
    fingerprint_before = authoritative_fingerprint(config)
    sidecar = _sidecar_payload(config)
    metadata = _hot_metadata(config)
    reason = "current"
    if metadata is None:
        stale = True
        reason = "hot-missing"
    elif (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        stale = True
        reason = "hot-unsafe"
    elif sidecar is None:
        stale = True
        reason = "sidecar-missing-or-invalid"
    else:
        hot_hash = _hash_ordinary_file(
            config.vault / _HOT_NAME, "hot.md", root=config.root
        )
        stale = sidecar["fingerprint"] != fingerprint_before
        if stale:
            reason = "authoritative-state-changed"
        elif sidecar["hot_hash"] != hot_hash:
            stale = True
            reason = "hot-changed"

    fingerprint_after = authoritative_fingerprint(config)
    if fingerprint_after != fingerprint_before:
        stale = True
        reason = "authoritative-state-changed-during-check"
    if stale and invalidate and metadata is not None:
        _invalidate_hot(config, _identity(metadata))
    return {
        "stale": stale,
        "reason": reason,
        "fingerprint": fingerprint_after,
    }


def _canonical_sidecar(payload: dict[str, str]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _write_sidecar(config: PortableConfig, payload: dict[str, str]) -> None:
    if not _SUPPORTS_BOUND_DIRECTORIES and os.name != "nt":
        raise LocalStateError(
            "safe sidecar writes require directory-relative filesystem operations"
        )
    local_state = _ensure_local_state(config)
    sidecar = local_state / _SIDECAR_NAME
    data = _canonical_sidecar(payload)
    if _SUPPORTS_BOUND_DIRECTORIES:
        directory = _open_bound_directory(config.root, local_state)
        try:
            identities = _directory_identities(config.root, local_state)
        except BaseException:
            os.close(directory)
            raise
        if _identity(os.fstat(directory)) != identities[-1][1]:
            os.close(directory)
            raise LocalStateError("local state changed while it was being opened")
        descriptor = -1
        temporary_name = ""
        temporary_identity: _FileIdentity | None = None
        try:
            for _attempt in range(128):
                candidate = ".hot-state-" + secrets.token_hex(16) + ".tmp"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                try:
                    descriptor = os.open(candidate, flags, 0o600, dir_fd=directory)
                except FileExistsError:
                    continue
                temporary_name = candidate
                temporary_identity = _identity(os.fstat(descriptor))
                break
            else:
                raise LocalStateError("cannot allocate a local hot-state file")
            _write_all(descriptor, data)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            current = os.stat(temporary_name, dir_fd=directory, follow_symlinks=False)
            if _identity(current) != temporary_identity:
                raise LocalStateError("local hot-state file changed during write")
            os.rename(
                temporary_name,
                _SIDECAR_NAME,
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            temporary_name = ""
            installed = os.stat(_SIDECAR_NAME, dir_fd=directory, follow_symlinks=False)
            if _identity(installed) != temporary_identity:
                raise LocalStateError("local hot-state target changed during write")
            os.fsync(directory)
            _verify_directory_identities(identities)
            return
        except OSError as exc:
            raise LocalStateError(f"cannot write local hot state: {sidecar}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory)

    handles = _windows_directory_guard(config.root, (local_state,))
    try:
        identities = _directory_identities(config.root, local_state)
    except BaseException:
        _close_windows_handles(handles)
        raise
    descriptor = -1
    temporary_name = ""
    temporary_identity: _FileIdentity | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".hot-state-", suffix=".tmp", dir=local_state
        )
        temporary_identity = _identity(os.fstat(descriptor))
        _write_all(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _verify_directory_identities(identities)
        if _identity(Path(temporary_name).lstat()) != temporary_identity:
            raise LocalStateError("local hot-state file changed during write")
        os.replace(temporary_name, sidecar)
        temporary_name = ""
        if _identity(sidecar.lstat()) != temporary_identity:
            raise LocalStateError("local hot-state target changed during write")
        _verify_directory_identities(identities)
        if os.name != "nt":
            directory = os.open(local_state, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as exc:
        raise LocalStateError(f"cannot write local hot state: {sidecar}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _close_windows_handles(handles)


def mark_hot_current(config: PortableConfig) -> None:
    """Record the current authoritative and derived hot-file hashes locally."""

    _validate_vault(config)
    metadata = _hot_metadata(config)
    if metadata is None:
        raise LocalStateError("hot.md must exist before it can be marked current")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise LocalStateError("hot.md must be a single-link ordinary file")
    fingerprint_before = authoritative_fingerprint(config)
    hot_hash = _hash_ordinary_file(config.vault / _HOT_NAME, "hot.md", root=config.root)
    fingerprint_after = authoritative_fingerprint(config)
    if fingerprint_after != fingerprint_before:
        raise LocalStateError(
            "authoritative state changed while hot.md was being marked current"
        )
    _write_sidecar(
        config,
        {"fingerprint": fingerprint_after, "hot_hash": hot_hash},
    )

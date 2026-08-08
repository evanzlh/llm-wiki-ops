"""Machine-local derived-state tracking for portable repositories."""

from __future__ import annotations

import hashlib
import json
import os
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
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise LocalStateError(f"{label} must be an ordinary directory: {path}")


def _validate_vault(config: PortableConfig) -> None:
    root = config.root.resolve(strict=False)
    _require_ordinary_directory(root, "portable repository root")
    _contained_relative(root, config.vault, "vault")
    _require_ordinary_directory(config.vault, "vault")


def _ensure_local_state(config: PortableConfig) -> Path:
    root = config.root.resolve(strict=False)
    relative = _contained_relative(root, config.local_state, "local state")
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
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise LocalStateError(
                f"local state path must contain only ordinary directories: {current}"
            )
    return config.local_state


def _hash_ordinary_file(path: Path, label: str) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LocalStateError(
            f"{label} must be a readable ordinary file: {path}"
        ) from exc
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
        ):
            raise LocalStateError(
                f"{label} must be a single-link ordinary file: {path}"
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
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
            if child_relative.parts[:1] == (".obsidian",):
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
            is_knowledge_page = relative.suffix == ".md"
            is_manifest_marker = relative == Path(".manifest.json")
            is_manifest_shard = relative.parts[:2] == (".manifest", "sources")
            if is_knowledge_page or is_manifest_marker or is_manifest_shard:
                selected.append(current / name)

    yield from sorted(selected, key=lambda item: item.relative_to(vault).as_posix())


def _git_identity(root: Path) -> str | None:
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
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
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
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
            _hash_ordinary_file(path, "authoritative file"),
        ]
        for path in _authoritative_files(config)
    ]
    payload = {"files": files, "git": _git_identity(config.root)}
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return _HASH_PREFIX + hashlib.sha256(canonical).hexdigest()


def _sidecar_payload(path: Path) -> dict[str, str] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        return None
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
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
    try:
        return hot.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LocalStateError(f"hot.md is unavailable: {hot}") from exc


def _invalidate_hot(config: PortableConfig) -> None:
    hot = config.vault / _HOT_NAME
    metadata = _hot_metadata(config)
    if metadata is None:
        return
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
        raise LocalStateError(f"hot.md must be a file or symlink: {hot}")
    try:
        hot.unlink()
    except OSError as exc:
        raise LocalStateError(f"cannot invalidate hot.md: {hot}") from exc


def hot_status(
    config: PortableConfig, *, invalidate: bool = False
) -> dict[str, object]:
    """Return whether local ``hot.md`` is stale, optionally removing it."""

    _validate_vault(config)
    fingerprint_before = authoritative_fingerprint(config)
    sidecar = _sidecar_payload(config.local_state / _SIDECAR_NAME)
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
        hot_hash = _hash_ordinary_file(config.vault / _HOT_NAME, "hot.md")
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
    if stale and invalidate:
        _invalidate_hot(config)
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
    local_state = _ensure_local_state(config)
    sidecar = local_state / _SIDECAR_NAME
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".hot-state-", suffix=".tmp", dir=local_state
        )
        os.write(descriptor, _canonical_sidecar(payload))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_name, sidecar)
        temporary_name = ""
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
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


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
    hot_hash = _hash_ordinary_file(config.vault / _HOT_NAME, "hot.md")
    fingerprint_after = authoritative_fingerprint(config)
    if fingerprint_after != fingerprint_before:
        raise LocalStateError(
            "authoritative state changed while hot.md was being marked current"
        )
    _write_sidecar(
        config,
        {"fingerprint": fingerprint_after, "hot_hash": hot_hash},
    )

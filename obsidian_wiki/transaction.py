from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from obsidian_wiki.config import PortableConfig
from obsidian_wiki.portable_manifest import ManifestError, ShardedManifest


class TransactionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TransactionRecord:
    transaction_id: str
    status: str
    started_at: str
    source_ids: tuple[str, ...]
    workspace: Path
    candidate_vault: Path
    preimages: dict[str, str | None]
    deletions: tuple[str, ...]


_TRANSACTION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")
_STATUSES = frozenset({"active", "promoting", "failed", "complete", "restored"})
_METADATA_FIELDS = frozenset(
    {"preimages", "source_ids", "started_at", "status", "transaction_id"}
)
_LOCK_FIELDS = frozenset({"started_at", "transaction_id"})
_MAX_JSON_BYTES = 1024 * 1024
_FileIdentity = tuple[int, int]


class TransactionManager:
    """Own one portable repository's local transaction workspaces and lock."""

    def __init__(self, config: PortableConfig) -> None:
        self.config = config
        self.local_state = config.local_state
        self.transactions_root = self.local_state / "transactions"
        self.lock_path = self.local_state / "write.lock"
        self._require_contained(self.local_state, self.config.root, "local state")

    def begin(
        self,
        sources: list[Path],
        *,
        transaction_id: str | None = None,
        started_at: str | None = None,
    ) -> TransactionRecord:
        resolved_started_at = started_at or self._utc_now()
        self._validate_started_at(resolved_started_at)
        resolved_id = transaction_id or self._generated_id()
        self._validate_transaction_id(resolved_id)
        workspace = self._workspace_path(resolved_id)

        source_ids = self._source_ids(sources)

        lock_owned = False
        workspace_created = False
        try:
            self._ensure_directory(self.local_state)
            self._ensure_directory(self.transactions_root)
            self._acquire_lock(resolved_id, resolved_started_at)
            lock_owned = True

            if workspace.exists() or workspace.is_symlink():
                raise TransactionError(
                    f"transaction workspace already exists: {workspace}"
                )
            workspace.mkdir(mode=0o700)
            workspace_created = True
            self._fsync_directory(self.transactions_root)
            (workspace / "wiki").mkdir()
            (workspace / "snapshots").mkdir()
            self._fsync_directory(workspace)

            self._write_json_atomic(workspace / "deletions.json", [])
            preimages = self._snapshot_preimages()
            payload = {
                "preimages": preimages,
                "source_ids": list(source_ids),
                "started_at": resolved_started_at,
                "status": "active",
                "transaction_id": resolved_id,
            }
            self._write_metadata(workspace, payload)
            return self.load(resolved_id)
        except Exception as exc:
            cleanup_errors: list[str] = []
            if workspace_created:
                try:
                    self._remove_workspace(workspace)
                except Exception as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            if lock_owned:
                try:
                    self._unlink_owned_lock(resolved_id)
                except Exception as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            detail = f": {exc}"
            if cleanup_errors:
                detail += f"; cleanup failed: {'; '.join(cleanup_errors)}"
            raise TransactionError(
                f"cannot begin transaction {resolved_id}{detail}"
            ) from exc

    def load(self, transaction_id: str) -> TransactionRecord:
        workspace = self._workspace_path(transaction_id)
        self._require_ordinary_directory(
            self.transactions_root, "transactions root"
        )
        self._require_ordinary_directory(workspace, "transaction workspace")

        metadata_path = workspace / "metadata.json"
        payload = self._read_json_file(metadata_path, "transaction metadata")
        if not isinstance(payload, dict) or set(payload) != _METADATA_FIELDS:
            raise TransactionError("transaction metadata has invalid fields")
        if payload.get("transaction_id") != transaction_id:
            raise TransactionError(
                "transaction metadata has a mismatched transaction ID"
            )

        status = payload.get("status")
        if not isinstance(status, str) or status not in _STATUSES:
            raise TransactionError(f"invalid transaction status: {status!r}")
        started_at = payload.get("started_at")
        self._validate_started_at(started_at)
        source_ids = self._load_source_ids(payload.get("source_ids"))
        preimages = self._load_preimages(payload.get("preimages"))
        deletions = self._load_deletions(workspace / "deletions.json")

        self._require_managed_tree(workspace)
        candidate_vault = workspace / "wiki"
        self._require_ordinary_directory(candidate_vault, "candidate vault")
        self._require_ordinary_directory(workspace / "snapshots", "snapshot directory")
        return TransactionRecord(
            transaction_id=transaction_id,
            status=status,
            started_at=started_at,
            source_ids=source_ids,
            workspace=workspace,
            candidate_vault=candidate_vault,
            preimages=preimages,
            deletions=deletions,
        )

    def list_transactions(self) -> list[TransactionRecord]:
        if (
            not self.transactions_root.exists()
            and not self.transactions_root.is_symlink()
        ):
            return []
        self._require_ordinary_directory(
            self.transactions_root, "transactions root"
        )
        records: list[TransactionRecord] = []
        for path in sorted(
            self.transactions_root.iterdir(), key=lambda item: item.name
        ):
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise TransactionError(
                    f"transaction entry is unreadable: {path}"
                ) from exc
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise TransactionError(
                    f"transaction entry must be an ordinary directory: {path}"
                )
            self._validate_transaction_id(path.name)
            records.append(self.load(path.name))
        return records

    def abort(self, transaction_id: str) -> None:
        record = self.load(transaction_id)
        if record.status in {"complete", "restored"}:
            raise TransactionError(
                f"transaction status {record.status!r} is retained; use discard"
            )

        lock_exists = self.lock_path.exists() or self.lock_path.is_symlink()
        lock_identity: _FileIdentity | None = None
        if lock_exists:
            lock, lock_identity = self._read_lock()
            owner = lock["transaction_id"]
            if owner != transaction_id:
                raise TransactionError(
                    f"transaction lock belongs to {owner}, not {transaction_id}"
                )
        elif record.status != "failed":
            raise TransactionError(
                f"cannot abort {record.status} transaction {transaction_id} "
                "without its lock"
            )

        self._remove_workspace(record.workspace)
        if lock_exists:
            self._unlink_owned_lock(
                transaction_id, expected_identity=lock_identity
            )

    def _workspace_path(self, transaction_id: str) -> Path:
        self._validate_transaction_id(transaction_id)
        workspace = self.transactions_root / transaction_id
        if workspace.parent != self.transactions_root:
            raise TransactionError("transaction workspace has an unsafe parent")
        self._require_contained(
            workspace,
            self.transactions_root,
            "transaction workspace",
            strict_child=True,
        )
        return workspace

    @staticmethod
    def _validate_transaction_id(transaction_id: object) -> None:
        if not isinstance(transaction_id, str) or _TRANSACTION_ID_RE.fullmatch(
            transaction_id
        ) is None:
            raise TransactionError(
                "transaction ID must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
            )

    @staticmethod
    def _validate_started_at(started_at: object) -> None:
        if (
            not isinstance(started_at, str)
            or not started_at
            or len(started_at) > 128
            or "\x00" in started_at
        ):
            raise TransactionError("transaction start time must be a non-empty string")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _generated_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{secrets.token_hex(6)}"

    def _source_ids(self, sources: list[Path]) -> tuple[str, ...]:
        if not isinstance(sources, list) or not sources:
            raise TransactionError("transaction requires at least one source")
        try:
            manifest = ShardedManifest(self.config)
        except ManifestError as exc:
            raise TransactionError(f"cannot load sharded manifest: {exc}") from exc

        source_ids: list[str] = []
        for raw_source in sources:
            source = Path(raw_source)
            self._require_ordinary_file(source, "source")
            try:
                source_ids.append(manifest.source_id(source))
            except ManifestError as exc:
                raise TransactionError(f"invalid transaction source: {exc}") from exc
        if len(set(source_ids)) != len(source_ids):
            raise TransactionError("duplicate transaction source")
        return tuple(sorted(source_ids))

    def _snapshot_preimages(self) -> dict[str, str | None]:
        self._require_ordinary_directory(self.config.vault, "portable vault")
        excluded_local: Path | None = None
        try:
            excluded_local = self.local_state.relative_to(self.config.vault)
        except ValueError:
            pass

        preimages: dict[str, str | None] = {}
        for directory, dirnames, filenames in os.walk(
            self.config.vault, topdown=True, followlinks=False
        ):
            current = Path(directory)
            self._require_ordinary_directory(current, "vault directory")
            relative_directory = current.relative_to(self.config.vault)

            kept_directories: list[str] = []
            for name in sorted(dirnames):
                relative = relative_directory / name
                if relative.parts and relative.parts[0] == ".obsidian":
                    continue
                if excluded_local is not None and (
                    relative == excluded_local or excluded_local in relative.parents
                ):
                    continue
                child = current / name
                self._require_ordinary_directory(child, "vault directory")
                kept_directories.append(name)
            dirnames[:] = kept_directories

            for name in sorted(filenames):
                relative = relative_directory / name
                relative_key = relative.as_posix()
                if relative_key == "hot.md":
                    continue
                if relative.parts and relative.parts[0] == ".obsidian":
                    continue
                if excluded_local is not None and (
                    relative == excluded_local or excluded_local in relative.parents
                ):
                    continue
                target = current / name
                preimages[relative_key] = self._hash_single_link_file(
                    target, "vault file"
                )
        return dict(sorted(preimages.items()))

    @staticmethod
    def _hash_single_link_file(path: Path, label: str) -> str:
        TransactionManager._require_ordinary_file(path, label)
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise TransactionError(
                f"{label} must be a readable single-link ordinary file: {path}"
            ) from exc
        digest = hashlib.sha256()
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise TransactionError(
                    f"{label} must be a single-link ordinary file: {path}"
                )
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
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
                raise TransactionError(f"{label} changed while being hashed: {path}")
        finally:
            os.close(descriptor)
        return f"sha256:{digest.hexdigest()}"

    def _acquire_lock(self, transaction_id: str, started_at: str) -> None:
        payload = {"started_at": started_at, "transaction_id": transaction_id}
        created_identity: _FileIdentity | None = None
        try:
            with self.lock_path.open("x", encoding="utf-8", newline="\n") as handle:
                opened = os.fstat(handle.fileno())
                created_identity = (opened.st_dev, opened.st_ino)
                handle.write(self._canonical_json(payload))
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_directory(self.local_state)
        except FileExistsError as exc:
            lock, _identity = self._read_lock()
            raise TransactionError(
                f"portable repository is locked by transaction {lock['transaction_id']}"
            ) from exc
        except (OSError, TransactionError) as exc:
            if created_identity is not None:
                self._cleanup_created_lock(created_identity)
            raise TransactionError(f"cannot create transaction lock: {exc}") from exc

    def _read_lock(self) -> tuple[dict[str, str], _FileIdentity]:
        payload, identity = self._read_json_file_with_identity(
            self.lock_path, "transaction lock"
        )
        if not isinstance(payload, dict) or set(payload) != _LOCK_FIELDS:
            raise TransactionError("transaction lock has invalid fields")
        transaction_id = payload.get("transaction_id")
        started_at = payload.get("started_at")
        self._validate_transaction_id(transaction_id)
        self._validate_started_at(started_at)
        return (
            {"started_at": started_at, "transaction_id": transaction_id},
            identity,
        )

    def _unlink_owned_lock(
        self,
        transaction_id: str,
        *,
        expected_identity: _FileIdentity | None = None,
    ) -> None:
        lock, identity = self._read_lock()
        if expected_identity is not None and identity != expected_identity:
            raise TransactionError("transaction lock inode changed before unlink")
        if lock["transaction_id"] != transaction_id:
            raise TransactionError(
                f"transaction lock belongs to {lock['transaction_id']}, "
                f"not {transaction_id}"
            )
        directory_fd = self._open_directory(self.local_state, "local state")
        try:
            current = os.stat(
                self.lock_path.name, dir_fd=directory_fd, follow_symlinks=False
            )
            if (current.st_dev, current.st_ino) != identity:
                raise TransactionError("transaction lock inode changed before unlink")
            os.unlink(self.lock_path.name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except TransactionError:
            raise
        except OSError as exc:
            raise TransactionError("cannot remove owned transaction lock") from exc
        finally:
            os.close(directory_fd)

    def _cleanup_created_lock(self, identity: _FileIdentity) -> None:
        try:
            directory_fd = self._open_directory(self.local_state, "local state")
        except TransactionError:
            return
        try:
            current = os.stat(
                self.lock_path.name, dir_fd=directory_fd, follow_symlinks=False
            )
            if (current.st_dev, current.st_ino) == identity:
                os.unlink(self.lock_path.name, dir_fd=directory_fd)
                try:
                    os.fsync(directory_fd)
                except OSError:
                    pass
        except OSError:
            pass
        finally:
            os.close(directory_fd)

    def _write_metadata(self, workspace: Path, payload: dict[str, object]) -> None:
        self._write_json_atomic(workspace / "metadata.json", payload)

    def _write_json_atomic(self, target: Path, payload: object) -> None:
        self._require_ordinary_directory(target.parent, "transaction directory")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(self._canonical_json(payload))
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(target)
            self._fsync_directory(target.parent)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _canonical_json(payload: object) -> str:
        return json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"

    def _load_source_ids(self, raw: object) -> tuple[str, ...]:
        if (
            not isinstance(raw, list)
            or not raw
            or any(not isinstance(item, str) for item in raw)
        ):
            raise TransactionError(
                "transaction source_ids must be a non-empty string list"
            )
        values = tuple(raw)
        if values != tuple(sorted(set(values))):
            raise TransactionError("transaction source_ids must be unique and sorted")
        try:
            manifest = ShardedManifest(self.config)
            for source_id in values:
                manifest.source_path(source_id)
        except ManifestError as exc:
            raise TransactionError(f"invalid transaction Source ID: {exc}") from exc
        return values

    def _load_preimages(self, raw: object) -> dict[str, str | None]:
        if not isinstance(raw, dict):
            raise TransactionError("transaction preimages must be an object")
        result: dict[str, str | None] = {}
        for path, content_hash in raw.items():
            if not isinstance(path, str):
                raise TransactionError("transaction preimage path must be a string")
            self._validate_relative_path(path, "transaction preimage path")
            if content_hash is not None and (
                not isinstance(content_hash, str)
                or _HASH_RE.fullmatch(content_hash) is None
            ):
                raise TransactionError(
                    f"transaction preimage hash is invalid for {path!r}"
                )
            result[path] = content_hash
        if list(result) != sorted(result):
            raise TransactionError("transaction preimage paths must be sorted")
        return result

    def _load_deletions(self, path: Path) -> tuple[str, ...]:
        raw = self._read_json_file(path, "transaction deletions")
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise TransactionError("transaction deletions must be a string list")
        values = tuple(raw)
        if values != tuple(sorted(set(values))):
            raise TransactionError("transaction deletions must be unique and sorted")
        for value in values:
            self._validate_relative_path(value, "transaction deletion path")
        return values

    @staticmethod
    def _validate_relative_path(raw: str, label: str) -> None:
        path = PurePosixPath(raw)
        windows_path = PureWindowsPath(raw)
        if (
            not raw
            or "\\" in raw
            or path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or "." in path.parts
            or ".." in path.parts
            or path.as_posix() != raw
            or raw in {".", ".."}
        ):
            raise TransactionError(f"{label} is unsafe: {raw!r}")

    @staticmethod
    def _read_json_file(path: Path, label: str) -> object:
        payload, _identity = TransactionManager._read_json_file_with_identity(
            path, label
        )
        return payload

    @staticmethod
    def _read_json_file_with_identity(
        path: Path, label: str
    ) -> tuple[object, _FileIdentity]:
        directory_fd = TransactionManager._open_directory(path.parent, label)
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path.name, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise TransactionError(
                    f"{label} must be a single-link ordinary file, "
                    f"not a symlink: {path}"
                ) from exc
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise TransactionError(
                    f"{label} must be a single-link ordinary file, "
                    f"not a symlink: {path}"
                )
            if before.st_size > _MAX_JSON_BYTES:
                raise TransactionError(f"{label} is too large")

            chunks: list[bytes] = []
            remaining = _MAX_JSON_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > _MAX_JSON_BYTES:
                raise TransactionError(f"{label} is too large")

            after = os.fstat(descriptor)
            identity = (after.st_dev, after.st_ino)
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
                raise TransactionError(f"{label} changed while being read")
            current = os.stat(
                path.name, dir_fd=directory_fd, follow_symlinks=False
            )
            if (current.st_dev, current.st_ino) != identity:
                raise TransactionError(f"{label} inode changed while being read")
            return json.loads(data.decode("utf-8")), identity
        except TransactionError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransactionError(f"invalid {label}: {exc}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_fd)

    def _ensure_directory(self, path: Path) -> None:
        self._require_contained(path, self.config.root, "local transaction directory")
        try:
            relative = path.relative_to(self.config.root)
        except ValueError as exc:
            raise TransactionError(
                "local transaction directory escapes repository"
            ) from exc
        current = self.config.root
        self._require_ordinary_directory(current, "portable repository root")
        for part in relative.parts:
            current = current / part
            if not current.exists() and not current.is_symlink():
                try:
                    current.mkdir()
                    self._fsync_directory(current.parent)
                except OSError as exc:
                    raise TransactionError(
                        f"cannot create local transaction directory: {current}"
                    ) from exc
            self._require_ordinary_directory(current, "local transaction directory")

    @staticmethod
    def _require_ordinary_directory(path: Path, label: str) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise TransactionError(f"{label} is missing or unreadable: {path}") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise TransactionError(
                f"{label} must be an ordinary directory, not a symlink: {path}"
            )

    @staticmethod
    def _open_directory(path: Path, label: str) -> int:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise OSError("not a directory")
            return descriptor
        except OSError as exc:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise TransactionError(
                f"{label} directory is unsafe or unreadable: {path}"
            ) from exc

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = TransactionManager._open_directory(path, "transaction")
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise TransactionError(
                f"cannot sync transaction directory: {path}"
            ) from exc
        finally:
            os.close(descriptor)

    @staticmethod
    def _require_ordinary_file(path: Path, label: str) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise TransactionError(
                f"{label} must be a single-link ordinary file, not a symlink: {path}"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise TransactionError(
                f"{label} must be a single-link ordinary file, not a symlink: {path}"
            )

    @staticmethod
    def _require_contained(
        path: Path, root: Path, label: str, *, strict_child: bool = False
    ) -> None:
        absolute_root = root.absolute()
        absolute_path = path.absolute()
        try:
            relative = absolute_path.relative_to(absolute_root)
        except ValueError as exc:
            raise TransactionError(f"{label} escapes {root}") from exc
        if strict_child and not relative.parts:
            raise TransactionError(f"{label} must be below {root}")
        try:
            resolved_root = root.resolve(strict=False)
            resolved_path = path.resolve(strict=False)
            resolved_relative = resolved_path.relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise TransactionError(
                f"{label} escapes {root} through an unsafe symlink or component"
            ) from exc
        if strict_child and not resolved_relative.parts:
            raise TransactionError(f"{label} must be below {root}")

    def _require_managed_tree(self, workspace: Path) -> None:
        self._require_contained(
            workspace,
            self.transactions_root,
            "transaction workspace",
            strict_child=True,
        )
        for directory, dirnames, filenames in os.walk(
            workspace, topdown=True, followlinks=False
        ):
            current = Path(directory)
            self._require_ordinary_directory(current, "transaction directory")
            for name in sorted(dirnames):
                child = current / name
                self._require_ordinary_directory(child, "transaction directory")
            for name in sorted(filenames):
                child = current / name
                self._require_ordinary_file(child, "transaction workspace file")

    def _remove_workspace(self, workspace: Path) -> None:
        expected = self._workspace_path(workspace.name)
        if workspace != expected or workspace.parent != self.transactions_root:
            raise TransactionError("refusing to remove an unsafe transaction workspace")
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            raise TransactionError(
                "symlink-safe recursive removal is unavailable on this platform"
            )
        self._require_managed_tree(workspace)
        try:
            shutil.rmtree(workspace)
            self._fsync_directory(self.transactions_root)
        except OSError as exc:
            raise TransactionError(
                f"cannot remove transaction workspace: {workspace}"
            ) from exc

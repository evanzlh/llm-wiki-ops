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
from typing import Callable

from obsidian_wiki.config import PortableConfig
from obsidian_wiki.frontmatter import FrontmatterError, parse_frontmatter
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


@dataclass(frozen=True)
class CommitResult:
    transaction_id: str
    created: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]
    operation_path: str


@dataclass(frozen=True)
class _OperationChange:
    transaction_id: str
    completed_at: str
    source_ids: tuple[str, ...]
    created: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]


_TRANSACTION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")
_STATUSES = frozenset({"active", "promoting", "failed", "complete", "restored"})
_METADATA_FIELDS = frozenset(
    {
        "completed_at",
        "created",
        "operation_path",
        "postimages",
        "preimages",
        "removed",
        "snapshot_index",
        "source_ids",
        "started_at",
        "status",
        "transaction_id",
        "updated",
    }
)
_LOCK_FIELDS = frozenset({"started_at", "transaction_id"})
_MAX_LOCK_BYTES = 16 * 1024
_FileIdentity = tuple[int, int]
_REQUIRED_FRONTMATTER = frozenset(
    {"title", "category", "tags", "sources", "created", "updated"}
)
_KNOWLEDGE_DIRECTORIES = frozenset(
    {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
)
_RESERVED_FILES = frozenset({"index.md", "log.md", "hot.md", ".manifest.json"})
_CONTROL_DIRECTORIES = frozenset(
    {
        ".manifest",
        ".obsidian",
        ".obsidian-wiki",
        "_archives",
        "_meta",
        "_raw",
        "_readouts",
        "_staging",
    }
)


def validate_candidate_path(candidate_vault: Path, raw_path: str | Path) -> str:
    """Return one canonical candidate-relative knowledge page path."""
    raw = os.fspath(raw_path)
    if not isinstance(raw, str):
        raise TransactionError("candidate path must be text")
    TransactionManager._validate_relative_path(raw, "candidate path")
    path = PurePosixPath(raw)
    if path.suffix != ".md":
        raise TransactionError(f"candidate path must be a markdown page: {raw!r}")
    if raw in _RESERVED_FILES:
        raise TransactionError(f"candidate path targets a reserved file: {raw!r}")
    if not path.parts or path.parts[0] not in _KNOWLEDGE_DIRECTORIES:
        raise TransactionError(f"candidate path targets a control directory: {raw!r}")
    if any(part in _CONTROL_DIRECTORIES or part.startswith(".") for part in path.parts):
        raise TransactionError(f"candidate path targets a control directory: {raw!r}")
    if path.parts[:2] == ("journal", "operations"):
        raise TransactionError("candidate pages cannot target journal/operations")
    candidate = candidate_vault.joinpath(*path.parts)
    TransactionManager._require_contained(
        candidate,
        candidate_vault,
        "candidate path",
        strict_child=True,
    )
    return raw


def validate_candidate_page(
    candidate_path: Path,
    transaction_source_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Validate candidate bytes and return their canonical portable Source IDs."""
    TransactionManager._require_ordinary_file(candidate_path, "candidate page")
    try:
        text = TransactionManager._read_single_link_bytes(
            candidate_path, "candidate page"
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TransactionError("candidate page must be UTF-8 text") from exc
    try:
        frontmatter = parse_frontmatter(text)
    except FrontmatterError as exc:
        raise TransactionError(f"invalid candidate frontmatter: {exc}") from exc
    fields = set(frontmatter.scalars) | set(frontmatter.lists)
    missing = sorted(_REQUIRED_FRONTMATTER - fields)
    if missing:
        raise TransactionError(
            f"candidate frontmatter is missing required fields: {', '.join(missing)}"
        )
    sources = frontmatter.lists.get("sources")
    if not sources:
        raise TransactionError("candidate frontmatter sources must be a non-empty list")
    if len(set(sources)) != len(sources):
        raise TransactionError("candidate frontmatter sources contain duplicates")
    allowed = set(transaction_source_ids)
    foreign = sorted(set(sources) - allowed)
    if foreign:
        raise TransactionError(
            "candidate frontmatter contains a source outside the transaction: "
            + ", ".join(foreign)
        )
    return tuple(sorted(sources))


class TransactionManager:
    """Own one portable repository's local transaction workspaces and lock."""

    def __init__(
        self,
        config: PortableConfig,
        *,
        operation_writer: Callable[[object], Path] | None = None,
    ) -> None:
        self.config = config
        self.local_state = config.local_state
        self.transactions_root = self.local_state / "transactions"
        self.lock_path = self.local_state / "write.lock"
        self.operation_writer = operation_writer or self._missing_operation_writer
        self._require_contained(self.local_state, self.config.root, "local state")

    @staticmethod
    def _missing_operation_writer(_change: object) -> Path:
        raise TransactionError("operation writer is not configured")

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
                "completed_at": None,
                "created": [],
                "operation_path": None,
                "postimages": {},
                "preimages": preimages,
                "removed": [],
                "snapshot_index": {},
                "source_ids": list(source_ids),
                "started_at": resolved_started_at,
                "status": "active",
                "transaction_id": resolved_id,
                "updated": [],
            }
            self._write_metadata(workspace, payload)
            return self.load(resolved_id)
        except Exception as exc:
            cleanup_errors: list[str] = []
            if workspace_created:
                try:
                    self._remove_workspace(workspace)
                except TransactionError as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            if lock_owned:
                try:
                    self._unlink_owned_lock(resolved_id)
                except TransactionError as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            detail = f": {exc}"
            if cleanup_errors:
                detail += f"; cleanup failed: {'; '.join(cleanup_errors)}"
            raise TransactionError(
                f"cannot begin transaction {resolved_id}{detail}"
            ) from exc

    def load(self, transaction_id: str) -> TransactionRecord:
        workspace = self._workspace_path(transaction_id)
        self._require_ordinary_directory(self.transactions_root, "transactions root")
        self._require_ordinary_directory(workspace, "transaction workspace")

        payload = self._read_metadata_payload(workspace)
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
        preimages = self._load_image_map(
            payload.get("preimages"), "transaction preimages"
        )
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
        self._require_ordinary_directory(self.transactions_root, "transactions root")
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
            self._unlink_owned_lock(transaction_id, expected_identity=lock_identity)

    def mark_delete(self, transaction_id: str, relative_path: str) -> None:
        record = self.load(transaction_id)
        if record.status != "active":
            raise TransactionError(
                f"cannot mark a deletion on {record.status} transaction"
            )
        self._require_owned_lock(transaction_id)
        relative = self._validate_output_path(relative_path, "transaction deletion")
        if relative in record.deletions:
            raise TransactionError(f"duplicate transaction deletion: {relative}")
        candidate = record.candidate_vault / relative
        if candidate.exists() or candidate.is_symlink():
            raise TransactionError(
                f"transaction deletion conflicts with candidate page: {relative}"
            )
        deletions = sorted((*record.deletions, relative))
        self._write_json_atomic(record.workspace / "deletions.json", deletions)

    def commit(
        self,
        transaction_id: str,
        *,
        completed_at: str | None = None,
    ) -> CommitResult:
        record = self.load(transaction_id)
        if record.status != "active":
            raise TransactionError(
                f"only an active transaction can commit, not {record.status}"
            )
        lock_identity = self._require_owned_lock(transaction_id)
        return self._commit_record(
            record,
            completed_at=completed_at,
            lock_identity=lock_identity,
            release_pre_snapshot_failure=False,
        )

    def retry(
        self,
        transaction_id: str,
        *,
        completed_at: str | None = None,
    ) -> CommitResult:
        record = self.load(transaction_id)
        if record.status != "failed":
            raise TransactionError(
                f"only a failed transaction can retry, not {record.status}"
            )
        self._acquire_lock(transaction_id, record.started_at)
        lock_identity = self._require_owned_lock(transaction_id)
        try:
            candidates = self._enumerate_candidates(record)
            affected = set(self._affected_preimage_paths(record, candidates))
            payload = self._read_metadata_payload(record.workspace)
            affected.update(self._load_snapshot_index(payload["snapshot_index"]))
            self._verify_preimages(record, affected)
            self._clear_snapshot_state(record)
            record = self.load(transaction_id)
            return self._commit_record(
                record,
                completed_at=completed_at,
                lock_identity=lock_identity,
                release_pre_snapshot_failure=True,
            )
        except Exception:
            if self.lock_path.exists() or self.lock_path.is_symlink():
                try:
                    self._unlink_owned_lock(
                        transaction_id, expected_identity=lock_identity
                    )
                except TransactionError:
                    pass
            raise

    def restore(self, transaction_id: str) -> None:
        record = self.load(transaction_id)
        if record.status not in {"failed", "complete", "restored"}:
            raise TransactionError(
                f"cannot restore {record.status} transaction {transaction_id}"
            )
        self._acquire_lock(transaction_id, record.started_at)
        lock_identity = self._require_owned_lock(transaction_id)
        try:
            if record.status == "restored":
                return
            payload = self._read_metadata_payload(record.workspace)
            postimages = self._load_image_map(
                payload["postimages"], "transaction postimages"
            )
            if record.status == "complete":
                for relative, expected in postimages.items():
                    try:
                        current = self._current_vault_hash(relative)
                    except TransactionError as exc:
                        raise TransactionError(
                            "transaction output changed after transaction completed: "
                            + relative
                        ) from exc
                    if current != expected:
                        raise TransactionError(
                            f"transaction output changed after transaction completed: {relative}"
                        )
            snapshot_index = self._load_snapshot_index(payload["snapshot_index"])
            self._restore_snapshot_index(record, snapshot_index)
            payload["status"] = "restored"
            self._write_metadata(record.workspace, payload)
        finally:
            if self.lock_path.exists() or self.lock_path.is_symlink():
                self._unlink_owned_lock(transaction_id, expected_identity=lock_identity)

    def discard(self, transaction_id: str) -> None:
        workspace = self._workspace_path(transaction_id)
        if not workspace.exists() and not workspace.is_symlink():
            return
        record = self.load(transaction_id)
        if record.status not in {"failed", "complete", "restored"}:
            raise TransactionError(
                f"cannot discard {record.status} transaction; active or promoting work is retained"
            )
        self._acquire_lock(transaction_id, record.started_at)
        lock_identity = self._require_owned_lock(transaction_id)
        removed = False
        try:
            self._remove_workspace(record.workspace)
            removed = True
        finally:
            if self.lock_path.exists() or self.lock_path.is_symlink():
                self._unlink_owned_lock(transaction_id, expected_identity=lock_identity)
        if not removed:
            raise TransactionError(f"cannot discard transaction {transaction_id}")

    def _commit_record(
        self,
        record: TransactionRecord,
        *,
        completed_at: str | None,
        lock_identity: _FileIdentity,
        release_pre_snapshot_failure: bool,
    ) -> CommitResult:
        resolved_completed_at = completed_at or self._utc_now()
        self._validate_started_at(resolved_completed_at)
        snapshot_started = False
        payload = self._read_metadata_payload(record.workspace)
        snapshot_index: dict[str, str | None] = {}
        operation_relative: str | None = None
        operation_before: dict[str, str] | None = None
        operation_before_index: dict[str, str] = {}
        try:
            candidates = self._enumerate_candidates(record)
            candidate_names = tuple(relative for relative, _path in candidates)
            overlap = sorted(set(candidate_names) & set(record.deletions))
            if overlap:
                raise TransactionError(
                    "candidate and deletion target the same page: " + ", ".join(overlap)
                )
            affected = self._affected_preimage_paths(record, candidates)
            self._verify_preimages(record, affected)

            created = tuple(
                sorted(
                    relative
                    for relative in candidate_names
                    if record.preimages.get(relative) is None
                )
            )
            updated = tuple(sorted(set(candidate_names) - set(created)))
            removed = tuple(
                sorted(
                    relative
                    for relative in record.deletions
                    if record.preimages.get(relative) is not None
                )
            )

            snapshot_started = True
            snapshot_index = self._snapshot_targets(record, affected)
            payload.update(
                {
                    "completed_at": resolved_completed_at,
                    "created": list(created),
                    "operation_path": None,
                    "postimages": {},
                    "removed": list(removed),
                    "snapshot_index": snapshot_index,
                    "status": "promoting",
                    "updated": list(updated),
                }
            )
            self._write_metadata(record.workspace, payload)

            for relative, candidate in candidates:
                self._promote_candidate(candidate, self._vault_path(relative))
            for relative in record.deletions:
                self._delete_vault_target(relative)

            pages_by_source = self._scan_page_relationships(record.source_ids)
            manifest = ShardedManifest(self.config)
            for source_id in record.source_ids:
                manifest.upsert(
                    manifest.source_path(source_id),
                    pages=list(pages_by_source[source_id]),
                    compiled_at=resolved_completed_at,
                )

            change = _OperationChange(
                transaction_id=record.transaction_id,
                completed_at=resolved_completed_at,
                source_ids=record.source_ids,
                created=created,
                updated=updated,
                removed=removed,
            )
            operation_before, operation_before_index = self._snapshot_operation_tree(
                record
            )
            operation_path = Path(self.operation_writer(change))
            operation_relative = self._validate_operation_result(operation_path)
            operation_after = self._operation_tree_state(allow_unsafe=True)
            operation_rollback = self._operation_tree_diff(
                operation_before,
                operation_after,
                operation_before_index,
            )
            added = set(operation_after) - set(operation_before)
            modified_or_removed = {
                relative
                for relative, content_hash in operation_before.items()
                if operation_after.get(relative) != content_hash
            }
            if added != {operation_relative} or modified_or_removed:
                snapshot_index.update(operation_rollback)
                raise TransactionError(
                    "operation writer must create exactly one new operation page "
                    "without modifying or removing existing operation pages"
                )
            snapshot_index[operation_relative] = None

            postimage_paths = sorted(set(affected) | {operation_relative})
            postimages = {
                relative: self._current_vault_hash(relative)
                for relative in postimage_paths
            }
            payload.update(
                {
                    "operation_path": operation_relative,
                    "postimages": postimages,
                    "snapshot_index": dict(sorted(snapshot_index.items())),
                    "status": "complete",
                }
            )
            self._write_metadata(record.workspace, payload)
            self._unlink_owned_lock(
                record.transaction_id, expected_identity=lock_identity
            )
            return CommitResult(
                transaction_id=record.transaction_id,
                created=created,
                updated=updated,
                removed=removed,
                operation_path=operation_relative,
            )
        except Exception as exc:
            if not snapshot_started:
                if release_pre_snapshot_failure and (
                    self.lock_path.exists() or self.lock_path.is_symlink()
                ):
                    self._unlink_owned_lock(
                        record.transaction_id, expected_identity=lock_identity
                    )
                if isinstance(exc, TransactionError):
                    raise
                raise TransactionError(str(exc)) from exc
            rollback_errors: list[str] = []
            if operation_before is not None:
                try:
                    operation_after = self._operation_tree_state(allow_unsafe=True)
                    snapshot_index.update(
                        self._operation_tree_diff(
                            operation_before,
                            operation_after,
                            operation_before_index,
                        )
                    )
                except (OSError, TransactionError) as rollback_exc:
                    rollback_errors.append(
                        f"operation cleanup discovery failed: {rollback_exc}"
                    )
            try:
                self._restore_snapshot_index(record, snapshot_index)
            except (OSError, TransactionError) as rollback_exc:
                rollback_errors.append(f"restore failed: {rollback_exc}")
            payload.update(
                {
                    "operation_path": operation_relative,
                    "postimages": {},
                    "snapshot_index": dict(sorted(snapshot_index.items())),
                    "status": "failed",
                }
            )
            try:
                self._write_metadata(record.workspace, payload)
            except (OSError, TransactionError) as rollback_exc:
                rollback_errors.append(f"metadata failed: {rollback_exc}")
            if self.lock_path.exists() or self.lock_path.is_symlink():
                try:
                    self._unlink_owned_lock(
                        record.transaction_id, expected_identity=lock_identity
                    )
                except TransactionError as rollback_exc:
                    rollback_errors.append(f"lock release failed: {rollback_exc}")
            detail = f"transaction {record.transaction_id} rolled back: {exc}"
            if rollback_errors:
                detail += "; " + "; ".join(rollback_errors)
            raise TransactionError(detail) from exc

    def _require_owned_lock(self, transaction_id: str) -> _FileIdentity:
        lock, identity = self._read_lock()
        if lock["transaction_id"] != transaction_id:
            raise TransactionError(
                f"transaction lock belongs to {lock['transaction_id']}, not {transaction_id}"
            )
        return identity

    def _validate_output_path(self, raw: str, label: str) -> str:
        try:
            relative = validate_candidate_path(self.config.vault, raw)
        except TransactionError as exc:
            message = str(exc).replace("candidate path", f"{label} path")
            message = message.replace("candidate pages", f"{label} pages")
            raise TransactionError(message) from exc
        self._vault_path(relative)
        return relative

    def _enumerate_candidates(
        self, record: TransactionRecord
    ) -> tuple[tuple[str, Path], ...]:
        candidates: list[tuple[str, Path]] = []
        for directory, dirnames, filenames in os.walk(
            record.candidate_vault, topdown=True, followlinks=False
        ):
            current = Path(directory)
            self._require_ordinary_directory(current, "candidate directory")
            for name in sorted(dirnames):
                self._require_ordinary_directory(current / name, "candidate directory")
            dirnames[:] = sorted(dirnames)
            for name in sorted(filenames):
                candidate = current / name
                relative = candidate.relative_to(record.candidate_vault).as_posix()
                validate_candidate_path(record.candidate_vault, relative)
                validate_candidate_page(candidate, record.source_ids)
                candidates.append((relative, candidate))
        return tuple(sorted(candidates, key=lambda item: item[0]))

    def _affected_preimage_paths(
        self,
        record: TransactionRecord,
        candidates: tuple[tuple[str, Path], ...],
    ) -> tuple[str, ...]:
        manifest = ShardedManifest(self.config)
        affected = {relative for relative, _candidate in candidates}
        affected.update(record.deletions)
        for source_id in record.source_ids:
            shard = manifest.entry_path(source_id)
            try:
                affected.add(shard.relative_to(self.config.vault).as_posix())
            except ValueError as exc:
                raise TransactionError(
                    "manifest shard escapes the portable vault"
                ) from exc
        return tuple(sorted(affected))

    def _verify_preimages(
        self, record: TransactionRecord, affected: tuple[str, ...] | set[str]
    ) -> None:
        for relative in sorted(affected):
            expected = record.preimages.get(relative)
            try:
                current = self._current_vault_hash(relative)
            except TransactionError as exc:
                raise TransactionError(
                    f"transaction target changed after transaction began: {relative}"
                ) from exc
            if current != expected:
                raise TransactionError(
                    f"transaction target changed after transaction began: {relative}"
                )

    def _snapshot_targets(
        self, record: TransactionRecord, affected: tuple[str, ...]
    ) -> dict[str, str | None]:
        self._clear_snapshot_files(record)
        originals = record.workspace / "snapshots" / "originals"
        originals.mkdir()
        self._fsync_directory(originals.parent)
        index: dict[str, str | None] = {}
        for relative in affected:
            target = self._vault_path(relative)
            current = self._current_vault_hash(relative)
            if current is None:
                index[relative] = None
                continue
            snapshot_relative = PurePosixPath("originals") / PurePosixPath(relative)
            snapshot = record.workspace / "snapshots" / snapshot_relative
            self._ensure_contained_directory(snapshot.parent, originals)
            data = self._read_single_link_bytes(target, "transaction target")
            self._write_atomic_bytes(snapshot, data)
            index[relative] = snapshot_relative.as_posix()
        return dict(sorted(index.items()))

    def _clear_snapshot_state(self, record: TransactionRecord) -> None:
        payload = self._read_metadata_payload(record.workspace)
        payload.update(
            {
                "completed_at": None,
                "created": [],
                "operation_path": None,
                "postimages": {},
                "removed": [],
                "snapshot_index": {},
                "status": "failed",
                "updated": [],
            }
        )
        self._write_metadata(record.workspace, payload)
        self._clear_snapshot_files(record)

    def _clear_snapshot_files(self, record: TransactionRecord) -> None:
        snapshots = record.workspace / "snapshots"
        self._require_ordinary_directory(snapshots, "snapshot directory")
        originals = snapshots / "originals"
        if not originals.exists() and not originals.is_symlink():
            return
        self._require_contained(
            originals, snapshots, "snapshot originals", strict_child=True
        )
        self._require_managed_tree(originals)
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            raise TransactionError(
                "symlink-safe recursive removal is unavailable on this platform"
            )
        try:
            shutil.rmtree(originals)
            self._fsync_directory(snapshots)
        except OSError as exc:
            raise TransactionError("cannot clear transaction snapshots") from exc

    def _restore_snapshot_index(
        self,
        record: TransactionRecord,
        snapshot_index: dict[str, str | None],
    ) -> None:
        snapshots = record.workspace / "snapshots"
        for relative in sorted(snapshot_index):
            stored = snapshot_index[relative]
            target = self._vault_path(relative)
            if stored is None:
                if target.exists() or target.is_symlink():
                    try:
                        metadata = target.lstat()
                    except OSError as exc:
                        raise TransactionError(
                            f"cannot inspect rollback target: {relative}"
                        ) from exc
                    if stat.S_ISDIR(metadata.st_mode):
                        raise TransactionError(
                            f"rollback target became a directory: {relative}"
                        )
                    target.unlink()
                    self._fsync_directory(target.parent)
                continue
            self._validate_relative_path(stored, "snapshot path")
            snapshot = snapshots.joinpath(*PurePosixPath(stored).parts)
            self._require_contained(
                snapshot, snapshots, "snapshot path", strict_child=True
            )
            data = self._read_single_link_bytes(snapshot, "transaction snapshot")
            self._replace_vault_bytes(target, data)

    def _promote_candidate(self, candidate: Path, target: Path) -> None:
        data = self._read_single_link_bytes(candidate, "candidate page")
        self._replace_vault_bytes(target, data)

    def _replace_vault_bytes(self, target: Path, data: bytes) -> None:
        self._require_contained(
            target, self.config.vault, "vault target", strict_child=True
        )
        self._ensure_contained_directory(target.parent, self.config.vault)
        self._write_atomic_bytes(target, data)

    def _delete_vault_target(self, relative: str) -> None:
        target = self._vault_path(relative)
        if not target.exists() and not target.is_symlink():
            return
        self._require_ordinary_file(target, "transaction deletion target")
        try:
            target.unlink()
            self._fsync_directory(target.parent)
        except OSError as exc:
            raise TransactionError(
                f"cannot delete transaction target: {relative}"
            ) from exc

    def _scan_page_relationships(
        self, source_ids: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        manifest = ShardedManifest(self.config)
        selected = set(source_ids)
        relationships: dict[str, list[str]] = {
            source_id: [] for source_id in source_ids
        }
        for category in sorted(_KNOWLEDGE_DIRECTORIES):
            root = self.config.vault / category
            if not root.exists() and not root.is_symlink():
                continue
            self._require_ordinary_directory(root, "knowledge directory")
            for directory, dirnames, filenames in os.walk(
                root, topdown=True, followlinks=False
            ):
                current = Path(directory)
                self._require_ordinary_directory(current, "knowledge directory")
                kept: list[str] = []
                for name in sorted(dirnames):
                    child = current / name
                    if (
                        category == "journal"
                        and current == root
                        and name == "operations"
                    ):
                        continue
                    self._require_ordinary_directory(child, "knowledge directory")
                    kept.append(name)
                dirnames[:] = kept
                for name in sorted(filenames):
                    if not name.endswith(".md"):
                        continue
                    page = current / name
                    self._require_ordinary_file(page, "knowledge page")
                    relative = page.relative_to(self.config.vault).as_posix()
                    try:
                        text = self._read_single_link_bytes(
                            page, "knowledge page"
                        ).decode("utf-8")
                        frontmatter = parse_frontmatter(text)
                    except (UnicodeDecodeError, FrontmatterError) as exc:
                        raise TransactionError(
                            f"invalid knowledge page frontmatter: {relative}: {exc}"
                        ) from exc
                    fields = set(frontmatter.scalars) | set(frontmatter.lists)
                    missing = sorted(_REQUIRED_FRONTMATTER - fields)
                    if missing:
                        raise TransactionError(
                            f"knowledge page frontmatter missing required fields: {relative}: "
                            + ", ".join(missing)
                        )
                    page_sources = frontmatter.lists.get("sources")
                    if not page_sources:
                        raise TransactionError(
                            f"knowledge page sources must be a non-empty list: {relative}"
                        )
                    for source_id in page_sources:
                        try:
                            manifest.source_path(source_id)
                        except ManifestError as exc:
                            raise TransactionError(
                                f"invalid knowledge page source {source_id!r}: {relative}"
                            ) from exc
                        if source_id in selected:
                            relationships[source_id].append(relative)
        return {
            source_id: tuple(sorted(set(pages)))
            for source_id, pages in sorted(relationships.items())
        }

    def _validate_operation_result(self, operation_path: Path) -> str:
        candidate = operation_path
        if not candidate.is_absolute():
            candidate = self.config.vault / candidate
        self._require_contained(
            candidate,
            self.config.vault,
            "operation path",
            strict_child=True,
        )
        try:
            relative = candidate.relative_to(self.config.vault).as_posix()
        except ValueError as exc:
            raise TransactionError("operation path escapes the portable vault") from exc
        self._validate_relative_path(relative, "operation path")
        path = PurePosixPath(relative)
        if path.suffix != ".md" or path.parts[:2] != ("journal", "operations"):
            raise TransactionError(
                "operation writer must return a markdown page below journal/operations"
            )
        self._require_ordinary_file(candidate, "operation page")
        return relative

    def _snapshot_operation_tree(
        self, record: TransactionRecord
    ) -> tuple[dict[str, str], dict[str, str]]:
        raw_state = self._operation_tree_state()
        state = {relative: content_hash for relative, content_hash in raw_state.items()}
        originals = record.workspace / "snapshots" / "originals"
        index: dict[str, str] = {}
        for relative in sorted(state):
            snapshot_relative = PurePosixPath("originals") / PurePosixPath(relative)
            snapshot = record.workspace / "snapshots" / snapshot_relative
            self._ensure_contained_directory(snapshot.parent, originals)
            data = self._read_single_link_bytes(
                self._vault_path(relative), "operation page"
            )
            self._write_atomic_bytes(snapshot, data)
            index[relative] = snapshot_relative.as_posix()
        return state, index

    @staticmethod
    def _operation_tree_diff(
        before: dict[str, str],
        after: dict[str, str | None],
        before_index: dict[str, str],
    ) -> dict[str, str | None]:
        changed = {
            relative: before_index[relative]
            for relative, content_hash in before.items()
            if after.get(relative) != content_hash
        }
        changed.update({relative: None for relative in set(after) - set(before)})
        return dict(sorted(changed.items()))

    def _operation_tree_state(
        self, *, allow_unsafe: bool = False
    ) -> dict[str, str | None]:
        root = self.config.vault / "journal" / "operations"
        if not root.exists() and not root.is_symlink():
            return {}
        self._require_ordinary_directory(root, "operation directory")
        result: dict[str, str | None] = {}
        for directory, dirnames, filenames in os.walk(
            root, topdown=True, followlinks=False
        ):
            current = Path(directory)
            self._require_ordinary_directory(current, "operation directory")
            for name in sorted(dirnames):
                self._require_ordinary_directory(current / name, "operation directory")
            dirnames[:] = sorted(dirnames)
            for name in sorted(filenames):
                page = current / name
                relative = page.relative_to(self.config.vault).as_posix()
                try:
                    result[relative] = self._hash_single_link_file(
                        page, "operation page"
                    )
                except TransactionError:
                    if not allow_unsafe:
                        raise
                    result[relative] = None
        return dict(sorted(result.items()))

    def _vault_path(self, relative: str) -> Path:
        self._validate_relative_path(relative, "vault-relative path")
        target = self.config.vault.joinpath(*PurePosixPath(relative).parts)
        self._require_contained(
            target, self.config.vault, "vault-relative path", strict_child=True
        )
        return target

    def _current_vault_hash(self, relative: str) -> str | None:
        target = self._vault_path(relative)
        if not target.exists() and not target.is_symlink():
            return None
        return self._hash_single_link_file(target, "transaction target")

    def _ensure_contained_directory(self, path: Path, root: Path) -> None:
        self._require_contained(path, root, "transaction directory")
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise TransactionError("transaction directory escapes its root") from exc
        current = root
        self._require_ordinary_directory(current, "transaction directory")
        for part in relative.parts:
            current = current / part
            if not current.exists() and not current.is_symlink():
                current.mkdir()
                self._fsync_directory(current.parent)
            self._require_ordinary_directory(current, "transaction directory")

    def _write_atomic_bytes(self, target: Path, data: bytes) -> None:
        self._require_ordinary_directory(target.parent, "atomic write directory")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
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
    def _read_single_link_bytes(path: Path, label: str) -> bytes:
        TransactionManager._require_ordinary_file(path, label)
        directory_fd = TransactionManager._open_directory(path.parent, label)
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path.name, flags, dir_fd=directory_fd)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise TransactionError(
                    f"{label} must be a single-link ordinary file: {path}"
                )
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
                raise TransactionError(f"{label} changed while being read: {path}")
            current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino):
                raise TransactionError(f"{label} inode changed while being read")
            return b"".join(chunks)
        except TransactionError:
            raise
        except OSError as exc:
            raise TransactionError(f"cannot read {label}: {path}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_fd)

    def _read_metadata_payload(self, workspace: Path) -> dict[str, object]:
        payload = self._read_json_file(
            workspace / "metadata.json", "transaction metadata"
        )
        if not isinstance(payload, dict) or set(payload) != _METADATA_FIELDS:
            raise TransactionError("transaction metadata has invalid fields")
        self._validate_recovery_metadata(payload)
        return payload

    def _validate_recovery_metadata(self, payload: dict[str, object]) -> None:
        completed_at = payload.get("completed_at")
        if completed_at is not None:
            try:
                self._validate_started_at(completed_at)
            except TransactionError as exc:
                raise TransactionError("transaction completed_at is invalid") from exc
        self._load_image_map(payload.get("preimages"), "transaction preimages")
        self._load_image_map(payload.get("postimages"), "transaction postimages")
        self._load_snapshot_index(payload.get("snapshot_index"))
        for field in ("created", "updated", "removed"):
            raw = payload.get(field)
            if not isinstance(raw, list) or any(
                not isinstance(item, str) for item in raw
            ):
                raise TransactionError(f"transaction {field} must be a string list")
            values = tuple(raw)
            if values != tuple(sorted(set(values))):
                raise TransactionError(f"transaction {field} must be unique and sorted")
            for relative in values:
                self._validate_output_path(relative, f"transaction {field}")
        operation_path = payload.get("operation_path")
        if operation_path is not None:
            if not isinstance(operation_path, str):
                raise TransactionError("transaction operation_path must be a string")
            self._validate_relative_path(operation_path, "transaction operation path")
            operation = PurePosixPath(operation_path)
            if operation.suffix != ".md" or operation.parts[:2] != (
                "journal",
                "operations",
            ):
                raise TransactionError(
                    "transaction operation_path must be below journal/operations"
                )

    def _load_image_map(self, raw: object, label: str) -> dict[str, str | None]:
        if not isinstance(raw, dict):
            raise TransactionError(f"{label} must be an object")
        result: dict[str, str | None] = {}
        for relative, content_hash in raw.items():
            if not isinstance(relative, str):
                raise TransactionError(f"{label} path must be a string")
            self._validate_relative_path(relative, f"{label} path")
            if content_hash is not None and (
                not isinstance(content_hash, str)
                or _HASH_RE.fullmatch(content_hash) is None
            ):
                raise TransactionError(f"{label} hash is invalid for {relative!r}")
            result[relative] = content_hash
        if list(result) != sorted(result):
            raise TransactionError(f"{label} paths must be sorted")
        return result

    def _load_snapshot_index(self, raw: object) -> dict[str, str | None]:
        if not isinstance(raw, dict):
            raise TransactionError("transaction snapshot_index must be an object")
        result: dict[str, str | None] = {}
        for relative, snapshot in raw.items():
            if not isinstance(relative, str):
                raise TransactionError("transaction snapshot target must be a string")
            self._validate_relative_path(relative, "transaction snapshot target")
            if snapshot is not None:
                if not isinstance(snapshot, str):
                    raise TransactionError("transaction snapshot path must be a string")
                self._validate_relative_path(snapshot, "transaction snapshot path")
                if not snapshot.startswith("originals/"):
                    raise TransactionError(
                        "transaction snapshot path is outside originals"
                    )
            result[relative] = snapshot
        if list(result) != sorted(result):
            raise TransactionError("transaction snapshot targets must be sorted")
        return result

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
        if (
            not isinstance(transaction_id, str)
            or _TRANSACTION_ID_RE.fullmatch(transaction_id) is None
        ):
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
            self.lock_path,
            "transaction lock",
            max_bytes=_MAX_LOCK_BYTES,
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
        self._write_atomic_bytes(target, self._canonical_json(payload).encode("utf-8"))

    @staticmethod
    def _canonical_json(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

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

    def _load_deletions(self, path: Path) -> tuple[str, ...]:
        raw = self._read_json_file(path, "transaction deletions")
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise TransactionError("transaction deletions must be a string list")
        values = tuple(raw)
        if values != tuple(sorted(set(values))):
            raise TransactionError("transaction deletions must be unique and sorted")
        for value in values:
            self._validate_output_path(value, "transaction deletion")
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
    def _read_json_file(
        path: Path, label: str, *, max_bytes: int | None = None
    ) -> object:
        payload, _identity = TransactionManager._read_json_file_with_identity(
            path, label, max_bytes=max_bytes
        )
        return payload

    @staticmethod
    def _read_json_file_with_identity(
        path: Path,
        label: str,
        *,
        max_bytes: int | None = None,
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
            if max_bytes is not None and before.st_size > max_bytes:
                raise TransactionError(f"{label} is too large")

            chunks: list[bytes] = []
            remaining = max_bytes + 1 if max_bytes is not None else None
            while remaining is None or remaining > 0:
                read_size = 64 * 1024
                if remaining is not None:
                    read_size = min(read_size, remaining)
                chunk = os.read(descriptor, read_size)
                if not chunk:
                    break
                chunks.append(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
            data = b"".join(chunks)
            if max_bytes is not None and len(data) > max_bytes:
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
            current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
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
            try:
                relative_current = current.relative_to(workspace)
            except ValueError:
                relative_current = Path()
            in_candidates = bool(
                relative_current.parts and relative_current.parts[0] == "wiki"
            )
            directory_label = (
                "candidate directory" if in_candidates else "transaction directory"
            )
            file_label = (
                "candidate page" if in_candidates else "transaction workspace file"
            )
            self._require_ordinary_directory(current, directory_label)
            for name in sorted(dirnames):
                child = current / name
                child_is_candidate = in_candidates or (
                    current == workspace and name == "wiki"
                )
                self._require_ordinary_directory(
                    child,
                    "candidate directory"
                    if child_is_candidate
                    else "transaction directory",
                )
            for name in sorted(filenames):
                child = current / name
                self._require_ordinary_file(child, file_label)

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

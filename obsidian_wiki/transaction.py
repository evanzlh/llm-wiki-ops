from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from obsidian_wiki.config import PortableConfig
from obsidian_wiki.frontmatter import FrontmatterError, parse_frontmatter
from obsidian_wiki.operations import OperationChange, write_operation
from obsidian_wiki.safe_files import stable_directory_identity
from obsidian_wiki.portable_manifest import (
    ManifestError,
    ManifestPreconditionError,
    ShardedManifest,
)
from obsidian_wiki.transaction_validation import (
    ProspectivePage,
    TransactionValidationReport,
    ValidationIssue,
    validate_prospective_pages,
)

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    _msvcrt = None

_SUPPORTS_DIR_FD = all(
    function in os.supports_dir_fd for function in (os.open, os.stat, os.unlink)
)
_SUPPORTS_DIRECTORY_FSYNC = os.name != "nt"
_SUPPORTS_SAFE_RMTREE = bool(getattr(shutil.rmtree, "avoids_symlink_attacks", False))
_TOMBSTONE_PREFIX = ".tombstone-"


class TransactionError(RuntimeError):
    pass


class _MutationPreconditionError(TransactionError):
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
class _Candidate:
    relative: str
    path: Path
    data: bytes


_TRANSACTION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")
_STATUSES = frozenset({"active", "promoting", "failed", "complete", "restored"})
_METADATA_FIELDS = frozenset(
    {
        "completed_at",
        "created",
        "operation_guard",
        "operation_path",
        "operation_paths",
        "postimages",
        "preimages",
        "residual_postimages",
        "rollback_exclusions",
        "removed",
        "snapshot_index",
        "source_ids",
        "source_preimages",
        "started_at",
        "status",
        "transaction_id",
        "updated",
        "writer_guard",
        "writer_prepared",
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
        "_meta",
    }
)
_TRACKED_ROOT_GRAPH_PAGES = frozenset({"index.md", "log.md"})


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
    data = TransactionManager._read_single_link_bytes(candidate_path, "candidate page")
    return _validate_candidate_bytes(data, transaction_source_ids)


def _validate_candidate_bytes(
    data: bytes,
    transaction_source_ids: tuple[str, ...],
) -> tuple[str, ...]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TransactionError("candidate page must be UTF-8 text") from exc
    try:
        frontmatter = parse_frontmatter(text)
    except FrontmatterError as exc:
        raise TransactionError(f"invalid candidate frontmatter: {exc}") from exc
    fields = frontmatter.fields
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
        self.action_lock_path = self.local_state / "action.lock"
        self.operation_writer = operation_writer or (
            lambda change: write_operation(
                self.config.vault,
                change,
                cleanup_root=self.local_state,
            )
        )
        self._action_manifest: ShardedManifest | None = None
        self._require_contained(self.local_state, self.config.root, "local state")

    def begin(
        self,
        sources: list[Path],
        *,
        transaction_id: str | None = None,
        started_at: str | None = None,
    ) -> TransactionRecord:
        self._verify_repository_identity()
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
            manifest = ShardedManifest(self.config)
            source_preimages = {
                source_id: self._hash_single_link_file(
                    manifest.source_path(source_id), "transaction source"
                )
                for source_id in source_ids
            }
            payload = {
                "completed_at": None,
                "created": [],
                "operation_guard": {},
                "operation_path": None,
                "operation_paths": [],
                "postimages": {},
                "preimages": preimages,
                "residual_postimages": {},
                "rollback_exclusions": {},
                "removed": [],
                "snapshot_index": {},
                "source_ids": list(source_ids),
                "source_preimages": dict(sorted(source_preimages.items())),
                "started_at": resolved_started_at,
                "status": "active",
                "transaction_id": resolved_id,
                "updated": [],
                "writer_guard": {},
                "writer_prepared": False,
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
        self._load_source_preimages(payload.get("source_preimages"), source_ids)
        preimages = self._load_image_map(
            payload.get("preimages"), "transaction preimages"
        )
        deletions = self._load_deletions(workspace / "deletions.json")

        self._require_managed_tree(workspace)
        candidate_vault = workspace / "wiki"
        self._require_ordinary_directory(candidate_vault, "candidate vault")
        self._require_ordinary_directory(workspace / "snapshots", "snapshot directory")
        candidate_names = self._candidate_path_names(candidate_vault)
        self._validate_metadata_semantics(
            workspace=workspace,
            payload=payload,
            status=status,
            source_ids=source_ids,
            preimages=preimages,
            deletions=deletions,
            candidate_names=candidate_names,
        )
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
            if path.name.startswith(_TOMBSTONE_PREFIX):
                continue
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

    def validate(self, transaction_id: str) -> TransactionValidationReport:
        """Validate one transaction without mutating its workspace or live vault."""
        record = self.load(transaction_id)
        if record.status not in {"active", "failed"}:
            raise TransactionError(
                f"cannot validate {record.status} transaction {transaction_id}"
            )
        _, report = self._validate_record(record)
        return report

    def abort(self, transaction_id: str) -> None:
        self._verify_repository_identity()
        with self._action_lock():
            record = self.load(transaction_id)
            if record.status == "promoting":
                raise TransactionError(
                    "promoting transaction retains recovery snapshots; use restore"
                )
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
        self._verify_repository_identity()
        with self._action_lock():
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
        self._verify_repository_identity()
        try:
            with self._action_lock(manifest=True):
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
                    manifest=self._action_manifest,
                )
        except TransactionError as exc:
            if "safe manifest mutation requires" in str(exc):
                record = self.load(transaction_id)
                payload = self._read_metadata_payload(record.workspace)
                payload["status"] = "failed"
                payload["residual_postimages"] = {}
                payload["rollback_exclusions"] = {}
                self._write_metadata(record.workspace, payload)
                if self.lock_path.exists() or self.lock_path.is_symlink():
                    self._unlink_owned_lock(transaction_id)
            raise

    def retry(
        self,
        transaction_id: str,
        *,
        completed_at: str | None = None,
    ) -> CommitResult:
        self._verify_repository_identity()
        with self._action_lock(manifest=True):
            record = self.load(transaction_id)
            if record.status != "failed":
                raise TransactionError(
                    f"only a failed transaction can retry, not {record.status}"
                )
            self._acquire_lock(transaction_id, record.started_at)
            lock_identity = self._require_owned_lock(transaction_id)
            try:
                candidates = self._read_candidate_files(record)
                payload = self._read_metadata_payload(record.workspace)
                if (
                    self._load_residual_postimages(payload["residual_postimages"])
                    is None
                ):
                    raise TransactionError(
                        "failed transaction residual state is unknown; "
                        "discard is required"
                    )
                affected_candidates = tuple(
                    item
                    for item in candidates
                    if self._candidate_path_issue(record, item) is None
                )
                affected = set(
                    self._affected_preimage_paths(record, affected_candidates)
                )
                snapshot_index = self._load_snapshot_index(payload["snapshot_index"])
                rollback_exclusions = self._verify_rollback_exclusions(payload)
                cleanup = self._persisted_writer_cleanup(
                    record,
                    payload,
                    snapshot_index,
                    base_affected=affected,
                    rollback_exclusions=set(rollback_exclusions),
                )
                self._verify_failed_residual_postimages(
                    record,
                    payload,
                    cleanup,
                    base_affected=affected,
                    rollback_exclusions=set(rollback_exclusions),
                )
                self._verify_preimages(record, affected - set(cleanup))
                recovered_files = self._virtual_recovered_files(record, cleanup)
                validation = self._content_validation_report(
                    record,
                    candidates,
                    live_overrides=recovered_files,
                )
                self._raise_validation_failure(validation)
                self._restore_persisted_writer_guard(
                    record,
                    payload,
                    snapshot_index,
                    base_affected=affected,
                    rollback_exclusions=set(rollback_exclusions),
                    cleanup=cleanup,
                )
                self._verify_preimages(record, affected)
                self._clear_snapshot_state(record)
                record = self.load(transaction_id)
                return self._commit_record(
                    record,
                    completed_at=completed_at,
                    lock_identity=lock_identity,
                    release_pre_snapshot_failure=True,
                    preflight_candidates=candidates,
                    manifest=self._action_manifest,
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
        self._verify_repository_identity()
        with self._action_lock(manifest=True):
            record = self.load(transaction_id)
            if record.status not in {"promoting", "failed", "complete", "restored"}:
                raise TransactionError(
                    f"cannot restore {record.status} transaction {transaction_id}"
                )
            if self.lock_path.exists() or self.lock_path.is_symlink():
                lock_identity = self._require_owned_lock(transaction_id)
            else:
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
                                "transaction output changed after transaction completed: "
                                + relative
                            )
                snapshot_index = self._load_snapshot_index(payload["snapshot_index"])
                candidates = self._enumerate_candidates(record)
                base_affected = set(self._affected_preimage_paths(record, candidates))
                rollback_exclusions: dict[str, str | None] = {}
                cleanup: dict[str, str | None] | None = None
                if record.status == "failed":
                    rollback_exclusions = self._verify_rollback_exclusions(payload)
                    if (
                        self._load_residual_postimages(payload["residual_postimages"])
                        is None
                    ):
                        raise TransactionError(
                            "failed transaction residual state is unknown; "
                            "discard is required"
                        )
                    cleanup = self._persisted_writer_cleanup(
                        record,
                        payload,
                        snapshot_index,
                        base_affected=base_affected,
                        rollback_exclusions=set(rollback_exclusions),
                    )
                    self._verify_failed_residual_postimages(
                        record,
                        payload,
                        cleanup,
                        base_affected=base_affected,
                        rollback_exclusions=set(rollback_exclusions),
                    )
                self._restore_persisted_writer_guard(
                    record,
                    payload,
                    snapshot_index,
                    base_affected=base_affected,
                    rollback_exclusions=set(rollback_exclusions),
                    cleanup=cleanup,
                )
                self._restore_snapshot_index(
                    record,
                    {
                        relative: stored
                        for relative, stored in snapshot_index.items()
                        if relative not in rollback_exclusions
                    },
                )
                payload["status"] = "restored"
                payload["residual_postimages"] = {}
                payload["rollback_exclusions"] = {}
                self._write_metadata(record.workspace, payload)
            finally:
                if self.lock_path.exists() or self.lock_path.is_symlink():
                    self._unlink_owned_lock(
                        transaction_id, expected_identity=lock_identity
                    )

    def discard(self, transaction_id: str) -> None:
        self._verify_repository_identity()
        with self._action_lock():
            workspace = self._workspace_path(transaction_id)
            if not workspace.exists() and not workspace.is_symlink():
                return
            record = self.load(transaction_id)
            if record.status not in {"failed", "complete", "restored"}:
                raise TransactionError(
                    f"cannot discard {record.status} transaction; "
                    "active or promoting work is retained"
                )
            self._acquire_lock(transaction_id, record.started_at)
            lock_identity = self._require_owned_lock(transaction_id)
            removed = False
            try:
                self._remove_workspace(record.workspace)
                removed = True
            finally:
                if self.lock_path.exists() or self.lock_path.is_symlink():
                    self._unlink_owned_lock(
                        transaction_id, expected_identity=lock_identity
                    )
            if not removed:
                raise TransactionError(f"cannot discard transaction {transaction_id}")

    def _commit_record(
        self,
        record: TransactionRecord,
        *,
        completed_at: str | None,
        lock_identity: _FileIdentity,
        release_pre_snapshot_failure: bool,
        preflight_candidates: tuple[_Candidate, ...] | None = None,
        manifest: ShardedManifest | None = None,
    ) -> CommitResult:
        resolved_completed_at = completed_at or self._utc_now()
        self._validate_started_at(resolved_completed_at)
        snapshot_started = False
        payload = self._read_metadata_payload(record.workspace)
        self._verify_source_preimages(payload, record.source_ids)
        snapshot_index: dict[str, str | None] = {}
        operation_relative: str | None = None
        operation_affected: set[str] = set()
        operation_before: dict[str, str] | None = None
        operation_before_index: dict[str, str] = {}
        writer_before: dict[str, str] | None = None
        writer_before_index: dict[str, str | None] = {}
        writer_rollback: dict[str, str | None] = {}
        rollback_exclusion_paths: set[str] = set()
        rollback_exclusions: dict[str, str | None] | None = {}
        try:
            candidates, validation = self._validate_record(
                record, preflight_candidates
            )
            self._raise_validation_failure(validation)
            candidate_names = tuple(candidate.relative for candidate in candidates)
            affected = self._affected_preimage_paths(record, candidates)

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
                    "operation_guard": {},
                    "operation_path": None,
                    "operation_paths": [],
                    "postimages": {},
                    "residual_postimages": {},
                    "rollback_exclusions": {},
                    "removed": list(removed),
                    "snapshot_index": snapshot_index,
                    "status": "promoting",
                    "updated": list(updated),
                    "writer_guard": {},
                    "writer_prepared": False,
                }
            )
            self._write_metadata(record.workspace, payload)

            for candidate in candidates:
                try:
                    self._promote_candidate(
                        candidate,
                        self._vault_path(candidate.relative),
                        record.preimages.get(candidate.relative),
                    )
                except _MutationPreconditionError:
                    rollback_exclusion_paths.add(candidate.relative)
                    rollback_exclusions = self._capture_rollback_exclusion(
                        rollback_exclusions, candidate.relative
                    )
                    raise
            for relative in record.deletions:
                try:
                    self._delete_vault_target(relative, record.preimages.get(relative))
                except _MutationPreconditionError:
                    rollback_exclusion_paths.add(relative)
                    rollback_exclusions = self._capture_rollback_exclusion(
                        rollback_exclusions, relative
                    )
                    raise

            pages_by_source = self._scan_page_relationships(record.source_ids)
            manifest = manifest or ShardedManifest(self.config)
            for source_id in record.source_ids:
                shard_relative = (
                    manifest.entry_path(source_id)
                    .relative_to(self.config.vault)
                    .as_posix()
                )
                try:
                    manifest.upsert(
                        manifest.source_path(source_id),
                        pages=list(pages_by_source[source_id]),
                        compiled_at=resolved_completed_at,
                        expected_preimage=record.preimages.get(shard_relative),
                    )
                except ManifestPreconditionError as exc:
                    rollback_exclusion_paths.add(shard_relative)
                    rollback_exclusions = self._capture_rollback_exclusion(
                        rollback_exclusions, shard_relative
                    )
                    raise TransactionError(
                        "transaction target changed after transaction began: "
                        + shard_relative
                    ) from exc

            change = OperationChange(
                transaction_id=record.transaction_id,
                completed_at=resolved_completed_at,
                source_ids=record.source_ids,
                created=created,
                updated=updated,
                removed=removed,
            )
            writer_before, writer_before_index = self._snapshot_writer_guard(
                record, snapshot_index
            )
            operation_before, operation_before_index = self._snapshot_operation_tree(
                record
            )
            snapshot_index.update(writer_before_index)
            snapshot_index.update(operation_before_index)
            payload.update(
                {
                    "operation_guard": operation_before,
                    "snapshot_index": dict(sorted(snapshot_index.items())),
                    "writer_guard": writer_before,
                    "writer_prepared": True,
                }
            )
            self._write_metadata(record.workspace, payload)
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
                operation_affected.update(operation_rollback)
                raise TransactionError(
                    "operation writer must create exactly one new operation page "
                    "without modifying or removing existing operation pages"
                )
            writer_after = self._writer_guard_state(allow_unsafe=True)
            writer_rollback = self._writer_guard_diff(
                writer_before,
                writer_after,
                writer_before_index,
            )
            if writer_rollback:
                raise TransactionError(
                    "unauthorized operation writer side effect outside "
                    "journal/operations"
                )
            snapshot_index[operation_relative] = None
            operation_affected.add(operation_relative)

            postimage_paths = sorted(set(affected) | {operation_relative})
            postimages = {
                relative: self._current_vault_hash(relative)
                for relative in postimage_paths
            }
            complete_payload = dict(payload)
            complete_payload.update(
                {
                    "operation_guard": {},
                    "operation_path": operation_relative,
                    "operation_paths": sorted(operation_affected),
                    "postimages": postimages,
                    "residual_postimages": {},
                    "rollback_exclusions": {},
                    "snapshot_index": {
                        relative: snapshot_index[relative]
                        for relative in postimage_paths
                    },
                    "status": "complete",
                    "writer_guard": {},
                    "writer_prepared": False,
                }
            )
            self._write_metadata(record.workspace, complete_payload)
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
                    operation_diff = self._operation_tree_diff(
                        operation_before,
                        operation_after,
                        operation_before_index,
                    )
                    snapshot_index.update(operation_diff)
                    operation_affected.update(operation_diff)
                except (OSError, TransactionError) as rollback_exc:
                    rollback_errors.append(
                        f"operation cleanup discovery failed: {rollback_exc}"
                    )
            if writer_before is not None:
                try:
                    writer_after = self._writer_guard_state(allow_unsafe=True)
                    writer_rollback.update(
                        self._writer_guard_diff(
                            writer_before,
                            writer_after,
                            writer_before_index,
                        )
                    )
                except (OSError, TransactionError) as rollback_exc:
                    rollback_errors.append(
                        f"writer cleanup discovery failed: {rollback_exc}"
                    )
            try:
                self._restore_snapshot_index(record, writer_rollback)
            except (OSError, TransactionError) as rollback_exc:
                rollback_errors.append(f"writer restore failed: {rollback_exc}")
            try:
                self._restore_snapshot_index(
                    record,
                    {
                        relative: stored
                        for relative, stored in snapshot_index.items()
                        if relative not in rollback_exclusion_paths
                    },
                )
            except (OSError, TransactionError) as rollback_exc:
                rollback_errors.append(f"restore failed: {rollback_exc}")
            try:
                residual_postimages = self._failed_residual_postimages(
                    record,
                    writer_before,
                    operation_before,
                    base_affected=set(affected),
                    rollback_exclusions=rollback_exclusion_paths,
                )
            except (OSError, TransactionError) as rollback_exc:
                residual_postimages = None
                rollback_errors.append(f"residual discovery failed: {rollback_exc}")
            payload.update(
                {
                    "operation_path": operation_relative,
                    "operation_paths": sorted(operation_affected),
                    "postimages": {},
                    "residual_postimages": residual_postimages,
                    "rollback_exclusions": rollback_exclusions,
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

    @contextmanager
    def _action_lock(self, *, manifest: bool = False) -> Iterator[None]:
        self._ensure_directory(self.local_state)
        self._require_contained(
            self.action_lock_path,
            self.local_state,
            "transaction action lock",
            strict_child=True,
        )
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.action_lock_path, flags, 0o600)
        except OSError as exc:
            raise TransactionError("cannot open transaction action lock") from exc
        locked = False
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise TransactionError(
                    "transaction action lock must be a single-link ordinary file"
                )
            self._lock_action_descriptor(descriptor)
            locked = True
            if manifest:
                store = ShardedManifest(self.config)
                try:
                    with store.mutation_session():
                        self._action_manifest = store
                        yield
                except ManifestError as exc:
                    raise TransactionError(str(exc)) from exc
                finally:
                    self._action_manifest = None
            else:
                yield
        finally:
            try:
                if locked:
                    self._unlock_action_descriptor(descriptor)
            finally:
                os.close(descriptor)

    @staticmethod
    def _lock_action_descriptor(descriptor: int) -> None:
        if os.name == "nt":
            if _msvcrt is None:
                raise TransactionError(
                    "transaction action locking is unavailable on this platform"
                )
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                _msvcrt.locking(descriptor, _msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise TransactionError(
                    "another transaction action is already in progress"
                ) from exc
            return
        if _fcntl is None:
            raise TransactionError(
                "transaction action locking is unavailable on this platform"
            )
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise TransactionError(
                    "another transaction action is already in progress"
                ) from exc
            raise TransactionError("cannot lock transaction action file") from exc

    @staticmethod
    def _unlock_action_descriptor(descriptor: int) -> None:
        if os.name == "nt":
            if _msvcrt is None:  # pragma: no cover - guarded by lock acquisition
                return
            os.lseek(descriptor, 0, os.SEEK_SET)
            _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)
            return
        if _fcntl is not None:
            _fcntl.flock(descriptor, _fcntl.LOCK_UN)

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
    ) -> tuple[_Candidate, ...]:
        candidates = self._read_candidate_files(record)
        for candidate in candidates:
            validate_candidate_path(record.candidate_vault, candidate.relative)
            _validate_candidate_bytes(candidate.data, record.source_ids)
        return candidates

    def _read_candidate_files(
        self, record: TransactionRecord
    ) -> tuple[_Candidate, ...]:
        candidates: list[_Candidate] = []
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
                data = self._read_single_link_bytes(candidate, "candidate page")
                candidates.append(
                    _Candidate(relative=relative, path=candidate, data=data)
                )
        return tuple(sorted(candidates, key=lambda item: item.relative))

    def _candidate_path_names(self, candidate_vault: Path) -> tuple[str, ...]:
        names: list[str] = []
        for directory, dirnames, filenames in os.walk(
            candidate_vault, topdown=True, followlinks=False
        ):
            current = Path(directory)
            self._require_ordinary_directory(current, "candidate directory")
            for name in sorted(dirnames):
                self._require_ordinary_directory(current / name, "candidate directory")
            dirnames[:] = sorted(dirnames)
            for name in sorted(filenames):
                candidate = current / name
                relative = candidate.relative_to(candidate_vault).as_posix()
                self._require_ordinary_file(candidate, "candidate page")
                names.append(relative)
        return tuple(sorted(names))

    def _validate_record(
        self,
        record: TransactionRecord,
        candidates: tuple[_Candidate, ...] | None = None,
    ) -> tuple[tuple[_Candidate, ...], TransactionValidationReport]:
        resolved_candidates = (
            self._read_candidate_files(record) if candidates is None else candidates
        )
        valid_candidates = tuple(
            item
            for item in resolved_candidates
            if self._candidate_path_issue(record, item) is None
        )
        self._verify_preimages(
            record, self._affected_preimage_paths(record, valid_candidates)
        )
        report = self._content_validation_report(record, resolved_candidates)
        return resolved_candidates, report

    def _content_validation_report(
        self,
        record: TransactionRecord,
        candidates: tuple[_Candidate, ...],
        *,
        live_overrides: dict[str, bytes | None] | None = None,
    ) -> TransactionValidationReport:
        candidate_names = tuple(item.relative for item in candidates)
        overlap = sorted(set(candidate_names) & set(record.deletions))
        if overlap:
            raise TransactionError(
                "candidate and deletion target the same page: " + ", ".join(overlap)
            )
        valid_candidates = tuple(
            item
            for item in candidates
            if self._candidate_path_issue(record, item) is None
        )
        valid_candidate_names = tuple(item.relative for item in valid_candidates)
        self._verify_existing_page_sources(
            record,
            valid_candidate_names,
            live_overrides=live_overrides,
        )
        pages, candidate_issues = self._prospective_pages(
            record,
            candidates,
            live_overrides=live_overrides,
        )
        issues = tuple(
            sorted(
                (*candidate_issues, *validate_prospective_pages(pages, record.source_ids)),
                key=lambda item: (item.path, item.code, item.target or ""),
            )
        )
        report = TransactionValidationReport(
            transaction_id=record.transaction_id,
            status="fail" if issues else "pass",
            candidate_pages=candidate_names,
            deletions=record.deletions,
            issues=issues,
        )
        return report

    @staticmethod
    def _raise_validation_failure(report: TransactionValidationReport) -> None:
        if not report.issues:
            return
        summary = "; ".join(
            f"{issue.path}: {issue.code}: {issue.message}"
            for issue in report.issues
        )
        raise TransactionError(f"transaction validation failed: {summary}")

    @staticmethod
    def _candidate_path_issue(
        record: TransactionRecord, candidate: _Candidate
    ) -> ValidationIssue | None:
        try:
            validate_candidate_path(record.candidate_vault, candidate.relative)
        except TransactionError as exc:
            return ValidationIssue(
                code="candidate-path-invalid",
                path=candidate.relative,
                message=str(exc),
            )
        return None

    def _prospective_pages(
        self,
        record: TransactionRecord,
        candidates: tuple[_Candidate, ...],
        *,
        live_overrides: dict[str, bytes | None] | None = None,
    ) -> tuple[tuple[ProspectivePage, ...], tuple[ValidationIssue, ...]]:
        pages: list[ProspectivePage] = []
        issues: list[ValidationIssue] = []
        candidate_names = {item.relative for item in candidates}
        omitted_live_paths = candidate_names | set(record.deletions)

        for candidate in candidates:
            path_issue = self._candidate_path_issue(record, candidate)
            if path_issue is not None:
                issues.append(path_issue)
                continue
            try:
                text = candidate.data.decode("utf-8")
            except UnicodeDecodeError:
                issues.append(
                    ValidationIssue(
                        code="candidate-utf8-invalid",
                        path=candidate.relative,
                        message="candidate page must be UTF-8 text",
                    )
                )
                continue
            pages.append(
                ProspectivePage(path=candidate.relative, text=text, candidate=True)
            )

        live_pages: dict[str, bytes] = {}
        self._require_ordinary_directory(self.config.vault, "portable vault")
        for relative in sorted(_TRACKED_ROOT_GRAPH_PAGES):
            page = self.config.vault / relative
            if not page.exists() and not page.is_symlink():
                continue
            live_pages[relative] = self._read_single_link_bytes(
                page, "knowledge page"
            )
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
                kept_directories: list[str] = []
                for name in sorted(dirnames):
                    child = current / name
                    if (
                        category == "journal"
                        and current == root
                        and name == "operations"
                    ):
                        continue
                    self._require_ordinary_directory(child, "knowledge directory")
                    kept_directories.append(name)
                dirnames[:] = kept_directories
                for name in sorted(filenames):
                    page = current / name
                    self._require_ordinary_file(page, "knowledge page")
                    if not name.endswith(".md"):
                        continue
                    relative = page.relative_to(self.config.vault).as_posix()
                    live_pages[relative] = self._read_single_link_bytes(
                        page, "knowledge page"
                    )

        for relative, data in (live_overrides or {}).items():
            if not self._is_graph_page_path(relative):
                continue
            if data is None:
                live_pages.pop(relative, None)
            else:
                live_pages[relative] = data

        for relative, data in sorted(live_pages.items()):
            if relative in omitted_live_paths:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TransactionError(
                    f"knowledge page must be UTF-8 text: {relative}"
                ) from exc
            pages.append(
                ProspectivePage(path=relative, text=text, candidate=False)
            )

        return tuple(sorted(pages, key=lambda item: item.path)), tuple(issues)

    @staticmethod
    def _is_graph_page_path(relative: str) -> bool:
        path = PurePosixPath(relative)
        if path.suffix != ".md":
            return False
        if len(path.parts) == 1:
            return relative in _TRACKED_ROOT_GRAPH_PAGES
        return (
            path.parts[0] in _KNOWLEDGE_DIRECTORIES
            and path.parts[:2] != ("journal", "operations")
        )

    def _affected_preimage_paths(
        self,
        record: TransactionRecord,
        candidates: tuple[_Candidate, ...],
    ) -> tuple[str, ...]:
        manifest = ShardedManifest(self.config)
        affected = {candidate.relative for candidate in candidates}
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

    def _verify_existing_page_sources(
        self,
        record: TransactionRecord,
        candidate_names: tuple[str, ...],
        *,
        live_overrides: dict[str, bytes | None] | None = None,
    ) -> None:
        manifest = ShardedManifest(self.config)
        selected = set(record.source_ids)
        affected_pages = sorted(set(candidate_names) | set(record.deletions))
        for relative in affected_pages:
            if record.preimages.get(relative) is None:
                continue
            try:
                if live_overrides is not None and relative in live_overrides:
                    data = live_overrides[relative]
                    if data is None:
                        raise TransactionError(
                            "existing page is absent from the recovered vault: "
                            + relative
                        )
                else:
                    page = self._vault_path(relative)
                    data = self._read_single_link_bytes(page, "existing page")
                text = data.decode("utf-8")
                frontmatter = parse_frontmatter(text)
            except TransactionError:
                raise
            except (UnicodeDecodeError, FrontmatterError) as exc:
                raise TransactionError(
                    f"invalid existing page frontmatter: {relative}: {exc}"
                ) from exc
            page_sources = frontmatter.lists.get("sources")
            if not page_sources:
                raise TransactionError(
                    f"existing page sources must be a non-empty list: {relative}"
                )
            for source_id in page_sources:
                try:
                    manifest.source_path(source_id)
                except ManifestError as exc:
                    raise TransactionError(
                        f"invalid existing page source {source_id!r}: {relative}"
                    ) from exc
            missing = sorted(set(page_sources) - selected)
            if missing:
                raise TransactionError(
                    f"existing page {relative} requires transaction sources: "
                    + ", ".join(missing)
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
            expected = record.preimages.get(relative)
            if not target.exists() and not target.is_symlink():
                if expected is not None:
                    raise TransactionError(
                        "transaction target changed after transaction began: "
                        + relative
                    )
                index[relative] = None
                continue
            data = self._read_single_link_bytes(target, "transaction target")
            if self._hash_bytes(data) != expected:
                raise TransactionError(
                    "transaction target changed after transaction began: " + relative
                )
            snapshot_relative = PurePosixPath("originals") / PurePosixPath(relative)
            snapshot = record.workspace / "snapshots" / snapshot_relative
            self._ensure_contained_directory(snapshot.parent, originals)
            self._write_atomic_bytes(snapshot, data)
            index[relative] = snapshot_relative.as_posix()
        return dict(sorted(index.items()))

    def _clear_snapshot_state(self, record: TransactionRecord) -> None:
        payload = self._read_metadata_payload(record.workspace)
        payload.update(
            {
                "completed_at": None,
                "created": [],
                "operation_guard": {},
                "operation_path": None,
                "operation_paths": [],
                "postimages": {},
                "residual_postimages": {},
                "rollback_exclusions": {},
                "removed": [],
                "snapshot_index": {},
                "status": "failed",
                "updated": [],
                "writer_guard": {},
                "writer_prepared": False,
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
        try:
            self._remove_checked_tree(originals)
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

    def _restore_persisted_writer_guard(
        self,
        record: TransactionRecord,
        payload: dict[str, object],
        snapshot_index: dict[str, str | None],
        *,
        base_affected: set[str],
        rollback_exclusions: set[str] | None = None,
        cleanup: dict[str, str | None] | None = None,
    ) -> None:
        resolved_cleanup = cleanup
        if resolved_cleanup is None:
            resolved_cleanup = self._persisted_writer_cleanup(
                record,
                payload,
                snapshot_index,
                base_affected=base_affected,
                rollback_exclusions=rollback_exclusions or set(),
            )
        self._restore_snapshot_index(record, resolved_cleanup)

    def _virtual_recovered_files(
        self,
        record: TransactionRecord,
        cleanup: dict[str, str | None],
    ) -> dict[str, bytes | None]:
        snapshots = record.workspace / "snapshots"
        recovered: dict[str, bytes | None] = {}
        for relative, stored in sorted(cleanup.items()):
            if stored is None:
                recovered[relative] = None
                continue
            self._validate_relative_path(stored, "snapshot path")
            snapshot = snapshots.joinpath(*PurePosixPath(stored).parts)
            self._require_contained(
                snapshot, snapshots, "snapshot path", strict_child=True
            )
            recovered[relative] = self._read_single_link_bytes(
                snapshot, "transaction snapshot"
            )
        return recovered

    def _persisted_writer_cleanup(
        self,
        record: TransactionRecord,
        payload: dict[str, object],
        snapshot_index: dict[str, str | None],
        *,
        base_affected: set[str],
        rollback_exclusions: set[str],
    ) -> dict[str, str | None]:
        cleanup: dict[str, str | None] = {}
        if payload["writer_prepared"]:
            excluded = base_affected | rollback_exclusions
            writer_before = {
                relative: content_hash
                for relative, content_hash in self._load_writer_guard(
                    payload["writer_guard"]
                ).items()
                if relative not in excluded
            }
            operation_before = {
                relative: content_hash
                for relative, content_hash in self._load_operation_guard(
                    payload["operation_guard"]
                ).items()
                if relative not in rollback_exclusions
            }
            writer_after = {
                relative: content_hash
                for relative, content_hash in self._writer_guard_state(
                    allow_unsafe=True
                ).items()
                if relative not in excluded
            }
            operation_after = {
                relative: content_hash
                for relative, content_hash in self._operation_tree_state(
                    allow_unsafe=True
                ).items()
                if relative not in rollback_exclusions
            }
            cleanup.update(
                self._writer_guard_diff(
                    writer_before,
                    writer_after,
                    {relative: snapshot_index[relative] for relative in writer_before},
                )
            )
            cleanup.update(
                self._operation_tree_diff(
                    operation_before,
                    operation_after,
                    {
                        relative: snapshot_index[relative]
                        for relative in operation_before
                    },
                )
            )
        for relative in sorted(base_affected - rollback_exclusions):
            if self._current_vault_hash(relative) != record.preimages.get(relative):
                cleanup[relative] = snapshot_index[relative]
        return dict(sorted(cleanup.items()))

    def _failed_residual_postimages(
        self,
        record: TransactionRecord,
        writer_before: dict[str, str] | None,
        operation_before: dict[str, str] | None,
        *,
        base_affected: set[str],
        rollback_exclusions: set[str],
    ) -> dict[str, str | None]:
        residual: dict[str, str | None] = {}
        if writer_before is not None:
            writer_after = self._writer_guard_state(allow_unsafe=True)
            changed = {
                relative
                for relative, content_hash in writer_before.items()
                if writer_after.get(relative) != content_hash
            }
            changed.update(set(writer_after) - set(writer_before))
            for relative in changed - base_affected:
                residual[relative] = self._current_vault_hash(relative)
        if operation_before is not None:
            operation_after = self._operation_tree_state(allow_unsafe=True)
            changed = {
                relative
                for relative, content_hash in operation_before.items()
                if operation_after.get(relative) != content_hash
            }
            changed.update(set(operation_after) - set(operation_before))
            for relative in changed:
                residual[relative] = self._current_vault_hash(relative)
        for relative in sorted(base_affected - rollback_exclusions):
            current = self._current_vault_hash(relative)
            if current != record.preimages.get(relative):
                residual[relative] = current
        return dict(sorted(residual.items()))

    def _verify_failed_residual_postimages(
        self,
        record: TransactionRecord,
        payload: dict[str, object],
        cleanup: dict[str, str | None],
        *,
        base_affected: set[str],
        rollback_exclusions: set[str],
    ) -> None:
        residuals = self._load_residual_postimages(payload["residual_postimages"])
        if residuals is None:
            raise TransactionError(
                "failed transaction residual state is unknown; discard is required"
            )
        actual: dict[str, str | None] = {}
        for relative in cleanup:
            try:
                actual[relative] = self._current_vault_hash(relative)
            except TransactionError as exc:
                raise TransactionError(
                    "failed transaction residual changed after lock release: "
                    + relative
                ) from exc
        unrecorded = set(actual) - set(residuals)
        if unrecorded:
            raise TransactionError(
                "failed transaction residual set or content changed after lock release"
            )
        for relative, current in actual.items():
            if current != residuals[relative]:
                raise TransactionError(
                    "failed transaction residual set or content changed after lock release"
                )
        baselines = {
            relative: record.preimages.get(relative)
            for relative in base_affected - rollback_exclusions
        }
        baselines.update(
            {
                relative: content_hash
                for relative, content_hash in self._load_writer_guard(
                    payload["writer_guard"]
                ).items()
                if relative not in base_affected | rollback_exclusions
            }
        )
        baselines.update(
            {
                relative: content_hash
                for relative, content_hash in self._load_operation_guard(
                    payload["operation_guard"]
                ).items()
                if relative not in rollback_exclusions
            }
        )
        for relative in set(residuals) - set(actual):
            try:
                current = self._current_vault_hash(relative)
            except TransactionError as exc:
                raise TransactionError(
                    "failed transaction residual changed after lock release: "
                    + relative
                ) from exc
            if current != baselines.get(relative):
                raise TransactionError(
                    "failed transaction residual set or content changed after lock release"
                )

    def _verify_rollback_exclusions(
        self, payload: dict[str, object]
    ) -> dict[str, str | None]:
        exclusions = self._load_rollback_exclusions(payload["rollback_exclusions"])
        if exclusions is None:
            raise TransactionError(
                "failed transaction rollback exclusion state is unknown; "
                "discard is required"
            )
        for relative, expected in exclusions.items():
            try:
                current = self._current_vault_hash(relative)
            except TransactionError as exc:
                raise TransactionError(
                    "failed transaction rollback exclusion changed after lock release: "
                    + relative
                ) from exc
            if current != expected:
                raise TransactionError(
                    "failed transaction rollback exclusion changed after lock release: "
                    + relative
                )
        return exclusions

    def _promote_candidate(
        self,
        candidate: _Candidate,
        target: Path,
        expected_preimage: str | None,
    ) -> None:
        self._replace_vault_bytes(
            target,
            candidate.data,
            before_replace=lambda: self._require_mutation_preimage(
                candidate.relative, expected_preimage
            ),
        )

    def _replace_vault_bytes(
        self,
        target: Path,
        data: bytes,
        *,
        before_replace: Callable[[], None] | None = None,
    ) -> None:
        self._require_contained(
            target, self.config.vault, "vault target", strict_child=True
        )
        self._ensure_contained_directory(target.parent, self.config.vault)
        self._write_atomic_bytes(target, data, before_replace=before_replace)

    def _delete_vault_target(
        self, relative: str, expected_preimage: str | None
    ) -> None:
        target = self._vault_path(relative)
        self._require_mutation_preimage(relative, expected_preimage)
        if expected_preimage is None:
            return
        self._require_ordinary_file(target, "transaction deletion target")
        try:
            target.unlink()
            self._fsync_directory(target.parent)
        except OSError as exc:
            raise TransactionError(
                f"cannot delete transaction target: {relative}"
            ) from exc

    def _require_mutation_preimage(
        self, relative: str, expected_preimage: str | None
    ) -> None:
        try:
            current = self._current_vault_hash(relative)
        except TransactionError as exc:
            raise _MutationPreconditionError(
                f"transaction target changed after transaction began: {relative}"
            ) from exc
        if current != expected_preimage:
            raise _MutationPreconditionError(
                f"transaction target changed after transaction began: {relative}"
            )

    def _capture_rollback_exclusion(
        self,
        exclusions: dict[str, str | None] | None,
        relative: str,
    ) -> dict[str, str | None] | None:
        if exclusions is None:
            return None
        try:
            exclusions[relative] = self._current_vault_hash(relative)
        except TransactionError:
            return None
        return dict(sorted(exclusions.items()))

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
                    fields = frontmatter.fields
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

    def _snapshot_writer_guard(
        self,
        record: TransactionRecord,
        reusable_index: dict[str, str | None],
    ) -> tuple[dict[str, str], dict[str, str | None]]:
        state = self._writer_guard_state()
        index: dict[str, str | None] = {}
        originals = record.workspace / "snapshots" / "originals"
        for relative in sorted(state):
            if relative in reusable_index:
                index[relative] = reusable_index[relative]
                continue
            snapshot_relative = (
                PurePosixPath("originals") / ".writer-guard" / PurePosixPath(relative)
            )
            snapshot = record.workspace / "snapshots" / snapshot_relative
            self._ensure_contained_directory(snapshot.parent, originals)
            data = self._read_single_link_bytes(
                self._vault_path(relative), "writer guard target"
            )
            self._write_atomic_bytes(snapshot, data)
            index[relative] = snapshot_relative.as_posix()
        return state, dict(sorted(index.items()))

    @staticmethod
    def _writer_guard_diff(
        before: dict[str, str],
        after: dict[str, str | None],
        before_index: dict[str, str | None],
    ) -> dict[str, str | None]:
        changed = {
            relative: before_index[relative]
            for relative, content_hash in before.items()
            if after.get(relative) != content_hash
        }
        changed.update({relative: None for relative in set(after) - set(before)})
        return dict(sorted(changed.items()))

    def _writer_guard_state(
        self, *, allow_unsafe: bool = False
    ) -> dict[str, str | None]:
        self._require_ordinary_directory(self.config.vault, "portable vault")
        excluded_local: Path | None = None
        try:
            excluded_local = self.local_state.relative_to(self.config.vault)
        except ValueError:
            pass

        result: dict[str, str | None] = {}
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
                if relative.parts[:2] == ("journal", "operations"):
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
                if relative.parts[:2] == ("journal", "operations"):
                    continue
                if excluded_local is not None and (
                    relative == excluded_local or excluded_local in relative.parents
                ):
                    continue
                target = current / name
                try:
                    result[relative_key] = self._hash_single_link_file(
                        target, "writer guard target"
                    )
                except TransactionError:
                    if not allow_unsafe:
                        raise
                    result[relative_key] = None
        return dict(sorted(result.items()))

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

    def _write_atomic_bytes(
        self,
        target: Path,
        data: bytes,
        *,
        before_replace: Callable[[], None] | None = None,
    ) -> None:
        self._verify_repository_identity()
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
            if before_replace is not None:
                before_replace()
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
        directory_fd = (
            TransactionManager._open_directory(path.parent, label)
            if _SUPPORTS_DIR_FD
            else None
        )
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            if directory_fd is None:
                descriptor = os.open(path, flags)
            else:
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
            if directory_fd is None:
                current = path.lstat()
            else:
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
            if directory_fd is not None:
                os.close(directory_fd)

    def _read_metadata_payload(self, workspace: Path) -> dict[str, object]:
        payload = self._read_json_file(
            workspace / "metadata.json", "transaction metadata"
        )
        if not isinstance(payload, dict) or set(payload) != _METADATA_FIELDS:
            raise TransactionError("transaction metadata has invalid fields")
        self._validate_recovery_metadata(payload, workspace)
        return payload

    def _validate_recovery_metadata(
        self, payload: dict[str, object], workspace: Path
    ) -> None:
        completed_at = payload.get("completed_at")
        if completed_at is not None:
            try:
                self._validate_started_at(completed_at)
            except TransactionError as exc:
                raise TransactionError("transaction completed_at is invalid") from exc
        self._load_image_map(payload.get("preimages"), "transaction preimages")
        self._load_image_map(payload.get("postimages"), "transaction postimages")
        self._load_residual_postimages(payload.get("residual_postimages"))
        self._load_rollback_exclusions(payload.get("rollback_exclusions"))
        self._load_snapshot_index(payload.get("snapshot_index"), workspace=workspace)
        self._load_operation_paths(payload.get("operation_paths"))
        self._load_operation_guard(payload.get("operation_guard"))
        self._load_writer_guard(payload.get("writer_guard"))
        if not isinstance(payload.get("writer_prepared"), bool):
            raise TransactionError("transaction writer_prepared must be a boolean")
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

    def _validate_metadata_semantics(
        self,
        *,
        workspace: Path,
        payload: dict[str, object],
        status: str,
        source_ids: tuple[str, ...],
        preimages: dict[str, str | None],
        deletions: tuple[str, ...],
        candidate_names: tuple[str, ...],
    ) -> None:
        manifest = ShardedManifest(self.config)
        shard_paths = {
            manifest.entry_path(source_id).relative_to(self.config.vault).as_posix()
            for source_id in source_ids
        }
        base_affected = set(candidate_names) | set(deletions) | shard_paths
        operation_guard = self._load_operation_guard(payload["operation_guard"])
        writer_guard = self._load_writer_guard(payload["writer_guard"])
        writer_prepared = payload["writer_prepared"]
        operation_paths = set(self._load_operation_paths(payload["operation_paths"]))
        operation_path = payload["operation_path"]
        if operation_path is not None and operation_path not in operation_paths:
            raise TransactionError(
                "transaction operation_path must belong to operation_paths"
            )
        allowed = base_affected | operation_paths
        guard_paths = set(writer_guard) | set(operation_guard)
        snapshot_allowed = allowed | guard_paths
        snapshots = self._load_snapshot_index(
            payload["snapshot_index"], workspace=workspace
        )
        postimages = self._load_image_map(
            payload["postimages"], "transaction postimages"
        )
        residual_postimages = self._load_residual_postimages(
            payload["residual_postimages"]
        )
        rollback_exclusions = self._load_rollback_exclusions(
            payload["rollback_exclusions"]
        )
        if (
            rollback_exclusions is not None
            and not set(rollback_exclusions) <= base_affected
        ):
            raise TransactionError(
                "transaction rollback exclusions are outside the affected set"
            )
        if (
            residual_postimages is not None
            and rollback_exclusions is not None
            and set(residual_postimages) & set(rollback_exclusions)
        ):
            raise TransactionError(
                "transaction residual postimages overlap rollback exclusions"
            )
        if (
            not writer_prepared
            and residual_postimages is not None
            and not set(residual_postimages) <= base_affected
        ):
            raise TransactionError(
                "unprepared transaction residuals must belong to affected paths"
            )
        expected_snapshot_hashes = {
            relative: preimages.get(relative) for relative in base_affected
        }
        for relative, content_hash in writer_guard.items():
            expected_snapshot_hashes.setdefault(relative, content_hash)
        for relative, content_hash in operation_guard.items():
            expected_snapshot_hashes.setdefault(relative, content_hash)
        for relative in operation_paths:
            expected_snapshot_hashes.setdefault(relative, None)
        if not set(snapshots) <= snapshot_allowed:
            raise TransactionError(
                "transaction snapshot target is outside the affected set"
            )
        if not set(postimages) <= allowed:
            raise TransactionError(
                "transaction postimages contain paths outside the affected set"
            )
        if writer_prepared:
            if set(snapshots) != snapshot_allowed:
                raise TransactionError(
                    "transaction snapshots do not exactly match persisted writer guard"
                )
            missing_backings = {
                relative
                for relative in guard_paths - base_affected
                if snapshots[relative] is None
            }
            if missing_backings:
                raise TransactionError(
                    "persisted writer guard path is missing its snapshot backing"
                )
        elif writer_guard or operation_guard:
            raise TransactionError(
                "unprepared transaction cannot contain persisted writer guards"
            )
        self._validate_snapshot_backing_hashes(
            workspace,
            snapshots,
            expected_snapshot_hashes,
        )

        created = set(payload["created"])
        updated = set(payload["updated"])
        removed = set(payload["removed"])
        if (created | updated) - set(candidate_names) or removed - set(deletions):
            raise TransactionError(
                "transaction change fields contain paths outside affected pages"
            )

        completed_at = payload["completed_at"]
        if status == "active":
            if any(
                (
                    completed_at is not None,
                    bool(created or updated or removed),
                    operation_path is not None,
                    bool(operation_paths),
                    bool(snapshots),
                    bool(postimages),
                    residual_postimages != {},
                    rollback_exclusions != {},
                    writer_prepared,
                )
            ):
                raise TransactionError("active transaction has invalid recovery fields")
            return
        if status == "promoting":
            if (
                completed_at is None
                or operation_path is not None
                or operation_paths
                or set(snapshots) != snapshot_allowed
                or postimages
                or residual_postimages != {}
                or rollback_exclusions != {}
            ):
                raise TransactionError(
                    "promoting transaction has invalid recovery fields"
                )
            return
        if status == "complete":
            if (
                completed_at is None
                or operation_path is None
                or operation_paths != {operation_path}
                or writer_prepared
                or residual_postimages != {}
                or rollback_exclusions != {}
                or set(snapshots) != allowed
                or set(postimages) != allowed
            ):
                raise TransactionError(
                    "complete transaction postimages must exactly match affected paths"
                )
            return
        if status == "failed":
            if completed_at is None:
                if any(
                    (
                        bool(created or updated or removed),
                        operation_path is not None,
                        bool(operation_paths),
                        bool(snapshots),
                        bool(postimages),
                        residual_postimages != {},
                        rollback_exclusions != {},
                        writer_prepared,
                    )
                ):
                    raise TransactionError(
                        "failed transaction reset fields are invalid"
                    )
                return
            if postimages or set(snapshots) != snapshot_allowed:
                raise TransactionError("failed transaction recovery fields are invalid")
            return
        if status == "restored" and (
            set(snapshots) != snapshot_allowed
            or (postimages and set(postimages) != allowed)
            or residual_postimages != {}
            or rollback_exclusions != {}
        ):
            raise TransactionError("restored transaction recovery fields are invalid")

    def _load_operation_paths(self, raw: object) -> tuple[str, ...]:
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise TransactionError("transaction operation_paths must be a string list")
        values = tuple(raw)
        if values != tuple(sorted(set(values))):
            raise TransactionError(
                "transaction operation_paths must be unique and sorted"
            )
        for relative in values:
            self._validate_relative_path(relative, "transaction operation path")
            path = PurePosixPath(relative)
            if path.suffix != ".md" or path.parts[:2] != (
                "journal",
                "operations",
            ):
                raise TransactionError(
                    "transaction operation path must be below journal/operations"
                )
        return values

    def _load_writer_guard(self, raw: object) -> dict[str, str]:
        guard = self._load_guard_hashes(raw, "transaction writer guard")
        for relative in guard:
            self._validate_writer_guard_path(relative)
        return guard

    def _load_operation_guard(self, raw: object) -> dict[str, str]:
        guard = self._load_guard_hashes(raw, "transaction operation guard")
        for relative in guard:
            self._validate_operation_guard_path(relative)
        return guard

    def _load_residual_postimages(self, raw: object) -> dict[str, str | None] | None:
        if raw is None:
            return None
        residuals = self._load_image_map(raw, "transaction residual postimages")
        for relative in residuals:
            if PurePosixPath(relative).parts[:2] == ("journal", "operations"):
                self._validate_operation_guard_path(relative)
            else:
                self._validate_writer_guard_path(relative)
        return residuals

    def _load_rollback_exclusions(self, raw: object) -> dict[str, str | None] | None:
        if raw is None:
            return None
        return self._load_image_map(raw, "transaction rollback exclusions")

    def _load_guard_hashes(self, raw: object, label: str) -> dict[str, str]:
        loaded = self._load_image_map(raw, label)
        if any(content_hash is None for content_hash in loaded.values()):
            raise TransactionError(f"{label} hashes cannot be null")
        return {
            relative: content_hash
            for relative, content_hash in loaded.items()
            if content_hash is not None
        }

    def _validate_writer_guard_path(self, relative: str) -> None:
        self._validate_relative_path(relative, "transaction writer guard path")
        path = PurePosixPath(relative)
        if (
            relative == "hot.md"
            or path.parts[0] == ".obsidian"
            or path.parts[:2] == ("journal", "operations")
        ):
            raise TransactionError(
                "transaction writer guard path is outside the authoritative vault"
            )
        try:
            excluded_local = self.local_state.relative_to(self.config.vault)
        except ValueError:
            excluded_local = None
        if excluded_local is not None and (
            path == excluded_local or excluded_local in path.parents
        ):
            raise TransactionError(
                "transaction writer guard path is outside the authoritative vault"
            )

    def _validate_operation_guard_path(self, relative: str) -> None:
        self._validate_relative_path(relative, "transaction operation guard path")
        path = PurePosixPath(relative)
        if len(path.parts) < 3 or path.parts[:2] != ("journal", "operations"):
            raise TransactionError(
                "transaction operation guard path must be below journal/operations"
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

    def _load_snapshot_index(
        self, raw: object, *, workspace: Path | None = None
    ) -> dict[str, str | None]:
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
                if workspace is not None:
                    snapshot_path = workspace / "snapshots" / PurePosixPath(snapshot)
                    self._require_contained(
                        snapshot_path,
                        workspace / "snapshots",
                        "transaction snapshot path",
                        strict_child=True,
                    )
                    self._require_ordinary_file(
                        snapshot_path, "transaction snapshot backing file"
                    )
            result[relative] = snapshot
        if list(result) != sorted(result):
            raise TransactionError("transaction snapshot targets must be sorted")
        return result

    def _validate_snapshot_backing_hashes(
        self,
        workspace: Path,
        snapshots: dict[str, str | None],
        expected_hashes: dict[str, str | None],
    ) -> None:
        snapshot_root = workspace / "snapshots"
        for relative, stored in snapshots.items():
            expected = expected_hashes[relative]
            if stored is None:
                if expected is not None:
                    raise TransactionError(
                        f"transaction snapshot backing hash is missing for {relative}"
                    )
                continue
            if expected is None:
                raise TransactionError(
                    f"transaction snapshot backing is unexpected for {relative}"
                )
            snapshot = snapshot_root.joinpath(*PurePosixPath(stored).parts)
            data = self._read_single_link_bytes(
                snapshot, "transaction snapshot backing"
            )
            if self._hash_bytes(data) != expected:
                raise TransactionError(
                    f"transaction snapshot backing hash mismatch for {relative}"
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

    def _load_source_preimages(
        self, raw: object, source_ids: tuple[str, ...]
    ) -> dict[str, str]:
        if not isinstance(raw, dict) or set(raw) != set(source_ids):
            raise TransactionError("transaction source_preimages must match source_ids")
        result: dict[str, str] = {}
        for source_id, content_hash in raw.items():
            if not isinstance(source_id, str) or not isinstance(content_hash, str) or _HASH_RE.fullmatch(content_hash) is None:
                raise TransactionError("transaction source_preimages are invalid")
            result[source_id] = content_hash
        return dict(sorted(result.items()))

    def _verify_source_preimages(
        self, payload: dict[str, object], source_ids: tuple[str, ...]
    ) -> None:
        expected = self._load_source_preimages(payload.get("source_preimages"), source_ids)
        manifest = ShardedManifest(self.config)
        for source_id in source_ids:
            try:
                current = self._hash_single_link_file(
                    manifest.source_path(source_id), "transaction source"
                )
            except (ManifestError, TransactionError) as exc:
                raise TransactionError(
                    f"transaction source changed after begin; restart required: {source_id}"
                ) from exc
            if current != expected[source_id]:
                raise TransactionError(
                    f"transaction source changed after begin; restart required: {source_id}"
                )

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

    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

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
        if not _SUPPORTS_DIR_FD:
            self._unlink_lock_path_fallback(identity)
            return
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
        if not _SUPPORTS_DIR_FD:
            try:
                self._unlink_lock_path_fallback(identity)
            except TransactionError:
                pass
            return
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

    def _unlink_lock_path_fallback(self, identity: _FileIdentity) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(self.lock_path, flags)
            opened = os.fstat(descriptor)
            current = self.lock_path.lstat()
            if (
                (opened.st_dev, opened.st_ino) != identity
                or (current.st_dev, current.st_ino) != identity
                or not stat.S_ISREG(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or current.st_nlink != 1
            ):
                raise TransactionError("transaction lock inode changed before unlink")
        except TransactionError:
            raise
        except OSError as exc:
            raise TransactionError("cannot remove owned transaction lock") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            current = self.lock_path.lstat()
            if (current.st_dev, current.st_ino) != identity:
                raise TransactionError("transaction lock inode changed before unlink")
            self.lock_path.unlink()
            self._fsync_directory(self.local_state)
        except TransactionError:
            raise
        except OSError as exc:
            raise TransactionError("cannot remove owned transaction lock") from exc

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
        directory_fd = (
            TransactionManager._open_directory(path.parent, label)
            if _SUPPORTS_DIR_FD
            else None
        )
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                if directory_fd is None:
                    descriptor = os.open(path, flags)
                else:
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
            if directory_fd is None:
                current = path.lstat()
            else:
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
            if directory_fd is not None:
                os.close(directory_fd)

    def _ensure_directory(self, path: Path) -> None:
        self._verify_repository_identity()
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

    def _verify_repository_identity(self) -> None:
        descriptor = self._open_directory(
            self.config.root, "portable repository root"
        )
        try:
            if (
                stable_directory_identity(os.fstat(descriptor))
                != self.config.root_identity
            ):
                raise TransactionError(
                    "configured repository root changed since configuration was read"
                )
        finally:
            os.close(descriptor)

    @staticmethod
    def _require_ordinary_directory(path: Path, label: str) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise TransactionError(f"{label} is missing or unreadable: {path}") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or TransactionManager._is_reparse_point(metadata)
        ):
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
        if not _SUPPORTS_DIRECTORY_FSYNC:
            TransactionManager._require_ordinary_directory(path, "transaction")
            return
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
            or TransactionManager._is_reparse_point(metadata)
            or metadata.st_nlink != 1
        ):
            raise TransactionError(
                f"{label} must be a single-link ordinary file, not a symlink: {path}"
            )

    @staticmethod
    def _is_reparse_point(metadata: object) -> bool:
        return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)

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
        self._require_managed_tree(workspace)
        try:
            self._remove_checked_tree(workspace)
            self._fsync_directory(self.transactions_root)
        except OSError as exc:
            raise TransactionError(
                f"cannot remove transaction workspace: {workspace}"
            ) from exc

    def _remove_checked_tree(self, root: Path) -> None:
        quarantine = self._quarantine_tree(root)
        if _SUPPORTS_SAFE_RMTREE and getattr(
            shutil.rmtree, "avoids_symlink_attacks", False
        ):
            shutil.rmtree(quarantine)
            self._fsync_directory(quarantine.parent)

    def _quarantine_tree(self, root: Path) -> Path:
        self._require_ordinary_directory(root, "transaction removal directory")
        self._require_ordinary_directory(root.parent, "transaction removal parent")
        before = root.lstat()
        identity = (before.st_dev, before.st_ino)
        while True:
            quarantine = root.parent / (_TOMBSTONE_PREFIX + secrets.token_hex(16))
            if not quarantine.exists() and not quarantine.is_symlink():
                break
        try:
            root.rename(quarantine)
            moved = quarantine.lstat()
        except OSError as exc:
            raise TransactionError(
                f"cannot quarantine transaction removal tree: {root}"
            ) from exc
        if (
            (moved.st_dev, moved.st_ino) != identity
            or not stat.S_ISDIR(moved.st_mode)
            or stat.S_ISLNK(moved.st_mode)
            or self._is_reparse_point(moved)
        ):
            raise TransactionError(
                f"transaction removal tree changed while quarantining: {root}"
            )
        self._fsync_directory(root.parent)
        return quarantine

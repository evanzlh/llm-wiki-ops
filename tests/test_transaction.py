from __future__ import annotations

import argparse
import inspect
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Optional

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import cli as cli_module
from obsidian_wiki import portable_manifest as portable_manifest_module
from obsidian_wiki import transaction as transaction_module
from obsidian_wiki import transaction_guidance as transaction_guidance_module
from obsidian_wiki import transaction_validation as transaction_validation_module
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.operations import (
    EMPTY_OPERATION_LOG,
    OperationChange,
    OperationError,
    append_operation,
    append_operation_text,
    parse_operation_log,
)
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.transaction import (
    TransactionError,
    TransactionManager,
    validate_candidate_path,
)
from obsidian_wiki.transaction_validation import (
    ProspectivePage,
    TransactionValidationReport,
    ValidationIssue,
    validate_page_metadata,
    validate_prospective_pages,
)


def test_transaction_excludes_llmwikiops_protocol_directory() -> None:
    assert ".llmwikiops" in transaction_module._CONTROL_DIRECTORIES
    assert ".obsidian-wiki" not in transaction_module._CONTROL_DIRECTORIES


def test_commit_holds_manifest_session_before_snapshot_and_through_manifest_updates(
    tmp_path: Path, log_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-manifest-session")
    candidate_page(record, "concepts/a.md")
    active = False
    observed_upsert = False
    original_session = ShardedManifest.mutation_session
    original_snapshot = manager._snapshot_targets
    original_upsert = ShardedManifest.upsert

    @contextmanager
    def tracked_session(store):
        nonlocal active
        with original_session(store):
            active = True
            try:
                yield store
            finally:
                active = False

    def checked_snapshot(*args, **kwargs):
        assert active
        return original_snapshot(*args, **kwargs)

    def checked_upsert(store, *args, **kwargs):
        nonlocal observed_upsert
        assert active
        observed_upsert = True
        return original_upsert(store, *args, **kwargs)

    monkeypatch.setattr(ShardedManifest, "mutation_session", tracked_session)
    monkeypatch.setattr(manager, "_snapshot_targets", checked_snapshot)
    monkeypatch.setattr(ShardedManifest, "upsert", checked_upsert)

    manager.commit("tx-manifest-session")
    assert observed_upsert
    assert not active


def test_commit_rejects_source_content_drift_after_begin(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source], transaction_id="tx-source-freeze")
    candidate_page(record, "concepts/a.md")
    source.write_text("changed after candidate generation", encoding="utf-8")

    with pytest.raises(TransactionError, match="source.*changed|restart"):
        manager.commit("tx-source-freeze")
    assert manager.load("tx-source-freeze").status in {"active", "failed"}


def test_commit_rejects_source_drift_after_initial_verification(
    tmp_path: Path, log_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source], transaction_id="tx-source-race")
    candidate_page(record, "concepts/a.md")
    original = manager._snapshot_targets

    def drift_after_verify(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_text("drift after verify", encoding="utf-8")
        return result

    monkeypatch.setattr(manager, "_snapshot_targets", drift_after_verify)
    with pytest.raises(TransactionError, match="source.*changed|restart"):
        manager.commit("tx-source-race")
    assert not (config.vault / "concepts/a.md").exists()
    assert ShardedManifest(config).load("sources/a.md") is None


def test_commit_rejects_source_drift_from_log_writer(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)

    def writer(change) -> Path:
        path = append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        source.write_text("drift in operation writer", encoding="utf-8")
        return path

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([source], transaction_id="tx-writer-drift")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(TransactionError, match="source.*changed|restart"):
        manager.commit("tx-writer-drift")
    assert manager.load("tx-writer-drift").status != "complete"
    assert not (config.vault / "concepts/a.md").exists()
    assert ShardedManifest(config).load("sources/a.md") is None
    assert (config.vault / "log.md").read_text(encoding="utf-8") == EMPTY_OPERATION_LOG


def _remove_source_preimages_for_legacy_fixture(workspace: Path) -> None:
    metadata = workspace / "metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload.pop("source_preimages")
    metadata.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def test_legacy_active_transaction_can_list_and_abort_but_not_commit(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="legacy-active")
    candidate_page(record, "concepts/a.md")
    _remove_source_preimages_for_legacy_fixture(record.workspace)

    loaded = manager.list_transactions()[0]
    assert loaded.legacy_source_preimages
    with pytest.raises(TransactionError, match="legacy.*restart"):
        manager.commit("legacy-active")
    manager.abort("legacy-active")
    assert not record.workspace.exists()


def test_legacy_failed_transaction_can_restore_and_discard(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="legacy-failed")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(TransactionError):
        manager.commit("legacy-failed")
    _remove_source_preimages_for_legacy_fixture(record.workspace)

    with pytest.raises(TransactionError, match="legacy.*restore|legacy.*discard"):
        manager.retry("legacy-failed")
    manager.restore("legacy-failed")
    assert manager.load("legacy-failed").status == "restored"
    manager.discard("legacy-failed")


def test_legacy_transaction_cli_lists_and_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="legacy-cli")
    _remove_source_preimages_for_legacy_fixture(record.workspace)
    monkeypatch.chdir(root)

    assert cli_module.main(["transaction", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["transaction_id"] == "legacy-cli"
    assert cli_module.main(
        ["transaction", "abort", "legacy-cli", "--json"]
    ) == 0
    capsys.readouterr()
    assert not record.workspace.exists()

PAGE = """---
title: A
category: concepts
tags:
  - example
sources:
  - sources/a.md
created: 2026-08-07
updated: 2026-08-07
---
# A
"""


def make_config(tmp_path: Path):
    root = tmp_path / "knowledge"
    (root / ".llmwikiops").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / ".skills").mkdir()
    path = root / ".llmwikiops" / "config.toml"
    path.write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".llmwikiops/local"
''',
        encoding="utf-8",
    )
    (root / "wiki" / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
    )
    (root / "wiki" / "log.md").write_text(
        EMPTY_OPERATION_LOG, encoding="utf-8"
    )
    return root, load_portable_config(
        path,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )


def add_source(root: Path, name: str = "a.md") -> Path:
    source = root / "sources" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source", encoding="utf-8")
    return source


def require_symlink_support(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    link.unlink()


@pytest.mark.skipif(os.name != "posix", reason="POSIX repository rebinding safety")
def test_begin_rejects_ordinary_repository_rebound(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    root.rename(tmp_path / "original-knowledge")
    (root / ".llmwikiops").mkdir(parents=True)
    (root / "wiki/concepts").mkdir(parents=True)
    (root / "sources").mkdir()

    with pytest.raises(TransactionError, match="repository root changed"):
        manager.begin([], transaction_id="rebound")

    assert not config.local_state.exists()


@pytest.fixture
def log_writer():
    calls: list[object] = []

    def factory(config, *, fail: bool = False):
        def write(change):
            calls.append(change)
            if fail:
                raise OSError("operation disk full")
            return append_operation(
                config.vault / "log.md",
                change,
                root=config.vault,
                lock_path=config.local_state / "operation-log.lock",
            )

        return write

    factory.calls = calls
    return factory


def candidate_page(record, relative: str, text: str = PAGE) -> Path:
    path = record.candidate_vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run_cli(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_transaction_module_imports_when_fcntl_is_unavailable() -> None:
    script = """
import builtins
import sys

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "fcntl":
        raise ImportError("fcntl unavailable")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
sys.modules.pop("fcntl", None)
sys.modules.pop("obsidian_wiki.transaction", None)
import obsidian_wiki.transaction
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_begin_creates_candidate_workspace_and_lock(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config)

    record = manager.begin(
        [source], transaction_id="tx-1", started_at="2026-08-07T00:00:00Z"
    )

    assert record.transaction_id == "tx-1"
    assert record.status == "active"
    assert record.started_at == "2026-08-07T00:00:00Z"
    assert record.source_ids == ("sources/a.md",)
    assert record.workspace == config.local_state / "transactions" / "tx-1"
    assert record.candidate_vault == record.workspace / "wiki"
    assert record.candidate_vault.is_dir()
    assert (record.workspace / "snapshots").is_dir()
    assert json.loads(manager.lock_path.read_text(encoding="utf-8")) == {
        "started_at": "2026-08-07T00:00:00Z",
        "transaction_id": "tx-1",
    }
    assert (record.workspace / "deletions.json").read_text(encoding="utf-8") == "[]\n"
    assert manager.load("tx-1") == record


def test_transaction_json_is_canonical_and_newline_terminated(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    record = TransactionManager(config).begin(
        [add_source(root)],
        transaction_id="tx-1",
        started_at="2026-08-07T00:00:00Z",
    )

    for path in (
        config.local_state / "write.lock",
        record.workspace / "metadata.json",
        record.workspace / "deletions.json",
    ):
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert (
            text
            == json.dumps(
                json.loads(text), ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n"
        )


def test_begin_records_vault_relative_preimages_and_excludes_local_state(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    target = config.vault / "concepts" / "existing.md"
    target.write_text("existing", encoding="utf-8")
    (config.vault / "hot.md").write_text("derived", encoding="utf-8")
    (config.vault / ".obsidian").mkdir()
    (config.vault / ".obsidian" / "workspace.json").write_text(
        "personal", encoding="utf-8"
    )

    record = TransactionManager(config).begin(
        [source], transaction_id="tx-1", started_at="2026-08-07T00:00:00Z"
    )

    assert set(record.preimages) == {
        ".manifest.json",
        "concepts/existing.md",
        "log.md",
    }
    assert record.preimages["concepts/existing.md"].startswith("sha256:")
    assert all(not key.startswith(".obsidian/") for key in record.preimages)
    assert "hot.md" not in record.preimages
    assert all("transactions" not in key for key in record.preimages)


def test_begin_rejects_nonordinary_vault_entries_without_opening_them(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    fifo = config.vault / "concepts" / "blocking-input"
    os.mkfifo(fifo)

    with pytest.raises(TransactionError, match="vault file.*ordinary file"):
        TransactionManager(config).begin([source], transaction_id="tx-1")

    assert not (config.local_state / "write.lock").exists()
    assert not (config.local_state / "transactions" / "tx-1").exists()


def test_second_transaction_is_rejected_while_lock_exists(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config)
    manager.begin([source], transaction_id="owner-1")

    with pytest.raises(TransactionError, match="owner-1"):
        manager.begin([source], transaction_id="tx-2")


def test_old_lock_is_never_broken_by_age(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config)
    manager.begin([source], transaction_id="owner-1", started_at="2000-01-01T00:00:00Z")

    with pytest.raises(TransactionError, match="owner-1"):
        manager.begin([source], transaction_id="tx-2")

    assert (
        json.loads(manager.lock_path.read_text(encoding="utf-8"))["transaction_id"]
        == "owner-1"
    )


def test_abort_removes_transaction_and_releases_lock(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config)
    manager.begin([source], transaction_id="tx-1")

    manager.abort("tx-1")

    assert manager.list_transactions() == []
    assert not manager.lock_path.exists()


@pytest.mark.parametrize(
    "transaction_id",
    [
        "../escape",
        "/tmp/tx",
        "nested/tx",
        "nested\\tx",
        ".",
        "-leading",
        "a" * 65,
        "space here",
    ],
)
def test_begin_rejects_unsafe_transaction_id(
    tmp_path: Path, transaction_id: str
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)

    with pytest.raises(TransactionError, match="transaction ID"):
        TransactionManager(config).begin([source], transaction_id=transaction_id)

    assert not config.local_state.exists()


def test_generated_transaction_id_is_safe_and_compact(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    record = TransactionManager(config).begin(
        [add_source(root)], started_at="2026-08-07T00:00:00Z"
    )

    prefix, suffix = record.transaction_id.rsplit("-", 1)
    assert len(prefix) == 16
    assert prefix.endswith("Z")
    assert len(suffix) >= 8
    assert suffix == suffix.lower()
    assert all(character in "0123456789abcdef" for character in suffix)


def test_begin_rejects_duplicate_and_missing_sources(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config)

    with pytest.raises(TransactionError, match="duplicate"):
        manager.begin([source, source], transaction_id="tx-1")
    with pytest.raises(TransactionError, match="source.*single-link ordinary file"):
        manager.begin([root / "sources" / "missing.md"], transaction_id="tx-2")

    assert not manager.lock_path.exists()
    assert not (manager.transactions_root / "tx-1").exists()
    assert not (manager.transactions_root / "tx-2").exists()


@pytest.mark.parametrize("kind", ["directory", "symlink", "hardlink"])
def test_begin_rejects_nonordinary_sources(tmp_path: Path, kind: str) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    if kind == "directory":
        source.mkdir()
    elif kind == "symlink":
        external = tmp_path / "external-source.md"
        external.write_text("external", encoding="utf-8")
        source.symlink_to(external)
    else:
        external = tmp_path / "external-source.md"
        external.write_text("external", encoding="utf-8")
        os.link(external, source)

    with pytest.raises(TransactionError, match="single-link ordinary file"):
        TransactionManager(config).begin([source], transaction_id="tx-1")

    assert not config.local_state.exists()


def test_begin_partial_failure_removes_owned_workspace_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config)

    def fail_metadata(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(manager, "_write_metadata", fail_metadata)

    with pytest.raises(TransactionError, match="begin transaction"):
        manager.begin([source], transaction_id="tx-1")

    assert not manager.lock_path.exists()
    assert not (manager.transactions_root / "tx-1").exists()


def test_lock_write_failure_removes_only_the_newly_created_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)

    def fail_sync(descriptor: int) -> None:
        raise OSError("sync failed")

    monkeypatch.setattr(transaction_module.os, "fsync", fail_sync)

    with pytest.raises(TransactionError, match="transaction lock|begin transaction"):
        TransactionManager(config).begin([source], transaction_id="tx-1")

    assert not (config.local_state / "write.lock").exists()
    assert not (config.local_state / "transactions" / "tx-1").exists()


def test_directory_sync_failure_after_lock_create_cleans_owned_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    (config.local_state / "transactions").mkdir(parents=True)
    original_fsync = transaction_module.os.fsync

    def fail_directory_sync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory sync failed")
        original_fsync(descriptor)

    monkeypatch.setattr(transaction_module.os, "fsync", fail_directory_sync)

    with pytest.raises(TransactionError, match="transaction lock|begin transaction"):
        TransactionManager(config).begin([source], transaction_id="tx-1")

    assert not (config.local_state / "write.lock").exists()
    assert not (config.local_state / "transactions" / "tx-1").exists()


def test_transaction_boundaries_fsync_containing_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    original_fsync = transaction_module.os.fsync
    synced_directories: set[tuple[int, int]] = set()

    def record_sync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            synced_directories.add((metadata.st_dev, metadata.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(transaction_module.os, "fsync", record_sync)
    manager = TransactionManager(config)
    record = manager.begin([source], transaction_id="tx-1")

    def identity(path: Path) -> tuple[int, int]:
        metadata = path.stat()
        return metadata.st_dev, metadata.st_ino

    assert identity(config.local_state) in synced_directories
    assert identity(manager.transactions_root) in synced_directories
    assert identity(record.workspace) in synced_directories

    synced_directories.clear()
    transactions_identity = identity(manager.transactions_root)
    local_state_identity = identity(config.local_state)
    manager.abort("tx-1")

    assert transactions_identity in synced_directories
    assert local_state_identity in synced_directories


def test_lock_read_refuses_inode_swap_before_returning_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    lock_metadata = manager.lock_path.stat()
    original_identity = lock_metadata.st_dev, lock_metadata.st_ino
    replacement = config.local_state / "replacement.lock"
    replacement.write_text(
        manager.lock_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    original_read = transaction_module.os.read
    swapped = False

    def swap_during_read(descriptor: int, size: int) -> bytes:
        nonlocal swapped
        metadata = os.fstat(descriptor)
        if not swapped and (metadata.st_dev, metadata.st_ino) == original_identity:
            os.replace(replacement, manager.lock_path)
            swapped = True
        return original_read(descriptor, size)

    monkeypatch.setattr(transaction_module.os, "read", swap_during_read)

    with pytest.raises(TransactionError, match="lock.*changed|inode"):
        manager.abort("tx-1")

    assert swapped
    assert record.workspace.is_dir()
    assert manager.lock_path.is_file()


def test_lock_unlink_refuses_same_owner_replacement_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    replacement = config.local_state / "replacement.lock"
    replacement.write_text(
        manager.lock_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    original_remove = manager._remove_workspace

    def remove_then_replace(workspace: Path) -> None:
        original_remove(workspace)
        os.replace(replacement, manager.lock_path)

    monkeypatch.setattr(manager, "_remove_workspace", remove_then_replace)

    with pytest.raises(TransactionError, match="lock.*changed|inode"):
        manager.abort("tx-1")

    assert not record.workspace.exists()
    assert manager.lock_path.is_file()
    assert (
        json.loads(manager.lock_path.read_text(encoding="utf-8"))["transaction_id"]
        == "tx-1"
    )


def test_begin_loads_legitimate_metadata_larger_than_one_megabyte(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    for index in range(5_000):
        page = config.vault / "concepts" / (f"{index:05d}-" + "x" * 150 + ".md")
        page.write_text(f"page {index}", encoding="utf-8")

    manager = TransactionManager(config)
    record = manager.begin([source], transaction_id="tx-1")

    metadata = record.workspace / "metadata.json"
    assert metadata.stat().st_size > 1024 * 1024
    assert len(record.preimages) == 5_002
    assert manager.load("tx-1") == record


def test_existing_lock_uses_a_strict_small_size_bound(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    config.local_state.mkdir(parents=True)
    lock = config.local_state / "write.lock"
    lock.write_text(
        json.dumps({"started_at": "2026-08-07T00:00:00Z", "transaction_id": "owner"})
        + " " * (32 * 1024)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TransactionError, match="lock.*large"):
        TransactionManager(config).begin([source], transaction_id="tx-1")

    assert lock.exists()


def test_transactions_root_and_workspace_must_not_be_symlinks(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    external = tmp_path / "external-transactions"
    external.mkdir()
    config.local_state.mkdir(parents=True)
    (config.local_state / "transactions").symlink_to(external, target_is_directory=True)

    with pytest.raises(TransactionError, match="symlink|ordinary directory"):
        TransactionManager(config).begin([source], transaction_id="tx-1")

    assert list(external.iterdir()) == []
    assert not (config.local_state / "write.lock").exists()


def test_existing_symlink_workspace_is_not_followed_or_removed(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    external = tmp_path / "external-workspace"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    transactions = config.local_state / "transactions"
    transactions.mkdir(parents=True)
    (transactions / "tx-1").symlink_to(external, target_is_directory=True)

    with pytest.raises(TransactionError, match="workspace|symlink|already exists"):
        TransactionManager(config).begin([source], transaction_id="tx-1")

    assert marker.read_text(encoding="utf-8") == "keep"
    assert (transactions / "tx-1").is_symlink()
    assert not (config.local_state / "write.lock").exists()


@pytest.mark.parametrize(
    ("target", "payload", "match"),
    [
        ("write.lock", "not json\n", "lock"),
        (
            "write.lock",
            '{"started_at":"x","transaction_id":"../escape"}\n',
            "lock|transaction ID",
        ),
    ],
)
def test_begin_refuses_malformed_existing_lock(
    tmp_path: Path, target: str, payload: str, match: str
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    config.local_state.mkdir(parents=True)
    lock = config.local_state / target
    lock.write_text(payload, encoding="utf-8")

    with pytest.raises(TransactionError, match=match):
        TransactionManager(config).begin([source], transaction_id="tx-1")

    assert lock.read_text(encoding="utf-8") == payload


def test_load_rejects_malformed_metadata_status_and_path_keys(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    metadata_path = record.workspace / "metadata.json"
    original = json.loads(metadata_path.read_text(encoding="utf-8"))

    metadata_path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(TransactionError, match="metadata"):
        manager.load("tx-1")

    malformed = dict(original)
    malformed["status"] = "unknown"
    metadata_path.write_text(json.dumps(malformed) + "\n", encoding="utf-8")
    with pytest.raises(TransactionError, match="status"):
        manager.load("tx-1")

    malformed = dict(original)
    malformed["preimages"] = {"../escape.md": "sha256:" + "0" * 64}
    metadata_path.write_text(json.dumps(malformed) + "\n", encoding="utf-8")
    with pytest.raises(TransactionError, match="preimage|path"):
        manager.load("tx-1")


def test_load_and_list_reject_symlinked_or_unexpected_entries(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    manager.lock_path.unlink()
    external = tmp_path / "metadata.json"
    external.write_text(
        (record.workspace / "metadata.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (record.workspace / "metadata.json").unlink()
    (record.workspace / "metadata.json").symlink_to(external)

    with pytest.raises(TransactionError, match="metadata.*single-link ordinary file"):
        manager.load("tx-1")

    unexpected = manager.transactions_root / "not-a-transaction.txt"
    unexpected.write_text("unexpected", encoding="utf-8")
    with pytest.raises(TransactionError, match="transaction.*ordinary directory"):
        manager.list_transactions()


def test_list_transactions_is_sorted_by_transaction_id(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config)
    manager.begin([source], transaction_id="z-last")
    manager.lock_path.unlink()
    manager.begin([source], transaction_id="a-first")

    assert [record.transaction_id for record in manager.list_transactions()] == [
        "a-first",
        "z-last",
    ]


def test_abort_refuses_foreign_lock_without_removing_workspace(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    manager.lock_path.write_text(
        json.dumps(
            {"started_at": record.started_at, "transaction_id": "foreign"},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TransactionError, match="foreign"):
        manager.abort("tx-1")

    assert record.workspace.is_dir()
    assert manager.lock_path.exists()


@pytest.mark.parametrize("status", ["complete", "restored"])
def test_abort_refuses_retained_transaction_statuses(
    tmp_path: Path, log_writer, status: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    manager.commit("tx-1")
    if status == "restored":
        manager.restore("tx-1")

    with pytest.raises(TransactionError, match="retain|discard|status"):
        manager.abort("tx-1")

    assert record.workspace.is_dir()
    assert not manager.lock_path.exists()


def test_abort_without_lock_only_cleans_failed_transaction(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    manager.lock_path.unlink()

    with pytest.raises(TransactionError, match="lock"):
        manager.abort("tx-1")
    assert record.workspace.is_dir()

    metadata_path = record.workspace / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["status"] = "failed"
    metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manager.abort("tx-1")
    assert not record.workspace.exists()


def test_abort_refuses_symlink_inside_workspace_without_touching_target(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    (record.workspace / "wiki" / "external").symlink_to(
        external, target_is_directory=True
    )

    with pytest.raises(TransactionError, match="symlink"):
        manager.abort("tx-1")

    assert marker.read_text(encoding="utf-8") == "keep"
    assert record.workspace.exists()
    assert manager.lock_path.exists()


def test_abort_uses_checked_path_fallback_when_safe_rmtree_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    monkeypatch.setattr(shutil.rmtree, "avoids_symlink_attacks", False)

    manager.abort("tx-1")

    assert not record.workspace.exists()
    assert not manager.lock_path.exists()
    tombstones = list(manager.transactions_root.glob(".tombstone-*"))
    assert len(tombstones) == 1
    assert (tombstones[0] / "metadata.json").is_file()
    assert manager.list_transactions() == []


def test_windows_reparse_attribute_is_treated_as_unsafe() -> None:
    metadata = type("Metadata", (), {"st_file_attributes": 0x400})()

    assert TransactionManager._is_reparse_point(metadata)


def test_transaction_manifest_mutation_fails_closed_without_posix_capabilities(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    deleted = config.vault / "concepts/deleted.md"
    deleted.write_text(PAGE, encoding="utf-8")
    monkeypatch.setattr(transaction_module, "_SUPPORTS_DIR_FD", False, raising=False)
    monkeypatch.setattr(
        transaction_module, "_SUPPORTS_DIRECTORY_FSYNC", False, raising=False
    )
    monkeypatch.setattr(
        transaction_module, "_SUPPORTS_SAFE_RMTREE", False, raising=False
    )
    monkeypatch.setattr(portable_manifest_module, "_SUPPORTS_DIRECTORY_FSYNC", False)
    original_open = transaction_module.os.open
    original_stat = transaction_module.os.stat
    original_unlink = transaction_module.os.unlink
    original_fsync = transaction_module.os.fsync

    def reject_dir_fd_open(*args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            raise NotImplementedError("dir_fd unavailable")
        return original_open(*args, **kwargs)

    def reject_dir_fd_stat(*args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            raise NotImplementedError("dir_fd unavailable")
        return original_stat(*args, **kwargs)

    def reject_dir_fd_unlink(*args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            raise NotImplementedError("dir_fd unavailable")
        return original_unlink(*args, **kwargs)

    def reject_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory fsync unavailable")
        original_fsync(descriptor)

    monkeypatch.setattr(transaction_module.os, "open", reject_dir_fd_open)
    monkeypatch.setattr(transaction_module.os, "stat", reject_dir_fd_stat)
    monkeypatch.setattr(transaction_module.os, "unlink", reject_dir_fd_unlink)
    monkeypatch.setattr(transaction_module.os, "fsync", reject_directory_fsync)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    manager.mark_delete("tx-1", "concepts/deleted.md")

    with pytest.raises(TransactionError, match="safe manifest mutation requires"):
        manager.commit("tx-1")

    manager.discard("tx-1")

    assert deleted.read_text(encoding="utf-8") == PAGE
    assert not (config.vault / "concepts/a.md").exists()
    assert not record.workspace.exists()
    assert not manager.lock_path.exists()


@pytest.mark.skipif(
    not shutil.rmtree.avoids_symlink_attacks,
    reason="path replacement behavior requires symlink-safe rmtree",
)
def test_abort_path_replacement_never_traverses_external_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    external = tmp_path / "external-delete-target"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    moved = record.workspace.with_name("tx-1-moved")
    original_validate = manager._require_managed_tree
    calls = 0

    def replace_after_validation(workspace: Path) -> None:
        nonlocal calls
        original_validate(workspace)
        calls += 1
        if calls == 2:
            workspace.rename(moved)
            workspace.symlink_to(external, target_is_directory=True)

    monkeypatch.setattr(manager, "_require_managed_tree", replace_after_validation)

    with pytest.raises(
        TransactionError, match="remove transaction workspace|removal directory"
    ):
        manager.abort("tx-1")

    assert marker.read_text(encoding="utf-8") == "keep"
    assert record.workspace.is_symlink()
    assert manager.lock_path.is_file()


def test_commit_promotes_candidate_and_updates_manifest(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    result = manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert (config.vault / "concepts/a.md").read_text(encoding="utf-8") == PAGE
    entry = ShardedManifest(config).load("sources/a.md")
    assert entry is not None
    assert entry.pages == ("concepts/a.md",)
    assert entry.compiled_at == "2026-08-07T01:00:00Z"
    assert result.transaction_id == "tx-1"
    assert result.created == ("concepts/a.md",)
    assert result.updated == ()
    assert result.removed == ()
    assert result.log_path == "log.md"
    records = parse_operation_log((config.vault / "log.md").read_text(encoding="utf-8"))
    assert records == (log_writer.calls[-1],)
    assert not manager.lock_path.exists()
    assert record.workspace.exists()
    assert manager.load("tx-1").status == "complete"


def test_commit_accepts_supported_nested_frontmatter(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    nested_page = PAGE.replace(
        "---\n# A\n",
        '''provenance:
  extracted: 0.72
  inferred: 0.25
  ambiguous: 0.03
relationships:
  - target: "[[concepts/a]]"
    type: related-to
---
# A
''',
    )
    candidate_page(record, "concepts/a.md", nested_page)

    manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert (config.vault / "concepts/a.md").read_text(encoding="utf-8") == nested_page


@pytest.mark.parametrize(
    "provenance",
    [
        "provenance:\n  extracted: >-\n  inferred: 0.25\n  ambiguous: 0.03",
        '"provenance": []\nprovenance:\n  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03',
    ],
    ids=["block-scalar", "reserved-key"],
)
def test_commit_rejects_unsafe_provenance_frontmatter(
    tmp_path: Path, log_writer, provenance: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    page = PAGE.replace("---\n", f"---\n{provenance}\n", 1)
    candidate_page(record, "concepts/a.md", page)

    with pytest.raises(TransactionError, match="provenance"):
        manager.commit("tx-1")

    assert not (config.vault / "concepts/a.md").exists()


def test_default_log_writer_records_operation_last(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    result = manager.commit("tx-1", completed_at="2026-08-07T07:30:00Z")

    assert (config.vault / "concepts/a.md").is_file()
    assert result.log_path == "log.md"
    assert (config.vault / result.log_path).is_file()
    assert parse_operation_log(
        (config.vault / result.log_path).read_text(encoding="utf-8")
    )[0].transaction_id == "tx-1"
    assert manager.load("tx-1").status == "complete"


def test_commit_refuses_target_changed_after_begin(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Candidate"))
    concurrent = PAGE.replace("# A", "# Concurrent")
    target.write_text(concurrent, encoding="utf-8")

    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == concurrent
    assert manager.load("tx-1").status == "active"
    assert manager.lock_path.exists()


def test_source_drift_requires_transaction_restart(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    source.write_text("new source bytes", encoding="utf-8")

    with pytest.raises(TransactionError, match="source.*changed|restart"):
        manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")


def test_failed_manifest_write_rolls_back_promoted_page_and_shard(
    tmp_path: Path, log_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manifest = ShardedManifest(config)
    manifest.upsert(source, pages=[], compiled_at="2026-08-06T00:00:00Z")
    shard = manifest.entry_path("sources/a.md")
    original_shard = shard.read_bytes()
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Candidate"))
    monkeypatch.setattr(
        ShardedManifest,
        "upsert",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(TransactionError, match="rolled back.*disk full"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == PAGE
    assert shard.read_bytes() == original_shard
    assert manager.load("tx-1").status == "failed"
    assert not manager.lock_path.exists()


def test_absent_manifest_shard_is_removed_during_rollback(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*operation disk full"):
        manager.commit("tx-1")

    assert ShardedManifest(config).load("sources/a.md") is None
    assert not (config.vault / "concepts/a.md").exists()


def test_multiple_candidates_mid_promotion_failure_rolls_back_all_pages(
    tmp_path: Path, log_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    candidate_page(
        record,
        "references/z.md",
        PAGE.replace("category: concepts", "category: references"),
    )
    original = manager._promote_candidate
    calls = 0

    def fail_second(candidate: Path, target: Path, expected_preimage) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second promotion failed")
        original(candidate, target, expected_preimage)

    monkeypatch.setattr(manager, "_promote_candidate", fail_second)

    with pytest.raises(TransactionError, match="rolled back.*second promotion failed"):
        manager.commit("tx-1")

    assert not (config.vault / "concepts/a.md").exists()
    assert not (config.vault / "references/z.md").exists()
    assert manager.load("tx-1").status == "failed"


def test_failed_transaction_can_retry_after_fault_is_removed(
    tmp_path: Path, log_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original = ShardedManifest.upsert
    monkeypatch.setattr(
        ShardedManifest,
        "upsert",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")
    monkeypatch.setattr(ShardedManifest, "upsert", original)

    result = manager.retry("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert result.created == ("concepts/a.md",)
    assert manager.load("tx-1").status == "complete"


def test_retry_refuses_live_target_change_and_foreign_lock(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")
    target = config.vault / "concepts/a.md"
    target.write_text("foreign output", encoding="utf-8")

    with pytest.raises(TransactionError, match="residual.*changed"):
        manager.retry("tx-1")
    assert target.read_text(encoding="utf-8") == "foreign output"

    target.unlink()
    manager._acquire_lock("foreign", "2026-08-07T02:00:00Z")
    with pytest.raises(TransactionError, match="foreign"):
        manager.retry("tx-1")


def test_restore_and_discard_are_explicit_and_idempotent(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    updated = PAGE.replace("# A", "# Updated")
    candidate_page(record, "concepts/a.md", updated)
    result = manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    manager.restore("tx-1")
    manager.restore("tx-1")

    assert target.read_text(encoding="utf-8") == PAGE
    assert (config.vault / result.log_path).read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert manager.load("tx-1").status == "restored"
    manager.discard("tx-1")
    manager.discard("tx-1")
    assert manager.list_transactions() == []


def test_restore_refuses_postimage_drift(tmp_path: Path, log_writer) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    manager.commit("tx-1")
    target = config.vault / "concepts/a.md"
    target.write_text("later work", encoding="utf-8")

    with pytest.raises(TransactionError, match="changed after transaction completed"):
        manager.restore("tx-1")

    assert target.read_text(encoding="utf-8") == "later work"
    assert manager.load("tx-1").status == "complete"


def test_restore_refuses_owner_drift_in_operation_log(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")
    append_operation(
        config.vault / "log.md",
        OperationChange(
            "owner-change",
            "2026-08-07T02:00:00Z",
            ("sources/a.md",),
            (),
            (),
            (),
        ),
        root=config.vault,
                lock_path=config.root.parent / ".operation-log.lock",
    )
    owner_bytes = (config.vault / "log.md").read_bytes()

    with pytest.raises(TransactionError, match="changed after transaction completed"):
        manager.restore("tx-1")

    assert (config.vault / "log.md").read_bytes() == owner_bytes
    assert manager.load("tx-1").status == "complete"


def test_restore_failed_transaction_marks_restored_without_overwriting(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Updated"))
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")

    manager.restore("tx-1")

    assert target.read_text(encoding="utf-8") == PAGE
    assert manager.load("tx-1").status == "restored"


@pytest.mark.parametrize(
    "raw",
    [
        "../escape.md",
        "/tmp/escape.md",
        "C:/escape.md",
        "nested\\escape.md",
        "index.md",
        "log.md",
        "hot.md",
        ".manifest.json",
        ".manifest/sources/a.json",
        ".obsidian/workspace.md",
        "concepts/.git/escape.md",
        "concepts/not-markdown.txt",
    ],
)
def test_candidate_path_validation_rejects_unsafe_and_control_paths(
    tmp_path: Path, raw: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")

    with pytest.raises(TransactionError, match="candidate|control|reserved|markdown"):
        validate_candidate_path(record.candidate_vault, raw)


def test_candidate_path_accepts_journal_operations_as_knowledge(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")

    assert validate_candidate_path(
        record.candidate_vault, "journal/operations/entry.md"
    ) == "journal/operations/entry.md"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_commit_rejects_nonordinary_candidate_files(
    tmp_path: Path, log_writer, kind: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate = record.candidate_vault / "concepts/a.md"
    candidate.parent.mkdir(parents=True)
    external = tmp_path / "external.md"
    external.write_text(PAGE, encoding="utf-8")
    if kind == "symlink":
        candidate.symlink_to(external)
    elif kind == "hardlink":
        os.link(external, candidate)
    else:
        os.mkfifo(candidate)

    with pytest.raises(TransactionError, match="candidate.*single-link ordinary file"):
        manager.commit("tx-1")

    metadata = json.loads((record.workspace / "metadata.json").read_text())
    assert metadata["status"] == "active"


@pytest.mark.parametrize(
    "text",
    [
        "# no frontmatter\n",
        PAGE.replace("title: A\n", ""),
        PAGE.replace("sources:\n  - sources/a.md", "sources: []"),
        PAGE.replace("sources/a.md", "sources/foreign.md"),
        PAGE.replace("sources/a.md", "../foreign.md"),
    ],
)
def test_commit_rejects_invalid_or_foreign_candidate_frontmatter(
    tmp_path: Path, log_writer, text: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", text)

    with pytest.raises(TransactionError, match="frontmatter|source"):
        manager.commit("tx-1")

    assert not (config.vault / "concepts/a.md").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "../escape.md",
        "/tmp/escape.md",
        "nested\\escape.md",
        "index.md",
        "log.md",
        "hot.md",
        ".manifest.json",
        ".manifest/sources/a.json",
        ".obsidian/workspace.md",
        "concepts/.git/escape.md",
        "concepts/not-markdown.txt",
    ],
)
def test_mark_delete_rejects_unsafe_and_control_paths(tmp_path: Path, raw: str) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    manager.begin([add_source(root)], transaction_id="tx-1")

    with pytest.raises(TransactionError, match="deletion|control|reserved|markdown"):
        manager.mark_delete("tx-1", raw)


def test_mark_delete_accepts_journal_operations_as_knowledge(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    manager.begin([add_source(root)], transaction_id="tx-1")

    manager.mark_delete("tx-1", "journal/operations/entry.md")

    assert manager.load("tx-1").deletions == ("journal/operations/entry.md",)


def test_mark_delete_rejects_duplicate_and_commit_removes_page(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, log_writer=log_writer(config))
    manager.begin([add_source(root)], transaction_id="tx-1")
    manager.mark_delete("tx-1", "concepts/a.md")
    with pytest.raises(TransactionError, match="duplicate"):
        manager.mark_delete("tx-1", "concepts/a.md")

    result = manager.commit("tx-1")

    assert result.removed == ("concepts/a.md",)
    assert not target.exists()


def test_multi_source_page_relationships_are_preserved(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")
    shared = PAGE.replace("  - sources/a.md\n", "  - sources/a.md\n  - sources/b.md\n")
    existing = config.vault / "concepts/shared.md"
    existing.write_text(shared, encoding="utf-8")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source_a, source_b], transaction_id="tx-1")
    candidate_page(
        record,
        "references/a.md",
        PAGE.replace("category: concepts", "category: references"),
    )

    manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    manifest = ShardedManifest(config)
    assert manifest.load("sources/a.md").pages == (
        "concepts/shared.md",
        "references/a.md",
    )
    assert manifest.load("sources/b.md").pages == ("concepts/shared.md",)


def test_commit_resolves_timestamp_once_and_sorts_change_sets(
    tmp_path: Path, log_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")
    updated = config.vault / "references/z.md"
    updated.parent.mkdir(parents=True)
    updated.write_text(PAGE, encoding="utf-8")
    removed = config.vault / "concepts/z.md"
    removed.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source_b, source_a], transaction_id="tx-1")
    candidate_page(
        record,
        "references/z.md",
        PAGE.replace("category: concepts", "category: references").replace(
            "# A", "# Z"
        ),
    )
    candidate_page(record, "concepts/b.md")
    candidate_page(record, "concepts/a.md")
    manager.mark_delete("tx-1", "concepts/z.md")
    times = iter(["2026-08-07T01:00:00Z", "unexpected-second-call"])
    monkeypatch.setattr(manager, "_utc_now", lambda: next(times))

    result = manager.commit("tx-1")

    assert result.created == ("concepts/a.md", "concepts/b.md")
    assert result.updated == ("references/z.md",)
    assert result.removed == ("concepts/z.md",)
    assert (
        ShardedManifest(config).load("sources/a.md").compiled_at
        == "2026-08-07T01:00:00Z"
    )
    assert (
        ShardedManifest(config).load("sources/b.md").compiled_at
        == "2026-08-07T01:00:00Z"
    )
    change = log_writer.calls[-1]
    assert change.completed_at == "2026-08-07T01:00:00Z"
    assert change.source_ids == ("sources/a.md", "sources/b.md")


def test_complete_metadata_failure_rolls_back_everything(
    tmp_path: Path, log_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original = manager._write_metadata
    failed = False

    def fail_complete(workspace: Path, payload: dict[str, object]) -> None:
        nonlocal failed
        if payload.get("status") == "complete" and not failed:
            failed = True
            raise OSError("metadata disk full")
        original(workspace, payload)

    monkeypatch.setattr(manager, "_write_metadata", fail_complete)

    with pytest.raises(TransactionError, match="rolled back.*metadata disk full"):
        manager.commit("tx-1")

    assert not (config.vault / "concepts/a.md").exists()
    assert (config.vault / "log.md").read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert manager.load("tx-1").status == "failed"


def test_discard_refuses_active_symlinked_workspace_and_foreign_lock(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    with pytest.raises(TransactionError, match="active|promoting"):
        manager.discard("tx-1")
    candidate_page(record, "concepts/a.md")
    manager.commit("tx-1")
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "keep"
    marker.write_text("keep", encoding="utf-8")
    (record.workspace / "link").symlink_to(external, target_is_directory=True)

    with pytest.raises(TransactionError, match="symlink"):
        manager.discard("tx-1")

    (record.workspace / "link").unlink()
    manager._acquire_lock("foreign", "2026-08-07T02:00:00Z")
    with pytest.raises(TransactionError, match="foreign"):
        manager.discard("tx-1")
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("snapshot_index", {"concepts/a.md": "../escape"}, "snapshot"),
        ("postimages", {"../escape": None}, "postimage"),
        ("created", ["concepts/b.md", "concepts/a.md"], "created"),
        ("completed_at", 42, "completion|completed"),
    ],
)
def test_load_rejects_malformed_recovery_metadata(
    tmp_path: Path, field: str, value: object, match: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    metadata = record.workspace / "metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload[field] = value
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TransactionError, match=match):
        manager.load("tx-1")


def test_retry_clears_snapshot_index_before_removing_old_snapshot_files(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")

    def fail_clear(_record) -> None:
        raise OSError("snapshot cleanup interrupted")

    monkeypatch.setattr(manager, "_clear_snapshot_files", fail_clear)
    with pytest.raises(OSError, match="snapshot cleanup interrupted"):
        manager.retry("tx-1")

    payload = json.loads((record.workspace / "metadata.json").read_text())
    assert payload["snapshot_index"] == {}
    assert payload["status"] == "failed"
    assert not manager.lock_path.exists()


def test_promotion_directory_fsync_failure_rolls_back(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original = manager._fsync_directory
    injected = False

    def fail_once(path: Path) -> None:
        nonlocal injected
        if path == config.vault / "concepts" and not injected:
            injected = True
            raise TransactionError("promotion fsync failed")
        original(path)

    monkeypatch.setattr(manager, "_fsync_directory", fail_once)

    with pytest.raises(TransactionError, match="rolled back.*promotion fsync failed"):
        manager.commit("tx-1")

    assert injected
    assert not (config.vault / "concepts/a.md").exists()
    assert manager.load("tx-1").status == "failed"


def test_log_writer_runs_after_pages_and_all_manifest_shards(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")

    def writer(change):
        assert (config.vault / "concepts/a.md").is_file()
        manifest = ShardedManifest(config)
        assert manifest.load("sources/a.md") is not None
        assert manifest.load("sources/b.md") is not None
        return append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([source_b, source_a], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    manager.commit("tx-1")


def test_log_writer_partial_creation_is_removed_on_failure(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)

    def writer(change):
        append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        raise OSError("operation interrupted")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*operation interrupted"):
        manager.commit("tx-1")

    assert (config.vault / "log.md").read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert not (config.vault / "concepts/a.md").exists()


def test_log_writer_malformed_installed_log_requires_manual_recovery(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"

    def writer(_change):
        log.write_text("malformed log\n", encoding="utf-8")
        return log

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-malformed-log")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="requires manual recovery.*invalid log"):
        manager.commit("tx-malformed-log", completed_at="2026-08-07T01:00:00Z")

    assert log.read_text(encoding="utf-8") == "malformed log\n"
    assert (config.vault / "concepts/a.md").exists()


def test_log_writer_wrong_last_record_requires_manual_recovery(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"

    def writer(change):
        wrong = OperationChange(
            "different-transaction",
            change.completed_at,
            change.source_ids,
            change.created,
            change.updated,
            change.removed,
        )
        return append_operation(log, wrong, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-wrong-tail")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="requires manual recovery.*intended"):
        manager.commit("tx-wrong-tail", completed_at="2026-08-07T01:00:00Z")

    assert parse_operation_log(log.read_text(encoding="utf-8"))[0].transaction_id == (
        "different-transaction"
    )
    assert (config.vault / "concepts/a.md").exists()


def test_log_writer_cannot_replace_valid_history_before_intended_tail(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"
    original_record = OperationChange(
        "original-history",
        "2026-08-07T00:00:00Z",
        ("sources/a.md",),
        (),
        (),
        (),
    )
    append_operation(log, original_record, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")

    def writer(change):
        replacement = OperationChange(
            "replacement-history",
            "2026-08-07T00:00:00Z",
            change.source_ids,
            (),
            (),
            (),
        )
        installed = append_operation_text(
            append_operation_text(EMPTY_OPERATION_LOG, replacement), change
        )
        log.write_text(installed, encoding="utf-8")
        return log

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-history")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(
        TransactionError, match="requires manual recovery.*exact expected log"
    ):
        manager.commit("tx-history", completed_at="2026-08-07T01:00:00Z")

    assert [item.transaction_id for item in parse_operation_log(log.read_text())] == [
        "replacement-history",
        "tx-history",
    ]
    assert (config.vault / "concepts/a.md").exists()


def test_log_drift_after_initial_writer_validation_requires_manual_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-final-log-check")
    candidate_page(record, "concepts/a.md")
    original_validate = manager._validate_log_result
    drifted = False

    def validate_then_drift(*args, **kwargs):
        nonlocal drifted
        result = original_validate(*args, **kwargs)
        if not drifted:
            drifted = True
            append_operation(
                log,
                OperationChange(
                    "owner-drift",
                    "2026-08-07T02:00:00Z",
                    ("sources/a.md",),
                    (),
                    (),
                    (),
                ),
                root=config.vault,
                lock_path=config.local_state / "operation-log.lock",
            )
        return result

    monkeypatch.setattr(manager, "_validate_log_result", validate_then_drift)

    with pytest.raises(
        TransactionError, match="requires manual recovery.*exact expected log"
    ):
        manager.commit("tx-final-log-check", completed_at="2026-08-07T01:00:00Z")

    assert drifted
    assert [item.transaction_id for item in parse_operation_log(log.read_text())] == [
        "tx-final-log-check",
        "owner-drift",
    ]
    page = config.vault / "concepts/a.md"
    assert page.exists()
    assert ShardedManifest(config).load("sources/a.md") is not None
    live_log = log.read_bytes()
    live_page = page.read_bytes()
    metadata = json.loads((record.workspace / "metadata.json").read_text())
    assert metadata["status"] == "failed"
    assert metadata["residual_postimages"] is None
    assert set(metadata["rollback_exclusions"]) == {
        ShardedManifest(config)
        .entry_path("sources/a.md")
        .relative_to(config.vault)
        .as_posix(),
        "concepts/a.md",
        "log.md",
    }

    for action in (manager.retry, manager.restore):
        with pytest.raises(TransactionError, match="discard is required"):
            action("tx-final-log-check")
        assert log.read_bytes() == live_log
        assert page.read_bytes() == live_page


def test_owner_append_before_writer_validation_requires_manual_recovery(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"
    lock_path = config.local_state / "operation-log.lock"

    def writer(change):
        result = append_operation(
            log, change, root=config.vault, lock_path=lock_path
        )
        append_operation(
            log,
            OperationChange(
                "owner-before-validation",
                "2026-08-07T02:00:00Z",
                ("sources/a.md",),
                (),
                (),
                (),
            ),
            root=config.vault,
            lock_path=lock_path,
        )
        return result

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin(
        [add_source(root)], transaction_id="tx-owner-before-validation"
    )
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="requires manual recovery"):
        manager.commit(
            "tx-owner-before-validation", completed_at="2026-08-07T01:00:00Z"
        )

    assert [item.transaction_id for item in parse_operation_log(log.read_text())] == [
        "tx-owner-before-validation",
        "owner-before-validation",
    ]
    assert (config.vault / "concepts/a.md").exists()
    assert ShardedManifest(config).load("sources/a.md") is not None
    payload = json.loads((record.workspace / "metadata.json").read_text())
    assert payload["status"] == "failed"
    assert payload["residual_postimages"] is None


def test_manual_recovery_metadata_failure_retains_lock_and_blocks_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"
    operation_lock = config.local_state / "operation-log.lock"

    def writer(change):
        result = append_operation(
            log, change, root=config.vault, lock_path=operation_lock
        )
        append_operation(
            log,
            OperationChange(
                "owner-after-main",
                "2026-08-07T02:00:00Z",
                ("sources/a.md",),
                (),
                (),
                (),
            ),
            root=config.vault,
            lock_path=operation_lock,
        )
        return result

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-metadata-failure")
    candidate_page(record, "concepts/a.md")
    original_write = manager._write_metadata

    def fail_failed_metadata(workspace, payload):
        if payload.get("status") == "failed":
            raise TransactionError("simulated failed metadata write")
        return original_write(workspace, payload)

    monkeypatch.setattr(manager, "_write_metadata", fail_failed_metadata)

    with pytest.raises(TransactionError, match="metadata failed"):
        manager.commit(
            "tx-metadata-failure", completed_at="2026-08-07T01:00:00Z"
        )

    live_log = log.read_bytes()
    live_page = (config.vault / "concepts/a.md").read_bytes()
    assert manager.load("tx-metadata-failure").status == "promoting"
    assert (config.local_state / "write.lock").exists()

    with pytest.raises(TransactionError, match="manual intervention"):
        manager.restore("tx-metadata-failure")

    assert log.read_bytes() == live_log
    assert (config.vault / "concepts/a.md").read_bytes() == live_page
    assert (config.local_state / "write.lock").exists()


def test_duplicate_transaction_in_log_is_rejected_and_preserved(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"
    existing = OperationChange(
        "tx-duplicate",
        "2026-08-07T00:00:00Z",
        ("sources/a.md",),
        (),
        (),
        (),
    )
    append_operation(log, existing, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
    original = log.read_bytes()
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-duplicate")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*already exists"):
        manager.commit("tx-duplicate", completed_at="2026-08-07T01:00:00Z")

    assert log.read_bytes() == original


def test_commit_rejects_log_preimage_drift_before_promotion(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-log-drift")
    candidate_page(record, "concepts/a.md")
    append_operation(
        config.vault / "log.md",
        OperationChange(
            "owner-change",
            "2026-08-07T00:00:00Z",
            ("sources/a.md",),
            (),
            (),
            (),
        ),
        root=config.vault,
                lock_path=config.root.parent / ".operation-log.lock",
    )

    with pytest.raises(TransactionError, match="changed after transaction began: log.md"):
        manager.commit("tx-log-drift", completed_at="2026-08-07T01:00:00Z")

    assert not (config.vault / "concepts/a.md").exists()


@pytest.mark.parametrize(
    "relative",
    [
        "index.md",
        ".manifest/sources/forged.json",
        ".obsidian/workspace.md",
    ],
)
def test_commit_revalidates_tampered_deletions_file(
    tmp_path: Path,
    log_writer,
    relative: str,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    (record.workspace / "deletions.json").write_text(
        json.dumps([relative], indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        TransactionError, match="deletion.*(control|reserved|markdown)"
    ):
        manager.commit("tx-1")

    assert log_writer.calls == []
    assert (
        json.loads((record.workspace / "metadata.json").read_text())["status"]
        == "active"
    )


def test_log_writer_overwrite_then_return_requires_manual_recovery(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    operation = config.vault / "log.md"

    def writer(_change):
        operation.write_text("corrupted operation\n", encoding="utf-8")
        return operation

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="requires manual recovery"):
        manager.commit("tx-1")

    assert operation.read_text() == "corrupted operation\n"
    assert (config.vault / "concepts/a.md").exists()
    assert manager.load("tx-1").status == "failed"


def test_log_writer_overwrite_then_raise_requires_manual_recovery(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    operation = config.vault / "log.md"

    def writer(_change):
        operation.write_text("corrupted operation\n", encoding="utf-8")
        raise OSError("writer failed after overwrite")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(
        TransactionError, match="requires manual recovery.*writer failed"
    ):
        manager.commit("tx-1")

    assert operation.read_text() == "corrupted operation\n"
    assert (config.vault / "concepts/a.md").exists()


def test_log_writer_extra_page_then_return_rolls_back_all_new_pages(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    returned = config.vault / "log.md"
    extra = config.vault / "concepts/extra.md"

    def writer(change):
        append_operation(returned, change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        extra.write_text("extra\n", encoding="utf-8")
        return returned

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*side effect"):
        manager.commit("tx-1")

    assert returned.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert not extra.exists()


def test_rollback_holds_operation_lock_through_log_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"
    operation_lock_path = config.local_state / "operation-log.lock"
    extra = config.vault / "concepts/extra.md"

    def writer(change):
        result = append_operation(
            log, change, root=config.vault, lock_path=operation_lock_path
        )
        extra.write_text("unauthorized\n", encoding="utf-8")
        return result

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-rollback-lock")
    candidate_page(record, "concepts/a.md")
    original_restore = manager._restore_snapshot_index
    nested_error = None

    def append_owner_during_restore(*args, **kwargs):
        nonlocal nested_error
        if nested_error is None:
            try:
                append_operation(
                    log,
                    OperationChange(
                        "owner-during-rollback",
                        "2026-08-07T02:00:00Z",
                        ("sources/a.md",),
                        (),
                        (),
                        (),
                    ),
                    root=config.vault,
                    lock_path=operation_lock_path,
                )
            except OperationError as exc:
                nested_error = exc
        return original_restore(*args, **kwargs)

    monkeypatch.setattr(manager, "_restore_snapshot_index", append_owner_during_restore)

    with pytest.raises(TransactionError, match="rolled back.*side effect"):
        manager.commit("tx-rollback-lock", completed_at="2026-08-07T01:00:00Z")

    assert nested_error is not None
    assert "in progress" in str(nested_error)
    assert log.read_text() == EMPTY_OPERATION_LOG
    assert not (config.vault / "concepts/a.md").exists()


def test_restore_prepared_promoting_before_writer_touch_succeeds(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)

    def crash_before_touch(_change):
        raise SystemExit("before writer touch")

    manager = TransactionManager(config, log_writer=crash_before_touch)
    record = manager.begin([add_source(root)], transaction_id="tx-before-writer-touch")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(SystemExit, match="before writer touch"):
        manager.commit("tx-before-writer-touch")

    assert manager.load("tx-before-writer-touch").status == "promoting"
    assert json.loads((record.workspace / "metadata.json").read_text())[
        "writer_prepared"
    ] is True
    assert (config.vault / "log.md").read_text() == EMPTY_OPERATION_LOG

    TransactionManager(config).restore("tx-before-writer-touch")

    assert manager.load("tx-before-writer-touch").status == "restored"
    assert not (config.vault / "concepts/a.md").exists()


def test_a_only_transaction_cannot_update_existing_a_b_page(
    tmp_path: Path,
    log_writer,
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")
    shared = PAGE.replace("  - sources/a.md\n", "  - sources/a.md\n  - sources/b.md\n")
    target = config.vault / "concepts/shared.md"
    target.write_text(shared, encoding="utf-8")
    manifest = ShardedManifest(config)
    manifest.upsert(
        source_a,
        pages=["concepts/shared.md"],
        compiled_at="2026-08-07T00:00:00Z",
    )
    manifest.upsert(
        source_b,
        pages=["concepts/shared.md"],
        compiled_at="2026-08-07T00:00:00Z",
    )
    shard_a = manifest.entry_path("sources/a.md")
    shard_b = manifest.entry_path("sources/b.md")
    before_a = shard_a.read_bytes()
    before_b = shard_b.read_bytes()
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source_a], transaction_id="tx-1")
    candidate_page(
        record,
        "concepts/shared.md",
        PAGE.replace("# A", "# Updated without B"),
    )

    with pytest.raises(TransactionError, match="existing page.*sources/b.md"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == shared
    assert shard_a.read_bytes() == before_a
    assert shard_b.read_bytes() == before_b
    assert log_writer.calls == []
    assert manager.load("tx-1").status == "active"


def test_a_only_transaction_cannot_delete_existing_a_b_page(
    tmp_path: Path,
    log_writer,
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")
    shared = PAGE.replace("  - sources/a.md\n", "  - sources/a.md\n  - sources/b.md\n")
    target = config.vault / "concepts/shared.md"
    target.write_text(shared, encoding="utf-8")
    manifest = ShardedManifest(config)
    manifest.upsert(
        source_a,
        pages=["concepts/shared.md"],
        compiled_at="2026-08-07T00:00:00Z",
    )
    manifest.upsert(
        source_b,
        pages=["concepts/shared.md"],
        compiled_at="2026-08-07T00:00:00Z",
    )
    shard_a = manifest.entry_path("sources/a.md")
    shard_b = manifest.entry_path("sources/b.md")
    before_a = shard_a.read_bytes()
    before_b = shard_b.read_bytes()
    manager = TransactionManager(config, log_writer=log_writer(config))
    manager.begin([source_a], transaction_id="tx-1")
    manager.mark_delete("tx-1", "concepts/shared.md")

    with pytest.raises(TransactionError, match="existing page.*sources/b.md"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == shared
    assert shard_a.read_bytes() == before_a
    assert shard_b.read_bytes() == before_b
    assert log_writer.calls == []
    assert manager.load("tx-1").status == "active"


def test_a_b_transaction_can_remove_b_relationship_and_rebuild_both_shards(
    tmp_path: Path,
    log_writer,
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")
    shared = PAGE.replace("  - sources/a.md\n", "  - sources/a.md\n  - sources/b.md\n")
    target = config.vault / "concepts/shared.md"
    target.write_text(shared, encoding="utf-8")
    manifest = ShardedManifest(config)
    manifest.upsert(
        source_a,
        pages=["concepts/shared.md"],
        compiled_at="2026-08-07T00:00:00Z",
    )
    manifest.upsert(
        source_b,
        pages=["concepts/shared.md"],
        compiled_at="2026-08-07T00:00:00Z",
    )
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source_b, source_a], transaction_id="tx-1")
    updated = PAGE.replace("# A", "# Updated without B")
    candidate_page(record, "concepts/shared.md", updated)

    manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert target.read_text(encoding="utf-8") == updated
    assert manifest.load("sources/a.md").pages == ("concepts/shared.md",)
    assert manifest.load("sources/b.md").pages == ()


def test_restore_recovers_promoting_transaction_after_page_replace_crash(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    candidate_page(record, "concepts/b.md")
    original_promote = manager._promote_candidate
    crashed = False

    def crash_after_first(candidate, target: Path, expected_preimage) -> None:
        nonlocal crashed
        original_promote(candidate, target, expected_preimage)
        if not crashed:
            crashed = True
            raise SystemExit("simulated process crash after page replace")

    monkeypatch.setattr(manager, "_promote_candidate", crash_after_first)

    with pytest.raises(SystemExit, match="simulated process crash"):
        manager.commit("tx-1")

    recovering = TransactionManager(config, log_writer=log_writer(config))
    assert recovering.load("tx-1").status == "promoting"
    with pytest.raises(TransactionError, match="promoting.*restore"):
        recovering.abort("tx-1")

    recovering.restore("tx-1")

    assert not (config.vault / "concepts/a.md").exists()
    assert not (config.vault / "concepts/b.md").exists()
    assert ShardedManifest(config).load("sources/a.md") is None
    assert recovering.load("tx-1").status == "restored"
    assert not recovering.lock_path.exists()


def test_restore_refuses_while_same_transaction_commit_is_still_running(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    writer_entered = threading.Event()
    release_writer = threading.Event()
    commit_errors: list[TransactionError] = []

    def writer(change):
        writer_entered.set()
        if not release_writer.wait(timeout=5):
            raise RuntimeError("test timed out waiting to release operation writer")
        return append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    def commit() -> None:
        try:
            manager.commit("tx-1")
        except TransactionError as exc:
            commit_errors.append(exc)

    commit_thread = threading.Thread(target=commit)
    commit_thread.start()
    assert writer_entered.wait(timeout=5)
    recovering = TransactionManager(config, log_writer=writer)
    try:
        with pytest.raises(TransactionError, match="action.*progress|in progress"):
            recovering.restore("tx-1")
    finally:
        release_writer.set()
        commit_thread.join(timeout=5)

    assert not commit_thread.is_alive()
    assert commit_errors == []
    assert manager.load("tx-1").status == "complete"


def test_mark_delete_refuses_while_same_transaction_commit_is_running(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    writer_entered = threading.Event()
    release_writer = threading.Event()
    commit_errors: list[TransactionError] = []

    def writer(change):
        writer_entered.set()
        if not release_writer.wait(timeout=5):
            raise RuntimeError("test timed out waiting to release operation writer")
        return append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    def commit() -> None:
        try:
            manager.commit("tx-1")
        except TransactionError as exc:
            commit_errors.append(exc)

    commit_thread = threading.Thread(target=commit)
    commit_thread.start()
    assert writer_entered.wait(timeout=5)
    try:
        with pytest.raises(TransactionError, match="action.*progress|in progress"):
            TransactionManager(config).mark_delete("tx-1", "concepts/delete.md")
    finally:
        release_writer.set()
        commit_thread.join(timeout=5)

    assert not commit_thread.is_alive()
    assert commit_errors == []
    assert json.loads((record.workspace / "deletions.json").read_text()) == []
    assert manager.load("tx-1").status == "complete"


def test_restore_recovers_promoting_transaction_after_manifest_replace_crash(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([source_a, source_b], transaction_id="tx-1")
    shared = PAGE.replace("  - sources/a.md\n", "  - sources/a.md\n  - sources/b.md\n")
    candidate_page(record, "concepts/shared.md", shared)
    original_upsert = ShardedManifest.upsert
    calls = 0

    def crash_after_first(store, source, **kwargs):
        nonlocal calls
        result = original_upsert(store, source, **kwargs)
        calls += 1
        if calls == 1:
            raise SystemExit("simulated process crash after manifest replace")
        return result

    monkeypatch.setattr(ShardedManifest, "upsert", crash_after_first)

    with pytest.raises(SystemExit, match="simulated process crash"):
        manager.commit("tx-1")

    monkeypatch.setattr(ShardedManifest, "upsert", original_upsert)
    manifest = ShardedManifest(config)
    assert manifest.load("sources/a.md") is not None
    recovering = TransactionManager(config, log_writer=log_writer(config))
    assert recovering.load("tx-1").status == "promoting"

    recovering.restore("tx-1")

    assert not (config.vault / "concepts/shared.md").exists()
    assert manifest.load("sources/a.md") is None
    assert manifest.load("sources/b.md") is None
    assert recovering.load("tx-1").status == "restored"
    assert not recovering.lock_path.exists()


def test_persistent_failed_status_write_leaves_promoting_recoverable(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original_metadata = manager._write_metadata

    def fail_failed_status(workspace: Path, payload: dict[str, object]) -> None:
        if payload.get("status") == "failed":
            raise OSError("persistent metadata failure")
        original_metadata(workspace, payload)

    monkeypatch.setattr(manager, "_write_metadata", fail_failed_status)

    with pytest.raises(TransactionError, match="metadata failed"):
        manager.commit("tx-1")

    assert not manager.lock_path.exists()
    recovering = TransactionManager(config, log_writer=log_writer(config))
    assert recovering.load("tx-1").status == "promoting"
    recovering.restore("tx-1")
    assert not (config.vault / "concepts/a.md").exists()
    assert ShardedManifest(config).load("sources/a.md") is None
    assert recovering.load("tx-1").status == "restored"


@pytest.mark.parametrize(
    "relative",
    ["index.md", ".manifest.json", ".obsidian/workspace.json"],
)
def test_load_rejects_snapshot_targets_outside_transaction_affected_set(
    tmp_path: Path,
    relative: str,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    metadata = record.workspace / "metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["snapshot_index"] = {relative: None}
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TransactionError, match="snapshot.*(target|affected|status)"):
        manager.load("tx-1")


@pytest.mark.parametrize("kind", ["empty", "extra"])
def test_load_rejects_complete_postimages_not_exactly_affected(
    tmp_path: Path,
    log_writer,
    kind: str,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    manager.commit("tx-1")
    metadata = record.workspace / "metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    if kind == "empty":
        payload["postimages"] = {}
    else:
        payload["postimages"]["concepts/unaffected.md"] = None
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TransactionError, match="postimages.*affected"):
        manager.load("tx-1")


def test_load_rejects_status_cross_fields(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    metadata = record.workspace / "metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["status"] = "complete"
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        TransactionError, match="complete.*(fields|completed|operation|postimages)"
    ):
        manager.load("tx-1")


def test_load_rejects_symlinked_snapshot_backing_file(
    tmp_path: Path,
    log_writer,
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Updated"))
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")
    payload = json.loads((record.workspace / "metadata.json").read_text())
    snapshot = (
        record.workspace / "snapshots" / payload["snapshot_index"]["concepts/a.md"]
    )
    external = tmp_path / "external-snapshot"
    external.write_text(PAGE, encoding="utf-8")
    snapshot.unlink()
    snapshot.symlink_to(external)

    with pytest.raises(TransactionError, match="snapshot|symlink|single-link"):
        manager.load("tx-1")


@pytest.mark.parametrize("kind", ["different-bytes", "symlink"])
def test_candidate_interposition_cannot_change_promoted_bytes(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate = candidate_page(record, "concepts/a.md")
    original_bytes = candidate.read_bytes()
    external = tmp_path / "swapped.md"
    external.write_text(PAGE.replace("# A", "# Swapped"), encoding="utf-8")
    original_promote = manager._promote_candidate

    def interpose(candidate_value, target: Path, expected_preimage) -> None:
        path = getattr(candidate_value, "path", candidate_value)
        path.unlink()
        if kind == "symlink":
            path.symlink_to(external)
        else:
            path.write_bytes(external.read_bytes())
        try:
            original_promote(candidate_value, target, expected_preimage)
        finally:
            path.unlink()
            path.write_bytes(original_bytes)

    monkeypatch.setattr(manager, "_promote_candidate", interpose)

    manager.commit("tx-1")

    assert (config.vault / "concepts/a.md").read_bytes() == original_bytes
    assert manager.load("tx-1").status == "complete"


def test_page_change_after_snapshot_is_rejected_at_promotion_boundary(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    concurrent = PAGE.replace("# A", "# Concurrent")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Candidate"))
    original_promote = manager._promote_candidate

    def interpose(candidate_value, target_value: Path, *args) -> None:
        target_value.write_text(concurrent, encoding="utf-8")
        original_promote(candidate_value, target_value, *args)

    monkeypatch.setattr(manager, "_promote_candidate", interpose)

    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == concurrent
    assert manager.load("tx-1").status == "failed"


def test_delete_change_after_snapshot_is_rejected_at_unlink_boundary(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    concurrent = PAGE.replace("# A", "# Concurrent")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    manager.mark_delete(record.transaction_id, "concepts/a.md")
    original_delete = manager._delete_vault_target

    def interpose(relative: str, expected_preimage) -> None:
        target.write_text(concurrent, encoding="utf-8")
        original_delete(relative, expected_preimage)

    monkeypatch.setattr(manager, "_delete_vault_target", interpose)

    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == concurrent
    assert manager.load("tx-1").status == "failed"


def test_manifest_change_after_snapshot_is_rejected_at_upsert_boundary(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manifest = ShardedManifest(config)
    manifest.upsert(source, compiled_at="2026-08-07T00:00:00Z")
    shard = manifest.entry_path("sources/a.md")
    concurrent = b'{"later":"concurrent work"}\n'
    manager = TransactionManager(config, log_writer=log_writer(config))
    manager.begin([source], transaction_id="tx-1")
    original_upsert = ShardedManifest.upsert

    def interpose(store, source_value, **kwargs):
        shard.write_bytes(concurrent)
        return original_upsert(store, source_value, **kwargs)

    monkeypatch.setattr(ShardedManifest, "upsert", interpose)

    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.commit("tx-1")

    assert shard.read_bytes() == concurrent
    assert manager.load("tx-1").status == "failed"


def _prepare_failed_page_conflict(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
    *,
    concurrent_heading: str,
):
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    concurrent = PAGE.replace("# A", concurrent_heading)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Candidate"))
    original_promote = manager._promote_candidate

    def interpose(candidate_value, target_value: Path, *args) -> None:
        target_value.write_text(concurrent, encoding="utf-8")
        original_promote(candidate_value, target_value, *args)

    monkeypatch.setattr(manager, "_promote_candidate", interpose)
    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.commit("tx-1")
    monkeypatch.setattr(manager, "_promote_candidate", original_promote)
    return manager, record, target, concurrent


def test_failed_restore_preserves_persisted_mutation_conflict(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, record, target, concurrent = _prepare_failed_page_conflict(
        tmp_path,
        log_writer,
        monkeypatch,
        concurrent_heading="# Concurrent",
    )

    payload = json.loads((record.workspace / "metadata.json").read_text())
    assert payload.get("rollback_exclusions") == {
        "concepts/a.md": manager._current_vault_hash("concepts/a.md")
    }

    manager.restore("tx-1")

    assert target.read_text(encoding="utf-8") == concurrent
    assert manager.load("tx-1").status == "restored"


@pytest.mark.parametrize("action", ["retry", "restore"])
def test_failed_recovery_rejects_drift_at_persisted_mutation_conflict(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    manager, record, target, _ = _prepare_failed_page_conflict(
        tmp_path,
        log_writer,
        monkeypatch,
        concurrent_heading="# First concurrent",
    )

    later = PAGE.replace("# A", "# Second concurrent")
    target.write_text(later, encoding="utf-8")
    metadata = record.workspace / "metadata.json"
    metadata_before = metadata.read_bytes()

    with pytest.raises(TransactionError, match="rollback exclusion.*changed"):
        getattr(manager, action)("tx-1")

    assert target.read_text(encoding="utf-8") == later
    assert metadata.read_bytes() == metadata_before
    assert manager.load("tx-1").status == "failed"


def test_failed_restore_rejects_deleted_rollback_exclusion(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, record, target, concurrent = _prepare_failed_page_conflict(
        tmp_path,
        log_writer,
        monkeypatch,
        concurrent_heading="# Concurrent",
    )
    metadata = record.workspace / "metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["rollback_exclusions"] = {}
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metadata_before = metadata.read_bytes()

    with pytest.raises(TransactionError, match="residual.*changed"):
        manager.restore("tx-1")

    assert target.read_text(encoding="utf-8") == concurrent
    assert metadata.read_bytes() == metadata_before
    assert manager.load("tx-1").status == "failed"


@pytest.mark.parametrize("target_kind", ["page", "manifest-marker"])
def test_log_writer_vault_side_effect_is_rolled_back(
    tmp_path: Path,
    target_kind: str,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    if target_kind == "page":
        unrelated = config.vault / "references/unrelated.md"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text(PAGE, encoding="utf-8")
    else:
        unrelated = config.vault / ".manifest.json"
    original = unrelated.read_bytes()

    def writer(change):
        operation = append_operation(
            config.vault / "log.md", change, root=config.vault,
            lock_path=config.root.parent / ".operation-log.lock",
        )
        unrelated.write_text("unauthorized writer side effect\n", encoding="utf-8")
        return operation

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*unauthorized"):
        manager.commit("tx-1")

    assert unrelated.read_bytes() == original
    assert not (config.vault / "concepts/a.md").exists()
    assert (config.vault / "log.md").read_text(encoding="utf-8") == EMPTY_OPERATION_LOG


@pytest.mark.parametrize("target_kind", ["hot", "obsidian"])
def test_log_writer_may_refresh_writer_guard_exclusions(
    tmp_path: Path,
    target_kind: str,
) -> None:
    root, config = make_config(tmp_path)
    if target_kind == "hot":
        target = config.vault / "hot.md"
    else:
        target = config.vault / ".obsidian/workspace.json"
        target.parent.mkdir()
    target.write_text("before\n", encoding="utf-8")

    def writer(change):
        operation = append_operation(
            config.vault / "log.md", change, root=config.vault,
            lock_path=config.root.parent / ".operation-log.lock",
        )
        target.write_text("refreshed\n", encoding="utf-8")
        return operation

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    result = manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert result.transaction_id == "tx-1"
    assert target.read_text(encoding="utf-8") == "refreshed\n"
    assert (config.vault / "concepts/a.md").read_text(encoding="utf-8") == PAGE


def test_log_writer_nested_hot_page_side_effect_is_rolled_back(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    nested_hot = config.vault / "concepts/hot.md"
    nested_hot.write_text(PAGE, encoding="utf-8")
    original = nested_hot.read_bytes()

    def writer(change):
        operation = append_operation(
            config.vault / "log.md", change, root=config.vault,
            lock_path=config.root.parent / ".operation-log.lock",
        )
        nested_hot.write_text("unauthorized writer side effect\n", encoding="utf-8")
        return operation

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*unauthorized"):
        manager.commit("tx-1")

    assert nested_hot.read_bytes() == original
    assert not (config.vault / "concepts/a.md").exists()


def test_writer_guard_prunes_root_exclusions_before_metadata_or_content_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    root_hot = config.vault / "hot.md"
    root_hot.write_text("derived\n", encoding="utf-8")
    editor_state = config.vault / ".obsidian"
    editor_state.mkdir()
    (editor_state / "workspace.json").write_text("personal\n", encoding="utf-8")
    nested_hot = config.vault / "concepts/hot.md"
    nested_hot.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config)
    original_lstat = Path.lstat
    original_hash = manager._hash_single_link_file

    def guarded_lstat(path: Path):
        if path == root_hot or path == editor_state or editor_state in path.parents:
            raise AssertionError(f"writer guard inspected excluded path: {path}")
        return original_lstat(path)

    hashed: list[str] = []

    def tracked_hash(path: Path, label: str) -> str:
        hashed.append(path.relative_to(config.vault).as_posix())
        return original_hash(path, label)

    monkeypatch.setattr(Path, "lstat", guarded_lstat)
    monkeypatch.setattr(manager, "_hash_single_link_file", tracked_hash)

    state = manager._writer_guard_state()

    assert "hot.md" not in state
    assert all(not relative.startswith(".obsidian") for relative in state)
    assert "concepts/hot.md" in state
    assert "concepts/hot.md" in hashed


def test_log_writer_empty_directory_side_effect_is_rolled_back(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    side_effect = config.vault / "references/writer-empty"

    def writer(change):
        result = append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        side_effect.mkdir(parents=True)
        return result

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*side effect"):
        manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert not side_effect.exists()
    assert (config.vault / "log.md").read_text(encoding="utf-8") == EMPTY_OPERATION_LOG


@pytest.mark.skipif(os.name != "posix", reason="exact chmod modes require POSIX")
def test_log_writer_chmod_side_effect_is_rolled_back(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    unrelated = config.vault / "references/unrelated.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text(PAGE, encoding="utf-8")
    unrelated.chmod(0o640)

    def writer(change):
        result = append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        unrelated.chmod(0o600)
        return result

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*side effect"):
        manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert stat.S_IMODE(unrelated.stat().st_mode) == 0o640
    assert unrelated.read_text(encoding="utf-8") == PAGE


@pytest.mark.skipif(os.name != "posix", reason="exact chmod modes require POSIX")
def test_log_writer_vault_root_chmod_is_rolled_back(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    config.vault.chmod(0o750)
    original_log = (config.vault / "log.md").read_bytes()
    original_manifest = (config.vault / ".manifest.json").read_bytes()

    def writer(change):
        result = append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        config.vault.chmod(0o700)
        return result

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-root-mode")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*side effect"):
        manager.commit("tx-root-mode", completed_at="2026-08-07T01:00:00Z")

    assert stat.S_IMODE(config.vault.stat().st_mode) == 0o750
    assert (config.vault / "log.md").read_bytes() == original_log
    assert (config.vault / ".manifest.json").read_bytes() == original_manifest
    assert not (config.vault / "concepts/a.md").exists()
    assert ShardedManifest(config).load("sources/a.md") is None


def test_log_writer_vault_root_substitution_preserves_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, config = make_config(tmp_path)
    original_vault = root / "original-wiki"
    replacement_state = None

    def tree_state(directory: Path):
        return [
            (
                path.relative_to(directory).as_posix(),
                stat.S_IMODE(path.lstat().st_mode),
                path.read_bytes() if path.is_file() else None,
            )
            for path in [directory, *sorted(directory.rglob("*"))]
        ]

    def writer(change):
        nonlocal replacement_state
        config.vault.rename(original_vault)
        config.vault.mkdir(mode=0o751)
        (config.vault / "owner").mkdir(mode=0o750)
        keep = config.vault / "owner" / "keep.txt"
        keep.write_bytes(b"owner replacement\n")
        keep.chmod(0o640)
        replacement_state = tree_state(config.vault)
        raise RuntimeError("writer failed after replacing vault root")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-root-replaced")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError) as failure:
        manager.commit("tx-root-replaced", completed_at="2026-08-07T01:00:00Z")

    assert replacement_state is not None
    assert tree_state(config.vault) == replacement_state
    assert "vault root" in str(failure.value)
    assert "replaced" in str(failure.value)
    assert "manual recovery" in str(failure.value)
    assert (original_vault / "concepts" / "a.md").read_text(encoding="utf-8") == PAGE
    assert (original_vault / "log.md").read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert record.workspace.is_dir()
    metadata = json.loads(
        (record.workspace / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["status"] == "failed"
    assert metadata["residual_postimages"] is None
    assert (
        record.workspace / "snapshots" / "originals" / "log.md"
    ).read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert not manager.lock_path.exists()
    assert manager.load("tx-root-replaced").status == "failed"
    assert [item.transaction_id for item in manager.list_transactions()] == [
        "tx-root-replaced"
    ]

    monkeypatch.setattr(cli_module, "_transaction_manager", lambda: manager)
    assert cli_module.cmd_transaction_list(
        argparse.Namespace(json=True, pretty=False)
    ) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["transaction_id"] == "tx-root-replaced"
    assert listed[0]["status"] == "failed"
    assert listed[0]["recommended_action"]["command"] == (
        "llmwikiops transaction retry tx-root-replaced"
    )
    assert tree_state(config.vault) == replacement_state


def test_transaction_metadata_rejects_multiple_source_roots(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    other_sources = root / "other-sources"
    other_sources.mkdir()
    manager = TransactionManager(
        replace(config, sources=(config.sources[0], other_sources))
    )

    with pytest.raises(TransactionError, match="exactly one source root"):
        manager._load_source_ids(["sources/a.md"])


def test_transaction_metadata_rejects_nul_source_id(tmp_path: Path) -> None:
    _root, config = make_config(tmp_path)
    manager = TransactionManager(config)

    with pytest.raises(TransactionError, match="Source ID.*unsafe"):
        manager._load_source_ids(["sources/a\0b.md"])


def test_transaction_manifest_shard_matches_nested_unicode_mapping(
    tmp_path: Path,
) -> None:
    _root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    source_id = "sources/资料/组会.md"

    assert manager._manifest_shard_relative(source_id) == (
        ShardedManifest(config).entry_path(source_id)
        .relative_to(config.vault)
        .as_posix()
    )


@pytest.mark.parametrize(
    "source_id",
    [
        "/sources/a.md",
        "C:/sources/a.md",
        "sources\\a.md",
        "sources/../a.md",
        "sources/./a.md",
        "sources//a.md",
        "sources",
        "outside/a.md",
    ],
)
def test_transaction_manifest_shard_rejects_unsafe_source_ids_like_manifest(
    tmp_path: Path,
    source_id: str,
) -> None:
    _root, config = make_config(tmp_path)
    manager = TransactionManager(config)

    with pytest.raises(ValueError):
        ShardedManifest(config).entry_path(source_id)
    with pytest.raises(TransactionError):
        manager._manifest_shard_relative(source_id)


def test_log_writer_nested_nonempty_directory_side_effect_is_rolled_back(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    side_effect = config.vault / "writer-tree/nested"

    def writer(change):
        result = append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        side_effect.mkdir(parents=True)
        (side_effect / "artifact.txt").write_text("writer data\n", encoding="utf-8")
        return result

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*side effect"):
        manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert not (config.vault / "writer-tree").exists()


@pytest.mark.skipif(os.name != "posix", reason="exact chmod modes require POSIX")
@pytest.mark.parametrize("replace", [False, True], ids=["removed", "replaced"])
def test_log_writer_restores_preexisting_empty_directory(
    tmp_path: Path,
    replace: bool,
) -> None:
    root, config = make_config(tmp_path)
    directory = config.vault / "references/owner-empty"
    directory.mkdir(parents=True)
    directory.chmod(0o750)

    def writer(change):
        result = append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        directory.rmdir()
        if replace:
            directory.write_text("replacement\n", encoding="utf-8")
        return result

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*side effect"):
        manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o750
    assert list(directory.iterdir()) == []


def test_restore_uses_persisted_writer_guard_after_writer_crash(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    modified = config.vault / "references/modified.md"
    removed = config.vault / "references/removed.md"
    modified.parent.mkdir(parents=True)
    modified.write_text(PAGE, encoding="utf-8")
    removed.write_text(PAGE.replace("# A", "# Removed"), encoding="utf-8")
    original_modified = modified.read_bytes()
    original_removed = removed.read_bytes()
    extra = config.vault / "references/extra.md"
    operation = config.vault / "log.md"

    def writer(change):
        append_operation(operation, change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        modified.write_text("writer corruption\n", encoding="utf-8")
        removed.unlink()
        extra.write_text("writer addition\n", encoding="utf-8")
        raise SystemExit("simulated writer crash")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(SystemExit, match="simulated writer crash"):
        manager.commit("tx-1")

    payload = json.loads((record.workspace / "metadata.json").read_text())
    assert payload["status"] == "promoting"
    assert payload["writer_prepared"] is True
    assert payload["writer_guard"]["references/modified.md"].startswith("sha256:")
    assert payload["writer_guard"]["references/removed.md"].startswith("sha256:")
    for relative in ("references/modified.md", "references/removed.md"):
        backing = payload["snapshot_index"][relative]
        assert backing is not None
        assert (record.workspace / "snapshots" / backing).is_file()

    recovering = TransactionManager(config, log_writer=writer)
    with pytest.raises(TransactionError, match="manual intervention"):
        recovering.restore("tx-1")

    assert modified.read_text() == "writer corruption\n"
    assert not removed.exists()
    assert extra.exists()
    assert (config.vault / "concepts/a.md").exists()
    assert recovering.load("tx-1").status == "promoting"


def test_restore_retries_persisted_writer_guard_after_cleanup_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    unrelated = config.vault / "references/unrelated.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text(PAGE, encoding="utf-8")
    original = unrelated.read_bytes()
    operation = config.vault / "log.md"

    def writer(change):
        append_operation(operation, change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        unrelated.write_text("writer corruption\n", encoding="utf-8")
        raise OSError("writer failed")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original_restore = manager._restore_snapshot_index
    cleanup_started = False

    def crash_during_cleanup(record_value, index):
        nonlocal cleanup_started
        if not cleanup_started and index:
            cleanup_started = True
            relative = min(index)
            original_restore(record_value, {relative: index[relative]})
            raise SystemExit("simulated cleanup crash")
        original_restore(record_value, index)

    monkeypatch.setattr(manager, "_restore_snapshot_index", crash_during_cleanup)

    with pytest.raises(SystemExit, match="simulated cleanup crash"):
        manager.commit("tx-1")
    assert cleanup_started

    recovering = TransactionManager(config, log_writer=writer)
    with pytest.raises(TransactionError, match="manual intervention"):
        recovering.restore("tx-1")
    assert recovering.load("tx-1").status == "promoting"


def test_load_rejects_persisted_writer_guard_outside_authoritative_vault(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)

    def writer(_change):
        raise SystemExit("simulated writer crash")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(SystemExit, match="simulated writer crash"):
        manager.commit("tx-1")

    metadata = record.workspace / "metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    relative = ".obsidian/workspace.json"
    payload["writer_guard"][relative] = "sha256:" + "0" * 64
    payload["snapshot_index"][relative] = None
    payload["writer_guard"] = dict(sorted(payload["writer_guard"].items()))
    payload["snapshot_index"] = dict(sorted(payload["snapshot_index"].items()))
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TransactionError, match="writer guard|authoritative|affected"):
        manager.load("tx-1")


def test_writer_guard_preserves_unrelated_changes_made_after_begin(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    removed = config.vault / "references/removed-before-writer.md"
    removed.parent.mkdir(parents=True)
    removed.write_text(PAGE, encoding="utf-8")

    def writer(_change):
        raise SystemExit("simulated writer crash")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    removed.unlink()
    added = config.vault / "references/added-before-writer.md"
    added.write_text(PAGE.replace("# A", "# Added"), encoding="utf-8")
    added_bytes = added.read_bytes()

    with pytest.raises(SystemExit, match="simulated writer crash"):
        manager.commit("tx-1")

    recovering = TransactionManager(config, log_writer=writer)
    assert recovering.load("tx-1").status == "promoting"
    recovering.restore("tx-1")

    assert added.read_bytes() == added_bytes
    assert not removed.exists()
    assert not (config.vault / "concepts/a.md").exists()
    assert ShardedManifest(config).load("sources/a.md") is None


def test_retry_cleans_persisted_writer_additions_before_new_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    extra = config.vault / "references/writer-extra.md"
    calls = 0

    def writer(change):
        nonlocal calls
        calls += 1
        operation = append_operation(
            config.vault / "log.md", change, root=config.vault,
            lock_path=config.root.parent / ".operation-log.lock",
        )
        if calls == 1:
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("writer addition\n", encoding="utf-8")
            raise OSError("first writer failed")
        return operation

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original_restore = manager._restore_snapshot_index
    cleanup_failed = False

    def fail_writer_cleanup(record_value, index):
        nonlocal cleanup_failed
        if not cleanup_failed and "references/writer-extra.md" in index:
            cleanup_failed = True
            raise TransactionError("simulated writer cleanup failure")
        original_restore(record_value, index)

    monkeypatch.setattr(manager, "_restore_snapshot_index", fail_writer_cleanup)
    with pytest.raises(TransactionError, match="writer restore failed"):
        manager.commit("tx-1")
    assert cleanup_failed
    assert extra.exists()
    assert manager.load("tx-1").status == "failed"

    monkeypatch.setattr(manager, "_restore_snapshot_index", original_restore)
    manager.retry("tx-1")

    assert not extra.exists()
    assert manager.load("tx-1").status == "complete"


def test_retry_preserves_failed_writer_residual_modified_after_lock_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _, extra = _prepare_failed_writer_residual(tmp_path, monkeypatch)

    later = "later legitimate work\n"
    extra.write_text(later, encoding="utf-8")

    with pytest.raises(TransactionError, match="residual.*changed"):
        manager.retry("tx-1")

    assert extra.read_text(encoding="utf-8") == later
    assert manager.load("tx-1").status == "failed"


def _prepare_failed_writer_residual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, config = make_config(tmp_path)
    extra = config.vault / "references/writer-extra.md"

    def writer(change):
        append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("writer addition\n", encoding="utf-8")
        raise OSError("writer failed")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original_restore = manager._restore_snapshot_index
    cleanup_failed = False

    def fail_writer_cleanup(record_value, index):
        nonlocal cleanup_failed
        if not cleanup_failed and "references/writer-extra.md" in index:
            cleanup_failed = True
            raise TransactionError("simulated writer cleanup failure")
        original_restore(record_value, index)

    monkeypatch.setattr(manager, "_restore_snapshot_index", fail_writer_cleanup)
    with pytest.raises(TransactionError, match="writer restore failed"):
        manager.commit("tx-1")
    assert cleanup_failed
    monkeypatch.setattr(manager, "_restore_snapshot_index", original_restore)
    assert manager.load("tx-1").status == "failed"
    return manager, record, extra


@pytest.mark.parametrize("action", ["retry", "restore"])
def test_failed_recovery_preflight_preserves_unrecorded_base_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    manager, record, extra = _prepare_failed_writer_residual(tmp_path, monkeypatch)
    target = manager.config.vault / "concepts/a.md"
    later = "later base work\n"
    target.write_text(later, encoding="utf-8")
    extra_before = extra.read_bytes()
    metadata = record.workspace / "metadata.json"
    metadata_before = metadata.read_bytes()

    with pytest.raises(TransactionError, match="residual.*changed"):
        getattr(manager, action)("tx-1")

    assert target.read_text(encoding="utf-8") == later
    assert extra.read_bytes() == extra_before
    assert metadata.read_bytes() == metadata_before


def test_retry_rejects_deleted_residual_metadata_key_without_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, record, extra = _prepare_failed_writer_residual(tmp_path, monkeypatch)
    metadata = record.workspace / "metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["residual_postimages"] = {}
    metadata.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    later = "later legitimate work\n"
    extra.write_text(later, encoding="utf-8")
    metadata_before = metadata.read_bytes()

    with pytest.raises(TransactionError, match="residual.*changed"):
        manager.retry("tx-1")

    assert extra.read_text(encoding="utf-8") == later
    assert metadata.read_bytes() == metadata_before


@pytest.mark.parametrize("action", ["retry", "restore"])
def test_failed_recovery_resumes_after_partial_residual_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    root, config = make_config(tmp_path)
    extras = [
        config.vault / "references/writer-a.md",
        config.vault / "references/writer-b.md",
    ]
    calls = 0

    def writer(change):
        nonlocal calls
        calls += 1
        operation = append_operation(
            config.vault / "log.md", change, root=config.vault,
            lock_path=config.root.parent / ".operation-log.lock",
        )
        if calls == 1:
            for extra in extras:
                extra.parent.mkdir(parents=True, exist_ok=True)
                extra.write_text(f"residual {extra.name}\n", encoding="utf-8")
            raise OSError("writer failed")
        return operation

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original_restore = manager._restore_snapshot_index
    failed_initial_cleanup = False

    def fail_initial_cleanup(record_value, index):
        nonlocal failed_initial_cleanup
        if not failed_initial_cleanup and all(
            extra.relative_to(config.vault).as_posix() in index for extra in extras
        ):
            failed_initial_cleanup = True
            raise TransactionError("initial cleanup failed")
        original_restore(record_value, index)

    monkeypatch.setattr(manager, "_restore_snapshot_index", fail_initial_cleanup)
    with pytest.raises(TransactionError, match="writer restore failed"):
        manager.commit("tx-1")
    assert failed_initial_cleanup
    monkeypatch.setattr(manager, "_restore_snapshot_index", original_restore)
    metadata = record.workspace / "metadata.json"
    metadata_before = metadata.read_bytes()
    interrupted = False

    def interrupt_after_first_cleanup(record_value, index):
        nonlocal interrupted
        if (
            not interrupted
            and len(
                set(index)
                & {extra.relative_to(config.vault).as_posix() for extra in extras}
            )
            == 2
        ):
            interrupted = True
            first = extras[0].relative_to(config.vault).as_posix()
            original_restore(record_value, {first: index[first]})
            raise TransactionError("transient cleanup failure")
        original_restore(record_value, index)

    monkeypatch.setattr(
        manager, "_restore_snapshot_index", interrupt_after_first_cleanup
    )
    with pytest.raises(TransactionError, match="transient cleanup failure"):
        getattr(manager, action)("tx-1")

    assert interrupted
    assert not extras[0].exists()
    assert extras[1].exists()
    assert metadata.read_bytes() == metadata_before
    monkeypatch.setattr(manager, "_restore_snapshot_index", original_restore)

    getattr(manager, action)("tx-1")

    assert not extras[0].exists()
    assert not extras[1].exists()
    expected_status = "complete" if action == "retry" else "restored"
    assert manager.load("tx-1").status == expected_status


def test_failed_restore_recovers_recorded_partial_base_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")

    def writer(_change):
        raise OSError("writer failed")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate = PAGE.replace("# A", "# Candidate")
    candidate_page(record, "concepts/a.md", candidate)
    original_restore = manager._restore_snapshot_index
    interrupted = False

    def leave_page_unrestored(record_value, index):
        nonlocal interrupted
        if not interrupted and "concepts/a.md" in index:
            interrupted = True
            original_restore(
                record_value,
                {
                    relative: stored
                    for relative, stored in index.items()
                    if relative != "concepts/a.md"
                },
            )
            raise TransactionError("partial base rollback")
        original_restore(record_value, index)

    monkeypatch.setattr(manager, "_restore_snapshot_index", leave_page_unrestored)
    with pytest.raises(TransactionError, match="restore failed.*partial base rollback"):
        manager.commit("tx-1")
    assert interrupted
    assert target.read_text(encoding="utf-8") == candidate
    payload = json.loads((record.workspace / "metadata.json").read_text())
    assert payload["residual_postimages"].get("concepts/a.md") == (
        manager._current_vault_hash("concepts/a.md")
    )
    monkeypatch.setattr(manager, "_restore_snapshot_index", original_restore)

    manager.restore("tx-1")

    assert target.read_text(encoding="utf-8") == PAGE
    assert manager.load("tx-1").status == "restored"


def test_writer_added_symlink_is_identity_checked_and_rolled_back(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    unsafe = config.vault / "references" / "unsafe"
    unsafe.parent.mkdir(parents=True)
    require_symlink_support(unsafe, external, target_is_directory=True)

    def writer(change):
        append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        unsafe.parent.mkdir(parents=True, exist_ok=True)
        unsafe.symlink_to(external, target_is_directory=True)
        raise OSError("writer failed")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*writer failed"):
        manager.commit("tx-1")

    payload = json.loads((record.workspace / "metadata.json").read_text())
    assert payload["status"] == "failed"
    assert payload["residual_postimages"] == {}
    assert not manager.lock_path.exists()
    assert not unsafe.exists() and not unsafe.is_symlink()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_writer_addition_substitution_is_preserved_during_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    root, config = make_config(tmp_path)
    addition = config.vault / "references/writer-added"
    displaced = tmp_path / "displaced-writer-object"
    original_target = tmp_path / "writer-target"
    owner_target = tmp_path / "owner-target"
    original_target.write_text("writer target\n", encoding="utf-8")
    owner_target.write_text("owner target\n", encoding="utf-8")
    if kind == "symlink":
        addition.parent.mkdir(parents=True)
        require_symlink_support(addition, original_target)

    def writer(change):
        append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        addition.parent.mkdir(parents=True, exist_ok=True)
        if kind == "file":
            addition.write_text("writer file\n", encoding="utf-8")
        else:
            addition.symlink_to(original_target)
        raise OSError("writer failed")

    original_replace = os.replace
    substituted = False

    def substitute_before_rename(source, destination, *args, **kwargs):
        nonlocal substituted
        targets_addition = Path(source) == addition or (
            source == addition.name and kwargs.get("src_dir_fd") is not None
        )
        if targets_addition and not substituted:
            substituted = True
            if kwargs.get("src_dir_fd") is None:
                original_replace(source, displaced)
            else:
                original_replace(
                    source, displaced, src_dir_fd=kwargs["src_dir_fd"]
                )
            if kind == "file":
                addition.write_text("owner replacement\n", encoding="utf-8")
            else:
                addition.symlink_to(owner_target)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", substitute_before_rename)
    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="writer restore failed.*preserved"):
        manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert substituted
    assert not addition.exists() and not addition.is_symlink()
    quarantined = list((record.workspace / "quarantine").iterdir())
    if kind == "file":
        assert any(
            path.is_file()
            and path.read_text(encoding="utf-8") == "owner replacement\n"
            for path in quarantined
        )
    else:
        assert any(
            path.is_symlink() and path.readlink() == owner_target
            for path in quarantined
        )


def test_writer_added_directory_substitution_is_preserved_during_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    addition = config.vault / "references/writer-directory"
    displaced = tmp_path / "displaced-writer-directory"
    original_replace = os.replace
    substituted = False

    def writer(change):
        result = append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        addition.mkdir(parents=True)
        return result

    def substitute_before_rename(source, destination, *args, **kwargs):
        nonlocal substituted
        targets_addition = Path(source) == addition or (
            source == addition.name and kwargs.get("src_dir_fd") is not None
        )
        if targets_addition and not substituted:
            substituted = True
            if kwargs.get("src_dir_fd") is None:
                original_replace(source, displaced)
            else:
                original_replace(
                    source, displaced, src_dir_fd=kwargs["src_dir_fd"]
                )
            addition.mkdir()
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", substitute_before_rename)
    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="writer restore failed.*preserved"):
        manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert substituted
    assert not addition.exists()
    assert any(
        path.is_dir() for path in (record.workspace / "quarantine").iterdir()
    )


def test_preexisting_directory_replacement_substitution_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    directory = config.vault / "references/owner-directory"
    directory.mkdir(parents=True)
    displaced = tmp_path / "displaced-writer-replacement"
    original_replace = os.replace
    substituted = False

    def writer(change):
        result = append_operation(config.vault / "log.md", change, root=config.vault, lock_path=config.root.parent / ".operation-log.lock")
        directory.rmdir()
        directory.write_text("writer replacement\n", encoding="utf-8")
        return result

    def substitute_before_rename(source, destination, *args, **kwargs):
        nonlocal substituted
        targets_directory = Path(source) == directory or (
            source == directory.name and kwargs.get("src_dir_fd") is not None
        )
        if targets_directory and not substituted:
            substituted = True
            if kwargs.get("src_dir_fd") is None:
                original_replace(source, displaced)
            else:
                original_replace(
                    source, displaced, src_dir_fd=kwargs["src_dir_fd"]
                )
            directory.write_text("owner replacement\n", encoding="utf-8")
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", substitute_before_rename)
    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="writer restore failed.*preserved"):
        manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert substituted
    assert not directory.exists()
    assert any(
        path.is_file()
        and path.read_text(encoding="utf-8") == "owner replacement\n"
        for path in (record.workspace / "quarantine").iterdir()
    )


@pytest.mark.parametrize("snapshot_kind", ["base", "writer", "log"])
def test_load_rejects_snapshot_backing_with_wrong_content_hash(
    tmp_path: Path,
    snapshot_kind: str,
) -> None:
    root, config = make_config(tmp_path)
    if snapshot_kind == "base":
        target = config.vault / "concepts/a.md"
        target.write_text(PAGE, encoding="utf-8")
    elif snapshot_kind == "writer":
        target = config.vault / "references/unrelated.md"
        target.parent.mkdir(parents=True)
        target.write_text(PAGE, encoding="utf-8")
    else:
        target = config.vault / "log.md"

    def writer(_change):
        raise SystemExit("simulated writer crash")

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    if snapshot_kind == "base":
        candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Updated"))
    else:
        candidate_page(record, "concepts/a.md")
    with pytest.raises(SystemExit, match="simulated writer crash"):
        manager.commit("tx-1")

    relative = target.relative_to(config.vault).as_posix()
    payload = json.loads((record.workspace / "metadata.json").read_text())
    backing = payload["snapshot_index"][relative]
    assert backing is not None
    (record.workspace / "snapshots" / backing).write_text(
        "tampered snapshot backing\n", encoding="utf-8"
    )

    with pytest.raises(TransactionError, match="snapshot.*hash|backing.*hash"):
        manager.load("tx-1")


def test_snapshot_rejects_target_changed_after_preimage_verification(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    interposed = PAGE.replace("# A", "# Interposed")
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Updated"))
    original_verify = manager._verify_preimages

    def verify_then_interpose(record_value, affected):
        original_verify(record_value, affected)
        target.write_text(interposed, encoding="utf-8")

    monkeypatch.setattr(manager, "_verify_preimages", verify_then_interpose)

    with pytest.raises(TransactionError, match="changed.*began|preimage"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == interposed


def test_transaction_cli_complete_lifecycle_and_git_is_read_only(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    obsolete = config.vault / "concepts/obsolete.md"
    obsolete.write_text(PAGE.replace("title: A", "title: Obsolete"), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
    before_head = git_output(root, "rev-parse", "HEAD")
    before_remotes = git_output(root, "remote", "-v")
    home = tmp_path / "home"

    begun = run_cli(
        home,
        root,
        "transaction",
        "begin",
        "--source",
        "sources/a.md",
        "--json",
    )
    assert begun.returncode == 0, begun.stderr
    begin_payload = json.loads(begun.stdout)
    assert set(begin_payload) == {
        "transaction_id",
        "status",
        "started_at",
        "source_ids",
        "workspace",
        "candidate_vault",
        "snapshots",
        "deletions",
    }
    transaction_id = begin_payload["transaction_id"]
    candidate_vault = Path(begin_payload["candidate_vault"])
    assert candidate_vault.is_dir()

    listed = run_cli(home, root, "transaction", "list", "--json")
    assert listed.returncode == 0, listed.stderr
    listed_payload = json.loads(listed.stdout)
    assert isinstance(listed_payload, list)
    assert set(listed_payload[0]) == {
        *begin_payload,
        "recommended_action",
        "allowed_actions",
    }
    assert listed_payload[0]["status"] == "active"
    assert listed_payload[0]["workspace"]
    assert listed_payload[0]["recommended_action"] == {
        "command": f"llmwikiops transaction commit {transaction_id}",
        "reason": "commit after fixing the original cause and reviewing the candidate",
        "requires": [
            "the original failure cause is removed",
            "the candidate vault has been reviewed",
        ],
    }
    assert listed_payload[0]["allowed_actions"] == [
        listed_payload[0]["recommended_action"],
        {
            "command": f"llmwikiops transaction abort {transaction_id}",
            "reason": "abandon the active staged work",
            "requires": ["the candidate is no longer needed"],
        },
    ]
    assert run_cli(home, root, "transaction", "list", "--json").stdout == listed.stdout

    human_listed = run_cli(home, root, "transaction", "list")
    assert human_listed.returncode == 0, human_listed.stderr
    assert human_listed.stdout == (
        f"{transaction_id}\tactive\t"
        f"llmwikiops transaction commit {transaction_id}\t"
        f"{candidate_vault.parent}\n"
    )

    deleted = run_cli(
        home,
        root,
        "transaction",
        "delete",
        transaction_id,
        "concepts/obsolete.md",
    )
    assert deleted.returncode == 0, deleted.stderr
    candidate = candidate_vault / "concepts/a.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(PAGE, encoding="utf-8")

    committed = run_cli(home, root, "transaction", "commit", transaction_id, "--json")
    assert committed.returncode == 0, committed.stderr
    commit_payload = json.loads(committed.stdout)
    assert commit_payload["created"] == ["concepts/a.md"]
    assert commit_payload["removed"] == ["concepts/obsolete.md"]
    assert commit_payload["log_path"] == "log.md"

    for _attempt in range(2):
        restored = run_cli(home, root, "transaction", "restore", transaction_id)
        assert restored.returncode == 0, restored.stderr
    assert obsolete.is_file()
    assert not (config.vault / "concepts/a.md").exists()

    for _attempt in range(2):
        discarded = run_cli(home, root, "transaction", "discard", transaction_id)
        assert discarded.returncode == 0, discarded.stderr
    assert json.loads(run_cli(home, root, "transaction", "list", "--json").stdout) == []

    aborted_begin = run_cli(
        home,
        root,
        "transaction",
        "begin",
        "--source",
        str(source),
        "--json",
    )
    aborted_id = json.loads(aborted_begin.stdout)["transaction_id"]
    aborted = run_cli(home, root, "transaction", "abort", aborted_id)
    assert aborted.returncode == 0, aborted.stderr

    assert git_output(root, "rev-parse", "HEAD") == before_head
    assert git_output(root, "remote", "-v") == before_remotes


def test_transaction_list_filters_and_summarizes_records(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config, log_writer=log_writer(config))
    complete = manager.begin([source], transaction_id="tx-complete")
    candidate_page(complete, "concepts/a.md")
    manager.commit(complete.transaction_id)
    manager.begin([source], transaction_id="tx-active")

    result = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "list",
        "--status",
        "active,promoting,failed",
        "--summary",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        {
            "transaction_id": "tx-active",
            "status": "active",
            "recommended_action": {
                "command": "llmwikiops transaction commit tx-active",
                "reason": (
                    "commit after fixing the original cause and reviewing the "
                    "candidate"
                ),
                "requires": [
                    "the original failure cause is removed",
                    "the candidate vault has been reviewed",
                ],
            },
        }
    ]


def test_transaction_list_filtered_empty_result_is_json_array(
    tmp_path: Path, log_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    complete = manager.begin([add_source(root)], transaction_id="tx-complete")
    candidate_page(complete, "concepts/a.md")
    manager.commit(complete.transaction_id)

    result = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "list",
        "--status",
        "active,promoting,failed",
        "--summary",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]\n"


def test_transaction_list_human_summary_omits_paths(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    TransactionManager(config).begin([add_source(root)], transaction_id="tx-active")

    result = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "list",
        "--status",
        "active",
        "--summary",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "tx-active\tactive\tllmwikiops transaction commit tx-active\n"
    )


def test_transaction_show_returns_one_full_record(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    record = TransactionManager(config).begin(
        [add_source(root)], transaction_id="tx-show"
    )

    result = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "show",
        record.transaction_id,
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["transaction_id"] == "tx-show"
    assert payload["status"] == "active"
    assert payload["source_ids"] == ["sources/a.md"]
    assert payload["candidate_vault"] == str(record.candidate_vault)
    assert payload["recommended_action"]["command"] == (
        "llmwikiops transaction commit tx-show"
    )
    assert payload["allowed_actions"]


def test_transaction_validate_parser_accepts_full_json_flags() -> None:
    args = cli_module.build_parser().parse_args(
        ["transaction", "validate", "tx-1", "--json", "--pretty"]
    )

    assert args.transaction_id == "tx-1"
    assert args.json is True
    assert args.pretty is True


def test_transaction_validate_cli_returns_structured_findings_read_only(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-cli")
    candidate_page(record, "concepts/a.md", PAGE + "[[missing]]\n")
    before = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    result = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "validate",
        "tx-cli",
        "--json",
        "--pretty",
    )

    after = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.startswith("{\n")
    assert set(payload) == {
        "transaction_id",
        "status",
        "candidate_pages",
        "deletions",
        "issues",
        "warnings",
    }
    assert payload["status"] == "fail"
    assert payload["issues"][0]["code"] == "broken-link"
    assert "recovery" not in payload
    assert before == after
    assert not any((record.workspace / "snapshots").iterdir())


def test_transaction_validate_cli_passing_human_output_is_concise(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-pass")
    candidate_page(record, "concepts/a.md")

    result = run_cli(
        tmp_path / "home", root, "transaction", "validate", "tx-pass"
    )

    assert result.returncode == 0
    assert result.stdout == "transaction tx-pass: pass (0 issues)\n"


def test_transaction_validate_cli_human_issues_escape_terminal_controls(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = TransactionValidationReport(
        transaction_id="tx-unsafe-output",
        status="fail",
        candidate_pages=("concepts/a.md",),
        deletions=(),
        issues=(
            ValidationIssue(
                "broken-link\nforged-code\x1b[31m",
                "concepts/a.md\nforged-path\x1b[31m",
                "broken link\nforged-message\x1b[31m",
                "missing",
            ),
        ),
    )

    class Manager:
        def validate(self, transaction_id: str) -> TransactionValidationReport:
            assert transaction_id == "tx-unsafe-output"
            return report

    monkeypatch.setattr(cli_module, "_transaction_manager", Manager)

    result = cli_module.cmd_transaction_validate(
        argparse.Namespace(
            transaction_id="tx-unsafe-output",
            json=False,
            pretty=False,
        )
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.err == ""
    assert "\x1b" not in captured.out
    assert captured.out.splitlines() == [
        "transaction tx-unsafe-output: fail (1 issues)",
        "broken-link\\nforged-code\\x1b[31m: "
        "concepts/a.md\\nforged-path\\x1b[31m: "
        "broken link\\nforged-message\\x1b[31m",
    ]


def test_transaction_validate_cli_invalid_id_uses_structured_error_envelope(
    tmp_path: Path,
) -> None:
    root, _config = make_config(tmp_path)

    result = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "validate",
        "missing",
        "--json",
        "--pretty",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.startswith("{\n")
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "transaction-error"
    assert "missing" in payload["error"]["message"]
    assert payload["recovery"] == {
        "transaction_id": None,
        "transaction_status": None,
        "inspect_command": (
            "llmwikiops transaction list --status active,promoting,failed "
            "--summary --json"
        ),
        "preferred_action": None,
        "alternatives": [],
    }


def test_transaction_cli_human_list_computes_guidance_once_per_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    manager.begin([add_source(root)], transaction_id="tx-1")
    calls: list[str] = []
    original = transaction_guidance_module.guidance_for_record

    def count_guidance(record):
        calls.append(record.transaction_id)
        return original(record)

    monkeypatch.setattr(cli_module, "_transaction_manager", lambda: manager)
    monkeypatch.setattr(
        transaction_guidance_module, "guidance_for_record", count_guidance
    )

    result = cli_module.cmd_transaction_list(
        argparse.Namespace(json=False, pretty=False)
    )

    assert result == 0
    assert calls == ["tx-1"]
    assert "\tllmwikiops transaction commit tx-1\t" in capsys.readouterr().out


def test_transaction_cli_retries_a_retained_failed_transaction(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
    before_head = git_output(root, "rev-parse", "HEAD")
    before_remotes = git_output(root, "remote", "-v")

    def fail_operation(_change):
        raise OSError("simulated operation failure")

    manager = TransactionManager(config, log_writer=fail_operation)
    record = manager.begin([source], transaction_id="retry-cli")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(TransactionError, match="simulated operation failure"):
        manager.commit("retry-cli")
    assert manager.load("retry-cli").status == "failed"

    retried = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "retry",
        "retry-cli",
        "--json",
    )

    assert retried.returncode == 0, retried.stderr
    assert json.loads(retried.stdout)["transaction_id"] == "retry-cli"
    assert TransactionManager(config).load("retry-cli").status == "complete"
    assert git_output(root, "rev-parse", "HEAD") == before_head
    assert git_output(root, "remote", "-v") == before_remotes


@pytest.mark.parametrize(
    "arguments",
    [
        ("transaction", "begin", "--source", "source.md", "--json"),
        ("transaction", "list", "--json"),
        ("transaction", "delete", "tx-1", "concepts/a.md", "--json"),
        ("transaction", "validate", "tx-1", "--json"),
        ("transaction", "commit", "tx-1", "--json"),
        ("transaction", "retry", "tx-1", "--json"),
        ("transaction", "restore", "tx-1", "--json"),
        ("transaction", "discard", "tx-1", "--json"),
        ("transaction", "abort", "tx-1", "--json"),
    ],
)
def test_transaction_cli_json_failures_outside_portable_mode_are_structured(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(tmp_path / "home", cwd, *arguments)

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "config-error"
    assert "portable repository" in payload["error"]["message"]
    assert payload["recovery"] == {
        "transaction_id": None,
        "transaction_status": None,
        "inspect_command": (
            "llmwikiops transaction list --status active,promoting,failed "
            "--summary --json"
        ),
        "preferred_action": None,
        "alternatives": [],
    }
    assert result.stdout == json.dumps(payload, ensure_ascii=False) + "\n"


def test_transaction_begin_json_unknown_user_source_is_structured(
    tmp_path: Path,
) -> None:
    root, _config = make_config(tmp_path)
    missing_user = f"~llmwikiops_no_such_user_{os.getpid()}/file.md"

    result = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "begin",
        "--source",
        missing_user,
        "--json",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "transaction-error"
    assert missing_user in payload["error"]["message"]
    assert payload["recovery"]["transaction_id"] is None
    assert result.stdout == json.dumps(payload, ensure_ascii=True) + "\n"


def test_transaction_begin_json_cwd_race_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _root, config = make_config(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "_portable_command_config",
        lambda _command: config,
    )

    def fail_cwd(_cls):
        raise OSError("cwd disappeared after config resolution")

    monkeypatch.setattr(cli_module.Path, "cwd", classmethod(fail_cwd))

    result = cli_module.cmd_transaction_begin(
        argparse.Namespace(sources=["relative.md"], json=True, pretty=False)
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["error"]["code"] == "transaction-error"
    assert "relative.md" in payload["error"]["message"]
    assert "cwd disappeared" in payload["error"]["message"]
    assert captured.out == json.dumps(payload, ensure_ascii=True) + "\n"


def test_transaction_cli_active_commit_failure_reports_trusted_guidance(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", "not frontmatter\n")

    result = run_cli(
        tmp_path / "home", root, "transaction", "commit", "tx-1", "--json"
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "transaction-error"
    assert payload["recovery"]["transaction_id"] == "tx-1"
    assert payload["recovery"]["transaction_status"] == "active"
    assert payload["recovery"]["preferred_action"]["command"] == (
        "llmwikiops transaction commit tx-1"
    )
    assert manager.load("tx-1").status == "active"


def test_transaction_cli_corrupt_record_only_reports_inspection_guidance(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    record = TransactionManager(config).begin(
        [add_source(root)], transaction_id="tx-1"
    )
    (record.workspace / "metadata.json").write_text("{not json\n", encoding="utf-8")

    result = run_cli(
        tmp_path / "home", root, "transaction", "commit", "tx-1", "--json"
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "transaction-error"
    assert payload["recovery"] == {
        "transaction_id": None,
        "transaction_status": None,
        "inspect_command": (
            "llmwikiops transaction list --status active,promoting,failed "
            "--summary --json"
        ),
        "preferred_action": None,
        "alternatives": [],
    }


def test_trusted_recovery_guidance_does_not_hide_programmer_value_error() -> None:
    class BrokenManager:
        def load(self, _transaction_id: str):
            raise ValueError("programmer regression")

    with pytest.raises(ValueError, match="^programmer regression$"):
        cli_module._trusted_recovery_guidance(BrokenManager(), "tx-1")


def test_transaction_cli_corrupt_manifest_marker_reports_manifest_error(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    (config.vault / ".manifest.json").write_text("{}\n", encoding="utf-8")

    result = run_cli(
        tmp_path / "home",
        root,
        "transaction",
        "begin",
        "--source",
        str(source),
        "--json",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "manifest-error"
    assert payload["recovery"] == {
        "transaction_id": None,
        "transaction_status": None,
        "inspect_command": (
            "llmwikiops transaction list --status active,promoting,failed "
            "--summary --json"
        ),
        "preferred_action": None,
        "alternatives": [],
    }
    assert result.stdout == json.dumps(payload, ensure_ascii=True) + "\n"


def test_transaction_error_code_handles_cyclic_cause_chain() -> None:
    error = TransactionError("cycle")
    error.__cause__ = error

    assert cli_module._transaction_error_code(error) == "transaction-error"


def test_transaction_cli_corrupt_manifest_shard_reports_manifest_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manifest = ShardedManifest(config)
    manifest.upsert(source, pages=[])
    shard = manifest.entry_path("sources/a.md")
    manager = TransactionManager(config)
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    original_promote = manager._promote_candidate

    def corrupt_shard_during_promotion(candidate_value, target_value, *args) -> None:
        original_promote(candidate_value, target_value, *args)
        shard.write_text("{not json\n", encoding="utf-8")

    monkeypatch.setattr(manager, "_promote_candidate", corrupt_shard_during_promotion)
    monkeypatch.setattr(cli_module, "_transaction_manager", lambda: manager)

    result = cli_module.cmd_transaction_commit(
        argparse.Namespace(transaction_id="tx-1", json=True, pretty=False)
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["error"]["code"] == "manifest-error"
    reloaded = manager.load("tx-1")
    assert reloaded.status == "failed"
    guidance = transaction_guidance_module.guidance_for_record(reloaded)
    assert payload["recovery"] == guidance.as_dict()
    assert payload["recovery"]["transaction_id"] == "tx-1"
    assert payload["recovery"]["transaction_status"] == "failed"
    serialized_actions = [
        payload["recovery"]["preferred_action"],
        *payload["recovery"]["alternatives"],
    ]
    assert [action["command"] for action in serialized_actions] == [
        action.command for action in guidance.allowed_actions
    ]
    assert captured.out == json.dumps(payload, ensure_ascii=True) + "\n"


def test_transaction_json_failure_escapes_surrogate_path_bytes(
    tmp_path: Path,
) -> None:
    root, _config = make_config(tmp_path)
    environment = os.environ.copy()
    environment["HOME"] = str(tmp_path / "home")
    command = [
        os.fsencode(sys.executable),
        b"-m",
        b"obsidian_wiki.cli",
        b"transaction",
        b"begin",
        b"--source",
        b"sources/missing-\xff.md",
        b"--json",
    ]

    result = subprocess.run(
        command,
        cwd=os.fsencode(root),
        env=environment,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == b""
    decoded = result.stdout.decode("utf-8", errors="strict")
    payload = json.loads(decoded)
    assert payload["error"]["code"] == "transaction-error"
    assert "\udcff" in payload["error"]["message"]
    assert b"\\udcff" in result.stdout


def test_transaction_human_failure_escapes_terminal_control_characters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    unsafe_path = "concepts/badé\npreferred: forged\x1b[31m.md"
    candidate_page(record, unsafe_path)
    monkeypatch.setattr(cli_module, "_transaction_manager", lambda: manager)

    result = cli_module.cmd_transaction_delete(
        argparse.Namespace(
            transaction_id="tx-1",
            path=unsafe_path,
            json=False,
            pretty=False,
        )
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert "\x1b" not in captured.err
    assert "badé" in captured.err
    assert "\\npreferred: forged\\x1b[31m.md" in captured.err
    assert [
        line for line in captured.err.splitlines() if line.startswith("preferred:")
    ] == [
        "preferred: llmwikiops transaction commit tx-1 — "
        "commit after fixing the original cause and reviewing the candidate"
    ]
    assert "inspect: llmwikiops transaction show tx-1 --json" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ("transaction", "commit", "--json"),
        ("transaction", "validate", "--json"),
        ("transaction", "list", "--json", "--unknown"),
        ("transaction", "commit", "--json", "--pretty"),
    ],
)
def test_transaction_cli_json_parse_errors_are_structured(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(tmp_path / "home", cwd, *arguments)

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "transaction-error"
    assert payload["recovery"] == {
        "transaction_id": None,
        "transaction_status": None,
        "inspect_command": (
            "llmwikiops transaction list --status active,promoting,failed "
            "--summary --json"
        ),
        "preferred_action": None,
        "alternatives": [],
    }
    assert result.stdout.endswith("\n")
    if "--pretty" in arguments:
        assert result.stdout.startswith("{\n")


@pytest.mark.parametrize("abbreviation", ["--j", "--js", "--jso", "--p", "--pr"])
def test_transaction_cli_rejects_long_option_abbreviations(
    tmp_path: Path, abbreviation: str
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(
        tmp_path / "home", cwd, "transaction", "list", abbreviation
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("usage: llmwikiops")
    assert f"unrecognized arguments: {abbreviation}" in result.stderr


@pytest.mark.parametrize("abbreviation", ["--j", "--js", "--jso", "--p", "--pr"])
def test_exact_json_keeps_machine_mode_when_abbreviation_is_rejected(
    tmp_path: Path, abbreviation: str
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(
        tmp_path / "home",
        cwd,
        "transaction",
        "list",
        "--json",
        abbreviation,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "transaction-error"
    assert f"unrecognized arguments: {abbreviation}" in payload["error"]["message"]


def test_long_option_abbreviation_remains_enabled_outside_transaction() -> None:
    args = cli_module.build_parser().parse_args(["info", "--j"])

    assert args.command == "info"
    assert args.json is True


@pytest.mark.parametrize(
    ("arguments", "expected_prog", "message"),
    [
        (
            ["transaction", "commit"],
            "llmwikiops transaction commit",
            "the following arguments are required: transaction_id",
        ),
        (
            ["transaction", "bogus"],
            "llmwikiops transaction",
            "invalid choice: 'bogus'",
        ),
    ],
)
def test_transaction_parse_error_carries_exact_nested_parser(
    arguments: list[str], expected_prog: str, message: str
) -> None:
    parser = cli_module.build_parser()

    with pytest.raises(cli_module._ArgumentParseError) as exc_info:
        parser.parse_args(arguments)

    assert exc_info.value.parser.prog == expected_prog
    assert message in exc_info.value.message


def test_transaction_json_parse_never_replaces_process_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected_stderr = sys.stderr
    original_parse_args = argparse.ArgumentParser.parse_args
    stderr_identity: list[bool] = []

    def guarded_parse_args(parser, *args, **kwargs):
        stderr_identity.append(sys.stderr is expected_stderr)
        return original_parse_args(parser, *args, **kwargs)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", guarded_parse_args)

    result = cli_module.main(["transaction", "commit", "--json"])

    captured = capsys.readouterr()
    assert result == 1
    assert stderr_identity == [True]
    assert captured.err == ""
    assert json.loads(captured.out)["error"]["code"] == "transaction-error"


def test_transaction_cli_human_parse_error_keeps_argparse_usage(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(tmp_path / "home", cwd, "transaction", "commit")

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("usage: llmwikiops transaction commit")
    assert "the following arguments are required: transaction_id" in result.stderr


def test_transaction_cli_unknown_human_error_uses_transaction_parser_prog(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(tmp_path / "home", cwd, "transaction", "bogus")

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("usage: llmwikiops transaction")
    assert "llmwikiops transaction: error: argument transaction_command" in (
        result.stderr
    )


def test_transaction_cli_json_parse_detection_ignores_post_separator_help(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(
        tmp_path / "home",
        cwd,
        "transaction",
        "commit",
        "--json",
        "tx",
        "--",
        "--help",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "transaction-error"
    assert payload["recovery"]["preferred_action"] is None


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("transaction", "--", "commit", "--json"), (True, False, False)),
        (
            ("transaction", "--", "commit", "tx", "--json"),
            (True, False, False),
        ),
        (
            ("transaction", "--", "commit", "tx", "--", "--json"),
            (False, False, False),
        ),
        (
            ("transaction", "commit", "--json", "tx", "--", "--help"),
            (True, False, False),
        ),
        (("transaction", "--help"), (False, False, True)),
        (("transaction", "--", "commit", "--help"), (False, False, True)),
        (
            ("transaction", "--", "commit", "--json", "--pretty"),
            (True, True, False),
        ),
        (
            ("transaction", "--", "commit", "--json", "--", "--pretty"),
            (True, False, False),
        ),
    ],
)
def test_transaction_option_intent_matches_nested_argparse_levels(
    arguments: tuple[str, ...], expected: tuple[bool, bool, bool]
) -> None:
    assert cli_module._transaction_option_intent(list(arguments)) == expected


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("transaction", "bogus", "--json"), (True, False, False)),
        (("transaction", "--json", "bogus"), (True, False, False)),
        (
            ("transaction", "bogus", "--pretty", "--json"),
            (True, True, False),
        ),
        (
            ("transaction", "--json", "--pretty", "bogus"),
            (True, True, False),
        ),
        (("transaction", "bogus", "--", "--json"), (False, False, False)),
        (("transaction", "--", "bogus", "--json"), (True, False, False)),
        (
            ("transaction", "bogus", "--json", "--help"),
            (True, False, True),
        ),
        (("transaction", "--json"), (True, False, False)),
        (("transaction", "--", "--json"), (True, False, False)),
    ],
)
def test_transaction_option_intent_scans_unknown_subcommand_error_region(
    arguments: tuple[str, ...], expected: tuple[bool, bool, bool]
) -> None:
    assert cli_module._transaction_option_intent(list(arguments)) == expected


@pytest.mark.parametrize(
    ("arguments", "expected_mode", "pretty"),
    [
        (("transaction", "bogus", "--json"), "json-error", False),
        (("transaction", "--json", "bogus"), "json-error", False),
        (
            ("transaction", "bogus", "--pretty", "--json"),
            "json-error",
            True,
        ),
        (
            ("transaction", "--pretty", "--json", "bogus"),
            "json-error",
            True,
        ),
        (("transaction", "bogus", "--", "--json"), "human-error", False),
        (("transaction", "--", "bogus", "--json"), "json-error", False),
        (
            ("transaction", "bogus", "--json", "--help"),
            "human-error",
            False,
        ),
        (("transaction", "--json"), "json-error", False),
        (("transaction", "--", "--json"), "json-error", False),
    ],
)
def test_transaction_cli_unknown_subcommand_intent_is_order_independent(
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected_mode: str,
    pretty: bool,
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(tmp_path / "home", cwd, *arguments)

    if expected_mode == "json-error":
        assert result.returncode == 1
        assert result.stderr == ""
        assert json.loads(result.stdout)["status"] == "error"
        assert result.stdout.startswith("{\n" if pretty else '{"')
    else:
        assert expected_mode == "human-error"
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith("usage: llmwikiops")


@pytest.mark.parametrize(
    ("arguments", "expected_mode", "pretty"),
    [
        (("transaction", "--", "commit", "--json"), "json-error", False),
        (
            ("transaction", "--", "commit", "tx", "--json"),
            "json-error",
            False,
        ),
        (
            ("transaction", "--", "commit", "tx", "--", "--json"),
            "human-error",
            False,
        ),
        (
            ("transaction", "commit", "--json", "tx", "--", "--help"),
            "json-error",
            False,
        ),
        (("transaction", "--help"), "help", False),
        (("transaction", "--", "commit", "--help"), "help", False),
        (
            ("transaction", "--", "commit", "--json", "--pretty"),
            "json-error",
            True,
        ),
        (
            ("transaction", "--", "commit", "--json", "--", "--pretty"),
            "json-error",
            False,
        ),
    ],
)
def test_transaction_cli_nested_separator_parse_matrix(
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected_mode: str,
    pretty: bool,
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(tmp_path / "home", cwd, *arguments)

    if expected_mode == "json-error":
        assert result.returncode == 1
        assert result.stderr == ""
        assert json.loads(result.stdout)["status"] == "error"
        assert result.stdout.startswith("{\n" if pretty else '{"')
    elif expected_mode == "human-error":
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith("usage: llmwikiops")
    else:
        assert expected_mode == "help"
        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout.startswith("usage: llmwikiops")


def test_transaction_cli_post_separator_json_keeps_human_argparse_error(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(
        tmp_path / "home",
        cwd,
        "transaction",
        "delete",
        "tx",
        "path",
        "--",
        "--json",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("usage: llmwikiops")
    assert "unrecognized arguments" in result.stderr


def test_transaction_cli_json_help_keeps_argparse_success(tmp_path: Path) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(
        tmp_path / "home",
        cwd,
        "transaction",
        "commit",
        "--json",
        "--help",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("usage: llmwikiops transaction commit")
    assert "transaction_id" in result.stdout


def test_transaction_cli_json_deleted_cwd_failure_is_structured(
    tmp_path: Path,
) -> None:
    deleted_cwd = tmp_path / "deleted-cwd"
    deleted_cwd.mkdir()
    environment = os.environ.copy()
    environment["HOME"] = str(tmp_path / "home")
    script = """
import os
import sys
from obsidian_wiki.cli import main

os.chdir(sys.argv[1])
os.rmdir(sys.argv[1])
raise SystemExit(main(sys.argv[2:]))
"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(deleted_cwd),
            "transaction",
            "list",
            "--json",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "config-error"
    assert "current working directory" in payload["error"]["message"]
    assert result.stdout == json.dumps(payload, ensure_ascii=True) + "\n"


def test_transaction_cli_human_missing_transaction_is_stderr_only(
    tmp_path: Path,
) -> None:
    root, _config = make_config(tmp_path)

    result = run_cli(tmp_path / "home", root, "transaction", "commit", "missing")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "error:" in result.stderr
    assert (
        "inspect: llmwikiops transaction list --status active,promoting,failed "
        "--summary --json"
    ) in result.stderr
    assert "preferred:" not in result.stderr
    assert "alternative:" not in result.stderr


def test_transaction_cli_human_failure_explains_trusted_recovery_actions(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    record = TransactionManager(config).begin(
        [add_source(root)], transaction_id="tx-1"
    )
    candidate_page(record, "concepts/a.md", "not frontmatter\n")

    result = run_cli(tmp_path / "home", root, "transaction", "commit", "tx-1")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "error:" in result.stderr
    assert "transaction status: active" in result.stderr
    assert "inspect: llmwikiops transaction show tx-1 --json" in result.stderr
    assert "preferred: llmwikiops transaction commit tx-1 — " in result.stderr
    assert "alternative: llmwikiops transaction abort tx-1 — " in result.stderr
    assert "requires: the original failure cause is removed" in result.stderr
    assert "requires: the candidate vault has been reviewed" in result.stderr
    assert "requires: the candidate is no longer needed" in result.stderr


def test_explicit_repository_transaction_source_is_relative_to_repository(
    tmp_path: Path,
) -> None:
    root, _config = make_config(tmp_path)
    business = tmp_path / "business"
    business.mkdir()
    (business / "sources").mkdir()
    (business / "sources" / "input.md").write_text("business", encoding="utf-8")
    selected_source = root / "sources" / "input.md"
    selected_source.write_text("selected", encoding="utf-8")

    result = run_cli(
        tmp_path / "home",
        business,
        "-C",
        str(root),
        "transaction",
        "begin",
        "--source",
        "sources/input.md",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["source_ids"] == ["sources/input.md"]
    assert Path(payload["workspace"]).is_relative_to(root)


@pytest.mark.skipif(os.name != "posix", reason="POSIX link safety contract")
def test_transaction_command_rejects_hard_linked_repository_config(
    tmp_path: Path,
) -> None:
    root, _ = make_config(tmp_path)
    config = root / ".llmwikiops" / "config.toml"
    duplicate = tmp_path / "config-copy.toml"
    os.link(config, duplicate)

    result = run_cli(tmp_path / "home", root, "transaction", "list", "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "single-link ordinary file" in payload["error"]["message"]
    assert "Traceback" not in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink behavior")
def test_transaction_json_structures_configured_path_symlink_loop(
    tmp_path: Path,
) -> None:
    root, _ = make_config(tmp_path)
    (root / "wiki").rename(root / "original-wiki")
    (root / "wiki").symlink_to("wiki")

    result = run_cli(tmp_path / "home", root, "transaction", "list", "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "paths.vault cannot be resolved safely" in payload["error"]["message"]
    assert "Traceback" not in result.stdout


def _prospective_page(
    path: str,
    *,
    title: str = "Example",
    category: str = "concepts",
    tags: str = "  - example",
    sources: str = "  - sources/a.md",
    tags_line: Optional[str] = None,
    sources_line: Optional[str] = None,
    created: str = "2026-08-07",
    updated: str = "2026-08-07",
    body: str = "# Example\n",
    candidate: bool = True,
) -> ProspectivePage:
    return ProspectivePage(
        path,
        "\n".join(
            (
                "---",
                f"title: {title}",
                f"category: {category}",
                *( (f"tags: {tags_line}",) if tags_line is not None else ("tags:", tags) ),
                *(
                    (f"sources: {sources_line}",)
                    if sources_line is not None
                    else ("sources:", sources)
                ),
                f"created: {created}",
                f"updated: {updated}",
                "---",
                body,
            )
        ),
        candidate,
    )


def _validation_codes(*pages: ProspectivePage, sources: tuple[str, ...] = ("sources/a.md",)) -> list[str]:
    return [
        issue.code
        for issue in validate_prospective_pages(tuple(pages), sources)
    ]


def test_transaction_validation_issue_is_frozen_and_serializes_stably() -> None:
    issue = ValidationIssue(
        "broken-link", "concepts/a.md", "link does not resolve", "missing"
    )

    assert issue.as_dict() == {
        "code": "broken-link",
        "path": "concepts/a.md",
        "message": "link does not resolve",
        "target": "missing",
    }
    assert ValidationIssue("frontmatter-invalid", "concepts/a.md", "invalid").as_dict() == {
        "code": "frontmatter-invalid",
        "path": "concepts/a.md",
        "message": "invalid",
    }
    with pytest.raises(AttributeError):
        issue.code = "other"  # type: ignore[misc]


def test_candidate_semantics_reports_invalid_timestamp_category_and_empty_scalar() -> None:
    page = _prospective_page(
        "entities/example.md",
        title='""',
        category="concepts",
        created="not-a-date",
        updated="2026-08-07T10:00:00",
    )

    assert _validation_codes(page) == [
        "frontmatter-category-path",
        "frontmatter-created-invalid",
        "frontmatter-title-empty",
        "frontmatter-updated-invalid",
    ]


def test_candidate_semantics_requires_list_tags_and_sources() -> None:
    page = _prospective_page(
        "concepts/example.md", tags_line="example", sources_line="sources/a.md"
    )

    assert _validation_codes(page) == [
        "frontmatter-sources-type",
        "frontmatter-tags-type",
    ]


def test_candidate_semantics_accepts_dates_aware_timestamps_and_source_subset() -> None:
    page = _prospective_page(
        "concepts/example.md",
        tags="  - example\n  - validation",
        sources="  - sources/b.md",
        created="2026-08-07T10:00:00+08:00",
        updated="2026-08-07",
    )

    assert _validation_codes(page, sources=("sources/a.md", "sources/b.md")) == []


def test_page_timestamp_parser_preserves_sortable_boundary_aware_values() -> None:
    earliest = transaction_validation_module.parse_date_or_aware_timestamp(
        "0001-01-01T00:00:00+23:59"
    )
    latest = transaction_validation_module.parse_date_or_aware_timestamp(
        "9999-12-31T23:59:59-23:59"
    )

    assert sorted((latest, earliest)) == [earliest, latest]
    assert earliest.isoformat() == "0001-01-01T00:00:00+23:59"
    assert latest.isoformat() == "9999-12-31T23:59:59-23:59"


def test_page_metadata_accepts_boundary_aware_timestamps() -> None:
    page = _prospective_page(
        "concepts/boundary.md",
        created="0001-01-01T00:00:00+23:59",
        updated="9999-12-31T23:59:59-23:59",
    )

    assert validate_page_metadata(
        page.path,
        page.text,
        allowed_source_ids=("sources/a.md",),
    ) == ()


@pytest.mark.parametrize(
    "source_id",
    [
        "../escape.md",
        "/absolute.md",
        r"sources\windows.md",
        "C:/drive.md",
        "./sources/dot.md",
        "sources/./dot.md",
    ],
)
def test_page_metadata_rejects_noncanonical_source_id_syntax(source_id: str) -> None:
    page = _prospective_page(
        "concepts/example.md",
        sources=f"  - {source_id}",
    )

    assert "frontmatter-source-invalid" in {
        issue.code for issue in validate_page_metadata(page.path, page.text)
    }


def test_page_metadata_rejects_empty_source_id_during_frontmatter_parsing() -> None:
    page = _prospective_page(
        "concepts/example.md",
        sources='  - ""',
    )

    issues = validate_page_metadata(page.path, page.text)

    assert [issue.code for issue in issues] == ["frontmatter-invalid"]
    assert "empty item" in issues[0].message


def test_page_metadata_validates_configured_source_roots_lexically() -> None:
    valid = _prospective_page(
        "concepts/valid.md",
        sources="  - sources/资料/组会.md",
    )
    invalid = _prospective_page(
        "concepts/invalid.md",
        sources="  - outside/资料/组会.md",
    )

    assert validate_page_metadata(
        valid.path,
        valid.text,
        source_roots=("sources",),
    ) == ()
    assert [
        issue.code
        for issue in validate_page_metadata(
            invalid.path,
            invalid.text,
            source_roots=("sources",),
        )
    ] == ["frontmatter-source-root"]


@pytest.mark.parametrize(
    "source_id",
    ["sources", "sources-other/资料.md"],
)
def test_page_metadata_requires_source_id_strictly_below_configured_root(
    source_id: str,
) -> None:
    page = _prospective_page(
        "concepts/invalid.md",
        sources=f"  - {source_id}",
    )

    assert [
        issue.code
        for issue in validate_page_metadata(
            page.path,
            page.text,
            source_roots=("sources",),
        )
    ] == ["frontmatter-source-root"]


def test_project_category_accepts_overviews_and_nested_semantic_pages() -> None:
    overview = _prospective_page("projects/widget.md", category="projects")
    named_overview = _prospective_page("projects/gadget/gadget.md", category="projects")
    nested = _prospective_page("projects/widget/concepts/design.md", category="concepts")

    assert _validation_codes(overview, named_overview, nested) == []


def test_project_category_rejects_non_overview_and_nonsemantic_nested_paths() -> None:
    misplaced_overview = _prospective_page(
        "projects/widget/not-widget.md", category="projects"
    )
    unsupported_nested = _prospective_page(
        "projects/widget/misc/note.md", category="projects"
    )

    assert _validation_codes(misplaced_overview, unsupported_nested) == [
        "frontmatter-category-path",
        "frontmatter-category-path",
    ]


def test_multi_source_validation_rejects_foreign_and_duplicate_sources() -> None:
    page = _prospective_page(
        "concepts/example.md",
        sources="  - sources/a.md\n  - sources/a.md\n  - sources/foreign.md",
    )

    assert _validation_codes(page, sources=("sources/a.md", "sources/b.md")) == [
        "frontmatter-sources-duplicate",
        "frontmatter-sources-foreign",
    ]


def test_prospective_graph_accepts_candidate_to_candidate_and_live_links() -> None:
    candidate = _prospective_page(
        "concepts/alpha.md", body="[[Beta]]\n[[Live Page]]\n"
    )
    beta = _prospective_page("concepts/beta.md")
    live = _prospective_page(
        "references/live-page.md", candidate=False, body="[[Alpha]]\n"
    )

    assert _validation_codes(candidate, beta, live) == []


def test_prospective_graph_reports_live_links_broken_by_deleted_page() -> None:
    live = _prospective_page(
        "concepts/retained.md", candidate=False, body="[[Deleted]]\n"
    )

    assert validate_prospective_pages((live,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/retained.md",
            "broken link target: deleted",
            "deleted",
        ),
    )


def test_prospective_graph_validates_markdown_links_and_allows_self_links() -> None:
    page = _prospective_page(
        "concepts/alpha.md",
        body="[Beta](../references/beta.md#details)\n[[Alpha]]\n",
    )
    beta = _prospective_page("references/beta.md", category="references")

    assert _validation_codes(page, beta) == []


def test_prospective_graph_ignores_external_and_nonmarkdown_links() -> None:
    page = _prospective_page(
        "concepts/alpha.md",
        body=(
            "[External](https://example.com/doc.md)\n"
            "[Asset](asset.md.png)\n"
            "[Gamma](../references/gamma.md#section)\n"
        ),
    )
    gamma = _prospective_page("references/gamma.md", category="references")

    assert _validation_codes(page, gamma) == []


def test_prospective_graph_ignores_attachment_wikilinks_but_checks_page_targets() -> None:
    page = _prospective_page(
        "concepts/alpha.md",
        body="[[asset.png]]\n![[asset.png]]\n[[Missing]]\n[[missing.md]]\n",
    )

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: missing",
            "missing",
        ),
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: missing",
            "missing",
        ),
    )


def test_prospective_graph_parses_markdown_destination_forms_before_classifying() -> None:
    page = _prospective_page(
        "concepts/alpha.md",
        body=(
            '[Title](missing.md "title")\n'
            "[Angle](<missing.md>)\n"
            "[Query](missing.md?raw=1)\n"
            "[Fragment](missing.md#section)\n"
        ),
    )

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: concepts/missing",
            "concepts/missing",
        ),
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: concepts/missing",
            "concepts/missing",
        ),
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: concepts/missing",
            "concepts/missing",
        ),
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: concepts/missing",
            "concepts/missing",
        ),
    )


def test_prospective_graph_ignores_malformed_bracket_heavy_text() -> None:
    page = _prospective_page(
        "concepts/alpha.md", body=("[" * 4096) + ("[[" * 4096), candidate=False
    )

    assert validate_prospective_pages((page,), ("sources/a.md",)) == ()


@pytest.mark.parametrize(
    "prefix",
    ("[ stray ", "[[ stray ", "[" * 4096),
    ids=("markdown", "wikilink", "bracket-heavy"),
)
def test_prospective_graph_recovers_valid_wikilinks_after_malformed_prefixes(
    prefix: str,
) -> None:
    page = _prospective_page("concepts/alpha.md", body=prefix + "[[Missing]]\n")

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: missing",
            "missing",
        ),
    )


@pytest.mark.parametrize(
    ("body", "target"),
    (
        ("[bad](unfinished [[Missing]]\n", "missing"),
        ("[bad](unfinished [Missing](missing.md)\n", "concepts/missing"),
        ("[[bad [Missing](missing.md)\n", "concepts/missing"),
        ('[bad](unfinished.md "title [Missing](missing.md)\n', "concepts/missing"),
        ("[bad](<unfinished.md [Missing](missing.md)\n", "concepts/missing"),
        ("[bad [Missing](missing.md)\n", "concepts/missing"),
        ("[[bad [text] [[Missing]]\n", "missing"),
    ),
    ids=(
        "unterminated-destination-wikilink",
        "unterminated-destination-markdown",
        "unterminated-wikilink-body",
        "unterminated-title",
        "unterminated-angle",
        "nested-markdown-label",
        "nested-wikilink-body",
    ),
)
def test_prospective_graph_recovers_links_from_all_malformed_scanner_states(
    body: str, target: str
) -> None:
    page = _prospective_page("concepts/alpha.md", body=body)

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: " + target,
            target,
        ),
    )


@pytest.mark.parametrize(
    "prefix",
    ("[", "[" * 4095, "[" * 4096),
    ids=("overlap", "odd-bracket-run", "even-bracket-run"),
)
def test_prospective_graph_recovers_overlapping_wikilinks_after_bracket_runs(
    prefix: str,
) -> None:
    page = _prospective_page("concepts/alpha.md", body=prefix + "[[Missing]]\n")

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: missing",
            "missing",
        ),
    )


@pytest.mark.parametrize(
    ("body", "target"),
    (
        ("[See [draft]](missing.md)\n", "concepts/missing"),
        ('[x](missing.md "title [draft]")\n', "concepts/missing"),
        ("[x](<missing[1].md>)\n", "concepts/missing[1]"),
        ("[[Missing|[draft]]]\n", "missing"),
    ),
    ids=("nested-label", "quoted-title", "angle-destination", "wikilink-alias"),
)
def test_prospective_graph_accepts_valid_brackets_inside_link_syntax(
    body: str, target: str
) -> None:
    page = _prospective_page("concepts/alpha.md", body=body)

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: " + target,
            target,
        ),
    )


def test_prospective_graph_ignores_malformed_external_destinations() -> None:
    page = _prospective_page(
        "concepts/alpha.md", body="[x](http://[example.com/doc.md)\n"
    )

    assert validate_prospective_pages((page,), ("sources/a.md",)) == ()


@pytest.mark.parametrize(
    ("body", "target"),
    (
        ("[x](<missing(and.md>)\n", "concepts/missing(and"),
        ("[x](<missing)and.md>)\n", "concepts/missing)and"),
        ('[x](missing.md "title (draft")\n', "concepts/missing"),
        ('[x](missing.md "title ) draft")\n', "concepts/missing"),
        ("[x](missing.md 'title (draft')\n", "concepts/missing"),
        ("[x](missing.md 'title ) draft')\n", "concepts/missing"),
    ),
    ids=(
        "angle-open-parenthesis",
        "angle-close-parenthesis",
        "double-quote-open-parenthesis",
        "double-quote-close-parenthesis",
        "single-quote-open-parenthesis",
        "single-quote-close-parenthesis",
    ),
)
def test_prospective_graph_keeps_destination_context_parentheses_as_content(
    body: str, target: str
) -> None:
    page = _prospective_page("concepts/alpha.md", body=body)

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: " + target,
            target,
        ),
    )


@pytest.mark.parametrize(
    ("body", "target"),
    (
        ("[x](missing'quote.md)\n", "concepts/missing'quote"),
        ('[x](missing"quote.md)\n', 'concepts/missing"quote'),
    ),
    ids=("single-quote-in-path", "double-quote-in-path"),
)
def test_prospective_graph_treats_quotes_in_bare_destinations_as_path_content(
    body: str, target: str
) -> None:
    page = _prospective_page("concepts/alpha.md", body=body)

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: " + target,
            target,
        ),
    )


@pytest.mark.parametrize(
    ("body", "target"),
    (
        (
            "[outer [Missing](missing.md)](../references/present.md)\n",
            "concepts/missing",
        ),
        (
            "[outer [[Missing]]](../references/present.md)\n",
            "missing",
        ),
    ),
    ids=("nested-markdown", "embedded-wikilink"),
)
def test_prospective_graph_keeps_nested_syntax_links_inside_present_outer_targets(
    body: str, target: str
) -> None:
    page = _prospective_page("concepts/alpha.md", body=body)
    present = _prospective_page(
        "references/present.md", category="references", candidate=False
    )

    assert validate_prospective_pages((page, present), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: " + target,
            target,
        ),
    )


@pytest.mark.parametrize(
    "body",
    (
        "[x](https://example.com/[[Missing]].md)\n",
        '[x](present.md "see [Missing](missing.md)")\n',
        "[x](present.md 'see [Missing](missing.md)')\n",
    ),
    ids=("external-destination", "double-quoted-title", "single-quoted-title"),
)
def test_prospective_graph_suppresses_syntax_inside_completed_destinations_and_titles(
    body: str,
) -> None:
    page = _prospective_page("concepts/alpha.md", body=body)
    present = _prospective_page("concepts/present.md", candidate=False)

    assert validate_prospective_pages((page, present), ("sources/a.md",)) == ()


def test_prospective_graph_keeps_present_nested_markdown_and_discards_outer_missing() -> None:
    page = _prospective_page(
        "concepts/alpha.md", body="[outer [Present](present.md)](missing.md)\n"
    )
    present = _prospective_page("concepts/present.md", candidate=False)

    assert validate_prospective_pages((page, present), ("sources/a.md",)) == ()


@pytest.mark.parametrize(
    ("body", "target"),
    (
        ("[outer [Present](present.md)]([[Missing]])\n", "missing"),
        (
            "[outer [Present](present.md)]([Missing](missing.md))\n",
            "concepts/missing",
        ),
    ),
    ids=("wikilink-destination", "markdown-destination"),
)
def test_prospective_graph_retains_destination_syntax_after_discarding_invalid_outer_markdown(
    body: str, target: str
) -> None:
    page = _prospective_page("concepts/alpha.md", body=body)
    present = _prospective_page("concepts/present.md", candidate=False)

    assert validate_prospective_pages((page, present), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: " + target,
            target,
        ),
    )


def test_span_precedence_avoids_pairwise_candidate_scans_for_large_link_streams() -> None:
    source = inspect.getsource(transaction_validation_module._apply_span_precedence)
    body = "".join(f"[label {index}](target-{index}.md)\n" for index in range(5_000))

    assert "any(" not in source
    assert transaction_validation_module._page_links("concepts/alpha.md", body) == tuple(
        f"concepts/target-{index}" for index in range(5_000)
    )


def test_prospective_graph_avoids_duplicate_title_pseudolinks() -> None:
    page = _prospective_page(
        "concepts/alpha.md", body='[Missing](missing.md "see [x](missing.md)")\n'
    )

    assert validate_prospective_pages((page,), ("sources/a.md",)) == (
        ValidationIssue(
            "broken-link",
            "concepts/alpha.md",
            "broken link target: concepts/missing",
            "concepts/missing",
        ),
    )


def test_prospective_graph_reports_duplicate_identity_and_uses_path_qualified_targets() -> None:
    first = _prospective_page("concepts/shared.md")
    second = _prospective_page("references/shared.md", category="references")
    ambiguous = _prospective_page(
        "skills/ambiguous.md", category="skills", body="[[Shared]]\n"
    )
    qualified = _prospective_page(
        "skills/qualified.md", category="skills", body="[[concepts/shared]]\n"
    )

    assert validate_prospective_pages(
        (first, second, ambiguous, qualified), ("sources/a.md",)
    ) == (
        ValidationIssue(
            "duplicate-page-identity",
            "concepts/shared.md",
            "duplicate page identity: shared",
            "shared",
        ),
        ValidationIssue(
            "duplicate-page-identity",
            "references/shared.md",
            "duplicate page identity: shared",
            "shared",
        ),
        ValidationIssue(
            "ambiguous-link",
            "skills/ambiguous.md",
            "ambiguous link target: shared",
            "shared",
        ),
    )


def test_transaction_validation_report_serializes_stably() -> None:
    issue = ValidationIssue(
        "broken-link", "concepts/a.md", "broken link target: missing", "missing"
    )
    report = TransactionValidationReport(
        transaction_id="tx-validate",
        status="fail",
        candidate_pages=("concepts/a.md",),
        deletions=("concepts/old.md",),
        issues=(issue,),
    )

    assert report.as_dict() == {
        "transaction_id": "tx-validate",
        "status": "fail",
        "candidate_pages": ["concepts/a.md"],
        "deletions": ["concepts/old.md"],
        "issues": [issue.as_dict()],
        "warnings": [],
    }


def test_validate_is_read_only_and_reports_all_candidate_issues(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    live = config.vault / "concepts/live.md"
    live.write_text(PAGE.replace("title: A", "title: Live"), encoding="utf-8")
    manager = TransactionManager(config)
    record = manager.begin([source], transaction_id="tx-validate")
    candidate = candidate_page(
        record,
        "concepts/a.md",
        PAGE.replace("updated: 2026-08-07", "updated: invalid")
        + "[[missing]]\n",
    )
    watched = (
        source,
        live,
        candidate,
        record.workspace / "metadata.json",
        record.workspace / "deletions.json",
    )
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in watched
    }

    report = manager.validate("tx-validate")

    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in watched
    }
    assert report.status == "fail"
    assert report.candidate_pages == ("concepts/a.md",)
    assert {issue.code for issue in report.issues} == {
        "frontmatter-updated-invalid",
        "broken-link",
    }
    assert report.warnings == ()
    assert before == after
    assert not (config.vault / "concepts/a.md").exists()
    assert not any((record.workspace / "snapshots").iterdir())


def test_validate_reports_live_link_broken_by_deletion(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    removed = config.vault / "concepts/removed.md"
    removed.write_text(PAGE.replace("title: A", "title: Removed"), encoding="utf-8")
    retained = config.vault / "concepts/retained.md"
    retained.write_text(
        PAGE.replace("title: A", "title: Retained") + "[[removed]]\n",
        encoding="utf-8",
    )
    manager = TransactionManager(config)
    manager.begin([add_source(root)], transaction_id="tx-delete")
    manager.mark_delete("tx-delete", "concepts/removed.md")

    report = manager.validate("tx-delete")

    assert report.status == "fail"
    assert report.candidate_pages == ()
    assert report.deletions == ("concepts/removed.md",)
    assert [
        (issue.code, issue.path, issue.target) for issue in report.issues
    ] == [("broken-link", "concepts/retained.md", "removed")]
    assert removed.read_bytes() == PAGE.replace(
        "title: A", "title: Removed"
    ).encode("utf-8")


def test_validate_includes_journal_operations_in_prospective_graph(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    operation = config.vault / "journal/operations/entry.md"
    operation.parent.mkdir(parents=True)
    operation.write_text(
        PAGE.replace("title: A", "title: Operation Entry")
        .replace("category: concepts", "category: journal")
        + "[[missing operation target]]\n",
        encoding="utf-8",
    )
    manager = TransactionManager(config)
    manager.begin([add_source(root)], transaction_id="tx-operation")

    report = manager.validate("tx-operation")

    assert any(
        issue.code == "broken-link"
        and issue.path == "journal/operations/entry.md"
        for issue in report.issues
    )


def test_validate_uses_candidate_replacement_in_prospective_graph(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/target.md"
    target.write_text(
        PAGE.replace("title: A", "title: Target") + "[[missing]]\n",
        encoding="utf-8",
    )
    retained = config.vault / "concepts/retained.md"
    retained.write_text(
        PAGE.replace("title: A", "title: Retained") + "[[target]]\n",
        encoding="utf-8",
    )
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-replace")
    replacement = PAGE.replace("title: A", "title: Target").replace(
        "# A", "# Replaced"
    )
    candidate_page(record, "concepts/target.md", replacement)

    report = manager.validate("tx-replace")

    assert report.status == "pass"
    assert report.issues == ()
    assert target.read_text(encoding="utf-8").endswith("[[missing]]\n")


def test_validate_does_not_resolve_candidate_link_to_root_view(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    (config.vault / "index.md").write_text("# Index\n", encoding="utf-8")
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-root-target")
    candidate_page(record, "concepts/a.md", PAGE + "[[index]]\n")

    report = manager.validate("tx-root-target")

    assert report.status == "fail"
    assert [(issue.code, issue.path, issue.target) for issue in report.issues] == [
        ("broken-link", "concepts/a.md", "index")
    ]


@pytest.mark.parametrize("name", ["index.md", "log.md", "hot.md"])
def test_validate_ignores_broken_link_from_root_view(
    tmp_path: Path, name: str
) -> None:
    root, config = make_config(tmp_path)
    (config.vault / name).write_text(
        "# Index\n[[missing]]\n", encoding="utf-8"
    )
    manager = TransactionManager(config)
    manager.begin([add_source(root)], transaction_id="tx-root-origin")

    report = manager.validate("tx-root-origin")

    assert report.status == "pass"
    assert report.issues == ()


def test_validate_still_reports_broken_link_from_knowledge_page(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    page = config.vault / "journal" / "daily.md"
    page.parent.mkdir()
    page.write_text(PAGE + "[[missing]]\n", encoding="utf-8")
    manager = TransactionManager(config)
    manager.begin([add_source(root)], transaction_id="tx-knowledge-origin")

    report = manager.validate("tx-knowledge-origin")

    assert [(issue.code, issue.path, issue.target) for issue in report.issues] == [
        ("broken-link", "journal/daily.md", "missing")
    ]


def test_validate_aggregates_invalid_ordinary_candidate_files(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-invalid-files")
    candidate_page(record, "concepts/frontmatter.md", "not frontmatter\n")
    invalid_utf8 = record.candidate_vault / "concepts/encoding.md"
    invalid_utf8.write_bytes(b"\xff\xfe")
    invalid_path = record.candidate_vault / "concepts/not-markdown.txt"
    invalid_path.write_text(PAGE, encoding="utf-8")
    invalid_separator = record.candidate_vault / "concepts/back\\slash.md"
    invalid_separator.write_text(PAGE, encoding="utf-8")

    report = manager.validate("tx-invalid-files")

    assert report.status == "fail"
    assert report.candidate_pages == (
        "concepts/back\\slash.md",
        "concepts/encoding.md",
        "concepts/frontmatter.md",
        "concepts/not-markdown.txt",
    )
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("candidate-path-invalid", "concepts/back\\slash.md"),
        ("candidate-utf8-invalid", "concepts/encoding.md"),
        ("frontmatter-invalid", "concepts/frontmatter.md"),
        ("candidate-path-invalid", "concepts/not-markdown.txt"),
    ]


def test_validate_keeps_unsafe_candidate_topology_fatal(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-unsafe")
    candidate = record.candidate_vault / "concepts/a.md"
    candidate.parent.mkdir(parents=True)
    external = tmp_path / "external.md"
    external.write_text(PAGE, encoding="utf-8")
    candidate.symlink_to(external)

    with pytest.raises(TransactionError, match="candidate.*single-link ordinary file"):
        manager.validate("tx-unsafe")


def test_validate_keeps_preimage_drift_fatal(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-drift")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Candidate"))
    concurrent = PAGE.replace("# A", "# Concurrent")
    target.write_text(concurrent, encoding="utf-8")

    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.validate("tx-drift")

    assert target.read_text(encoding="utf-8") == concurrent
    assert not any((record.workspace / "snapshots").iterdir())


def test_validate_preserves_cjk_candidate_and_source_paths(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root, "资料/来源.md")
    manager = TransactionManager(config)
    record = manager.begin([source], transaction_id="tx-cjk")
    page = PAGE.replace("title: A", "title: 中文页面").replace(
        "sources/a.md", "sources/资料/来源.md"
    )
    candidate_page(record, "concepts/中文页面.md", page)

    report = manager.validate("tx-cjk")

    assert report.status == "pass"
    assert report.candidate_pages == ("concepts/中文页面.md",)
    assert report.as_dict()["candidate_pages"] == ["concepts/中文页面.md"]
    assert record.source_ids == ("sources/资料/来源.md",)
    assert report.issues == ()


def test_commit_rejects_preflight_before_snapshot_or_mutation(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-invalid")
    candidate_page(record, "concepts/a.md", PAGE + "[[missing]]\n")

    with pytest.raises(TransactionError, match="transaction validation failed"):
        manager.commit("tx-invalid")

    payload = json.loads(
        (record.workspace / "metadata.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "active"
    assert payload["snapshot_index"] == {}
    assert not (config.vault / "concepts/a.md").exists()
    assert manager.lock_path.exists()


def test_commit_reads_candidate_bytes_once_and_promotes_them_unchanged(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, log_writer=log_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-read-once")
    candidate = candidate_page(record, "concepts/a.md", PAGE)
    expected = candidate.read_bytes()
    before_mtime = candidate.stat().st_mtime_ns
    original_read = manager._read_single_link_bytes
    candidate_reads = 0

    def counting_read(path: Path, label: str) -> bytes:
        nonlocal candidate_reads
        if path == candidate:
            candidate_reads += 1
        return original_read(path, label)

    monkeypatch.setattr(manager, "_read_single_link_bytes", counting_read)

    manager.commit("tx-read-once", completed_at="2026-08-07T01:00:00Z")

    assert candidate_reads == 1
    assert candidate.read_bytes() == expected
    assert candidate.stat().st_mtime_ns == before_mtime
    assert (config.vault / "concepts/a.md").read_bytes() == expected


def test_retry_validation_failure_preserves_recovery_evidence(
    tmp_path: Path,
    log_writer,
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-retry-invalid")
    candidate = candidate_page(
        record, "concepts/a.md", PAGE.replace("# A", "# Candidate")
    )
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-retry-invalid")
    candidate.write_text(PAGE + "[[missing]]\n", encoding="utf-8")
    metadata = record.workspace / "metadata.json"
    before_metadata = metadata.read_bytes()
    before_payload = json.loads(before_metadata)
    before_snapshots = {
        path.relative_to(record.workspace / "snapshots").as_posix(): path.read_bytes()
        for path in (record.workspace / "snapshots").rglob("*")
        if path.is_file()
    }
    assert before_payload["snapshot_index"]
    assert before_snapshots

    with pytest.raises(TransactionError, match="transaction validation failed"):
        manager.retry("tx-retry-invalid")

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    after_snapshots = {
        path.relative_to(record.workspace / "snapshots").as_posix(): path.read_bytes()
        for path in (record.workspace / "snapshots").rglob("*")
        if path.is_file()
    }
    assert payload["status"] == "failed"
    assert metadata.read_bytes() == before_metadata
    assert payload["snapshot_index"] == before_payload["snapshot_index"]
    assert payload["residual_postimages"] == before_payload["residual_postimages"]
    assert after_snapshots == before_snapshots
    assert target.read_bytes() == PAGE.encode("utf-8")
    assert not manager.lock_path.exists()


def test_retry_reads_candidate_bytes_once(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-retry-read-once")
    candidate = candidate_page(record, "concepts/a.md", PAGE)
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-retry-read-once")
    manager.log_writer = log_writer(config)
    original_read = manager._read_single_link_bytes
    candidate_reads = 0

    def counting_read(path: Path, label: str) -> bytes:
        nonlocal candidate_reads
        if path == candidate:
            candidate_reads += 1
        return original_read(path, label)

    monkeypatch.setattr(manager, "_read_single_link_bytes", counting_read)

    manager.retry("tx-retry-read-once", completed_at="2026-08-07T02:00:00Z")

    assert candidate_reads == 1
    assert (config.vault / "concepts/a.md").read_bytes() == PAGE.encode("utf-8")


def test_retry_promotes_cached_bytes_when_candidate_changes_after_preflight(
    tmp_path: Path,
    log_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(
        config, log_writer=log_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-retry-cached")
    candidate = candidate_page(record, "concepts/a.md", PAGE)
    cached_bytes = candidate.read_bytes()
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-retry-cached")
    manager.log_writer = log_writer(config)
    original_clear = manager._clear_snapshot_state
    changed_bytes = (PAGE + "[[missing-after-preflight]]\n").encode("utf-8")

    def change_candidate_after_preflight(record_value) -> None:
        candidate.write_bytes(changed_bytes)
        original_clear(record_value)

    monkeypatch.setattr(manager, "_clear_snapshot_state", change_candidate_after_preflight)

    manager.retry("tx-retry-cached", completed_at="2026-08-07T02:00:00Z")

    assert candidate.read_bytes() == changed_bytes
    assert (config.vault / "concepts/a.md").read_bytes() == cached_bytes


def test_retry_preflight_omits_known_failed_writer_residual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    extra = config.vault / "references/writer-extra.md"
    calls = 0

    def writer(change):
        nonlocal calls
        calls += 1
        operation = append_operation(
            config.vault / "log.md", change, root=config.vault,
            lock_path=config.root.parent / ".operation-log.lock",
        )
        if calls == 1:
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text(
                PAGE.replace("category: concepts", "category: references")
                + "[[missing]]\n",
                encoding="utf-8",
            )
            raise OSError("writer failed")
        return operation

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-retry-residual")
    candidate_page(record, "concepts/a.md")
    original_restore = manager._restore_snapshot_index
    cleanup_failed = False

    def fail_initial_cleanup(record_value, index) -> None:
        nonlocal cleanup_failed
        if not cleanup_failed and "references/writer-extra.md" in index:
            cleanup_failed = True
            raise TransactionError("simulated writer cleanup failure")
        original_restore(record_value, index)

    monkeypatch.setattr(manager, "_restore_snapshot_index", fail_initial_cleanup)
    with pytest.raises(TransactionError, match="writer restore failed"):
        manager.commit("tx-retry-residual")
    assert cleanup_failed
    assert extra.exists()
    failed_metadata = (record.workspace / "metadata.json").read_bytes()
    monkeypatch.setattr(manager, "_restore_snapshot_index", original_restore)

    result = manager.retry(
        "tx-retry-residual", completed_at="2026-08-07T02:00:00Z"
    )

    assert result.created == ("concepts/a.md",)
    assert not extra.exists()
    assert manager.load("tx-retry-residual").status == "complete"
    assert (record.workspace / "metadata.json").read_bytes() != failed_metadata


def test_retry_preflight_reads_absent_deletion_target_from_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    removed = config.vault / "concepts/removed.md"
    removed.write_text(
        PAGE.replace("title: A", "title: Removed"), encoding="utf-8"
    )
    calls = 0

    def writer(change):
        nonlocal calls
        calls += 1
        operation = append_operation(
            config.vault / "log.md", change, root=config.vault,
            lock_path=config.root.parent / ".operation-log.lock",
        )
        if calls == 1:
            raise OSError("writer failed")
        return operation

    manager = TransactionManager(config, log_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-retry-deletion")
    manager.mark_delete("tx-retry-deletion", "concepts/removed.md")
    original_restore = manager._restore_snapshot_index
    rollback_failed = False

    def leave_deletion_unrestored(record_value, index) -> None:
        nonlocal rollback_failed
        if not rollback_failed and "concepts/removed.md" in index:
            rollback_failed = True
            original_restore(
                record_value,
                {
                    relative: stored
                    for relative, stored in index.items()
                    if relative != "concepts/removed.md"
                },
            )
            raise TransactionError("simulated deletion rollback failure")
        original_restore(record_value, index)

    monkeypatch.setattr(manager, "_restore_snapshot_index", leave_deletion_unrestored)
    with pytest.raises(TransactionError, match="restore failed"):
        manager.commit("tx-retry-deletion")
    assert rollback_failed
    assert not removed.exists()
    payload = json.loads((record.workspace / "metadata.json").read_text())
    assert payload["snapshot_index"]["concepts/removed.md"] is not None
    assert payload["residual_postimages"]["concepts/removed.md"] is None
    monkeypatch.setattr(manager, "_restore_snapshot_index", original_restore)

    result = manager.retry(
        "tx-retry-deletion", completed_at="2026-08-07T02:00:00Z"
    )

    assert result.removed == ("concepts/removed.md",)
    assert not removed.exists()
    assert manager.load("tx-retry-deletion").status == "complete"

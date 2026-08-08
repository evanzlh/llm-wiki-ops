from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import transaction as transaction_module
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.transaction import (
    TransactionError,
    TransactionManager,
    validate_candidate_path,
)

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
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / ".skills").mkdir()
    path = root / ".obsidian-wiki" / "config.toml"
    path.write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    (root / "wiki" / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
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


@pytest.fixture
def operation_writer():
    calls: list[object] = []

    def factory(config, *, fail: bool = False):
        def write(change):
            calls.append(change)
            if fail:
                raise OSError("operation disk full")
            path = (
                config.vault
                / "journal"
                / "operations"
                / "2026"
                / "08"
                / f"{change.transaction_id}.md"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Test operation\n", encoding="utf-8")
            return path

        return write

    factory.calls = calls
    return factory


def candidate_page(record, relative: str, text: str = PAGE) -> Path:
    path = record.candidate_vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


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

    assert set(record.preimages) == {".manifest.json", "concepts/existing.md"}
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
    assert len(record.preimages) == 5_001
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
    tmp_path: Path, status: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    metadata_path = record.workspace / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["status"] = status
    metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TransactionError, match="retain|discard|status"):
        manager.abort("tx-1")

    assert record.workspace.is_dir()
    assert manager.lock_path.exists()


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


def test_abort_fails_closed_when_safe_rmtree_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    monkeypatch.setattr(shutil.rmtree, "avoids_symlink_attacks", False)

    with pytest.raises(TransactionError, match="symlink-safe|safe recursive"):
        manager.abort("tx-1")

    assert record.workspace.is_dir()
    assert manager.lock_path.is_file()


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

    with pytest.raises(TransactionError, match="remove transaction workspace"):
        manager.abort("tx-1")

    assert marker.read_text(encoding="utf-8") == "keep"
    assert record.workspace.is_symlink()
    assert manager.lock_path.is_file()


def test_commit_promotes_candidate_and_updates_manifest(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
    assert result.operation_path == "journal/operations/2026/08/tx-1.md"
    assert not manager.lock_path.exists()
    assert record.workspace.exists()
    assert manager.load("tx-1").status == "complete"


def test_default_operation_writer_fails_and_rolls_back(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(
        TransactionError,
        match="rolled back.*operation writer is not configured",
    ):
        manager.commit("tx-1")

    assert not (config.vault / "concepts/a.md").exists()
    assert manager.load("tx-1").status == "failed"


def test_commit_refuses_target_changed_after_begin(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md", PAGE.replace("# A", "# Candidate"))
    concurrent = PAGE.replace("# A", "# Concurrent")
    target.write_text(concurrent, encoding="utf-8")

    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == concurrent
    assert manager.load("tx-1").status == "active"
    assert manager.lock_path.exists()


def test_source_drift_does_not_count_as_output_target_drift(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    source.write_text("new source bytes", encoding="utf-8")

    manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    entry = ShardedManifest(config).load("sources/a.md")
    assert entry is not None
    assert (
        entry.content_hash
        == transaction_module.TransactionManager._hash_single_link_file(
            source, "source"
        )
    )


def test_failed_manifest_write_rolls_back_promoted_page_and_shard(
    tmp_path: Path, operation_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manifest = ShardedManifest(config)
    manifest.upsert(source, pages=[], compiled_at="2026-08-06T00:00:00Z")
    shard = manifest.entry_path("sources/a.md")
    original_shard = shard.read_bytes()
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(
        config, operation_writer=operation_writer(config, fail=True)
    )
    record = manager.begin([source], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*operation disk full"):
        manager.commit("tx-1")

    assert ShardedManifest(config).load("sources/a.md") is None
    assert not (config.vault / "concepts/a.md").exists()


def test_multiple_candidates_mid_promotion_failure_rolls_back_all_pages(
    tmp_path: Path, operation_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    candidate_page(
        record,
        "references/z.md",
        PAGE.replace("category: concepts", "category: references"),
    )
    original = manager._promote_candidate
    calls = 0

    def fail_second(candidate: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second promotion failed")
        original(candidate, target)

    monkeypatch.setattr(manager, "_promote_candidate", fail_second)

    with pytest.raises(TransactionError, match="rolled back.*second promotion failed"):
        manager.commit("tx-1")

    assert not (config.vault / "concepts/a.md").exists()
    assert not (config.vault / "references/z.md").exists()
    assert manager.load("tx-1").status == "failed"


def test_failed_transaction_can_retry_after_fault_is_removed(
    tmp_path: Path, operation_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(
        config, operation_writer=operation_writer(config, fail=True)
    )
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")
    target = config.vault / "concepts/a.md"
    target.write_text("foreign output", encoding="utf-8")

    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.retry("tx-1")
    assert target.read_text(encoding="utf-8") == "foreign output"

    target.unlink()
    manager._acquire_lock("foreign", "2026-08-07T02:00:00Z")
    with pytest.raises(TransactionError, match="foreign"):
        manager.retry("tx-1")


def test_restore_and_discard_are_explicit_and_idempotent(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    updated = PAGE.replace("# A", "# Updated")
    candidate_page(record, "concepts/a.md", updated)
    result = manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    manager.restore("tx-1")
    manager.restore("tx-1")

    assert target.read_text(encoding="utf-8") == PAGE
    assert not (config.vault / result.operation_path).exists()
    assert manager.load("tx-1").status == "restored"
    manager.discard("tx-1")
    manager.discard("tx-1")
    assert manager.list_transactions() == []


def test_restore_refuses_postimage_drift(tmp_path: Path, operation_writer) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    manager.commit("tx-1")
    target = config.vault / "concepts/a.md"
    target.write_text("later work", encoding="utf-8")

    with pytest.raises(TransactionError, match="changed after transaction completed"):
        manager.restore("tx-1")

    assert target.read_text(encoding="utf-8") == "later work"
    assert manager.load("tx-1").status == "complete"


def test_restore_failed_transaction_marks_restored_without_overwriting(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(
        config, operation_writer=operation_writer(config, fail=True)
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
        "journal/operations/fake.md",
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


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_commit_rejects_nonordinary_candidate_files(
    tmp_path: Path, operation_writer, kind: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
    tmp_path: Path, operation_writer, text: str
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
        "journal/operations/fake.md",
        "concepts/not-markdown.txt",
    ],
)
def test_mark_delete_rejects_unsafe_and_control_paths(tmp_path: Path, raw: str) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    manager.begin([add_source(root)], transaction_id="tx-1")

    with pytest.raises(TransactionError, match="deletion|control|reserved|markdown"):
        manager.mark_delete("tx-1", raw)


def test_mark_delete_rejects_duplicate_and_commit_removes_page(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    target = config.vault / "concepts/a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    manager.begin([add_source(root)], transaction_id="tx-1")
    manager.mark_delete("tx-1", "concepts/a.md")
    with pytest.raises(TransactionError, match="duplicate"):
        manager.mark_delete("tx-1", "concepts/a.md")

    result = manager.commit("tx-1")

    assert result.removed == ("concepts/a.md",)
    assert not target.exists()


def test_multi_source_page_relationships_are_preserved(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")
    shared = PAGE.replace("  - sources/a.md\n", "  - sources/a.md\n  - sources/b.md\n")
    existing = config.vault / "concepts/shared.md"
    existing.write_text(shared, encoding="utf-8")
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
    tmp_path: Path, operation_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    source_a = add_source(root, "a.md")
    source_b = add_source(root, "b.md")
    updated = config.vault / "references/z.md"
    updated.parent.mkdir(parents=True)
    updated.write_text(PAGE, encoding="utf-8")
    removed = config.vault / "concepts/z.md"
    removed.write_text(PAGE, encoding="utf-8")
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([source_b, source_a], transaction_id="tx-1")
    candidate_page(record, "references/z.md", PAGE.replace("# A", "# Z"))
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
    change = operation_writer.calls[-1]
    assert change.completed_at == "2026-08-07T01:00:00Z"
    assert change.source_ids == ("sources/a.md", "sources/b.md")


def test_complete_metadata_failure_rolls_back_everything(
    tmp_path: Path, operation_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
    assert not (config.vault / "journal/operations/2026/08/tx-1.md").exists()
    assert manager.load("tx-1").status == "failed"


def test_discard_refuses_active_symlinked_workspace_and_foreign_lock(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
        ("operation_path", "concepts/not-an-operation.md", "operation"),
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
    operation_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(
        config, operation_writer=operation_writer(config, fail=True)
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
    operation_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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


def test_operation_writer_runs_after_pages_and_all_manifest_shards(
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
        path = config.vault / "journal/operations/tx-1.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# operation\n", encoding="utf-8")
        return path

    manager = TransactionManager(config, operation_writer=writer)
    record = manager.begin([source_b, source_a], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    manager.commit("tx-1")


def test_operation_writer_partial_creation_is_removed_on_failure(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)

    def writer(_change):
        path = config.vault / "journal/operations/partial.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("partial\n", encoding="utf-8")
        raise OSError("operation interrupted")

    manager = TransactionManager(config, operation_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*operation interrupted"):
        manager.commit("tx-1")

    assert not (config.vault / "journal/operations/partial.md").exists()
    assert not (config.vault / "concepts/a.md").exists()


@pytest.mark.parametrize(
    "relative",
    [
        "index.md",
        ".manifest/sources/forged.json",
        ".obsidian/workspace.md",
        "journal/operations/forged.md",
    ],
)
def test_commit_revalidates_tampered_deletions_file(
    tmp_path: Path,
    operation_writer,
    relative: str,
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    (record.workspace / "deletions.json").write_text(
        json.dumps([relative], indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        TransactionError, match="deletion.*(control|reserved|operations|markdown)"
    ):
        manager.commit("tx-1")

    assert operation_writer.calls == []
    assert (
        json.loads((record.workspace / "metadata.json").read_text())["status"]
        == "active"
    )


def test_operation_writer_overwrite_then_return_restores_existing_page(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    operation = config.vault / "journal/operations/existing.md"
    operation.parent.mkdir(parents=True)
    operation.write_text("original operation\n", encoding="utf-8")

    def writer(_change):
        operation.write_text("corrupted operation\n", encoding="utf-8")
        return operation

    manager = TransactionManager(config, operation_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")

    assert operation.read_text(encoding="utf-8") == "original operation\n"
    assert not (config.vault / "concepts/a.md").exists()
    assert manager.load("tx-1").status == "failed"


def test_operation_writer_overwrite_then_raise_restores_existing_page(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    operation = config.vault / "journal/operations/existing.md"
    operation.parent.mkdir(parents=True)
    operation.write_text("original operation\n", encoding="utf-8")

    def writer(_change):
        operation.write_text("corrupted operation\n", encoding="utf-8")
        raise OSError("writer failed after overwrite")

    manager = TransactionManager(config, operation_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*writer failed"):
        manager.commit("tx-1")

    assert operation.read_text(encoding="utf-8") == "original operation\n"
    assert not (config.vault / "concepts/a.md").exists()


def test_operation_writer_extra_page_then_return_rolls_back_all_new_pages(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    returned = config.vault / "journal/operations/returned.md"
    extra = config.vault / "journal/operations/extra.md"

    def writer(_change):
        returned.parent.mkdir(parents=True, exist_ok=True)
        returned.write_text("returned\n", encoding="utf-8")
        extra.write_text("extra\n", encoding="utf-8")
        return returned

    manager = TransactionManager(config, operation_writer=writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")

    with pytest.raises(TransactionError, match="rolled back.*exactly one"):
        manager.commit("tx-1")

    assert not returned.exists()
    assert not extra.exists()
    assert not (config.vault / "concepts/a.md").exists()


def test_retry_refuses_drift_at_prior_failed_operation_artifact(
    tmp_path: Path,
    operation_writer,
) -> None:
    root, config = make_config(tmp_path)
    partial = config.vault / "journal/operations/partial.md"

    def failing_writer(_change):
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text("partial\n", encoding="utf-8")
        raise OSError("operation interrupted")

    manager = TransactionManager(config, operation_writer=failing_writer)
    record = manager.begin([add_source(root)], transaction_id="tx-1")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")
    assert not partial.exists()
    partial.write_text("foreign artifact\n", encoding="utf-8")
    manager.operation_writer = operation_writer(config)

    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.retry("tx-1")

    assert partial.read_text(encoding="utf-8") == "foreign artifact\n"
    assert manager.load("tx-1").status == "failed"


def test_a_only_transaction_cannot_update_existing_a_b_page(
    tmp_path: Path,
    operation_writer,
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
    manager = TransactionManager(config, operation_writer=operation_writer(config))
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
    assert operation_writer.calls == []
    assert manager.load("tx-1").status == "active"


def test_a_only_transaction_cannot_delete_existing_a_b_page(
    tmp_path: Path,
    operation_writer,
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
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    manager.begin([source_a], transaction_id="tx-1")
    manager.mark_delete("tx-1", "concepts/shared.md")

    with pytest.raises(TransactionError, match="existing page.*sources/b.md"):
        manager.commit("tx-1")

    assert target.read_text(encoding="utf-8") == shared
    assert shard_a.read_bytes() == before_a
    assert shard_b.read_bytes() == before_b
    assert operation_writer.calls == []
    assert manager.load("tx-1").status == "active"


def test_a_b_transaction_can_remove_b_relationship_and_rebuild_both_shards(
    tmp_path: Path,
    operation_writer,
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
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([source_b, source_a], transaction_id="tx-1")
    updated = PAGE.replace("# A", "# Updated without B")
    candidate_page(record, "concepts/shared.md", updated)

    manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")

    assert target.read_text(encoding="utf-8") == updated
    assert manifest.load("sources/a.md").pages == ("concepts/shared.md",)
    assert manifest.load("sources/b.md").pages == ()

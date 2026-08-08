from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import transaction as transaction_module
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.transaction import TransactionError, TransactionManager


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
        assert text == json.dumps(
            json.loads(text), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"


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
    manager.begin(
        [source], transaction_id="owner-1", started_at="2000-01-01T00:00:00Z"
    )

    with pytest.raises(TransactionError, match="owner-1"):
        manager.begin([source], transaction_id="tx-2")

    assert json.loads(manager.lock_path.read_text(encoding="utf-8"))[
        "transaction_id"
    ] == "owner-1"


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


def test_transactions_root_and_workspace_must_not_be_symlinks(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    external = tmp_path / "external-transactions"
    external.mkdir()
    config.local_state.mkdir(parents=True)
    (config.local_state / "transactions").symlink_to(
        external, target_is_directory=True
    )

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

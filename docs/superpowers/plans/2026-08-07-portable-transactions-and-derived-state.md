# Portable Transactions and Derived State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make portable writes recoverable and merge-friendly by staging agent output in local transactions, promoting it with preimage checks and rollback, recording one immutable operation page, and treating `hot.md` as local derived state.

**Architecture:** A logical repository lock and transaction workspace span the multi-command agent workflow. Agents write candidate vault-relative pages under `.obsidian-wiki/local/transactions/<id>/wiki`; the CLI validates and promotes them, rebuilds source/page manifest relationships, and writes the operation journal last. Stable index/log query pages are never rewritten, while a small fingerprint sidecar invalidates ignored `wiki/hot.md` when branch or authoritative state changes.

**Tech Stack:** Python dataclasses/pathlib, exclusive file creation, SHA-256 preimages, atomic `Path.replace`, rollback snapshots, Git read-only branch inspection, argparse, Markdown Agent Skills, pytest

**Depends on:** `2026-08-07-sharded-manifest-and-check.md`

---

## File map

- `obsidian_wiki/transaction.py`: transaction metadata, logical lock, candidate workspace, conflict detection, promotion, rollback, recovery.
- `obsidian_wiki/operations.py`: immutable operation-page rendering and validation.
- `obsidian_wiki/local_state.py`: authoritative-state fingerprint and local `hot.md` invalidation/marking.
- `obsidian_wiki/portable_check.py`: validate operation pages and ignore them in ordinary source/page bidirectional checks.
- `obsidian_wiki/cli.py`: `transaction` and `hot` command groups.
- `tests/test_transaction.py`, `tests/test_operations.py`, `tests/test_local_state.py`: focused behavior.
- `.skills/llm-wiki/SKILL.md` and all write-capable skills: one canonical portable write protocol; personal mode remains unchanged.
- `docs/architecture.md`, `docs/cli.md`, `docs/configuration.md`: transaction and derived-state reference.

### Task 1: Begin, list, and abort local transactions safely

**Files:**
- Create: `obsidian_wiki/transaction.py`
- Create: `tests/test_transaction.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
# tests/test_transaction.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
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


def test_begin_creates_candidate_workspace_and_lock(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    manager = TransactionManager(config)
    record = manager.begin([source], transaction_id="tx-1", started_at="2026-08-07T00:00:00Z")
    assert record.transaction_id == "tx-1"
    assert record.source_ids == ("sources/a.md",)
    assert record.candidate_vault == config.local_state / "transactions" / "tx-1" / "wiki"
    assert record.candidate_vault.is_dir()
    assert json.loads(manager.lock_path.read_text())["transaction_id"] == "tx-1"


def test_second_transaction_is_rejected_while_lock_exists(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    manager = TransactionManager(config)
    manager.begin([source], transaction_id="tx-1")
    with pytest.raises(TransactionError, match="tx-1"):
        manager.begin([source], transaction_id="tx-2")


def test_abort_removes_transaction_and_releases_lock(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    manager = TransactionManager(config)
    manager.begin([source], transaction_id="tx-1")
    manager.abort("tx-1")
    assert manager.list_transactions() == []
    assert not manager.lock_path.exists()


@pytest.mark.parametrize("transaction_id", ["../escape", "/tmp/tx", "nested/tx", "nested\\tx"])
def test_begin_rejects_unsafe_transaction_id(tmp_path: Path, transaction_id: str) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    with pytest.raises(TransactionError, match="transaction ID"):
        TransactionManager(config).begin([source], transaction_id=transaction_id)
```

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `uv run pytest tests/test_transaction.py -q`

Expected: FAIL because `transaction.py` does not exist.

- [ ] **Step 3: Implement transaction records and exclusive lock creation**

Define:

```python
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


class TransactionManager:
    """Own one portable repository's local transaction workspaces and lock."""
```

Add methods `__init__(self, config: PortableConfig)`, `begin(self, sources: list[Path], *, transaction_id: str | None = None, started_at: str | None = None) -> TransactionRecord`, `load(self, transaction_id: str) -> TransactionRecord`, `list_transactions(self) -> list[TransactionRecord]`, and `abort(self, transaction_id: str) -> None`. Persist status as one of `active`, `promoting`, `failed`, `complete`, or `restored`; reject unknown values when loading metadata.

Use `<local_state>/write.lock`, `<local_state>/transactions/<id>/metadata.json`, `<workspace>/wiki/`, `<workspace>/snapshots/`, and `<workspace>/deletions.json`. Create the lock with mode `x`; its canonical JSON contains ID and start time. Never auto-break an existing lock based only on age.

User-supplied transaction IDs must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Generated IDs use a UTC compact timestamp plus random lowercase hex. Resolve the final workspace and require it below `local_state/transactions` before creating any directory.

At begin:

- resolve and validate Source IDs with `ShardedManifest`;
- reject duplicate or missing sources;
- snapshot SHA-256 preimages for every regular file currently under the vault except ignored `hot.md`, `.obsidian/**` workspace files, and local state;
- write metadata atomically;
- clean up the new workspace and lock if initialization fails.

At abort, if the lock exists require it to belong to the requested transaction; if it is absent, permit cleanup only for a `failed` transaction. Delete only that validated workspace below `local_state/transactions`, then unlink an owned lock. Path containment checks precede every recursive removal. A completed transaction is retained for recovery and can only be removed by the explicit `discard` operation introduced in Task 2.

- [ ] **Step 4: Run lifecycle tests**

Run: `uv run pytest tests/test_transaction.py -q`

Expected: PASS.

- [ ] **Step 5: Commit transaction lifecycle**

```bash
git add obsidian_wiki/transaction.py tests/test_transaction.py
git commit -m "feat: stage portable writes in local transactions"
```

### Task 2: Validate candidates, detect conflicts, promote, and roll back

**Files:**
- Modify: `obsidian_wiki/transaction.py`
- Modify: `tests/test_transaction.py`

- [ ] **Step 1: Add failing promotion/conflict/rollback tests**

Append tests that use valid required frontmatter:

```python
PAGE = '''---
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
'''


def manager_with_test_operation(config):
    def write_test_operation(change):
        path = config.vault / "journal" / "operations" / "2026" / "08" / "test-operation.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Test operation\n", encoding="utf-8")
        return path

    return TransactionManager(config, operation_writer=write_test_operation)


def test_commit_promotes_candidate_and_updates_manifest(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    manager = manager_with_test_operation(config)
    record = manager.begin([source], transaction_id="tx-1")
    candidate = record.candidate_vault / "concepts" / "a.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(PAGE, encoding="utf-8")
    result = manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")
    assert (config.vault / "concepts" / "a.md").read_text(encoding="utf-8") == PAGE
    entry = ShardedManifest(config).load("sources/a.md")
    assert entry is not None
    assert entry.pages == ("concepts/a.md",)
    assert result.created == ("concepts/a.md",)
    assert not manager.lock_path.exists()


def test_commit_refuses_target_changed_after_begin(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    target = config.vault / "concepts" / "a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = manager_with_test_operation(config)
    record = manager.begin([source], transaction_id="tx-1")
    candidate = record.candidate_vault / "concepts" / "a.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(PAGE.replace("# A", "# Candidate"), encoding="utf-8")
    target.write_text(PAGE.replace("# A", "# Concurrent"), encoding="utf-8")
    with pytest.raises(TransactionError, match="changed after transaction began"):
        manager.commit("tx-1")
    assert "# Concurrent" in target.read_text(encoding="utf-8")


def test_failed_manifest_write_rolls_back_promoted_page(tmp_path: Path, monkeypatch) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    target = config.vault / "concepts" / "a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = manager_with_test_operation(config)
    record = manager.begin([source], transaction_id="tx-1")
    candidate = record.candidate_vault / "concepts" / "a.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(PAGE.replace("# A", "# Candidate"), encoding="utf-8")
    monkeypatch.setattr(ShardedManifest, "upsert", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit("tx-1")
    assert target.read_text(encoding="utf-8") == PAGE
    assert manager.load("tx-1").status == "failed"


def test_failed_transaction_can_retry_after_fault_is_removed(tmp_path: Path, monkeypatch) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    manager = manager_with_test_operation(config)
    record = manager.begin([source], transaction_id="tx-1")
    candidate = record.candidate_vault / "concepts" / "a.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(PAGE, encoding="utf-8")
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


def test_restore_and_discard_are_explicit_and_idempotent(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("source", encoding="utf-8")
    target = config.vault / "concepts" / "a.md"
    target.write_text(PAGE, encoding="utf-8")
    manager = manager_with_test_operation(config)
    record = manager.begin([source], transaction_id="tx-1")
    candidate = record.candidate_vault / "concepts" / "a.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(PAGE.replace("# A", "# Updated"), encoding="utf-8")
    manager.commit("tx-1", completed_at="2026-08-07T01:00:00Z")
    manager.restore("tx-1")
    manager.restore("tx-1")
    assert target.read_text(encoding="utf-8") == PAGE
    manager.discard("tx-1")
    assert manager.list_transactions() == []
```

Import `ShardedManifest` in the test module.

- [ ] **Step 2: Run promotion tests and verify failure**

Run: `uv run pytest tests/test_transaction.py -q`

Expected: FAIL because commit is missing.

- [ ] **Step 3: Implement safe candidate validation**

Add `validate_candidate_path` and `validate_candidate_page`:

- candidate paths must be relative, remain below candidate `wiki/`, end in `.md`, and not target reserved files (`index.md`, `log.md`, `hot.md`, `.manifest.json`) or control directories;
- frontmatter must contain all six required fields;
- each portable source must be one of the transaction's Source IDs;
- `journal/operations` cannot be supplied by the agent;
- symlinks and non-regular candidate files are rejected;
- deletions are recorded only through `mark_delete(transaction_id, vault_relative_path)` and cannot target control files.

- [ ] **Step 4: Implement preimage checks, backups, promotion, and manifest rebuild**

Add:

```python
@dataclass(frozen=True)
class CommitResult:
    transaction_id: str
    created: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]
    operation_path: str
```

Add `commit(self, transaction_id: str, *, completed_at: str | None = None) -> CommitResult`, `retry(self, transaction_id: str, *, completed_at: str | None = None) -> CommitResult`, `restore(self, transaction_id: str) -> None`, `discard(self, transaction_id: str) -> None`, and `mark_delete(self, transaction_id: str, relative_path: str) -> None` to `TransactionManager`.

Extend `TransactionManager.__init__` with an optional `operation_writer` callable. The default raises `TransactionError("operation writer is not configured")` until Task 3 wires the real operation module; focused Task 2 tests use `manager_with_test_operation` so page/manifest rollback can be delivered and committed independently.

Commit sequence:

1. load metadata and verify lock ownership;
2. enumerate/validate candidates and deletions;
3. compare current target hashes with begin-time preimages;
4. copy every existing target and affected manifest shard into `snapshots/`, recording absent preimages separately;
5. promote candidate pages through same-directory temp files and `Path.replace`; apply deletions;
6. scan all live knowledge-page frontmatter to compute the complete page set for each transaction Source ID, preserving pages supported by other sources;
7. call `ShardedManifest.upsert` for each transaction source with the transaction's single resolved `completed_at` value;
8. create the operation entry through Task 3's renderer;
9. record postimage hashes for every promoted page, manifest shard, deletion, and operation path; mark metadata complete, release the lock, and retain the completed transaction directory until explicit cleanup.

On any exception after snapshots begin, restore replaced/deleted files and manifest shards byte-for-byte, remove paths that did not exist before, mark the transaction `failed`, keep its workspace for diagnosis, release the lock, and raise `TransactionError` containing “rolled back”.

`retry` accepts only `failed` transactions, reacquires the exclusive repository lock, verifies that live targets still equal their recorded preimages, clears only the previous attempt's generated snapshot index, and executes the same commit sequence against the retained candidates. `restore` accepts `failed`, `complete`, or already `restored` transactions and acquires the same exclusive lock. For a complete transaction it first requires every affected live file to equal the transaction's recorded postimage, so it never overwrites later work; it then reapplies the recorded original/absent state byte-for-byte, removes the operation page created by that transaction when it was absent at begin, and marks status `restored`. A second restore is a no-op. `discard` accepts `failed`, `complete`, or `restored`, refuses an active/promoting transaction, and removes only that transaction workspace after containment checks. None of these commands invokes Git.

- [ ] **Step 5: Run transaction tests**

Run: `uv run pytest tests/test_transaction.py -q`

Expected: PASS using the injected test operation writer.

- [ ] **Step 6: Commit promotion and rollback**

```bash
git add obsidian_wiki/transaction.py tests/test_transaction.py
git commit -m "feat: promote portable transactions with rollback"
```

### Task 3: Write immutable operation pages last

**Files:**
- Create: `obsidian_wiki/operations.py`
- Create: `tests/test_operations.py`
- Modify: `obsidian_wiki/transaction.py`
- Modify: `obsidian_wiki/portable_check.py`
- Modify: `tests/test_portable_check.py`

- [ ] **Step 1: Write failing operation rendering tests**

```python
# tests/test_operations.py
from pathlib import Path

from obsidian_wiki.operations import OperationChange, render_operation, write_operation


def test_render_operation_has_required_frontmatter_and_sorted_changes() -> None:
    change = OperationChange(
        transaction_id="tx-1",
        completed_at="2026-08-07T07:30:00Z",
        source_ids=("sources/b.md", "sources/a.md"),
        created=("concepts/b.md", "concepts/a.md"),
        updated=("references/z.md",),
        removed=(),
    )
    text = render_operation(change)
    assert "category: journal" in text
    assert "  - sources/a.md\n  - sources/b.md" in text
    assert text.index("[[concepts/a]]") < text.index("[[concepts/b]]")
    assert "model" not in text.lower()
    assert "agent:" not in text.lower()


def test_write_operation_uses_unique_immutable_path(tmp_path: Path) -> None:
    change = OperationChange("tx-1", "2026-08-07T07:30:00Z", ("sources/a.md",), (), (), ())
    first = write_operation(tmp_path, change, suffix="a81f")
    second = write_operation(tmp_path, change, suffix="b92e")
    assert first != second
    assert first.as_posix().endswith("journal/operations/2026/08/20260807T073000Z-a81f.md")
    assert first.is_file() and second.is_file()
```

- [ ] **Step 2: Run operation tests and verify failure**

Run: `uv run pytest tests/test_operations.py -q`

Expected: FAIL because `operations.py` does not exist.

- [ ] **Step 3: Implement deterministic operation rendering**

Define immutable `OperationChange`, a pure `render_operation`, `operation_path`, `write_operation`, and `validate_operation`. Render required frontmatter (`title`, `category`, `tags`, `sources`, `created`, `updated`) and Created/Updated/Removed sections with sorted wikilinks. Use UTC filename timestamps plus a four-or-more-character lowercase hex suffix. Write with exclusive mode `x`; a collision is retried with a new suffix, never overwritten.

Do not include operator, agent, model, API provider, or tool metadata.

- [ ] **Step 4: Integrate operation-last semantics and checker validation**

Call `write_operation` only after pages and manifest shards succeed. If operation writing fails, transaction rollback restores prior state.

In `portable_check.py`, exclude `journal/operations/**` from ordinary page/source bidirectional membership, then validate each operation with `validate_operation`: filename timestamp/suffix, required frontmatter, operation tag, source IDs, and safe listed page paths. Duplicate operation paths are impossible by Git, but duplicate transaction IDs are errors.

- [ ] **Step 5: Run operation, transaction, and check tests**

Run: `uv run pytest tests/test_operations.py tests/test_transaction.py tests/test_portable_check.py -q`

Expected: PASS.

- [ ] **Step 6: Commit operation journaling**

```bash
git add obsidian_wiki/operations.py obsidian_wiki/transaction.py obsidian_wiki/portable_check.py tests/test_operations.py tests/test_transaction.py tests/test_portable_check.py
git commit -m "feat: record immutable portable operations"
```

### Task 4: Invalidate and mark local hot state

**Files:**
- Create: `obsidian_wiki/local_state.py`
- Create: `tests/test_local_state.py`

- [ ] **Step 1: Write failing local-state tests**

```python
# tests/test_local_state.py
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.local_state import hot_status, mark_hot_current


@pytest.fixture
def config_fixture(tmp_path: Path):
    root = tmp_path / "knowledge"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "wiki").mkdir()
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
    (root / "wiki" / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    (root / "wiki" / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
    )
    return load_portable_config(
        path,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )


def test_hot_is_stale_until_marked(config_fixture) -> None:
    config = config_fixture
    assert hot_status(config)["stale"] is True
    config.vault.joinpath("hot.md").write_text("# Hot\n", encoding="utf-8")
    mark_hot_current(config)
    assert hot_status(config)["stale"] is False


def test_page_change_invalidates_and_removes_hot(config_fixture) -> None:
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("# Hot\n", encoding="utf-8")
    mark_hot_current(config)
    page = config.vault / "concepts" / "a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# changed\n", encoding="utf-8")
    status = hot_status(config, invalidate=True)
    assert status["stale"] is True
    assert not hot.exists()
```

- [ ] **Step 2: Run local-state tests and verify failure**

Run: `uv run pytest tests/test_local_state.py -q`

Expected: FAIL because `local_state.py` does not exist.

- [ ] **Step 3: Implement authoritative fingerprinting**

Expose `authoritative_fingerprint(config: PortableConfig) -> str`, `hot_status(config: PortableConfig, *, invalidate: bool = False) -> dict[str, object]`, and `mark_hot_current(config: PortableConfig) -> None`.

Hash sorted tuples of repository-relative path plus file SHA-256 for knowledge pages (excluding `hot.md` and Obsidian local state), manifest marker/shards, and operation entries. Include `git rev-parse --abbrev-ref HEAD` or detached `HEAD` when available. Store canonical JSON at `<local_state>/hot-state.json` with fingerprint and `hot.md` hash.

`hot_status` is stale when `hot.md` or sidecar is absent, fingerprint differs, or hot hash differs. With `invalidate=True`, delete only `wiki/hot.md` after containment validation; never write semantic content. `mark_hot_current` requires an existing hot file and records the current fingerprint.

- [ ] **Step 4: Run local-state tests**

Run: `uv run pytest tests/test_local_state.py -q`

Expected: PASS.

- [ ] **Step 5: Commit local hot invalidation**

```bash
git add obsidian_wiki/local_state.py tests/test_local_state.py
git commit -m "feat: invalidate portable hot state locally"
```

### Task 5: Expose transaction and hot CLI commands

**Files:**
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_transaction.py`
- Modify: `tests/test_local_state.py`

- [ ] **Step 1: Add failing CLI tests**

Add subprocess tests for:

```bash
obsidian-wiki transaction begin --source sources/a.md --json
obsidian-wiki transaction list --json
obsidian-wiki transaction delete <id> concepts/obsolete.md
obsidian-wiki transaction commit <id> --json
obsidian-wiki transaction retry <id> --json
obsidian-wiki transaction restore <id>
obsidian-wiki transaction discard <id>
obsidian-wiki transaction abort <id>
obsidian-wiki hot status --json
obsidian-wiki hot mark-current
```

Assert every command fails outside portable mode, JSON output is parseable, begin prints the candidate-vault path, commit/retry/restore/discard never change Git commits or remotes, and restore/list are idempotent where documented.

- [ ] **Step 2: Run CLI cases and verify failure**

Run: `uv run pytest tests/test_transaction.py tests/test_local_state.py -q`

Expected: FAIL because command groups are missing.

- [ ] **Step 3: Register nested command groups**

Use required subparsers under `transaction` and `hot`. Resolve portable config from CWD for every subcommand. `begin --source` accepts one or more repository-relative or filesystem paths, resolves them through `ShardedManifest`, and prints JSON including `transaction_id` and `candidate_vault`. `commit` and `retry` print created/updated/removed and operation path. `delete`, `restore`, `discard`, `abort`, and `list` call only `TransactionManager`. `list --json` includes status and retained recovery paths. `hot status` defaults to invalidating stale hot state; `hot mark-current` records the Agent-written hot file.

Catch `ConfigError`, `ManifestError`, and `TransactionError` in the main CLI error boundary and return `1` without a traceback.

- [ ] **Step 4: Run CLI and module tests**

Run: `uv run pytest tests/test_transaction.py tests/test_operations.py tests/test_local_state.py -q`

Expected: PASS.

- [ ] **Step 5: Commit CLI surface**

```bash
git add obsidian_wiki/cli.py tests/test_transaction.py tests/test_local_state.py
git commit -m "feat: expose portable transaction and hot commands"
```

### Task 6: Route write skills through the portable transaction protocol

**Files:**
- Modify: `.skills/llm-wiki/SKILL.md`
- Modify: `.skills/claude-history-ingest/SKILL.md`
- Modify: `.skills/codex-history-ingest/SKILL.md`
- Modify: `.skills/copilot-history-ingest/SKILL.md`
- Modify: `.skills/cross-linker/SKILL.md`
- Modify: `.skills/daily-update/SKILL.md`
- Modify: `.skills/hermes-history-ingest/SKILL.md`
- Modify: `.skills/openclaw-history-ingest/SKILL.md`
- Modify: `.skills/pi-history-ingest/SKILL.md`
- Modify: `.skills/tag-taxonomy/SKILL.md`
- Modify: `.skills/wiki-agent/SKILL.md`
- Modify: `.skills/wiki-capture/SKILL.md`
- Modify: `.skills/wiki-dashboard/SKILL.md`
- Modify: `.skills/wiki-dedup/SKILL.md`
- Modify: `.skills/wiki-import/SKILL.md`
- Modify: `.skills/wiki-ingest/SKILL.md`
- Modify: `.skills/wiki-lint/SKILL.md`
- Modify: `.skills/wiki-rebuild/SKILL.md`
- Modify: `.skills/wiki-research/SKILL.md`
- Modify: `.skills/wiki-stage-commit/SKILL.md`
- Modify: `.skills/wiki-status/SKILL.md`
- Modify: `.skills/wiki-synthesize/SKILL.md`
- Modify: `.skills/wiki-update/SKILL.md`
- Modify: `.skills/wiki-query/SKILL.md`
- Modify: `.skills/wiki-narrate/SKILL.md`
- Modify: `.skills/wiki-digest/SKILL.md`
- Create: `tests/test_portable_write_protocol.py`

- [ ] **Step 1: Write a failing protocol-routing test**

```python
# tests/test_portable_write_protocol.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRITE_SKILLS = (
    "claude-history-ingest", "codex-history-ingest", "copilot-history-ingest",
    "cross-linker", "daily-update", "hermes-history-ingest", "openclaw-history-ingest",
    "pi-history-ingest", "tag-taxonomy", "wiki-agent", "wiki-capture", "wiki-dashboard",
    "wiki-dedup", "wiki-import", "wiki-ingest", "wiki-lint", "wiki-rebuild",
    "wiki-research", "wiki-stage-commit", "wiki-status", "wiki-synthesize", "wiki-update",
)


def test_every_write_skill_routes_portable_writes_to_transactions() -> None:
    for name in WRITE_SKILLS:
        text = (ROOT / ".skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "Portable Write Protocol" in text, name


def test_canonical_protocol_owns_begin_and_commit_commands() -> None:
    text = (ROOT / ".skills/llm-wiki/SKILL.md").read_text(encoding="utf-8")
    assert "obsidian-wiki transaction begin" in text
    assert "obsidian-wiki transaction commit" in text
    assert "Do not commit, push, or open a pull request" in text
    assert "index.md and log.md are stable" in text
    assert "hot.md is local and ignored" in text
```

- [ ] **Step 2: Run the routing test and verify failure**

Run: `uv run pytest tests/test_portable_write_protocol.py -q`

Expected: FAIL because write skills still edit live pages and central files directly.

- [ ] **Step 3: Add the canonical Portable Write Protocol**

In `.skills/llm-wiki/SKILL.md`, specify this executable flow:

1. run `obsidian-wiki transaction begin --source sources/a.md sources/b.md --json` (with the actual one-or-more source paths);
2. write only candidate pages below the returned `candidate_vault`, preserving final vault-relative paths;
3. declare removals with `transaction delete`;
4. run `transaction commit <id> --json` only after reviewing candidate frontmatter/links;
5. on failure, inspect `transaction list`, then explicitly `retry`, `restore`, or `abort`/`discard` the retained transaction;
6. never edit manifest shards or operation pages manually;
7. never update stable `index.md`/`log.md` during ordinary portable writes;
8. regenerate ignored `hot.md` when `hot status` reports stale, then run `hot mark-current`;
9. never commit, push, or open a PR automatically.

State that personal mode retains its existing direct-write, central manifest/log/index/hot behavior.

- [ ] **Step 4: Route each write skill to the canonical block**

Each listed write skill must branch immediately after config resolution: in portable mode, follow the Portable Write Protocol and suppress its legacy direct manifest/index/log/hot and pre-write Git commit steps; in personal mode, retain current behavior. History routers that delegate to `wiki-ingest` may say “Portable writes are owned by wiki-ingest's Portable Write Protocol” instead of duplicating commands.

For `wiki-stage-commit`, portable mode lists and commits/aborts CLI transactions; `_staging/` remains personal-mode behavior. For destructive skills, portable mode uses transaction snapshots and never creates a Git commit. For `wiki-status`, write modes use a transaction, while read-only status merely reports active transactions first.

Update query/narrate/digest read paths to call `obsidian-wiki hot status --json`; when stale, either rebuild the local semantic snapshot from page summaries/recent operation entries or proceed without it, never treat stale hot state as authoritative.

- [ ] **Step 5: Run protocol and existing skill contract tests**

Run: `uv run pytest tests/test_portable_write_protocol.py tests/test_pre_write_snapshot_docs.py tests/test_wiki_narrate_docs.py tests/test_context_pack_docs.py -q`

Expected: PASS; personal pre-write snapshot assertions remain under the personal-mode branch.

- [ ] **Step 6: Commit skill routing**

```bash
git add .skills tests/test_portable_write_protocol.py
git commit -m "docs: route portable writes through transactions"
```

### Task 7: Document and verify the collaboration state model

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/configuration.md`
- Modify: `docs/cli.md`
- Modify: `docs/agents.md`
- Modify: `README.md`
- Modify: `README_ZH.md`

- [ ] **Step 1: Document authoritative versus derived state**

Add the exact tracked/ignored table from the design, transaction lifecycle/commands, recovery behavior, operation path format, stable built-in-query index/log behavior, and hot invalidation flow. State that an edited/dirty source worktree is expected and allowed; only preimage drift in affected output targets blocks promotion. State that Git diff and PR review are the content boundary and the CLI never commits or pushes portable changes.

- [ ] **Step 2: Run the complete fourth-plan verification**

Run: `uv run pytest tests/test_transaction.py tests/test_operations.py tests/test_local_state.py tests/test_portable_check.py tests/test_portable_write_protocol.py tests/test_pre_write_snapshot_docs.py tests/test_wiki_narrate_docs.py tests/test_readme_sync.py -q`

Expected: PASS.

Run: `uv run pytest -q`

Expected: full suite PASS.

- [ ] **Step 3: Verify ordinary transaction output does not touch hot central files**

Create a temporary portable repository, begin/commit one candidate page, and run:

```bash
git diff --name-only
```

Expected changed paths are the knowledge page, its manifest shard, and one `journal/operations` page. `wiki/index.md`, `wiki/log.md`, and `wiki/hot.md` are absent from the diff.

- [ ] **Step 4: Commit collaboration documentation**

```bash
git add docs README.md README_ZH.md
git commit -m "docs: explain portable transactions and derived state"
```

# Single Operation Log and Tracked Hot View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-operation Markdown pages with one tracked, canonical `wiki/log.md`, track `wiki/hot.md`, and exclude all root control/derived pages from knowledge-graph semantics.

**Architecture:** `obsidian_wiki.operations` becomes a pure canonical codec plus a guarded atomic updater for the single log. `TransactionManager` includes `log.md` in its normal preimage/postimage and rollback set and writes it last; local status reads operation records from that log. Setup/check own the initial tracked `log.md` and `hot.md`, while graph consumers exclude `index.md`, `log.md`, and `hot.md`.

**Tech Stack:** Python 3.8+, dataclasses, pathlib, descriptor-safe filesystem operations, Markdown, pytest, uv, Git.

---

## File and responsibility map

- `obsidian_wiki/operations.py`: canonical operation data, complete-log render/parse,
  safe single-log read, and atomic append.
- `obsidian_wiki/transaction.py`: make `log.md` the final guarded transaction
  mutation and include it in recovery metadata.
- `obsidian_wiki/cli.py`: expose `log_path` and make `hot status` read-only.
- `obsidian_wiki/local_state.py`: parse recent operations from `log.md`; retain the
  hot sidecar but remove tracked-file invalidation.
- `obsidian_wiki/portable.py`: scaffold canonical `log.md` and `hot.md`, stop
  scaffolding `journal/operations`, and stop ignoring `hot.md`.
- `obsidian_wiki/portable_check.py`: validate the single log and the three root
  control/derived files.
- `obsidian_wiki/graph_analysis.py` and `obsidian_wiki/transaction.py`: exclude all
  three root pages from graph semantics.
- `tests/test_operations.py`: focused canonical log codec and write safety.
- `tests/test_transaction.py`: transaction ordering, rollback, recovery metadata,
  and CLI payload.
- `tests/test_local_state.py`: recent-log inputs and non-destructive hot freshness.
- `tests/test_portable_setup.py`, `tests/test_portable_check.py`,
  `tests/test_portable_git.py`: tracked scaffold and repository checks.
- `tests/test_graph_analysis.py`, `tests/test_lint.py`: root-page graph exclusion.
- `README.md`, `README_ZH.md`, active `docs/*.md`, packaged bootstrap and runtime
  skills: publish the new contract.

Historical specs and plans under `docs/superpowers/` remain historical records and
are not rewritten, except for this implementation plan.

### Task 1: Replace the operation-page codec with a single-log codec

**Files:**
- Rewrite: `obsidian_wiki/operations.py`
- Rewrite: `tests/test_operations.py`

- [ ] **Step 1: Write failing canonical render/parse tests**

Replace operation-path and immutable-page tests with these public-contract tests:

```python
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from obsidian_wiki.operations import (
    EMPTY_OPERATION_LOG,
    OperationChange,
    OperationError,
    append_operation,
    append_operation_text,
    parse_operation_log,
    render_operation_log,
)


def _change(transaction_id: str = "tx-1") -> OperationChange:
    return OperationChange(
        transaction_id=transaction_id,
        completed_at="2026-08-13T09:15:00Z",
        source_ids=("sources/example.md",),
        created=("concepts/example.md",),
        updated=(),
        removed=(),
    )


def test_empty_operation_log_is_canonical() -> None:
    assert EMPTY_OPERATION_LOG == (
        "---\n"
        "title: Wiki Operation Log\n"
        "operation_log_schema: 1\n"
        "---\n\n"
        "# Wiki Operation Log\n"
    )
    assert parse_operation_log(EMPTY_OPERATION_LOG) == ()


def test_operation_log_round_trip_canonicalizes_lists() -> None:
    change = OperationChange(
        transaction_id="tx-1",
        completed_at="2026-08-13T09:15:00Z",
        source_ids=("sources/z.md", "sources/a.md", "sources/a.md"),
        created=("concepts/z.md", "concepts/a.md"),
        updated=(),
        removed=(),
    )
    rendered = render_operation_log((change,))
    parsed = parse_operation_log(rendered)
    assert parsed == (
        OperationChange(
            transaction_id="tx-1",
            completed_at="2026-08-13T09:15:00Z",
            source_ids=("sources/a.md", "sources/z.md"),
            created=("concepts/a.md", "concepts/z.md"),
            updated=(),
            removed=(),
        ),
    )
    assert "- `sources/a.md`\n- `sources/z.md`" in rendered
    assert "- [[concepts/a]]\n- [[concepts/z]]" in rendered
    assert "### Updated\n\n- None" in rendered


def test_append_rejects_duplicate_transaction_id() -> None:
    text = append_operation_text(EMPTY_OPERATION_LOG, _change())
    with pytest.raises(OperationError, match="duplicate transaction ID"):
        append_operation_text(text, _change())


@pytest.mark.parametrize(
    "text, message",
    [
        ("# not the log\n", "header"),
        (EMPTY_OPERATION_LOG + "\nextra\n", "operation heading"),
    ],
)
def test_parse_rejects_noncanonical_log(text: str, message: str) -> None:
    with pytest.raises(OperationError, match=message):
        parse_operation_log(text)


def test_operation_change_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        _change().transaction_id = "changed"  # type: ignore[misc]
```

- [ ] **Step 2: Run the codec tests to verify they fail**

Run:

```bash
uv run --with pytest python -m pytest tests/test_operations.py -q
```

Expected: FAIL during import because `EMPTY_OPERATION_LOG`,
`parse_operation_log`, and `append_operation_text` do not exist.

- [ ] **Step 3: Implement the canonical pure codec**

Keep `OperationError`, frozen `OperationChange`, `_timestamp`, `_safe_relative`, and
`_canonical_change`. Remove operation filenames, per-page frontmatter, directory
creation, and immutable-page validation. Add this public shape:

```python
EMPTY_OPERATION_LOG = (
    "---\n"
    "title: Wiki Operation Log\n"
    "operation_log_schema: 1\n"
    "---\n\n"
    "# Wiki Operation Log\n"
)


def _source_section(values: tuple[str, ...]) -> str:
    return "\n".join(f"- `{value}`" for value in values)


def _page_section(values: tuple[str, ...]) -> str:
    if not values:
        return "- None"
    return "\n".join(
        f"- [[{PurePosixPath(value).with_suffix('').as_posix()}]]"
        for value in values
    )


def render_operation_block(change: OperationChange) -> str:
    item = _canonical_change(change)
    return (
        f"\n## {item.completed_at} · {item.transaction_id}\n\n"
        f"### Sources\n\n{_source_section(item.source_ids)}\n\n"
        f"### Created\n\n{_page_section(item.created)}\n\n"
        f"### Updated\n\n{_page_section(item.updated)}\n\n"
        f"### Removed\n\n{_page_section(item.removed)}\n"
    )


def render_operation_log(changes: tuple[OperationChange, ...]) -> str:
    canonical = tuple(_canonical_change(change) for change in changes)
    completed = [change.completed_at for change in canonical]
    if completed != sorted(completed):
        raise OperationError("operation records must use ascending completion order")
    transaction_ids = [change.transaction_id for change in canonical]
    if len(transaction_ids) != len(set(transaction_ids)):
        raise OperationError("operation log contains a duplicate transaction ID")
    return EMPTY_OPERATION_LOG + "".join(
        render_operation_block(change) for change in canonical
    )


def append_operation_text(text: str, change: OperationChange) -> str:
    records = parse_operation_log(text)
    canonical = _canonical_change(change)
    if canonical.transaction_id in {item.transaction_id for item in records}:
        raise OperationError("operation log contains a duplicate transaction ID")
    if records and canonical.completed_at < records[-1].completed_at:
        raise OperationError("operation record is older than the current log tail")
    return text + render_operation_block(canonical)
```

Extend `_canonical_change` to reject an empty `source_ids` tuple with
`OperationError("operation sources must not be empty")`; the single log preserves
the existing transaction rule that every operation has source authority.

Implement `parse_operation_log(text)` as an exact inverse: require the exact header,
split only on `\n\n## ` record boundaries, require the heading regular expression
`(UTC timestamp) · (transaction ID)`, require Sources/Created/Updated/Removed once
and in order, accept `- None` only for change lists, convert wikilinks back to `.md`,
run `_canonical_change`, require each parsed list already equals its canonical form,
then require `render_operation_log(result) == text`. This last equality rejects all
extra prose, reordered entries, aliases, anchors, duplicate entries, and whitespace
drift without adding another permissive grammar.

- [ ] **Step 4: Add failing guarded-write tests**

```python
def test_append_operation_atomically_replaces_log(tmp_path: Path) -> None:
    log = tmp_path / "log.md"
    log.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    returned = append_operation(log, _change(), root=tmp_path)
    assert returned == log
    assert parse_operation_log(log.read_text(encoding="utf-8")) == (_change(),)


def test_append_operation_rejects_hard_link(tmp_path: Path) -> None:
    original = tmp_path / "outside.md"
    original.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    log = tmp_path / "log.md"
    os.link(original, log)
    with pytest.raises(OperationError, match="single-link ordinary file"):
        append_operation(log, _change(), root=tmp_path)
    assert original.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
```

- [ ] **Step 5: Implement the guarded atomic updater**

Add:

```python
def append_operation(path: Path, change: OperationChange, *, root: Path) -> Path:
    """Validate and atomically replace one contained canonical operation log."""
```

Reuse the existing module's no-follow ordinary-file read, file/directory identity
checks, complete-write loop, `fsync`, temporary single-link file, and cleanup logic.
The updater must:

1. require `path` to be exactly `root / "log.md"` after lexical containment;
2. read a stable UTF-8 single-link ordinary preimage;
3. compute `append_operation_text(preimage, change)`;
4. create an exclusive `.operation-log-<hex>.tmp` beside `log.md`;
5. flush it, recheck the bound parent and original identity, and `os.replace` it;
6. flush the parent and parse the installed bytes again; and
7. raise `OperationError` without overwriting a concurrently changed target.

Do not retain the old `_open_operation_parent`, `operation_path`, `write_operation`,
`validate_operation_text(relative, text)`, or per-operation quarantine paths.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run --with pytest python -m pytest tests/test_operations.py -q
git diff --check
```

Expected: all operation tests PASS and `git diff --check` prints nothing.

Commit:

```bash
git add obsidian_wiki/operations.py tests/test_operations.py
git commit -m "refactor: use one canonical operation log"
```

### Task 2: Make `log.md` the final transaction mutation

**Files:**
- Modify: `obsidian_wiki/transaction.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_transaction.py`

- [ ] **Step 1: Write failing commit-result and ordering tests**

Adapt the existing operation-writer fixtures to a `log_writer` that edits only
`wiki/log.md`, then add:

```python
def test_log_writer_runs_after_pages_and_manifest_shards(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    (config.vault / "log.md").write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    events: list[str] = []

    def log_writer(change: OperationChange) -> Path:
        assert (config.vault / "concepts/a.md").exists()
        assert ShardedManifest(config).load("sources/a.md") is not None
        events.append("log")
        return append_operation(
            config.vault / "log.md",
            change,
            root=config.vault,
        )

    manager = TransactionManager(config, log_writer=log_writer)
    record = manager.begin([add_source(root)], transaction_id="tx-log-last")
    candidate_page(record, "concepts/a.md")
    result = manager.commit(record.transaction_id)
    assert events == ["log"]
    assert result.log_path == "log.md"
    assert parse_operation_log(
        (config.vault / "log.md").read_text(encoding="utf-8")
    )[-1].transaction_id == record.transaction_id


def test_log_writer_failure_restores_pages_manifest_and_exact_log(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    log = config.vault / "log.md"
    log.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    before = log.read_bytes()

    def failing_writer(change: OperationChange) -> Path:
        append_operation(log, change, root=config.vault)
        raise OSError("log post-write failure")

    manager = TransactionManager(config, log_writer=failing_writer)
    record = manager.begin([add_source(root)], transaction_id="tx-log-failure")
    candidate_page(record, "concepts/a.md")
    with pytest.raises(TransactionError, match="rolled back"):
        manager.commit(record.transaction_id)
    assert log.read_bytes() == before
    assert not (config.vault / "concepts/a.md").exists()
```

Update CLI payload assertions to expect `"log_path": "log.md"` and reject
`operation_path`.

Update `make_config` and other direct transaction fixtures to create
`EMPTY_OPERATION_LOG` at `wiki/log.md`; transaction tests must not rely on the setup
task that lands later in this plan.

- [ ] **Step 2: Run the focused transaction tests to verify they fail**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_transaction.py::test_log_writer_runs_after_pages_and_manifest_shards \
  tests/test_transaction.py::test_log_writer_failure_restores_pages_manifest_and_exact_log -q
```

Expected: FAIL because `TransactionManager` has no `log_writer` parameter and
`CommitResult` has no `log_path`.

- [ ] **Step 3: Replace operation-tree transaction state with the normal log target**

Make these public type changes:

```python
@dataclass(frozen=True)
class CommitResult:
    transaction_id: str
    created: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]
    log_path: str


def __init__(
    self,
    config: PortableConfig,
    *,
    log_writer: Callable[[OperationChange], Path] | None = None,
) -> None:
    ...
    self.log_writer = log_writer or (
        lambda change: append_operation(
            self.config.vault / "log.md",
            change,
            root=self.config.vault,
        )
    )
```

Change `_affected_preimage_paths` to always add `"log.md"`. Remove
`operation_guard`, `operation_path`, and `operation_paths` from `_METADATA_FIELDS`
and every active/promoting/failed/complete payload. Remove
`_snapshot_operation_tree`, `_operation_tree_state`, `_operation_tree_diff`, and
`_validate_operation_guard_path`.

Immediately after all page/manifest promotions and source-preimage verification:

```python
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
payload.update(
    {
        "snapshot_index": dict(sorted(snapshot_index.items())),
        "writer_guard": writer_before,
        "writer_prepared": True,
    }
)
self._write_metadata(record.workspace, payload)
log_path = Path(self.log_writer(change))
log_relative = self._validate_log_result(log_path)
if log_relative != "log.md":
    raise TransactionError("log writer must return the portable log.md")
parsed = parse_operation_log(
    self._read_single_link_bytes(log_path, "operation log").decode("utf-8")
)
if not parsed or parsed[-1] != change:
    raise TransactionError("log writer did not append the expected operation")
```

Exclude `log.md` from `_writer_guard_state`, because it is the one authorized writer
target, while retaining it in the normal affected snapshot. Require the writer guard
to prove no other vault file changed. Build `postimage_paths` from `affected`, which
already contains `log.md`, and return `CommitResult(..., log_path="log.md")`.

Update complete/failed/retry/restore/discard metadata validation to use only the
existing affected path, snapshot, postimage, residual, and writer fields. There is no
legacy metadata branch because the old format is unreleased.

- [ ] **Step 4: Update the CLI result contract**

Change `_commit_payload` and human output:

```python
def _commit_payload(result) -> dict[str, object]:
    return {
        "transaction_id": result.transaction_id,
        "created": list(result.created),
        "updated": list(result.updated),
        "removed": list(result.removed),
        "log_path": result.log_path,
    }
```

The text result ends with `; log.md` instead of an operation-page path.

- [ ] **Step 5: Replace operation-specific transaction regressions**

Delete tests whose only behavior is allocating, preserving, or cleaning multiple
`journal/operations` files. Retain their safety intent as single-log tests covering:

- concurrent `log.md` preimage drift before promotion;
- writer modification of any path other than `log.md` rolls back;
- writer replacement followed by raise restores the exact log preimage;
- malformed installed log rolls back;
- duplicate transaction append rolls back;
- interrupted complete metadata validates the `log.md` postimage; and
- retry refuses owner drift at `log.md`.

For each injected writer, call `append_operation(log, change, root=vault)` before
the intended extra mutation or exception; do not create fake operation directories.

- [ ] **Step 6: Run transaction tests and commit**

Run:

```bash
uv run --with pytest python -m pytest tests/test_transaction.py -q
git diff --check
```

Expected: all transaction tests PASS and no whitespace errors.

Commit:

```bash
git add obsidian_wiki/transaction.py obsidian_wiki/cli.py tests/test_transaction.py
git commit -m "refactor: append operation log in transactions"
```

### Task 3: Read and validate operations from `log.md`

**Files:**
- Modify: `obsidian_wiki/local_state.py`
- Modify: `obsidian_wiki/portable_check.py`
- Modify: `tests/test_local_state.py`
- Modify: `tests/test_portable_check.py`

- [ ] **Step 1: Write failing recent-input and portable-check tests**

```python
def _log_change(transaction_id: str, completed_at: str) -> OperationChange:
    return OperationChange(
        transaction_id=transaction_id,
        completed_at=completed_at,
        source_ids=("sources/input.md",),
        created=(),
        updated=(),
        removed=(),
    )


def test_hot_inputs_reads_recent_operations_from_single_log(config_fixture) -> None:
    log = config_fixture.vault / "log.md"
    first = _log_change("tx-1", "2026-08-13T09:15:00Z")
    second = _log_change("tx-2", "2026-08-13T10:15:00Z")
    log.write_text(render_operation_log((first, second)), encoding="utf-8")
    payload = hot_inputs(config_fixture, page_limit=0, operation_limit=1)
    assert payload["operations"] == [
        {
            "transaction_id": "tx-2",
            "completed_at": "2026-08-13T10:15:00Z",
            "source_ids": list(second.source_ids),
            "created": list(second.created),
            "updated": list(second.updated),
            "removed": list(second.removed),
        }
    ]


def test_check_reports_malformed_single_operation_log(tmp_path: Path) -> None:
    _root, config, _source, _page, _entry = valid_repo(tmp_path)
    (config.vault / "log.md").write_text("broken\n", encoding="utf-8")
    report = check_portable_repo(config)
    assert any(issue["code"] == "operation-log-invalid" for issue in report["issues"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_local_state.py::test_hot_inputs_reads_recent_operations_from_single_log \
  tests/test_portable_check.py::test_check_reports_malformed_single_operation_log -q
```

Expected: FAIL because local state still scans `journal/operations` and check still
expects a query-only stable log.

- [ ] **Step 3: Make the authoritative snapshot treat `log.md` specially**

In `_authoritative_files`, add `relative == Path("log.md")` as an authoritative
selection. Continue excluding `index.md` and `hot.md` from the fingerprint. Remove
the `journal/operations` directory special case.

In `_authoritative_snapshot`, handle `relative == Path("log.md")` first:

```python
content, text = _read_ordinary_text_bytes(
    path, "operation log", root=config.root
)
try:
    changes = parse_operation_log(text)
except OperationError as exc:
    raise LocalStateError(f"invalid operation log: log.md: {exc}") from exc
for change in changes:
    outside_source = next(
        (
            source_id
            for source_id in change.source_ids
            if not _below_source_roots(source_id, source_roots)
        ),
        None,
    )
    if outside_source is not None:
        raise LocalStateError(
            "invalid operation log: Source ID is outside configured source roots: "
            + outside_source
        )
    if operation_limit:
        item = (
            change.completed_at,
            change.transaction_id,
            {
                "transaction_id": change.transaction_id,
                "completed_at": change.completed_at,
                "source_ids": list(change.source_ids),
                "created": list(change.created),
                "updated": list(change.updated),
                "removed": list(change.removed),
            },
        )
        if len(records) < operation_limit:
            heapq.heappush(records, item)
        elif item[:2] > records[0][:2]:
            heapq.heapreplace(records, item)
```

Do not validate `log.md` as a knowledge page. Preserve full-log fail-closed parsing
even when `operation_limit == 0`.

- [ ] **Step 4: Replace `_check_operations` with `_check_operation_log`**

The check reads exactly `config.vault / "log.md"`, requires a contained single-link
ordinary UTF-8 file, parses it with `parse_operation_log`, and resolves every Source
ID through `ShardedManifest.source_path`. Report all failures as
`operation-log-invalid` at the repository-relative log path. Remove operation
directory walking and duplicate-ID tracking because the canonical parser owns that
validation.

Change `_check_stable_views` to validate only the exact stable `index.md` template.
Add a `_check_hot_view` structural check that requires `hot.md` to be a contained
single-link ordinary UTF-8 Markdown file but does not evaluate freshness or exact
contents.

- [ ] **Step 5: Update and run local-state/check regression suites**

Rewrite operation fixtures in `tests/test_local_state.py` to populate `log.md` with
`render_operation_log`. Rewrite operation check fixtures similarly. Remove tests for
operation filenames, nested directories, duplicate per-file transaction IDs, and
operation-directory symlinks; retain equivalent malformed-log, duplicate-record,
unsafe-file, out-of-root Source ID, zero-limit, deterministic ordering, and concurrent
read tests.

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_local_state.py tests/test_portable_check.py -q
git diff --check
```

Expected: both suites PASS.

Commit:

```bash
git add obsidian_wiki/local_state.py obsidian_wiki/portable_check.py \
  tests/test_local_state.py tests/test_portable_check.py
git commit -m "refactor: read operations from log page"
```

### Task 4: Track `hot.md` and make stale checks non-destructive

**Files:**
- Modify: `obsidian_wiki/portable.py`
- Modify: `obsidian_wiki/local_state.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_portable_setup.py`
- Modify: `tests/test_local_state.py`
- Modify: `tests/test_portable_git.py`

- [ ] **Step 1: Write failing setup and stale-status tests**

```python
def test_setup_creates_tracked_hot_and_does_not_ignore_it(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    root = work / "knowledge"
    work.mkdir()
    result = run_cli(home, work, "setup", str(root))
    assert result.returncode == 0, result.stderr
    hot = root / "wiki/hot.md"
    assert hot.read_text(encoding="utf-8") == "---\ntitle: Wiki Hot View\n---\n\n# Wiki Hot View\n"
    assert "wiki/hot.md" not in (root / ".gitignore").read_text(
        encoding="utf-8"
    ).splitlines()


def test_stale_hot_status_never_removes_tracked_file(config_fixture) -> None:
    hot = config_fixture.vault / "hot.md"
    before = hot.read_bytes()
    status = hot_status(config_fixture)
    assert status["stale"] is True
    assert hot.read_bytes() == before
```

Update the CLI status test to assert the stale file still exists after
`obsidian-wiki hot status --json`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_setup.py::test_setup_creates_tracked_hot_and_does_not_ignore_it \
  tests/test_local_state.py::test_stale_hot_status_never_removes_tracked_file -q
```

Expected: FAIL because setup does not create `hot.md` and the current CLI invalidates
stale hot state.

- [ ] **Step 3: Scaffold and preserve the tracked hot view**

In `portable.py`, add:

```python
_HOT = """---
title: Wiki Hot View
---

# Wiki Hot View
"""
```

Replace `journal/operations` with `journal` in `PORTABLE_VAULT_DIRS`. In
`scaffold_portable_vault`, write `_HOT` with `_write_text_if_missing`. In existing
repository validation, parse `wiki/log.md` with `parse_operation_log` instead of
requiring its bytes to equal an empty template. Require `wiki/hot.md` to be an
ordinary single-link UTF-8 file but do not require exact `_HOT` bytes after setup
because the Agent owns its derived content. `render_stable_log()` returns
`EMPTY_OPERATION_LOG`, and `_LOG` is removed from `portable.py` rather than retained
as a second template authority.

Change `render_portable_gitignore` required entries to:

```python
required = (
    *PORTABLE_ROOT_IGNORE,
    f"{prefix}.obsidian/",
    f"{prefix}.trash/",
)
```

Do not remove an owner-authored historical `wiki/hot.md` ignore line on rerun; setup
only stops adding it. Adjust tests to distinguish preservation of owner entries from
new canonical scaffold output.

- [ ] **Step 4: Delete hot invalidation and make the CLI read-only**

Change the public signature to:

```python
def hot_status(config: PortableConfig) -> dict[str, object]:
    """Return whether tracked ``hot.md`` is stale without changing it."""
```

Delete `_invalidated_name`, `_restore_mismatched_hot_at`,
`_restore_mismatched_hot_path`, `_invalidate_hot`, and the `invalidate` branch. Keep
safe metadata/hash reads and the local sidecar. In `cmd_hot_status`, call only
`hot_status(config)`.

Keep `hot.md` excluded from `_authoritative_files`, transaction `_snapshot_preimages`,
and transaction writer guards so refreshing it cannot make its own fingerprint stale
or join a knowledge transaction.

- [ ] **Step 5: Run setup, local-state, and Git tests and commit**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_setup.py tests/test_local_state.py tests/test_portable_git.py -q
git diff --check
```

Expected: all tests PASS; stale checks leave the tracked file untouched.

Commit:

```bash
git add obsidian_wiki/portable.py obsidian_wiki/local_state.py obsidian_wiki/cli.py \
  tests/test_portable_setup.py tests/test_local_state.py tests/test_portable_git.py
git commit -m "feat: track the hot view"
```

### Task 5: Exclude root control and derived pages from every knowledge graph

**Files:**
- Modify: `obsidian_wiki/graph_analysis.py`
- Modify: `obsidian_wiki/graphrag.py`
- Modify: `obsidian_wiki/transaction.py`
- Modify: `tests/test_graph_analysis.py`
- Modify: `tests/test_graphrag.py`
- Modify: `tests/test_transaction.py`
- Modify: `tests/test_lint.py`

- [ ] **Step 1: Write failing graph-boundary tests**

```python
def test_root_control_views_are_not_graph_nodes(vault: Path) -> None:
    (vault / "concepts").mkdir()
    (vault / "concepts/topic.md").write_text(
        "---\ntags:\n  - topic\n---\n# Topic\n", encoding="utf-8"
    )
    for name in ("index.md", "log.md", "hot.md"):
        (vault / name).write_text(
            f"# {name}\n\n[[concepts/topic]]\n", encoding="utf-8"
        )
    outgoing, _tags = parse_vault_graph(vault)
    assert set(outgoing) == {"topic"}
    assert outgoing["topic"] == []
```

Add a transaction prospective-graph test that writes missing links into all three
root files and proves validation ignores them while still rejecting a missing link
from `concepts/topic.md`.

Add a GraphRAG regression with valid frontmatter in the three root files and one
knowledge page:

```python
def test_graphrag_excludes_root_control_views(vault: Path) -> None:
    (vault / "concepts").mkdir()
    _page(vault / "concepts", "topic", title="Topic")
    _page(vault, "index", title="index.md")
    _page(vault, "log", title="log.md")
    _page(vault, "hot", title="hot.md")
    index = build_index(vault)
    assert {item["path"] for item in index.values()} == {"concepts/topic.md"}
```

Use the existing GraphRAG `_page` helper and its actual required frontmatter rather
than introducing a second fixture format.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_graph_analysis.py::test_root_control_views_are_not_graph_nodes \
  tests/test_graphrag.py -k root_control_views \
  tests/test_transaction.py -k root_control_views -q
```

Expected: FAIL because graph analysis scans every root Markdown file and transaction
prospective graphs still include index/log.

- [ ] **Step 3: Apply one explicit root exclusion policy**

In `graph_analysis.py`:

```python
_ROOT_CONTROL_PAGES = frozenset({"index.md", "log.md", "hot.md"})

pages = [
    page
    for page in scan_markdown_files(vault, skip_dirs=skip_dirs)
    if page.relative not in _ROOT_CONTROL_PAGES
]
```

Apply the same `_ROOT_CONTROL_PAGES` filename exclusion to `graphrag.build_index`
before its first-pass frontmatter parsing. `context_pack.py` already excludes these
three names through `SKIP_FILES`; retain its existing regression.

In `transaction.py`, remove `_TRACKED_ROOT_GRAPH_PAGES`. Do not preload any root
Markdown files in `_prospective_pages`, and make `_is_graph_page_path` return `False`
for every one-part path:

```python
if len(path.parts) == 1:
    return False
```

Remove all remaining `journal/operations` exclusions because the directory is no
longer part of the scaffold; category pages below `journal/` otherwise remain normal
knowledge pages.

Ensure lint topology tests establish that broken links in `log.md`/`hot.md` do not
become knowledge findings. If lint already excludes all root control pages, add the
regression without changing its implementation.

- [ ] **Step 4: Run graph and validation suites and commit**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_graph_analysis.py tests/test_graphrag.py tests/test_context_pack.py \
  tests/test_lint.py tests/test_transaction.py -q
git diff --check
```

Expected: all suites PASS and root pages are absent from graph statistics.

Commit:

```bash
git add obsidian_wiki/graph_analysis.py obsidian_wiki/graphrag.py \
  obsidian_wiki/transaction.py tests/test_graph_analysis.py \
  tests/test_graphrag.py tests/test_transaction.py tests/test_lint.py
git commit -m "fix: exclude root views from knowledge graphs"
```

### Task 6: Update active documentation, bootstrap, and runtime skill contracts

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`
- Modify: `docs/agents.md`
- Modify: `docs/architecture.md`
- Modify: `docs/cli.md`
- Modify: `docs/configuration.md`
- Modify: `obsidian_wiki/_data/bootstrap/AGENTS.md`
- Modify: `obsidian_wiki/_data/skills/llm-wiki/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/daily-update/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-digest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-narrate/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-status/SKILL.md`
- Modify: affected protocol tests under `tests/test_portable_*docs.py`,
  `tests/test_portable_skill_protocol.py`, and `tests/test_portable_write_protocol.py`

- [ ] **Step 1: Change protocol tests to assert the new wording and paths**

Replace assertions for `<vault>/journal/operations/**/*.md`, immutable operation
pages, stable unchanged `log.md`, ignored/deleted `hot.md`, and `operation_path` with
assertions for:

```python
assert "<vault>/log.md" in status_skill
assert "tracked" in hot_paragraph.lower()
assert "must not remove" in hot_status_paragraph.lower()
assert "log_path" in cli_documentation
assert "journal/operations" not in active_runtime_text
assert "operation_path" not in active_runtime_text
```

Keep historical `docs/superpowers/` files out of this absence assertion.

- [ ] **Step 2: Run documentation/protocol tests to verify they fail**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_readme_sync.py \
  tests/test_portable_human_docs.py \
  tests/test_portable_manifest_docs.py \
  tests/test_portable_skill_protocol.py \
  tests/test_portable_write_protocol.py \
  tests/test_pre_write_snapshot_docs.py \
  tests/test_wiki_narrate_docs.py -q
```

Expected: FAIL on old per-operation and ignored-hot wording.

- [ ] **Step 3: Update English and Chinese landing documentation together**

Keep headings/examples aligned and state:

- tracked authority includes `wiki/log.md`;
- `wiki/hot.md` is tracked derived state;
- transactions append one canonical block to `log.md` last;
- CLI output returns `log_path`;
- `hot status` is read-only and never removes the tracked file; and
- owners resolve ordinary Git conflicts in `log.md` and `hot.md`.

Remove claims that `log.md` is a stable query page or that normal writes leave it
unchanged. Do not add migration, JSON, pagination, rotation, or automatic merge
sections.

- [ ] **Step 4: Update active architecture, CLI, configuration, and agent docs**

Use this repository layout in `docs/architecture.md`:

```text
wiki/
├── concepts/ entities/ skills/ references/ synthesis/ journal/ projects/
├── .manifest/sources/
├── index.md          # tracked control view
├── log.md            # tracked authoritative operation log
└── hot.md            # tracked derived semantic view
```

Document that `.obsidian-wiki/local/` and transaction/recovery state remain ignored;
`hot.md` does not. Describe the new commit order and the non-destructive hot status.

- [ ] **Step 5: Update packaged bootstrap and runtime skills**

Across the listed runtime files:

- replace operation-directory inventory with safe exact reading of `<vault>/log.md`;
- require canonical log parsing before reporting operations;
- keep agents from directly editing `log.md` because transaction commit owns it;
- describe post-commit `hot.md` refresh as a tracked diff;
- remove permission to invalidate/delete stale hot state; and
- preserve the rule that refresh happens only after a successful terminal
  transaction state.

Do not invoke wiki runtime skills in this framework checkout; these are package
resource edits verified by tests.

- [ ] **Step 6: Run synchronized documentation checks and commit**

Run:

```bash
uv run python tools/check_readme_sync.py
uv run --with pytest python -m pytest \
  tests/test_readme_sync.py \
  tests/test_portable_human_docs.py \
  tests/test_portable_manifest_docs.py \
  tests/test_portable_skill_protocol.py \
  tests/test_portable_write_protocol.py \
  tests/test_pre_write_snapshot_docs.py \
  tests/test_wiki_narrate_docs.py -q
git diff --check
```

Expected: README sync exits 0, all protocol tests PASS, and no whitespace errors.

Commit:

```bash
git add README.md README_ZH.md docs/agents.md docs/architecture.md docs/cli.md \
  docs/configuration.md obsidian_wiki/_data/bootstrap/AGENTS.md \
  obsidian_wiki/_data/skills/llm-wiki/SKILL.md \
  obsidian_wiki/_data/skills/daily-update/SKILL.md \
  obsidian_wiki/_data/skills/wiki-digest/SKILL.md \
  obsidian_wiki/_data/skills/wiki-narrate/SKILL.md \
  obsidian_wiki/_data/skills/wiki-status/SKILL.md \
  tests/test_readme_sync.py tests/test_portable_human_docs.py \
  tests/test_portable_manifest_docs.py tests/test_portable_skill_protocol.py \
  tests/test_portable_write_protocol.py tests/test_pre_write_snapshot_docs.py \
  tests/test_wiki_narrate_docs.py
git commit -m "docs: describe tracked log and hot views"
```

### Task 7: Remove stale operation-page assumptions and run full verification

**Files:**
- Modify: any active runtime or test file returned by the scans below
- Do not modify: historical files under `docs/superpowers/specs/` or
  `docs/superpowers/plans/`

- [ ] **Step 1: Scan active code, assets, and tests for the unreleased format**

Run:

```bash
rg -n "journal/operations|operation_path|operation_paths|operation_guard|write_operation|validate_operation" \
  obsidian_wiki tests README.md README_ZH.md docs \
  -g '!docs/superpowers/**'
rg -n "ignored.*hot\.md|hot\.md.*ignored|remove.*hot\.md|invalidate.*hot\.md" \
  obsidian_wiki tests README.md README_ZH.md docs \
  -g '!docs/superpowers/**'
```

Expected: no matches representing the removed operation-page API or destructive hot
contract. Generic replacement-journal internals and unrelated uses of the word
“operation” are allowed.

- [ ] **Step 2: Remove or update every active match**

For runtime/test matches, route operation reads through `parse_operation_log`, use
`log_path`, or delete obsolete per-file guards. For documentation matches, use the
approved single-log/tracked-hot wording. Do not add compatibility aliases for old
function or metadata names.

- [ ] **Step 3: Run focused cross-component verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q \
  -p no:cacheprovider \
  tests/test_operations.py \
  tests/test_transaction.py \
  tests/test_local_state.py \
  tests/test_portable_setup.py \
  tests/test_portable_check.py \
  tests/test_portable_git.py \
  tests/test_graph_analysis.py \
  tests/test_graphrag.py \
  tests/test_lint.py
```

Expected: all focused tests PASS.

- [ ] **Step 4: Run the repository-mandated full verification**

Run:

```bash
uv run python tools/check_readme_sync.py
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

Expected:

- README sync exits 0;
- the full pytest suite passes;
- `git diff --check` prints nothing; and
- `git status --short` lists only intentional implementation changes, or is clean
  after the task commits.

- [ ] **Step 5: Require a clean implementation worktree**

If `git status --short` is nonempty, inspect every path. Amend the matching Task 1–6
commit with only its intentional files, rerun that task's focused tests, and repeat
Step 4. Do not create a catch-all cleanup commit and never stage unrelated owner
changes.

## Completion criteria

The implementation is complete only when:

- `wiki/log.md` is the only operation-record store;
- every successful transaction appends exactly one canonical record last and can
  restore the exact log preimage on failure;
- no active compatibility reader or migration exists for `journal/operations`;
- setup creates tracked `log.md` and `hot.md` and does not add a hot ignore rule;
- stale hot checks never mutate the tracked file;
- `index.md`, `log.md`, and `hot.md` are absent from framework knowledge graphs;
- CLI, docs, bootstrap, and runtime skills agree on the new paths and behavior; and
- focused tests, full tests, README synchronization, and whitespace checks all pass.

# Failing Test Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the full suite green by repairing uv test isolation, the historical-document baseline, and descriptor-bound cleanup validation.

**Architecture:** Keep product behavior unchanged for valid cleanup trees. Repair two test fixtures directly, then add a recursive read-only preflight before the existing destructive cleanup pass so unsafe trees are rejected before any evidence is removed.

**Tech Stack:** Python 3, pathlib/os descriptor APIs, pytest, uv, Git

---

### Task 1: Repair Hermetic Test Fixtures

**Files:**
- Modify: `tests/test_installation_policy.py`
- Modify: `tests/test_portable_human_docs.py`

- [ ] **Step 1: Extend the uv environment helper contract**

In `test_uv_tool_environment_ignores_parent_behavior_overrides`, seed an inherited
data directory and require the helper to replace it:

```python
monkeypatch.setenv("XDG_DATA_HOME", "/inherited/data")
env = _uv_tool_environment(tmp_path)
assert env["XDG_DATA_HOME"] == str(tmp_path / "xdg-data")
```

- [ ] **Step 2: Run the helper contract and verify RED**

```bash
uv run --with pytest python -m pytest \
  tests/test_installation_policy.py::test_uv_tool_environment_ignores_parent_behavior_overrides -q
```

Expected: FAIL because `XDG_DATA_HOME` remains `/inherited/data`.

- [ ] **Step 3: Isolate XDG data and repair the Git baseline**

Add this entry to `_uv_tool_environment`:

```python
XDG_DATA_HOME=str(tmp_path / "xdg-data"),
```

Replace the unreachable history constant with the actual pre-banner parent:

```python
HISTORICAL_BODY_BASE = "4e16b436ec358067d33df7f59ea30b046a6300ae"
```

- [ ] **Step 4: Run fixture tests and verify GREEN**

```bash
uv run --with pytest python -m pytest \
  tests/test_installation_policy.py::test_uv_tool_environment_ignores_parent_behavior_overrides \
  tests/test_installation_policy.py::test_uv_tool_install_survives_source_move \
  tests/test_portable_human_docs.py::test_historical_documents_have_one_exact_banner_before_the_body -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit fixture repairs**

```bash
git add tests/test_installation_policy.py tests/test_portable_human_docs.py
git commit -m "test: isolate uv data and repair history baseline"
```

### Task 2: Define Whole-Tree Cleanup Preflight

**Files:**
- Modify: `tests/test_portable_setup.py`

- [ ] **Step 1: Add a cross-subtree unsafe-entry regression**

Add a POSIX-only test beside the existing nested FIFO test:

```python
@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO cleanup safety")
def test_sync_cleanup_preflights_all_subtrees_before_removing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    victim = root / "disposable"
    evidence = victim / "a-evidence/journal.json"
    fifo = victim / "z-unsafe/owner.fifo"
    evidence.parent.mkdir(parents=True)
    fifo.parent.mkdir(parents=True)
    evidence.write_bytes(b"cross-subtree evidence remains\n")
    os.mkfifo(fifo)
    original_listdir = portable.os.listdir
    monkeypatch.setattr(
        portable.os,
        "listdir",
        lambda directory: sorted(original_listdir(directory)),
    )

    with pytest.raises(ValueError, match="unsafe entry"):
        portable._remove_sync_path(root, victim)

    assert evidence.read_bytes() == b"cross-subtree evidence remains\n"
    assert fifo.exists()
```

- [ ] **Step 2: Run both FIFO tests and verify RED**

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_setup.py::test_sync_cleanup_rejects_nested_fifo_without_removing_evidence \
  tests/test_portable_setup.py::test_sync_cleanup_preflights_all_subtrees_before_removing_evidence -q
```

Expected: both tests fail because regular evidence is removed before the FIFO is
encountered.

- [ ] **Step 3: Commit the failing safety contract**

```bash
git add tests/test_portable_setup.py
git commit -m "test: require whole-tree cleanup preflight"
```

### Task 3: Preflight Bound Cleanup Trees

**Files:**
- Modify: `obsidian_wiki/portable.py`

- [ ] **Step 1: Add a recursive descriptor-bound validator**

Add `_validate_bound_sync_directory` immediately before the purge helper. It
must enumerate without following symlinks, recurse only through verified bound
directories, accept regular files and symlinks, and reject all special files:

```python
def _validate_bound_sync_directory(descriptor: int) -> None:
    for name in os.listdir(descriptor):
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            child = os.open(name, _inventory_directory_flags(), dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if (metadata.st_dev, metadata.st_ino) != (
                    opened.st_dev,
                    opened.st_ino,
                ):
                    raise ValueError("portable sync cleanup child identity changed")
                _validate_bound_sync_directory(child)
                current = os.fstat(child)
                if (metadata.st_dev, metadata.st_ino) != (
                    current.st_dev,
                    current.st_ino,
                ):
                    raise ValueError("portable sync cleanup child identity changed")
            finally:
                os.close(child)
            attached = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (metadata.st_dev, metadata.st_ino) != (
                attached.st_dev,
                attached.st_ino,
            ):
                raise ValueError("portable sync cleanup child was replaced")
        elif stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            continue
        else:
            raise ValueError("portable sync cleanup contains an unsafe entry")
```

- [ ] **Step 2: Invoke preflight before destructive purge**

In `_remove_sync_path`, after binding and identity-checking `target_fd`, add:

```python
_validate_bound_sync_directory(target_fd)
_purge_bound_sync_directory(target_fd)
```

Retain all existing validation in `_purge_bound_sync_directory`.

- [ ] **Step 3: Run cleanup tests and verify GREEN**

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_setup.py::test_sync_cleanup_rejects_nested_fifo_without_removing_evidence \
  tests/test_portable_setup.py::test_sync_cleanup_preflights_all_subtrees_before_removing_evidence \
  tests/test_portable_setup.py::test_sync_cleanup_rejects_top_level_directory_swap_before_purge \
  tests/test_portable_setup.py::test_sync_cleanup_rejects_recursive_child_swap_before_purge \
  tests/test_portable_setup.py::test_sync_cleanup_unlinks_internal_symlink_without_following_owner_target -q
```

Expected: `5 passed`.

- [ ] **Step 4: Run the full portable setup test file**

```bash
uv run --with pytest python -m pytest tests/test_portable_setup.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit production fix**

```bash
git add obsidian_wiki/portable.py
git commit -m "fix: preflight sync cleanup trees"
```

### Task 4: Verify Repository Contracts

**Files:**
- Verify only.

- [ ] **Step 1: Run the full suite**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
```

Expected: all tests pass.

- [ ] **Step 2: Check README parity and whitespace**

```bash
uv run python tools/check_readme_sync.py
git diff --check
```

Expected: README parity passes and no whitespace errors are reported.

- [ ] **Step 3: Review final scope**

```bash
git status --short
git log -5 --oneline
```

Expected: a clean feature branch containing only the planned fixture, regression,
and cleanup changes.

> **Superseded (2026-08-12):** Current behavior is defined by the
> [Portable-Only Repository Design](../specs/2026-08-12-portable-only-design.md).

# Portable Migration and End-to-End Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide an explicit dry-run-first migration from an already co-located legacy vault/source repository, make Git-root handling correct for a vault nested at `wiki/`, and prove the complete portable workflow across clone locations and concurrent branches.

**Architecture:** Migration is a separate analyzer/applicator, not an implicit branch in setup. It accepts only source and vault directories already contained in the target repository, emits blockers for external/URL sources, builds all candidate config/shards/frontmatter locally, and applies with rollback. A small read-only Git helper discovers the enclosing repository root; portable mode never initializes a nested `wiki/.git` and never auto-commits or pushes.

**Tech Stack:** Python dataclasses/pathlib, legacy manifest adapters, canonical JSON, rollback snapshots, subprocess Git, argparse, pytest, uv source-install smoke tests

**Depends on:** all four earlier portable implementation plans

---

## File map

- `obsidian_wiki/migration.py`: inspect legacy manifest/page paths, report blockers and changes, build/apply/rollback portable migration.
- `obsidian_wiki/git_support.py`: read-only enclosing Git-root discovery and repository facts.
- `obsidian_wiki/sync.py`, `obsidian_wiki/cli.py`: preserve personal sync, refuse auto-sync in portable mode, register migration commands.
- `obsidian_wiki/portable.py`: expose reusable config/skills/bootstrap/template helpers to migration.
- `obsidian_wiki/portable_check.py`, `obsidian_wiki/transaction.py`, `obsidian_wiki/local_state.py`: use the enclosing portable Git root where needed.
- `tests/test_portable_migration.py`, `tests/test_portable_git.py`, `tests/test_portable_collaboration_e2e.py`: migration, Git, and multi-clone proofs.
- `.skills/wiki-setup/SKILL.md`, `.skills/wiki-status/SKILL.md`, `.skills/llm-wiki/SKILL.md`: explicit migration guidance and portable Git policy.
- `docs/installation.md`, `docs/configuration.md`, `docs/architecture.md`, `docs/cli.md`, `docs/fork.md`: final operator documentation.

### Task 1: Analyze legacy-to-portable migration without writing

**Files:**
- Create: `obsidian_wiki/migration.py`
- Create: `tests/test_portable_migration.py`

- [ ] **Step 1: Write failing dry-run analysis tests**

```python
# tests/test_portable_migration.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from obsidian_wiki.migration import analyze_migration


def make_legacy_repo(tmp_path: Path):
    root = tmp_path / "knowledge"
    sources = root / "sources"
    vault = root / "wiki"
    sources.mkdir(parents=True)
    (vault / "concepts").mkdir(parents=True)
    source = sources / "a.md"
    source.write_text("source", encoding="utf-8")
    page = vault / "concepts" / "a.md"
    page.write_text(
        f'''---
title: A
category: concepts
tags: [example]
sources:
  - {source}
created: 2026-08-07
updated: 2026-08-07
---
# A
''',
        encoding="utf-8",
    )
    (vault / "index.md").write_text("# Legacy index\n", encoding="utf-8")
    (vault / "log.md").write_text("# Legacy log\n", encoding="utf-8")
    (vault / "hot.md").write_text("# Legacy hot\n", encoding="utf-8")
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {
                    str(source): {
                        "content_hash": "sha256:old",
                        "pages_produced": ["concepts/a.md"],
                        "last_ingested": "2026-08-01T00:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return root, sources, vault, source, page


def test_analyze_maps_contained_absolute_source_to_repo_id(tmp_path: Path) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    assert plan.blockers == ()
    assert plan.source_mappings == ((str(source.resolve()), "sources/a.md"),)
    assert plan.page_updates == ("concepts/a.md",)
    assert plan.manifest_entries == ("sources/a.md",)
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_analyze_blocks_external_and_url_sources(tmp_path: Path) -> None:
    root, sources, vault, _, _ = make_legacy_repo(tmp_path)
    external = tmp_path / "external.md"
    external.write_text("external", encoding="utf-8")
    manifest = json.loads((vault / ".manifest.json").read_text())
    manifest["sources"][str(external)] = {"content_hash": "sha256:x", "pages_produced": []}
    manifest["sources"]["https://example.com/live"] = {"content_hash": "sha256:y", "pages_produced": []}
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    assert {blocker.code for blocker in plan.blockers} == {"external-source", "live-url-source"}


def test_analyze_rejects_vault_or_sources_outside_root(tmp_path: Path) -> None:
    root, sources, vault, _, _ = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root / "nested", vault=vault, source_root=sources)
    assert "outside-root" in {blocker.code for blocker in plan.blockers}
```

- [ ] **Step 2: Run migration tests and verify failure**

Run: `uv run pytest tests/test_portable_migration.py -q`

Expected: FAIL because `migration.py` does not exist.

- [ ] **Step 3: Define a serializable migration plan**

```python
@dataclass(frozen=True)
class MigrationBlocker:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class MigrationPlan:
    root: Path
    vault: Path
    source_root: Path
    source_mappings: tuple[tuple[str, str], ...]
    page_updates: tuple[str, ...]
    manifest_entries: tuple[str, ...]
    blockers: tuple[MigrationBlocker, ...]
    warnings: tuple[str, ...]
```

Add `MigrationPlan.to_dict(self) -> dict[str, object]`. It emits repository-relative POSIX strings for paths, arrays for every tuple, blocker objects with exactly `code`, `source`, and `message`, and keys in dataclass field order so CLI JSON is stable.

Implement `analyze_migration(*, root, vault, source_root)` as a strictly read-only function. Resolve all three paths and report `outside-root`/overlap blockers before reading content. Read both dict- and list-shaped manifest v1 entries using the same field fallbacks as `cache.py`: key or `path`/`source_id`, `pages_produced` or `pages`, and `last_ingested`/`ingested_at`.

Mapping rules:

- absolute paths below `source_root` map to `root`-relative POSIX Source IDs;
- relative v1 paths resolve against the legacy vault, then must fall below `source_root`;
- URLs/pseudo-sources are blockers;
- missing sources are blockers;
- two old keys mapping to the same Source ID are a `source-id-collision` blocker unless their normalized entry payloads are identical;
- page paths must be safe and present;
- page frontmatter occurrences of old source strings are recorded for replacement;
- absolute source strings in a page with no matching manifest mapping are blockers.

The analyzer does not copy, delete, or rewrite anything.

- [ ] **Step 4: Run dry-run tests**

Run: `uv run pytest tests/test_portable_migration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit migration analysis**

```bash
git add obsidian_wiki/migration.py tests/test_portable_migration.py
git commit -m "feat: analyze legacy portable migration"
```

### Task 2: Apply migration with local rollback

**Files:**
- Modify: `obsidian_wiki/migration.py`
- Modify: `obsidian_wiki/portable.py`
- Modify: `tests/test_portable_migration.py`

- [ ] **Step 1: Add failing apply and rollback tests**

Append:

```python
from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.cli import skills_dir
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.migration import MigrationError, apply_migration
from obsidian_wiki.portable_manifest import ShardedManifest


def test_apply_converts_manifest_frontmatter_and_derived_files(tmp_path: Path) -> None:
    root, sources, vault, _, page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    result = apply_migration(plan, installed_version="2026.8", source_skills=skills_dir())
    config = load_portable_config(
        root / ".obsidian-wiki" / "config.toml",
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    assert "sources/a.md" in page.read_text(encoding="utf-8")
    assert ShardedManifest(config).load("sources/a.md") is not None
    assert json.loads((vault / ".manifest.json").read_text())["schema_version"] == 2
    assert "```query" in (vault / "index.md").read_text(encoding="utf-8")
    assert "journal/operations" in (vault / "log.md").read_text(encoding="utf-8")
    assert not (vault / "hot.md").exists()
    assert result.changed_files


def test_apply_refuses_blocked_plan(tmp_path: Path) -> None:
    root, sources, vault, _, _ = make_legacy_repo(tmp_path)
    external = tmp_path / "outside.md"
    manifest = json.loads((vault / ".manifest.json").read_text())
    manifest["sources"][str(external)] = {"content_hash": "x", "pages_produced": []}
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    with pytest.raises(MigrationError, match="blocker"):
        apply_migration(plan, installed_version="2026.8", source_skills=skills_dir())
    assert not (root / ".obsidian-wiki" / "config.toml").exists()


def test_apply_failure_restores_every_original_file(tmp_path: Path, monkeypatch) -> None:
    root, sources, vault, _, _ = make_legacy_repo(tmp_path)
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    monkeypatch.setattr(ShardedManifest, "upsert", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(MigrationError, match="rolled back"):
        apply_migration(plan, installed_version="2026.8", source_skills=skills_dir())
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before
```

- [ ] **Step 2: Run apply tests and verify failure**

Run: `uv run pytest tests/test_portable_migration.py -q`

Expected: FAIL because `apply_migration` is missing.

- [ ] **Step 3: Refactor portable setup into reusable candidate writers**

Expose pure renderers for portable TOML, stable index, stable log, manifest marker, ignore additions, and managed bootstrap content. Keep `setup_portable_repo` behavior unchanged by making it call those renderers.

- [ ] **Step 4: Implement staged apply and byte-for-byte rollback**

Add:

```python
class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationResult:
    changed_files: tuple[str, ...]
    removed_files: tuple[str, ...]
    backup_dir: Path
```

Expose `apply_migration(plan: MigrationPlan, *, installed_version: str, source_skills: Path) -> MigrationResult` below these types. The CLI passes its installed `skills_dir()`; migration never guesses a framework source checkout from the current directory.

Apply only a blocker-free plan whose current manifest/page hashes still match the analyzed preimages. Create `<root>/.obsidian-wiki/local/migrations/<id>/snapshots` and a manifest of original bytes/absence before replacing anything. Build candidates in the migration workspace, then:

1. write portable config and ignore rules;
2. copy canonical skills and managed adapters/bootstrap files;
3. rewrite only scalar/list tokens inside the leading frontmatter `sources` field, using exact old-string-to-Source-ID mappings while preserving the Markdown body byte-for-byte; reject malformed or ambiguous frontmatter instead of applying a broad text replacement;
4. create v2 shards using the current source hash, migrated pages, and the best legacy ingest timestamp;
5. replace `.manifest.json` with the v2 marker;
6. replace index/log with stable built-in-query views;
7. remove tracked `hot.md` from the worktree;
8. write one migration operation entry last.

Never copy or delete external source files. On failure, restore every original tracked file byte-for-byte, remove created files, and raise `MigrationError(f"migration failed and was rolled back: {exc}")` with the original exception text. Retain backup metadata after success until the user commits or explicitly cleans it.

- [ ] **Step 5: Run migration tests**

Run: `uv run pytest tests/test_portable_migration.py -q`

Expected: PASS.

- [ ] **Step 6: Commit migration apply**

```bash
git add obsidian_wiki/migration.py obsidian_wiki/portable.py tests/test_portable_migration.py
git commit -m "feat: apply portable migration with rollback"
```

### Task 3: Add dry-run-first migration CLI

**Files:**
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_portable_migration.py`

- [ ] **Step 1: Add failing CLI tests**

Test:

```bash
obsidian-wiki repo migrate --root <root> --vault wiki --sources sources --json
obsidian-wiki repo migrate --root <root> --vault wiki --sources sources --apply --json
```

Assertions:

- no flag means dry-run and leaves all files unchanged;
- paths are resolved against `--root`, not CWD;
- dry-run returns `1` when blockers exist and JSON lists them;
- `--apply` refuses blockers and preimage drift;
- `--apply` prints changed/removed files and never commits;
- an already-portable repository reports “already portable” without rewriting.

- [ ] **Step 2: Run CLI cases and verify failure**

Run: `uv run pytest tests/test_portable_migration.py -q`

Expected: FAIL because the nested `migrate` command is missing.

- [ ] **Step 3: Register `repo migrate`**

Add required `--root`, `--vault`, and `--sources` arguments plus `--apply`, `--json`, and `--pretty`. Always run `analyze_migration` first. For apply, pass the CLI's installed `skills_dir()` explicitly. Human dry-run output has sections `Mappings`, `Page updates`, `Manifest shards`, `Warnings`, and `Blockers`, followed by the exact apply command only when blocker-free. No interactive prompt, implicit apply, commit, or push.

- [ ] **Step 4: Run migration CLI tests**

Run: `uv run pytest tests/test_portable_migration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit migration CLI**

```bash
git add obsidian_wiki/cli.py tests/test_portable_migration.py
git commit -m "feat: expose dry-run-first portable migration"
```

### Task 4: Discover the enclosing Git repository without nested initialization

**Files:**
- Create: `obsidian_wiki/git_support.py`
- Create: `tests/test_portable_git.py`
- Modify: `obsidian_wiki/sync.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `obsidian_wiki/portable_check.py`
- Modify: `obsidian_wiki/local_state.py`
- Modify: `tests/test_sync.py`

- [ ] **Step 1: Write failing parent-repository tests**

```python
# tests/test_portable_git.py
from pathlib import Path
import subprocess

from obsidian_wiki.git_support import discover_git_root, git_branch_id


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def test_vault_discovers_enclosing_repo_root(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    vault = root / "wiki"
    vault.mkdir(parents=True)
    git(root, "init", "-q")
    assert discover_git_root(vault) == root.resolve()
    assert not (vault / ".git").exists()


def test_non_repo_returns_none(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    assert discover_git_root(vault) is None


def test_branch_id_uses_branch_or_detached_head(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "seed").write_text("x")
    git(root, "add", "seed")
    git(root, "commit", "-q", "-m", "seed")
    assert git_branch_id(root)
```

Add portable sync CLI tests asserting `obsidian-wiki sync` and `sync-setup` from a portable repository return nonzero with “portable repositories use branch and pull-request workflows” and do not add/commit/push or create `wiki/.git`.

- [ ] **Step 2: Run Git tests and verify failure**

Run: `uv run pytest tests/test_portable_git.py tests/test_sync.py -q`

Expected: FAIL because the helper and portable refusal are missing.

- [ ] **Step 3: Implement read-only Git discovery**

```python
def discover_git_root(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def git_branch_id(root: Path) -> str:
    branch = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"],
        text=True,
        capture_output=True,
    )
    if branch.returncode == 0 and branch.stdout.strip():
        return branch.stdout.strip()
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        text=True,
        capture_output=True,
    )
    return head.stdout.strip() if head.returncode == 0 and head.stdout.strip() else "no-git"


def tracked_paths(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
    )
    if result.returncode != 0:
        return ()
    return tuple(sorted(part.decode("utf-8") for part in result.stdout.split(b"\0") if part))
```

Implement the two remaining helpers with read-only `git` commands and deterministic sorted output. Never call `git init`, `add`, `commit`, `remote`, or `push` from this module.

- [ ] **Step 4: Integrate root discovery and portable sync refusal**

Use `discover_git_root(config.vault)` in doctor, check, and hot fingerprinting; require it to equal `config.root` when Git exists. In portable mode, `sync`/`sync-setup` stop before calling `sync.py` and explain the PR workflow. Keep `sync.py` personal-vault behavior and its existing tests unchanged; update only obsolete documentation strings.

- [ ] **Step 5: Run Git and sync tests**

Run: `uv run pytest tests/test_portable_git.py tests/test_sync.py tests/test_portable_check.py tests/test_local_state.py -q`

Expected: PASS.

- [ ] **Step 6: Commit enclosing Git support**

```bash
git add obsidian_wiki/git_support.py obsidian_wiki/sync.py obsidian_wiki/cli.py obsidian_wiki/portable_check.py obsidian_wiki/local_state.py tests/test_portable_git.py tests/test_sync.py
git commit -m "feat: recognize enclosing portable Git repositories"
```

### Task 5: Prove clone portability and merge-friendly concurrent work

**Files:**
- Create: `tests/test_portable_collaboration_e2e.py`

- [ ] **Step 1: Add an end-to-end clone-location test**

Create one portable seed repository, copy it to two differently named absolute roots, add the same `sources/design/a.md` bytes, and use `TransactionManager` with fixed transaction ID/time/operation suffix in each. Assert:

```python
assert manifest_payload_a == manifest_payload_b
assert page_frontmatter_a == page_frontmatter_b
assert "/clone-a/" not in json.dumps(manifest_payload_a)
assert "/clone-b/" not in json.dumps(manifest_payload_b)
assert manifest_relative_path_a == manifest_relative_path_b
```

The test supplies deterministic timestamps through existing injectable clock/suffix arguments; production defaults remain UTC plus random suffix.

- [ ] **Step 2: Add a real Git merge test for unrelated sources**

The test must:

1. initialize and commit a portable seed repository;
2. clone it twice to `alice` and `bob`;
3. configure local test Git identities;
4. in Alice, ingest `sources/alice.md` into `concepts/alice.md` and commit;
5. in Bob, ingest `sources/bob.md` into `concepts/bob.md` and commit;
6. fetch Bob's branch into Alice and merge it;
7. assert merge return code `0` and no unmerged paths from `git diff --name-only --diff-filter=U`;
8. run `check_portable_repo` on the merged repository and assert pass.

Use module APIs rather than an LLM; inject deterministic operation suffixes that remain distinct (`a11c`, `b22d`).

- [ ] **Step 3: Run end-to-end tests and fix only integration defects**

Run: `uv run pytest tests/test_portable_collaboration_e2e.py -q`

Expected: PASS.

- [ ] **Step 4: Commit collaboration proofs**

```bash
git add tests/test_portable_collaboration_e2e.py
git commit -m "test: prove clone portability and conflict-free shards"
```

### Task 6: Finalize migration/operator documentation and full verification

**Files:**
- Modify: `.skills/wiki-setup/SKILL.md`
- Modify: `.skills/wiki-status/SKILL.md`
- Modify: `.skills/llm-wiki/SKILL.md`
- Modify: `docs/installation.md`
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`
- Modify: `docs/cli.md`
- Modify: `docs/fork.md`
- Modify: `README.md`
- Modify: `README_ZH.md`

- [ ] **Step 1: Document explicit migration and Git boundaries**

Document the complete dry-run/apply commands, contained-source prerequisite, blocker meanings, rollback path, hot removal, stable index/log conversion, and ordinary Git diff review. State that portable mode recognizes the repository surrounding `wiki/`, never creates `wiki/.git`, and refuses auto-sync/commit/push. Document Markdown/text/small snapshotted sources as the supported collaboration format and state that Git LFS pointers are not compiled as source contents.

Include this provider-neutral CI sequence and state that it performs no LLM or hosting API calls:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
cd ../knowledge-base
obsidian-wiki check
```

For production CI, the knowledge-repository maintainer pins that Git checkout to a concrete fork release tag accepted by `requires_cli`; the generic sequence above remains runnable before the fork's first release tag exists. Do not document `uv tool install` with a package index, remote Git URL, editable flag, or source path other than `.`.

- [ ] **Step 2: Run all new portable tests**

Run: `uv run pytest tests/test_fork_identity.py tests/test_installation_policy.py tests/test_portable_config.py tests/test_portable_setup.py tests/test_portable_manifest.py tests/test_portable_check.py tests/test_transaction.py tests/test_operations.py tests/test_local_state.py tests/test_portable_migration.py tests/test_portable_git.py tests/test_portable_collaboration_e2e.py -q`

Expected: PASS.

- [ ] **Step 3: Run all legacy regression tests**

Run: `uv run pytest -q`

Expected: full suite PASS; personal/global setup, named vaults, `.env` precedence below portable mode, manifest v1 shapes, lint/query/context, and personal sync remain green.

- [ ] **Step 4: Verify source-only installation after all packaged assets change**

Run: `uv run pytest tests/test_installation_policy.py::test_uv_tool_install_survives_source_move -q`

Expected: PASS and version output includes `evanzlh/obsidian-wiki`.

Run: `uv build`

Expected: wheel and sdist build successfully; the wheel contains canonical skills, bootstrap files, and new runtime modules.

- [ ] **Step 5: Run documentation and path audits**

Run: `uv run python tools/check_readme_sync.py`

Expected: `README_ZH.md is up to date with README.md.`

Run: `rg -n --glob '!docs/superpowers/**' "README_TW|pip install obsidian-wiki|pipx install obsidian-wiki|bash setup\.sh|uv tool install git\+|canonical absolute paths" README.md README_ZH.md AGENTS.md docs .skills obsidian_wiki tools .github`

Expected: no unsupported install guidance; “canonical absolute paths” may appear only inside an explicitly labeled personal manifest v1 section.

- [ ] **Step 6: Commit final protocols and docs**

```bash
git add .skills docs README.md README_ZH.md
git commit -m "docs: finalize portable migration and collaboration workflow"
```

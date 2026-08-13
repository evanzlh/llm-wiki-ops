> **Superseded (2026-08-12):** Current behavior is defined by the
> [Portable-Only Repository Design](../specs/2026-08-12-portable-only-design.md).

# Sharded Manifest and Deterministic Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give portable repositories stable repository-relative Source IDs, merge-friendly manifest v2 shards, backward-compatible cache commands, and a read-only `obsidian-wiki check` suitable for any CI platform.

**Architecture:** Leave manifest v1 parsing in `cache.py` for personal vaults and add a separate `ShardedManifest` implementation selected only when resolved portable config points at a v2 marker. Centralize Source ID/path containment in the sharded store. Build `portable_check.py` as an issue-producing validator that composes configuration, manifest, page-frontmatter, lint, and Git-tracking checks without invoking an LLM.

**Tech Stack:** Python dataclasses, pathlib, SHA-256, canonical JSON, subprocess Git inspection, argparse, pytest

**Depends on:** `2026-08-07-portable-config-and-setup.md`

---

## File map

- `obsidian_wiki/portable_manifest.py`: Source ID mapping, manifest v2 marker/entry parsing, sharded read/write/status operations.
- `obsidian_wiki/frontmatter.py`: minimal frontmatter scalar/list extraction shared by portable validation.
- `obsidian_wiki/portable_check.py`: deterministic issue model and full portable-repository validation.
- `obsidian_wiki/cache.py`, `obsidian_wiki/batch.py`: delegate to v2 only when given portable config; preserve all v1 shapes.
- `obsidian_wiki/cli.py`: pass portable context into cache/batch helpers, register `check`, and teach doctor manifest v2.
- `scripts/manifest.py`: explicitly remain a v1 maintenance tool and fail safely on v2.
- `tests/test_portable_manifest.py`, `tests/test_portable_check.py`: new behavior.
- `tests/test_cache.py`, `tests/test_cache_manifest_shapes.py`, `tests/test_batch.py`, `tests/test_doctor.py`, `tests/test_manifest_delta.py`: compatibility coverage.
- `.skills/llm-wiki/SKILL.md`, `.skills/wiki-ingest/SKILL.md`, `.skills/wiki-update/SKILL.md`, `.skills/wiki-status/SKILL.md`: mode-dependent manifest rules.
- `docs/architecture.md`, `docs/configuration.md`, `docs/cli.md`: human reference.

### Task 1: Define stable Source IDs and entry paths

**Files:**
- Create: `obsidian_wiki/portable_manifest.py`
- Create: `tests/test_portable_manifest.py`

- [ ] **Step 1: Write failing Source ID tests**

```python
# tests/test_portable_manifest.py
from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable_manifest import ManifestError, ShardedManifest


def make_repo(tmp_path: Path):
    root = tmp_path / "knowledge"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources" / "design").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / ".skills").mkdir()
    (root / ".obsidian-wiki" / "config.toml").write_text(
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
    config = load_portable_config(
        root / ".obsidian-wiki" / "config.toml",
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    return root, config


def test_source_id_is_repo_relative_posix_path(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "design" / "architecture.md"
    source.write_text("body", encoding="utf-8")
    store = ShardedManifest(config)
    assert store.source_id(source) == "sources/design/architecture.md"
    assert store.entry_path("sources/design/architecture.md") == (
        root / "wiki" / ".manifest" / "sources" / "design" / "architecture.md.json"
    )


def test_source_outside_configured_root_is_rejected(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    external = root / "notes.md"
    external.write_text("body", encoding="utf-8")
    with pytest.raises(ManifestError, match="configured source root"):
        ShardedManifest(config).source_id(external)


def test_source_id_rejects_absolute_and_traversal_strings(tmp_path: Path) -> None:
    _, config = make_repo(tmp_path)
    store = ShardedManifest(config)
    for source_id in (
        "/tmp/file.md",
        "C:/tmp/file.md",
        "sources\\file.md",
        "sources/../../file.md",
        "../sources/file.md",
    ):
        with pytest.raises(ManifestError, match="Source ID"):
            store.entry_path(source_id)
```

- [ ] **Step 2: Run Source ID tests and verify failure**

Run: `uv run pytest tests/test_portable_manifest.py -q`

Expected: FAIL because `portable_manifest.py` does not exist.

- [ ] **Step 3: Implement Source ID and marker validation**

Create these contracts:

```python
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from obsidian_wiki.config import PortableConfig


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestEntry:
    source_id: str
    content_hash: str
    pages: tuple[str, ...]
    compiled_at: str


class ShardedManifest:
    def __init__(self, config: PortableConfig) -> None:
        if len(config.sources) != 1:
            raise ManifestError("manifest v2 schema 1 requires exactly one source root")
        self.config = config
        self.source_root = config.sources[0]
        self.marker_path = config.vault / ".manifest.json"
        self.entries_root = config.vault / ".manifest" / "sources"
        self._validate_marker()
```

Add public methods `source_id(self, source: Path) -> str`, `source_path(self, source_id: str) -> Path`, and `entry_path(self, source_id: str) -> Path` to that class.

`source_id` resolves symlinks, requires containment below the one configured source root, and returns `path.relative_to(config.root).as_posix()`. `entry_path` accepts only normalized relative POSIX IDs beginning with the configured source-root repository-relative prefix, strips that prefix, appends `.json`, and verifies the resolved entry remains below `entries_root`.

`_validate_marker` requires exactly schema `2`, storage `sharded`, and entries `.manifest/sources`; invalid or missing JSON raises `ManifestError` with the marker path.

- [ ] **Step 4: Run Source ID tests**

Run: `uv run pytest tests/test_portable_manifest.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Source ID mapping**

```bash
git add obsidian_wiki/portable_manifest.py tests/test_portable_manifest.py
git commit -m "feat: map portable sources to stable IDs"
```

### Task 2: Read and write canonical manifest shards

**Files:**
- Modify: `obsidian_wiki/portable_manifest.py`
- Modify: `tests/test_portable_manifest.py`

- [ ] **Step 1: Add failing sharded-store tests**

Append:

```python
import json
from datetime import datetime, timezone

from obsidian_wiki.cache import compute_hash


def test_upsert_writes_one_canonical_shard(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "design" / "architecture.md"
    source.write_text("body", encoding="utf-8")
    store = ShardedManifest(config)
    entry = store.upsert(
        source,
        pages=["references/layout.md", "concepts/portable-repo.md"],
        compiled_at="2026-08-07T07:30:00Z",
    )
    payload = json.loads(store.entry_path(entry.source_id).read_text(encoding="utf-8"))
    assert payload == {
        "compiled_at": "2026-08-07T07:30:00Z",
        "content_hash": f"sha256:{compute_hash(source)}",
        "pages": ["concepts/portable-repo.md", "references/layout.md"],
        "source_id": "sources/design/architecture.md",
    }
    assert store.entry_path(entry.source_id).read_text(encoding="utf-8").endswith("\n")


def test_unrelated_sources_use_unrelated_shards(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    first = root / "sources" / "a.md"
    second = root / "sources" / "b.md"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(first, pages=["concepts/a.md"], compiled_at="2026-08-07T00:00:00Z")
    store.upsert(second, pages=["concepts/b.md"], compiled_at="2026-08-07T00:00:01Z")
    assert store.entry_path("sources/a.md") != store.entry_path("sources/b.md")
    assert [entry.source_id for entry in store.iter_entries()] == ["sources/a.md", "sources/b.md"]


def test_status_uses_hash_not_mtime(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("a", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, pages=[], compiled_at="2026-08-07T00:00:00Z")
    source.touch()
    assert store.status()["unchanged"] == ["sources/a.md"]
    source.write_text("changed", encoding="utf-8")
    assert store.status()["modified"] == ["sources/a.md"]


def test_status_reports_uncompiled_and_orphaned(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    tracked = root / "sources" / "tracked.md"
    new = root / "sources" / "new.md"
    tracked.write_text("tracked", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(tracked, pages=[], compiled_at="2026-08-07T00:00:00Z")
    tracked.unlink()
    status = store.status()
    assert status["new"] == ["sources/new.md"]
    assert status["missing"] == ["sources/tracked.md"]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/test_portable_manifest.py -q`

Expected: FAIL because `upsert`, `iter_entries`, and `status` are missing.

- [ ] **Step 3: Implement canonical shard operations**

Add `load`, `iter_entries`, `upsert`, `remove`, and `status` methods to `ShardedManifest`. Their typed contracts are fixed by the calls and assertions in the preceding tests: `load` returns `ManifestEntry | None`; `iter_entries` returns a Source-ID-sorted list; `upsert` accepts a source plus pages and optional completion time and returns the stored entry; `remove` accepts one Source ID; and `status` returns the four named lists below.

Rules:

- `content_hash` is always `sha256:<lowercase hex>`.
- `pages` are unique, sorted, normalized vault-relative POSIX paths; reject absolute paths and `..`.
- `compiled_at` defaults to current UTC RFC 3339 with `Z`.
- JSON uses `json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"`.
- Write via a sibling temporary file and `Path.replace` so one shard is never partially written.
- `iter_entries` sorts by `source_id` and rejects duplicate source IDs or entry-path/source-ID mismatch.
- `status` enumerates regular files below the source root, ignoring `.gitkeep` and hidden path components, and returns sorted `new`, `modified`, `unchanged`, and `missing` Source IDs.

- [ ] **Step 4: Run manifest tests**

Run: `uv run pytest tests/test_portable_manifest.py -q`

Expected: PASS.

- [ ] **Step 5: Commit sharded persistence**

```bash
git add obsidian_wiki/portable_manifest.py tests/test_portable_manifest.py
git commit -m "feat: persist sharded manifest entries"
```

### Task 3: Delegate cache and batch commands without regressing v1

**Files:**
- Modify: `obsidian_wiki/cache.py`
- Modify: `obsidian_wiki/batch.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `scripts/manifest.py`
- Modify: `tests/test_cache.py`
- Modify: `tests/test_batch.py`
- Modify: `tests/test_manifest_delta.py`

- [ ] **Step 1: Add failing portable cache/batch tests**

Add tests that call:

```python
result = check_sources(config.vault, [source], portable=config)
assert result["new"] == ["sources/a.md"]
update_source(config.vault, source, pages_produced=["concepts/a.md"], portable=config)
assert check_sources(config.vault, [source], portable=config)["unchanged"] == ["sources/a.md"]
```

Add a batch test:

```python
update_source(config.vault, source, pages_produced=[], portable=config)
result = plan_batches(config.sources[0], config.vault, portable=config)
assert result["stats"]["skipped_unchanged"] == 1
```

Add a `scripts/manifest.py` test that supplies a v2 marker to `normalize` and expects return code `1` plus `manifest v2 is managed by obsidian-wiki` rather than rewriting it.

- [ ] **Step 2: Run cache/batch compatibility tests and verify only new cases fail**

Run: `uv run pytest tests/test_cache.py tests/test_cache_manifest_shapes.py tests/test_batch.py tests/test_manifest_delta.py -q`

Expected: existing v1 tests PASS; new portable cases FAIL because the optional context is unsupported.

- [ ] **Step 3: Add optional portable delegation**

Change signatures without altering defaults:

```python
def check_sources(vault: Path, source_paths: list[Path], *, portable: PortableConfig | None = None) -> CheckResult:
    if portable is not None:
        return ShardedManifest(portable).status_for(source_paths)
    # existing v1 implementation unchanged


def update_source(
    vault: Path,
    source_path: Path,
    pages_produced: list[str] | None = None,
    *,
    portable: PortableConfig | None = None,
) -> str:
    if portable is not None:
        entry = ShardedManifest(portable).upsert(source_path, pages=pages_produced or [])
        return entry.content_hash.removeprefix("sha256:")
    # existing v1 implementation unchanged
```

Add `status_for(source_paths)` to the store so CLI-requested missing files retain cache-check semantics while all-entry `status()` remains available to repository validation.

Thread `portable` through `plan_batches` and its unchanged filter. In cache/batch CLI commands, resolve CWD configuration and pass `resolved.portable` only when the supplied vault resolves to the portable vault; otherwise use v1.

Before `scripts/manifest.py` reads `sources`, detect `schema_version == 2` and return an actionable refusal. Do not make the standalone v1 script write shards.

- [ ] **Step 4: Run cache/batch/manifest tests**

Run: `uv run pytest tests/test_cache.py tests/test_cache_manifest_shapes.py tests/test_batch.py tests/test_manifest_delta.py -q`

Expected: PASS, including all legacy manifest shapes.

- [ ] **Step 5: Commit compatibility delegation**

```bash
git add obsidian_wiki/cache.py obsidian_wiki/batch.py obsidian_wiki/cli.py scripts/manifest.py tests/test_cache.py tests/test_batch.py tests/test_manifest_delta.py
git commit -m "feat: route portable cache operations to manifest v2"
```

### Task 4: Parse page provenance deterministically

**Files:**
- Create: `obsidian_wiki/frontmatter.py`
- Create: `tests/test_portable_check.py`

- [ ] **Step 1: Write failing frontmatter tests**

```python
# tests/test_portable_check.py
import pytest

from obsidian_wiki.frontmatter import FrontmatterError, parse_frontmatter


def test_parse_block_sources_and_required_fields() -> None:
    page = '''---
title: Portable Repository
category: concepts
tags:
  - knowledge-management
sources:
  - sources/design/portable.md
  - sources/meetings/review.md
created: 2026-08-07
updated: 2026-08-07
---
# Portable Repository
'''
    parsed = parse_frontmatter(page)
    assert parsed.scalars["title"] == "Portable Repository"
    assert parsed.lists["sources"] == (
        "sources/design/portable.md",
        "sources/meetings/review.md",
    )


def test_parse_inline_sources() -> None:
    parsed = parse_frontmatter("---\ntitle: A\nsources: [sources/a.md, sources/b.md]\n---\n")
    assert parsed.lists["sources"] == ("sources/a.md", "sources/b.md")


def test_missing_frontmatter_is_rejected() -> None:
    with pytest.raises(FrontmatterError, match="frontmatter"):
        parse_frontmatter("# No metadata\n")
```

- [ ] **Step 2: Run frontmatter tests and verify failure**

Run: `uv run pytest tests/test_portable_check.py -q`

Expected: FAIL because `frontmatter.py` does not exist.

- [ ] **Step 3: Implement a narrow parser**

Expose:

```python
@dataclass(frozen=True)
class Frontmatter:
    scalars: dict[str, str]
    lists: dict[str, tuple[str, ...]]


class FrontmatterError(ValueError):
    pass
```

Add `parse_frontmatter(text: str) -> Frontmatter` below these types.

Support top-level scalar values, YAML-style block lists, and inline bracket lists. Strip matching single/double quotes and comments outside quotes. Reject duplicate top-level keys, malformed list indentation, or a missing closing delimiter. Do not add a general YAML dependency and do not interpret nested arbitrary objects.

- [ ] **Step 4: Run frontmatter tests**

Run: `uv run pytest tests/test_portable_check.py -q`

Expected: PASS.

- [ ] **Step 5: Commit provenance parsing**

```bash
git add obsidian_wiki/frontmatter.py tests/test_portable_check.py
git commit -m "feat: parse portable page provenance"
```

### Task 5: Implement read-only portable repository validation

**Files:**
- Create: `obsidian_wiki/portable_check.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_portable_check.py`
- Modify: `tests/test_doctor.py`

- [ ] **Step 1: Add failing repository-check tests**

Extend `tests/test_portable_check.py` with a complete valid fixture:

```python
import json
import subprocess
from pathlib import Path

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.cli import skills_dir
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable import setup_portable_repo
from obsidian_wiki.portable_check import check_portable_repo
from obsidian_wiki.portable_manifest import ShardedManifest


def valid_repo(tmp_path: Path):
    root = tmp_path / "knowledge"
    setup_portable_repo(
        root,
        version="2026.8",
        source_skills=skills_dir(),
    )
    config_path = root / ".obsidian-wiki" / "config.toml"
    config = load_portable_config(
        config_path,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    source = root / "sources" / "a.md"
    source.write_text("authoritative source", encoding="utf-8")
    page = root / "wiki" / "concepts" / "a.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        '''---
title: A
category: concepts
tags:
  - example
sources:
  - sources/a.md
created: 2026-08-07
updated: 2026-08-07
summary: A compiled example.
---
# A
''',
        encoding="utf-8",
    )
    store = ShardedManifest(config)
    store.upsert(source, pages=["concepts/a.md"], compiled_at="2026-08-07T00:00:00Z")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    return root, config, source, page, store.entry_path("sources/a.md")
```

Test these independent failures:

```python
def issue_codes(report: dict[str, object]) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


def test_valid_portable_repo_passes(tmp_path: Path) -> None:
    _, config, _, _, _ = valid_repo(tmp_path)
    assert check_portable_repo(config)["status"] == "pass"


def test_changed_source_is_an_error(tmp_path: Path) -> None:
    _, config, source, _, _ = valid_repo(tmp_path)
    source.write_text("changed", encoding="utf-8")
    assert "source-stale" in issue_codes(check_portable_repo(config))


def test_absolute_page_source_is_an_error(tmp_path: Path) -> None:
    _, config, _, page, _ = valid_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace("sources/a.md", "/tmp/a.md"),
        encoding="utf-8",
    )
    assert "absolute-page-source" in issue_codes(check_portable_repo(config))


def test_missing_manifest_page_is_an_error(tmp_path: Path) -> None:
    _, config, _, _, entry_path = valid_repo(tmp_path)
    entry_path.write_text(
        entry_path.read_text(encoding="utf-8").replace("concepts/a.md", "concepts/missing.md"),
        encoding="utf-8",
    )
    assert "manifest-page-missing" in issue_codes(check_portable_repo(config))


def test_git_lfs_pointer_is_not_treated_as_source_content(tmp_path: Path) -> None:
    _, config, source, _, _ = valid_repo(tmp_path)
    source.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0123456789abcdef\n"
        "size 42\n",
        encoding="utf-8",
    )
    assert "unsupported-git-lfs-pointer" in issue_codes(check_portable_repo(config))


def test_damaged_agent_adapter_is_an_error(tmp_path: Path) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    adapter = root / ".claude" / "skills" / "wiki-ingest" / "SKILL.md"
    adapter.write_text("# wrong target\n", encoding="utf-8")
    assert "managed-adapter-invalid" in issue_codes(check_portable_repo(config))


def test_mutable_central_index_is_an_error(tmp_path: Path) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    (root / "wiki" / "index.md").write_text("# Hand-maintained list\n", encoding="utf-8")
    assert "stable-view-modified" in issue_codes(check_portable_repo(config))
```

Add a Git tracking case that force-adds `wiki/hot.md` or `.obsidian-wiki/local/cache.json` and expects `tracked-local-state`. Add a CLI case running `obsidian-wiki check --json` from a nested source directory and asserting a nonzero exit for stale state. Add another case that changes the config implementation to `Ar9av/obsidian-wiki`; assert nonzero exit, an implementation-specific error, and no fallback to legacy/global config.

- [ ] **Step 2: Run check tests and verify failure**

Run: `uv run pytest tests/test_portable_check.py tests/test_doctor.py -q`

Expected: FAIL because `portable_check.py` and the command are missing.

- [ ] **Step 3: Implement the issue/report model**

Use this issue type:

```python
@dataclass(frozen=True)
class CheckIssue:
    code: str
    path: str
    message: str
    severity: Literal["warning", "error"] = "error"
```

Expose `check_portable_repo(config: PortableConfig) -> dict[str, object]` as the read-only orchestrator.

The checker must remain read-only and perform these passes:

1. Re-validate config implementation, version, path containment, and v2 marker.
2. Load every shard and classify source hashes. Treat new, stale, and orphaned sources as errors. If a source begins with the Git LFS pointer signature `version https://git-lfs.github.com/spec/v1`, emit `unsupported-git-lfs-pointer` instead of treating the pointer bytes as authoritative content.
3. For each manifest page, require an existing safe vault-relative file.
4. For each knowledge page under `concepts`, `entities`, `skills`, `references`, `synthesis`, `journal` (except `journal/operations`), and `projects`, require `title`, `category`, `tags`, `sources`, `created`, and `updated`. Exclude root control pages and `_meta`, `_raw`, `_readouts`, `.manifest`, and `.obsidian` from this knowledge-page pass.
5. Reject absolute page sources and portable Source IDs outside configured roots.
6. Require each manifest-to-page edge to appear in page `sources` and each portable page source to point back from the manifest entry.
7. Call existing `lint_vault` and convert fail-level structural/link findings into check errors without changing lint's personal-mode severity. Filter findings whose source page is `journal/operations/**`; operation entries have their own validator and their Removed links intentionally target pages that no longer exist.
8. Run `git -C <root> ls-files -z` when root is a Git worktree and reject tracked `wiki/hot.md`, `.obsidian-wiki/local/**`, locks, snapshots, or transactions. If Git is absent, emit one warning rather than initializing it.
9. Validate `.obsidian-wiki/managed-skills.json`: implementation must match, `skills_version` must be a valid version accepted by the repository's `requires_cli` range, and names must equal the exact set of canonical `.skills/*/SKILL.md` directories. Do not require `skills_version` to equal the currently installed compatible CLI, because skill upgrades are explicit. Validate every fully managed agent adapter against the same pure renderer used by `repo upgrade-skills`; a missing, absolute, symlinked, or stale adapter is an error. For bootstrap files, require exactly one marker pair and compare only the managed region, leaving all team-maintained text outside the markers unconstrained.
10. Require `index.md` and `log.md` to equal the stable templates rendered by `portable.py`. This prevents an ordinary write path from reintroducing mutable central lists; owner-specific navigation belongs in separate pages or the team-maintained bootstrap region.

Sort issues by `(severity, code, path, message)` and return deterministic counts. Status is `fail` if any error, `warn` for warning-only, otherwise `pass`.

Every `CheckIssue.path` is a repository-relative POSIX string (or `.` for the repository itself), and portable check messages must not interpolate the absolute clone root. This keeps JSON reports comparable across clone locations.

- [ ] **Step 4: Register `obsidian-wiki check`**

The command takes `--json` and `--pretty`, resolves portable config from CWD, fails outside portable mode with “check requires a portable repository,” prints a human summary or JSON, and returns nonzero only for status `fail`.

Update doctor so a valid v2 marker reports the number of manifest shards instead of looking for a top-level `sources` collection.

- [ ] **Step 5: Run check and doctor tests**

Run: `uv run pytest tests/test_portable_check.py tests/test_doctor.py -q`

Expected: PASS.

- [ ] **Step 6: Commit deterministic validation**

```bash
git add obsidian_wiki/portable_check.py obsidian_wiki/cli.py tests/test_portable_check.py tests/test_doctor.py
git commit -m "feat: validate portable repositories without an LLM"
```

### Task 6: Update manifest protocols and documentation

**Files:**
- Modify: `.skills/llm-wiki/SKILL.md`
- Modify: `.skills/wiki-ingest/SKILL.md`
- Modify: `.skills/wiki-update/SKILL.md`
- Modify: `.skills/wiki-status/SKILL.md`
- Modify: `docs/architecture.md`
- Modify: `docs/configuration.md`
- Modify: `docs/cli.md`
- Modify: `README.md`
- Modify: `README_ZH.md`
- Create: `tests/test_portable_manifest_docs.py`

- [ ] **Step 1: Write a failing mode-specific protocol test**

```python
# tests/test_portable_manifest_docs.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ".skills/llm-wiki/SKILL.md",
    ".skills/wiki-ingest/SKILL.md",
    ".skills/wiki-update/SKILL.md",
    ".skills/wiki-status/SKILL.md",
)


def test_core_skills_distinguish_manifest_versions() -> None:
    for relative in FILES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "manifest v1" in text, relative
        assert "manifest v2" in text, relative
        assert "repository-relative Source ID" in text, relative


def test_portable_protocol_does_not_require_absolute_source_keys() -> None:
    text = (ROOT / ".skills/llm-wiki/SKILL.md").read_text(encoding="utf-8")
    portable = text.split("### Portable manifest v2", 1)[1]
    assert "canonical absolute paths" not in portable
```

- [ ] **Step 2: Run protocol tests and verify failure**

Run: `uv run pytest tests/test_portable_manifest_docs.py -q`

Expected: FAIL because skills only describe the legacy single-file manifest.

- [ ] **Step 3: Add explicit mode branches**

Keep current absolute/vault-relative v1 rules under a clearly labeled personal-mode section. Add a portable v2 section that requires:

- Source IDs relative to repository root using `/` separators;
- authoritative files below configured `sources/`;
- one shard under `wiki/.manifest/sources/` per source;
- `obsidian-wiki cache-check` and `cache-update` rather than manual JSON mutation;
- no live URL or external path as a durable Source ID;
- no model, agent, API, or generation-tool provenance fields.
- ordinary Git storage for Markdown, text, and other reviewable small source snapshots; Git LFS pointers are not supported as authoritative source contents.

Update status instructions to treat new/stale/orphaned portable sources as PR-blocking conditions and describe `obsidian-wiki check`. Do not yet change index/log/hot write behavior; that belongs to the transaction plan.

- [ ] **Step 4: Document manifest v2 and check**

Add the marker/entry examples, Source ID rules, status semantics, and `check` exit behavior to human docs. Keep README language parity when adding the single check example.

- [ ] **Step 5: Run protocol and complete manifest tests**

Run: `uv run pytest tests/test_portable_manifest_docs.py tests/test_portable_manifest.py tests/test_portable_check.py tests/test_cache.py tests/test_cache_manifest_shapes.py tests/test_batch.py tests/test_manifest_delta.py tests/test_doctor.py tests/test_readme_sync.py -q`

Expected: PASS.

- [ ] **Step 6: Commit manifest documentation**

```bash
git add .skills/llm-wiki .skills/wiki-ingest .skills/wiki-update .skills/wiki-status docs README.md README_ZH.md tests/test_portable_manifest_docs.py
git commit -m "docs: define portable source and manifest rules"
```

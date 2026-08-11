# Portable Agent Preflight CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add transaction preflight, consistent cache JSON/config resolution, deterministic hot-cache inputs, and Unicode path regression coverage without weakening portable transaction safety.

**Architecture:** Extract the reusable page-identity/link parser from `lint.py` into a pure `page_graph.py` module. Build a transaction-specific validator over an in-memory prospective page set and call it both from a new read-only CLI command and from commit/retry before snapshots. Keep cache compatibility through an explicit `--configured` mode, and expose bounded hot inputs from existing page summaries and validated operation records.

**Tech Stack:** Python 3.9+, argparse, dataclasses, pathlib, restricted frontmatter parser, pytest, uv.

---

## File Map

- Create `obsidian_wiki/page_graph.py`: pure page identity and link extraction shared by lint and transaction validation.
- Create `obsidian_wiki/transaction_validation.py`: issue/report types and semantic candidate/prospective-graph rules.
- Modify `obsidian_wiki/lint.py`: consume the shared page graph parser without changing lint output.
- Modify `obsidian_wiki/transaction.py`: safely assemble prospective pages, expose `validate`, and enforce validation in commit/retry.
- Modify `obsidian_wiki/local_state.py`: collect bounded summaries and recent validated operations for `hot inputs`.
- Modify `obsidian_wiki/cli.py`: add `transaction validate`, cache flags/`--configured`, warning routing, and `hot inputs`.
- Modify `tests/test_lint.py`: prove the parser extraction preserves lint behavior.
- Modify `tests/test_transaction.py`: cover semantic preflight, prospective links, read-only behavior, and commit enforcement.
- Modify `tests/test_cache.py`: cover explicit JSON flags, configured resolution, compatibility, and warning suppression.
- Modify `tests/test_local_state.py`: cover bounded deterministic hot inputs and CLI output.
- Modify `tests/test_portable_collaboration_e2e.py`: cover CJK source paths end to end.

## Task 1: Create and Verify the Development Worktree

**Files:** None.

- [ ] **Step 1: Create the isolated development branch from the approved baseline**

Run:

```bash
git worktree add /tmp/obsidian-wiki-preflight-agent -b feat/portable-agent-preflight feat/portable-repo-mode
```

Expected: worktree HEAD is `cba3708` and the original workspace stays on `feat/portable-repo-mode`.

- [ ] **Step 2: Install the project and test runner with uv**

Run:

```bash
uv sync
uv pip install pytest
```

Expected: `.venv` exists only in the temporary worktree and imports `obsidian_wiki` from that worktree.

- [ ] **Step 3: Record the known baseline**

Run:

```bash
uv run pytest -q
```

Expected baseline: `1687 passed`, `18 subtests passed`, and only `tests/test_portable_manifest_docs.py::test_readmes_have_one_aligned_portable_check_example` fails. Do not modify that documentation assertion on this development branch.

## Task 2: Extract Shared Page Graph Parsing

**Files:**
- Create: `obsidian_wiki/page_graph.py`
- Modify: `obsidian_wiki/lint.py`
- Test: `tests/test_lint.py`

- [ ] **Step 1: Write failing parser-equivalence tests**

Add imports and tests to `tests/test_lint.py`:

```python
from obsidian_wiki.page_graph import parse_page_text


def test_shared_page_parser_preserves_wikilink_and_markdown_targets(tmp_path: Path) -> None:
    text = """---
title: Alpha
category: concepts
tags: [example]
sources: [sources/a.md]
created: 2026-08-11
updated: 2026-08-11
---
# Alpha
[[concepts/Beta|Beta]] [Gamma](../references/gamma.md)
"""
    parsed = parse_page_text("concepts/alpha.md", text)

    assert parsed.path == "concepts/alpha.md"
    assert parsed.node_id == "concepts/alpha"
    assert parsed.slug == "alpha"
    assert parsed.title == "Alpha"
    assert parsed.links == ("beta", "gamma")


def test_lint_still_reports_broken_links_after_shared_parser_extraction(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    page = vault / "concepts" / "alpha.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: Alpha\ncategory: concepts\ntags: [example]\n"
        "sources: [sources/a.md]\ncreated: 2026-08-11\nupdated: 2026-08-11\n"
        "---\n# Alpha\n[[missing]]\n",
        encoding="utf-8",
    )

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["broken_links"] == [
        {"page": "concepts/alpha.md", "target": "missing"}
    ]
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_lint.py::test_shared_page_parser_preserves_wikilink_and_markdown_targets tests/test_lint.py::test_lint_still_reports_broken_links_after_shared_parser_extraction
```

Expected: collection fails because `obsidian_wiki.page_graph` does not exist.

- [ ] **Step 3: Implement the pure shared parser**

Create `obsidian_wiki/page_graph.py` with these public types and functions, moving the existing normalization and regex behavior from `lint.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
_MD_LINK_RE = re.compile(r"\[.*?\]\(([^)]+\.md[^)]*)\)")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_FIELD_RE = re.compile(r"^([A-Za-z_][\w-]*):", re.MULTILINE)


@dataclass(frozen=True)
class PageGraphRecord:
    path: str
    node_id: str
    slug: str
    title: str
    summary: str
    fields: frozenset[str]
    links: tuple[str, ...]
    text: str


def slug(text: str) -> str:
    return text.strip().lower().replace(" ", "-")


def normalise_node_id(raw: str) -> str:
    target = raw.strip().removeprefix("[[").removesuffix("]]")
    target = target.split("|", 1)[0].split("#", 1)[0].strip()
    if target.lower().endswith(".md"):
        target = target[:-3]
    return "/".join(slug(part) for part in target.strip("/").split("/") if part)


def parse_page_text(relative: str, text: str) -> PageGraphRecord:
    path = PurePosixPath(relative)
    front_match = _FRONTMATTER_RE.match(text)
    frontmatter = front_match.group(1) if front_match else ""
    fields = frozenset(_FIELD_RE.findall(frontmatter))
    values = parse_frontmatter_scalars(frontmatter)
    links = [slug(raw.split("/")[-1]) for raw in _WIKILINK_RE.findall(text)]
    links.extend(slug(PurePosixPath(href.split("#", 1)[0]).stem) for href in _MD_LINK_RE.findall(text))
    return PageGraphRecord(
        path=relative,
        node_id=normalise_node_id(path.with_suffix("").as_posix()),
        slug=slug(path.stem),
        title=values.get("title", "").strip() or path.stem,
        summary=values.get("summary", "").strip(),
        fields=fields,
        links=tuple(target for target in links if target),
        text=text,
    )
```

Implement `parse_frontmatter_scalars` by moving `_parse_frontmatter_values` unchanged from `lint.py`. Keep relationship parsing in `lint.py`; only page identity, scalar extraction, and ordinary body-link parsing move.

- [ ] **Step 4: Refactor lint to consume `PageGraphRecord`**

Replace the duplicated regex/slug/node parsing in `lint.py` with imports from `page_graph.py`. Keep `_parse_page(path, vault)` as a compatibility wrapper returning the current dictionary shape:

```python
def _parse_page(path: Path, vault: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_page_text(path.relative_to(vault).as_posix(), text)
    return {
        "path": parsed.path,
        "node_id": parsed.node_id,
        "slug": parsed.slug,
        "title": parsed.title,
        "summary": parsed.summary,
        "fields": set(parsed.fields),
        "links": list(parsed.links),
        "relationships": _typed_relationships(text) if text.startswith("---\n") else [],
    }
```

- [ ] **Step 5: Run focused and full lint tests**

Run:

```bash
uv run pytest -q tests/test_lint.py tests/test_graph_analysis.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add obsidian_wiki/page_graph.py obsidian_wiki/lint.py tests/test_lint.py
git commit -m "refactor: share portable page graph parsing"
```

## Task 3: Add Pure Transaction Validation Rules

**Files:**
- Create: `obsidian_wiki/transaction_validation.py`
- Test: `tests/test_transaction.py`

- [ ] **Step 1: Write failing unit tests for page semantics and graph overlays**

Add tests that import the new pure validator:

```python
from obsidian_wiki.transaction_validation import (
    ProspectivePage,
    validate_prospective_pages,
)


def test_candidate_semantics_reject_invalid_timestamp_and_category() -> None:
    page = ProspectivePage(
        path="concepts/a.md",
        text=PAGE.replace("category: concepts", "category: references").replace(
            "updated: 2026-08-07", "updated: made-up"
        ),
        candidate=True,
    )

    issues = validate_prospective_pages((page,), ("sources/a.md",))

    assert [(item.code, item.path) for item in issues] == [
        ("frontmatter-category-path", "concepts/a.md"),
        ("frontmatter-updated-invalid", "concepts/a.md"),
    ]


def test_prospective_graph_accepts_candidate_to_candidate_links() -> None:
    alpha = ProspectivePage(
        "concepts/alpha.md", PAGE.replace("title: A", "title: Alpha") + "[[beta]]\n", True
    )
    beta = ProspectivePage(
        "concepts/beta.md", PAGE.replace("title: A", "title: Beta"), True
    )

    assert validate_prospective_pages((alpha, beta), ("sources/a.md",)) == ()


def test_prospective_graph_reports_live_link_broken_by_deletion() -> None:
    live = ProspectivePage(
        "concepts/live.md", PAGE.replace("title: A", "title: Live") + "[[removed]]\n", False
    )

    issues = validate_prospective_pages((live,), ("sources/a.md",))

    assert [(item.code, item.path, item.target) for item in issues] == [
        ("broken-link", "concepts/live.md", "removed")
    ]
```

Also add parameterized tests for empty scalar values, scalar `tags`, scalar `sources`, date-only timestamps, timezone-aware timestamps, project overview categories, nested project categories, Markdown links, duplicate slugs, and a multi-source page that cites only one transaction source.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_transaction.py -k "candidate_semantics or prospective_graph"
```

Expected: collection fails because `transaction_validation` does not exist.

- [ ] **Step 3: Implement issue and page types**

Create `obsidian_wiki/transaction_validation.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from .frontmatter import FrontmatterError, parse_frontmatter
from .page_graph import PageGraphRecord, parse_page_text

_CATEGORIES = frozenset(
    {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
)


@dataclass(frozen=True)
class ProspectivePage:
    path: str
    text: str
    candidate: bool


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    target: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "path": self.path, "message": self.message}
        if self.target is not None:
            payload["target"] = self.target
        return payload
```

Implement `_timestamp`, `_expected_category`, `_validate_candidate_frontmatter`, and `validate_prospective_pages`. `_timestamp` accepts `YYYY-MM-DD` or any ISO timestamp with a timezone; `_expected_category` returns the nested category for `projects/<name>/<category>/...` and `projects` for `projects/<name>.md` or `projects/<name>/<name>.md`.

- [ ] **Step 4: Implement graph resolution over parsed pages**

`validate_prospective_pages` must sort issues by `(path, code, target or "")`, build slug and node-ID indexes, resolve path-qualified relationships by node ID and ordinary links by slug, and emit:

```python
ValidationIssue(
    code="broken-link",
    path=page.path,
    target=target,
    message=f"link target does not exist in prospective vault: {target}",
)
```

Duplicate slug identities emit `duplicate-page-identity` for every affected path. Self-links remain accepted, matching current lint behavior.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_transaction.py -k "candidate_semantics or prospective_graph or project_category or multi_source"
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add obsidian_wiki/transaction_validation.py tests/test_transaction.py
git commit -m "feat: validate prospective transaction pages"
```

## Task 4: Integrate Read-only Validation with TransactionManager

**Files:**
- Modify: `obsidian_wiki/transaction.py`
- Test: `tests/test_transaction.py`

- [ ] **Step 1: Write failing manager tests**

Add:

```python
def test_validate_is_read_only_and_reports_all_candidate_issues(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-validate")
    candidate_page(
        record,
        "concepts/a.md",
        PAGE.replace("updated: 2026-08-07", "updated: invalid") + "[[missing]]\n",
    )
    before = json.loads((record.workspace / "metadata.json").read_text(encoding="utf-8"))

    report = manager.validate("tx-validate")

    after = json.loads((record.workspace / "metadata.json").read_text(encoding="utf-8"))
    assert report.status == "fail"
    assert {issue.code for issue in report.issues} == {
        "frontmatter-updated-invalid",
        "broken-link",
    }
    assert before == after
    assert not (config.vault / "concepts/a.md").exists()


def test_commit_rejects_preflight_before_snapshot_or_mutation(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-invalid")
    candidate_page(record, "concepts/a.md", PAGE + "[[missing]]\n")

    with pytest.raises(TransactionError, match="transaction validation failed"):
        manager.commit("tx-invalid")

    payload = json.loads((record.workspace / "metadata.json").read_text(encoding="utf-8"))
    assert payload["status"] == "active"
    assert payload["snapshot_index"] == {}
    assert not (config.vault / "concepts/a.md").exists()
```

Add tests for live-to-deleted links, candidate replacements, invalid candidate ordinary files, preimage drift, and validation of CJK candidate/source paths.

- [ ] **Step 2: Run manager tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_transaction.py -k "validate_is_read_only or commit_rejects_preflight"
```

Expected: fails because `TransactionManager.validate` is missing and commit accepts a missing link.

- [ ] **Step 3: Add report type and prospective-page assembly**

In `transaction_validation.py`, add:

```python
@dataclass(frozen=True)
class TransactionValidationReport:
    transaction_id: str
    status: str
    candidate_pages: tuple[str, ...]
    deletions: tuple[str, ...]
    issues: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "status": self.status,
            "candidate_pages": list(self.candidate_pages),
            "deletions": list(self.deletions),
            "issues": [item.as_dict() for item in self.issues],
            "warnings": [item.as_dict() for item in self.warnings],
        }
```

In `TransactionManager`, implement `_prospective_pages(record, candidates)` using existing guarded directory/file reads. Skip `journal/operations`, replace live pages with candidate bytes by relative path, and omit declared deletions. Decode all bytes strictly as UTF-8.

- [ ] **Step 4: Implement `TransactionManager.validate`**

Add:

```python
def validate(self, transaction_id: str) -> TransactionValidationReport:
    record = self.load(transaction_id)
    if record.status not in {"active", "failed"}:
        raise TransactionError(
            f"cannot validate {record.status} transaction {transaction_id}"
        )
    candidates = self._enumerate_candidates(record)
    candidate_names = tuple(item.relative for item in candidates)
    overlap = sorted(set(candidate_names) & set(record.deletions))
    if overlap:
        raise TransactionError(
            "candidate and deletion target the same page: " + ", ".join(overlap)
        )
    self._verify_preimages(record, self._affected_preimage_paths(record, candidates))
    self._verify_existing_page_sources(record, candidate_names)
    pages = self._prospective_pages(record, candidates)
    issues = validate_prospective_pages(pages, record.source_ids)
    return TransactionValidationReport(
        transaction_id=transaction_id,
        status="fail" if issues else "pass",
        candidate_pages=candidate_names,
        deletions=record.deletions,
        issues=issues,
    )
```

If basic candidate parsing currently raises before issues can be accumulated, refactor `_enumerate_candidates` to preserve guarded reads while returning candidate-specific structural issues through the same report. Unsafe filesystem topology and preimage drift remain fatal `TransactionError`s rather than ordinary content issues.

- [ ] **Step 5: Enforce the same report in commit and retry**

At the start of `_commit_record`, before `snapshot_started = True`, call a private `_validate_record(record)` that returns both candidates and the report so candidate bytes are read once. Raise:

```python
if report.issues:
    summary = "; ".join(
        f"{issue.path}: {issue.code}: {issue.message}" for issue in report.issues
    )
    raise TransactionError(f"transaction validation failed: {summary}")
```

Do not change rollback handling after snapshot start.

- [ ] **Step 6: Run transaction tests**

Run:

```bash
uv run pytest -q tests/test_transaction.py tests/test_transaction_guidance.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add obsidian_wiki/transaction.py obsidian_wiki/transaction_validation.py tests/test_transaction.py
git commit -m "feat: preflight portable transactions"
```

## Task 5: Add `transaction validate` to the CLI

**Files:**
- Modify: `obsidian_wiki/cli.py`
- Test: `tests/test_transaction.py`

- [ ] **Step 1: Write failing parser and CLI tests**

Add:

```python
def test_transaction_validate_parser_accepts_full_json_flags() -> None:
    args = cli_module.build_parser().parse_args(
        ["transaction", "validate", "tx-1", "--json", "--pretty"]
    )
    assert args.transaction_id == "tx-1"
    assert args.json is True
    assert args.pretty is True


def test_transaction_validate_cli_returns_structured_findings(tmp_path: Path) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config)
    record = manager.begin([add_source(root)], transaction_id="tx-cli")
    candidate_page(record, "concepts/a.md", PAGE + "[[missing]]\n")

    result = run_cli(root, "transaction", "validate", "tx-cli", "--json", "--pretty")

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["status"] == "fail"
    assert payload["issues"][0]["code"] == "broken-link"
    assert "recovery" not in payload
```

Add a passing human-output test and an invalid transaction-ID error-envelope test.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_transaction.py -k "transaction_validate_parser or transaction_validate_cli"
```

Expected: parser rejects the unknown `validate` subcommand.

- [ ] **Step 3: Implement the command handler**

In `cli.py`:

```python
def cmd_transaction_validate(args: argparse.Namespace) -> int:
    from obsidian_wiki.transaction import TransactionError, TransactionManager

    manager = None
    try:
        manager = _transaction_manager()
        report = manager.validate(args.transaction_id)
    except (ConfigError, ManifestError, TransactionError) as exc:
        return _render_transaction_failure(
            args, exc, manager=manager, transaction_id=args.transaction_id
        )
    payload = report.as_dict()
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(
            f"transaction {args.transaction_id}: {report.status} "
            f"({len(report.issues)} issues)"
        )
        for issue in report.issues:
            print(f"{issue.code}: {issue.path}: {issue.message}")
    return 1 if report.status == "fail" else 0
```

Add `validate` as its own transaction parser before the generic state-action loop, with `allow_abbrev=False`, `transaction_id`, and `_add_json_args`.

- [ ] **Step 4: Run focused and transaction CLI tests**

Run:

```bash
uv run pytest -q tests/test_transaction.py tests/test_portable_write_protocol.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add obsidian_wiki/cli.py tests/test_transaction.py
git commit -m "feat: expose transaction validation CLI"
```

## Task 6: Normalize Cache Structured Output and Configured Resolution

**Files:**
- Modify: `obsidian_wiki/cli.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write failing cache CLI tests**

First change the existing `_run_from` helper to
`home.mkdir(exist_ok=True)` so a test can pre-populate a stale global config.
Then add to `TestCacheCLI`:

```python
@pytest.mark.parametrize("command", ["cache-check", "cache-update", "cache-hash"])
def test_cache_commands_accept_explicit_json(command, vault, src_file):
    arguments = {
        "cache-check": [str(vault), str(src_file)],
        "cache-update": [str(vault), str(src_file)],
        "cache-hash": [str(src_file)],
    }[command]
    proc = self._run(command, *arguments, "--json", "--pretty")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)
    assert "\n  " in proc.stdout


def test_cache_check_configured_resolves_portable_vault(self, portable_repo, tmp_path):
    root, _config = portable_repo
    source = root / "sources" / "组会纪要.md"
    source.write_text("会议", encoding="utf-8")
    proc = self._run_from(
        root,
        tmp_path / "home",
        "cache-check",
        "--configured",
        "sources/组会纪要.md",
        "--json",
        "--pretty",
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["new"] == ["sources/组会纪要.md"]
    assert proc.stderr == ""


def test_cache_default_json_does_not_emit_global_setup_warning(
    self, vault, src_file, tmp_path
):
    home = tmp_path / "home"
    config = home / ".obsidian-wiki" / "config"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"OBSIDIAN_VAULT_PATH={vault}\nOBSIDIAN_WIKI_VERSION=old\n",
        encoding="utf-8",
    )
    proc = self._run_from(home, home, "cache-check", str(vault), str(src_file))
    assert proc.returncode == 0
    assert json.loads(proc.stdout)
    assert proc.stderr == ""


def test_cache_json_structures_relevant_context_warning(
    self, portable_repo, vault, src_file, tmp_path
):
    root, _config = portable_repo
    proc = self._run_from(
        root,
        tmp_path / "home",
        "cache-check",
        str(vault),
        str(src_file),
        "--json",
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["context_warnings"][0]["code"] == "portable-context-overridden"
    assert proc.stderr == ""
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_cache.py -k "explicit_json or configured_resolves or default_json or structures_relevant"
```

Expected: explicit `--json` and `--configured` are rejected, and the default command emits the stale warning.

- [ ] **Step 3: Refactor cache parser arguments without removing legacy forms**

For `cache-check`, replace the two positional declarations with:

```python
cc.add_argument("paths", nargs="+", metavar="PATH")
cc.add_argument(
    "--configured",
    action="store_true",
    help="resolve the vault from config and treat every PATH as a source",
)
_add_json_args(cc)
cc.set_defaults(func=cmd_cache_check, json=True)
```

For `cache-update` and `cache-hash`, retain existing positionals, call `_add_json_args`, and set `json=True` because JSON remains the default. This makes explicit `--json` idempotent and suppresses the generic human warning path. Use `args.pretty` in every JSON print.

- [ ] **Step 4: Implement unambiguous configured resolution**

At the start of `cmd_cache_check`:

```python
if args.configured:
    runtime = _resolve_runtime()
    if runtime is None:
        return 1
    vault = runtime.vault
    sources_raw = args.paths
else:
    if len(args.paths) < 2:
        print(
            "error: cache-check requires VAULT SOURCE... or --configured SOURCE...",
            file=sys.stderr,
        )
        return 2
    vault = Path(args.paths[0]).expanduser().resolve()
    sources_raw = args.paths[1:]
sources = [Path(path).expanduser().resolve() for path in sources_raw]
```

When configured mode resolves Portable Repository mode, pass `runtime.portable` directly; otherwise use `_manifest_context_for_vault(vault)`. Do not infer mode from filenames.

Inspect the selected runtime with `_inspect_cli_runtime`: pass no explicit vault in
configured mode and pass the legacy vault in positional mode. Add
`context_warnings` to the JSON result with `_attach_context_warnings`; do not emit
those warnings to stderr because cache output is structured JSON by default.

- [ ] **Step 5: Run cache and runtime-context tests**

Run:

```bash
uv run pytest -q tests/test_cache.py tests/test_runtime_context.py tests/test_info_cli.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add obsidian_wiki/cli.py tests/test_cache.py
git commit -m "fix: normalize cache structured output"
```

## Task 7: Add Deterministic `hot inputs`

**Files:**
- Modify: `obsidian_wiki/local_state.py`
- Modify: `obsidian_wiki/cli.py`
- Test: `tests/test_local_state.py`

- [ ] **Step 1: Write failing hot-input tests**

Add:

```python
def test_hot_inputs_returns_bounded_summaries_and_recent_operations(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    page = config.vault / "concepts" / "缓存.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: 缓存\ncategory: concepts\ntags: [cache]\n"
        "sources: [sources/组会.md]\nsummary: 本地派生缓存。\n"
        "created: 2026-08-11\nupdated: 2026-08-11\n---\n# 缓存\n",
        encoding="utf-8",
    )
    operation = config.vault / "journal" / "operations" / "2026" / "08" / "20260811T010000Z-abcd.md"
    operation.parent.mkdir(parents=True)
    operation.write_text(
        render_operation(
            OperationChange(
                transaction_id="tx-hot",
                completed_at="2026-08-11T01:00:00Z",
                source_ids=("sources/组会.md",),
                created=("concepts/缓存.md",),
                updated=(),
                removed=(),
            )
        ),
        encoding="utf-8",
    )

    payload = hot_inputs(config, page_limit=20, operation_limit=5)

    assert payload["fingerprint"] == authoritative_fingerprint(config)
    assert payload["pages"] == [
        {"path": "concepts/缓存.md", "title": "缓存", "summary": "本地派生缓存。", "updated": "2026-08-11"}
    ]
    assert payload["operations"][0]["transaction_id"] == "tx-hot"


def test_hot_inputs_cli_is_read_only(config_fixture: PortableConfig, tmp_path: Path) -> None:
    before = set(config_fixture.root.rglob("*"))
    result = run_cli(tmp_path / "home", config_fixture.root, "hot", "inputs", "--json", "--pretty")
    after = set(config_fixture.root.rglob("*"))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pages"] == []
    assert before == after
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_local_state.py -k "hot_inputs"
```

Expected: fails because `hot_inputs` and the CLI subcommand do not exist.

- [ ] **Step 3: Implement bounded collection**

In `local_state.py`, add:

```python
def hot_inputs(
    config: PortableConfig,
    *,
    page_limit: int = 50,
    operation_limit: int = 10,
) -> dict[str, object]:
    if page_limit < 0 or operation_limit < 0:
        raise LocalStateError("hot input limits must be non-negative")
    _validate_vault(config)
    pages = _hot_page_summaries(config, limit=page_limit)
    operations = _recent_operations(config, limit=operation_limit)
    return {
        "fingerprint": authoritative_fingerprint(config),
        "pages": pages,
        "operations": operations,
    }
```

Use `parse_frontmatter` for `title`, `summary`, and `updated`. Skip root control pages and operations when gathering summaries. Sort pages by `(updated, path)` descending, then truncate. Validate operation files with `validate_operation`, sort by `completed_at` descending, and serialize transaction/source/change fields. Unsafe or invalid authoritative files fail closed with `LocalStateError`; no files are written.

- [ ] **Step 4: Add the CLI parser and handler**

Add `hot inputs` with `--pages` default 50, `--operations` default 10, and `_add_json_args`. The handler calls `hot_inputs`, emits JSON when `--json`, and otherwise prints the same JSON because the data has no useful lossy human form. Set `json=True` by default to prevent global setup warning pollution.

- [ ] **Step 5: Run local-state and operation tests**

Run:

```bash
uv run pytest -q tests/test_local_state.py tests/test_operations.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add obsidian_wiki/local_state.py obsidian_wiki/cli.py tests/test_local_state.py
git commit -m "feat: expose deterministic hot cache inputs"
```

## Task 8: Add CJK Portable Collaboration Regression Coverage

**Files:**
- Modify: `tests/test_portable_collaboration_e2e.py`

- [ ] **Step 1: Write the end-to-end Unicode test**

Add a test using existing portable-repository helpers:

```python
def test_cjk_source_id_survives_cache_transaction_operation_and_check(tmp_path: Path) -> None:
    root = setup_portable_repository(tmp_path / "知识库")
    source = root / "sources" / "meetings" / "2026-08-06-组会纪要.md"
    source.parent.mkdir(parents=True)
    source.write_text("# 组会纪要\n版本管理决策。\n", encoding="utf-8")

    cache = run_cli(root, "cache-check", "--configured", str(source.relative_to(root)), "--json")
    assert json.loads(cache.stdout)["new"] == [
        "sources/meetings/2026-08-06-组会纪要.md"
    ]

    begin = run_cli(root, "transaction", "begin", "--source", str(source), "--json")
    transaction = json.loads(begin.stdout)
    candidate = Path(transaction["candidate_vault"]) / "references" / "2026-08-06-组会纪要.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(cjk_candidate_page(transaction["started_at"]), encoding="utf-8")

    assert run_cli(root, "transaction", "validate", transaction["transaction_id"], "--json").returncode == 0
    assert run_cli(root, "transaction", "commit", transaction["transaction_id"], "--json").returncode == 0
    assert run_cli(root, "check", "--json").returncode == 0

    shard = root / "wiki" / ".manifest" / "sources" / "meetings" / "2026-08-06-组会纪要.md.json"
    assert json.loads(shard.read_text(encoding="utf-8"))["source_id"] == (
        "sources/meetings/2026-08-06-组会纪要.md"
    )
```

Implement `cjk_candidate_page(started_at)` in the test module with required frontmatter, the exact Source ID, and no invented absolute path.

- [ ] **Step 2: Run the new test**

Run:

```bash
uv run pytest -q tests/test_portable_collaboration_e2e.py -k cjk
```

Expected: pass with the implemented commands. To prove the regression test, temporarily revert the cache configured-mode dispatch and confirm this test fails before restoring it.

- [ ] **Step 3: Run all portable E2E tests**

Run:

```bash
uv run pytest -q tests/test_portable_collaboration_e2e.py tests/test_portable_check.py
```

Expected: all selected tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_portable_collaboration_e2e.py
git commit -m "test: preserve CJK portable source identities"
```

## Task 9: Development Branch Verification

**Files:** None.

- [ ] **Step 1: Run focused feature suites**

Run:

```bash
uv run pytest -q tests/test_transaction.py tests/test_cache.py tests/test_local_state.py tests/test_lint.py tests/test_operations.py tests/test_portable_collaboration_e2e.py tests/test_portable_check.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the full suite**

Run:

```bash
uv run pytest -q
```

Expected on the development branch: the same single pre-existing README assertion may remain; there must be no new failures. After combining with the documentation branch, the complete suite must pass.

- [ ] **Step 3: Run static repository checks**

Run:

```bash
git diff feat/portable-repo-mode...HEAD --check
uv build
```

Expected: no whitespace errors and wheel/sdist build succeeds.

- [ ] **Step 4: Review branch scope**

Run:

```bash
git status --short
git log --oneline feat/portable-repo-mode..HEAD
git diff --stat feat/portable-repo-mode...HEAD
```

Expected: clean worktree; only Python implementation and automated development tests are present.

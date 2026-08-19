# External Adapter Preflight-Only Reads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make successful exact-root `info --json` then `check` the Adapter's only static filesystem safety boundary and remove the unreliable per-read metadata protocol.

**Architecture:** The CLI remains responsible for deterministic static topology validation. After serialized preflight, the generated Adapter permits ordinary size-bounded reads in the approved owner-controlled, local, quiescent repository while preserving routing, authority order, explicit-root command binding, and recovery review. No reader API or runtime-skill change is introduced.

**Tech Stack:** Python 3.9+, pytest, Markdown Skill templates, existing `obsidian_wiki.agent_adapter`, `llmwikiops` CLI, `uv`, Git.

---

## Files and responsibilities

- `obsidian_wiki/_data/adapter/SKILL.md.in`: generated global Adapter runtime protocol.
- `tests/test_agent_context_boundary.py`: structural contract for preflight, bounded reads, routing, and authority order.
- `tests/test_agent_adapter.py`: rendered Adapter forbidden/required surface, renderer invariants, and size bound.
- `docs/agents.md`: human-facing external authority/read protocol.
- `tests/test_portable_human_docs.py`: executable human-documentation contract.
- `docs/superpowers/specs/2026-08-19-external-adapter-preflight-only-reads-design.md`: approved design authority; do not edit during implementation unless a contradiction is found and reviewed.

## Constraints shared by every task

- Preserve the complete Adapter bootstrap read and unique terminal EOF marker.
- Preserve exact immutable `<wiki-cli>` / `<git-cli>` binding and serialized `info --json` then `check` before ordinary external access.
- Preserve direct-only skill enumeration, 64 KiB frontmatter, 1 MiB complete-read consumption limits, catalog merge/reroute, authority order, one-body-per-step loading, candidate review, returned recovery argv, and hot-refresh diff gates.
- Do not weaken `obsidian_wiki.portable`, `check_portable_repo`, or their static unsafe-topology tests.
- Do not add a CLI reader, default repository, environment/profile/recent-root selector, `chdir`, automatic Adapter installation, or Wiki/runtime-skill migration.
- Start every behavior change with an observed RED, implement the minimum GREEN, and commit before moving to the next task.

## Task 1: Replace repeated-read contracts with the preflight-only contract

**Files:**

- Modify: `tests/test_agent_context_boundary.py:168-258`
- Modify: `tests/test_agent_adapter.py:666-704`
- Modify: `tests/test_portable_human_docs.py:321-394`

- [ ] **Step 1: Replace the four obsolete per-read tests with one failing boundary test**

Delete `test_external_adapter_checks_metadata_before_any_file_bytes`,
`test_external_adapter_forbids_following_and_gnu_only_stat_forms`,
`test_external_adapter_rechecks_bounds_for_each_separate_byte_read`, and
`test_external_adapter_rechecks_bounds_for_formatting_and_citation_rereads`.
Add this test in their place:

```python
def test_external_adapter_uses_preflight_only_bounded_read_boundary() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    for required in (
        "After successful preflight, use ordinary bounded file tools",
        "Read routing frontmatter within 64 KiB",
        "Limit each complete external file read—including authority, task, candidate, "
        "query-result, hot, recovery, formatting, citation, JSON, hash, and preimage "
        "reads—to 1 MiB.",
        "consumption limits, not a per-read metadata or TOCTOU protocol",
        "If relevant repository evidence changes after preflight",
    ):
        assert required in template_text

    for forbidden in (
        "os.lstat",
        "stat.S_ISREG",
        "os.path.isfile",
        "Path.is_file",
        "same process immediately before reading",
        "A prior command's check never authorizes a later read",
        "hash-only reads",
    ):
        assert forbidden not in template_text
```

- [ ] **Step 2: Strengthen the rendered-surface test before changing the template**

Extend the `forbidden` tuple in
`test_template_uses_static_repository_preflight_instead_of_safe_reader` with:

```python
        "os.lstat",
        "stat.S_ISREG",
        "os.path.isfile",
        "Path.is_file",
        "same process immediately before reading",
        "hash-only reads",
```

Extend its `required` tuple with:

```python
        "consumption limits, not a per-read metadata or TOCTOU protocol",
```

- [ ] **Step 3: Change the human-doc contract to reject the old rule**

In `test_external_adapter_docs_state_static_quiescent_repository_boundary`,
replace the required phrase `inspect ordinary-file metadata before every full read`
with `limit each complete external file read to 1 mib`, then add:

```python
    for forbidden in (
        "metadata before every full read",
        "same-process metadata",
        "os.lstat",
        "stat.s_isreg",
    ):
        assert forbidden not in agents_lower, forbidden
```

- [ ] **Step 4: Run the focused tests and verify RED**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_agent_adapter.py \
  tests/test_portable_human_docs.py \
  -q -k 'preflight_only or static_repository_preflight or static_quiescent'
```

Expected: the new tests fail because the current Adapter still contains
`os.lstat`, `stat.S_ISREG`, same-process reread rules, and the human docs still
require metadata inspection before every full read. Existing preflight tests pass.

- [ ] **Step 5: Commit only the RED contract**

```bash
git add tests/test_agent_context_boundary.py \
  tests/test_agent_adapter.py \
  tests/test_portable_human_docs.py
git commit -m "test: define preflight-only adapter reads"
```

## Task 2: Simplify the Adapter and human protocol

**Files:**

- Modify: `obsidian_wiki/_data/adapter/SKILL.md.in:69-102`
- Modify: `docs/agents.md:43-50`
- Modify: `docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md`
- Modify: `tests/test_agent_context_boundary.py`
- Test: `tests/test_agent_adapter.py`
- Modify: `tests/test_portable_human_docs.py`

- [ ] **Step 1: Add failing byte-bound and human-doc contract assertions**

Add this section-scoped test to `tests/test_agent_context_boundary.py`:

```python
def test_external_adapter_distinguishes_byte_bounds_from_output_truncation() -> None:
    template = ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    section = template.split("## Catalog and bounded reads", 1)[1].split(
        "## Route and load authority", 1
    )[0]
    section_text = " ".join(section.split())

    for required in (
        "Read at most 1,048,576 bytes",
        "Require an explicit EOF or completeness indication separate from delivered content",
        "If the tool cannot establish completeness within that limit, reject the read",
        "token or tool-output limit is not a byte bound or proof of EOF",
        "Do not treat truncated output as a complete read",
        "bare `cat` is allowed only after",
        "at-most-1-MiB size is otherwise established",
    ):
        assert required in section_text
```

In the `discovery_boundary` tuple in
`test_external_adapter_docs_state_static_quiescent_repository_boundary` in
`tests/test_portable_human_docs.py`, add:

```python
        "read at most 1,048,576 bytes",
        "require an explicit eof or completeness indication separate from delivered content",
        "if the tool cannot establish completeness within that limit, reject the read",
        "token or tool-output limit is not a byte bound or proof of eof",
        "do not treat truncated output as a complete read",
```

- [ ] **Step 2: Run the smallest tests and verify RED**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  -q -k 'distinguishes_byte_bounds_from_output_truncation or static_quiescent_repository_boundary'
```

Expected: both tests fail because the Adapter and human documentation do not yet
state the 1,048,576-byte delivered-content maximum, explicit separate EOF or
completeness indication, and rejection when the tool cannot establish it.

- [ ] **Step 3: Replace the metadata/reread program with the approved bounded-read text**

Keep the direct one-level enumeration paragraphs. Replace the current region from
`Before opening or reading any external file content` through the sentence ending
`Do not add identity, digest, double-read, or TOCTOU machinery.` with:

```markdown
After successful preflight, use ordinary bounded file tools against paths derived
from the verified configuration. Read routing frontmatter within 64 KiB, require
one complete frontmatter block, and reject malformed or duplicate skill names.
Limit each complete external file read—including authority, task, candidate,
query-result, hot, recovery, formatting, citation, JSON, hash, and preimage
reads—to 1 MiB.
Read at most 1,048,576 bytes. Require an explicit EOF or completeness indication
separate from delivered content. If the tool cannot establish completeness within
that limit, reject the read. A token or tool-output limit is not a byte bound or
proof of EOF. Do not treat truncated output as a complete read. A bare `cat` is
allowed only after the file's at-most-1-MiB size is otherwise established.

These are consumption limits, not a per-read metadata or TOCTOU protocol. Do not
prescribe `lstat`, link-count checks, snapshots, same-process revalidation, or a
special reader before each open. Static unsafe topology is the responsibility of
the successful CLI preflight in this supported quiescent repository. If relevant
repository evidence changes after preflight, stop and restart only after the
repository is quiescent.
```

Do not alter the complete-Adapter bootstrap gate, preflight ordering, catalog
merge, authority sequence, execution, query, transaction, recovery, hot-refresh,
or terminal EOF sections.

Keep the migrated routing/frontmatter coverage in
`test_external_adapter_finishes_catalog_reroute_before_any_authority_body` and
`test_external_adapter_frontmatter_reads_are_bounded_to_64_kib`; both assert the
post-preflight catalog routing boundary rather than the removed safe-reader
mechanics.

- [ ] **Step 4: Update the human-facing authority read step**

Replace step 4 in `docs/agents.md` with:

```markdown
4. Load full ordinary UTF-8 authorities in this exact order: `<root>/AGENTS.md` if present, `<root>/.skills/llm-wiki/SKILL.md`, optional `<vault>/AGENTS.md`, and the selected task's direct `SKILL.md`. Limit each complete external file read to 1 MiB. Read at most 1,048,576 bytes. Require an explicit EOF or completeness indication separate from delivered content. If the tool cannot establish completeness within that limit, reject the read. A token or tool-output limit is not a byte bound or proof of EOF. Do not treat truncated output as a complete read. This is a content-consumption bound after successful static preflight, not a requirement to repeat metadata or TOCTOU checks before each read. If `llm-wiki` is selected, read it once.
```

- [ ] **Step 5: Run the focused contract tests and verify GREEN**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_agent_adapter.py \
  tests/test_portable_human_docs.py \
  -q -k 'preflight_only or static_repository_preflight or static_quiescent or frontmatter or rendered_frontmatter or byte_bounds'
```

Expected: all selected tests pass; rendered Adapter remains below 16,384 bytes and
retains one independent terminal EOF marker.

- [ ] **Step 6: Run owner suites and documentation synchronization**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  -q
uv run python tools/check_readme_sync.py
```

Expected: all tests pass and `README_ZH.md is up to date with README.md.` No README
edit is expected because both landing pages already describe preflight and the
quiescent boundary without prescribing per-read metadata.

- [ ] **Step 7: Validate the generated Skill and commit**

Render/install the Adapter into a fresh temporary Codex home, run the bundled
strict validator, and require `Skill is valid!`. Then run:

```bash
git diff --check
git add obsidian_wiki/_data/adapter/SKILL.md.in \
  docs/agents.md \
  docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py
git commit -m "refactor: make adapter reads preflight-only"
```

## Task 3: Prove static safety and artifact/install parity remain intact

**Files:**

- Test only: `tests/test_portable_config.py`
- Test only: `tests/test_portable_check.py`
- Test only: `tests/test_asset_artifact_parity.py`
- Test only: `tests/test_agent_adapter.py`
- Evidence only: a fresh directory below `/tmp/llmwikiops-preflight-only-artifacts.*`

- [ ] **Step 1: Run retained static-topology coverage**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_config.py \
  tests/test_portable_check.py \
  -q
```

Expected: all tests pass, including symlink, hardlink, special-file, unsafe-name,
and escaping-path rejection. Do not change these tests to accommodate the Adapter.

- [ ] **Step 2: Verify source, wheel, and sdist Adapter bytes**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_asset_artifact_parity.py \
  tests/test_installation_policy.py \
  -q
```

Expected: all artifact parity and installation-policy tests pass with identical
packaged Adapter bytes and LF-only packaged Markdown.

- [ ] **Step 3: Verify fresh Codex and Claude installations**

Install into separate fresh homes using the current worktree, compare installed
`SKILL.md` bytes, parse both ownership records, and require each recorded digest
to match the installed bytes. Re-run both installs and require `unchanged`. Do not
touch the user's live Agent homes.

- [ ] **Step 4: Commit only if a packaging defect required a RED/GREEN fix**

If all verification passes, make no empty commit. If a real packaging defect is
found, add a focused failing artifact test first, implement the minimum fix, rerun
Steps 1-3, and commit only the files involved with:

```bash
git commit -m "fix: package preflight-only adapter"
```

## Task 4: Run fresh behavioral acceptance under the revised boundary

**Files:**

- Modify only if Step 1 observes an obsolete protocol expectation: `tests/test_external_wiki_e2e.py`
- Evidence only: a fresh directory below `/tmp/llmwikiops-preflight-only-eval.*`

- [ ] **Step 1: Preserve automated external lifecycle coverage**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_external_wiki_e2e.py \
  tests/test_explicit_repository_cli.py \
  -q
```

Expected: all tests pass. If a fixture still requires a per-read metadata syscall,
first change it to fail for that obsolete expectation, then update only that
fixture and commit the RED/GREEN test change.

- [ ] **Step 2: Build a fresh isolated campaign**

Create a new eval root with isolated `HOME`, `CODEX_HOME`, `UV_CACHE_DIR`, XDG
directories, temp directory, virtual environment, and Git-backed fixtures. Build
and install exact current HEAD with copy link mode. Record HEAD/tree, CLI version,
Adapter SHA-256, fixture hashes, prompt hashes, runtime/model, UTC times, every
tool command, and before/after snapshots. Use an outer sandbox that makes only the
eval root writable; never inspect or archive credential contents.

- [ ] **Step 3: Run external query, recovery/hot, and override scenarios**

Run one fresh genuine attempt for each:

1. external query returning a fixture-only sentinel;
2. retained transaction recovery using the exact CLI-returned argv, candidate
   review, completed retry, content-changing hot refresh, diff, and mark-current;
3. repository-local same-name metadata override, reroute, complete canonical and
   selected bodies, override sentinel, and one exact-root query.

All scenarios must load the complete Adapter before external access, complete
`info` then `check` serially, keep the business CWD, use exact `-C`/`git -C`, obey
direct-only enumeration and authority order, and avoid alternate roots and
unexpected writes.

- [ ] **Step 4: Apply the revised verdict rule**

Ordinary post-preflight reads are valid without a same-process `lstat`, type,
link-count, or size syscall before each open. Still fail a run for preflight-order
violations, recursive/unbounded hunting, a read exceeding the stated content
bound, wrong-root commands, combined/out-of-order authority bodies, reconstructed
recovery commands, unreviewed candidates, read-only hot marking, unexpected
writes, or observed concurrent repository change.

Archive raw JSONL, command audits, verdict JSON, snapshots, and a SHA-256 manifest.
Do not commit fixtures or transcripts.

- [ ] **Step 5: Commit only automated-test changes**

If `tests/test_external_wiki_e2e.py` changed through a real RED/GREEN loop, rerun
its owner suites and commit it with:

```bash
git add tests/test_external_wiki_e2e.py
git commit -m "test: accept preflight-only external reads"
```

## Task 5: Final verification and review

**Files:**

- Review all files changed since `f071780`

- [ ] **Step 1: Run focused final suites**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  tests/test_asset_artifact_parity.py \
  tests/test_installation_policy.py \
  tests/test_portable_config.py \
  tests/test_portable_check.py \
  tests/test_external_wiki_e2e.py \
  tests/test_explicit_repository_cli.py \
  -q
```

- [ ] **Step 2: Run repository checks**

Run:

```bash
uv run python tools/check_readme_sync.py
uv run --with ruff ruff check --select E4,E7,E9,F82 \
  obsidian_wiki/agent_adapter.py \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_external_wiki_e2e.py
git diff --check f071780..HEAD
```

Expected: README sync, fatal Ruff correctness rules, and diff check all pass. The
broader latest-Ruff I001/UP032 findings already present at `f071780` are outside
this behavior change and must be reported rather than mixed into it.

- [ ] **Step 3: Run the full exact suite from `AGENTS.md`**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest \
  -q -p no:cacheprovider
```

Expected: zero failures and all subtests pass.

- [ ] **Step 4: Perform spec and quality reviews**

Review the final diff against both static-repository design documents. Require:

- `info --json` then `check` remains the only pre-ordinary-read static boundary;
- no per-read metadata/TOCTOU orchestration remains in Adapter or docs;
- resource bounds, routing, authority, recovery/hot, and explicit-root rules remain;
- Python static topology validation and its tests are unchanged;
- artifacts and installed Adapter bytes agree;
- all three revised-boundary behavioral scenarios pass without outside writes.

Resolve each substantive finding with its own RED/GREEN loop and focused commit.

- [ ] **Step 5: Confirm clean handoff**

Run:

```bash
git status --short
git log --oneline f071780..HEAD
git fsck --full
```

Expected: clean worktree, all scoped commits visible, and no corrupt objects. A
known unrelated dangling blob may remain informational. Report behavior evidence
paths and the recoverable Git-corruption quarantine; do not reinstall live
Adapters, update an external Wiki, push, or publish without separate authority.

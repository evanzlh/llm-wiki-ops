# Portable Agent Skill and Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every portable write workflow locally complete and unambiguous, document the new preflight/cache/hot contracts, and align timestamp, CWD, Unicode, and CLI-version guidance.

**Architecture:** Keep `.skills/llm-wiki/SKILL.md` as the canonical portable protocol, but require each write skill to expose explicit Portable and Personal completion branches instead of relying on a distant suppression sentence. Human CLI/configuration/architecture docs describe deterministic command contracts; tests enforce mode headings, portable prohibitions, timestamp usage, and README translation invariants.

**Tech Stack:** Markdown skills, Python documentation contract tests, repository README parity checker, pytest, uv.

---

## File Map

- Modify `.skills/llm-wiki/SKILL.md`: canonical validate/timestamp/CWD/cache/hot/Unicode rules.
- Modify `.skills/wiki-ingest/SKILL.md`: fully split Portable and Personal completion workflows.
- Modify `.skills/wiki-update/SKILL.md`: fully split Portable and Personal completion workflows.
- Modify the remaining portable write skills listed in Task 5: explicit mode completion headings and stop boundaries.
- Modify `AGENTS.md`: advertise the new portable transaction and cache conventions.
- Modify `docs/cli.md`: command syntax, JSON, warning, validation, and hot input contracts.
- Modify `docs/architecture.md`: prospective-vault validation and deterministic/semantic boundary.
- Modify `docs/configuration.md`: shell-runtime resolution and `requires_cli` range guidance.
- Modify `docs/agents.md` and `docs/skills.md`: agent CWD and write-skill mode behavior.
- Modify `tests/test_portable_skill_protocol.py`: executable skill-contract assertions.
- Modify `tests/test_portable_write_protocol.py`: canonical protocol assertions.
- Modify `tests/test_portable_manifest_docs.py`: correct the stale README count invariant.

## Task 1: Add Failing Documentation Contract Tests

**Files:**
- Modify: `tests/test_portable_skill_protocol.py`
- Modify: `tests/test_portable_write_protocol.py`
- Modify: `tests/test_portable_manifest_docs.py`

- [ ] **Step 1: Define the portable write-skill inventory**

Add to `tests/test_portable_skill_protocol.py`:

```python
PORTABLE_WRITE_SKILLS = (
    ".skills/claude-history-ingest/SKILL.md",
    ".skills/codex-history-ingest/SKILL.md",
    ".skills/copilot-history-ingest/SKILL.md",
    ".skills/cross-linker/SKILL.md",
    ".skills/daily-update/SKILL.md",
    ".skills/hermes-history-ingest/SKILL.md",
    ".skills/openclaw-history-ingest/SKILL.md",
    ".skills/pi-history-ingest/SKILL.md",
    ".skills/tag-taxonomy/SKILL.md",
    ".skills/wiki-capture/SKILL.md",
    ".skills/wiki-dashboard/SKILL.md",
    ".skills/wiki-dedup/SKILL.md",
    ".skills/wiki-import/SKILL.md",
    ".skills/wiki-ingest/SKILL.md",
    ".skills/wiki-lint/SKILL.md",
    ".skills/wiki-rebuild/SKILL.md",
    ".skills/wiki-research/SKILL.md",
    ".skills/wiki-stage-commit/SKILL.md",
    ".skills/wiki-status/SKILL.md",
    ".skills/wiki-synthesize/SKILL.md",
    ".skills/wiki-update/SKILL.md",
)
```

- [ ] **Step 2: Add explicit-mode and canonical-command assertions**

Add:

```python
def test_portable_write_skills_have_local_completion_branches() -> None:
    for relative in PORTABLE_WRITE_SKILLS:
        text = _text(relative)
        assert "## Portable Repository completion" in text, relative
        assert "## Personal mode completion" in text, relative
        portable = text.split("## Portable Repository completion", 1)[1].split(
            "## Personal mode completion", 1
        )[0]
        assert "transaction validate" in portable, relative
        assert "Stop the portable workflow here" in portable, relative


def test_canonical_portable_protocol_defines_runtime_safety_rules() -> None:
    text = _text(".skills/llm-wiki/SKILL.md")
    for phrase in (
        "transaction validate <id> --json --pretty",
        "created = updated = started_at",
        "preserve the existing `created`",
        "keep the repository root as the command working directory",
        "do not `cd` into `candidate_vault`",
        "cache-check --configured",
        "hot inputs --json --pretty",
        "preserve Unicode filenames",
    ):
        assert phrase in text


def test_portable_ingest_completion_forbids_personal_tracking_steps() -> None:
    text = _text(".skills/wiki-ingest/SKILL.md")
    portable = text.split("## Portable Repository completion", 1)[1].split(
        "## Personal mode completion", 1
    )[0]
    for forbidden in (
        "cache-update",
        "Add entries for any new pages",
        "Append an entry",
        "Rewrite the **Recent Activity** section",
    ):
        assert forbidden not in portable
```

- [ ] **Step 3: Correct the README invariant test**

Replace `test_readmes_have_one_aligned_portable_check_example` with:

```python
def test_readmes_have_aligned_portable_check_examples() -> None:
    english = _text("README.md")
    chinese = _text("README_ZH.md")
    command = "obsidian-wiki check"

    assert english.count(command) > 0
    assert english.count(command) == chinese.count(command)
    assert command in english.split("## Start a portable team wiki", 1)[1]
    assert command in chinese.split("## 创建便携式团队知识库", 1)[1]
    assert "README_TW.md" not in english + chinese
```

- [ ] **Step 4: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_portable_skill_protocol.py tests/test_portable_write_protocol.py tests/test_portable_manifest_docs.py
```

Expected: the README invariant now passes; mode-branch and new-command assertions fail because skills have not been rewritten.

- [ ] **Step 5: Commit the corrected invariant and failing contracts**

```bash
git add tests/test_portable_skill_protocol.py tests/test_portable_write_protocol.py tests/test_portable_manifest_docs.py
git commit -m "test: define portable agent workflow contracts"
```

The commit intentionally contains failing future-behavior tests and is followed immediately by Tasks 2–5 on the same branch.

## Task 2: Update the Canonical Portable Protocol

**Files:**
- Modify: `.skills/llm-wiki/SKILL.md`
- Modify: `AGENTS.md`
- Test: `tests/test_portable_write_protocol.py`

- [ ] **Step 1: Rewrite the canonical transaction lifecycle**

Replace the Portable Repository subsection under `## Portable Write Protocol` with a locally complete sequence containing this exact command flow:

```markdown
## Portable Repository completion

Keep the repository root as the command working directory. Record the absolute
runtime-only `candidate_vault` returned by `transaction begin`; do not `cd` into
`candidate_vault`, and never write that absolute path into a page or tracked config.

1. Begin with authoritative source files:
   `obsidian-wiki transaction begin --source sources/a.md --json --pretty`.
2. For a new page, set `created = updated = started_at`. For an update, preserve
   the existing `created` and set `updated = started_at`.
3. Write candidate knowledge pages at final vault-relative paths below
   `candidate_vault`; declare removals with `transaction delete`.
4. Run `obsidian-wiki transaction validate <id> --json --pretty`. Review every
   issue and do not commit a failing report.
5. Run `obsidian-wiki transaction commit <id> --json --pretty` only after the
   candidate report passes.
6. Run `obsidian-wiki hot status --json` before using `hot.md`. If stale, run
   `obsidian-wiki hot inputs --json --pretty`, regenerate the semantic snapshot,
   and run `obsidian-wiki hot mark-current --json`.
7. Do not run `cache-update`, edit manifest shards, edit `index.md`/`log.md`, or
   commit/push Git state. Transaction commit owns shards and the operation page.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Personal mode retains direct page writes, manifest v1, `index.md`, `log.md`,
`hot.md`, optional `_staging/`, QMD refresh, and the existing Git snapshot rules.
```

Keep the existing recovery actions after the validation/commit step. Add one sentence that page `sources` may be a non-empty subset of the transaction Source IDs.

- [ ] **Step 2: Add cache and Unicode rules near manifest v2**

Document this portable freshness command:

```bash
obsidian-wiki cache-check --configured sources/a.md sources/组会纪要.md --json --pretty
```

State that JSON is the default but explicit `--json` is accepted. State that Source IDs and knowledge filenames preserve Unicode exactly and must not be transliterated or normalized by the agent.

- [ ] **Step 3: Align root AGENTS.md**

In the Configuration and Portable Write Protocol guidance in `AGENTS.md`, add the same invariants in concise form:

```markdown
- Keep repo root as command CWD; use absolute candidate paths only in memory.
- Validate candidates before commit.
- Use transaction `started_at` for page timestamps.
- Use `cache-check --configured` instead of assuming a shell-exported vault.
- Preserve Unicode Source IDs and filenames exactly.
```

Do not add command-detail duplication outside the managed Obsidian Wiki section.

- [ ] **Step 4: Run canonical protocol tests**

Run:

```bash
uv run pytest -q tests/test_portable_write_protocol.py tests/test_portable_skill_protocol.py -k "canonical or runtime_safety"
```

Expected: canonical protocol assertions pass; per-skill branch assertions still fail.

- [ ] **Step 5: Commit**

```bash
git add .skills/llm-wiki/SKILL.md AGENTS.md tests/test_portable_write_protocol.py
git commit -m "docs: define portable transaction preflight"
```

## Task 3: Split `wiki-ingest` into Explicit Completion Workflows

**Files:**
- Modify: `.skills/wiki-ingest/SKILL.md`
- Test: `tests/test_portable_skill_protocol.py`

- [ ] **Step 1: Replace shell-variable cache examples**

In Append Mode, show two explicit forms:

```markdown
Portable Repository mode:

```bash
obsidian-wiki cache-check --configured <source1> [source2 ...] --json --pretty
```

Personal mode, after resolving a concrete vault path in agent memory:

```bash
obsidian-wiki cache-check <resolved-vault-path> <source1> [source2 ...] --json --pretty
```
```

Explain that resolving config gives the agent a runtime value but does not export a variable into its parent shell.

- [ ] **Step 2: Add the complete portable terminal branch before tracking steps**

After Step 6, insert `## Portable Repository completion` with these required actions:

```markdown
1. Keep repository root as CWD and begin one transaction with all authoritative
   sources used by the candidate pages.
2. Use `started_at` for new/updated page timestamps.
3. Write only to the returned absolute `candidate_vault`; do not change CWD.
4. Declare deletions, then run `transaction validate` and review all findings.
5. Commit only a passing candidate report.
6. If local hot context is needed, use `hot status` → `hot inputs` → agent write
   → `hot mark-current`.
7. Report created/updated/removed pages and stop.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`,
write `hot.md` as part of the transaction, refresh Personal QMD tracking, create
a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.
```

- [ ] **Step 3: Mark the legacy terminal steps Personal-only**

Insert `## Personal mode completion` immediately before the current Step 7. Rename headings to:

```markdown
### Personal Step 7: Update Manifest and Special Files
### Personal Step 8: Refresh QMD Wiki Index
### Personal Quality Checklist
```

Remove the current Portable manifest-v2 `cache-update` subsection entirely. Keep manifest v1, index, log, hot, staging, QMD, and Personal fallback behavior unchanged.

- [ ] **Step 4: Make the checklist mode-correct**

Shared quality items cover frontmatter, sources, links, provenance, summaries, and relationships. Personal-only items cover `index.md`, `log.md`, `hot.md`, QMD, and `_staging/`. Portable-only items cover validate pass, no central-file writes, and repository-relative Source IDs.

- [ ] **Step 5: Run ingest contract tests**

Run:

```bash
uv run pytest -q tests/test_portable_skill_protocol.py -k "portable_ingest or local_completion"
```

Expected: `wiki-ingest` assertions pass; remaining write skills still fail inventory-wide assertions.

- [ ] **Step 6: Commit**

```bash
git add .skills/wiki-ingest/SKILL.md tests/test_portable_skill_protocol.py
git commit -m "docs: split portable ingest completion"
```

## Task 4: Split `wiki-update` into Explicit Completion Workflows

**Files:**
- Modify: `.skills/wiki-update/SKILL.md`
- Test: `tests/test_portable_skill_protocol.py`

- [ ] **Step 1: Replace the early suppression paragraph**

Keep config resolution at the top, but replace “suppress all steps below” with:

```markdown
Select one terminal workflow after the shared analysis and page-writing steps:
Portable Repository completion or Personal mode completion. Never mix their
tracking operations.
```

- [ ] **Step 2: Add the complete portable terminal branch**

Before the current tracking section, add `## Portable Repository completion` with repository-root CWD, authoritative source requirement, `started_at`, candidate writes, validate, commit, optional hot-input flow, explicit central-file/cache/Git prohibitions, and the exact stop sentence.

- [ ] **Step 3: Mark the current tracking/QMD sections Personal-only**

Insert `## Personal mode completion` and move/rename the current manifest v1, cache update, `index.md`, `log.md`, `hot.md`, and QMD instructions under it. Remove Portable manifest-v2 `cache-update` language because transaction commit owns shards.

- [ ] **Step 4: Run update-skill tests**

Run:

```bash
uv run pytest -q tests/test_portable_skill_protocol.py -k "wiki_update or local_completion"
```

Expected: `wiki-update` assertions pass.

- [ ] **Step 5: Commit**

```bash
git add .skills/wiki-update/SKILL.md tests/test_portable_skill_protocol.py
git commit -m "docs: split portable project updates"
```

## Task 5: Audit and Gate the Remaining Portable Write Skills

**Files:**
- Modify: the remaining 19 paths in `PORTABLE_WRITE_SKILLS`
- Test: `tests/test_portable_skill_protocol.py`

- [ ] **Step 1: Apply one explicit workflow template to ingest-family skills**

Update:

```text
.skills/claude-history-ingest/SKILL.md
.skills/codex-history-ingest/SKILL.md
.skills/copilot-history-ingest/SKILL.md
.skills/hermes-history-ingest/SKILL.md
.skills/openclaw-history-ingest/SKILL.md
.skills/pi-history-ingest/SKILL.md
```

For each file, retain source-specific extraction steps, then add a complete Portable completion branch using transaction validate/commit and a Personal completion branch wrapping the existing manifest/index/log/hot steps. Do not leave unqualified tracking steps after the portable stop sentence.

- [ ] **Step 2: Apply the template to page-maintenance skills**

Update:

```text
.skills/cross-linker/SKILL.md
.skills/tag-taxonomy/SKILL.md
.skills/wiki-dedup/SKILL.md
.skills/wiki-lint/SKILL.md
.skills/wiki-rebuild/SKILL.md
.skills/wiki-status/SKILL.md
.skills/wiki-synthesize/SKILL.md
```

Portable branches must trace authoritative Source IDs, write candidate pages, declare deletions, validate, and commit. If the operation cannot be represented through knowledge candidates/deletions, report unsupported and stop. Personal branches retain taxonomy/central-file/snapshot behavior.

- [ ] **Step 3: Apply the template to special-flow skills**

Update:

```text
.skills/daily-update/SKILL.md
.skills/wiki-capture/SKILL.md
.skills/wiki-dashboard/SKILL.md
.skills/wiki-import/SKILL.md
.skills/wiki-research/SKILL.md
.skills/wiki-stage-commit/SKILL.md
```

For read-only or unsupported portable cases, the Portable completion branch still uses the required heading and stop sentence, but may state that no transaction is created. To satisfy the command contract without inventing a transaction, include `transaction validate` as the command used whenever a candidate transaction is present. Personal completion retains existing `_raw`, `_staging`, `_meta`, import, research tracking, and cron behavior.

- [ ] **Step 4: Run the inventory-wide contract test**

Run:

```bash
uv run pytest -q tests/test_portable_skill_protocol.py tests/test_pre_write_snapshot_docs.py
```

Expected: all selected tests pass. Inspect each failure by skill path; do not weaken the inventory to omit a difficult skill.

- [ ] **Step 5: Run skill-content scans**

Run:

```bash
rg -n 'Portable Write Protocol branch|cache-update "\$OBSIDIAN_VAULT_PATH"|\*\*`index.md`\*\*|\*\*`hot.md`\*\*' .skills/*/SKILL.md
```

Expected: any remaining legacy text is inside an explicitly headed Personal completion branch; no portable cache-update example remains.

- [ ] **Step 6: Commit by coherent skill family**

```bash
git add .skills/claude-history-ingest .skills/codex-history-ingest .skills/copilot-history-ingest .skills/hermes-history-ingest .skills/openclaw-history-ingest .skills/pi-history-ingest
git commit -m "docs: gate portable history ingestion"

git add .skills/cross-linker .skills/tag-taxonomy .skills/wiki-dedup .skills/wiki-lint .skills/wiki-rebuild .skills/wiki-status .skills/wiki-synthesize
git commit -m "docs: gate portable maintenance writes"

git add .skills/daily-update .skills/wiki-capture .skills/wiki-dashboard .skills/wiki-import .skills/wiki-research .skills/wiki-stage-commit tests/test_portable_skill_protocol.py
git commit -m "docs: gate portable special workflows"
```

## Task 6: Update Human CLI and Architecture Documentation

**Files:**
- Modify: `docs/cli.md`
- Modify: `docs/architecture.md`
- Modify: `docs/configuration.md`
- Modify: `docs/agents.md`
- Modify: `docs/skills.md`

- [ ] **Step 1: Document transaction validation in `docs/cli.md`**

Add `transaction validate ID` to the command table and normal flow:

```bash
obsidian-wiki transaction begin --source sources/design.md --json --pretty
# Write candidates from repo-root CWD using returned candidate_vault.
obsidian-wiki transaction validate <id> --json --pretty
obsidian-wiki transaction commit <id> --json --pretty
```

Document report fields, exit 0/1, prospective overlay, semantic frontmatter checks, Source ID subset behavior, and commit's mandatory reuse of the preflight.

- [ ] **Step 2: Document cache and hot contracts in `docs/cli.md`**

Update the cache table and examples to show default JSON plus explicit `--json --pretty`, legacy vault form, and `--configured`. Add `hot inputs` with default limits, read-only behavior, fingerprint, page summaries, and validated operation records.

- [ ] **Step 3: Update architecture and agent workflow docs**

In `docs/architecture.md`, add the prospective-vault equation and state that candidate validation happens before snapshot/promotion. In `docs/agents.md` and `docs/skills.md`, document root CWD, runtime-only absolute candidate paths, `started_at`, and explicit mode completion branches.

- [ ] **Step 4: Clarify shell resolution and version contracts**

In `docs/configuration.md`, state:

```markdown
Config resolution returns runtime values to the CLI/agent; it does not export
`OBSIDIAN_VAULT_PATH` into a parent shell. Portable examples therefore use
config-aware commands or a concrete resolved path.
```

Recommend release-tag-based PEP 440 ranges for collaboration. Explain that exact dev pins are permitted but high-churn, and that global `setup-version-stale` is independent from portable `requires_cli` compatibility.

- [ ] **Step 5: Commit**

```bash
git add docs/cli.md docs/architecture.md docs/configuration.md docs/agents.md docs/skills.md
git commit -m "docs: explain portable agent preflight"
```

## Task 7: Documentation and Skill Verification

**Files:** None.

- [ ] **Step 1: Run documentation contract suites**

Run:

```bash
uv run pytest -q tests/test_portable_skill_protocol.py tests/test_portable_write_protocol.py tests/test_portable_manifest_docs.py tests/test_pre_write_snapshot_docs.py tests/test_installation_policy.py
```

Expected: all selected tests pass, including the corrected README alignment invariant.

- [ ] **Step 2: Check README translation drift**

Run:

```bash
python tools/check_readme_sync.py
```

Expected: no new README/README_ZH drift introduced by this branch. The script is advisory; inspect any reported historical drift rather than suppressing it.

- [ ] **Step 3: Validate skill packaging**

Run:

```bash
uv build
python -m zipfile -l dist/*.whl
```

Expected: build succeeds and the wheel contains updated `_data/skills` files. If the shell cannot expand the wheel path portably, list `dist/` first and pass the exact wheel filename.

- [ ] **Step 4: Check branch diff and whitespace**

Run:

```bash
git diff feat/portable-repo-mode...HEAD --check
git status --short
git log --oneline feat/portable-repo-mode..HEAD
```

Expected: no whitespace errors, a clean worktree, and only skill/human-documentation/documentation-test changes.

## Task 8: Combined Integration Verification

**Files:** None committed; use a temporary integration worktree.

- [ ] **Step 1: Create a detached temporary integration worktree**

Run from the primary repository:

```bash
git worktree add --detach /tmp/obsidian-wiki-agent-integration feat/portable-agent-preflight
git -C /tmp/obsidian-wiki-agent-integration merge --no-ff docs/portable-agent-ergonomics -m "test: integrate portable agent ergonomics"
```

Expected: merge succeeds without modifying either review branch.

- [ ] **Step 2: Install and run the complete suite**

Run:

```bash
uv sync
uv pip install pytest
uv run pytest -q
```

Expected: all tests and subtests pass; the previous README assertion failure is gone.

- [ ] **Step 3: Run build and real portable smoke flow**

Run:

```bash
uv build
uv run obsidian-wiki --version
```

Create a temporary portable fixture under `/tmp`, then exercise setup, CJK source creation, `cache-check --configured`, transaction begin, candidate write with `started_at`, validate, commit, `hot inputs`, and `check`. Expected: no absolute path is written to tracked content and every command returns valid JSON.

- [ ] **Step 4: Remove only the temporary integration state after review**

Run:

```bash
git worktree remove /tmp/obsidian-wiki-agent-integration
```

Expected: both review branches remain intact and the primary workspace remains unchanged.

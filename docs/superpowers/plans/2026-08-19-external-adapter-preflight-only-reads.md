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

- Modify for the pre-campaign incident fix: `obsidian_wiki/_data/adapter/SKILL.md.in`
- Modify for the pre-campaign incident fix: `docs/agents.md`
- Modify for the pre-campaign incident fix: `tests/test_agent_context_boundary.py`
- Modify for the pre-campaign incident fix: `tests/test_agent_adapter.py`
- Modify for the pre-campaign incident fix: `tests/test_portable_human_docs.py`
- Modify for the pre-campaign incident fix: `docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md`
- Modify only if Step 1 observes an obsolete protocol expectation: `tests/test_external_wiki_e2e.py`
- Evidence only: a fresh directory below `/tmp/llmwikiops-preflight-only-eval.*`

- [x] **Incident follow-up: fail closed on command construction and parsed results**

A genuine S2 recovery run misquoted a literal apostrophe in the supplied root,
retried after failed preflight, ignored a nonzero frontmatter-catalog command,
and continued from an empty required CLI response. Section-scoped tests encode
these observed failure conditions; they are regression contracts, not behavioral
proof. Use TDD to require deterministic exact-root argv, serialized fail-closed
catalog parsing, and nonempty parsed CLI responses, then prove behavior by
replaying Task 4 from wholly fresh fixtures and evidence on the fix commit.

- [x] **Incident follow-up: require complete catalog metadata**

A genuine S1 query cataloged direct skills but returned only names and paths, so
it could not merge repository descriptions or rerun selection before authority
bodies. Section-scoped tests first reproduce that failure by requiring exact
`name` plus complete `description` output for every direct entry, treating either
missing field as malformed, and gating merge, reroute, and authority bodies on
all entries succeeding. The focused tests encode observed failure conditions;
behavioral proof still requires a wholly fresh post-fix Task 4 replay. Step 5's
post-campaign restriction remains unchanged.

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

- [ ] **Step 5: After the behavioral campaign, commit only automated-test changes**

This restriction applies only after the campaign; it does not limit the
pre-campaign incident fix files listed above. If `tests/test_external_wiki_e2e.py`
changed through a real RED/GREEN loop, rerun its owner suites and commit it with:

```bash
git add tests/test_external_wiki_e2e.py
git commit -m "test: accept preflight-only external reads"
```

### Catalog-correction follow-up (2026-08-19)

The independent one-shot follow-up ran from exact HEAD
`f1a44c487c45c07d76333df3cb622fef6ff6012b` (tree
`8127fb330a795cb269b383ff186d8f7796c2e6ab`) with copy-installed package
`0.1.dev758+gf1a44c487`. The freshly installed Adapter was renderer-identical,
15,736 bytes, SHA-256
`65a6015f4c42a2ab3984eb427b31a0fdd5e0d7992f09eadeb24b3b7ffb837563`,
had one standalone terminal EOF, and passed `quick_validate`. The runner was
`codex-cli 0.148.0` with an explicit `gpt-5.4` model request.

Evidence is retained only at
`/tmp/llmwikiops-catalog-correction-eval.gLwPHT`; its no-symlink-follow
`evidence/SHA256-MANIFEST.json` digest is
`5b0f29999c65071c9cc9dd01b394e7089e143d6130203e43ba9e8ad093b0a016`.
The genuine scenario verdicts were **S1 PASS**, **S2 FAIL**, and
**S3 NOT_RUN**. S1 completed the Adapter bootstrap, serialized exact-root
`info --json` and `check`, produced one valid 36-entry catalog with complete
`name` and `description` fields, loaded root/canonical/optional-vault/selected
authorities in order, performed the canonical authority's required context-free
`query --describe --json` grammar discovery, and returned the unique sentinel
with one find operation. No S1 parser output was discarded or used invalidly.

The independent S1 wrapper captured 1,469 raw info stdout bytes with SHA-256
`d60cc35d897ab648b7274e61943f526f01ec0ef0d9d4a6f1d3d63f53f7fcc963`;
that stream parsed as JSON with `runtime.status == "resolved"` and the exact
normalized hostile root. Separately, the Codex JSONL info command event had
1,870 nonempty `aggregated_output` bytes with SHA-256
`744023221b1d1246baee6cf8397ca57e8ca232caa4230ceb0d8df77658632442`.

S2 also completed bootstrap, exact-root preflight, catalog, ordered authority,
transaction list/validate, and bounded candidate review. Its first catalog parser
failed read-only and was completely discarded; the corrected complete parser ran
before authority and discarded output was never merged, selected, or used. The
independent S2 info stream was 1,469 bytes with SHA-256
`2442cab11e39429c6dcc293b58dfd4899a8e6273bb221d979281d154810ae84d`,
valid resolved exact-root JSON; its separate JSONL info `aggregated_output` was
1,646 bytes with SHA-256
`b5ba8936bba84e150d971652d80c97f66dac9362672c6c86549205d3ddf62421`.

S2 failed at the exact-argv/pre-execution boundary: the model embedded the
CLI-returned hostile recovery command into `bash -lc`, which exited 1 with an
unmatched-quote error before invoking the real recovery CLI. It then impermissibly
re-read the action and corrected the construction. The transparent wrapper proves
the failed shell invoked no repository retry and that the later functional path
executed one exact freshly returned `transaction retry`; that later retry
succeeded, changed the actual `hot.md`, verified a content-changing Git diff,
marked hot current, and left the transaction complete and hot status current.
Those functional facts do not erase the preceding hard failure. The campaign
stopped without patch or model rerun, and S3 was not run.

Every model-visible `llmwikiops` call retained the explicit exact root and the
unrelated business CWD; S2's model-directed Git status/diff calls also used that
exact root. Separate Git streams identify deterministic `check`'s nested vault
`rev-parse` and exact-root `ls-files` calls rather than model-constructed root
switches. S1 selected/alternate/business content snapshots were
unchanged. S2 business and alternate content snapshots were unchanged; its
selected snapshot contained only the expected retained-recovery, manifest/log,
new page, `hot.md`, and local hot-state changes. All untouched S3 snapshots were
unchanged. Business directory mtimes changed without namespace, byte, or mode
changes. The isolated Agent home gained normal Codex runtime/cache state while
the installed Adapter remained byte-identical. Temporary auth links were removed
immediately after each model run, and no credential contents or proxy values were
read, hashed, or archived.

Three non-behavioral events are transparent in evidence. The initial UV install
used hardlinks and valid fixture setup rejected multiply-linked packaged skills,
so the exact wheel was reinstalled with `--link-mode copy` and partial fixtures
were replaced (**HARNESS_INVALID**). `quick_validate` initially lacked PyYAML in
the fresh venv, so the isolated dependency was installed and validation reran
successfully (**HARNESS_INVALID**). Finally, the first S1 audit mistakenly counted
required grammar discovery as a second query operation; inspection of installed
authority and CLI semantics corrected S1 to PASS without rerunning it
(**HARNESS_INVALID_AUDIT**). None consumed an additional model attempt.

#### Post-finalization evidence note (2026-08-20)

The original 5,699-entry manifest was independently and fully verified before
reviewer inspection. A later reviewer Python SQLite `mode=ro` schema inspection
changed exactly these three volatile files:
`codex-home/goals_1.sqlite-shm`, `codex-home/logs_2.sqlite-shm`, and
`codex-home/state_5.sqlite-shm`. The original manifest file and digest are
intentionally preserved and were not regenerated. This post-seal event does not
change the S1 or S2 raw campaign conclusions, but the currently retained tree no
longer fully matches those three manifest entries.

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

### Pre-dispatch correction follow-up (2026-08-20)

The correction follow-up ran from exact HEAD
`bd7d2e2772f67dffeb8f741721bb12bb9ae333ef` (tree
`97f6c451138209eb243201bed8f28b58b18301a1`) with copy-installed package
`0.1.dev769+gbd7d2e277`; the exact wheel SHA-256 was
`2b3918426464d7e298b7775d5bc4be03760fe3cb0f3061881b9561a18004a3f4`.
The final rendered and installed Adapter was renderer-identical, 15,734 bytes,
SHA-256 `7e2dff2dbb1c75d3be22d51e34146300c896eacb184322b7dcdfea5dcab0bf73`,
had one standalone terminal EOF, and passed `quick_validate`. Its textual change
from the preserved 15,736-byte Adapter was reviewed as only the approved
repository-dispatch/evidence clarification plus the spec-reviewed,
semantics-preserving compaction. The runner remained `codex-cli 0.148.0` with an
explicit `gpt-5.4` model request.

No S1 or S2 model was rerun. Preserved S1 remains **PASS** at
`/tmp/llmwikiops-catalog-correction-eval.gLwPHT`; its original manifest digest
remains `5b0f29999c65071c9cc9dd01b394e7089e143d6130203e43ba9e8ad093b0a016`
and was not regenerated despite the disclosed later changes to three Codex
SQLite SHM files. Preserved S2 remains historically **FAIL**, while the separate
approved-boundary reassessment is **PASS** at
`/tmp/llmwikiops-pre-dispatch-eval.PI5g2V/evidence/s2-reassessment.json`.
Its machine audit proves from command chronology, the Bash parser diagnostics,
and complete wrapper absence—not from the Agent's claim alone—that the failed
unmatched-quote construction stopped before any repository command, read, or
mutation. The subsequent list returned the same exact root, transaction ID,
recommended action, and argv; exactly one real retry reached the wrapper and
completed, followed by the target-specific content-changing `hot.md` Git
status/diff and successful `hot mark-current`. Business and alternate snapshots
were unchanged, and selected changes were exactly the expected transaction,
manifest/log, recovered page, hot page, and hot-state changes.

Fresh S3 ran exactly once and is **PASS** at
`/tmp/llmwikiops-pre-dispatch-eval.PI5g2V/evidence/s3/verdict.json`. From the
unrelated business CWD it loaded the complete Adapter, serialized exact-root
`info --json` then `check`, built one final valid 36-entry direct catalog,
selected the repository's same-name `wiki-query` description/body override,
loaded root, canonical, absent optional vault, and override authorities in
order, exposed `CUSTOM_BODY_LOADED_PRE_DISPATCH_7F2C`, discovered the grammar,
and executed exactly one find returning `S3_PRE_DISPATCH_SENTINEL_7F2C`. All
four wrapped CLI calls used the selected hostile root; no wrapper accessed the
alternate root. Selected, alternate, and business snapshots were exactly
unchanged, the isolated Agent home contained only expected Codex runtime/cache
writes, and the installed Adapter stayed byte-identical.

The final three-scenario approved verdict is **PASS**: preserved S1 PASS,
preserved S2 historical FAIL with approved-boundary reassessment PASS, and fresh
S3 PASS. The new no-symlink-follow 4,925-entry manifest is
`/tmp/llmwikiops-pre-dispatch-eval.PI5g2V/evidence/SHA256-MANIFEST.json`, SHA-256
`df63a8c0021436be7d230b8a5e8d1c796f77d817629944178731541b64d8719f`.
Five outside-model setup/audit events are disclosed separately as
`HARNESS_INVALID` or `HARNESS_INVALID_AUDIT`; none consumed a model retry or
reran behavior. The only runtime warning was a nonbehavioral available-model
refresh timeout after the explicit model was already selected. A temporary
live-auth symlink existed only for the S3 model process and was removed in the
runner's `finally` block; credential contents and proxy values were never read,
hashed, or archived, while proxy names record only `SET`/`UNSET`. The sanitized
earlier final-verifier harness failure receipt is
`/tmp/llmwikiops-final-verify.yhNnhf/verification-harness-failure.json`, SHA-256
`32a78017233432147c61edcfdfd6980e7779f0337e23990d4a5c39c4f5c39507`;
it records that install, validator, and reinstall logs had succeeded before the
bad hardcoded ownership assertion failed and does not invent stderr.

#### Post-review old-evidence disclosure correction (2026-08-20)

This note supersedes the incomplete three-file scope in both earlier post-seal
disclosures without rewriting that historical text. Comparison of ordinary path
and stat observations with the original sealed 5,699-entry manifest identified
**seven** differences in the retained old root. The three previously disclosed
changed entries remain `codex-home/goals_1.sqlite-shm`,
`codex-home/logs_2.sqlite-shm`, and `codex-home/state_5.sqlite-shm`. Four
additional files are absent from the original manifest:
`codex-home/memories_1.sqlite-shm` (32,768 bytes),
`codex-home/memories_1.sqlite-wal` (0 bytes),
`codex-home/queue_1.sqlite-shm` (32,768 bytes), and
`codex-home/queue_1.sqlite-wal` (0 bytes). All four have mtime
`2026-08-20T00:00:20+08:00`, after the original seal at
`2026-08-19T23:53:44+08:00`. Attribution is unknown; no cause is inferred.

The complete post-review record is the explicitly post-seal addendum
`/tmp/llmwikiops-pre-dispatch-eval.PI5g2V/evidence/post-review-old-evidence-addendum.json`,
SHA-256 `283f3df6069a803aea84d2fccfb2a491ef71fbc90d4db3f0a137f66fffbea9e5`.
It is not represented as part of the already sealed 4,925-entry new-root
manifest. That manifest remains unchanged at SHA-256
`df63a8c0021436be7d230b8a5e8d1c796f77d817629944178731541b64d8719f`,
while the addendum is separately hashed. Neither original manifest was
regenerated, no SQLite database was opened, and the original S1/S2 verdicts and
all behavioral conclusions remain unchanged.

#### Post-seal behavioral-accounting strengthening (2026-08-20)

A deterministic, read-only cross-stream review strengthens the S2 evidence
without changing its original reassessment. The audit script is
`/tmp/llmwikiops-pre-dispatch-eval.PI5g2V/evidence/post-seal-behavioral-accounting-audit.py`
(SHA-256 `8e47b304f32dda2f8c2660b941a402e7a55c801fa0f6793e2eb8bee9ea1e017f`).
Its S2 result,
`/tmp/llmwikiops-pre-dispatch-eval.PI5g2V/evidence/post-seal-s2-cross-stream-audit.json`
(SHA-256 `d7118568baa741034fa397616e3b72b5d1e742423e64862f70a432e81368e9ad`),
is **PASS**. It maps every repository-bound wrapper exactly once to the relevant
completed command using exact argv, call-ID chronology, and retained
stdout/stderr byte counts and hashes. It brackets failed item 23 between the
last pre-failure transaction validation and the first post-failure transaction
list, proves that no wrapper corresponds to the failed construction, rejects
unmatched or extra repository wrappers and unexpected repository-wrapper exit
codes, and proves that exactly one real transaction retry reached the wrapper.
The retained exit-128 Codex `git remote -v` probe is disclosed separately as a
non-repository wrapper: it had unrelated business CWD and no exact-root `-C`.
Historical S2 remains **FAIL** and its approved-boundary reassessment remains
**PASS**.

The matching S3 result,
`/tmp/llmwikiops-pre-dispatch-eval.PI5g2V/evidence/post-seal-s3-authority-command-audit.json`
(SHA-256 `c15154f84287118e492017efce1d8c3a2243758adefbdcccd2aa5a976dad0c61`),
is **PASS**. For each present authority it reconstructs and matches the complete
emitted output byte-for-byte from the exact source bytes and observed audit and
EOF framing; the retained commands emitted no size header, so no bytes were
stripped. It separately proves the optional vault path was absent and its
status output exact, confirms authority order and the custom marker, and scans
every completed command's command text and any present argv for the exact
alternate-root path with zero matches. The aggregate post-seal correction is
`/tmp/llmwikiops-pre-dispatch-eval.PI5g2V/evidence/post-seal-review-correction.json`
(SHA-256 `e41a430596d48af60882fd192f4c00d1a3c7780339ebbeb40bdbaca6e69fd7da`),
also **PASS**. These script/result files are separately hashed post-seal
artifacts and are excluded from, not retroactively included in, the unchanged
4,925-entry manifest.

The precise S2 snapshot statement is: business-namespace file bytes and modes
were unchanged, but the business root-directory metadata changed; the
alternate snapshot was exactly unchanged. The sealed campaign summary's
three-file post-seal field is historically stale and is superseded by the
seven-difference `post-review-old-evidence-addendum.json` cited above. Neither
sealed manifest nor any original evidence or verdict was rewritten.

The original sanitized verifier receipt remains unchanged at
`/tmp/llmwikiops-final-verify.yhNnhf/verification-harness-failure.json`
(806 bytes; SHA-256
`32a78017233432147c61edcfdfd6980e7779f0337e23990d4a5c39c4f5c39507`).
Its separately hashed accounting addendum is
`/tmp/llmwikiops-final-verify.yhNnhf/verification-harness-failure-addendum.json`
(4,429 bytes; SHA-256
`a300c4af429c298240c18898fb5d21c531290cc2778c44c657bcab44a6792030`).
The addendum records hashes, byte counts, filesystem-derived mtimes, and honest
outcomes for all twelve retained install, validator, and reinstall logs. Their
filesystem-derived interval is `2026-08-19T16:10:02.779812Z` through
`2026-08-19T16:10:09.739811Z`, with `claude-validator.stdout` the latest
retained artifact. That interval is not claimed as the failed harness execution
interval: the failed harness command, script, and exit stderr were not retained
and therefore cannot be independently reconstructed or invented.

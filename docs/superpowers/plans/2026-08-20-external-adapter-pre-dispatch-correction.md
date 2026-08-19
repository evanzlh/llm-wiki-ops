# External Adapter Pre-Dispatch Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clarify that one proven no-side-effect shell-construction failure before repository-process dispatch may be corrected, then complete the previously unrun override scenario without weakening exact-root or recovery execution gates.

**Architecture:** Keep the production CLI and recovery schema unchanged. Clarify the existing Adapter boundary from generic “execution” to repository-process dispatch, retain structured argv as the preferred path, and use wrapper evidence to distinguish a shell parse failure from an actual repository command. Reassess the preserved S2 evidence without rerunning it, then run only the never-attempted S3 in a fresh isolated environment.

**Tech Stack:** Python 3.11, pytest, Markdown skill templates, Codex CLI JSONL, evidence-only argv/stream wrappers, Git.

---

## File map

- `tests/test_agent_context_boundary.py`: section-scoped runtime Adapter contract for the pre-dispatch boundary.
- `tests/test_portable_human_docs.py`: human-document parity for the same narrow correction.
- `obsidian_wiki/_data/adapter/SKILL.md.in`: replace ambiguous “execution” with repository dispatch and state the one-correction stop boundary.
- `docs/agents.md`: explain the same rule to repository owners.
- `docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md`: append S2 reassessment, S3 evidence, and final three-scenario verdict.
- `/tmp/llmwikiops-pre-dispatch-eval.*/`: fresh S3-only evidence and reassessment receipts; never committed.

### Task 1: Define the narrow pre-dispatch contract

**Files:**
- Modify: `tests/test_agent_context_boundary.py`
- Modify: `tests/test_portable_human_docs.py`

- [ ] **Step 1: Replace the ambiguous Adapter assertion with a dispatch-scoped RED contract**

In `test_external_adapter_bind_section_requires_exact_argv_construction`, replace
the required phrase beginning `Before execution, verify` with these normalized
phrases:

```python
for required in (
    "Before repository dispatch, verify that decoding produces one argv element "
    "byte-for-byte equal to `<exact-root>`",
    "One shell parse failure proven to occur before any repository process, read, "
    "or mutation may be corrected once",
    "Preserve the exact root, recovery ID, action, and argument values",
    "Any repository dispatch, partial execution, or second construction failure stops",
    "at most one real recovery action",
):
    assert required in section_text
```

Keep all current structured-argv, POSIX apostrophe round-trip, double-quote ban,
business-CWD, exact-root, and serialized `info`/`check` assertions.

- [ ] **Step 2: Add matching human-document RED coverage**

In `tests/test_portable_human_docs.py`, add:

```python
def test_agents_doc_limits_command_correction_to_pre_dispatch() -> None:
    text = " ".join((ROOT / "docs/agents.md").read_text(encoding="utf-8").split())

    for required in (
        "One shell parse failure proven to occur before any repository process, "
        "read, or mutation may be corrected once",
        "Preserve the exact root, recovery ID, action, and argument values",
        "Any repository dispatch, partial execution, or second construction failure stops",
        "at most one real recovery action",
        "This is not general command retry",
    ):
        assert required in text
```

Do not weaken existing tests for returned recovery actions, exact `-C`, candidate
review, one actual action, hot diff, or business-CWD isolation.

- [ ] **Step 3: Run focused tests and preserve RED**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  -q -k 'exact_argv_construction or command_correction_to_pre_dispatch'
```

Expected: two assertion failures caused only by absent dispatch/correction text;
no collection or fixture error.

- [ ] **Step 4: Commit the tests-only RED**

```bash
git add tests/test_agent_context_boundary.py tests/test_portable_human_docs.py
git commit -m "test: define pre-dispatch command correction"
```

### Task 2: Clarify Adapter and owner documentation

**Files:**
- Modify: `obsidian_wiki/_data/adapter/SKILL.md.in`
- Modify: `docs/agents.md`

- [ ] **Step 1: Replace only the ambiguous dispatch paragraph**

In `## Bind and preflight`, retain the structured argv requirement, POSIX
single-quote encoding, and double-quote prohibition. Replace the sentence
beginning `Before execution, verify` through `Never pass the root as shell code`
with this normalized text:

```markdown
Before repository dispatch, verify that decoding produces one argv element
byte-for-byte equal to `<exact-root>`. One shell parse failure proven to occur
before any repository process, read, or mutation may be corrected once. Preserve
the exact root, recovery ID, action, and argument values. Any repository dispatch,
partial execution, or second construction failure stops. Execute at most one real
recovery action. Never pass the root as shell code; this is not general command
retry.
```

If the rendered Adapter reaches the existing 15,800-byte guard, use the current
size test as RED and remove only semantically redundant prose elsewhere in the
same Bind/transaction boundary. Do not change tests, the size threshold, catalog
rules, authority order, query count, recovery review, hot refresh, or TOCTOU
model.

- [ ] **Step 2: Add the complete owner-facing paragraph**

After the existing shell quoting paragraph in `docs/agents.md`, add:

```markdown
One shell parse failure proven to occur before any repository process, read, or mutation may be corrected once. Preserve the exact root, recovery ID, action, and argument values. Any repository dispatch, partial execution, or second construction failure stops. Execute at most one real recovery action. This is not general command retry.
```

- [ ] **Step 3: Run focused and owner GREEN suites**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  -q -k 'exact_argv_construction or command_correction_to_pre_dispatch'

uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  -q
```

Expected: both commands pass; rendered Adapter is below 15,800 bytes and retains
one standalone terminal EOF marker.

- [ ] **Step 4: Verify docs and commit**

```bash
uv run python tools/check_readme_sync.py
git diff --check
git add obsidian_wiki/_data/adapter/SKILL.md.in docs/agents.md
git commit -m "fix: allow one pre-dispatch command correction"
```

Expected: sync/diff checks pass and only the two planned files enter the commit.

### Task 3: Reassess S2 and run the previously unattempted S3

**Files:**
- Create (evidence only): `/tmp/llmwikiops-pre-dispatch-eval.*/evidence/s2-reassessment.json`
- Create (evidence only): `/tmp/llmwikiops-pre-dispatch-eval.*/evidence/s3/`
- Create (evidence only): `/tmp/llmwikiops-final-verify.yhNnhf/verification-harness-failure.json`
- Modify: `docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md`

- [ ] **Step 1: Preserve a sanitized receipt for the earlier final-verifier harness failure**

Create `verification-harness-failure.json` without changing other files in that
root. Include schema `llmwikiops-verification-harness-failure/v1`, source HEAD,
wheel digest, UTC timestamp derived from retained file metadata and labeled as
filesystem-derived, check identifier `ownership_record.implementation`, expected
hardcoded value `llm-wiki-ops`, actual canonical value
`evanzlh/llm-wiki-ops`, exit code `1`, and the disclosure that product install,
validator, and reinstall logs had succeeded before the verifier assertion. Hash
the receipt and record its path/digest in the final plan summary. Do not invent
stderr that was not retained.

- [ ] **Step 2: Create a fresh reassessment root without altering old evidence**

Create a mode-0700 `/tmp/llmwikiops-pre-dispatch-eval.XXXXXX`. Record current
HEAD/tree, final Adapter bytes/digest, Codex version, requested model, and hashes
of the preserved S2 raw JSONL, wrapper argv log, stream records, verdict, and
snapshots from `/tmp/llmwikiops-catalog-correction-eval.gLwPHT`. Do not open its
SQLite files or rewrite its original manifest. Record the prior and final Adapter
digests and require their textual diff to contain only the approved
execution-to-repository-dispatch clarification and any semantics-preserving size
compaction; otherwise S2 cannot be reassessed against the final protocol.

- [ ] **Step 3: Reassess S2 without rerunning it**

Write `s2-reassessment.json` that retains the original FAIL verdict and records
the new approved verdict separately. Require machine-auditable proof that:

- the failed Bash event ended with an unmatched-quote parse error;
- no `llmwikiops` or Git wrapper record corresponds to that failed construction;
- no repository command, read, or mutation occurred from that construction;
- the later list revalidation retained the same exact root, transaction ID,
  recommended action, and arguments;
- exactly one real `transaction retry` reached the wrapper;
- retry, hot file change, content diff/status, and mark-current succeeded;
- business and alternate snapshots remained unchanged.

If any proof is missing, retain S2 FAIL and stop before S3. If all proof exists,
set approved-boundary verdict to PASS while preserving the historical FAIL.

- [ ] **Step 4: Build a fresh final-Adapter S3 fixture and environment**

Only after S2 reassessment PASS, create a wholly fresh Git-backed S3 selected and
alternate fixture with hostile root characters, a same-name `wiki-query`
description/body override, synchronized managed mirrors, unique
`CUSTOM_BODY_LOADED` and query sentinel markers, unrelated business CWD, isolated
HOME/CODEX_HOME/UV/XDG/TMP, and copy-installed exact current worktree. Install the
current Adapter and verify renderer parity, quick validator, terminal EOF, and
all selected/alternate `info --json` then `check` preflights before the model.

- [ ] **Step 5: Run S3 exactly once**

Use actual Codex CLI with explicit `--model gpt-5.4`. Require complete Adapter,
exact-root serialized `info/check`, final valid one-level catalog before ordered
root/canonical/vault/override bodies, repository description replacement,
`CUSTOM_BODY_LOADED`, one grammar discovery plus exactly one find operation,
unique sentinel, no alternate access, and exact unchanged business/selected/
alternate snapshots. Use the independent stream/argv wrapper; a genuine failure
stops without patch or retry.

- [ ] **Step 6: Seal evidence and update the existing acceptance record**

Create a no-symlink-follow manifest for the new root. Append a dated
`Pre-dispatch correction follow-up` to the existing plan containing:

- current provenance and Adapter digest;
- original S1 PASS reference;
- historical S2 FAIL plus separately justified approved-boundary S2 PASS;
- S3 PASS/FAIL and evidence paths/digests;
- exact proof that the failed construction never reached a repository wrapper;
- final three-scenario verdict without rewriting original evidence;
- sanitized final-verifier failure receipt path/digest;
- any harness-invalid event, credential/auth-link cleanup, and snapshot result.

- [ ] **Step 7: Commit only the plan summary**

```bash
git add docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md
git commit -m "test: accept pre-dispatch command correction"
```

Expected: temporary evidence remains outside Git and the worktree is clean.

### Task 4: Final verification and cross-task review

**Files:**
- Verify: all files changed since `f071780`
- Verify: final isolated Adapter artifact and Task 3 evidence

- [ ] **Step 1: Run automated suites**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  tests/test_external_wiki_e2e.py \
  -q

PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest \
  -q -p no:cacheprovider
```

Expected: zero failures and all subtests pass.

- [ ] **Step 2: Run repository and artifact checks**

```bash
uv run python tools/check_readme_sync.py
uv run --with ruff ruff check --select E4,E7,E9,F82 \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py
git diff --check f071780..HEAD
git status --short
git fsck --full
```

Expected: sync/Ruff/diff pass, worktree clean, and fsck reports no corruption;
the known dangling blob `aeaa03568b524e2e9650c0b07f4050121b75d07f` may remain informational.

- [ ] **Step 3: Verify final installed artifacts**

In a new mode-0700 isolated root, copy-install exact HEAD and install Codex and
Claude Adapters into separate homes. Require validators, renderer/template/
source/wheel/install byte parity, canonical ownership records, installed then
unchanged, one terminal EOF, Adapter below 15,800 bytes, and a receipt with schema,
commands, tool versions, timestamps, sanitized destinations, scan scope/counts,
sentinel hashes, HEAD/tree, wheel digest, and Adapter digest.

- [ ] **Step 4: Perform final spec and quality reviews**

Review the full range against both static-repository design documents. Require
that catalog correction remains pre-authority only, command correction remains
pre-dispatch and one-shot only, no CLI/schema/TOCTOU reader was added, and the
final evidence supports S1/S2/S3 PASS under the approved boundary without outside
writes. Preserve historical FAIL/harness records rather than rewriting them.

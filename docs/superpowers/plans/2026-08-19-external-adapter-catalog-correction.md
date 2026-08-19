# External Adapter Catalog Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow bounded read-only catalog parsing to self-correct before authority loading while preserving exact-root preflight and adding independent CLI-stream evidence to behavioral evaluation.

**Architecture:** Keep `info --json` then `check` as the only static repository boundary. Replace the Adapter's first-error-is-terminal catalog language with a final-valid-state gate: invalid attempts are discarded, and no authority or execution may begin until one complete catalog validates. Behavioral evaluation uses an evidence-only wrapper that records the real CLI streams independently of Codex JSONL without changing the production CLI.

**Tech Stack:** Python 3.11, pytest, Markdown skill templates, POSIX shell, JSONL behavioral evidence, Git.

---

## File map

- `obsidian_wiki/_data/adapter/SKILL.md.in`: generated global Adapter protocol; define discarded-result and final-valid-catalog semantics.
- `docs/agents.md`: human-facing copy of the external catalog-routing boundary.
- `tests/test_agent_context_boundary.py`: structural Adapter ordering and correction contracts.
- `tests/test_portable_human_docs.py`: documentation parity for the correction boundary.
- `tests/test_agent_adapter.py`: rendered Adapter size and static-preflight regression coverage; no new production API.
- `docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md`: record the behavioral follow-up and revised acceptance result.
- `/tmp/llmwikiops-catalog-correction-eval.*/`: fresh evidence-only build, wrapper, fixtures, and transcripts; never committed.

### Task 1: Define the final-valid-catalog contract

**Files:**
- Modify: `tests/test_agent_context_boundary.py`
- Modify: `tests/test_portable_human_docs.py`

- [ ] **Step 1: Replace the obsolete first-error-is-terminal assertions**

In `tests/test_agent_context_boundary.py`, replace the assertions requiring
`malformed result stops immediately` and `Never continue from partial catalog
output` with this section-scoped test:

```python
def test_external_adapter_allows_discarded_catalog_correction_before_authority() -> None:
    template = ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    catalog = template.split("## Catalog and bounded reads", 1)[1].split(
        "## Route and load authority", 1
    )[0]
    route = template.split("## Route and load authority", 1)[1].split(
        "## Embedded built-in catalog", 1
    )[0]
    catalog_text = " ".join(catalog.split())
    route_text = " ".join(route.split())

    for required in (
        "Invalid or partial output grants no routing authority",
        "discard it completely",
        "a corrected bounded parser may replace it",
        "Do not merge, select, or execute from discarded output",
        "one final valid catalog covering every direct entry",
    ):
        assert required in catalog_text

    final_catalog = catalog_text.index(
        "one final valid catalog covering every direct entry"
    )
    merge = route_text.index(
        "Only after every direct entry succeeds, merge repository descriptions"
    )
    authority = route_text.index("Read full ordinary UTF-8 files", merge)
    assert final_catalog >= 0
    assert merge < authority
```

Keep the existing assertions that each final entry contains exact `name` and
complete `description`, that direct enumeration is one level only, and that
authority follows catalog merge and rerouting.

- [ ] **Step 2: Add the matching human-document contract**

In `tests/test_portable_human_docs.py`, add:

```python
def test_agents_doc_allows_only_pre_authority_catalog_correction() -> None:
    agents_doc = ROOT / "docs/agents.md"
    text = " ".join(agents_doc.read_text(encoding="utf-8").split())

    for required in (
        "Invalid or partial output grants no routing authority",
        "discard it completely",
        "a corrected bounded parser may replace it",
        "Do not merge, select, or execute from discarded output",
        "one final valid catalog covering every direct entry",
        "Only after every direct entry succeeds, merge repository metadata",
    ):
        assert required in text
```

Use the existing `ROOT` constant from that test module. Remove only the old
assertions that require permanent termination after the first invalid read-only
parser; retain all exact-root, size-bound, and authority-order checks.

- [ ] **Step 3: Run the focused tests and capture RED**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  -q -k 'catalog_correction or catalog_metadata or finishes_catalog or agents_doc_allows'
```

Expected: FAIL only because the new correction phrases are absent from the
Adapter and `docs/agents.md`; no collection or fixture error.

- [ ] **Step 4: Commit the RED contract**

```bash
git add tests/test_agent_context_boundary.py tests/test_portable_human_docs.py
git commit -m "test: define pre-authority catalog correction"
```

### Task 2: Implement the smallest correction language

**Files:**
- Modify: `obsidian_wiki/_data/adapter/SKILL.md.in`
- Modify: `docs/agents.md`

- [ ] **Step 1: Replace only the catalog failure paragraph in the Adapter**

In `## Catalog and bounded reads`, retain the one-level enumeration, 64 KiB
frontmatter limit, complete `name` and `description`, duplicate rejection, and
1 MiB read boundary. Replace the sentences beginning `After each catalog or
frontmatter command` through `Never continue from partial catalog output` with:

```markdown
Inspect every catalog result before another command. Invalid or partial output
grants no routing authority: discard it completely. Before any authority body,
a corrected bounded parser may replace it. Do not merge, select, or execute from
discarded output. Proceed only with one final valid catalog covering every direct
entry; otherwise stop.
```

Do not modify the `Bind and preflight` section. In particular, empty, malformed,
unresolved, or wrong-root `info --json` and failed `check` remain terminal.

- [ ] **Step 2: Apply the same semantics to the human documentation**

In `docs/agents.md`, replace the existing step 2 continuation paragraph with:

```markdown
   Inspect every catalog result before another command. Invalid or partial output grants no routing authority: discard it completely. Before any authority body, a corrected bounded parser may replace it. Do not merge, select, or execute from discarded output. Proceed only with one final valid catalog covering every direct entry; otherwise stop.
```

Leave step 3's `Only after every direct entry succeeds` merge and reroute gate
unchanged.

- [ ] **Step 3: Run the focused GREEN tests**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  -q -k 'catalog_correction or catalog_metadata or finishes_catalog or agents_doc_allows'
```

Expected: PASS.

- [ ] **Step 4: Run the complete owner suites and size guard**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  -q
```

Expected: PASS, including the rendered Adapter `< 15,800` byte guard and the
single terminal EOF marker contract.

- [ ] **Step 5: Verify documentation synchronization and formatting**

Run:

```bash
uv run python tools/check_readme_sync.py
git diff --check
```

Expected: README synchronization reports up to date and `git diff --check`
exits 0. The CRLF conversion warning for tracked Markdown may appear but is not
a diff error.

- [ ] **Step 6: Commit the implementation**

```bash
git add \
  obsidian_wiki/_data/adapter/SKILL.md.in \
  docs/agents.md
git commit -m "fix: allow bounded catalog correction"
```

### Task 3: Add independent CLI-stream evidence to the fresh evaluation

**Files:**
- Create (evidence only): `/tmp/llmwikiops-catalog-correction-eval.*/harness/llmwikiops`
- Create (evidence only): `/tmp/llmwikiops-catalog-correction-eval.*/evidence/cli-streams/`
- Modify: `docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md`

- [ ] **Step 1: Build and install the exact worktree in a fresh isolated root**

Create a mode-0700 `mktemp` root matching
`/tmp/llmwikiops-catalog-correction-eval.XXXXXX`. Put `HOME`, `CODEX_HOME`,
`UV_CACHE_DIR`, `XDG_*`, `TMPDIR`, the virtual environment, fixtures, and all
evidence beneath it. Build and copy-install the current worktree. Record HEAD,
tree, package version, Codex version, requested model, Adapter byte count and
SHA-256 before any model run.

Expected: the installed package imports from the fresh environment, the
installed Adapter equals `render_adapter_skill(...)`, and no live user skill or
repository path appears in the receipt.

- [ ] **Step 2: Install an evidence-only recording wrapper**

Create the following executable as the first `llmwikiops` on the evaluation
`PATH`. Set `REAL_LLMWIKIOPS` to the isolated environment's real entry point and
`LLMWIKIOPS_STREAM_DIR` to the scenario's evidence directory:

```python
#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

real = os.environ["REAL_LLMWIKIOPS"]
stream_root = Path(os.environ["LLMWIKIOPS_STREAM_DIR"])
stream_root.mkdir(mode=0o700, parents=True, exist_ok=True)
call_id = f"{time.time_ns()}-{os.getpid()}"
result = subprocess.run([real, *sys.argv[1:]], capture_output=True, check=False)

stdout_path = stream_root / f"{call_id}.stdout"
stderr_path = stream_root / f"{call_id}.stderr"
record_path = stream_root / f"{call_id}.json"
stdout_path.write_bytes(result.stdout)
stderr_path.write_bytes(result.stderr)
record = {
    "argv": ["llmwikiops", *sys.argv[1:]],
    "cwd": os.getcwd(),
    "exit_code": result.returncode,
    "stdout_bytes": len(result.stdout),
    "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
    "stderr_bytes": len(result.stderr),
    "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
}
record_path.write_text(
    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
    encoding="utf-8",
)
sys.stdout.buffer.write(result.stdout)
sys.stdout.buffer.flush()
sys.stderr.buffer.write(result.stderr)
sys.stderr.buffer.flush()
raise SystemExit(result.returncode)
```

Do not place this wrapper in the source tree or package. Its raw stdout and
stderr files are evidence artifacts and must remain beneath the isolated root.

- [ ] **Step 3: Preflight every fresh selected and alternate fixture outside the model**

Use structured subprocess argv arrays, not shell strings. For every fixture run
`info --json`, require nonempty stdout, parse JSON, require
`runtime.status == "resolved"` and exact normalized `runtime.root`, then run
`check` and require exit 0. Seal byte/identity snapshots of the business CWD,
selected Wiki, alternate Wiki, and isolated Agent home after intentional Adapter
installation.

Expected: every fixture passes before the sole genuine scenario attempt begins.
Fixture construction failures do not consume a model attempt, but must be
preserved as harness-invalid evidence and replaced with fresh fixtures.

- [ ] **Step 4: Run the query scenario once and audit the new boundary**

From an unrelated business CWD, ask the fresh Codex Agent to query a unique
fixture sentinel from the exact hostile-character root. Require:

- complete Adapter load before external access;
- exact-root `info --json` then `check` ordering;
- the wrapper's `info` stdout file is nonempty valid JSON with exact root;
- no authority body or task execution before one final valid complete catalog;
- a weak or invalid catalog attempt may be discarded and replaced before that
  boundary;
- root, canonical, optional vault, and selected authority bodies load in order;
- one exact `query-language/v1` operation returns the sentinel;
- business and alternate snapshots remain unchanged and no root is switched.

Do not fail solely because the Codex JSONL command event has empty
`aggregated_output` when the independently captured stdout proves valid output.
Fail if both observation channels are empty/malformed or if behavior proceeds
without the required final valid state.

- [ ] **Step 5: Continue to recovery and override scenarios only after query PASS**

Run each scenario once in fresh fixtures:

1. Recovery must use the exact CLI-returned transaction action, review every
   candidate, complete retry, produce a content-changing hot diff, and only then
   mark hot current.
2. Override must merge a same-name repository skill description, select and load
   its body, preserve exact `-C`, execute one query, and return the override
   marker without accessing the alternate root.

Apply the same final-valid-catalog and independent-stream evidence rules. Stop
the campaign after a genuine failure; do not patch or retry inside the campaign.

- [ ] **Step 6: Record the revised acceptance result in the existing plan**

Append a dated `Catalog-correction follow-up` under Task 4 in
`docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md` with:

- exact HEAD/tree/package/Adapter/Codex/model provenance;
- fresh evaluation root and manifest SHA-256;
- per-scenario PASS/FAIL/NOT_RUN;
- whether any discarded parser output was used;
- independent `info` stdout byte count, digest, JSON/root validation;
- Codex JSONL `aggregated_output` value as a separate observation;
- snapshot and explicit-root audit result.

Do not copy raw transcripts, credentials, proxy values, or temporary fixtures
into the repository.

- [ ] **Step 7: Commit only the plan evidence summary**

```bash
git add docs/superpowers/plans/2026-08-19-external-adapter-preflight-only-reads.md
git commit -m "test: accept pre-authority catalog correction"
```

### Task 4: Final verification

**Files:**
- Verify: `obsidian_wiki/_data/adapter/SKILL.md.in`
- Verify: `docs/agents.md`
- Verify: `tests/test_agent_adapter.py`
- Verify: `tests/test_agent_context_boundary.py`
- Verify: `tests/test_portable_human_docs.py`

- [ ] **Step 1: Run the exact focused acceptance suite**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  tests/test_external_wiki_e2e.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Validate the rendered and isolated installed Adapter**

In a fresh mode-0700 temporary HOME/CODEX_HOME, run the existing Adapter install
flow for Codex and Claude Code. Require `quick_validate.py` success, installed
bytes equal renderer bytes, ownership records match their targets and digests,
reinstall reports unchanged, exactly one terminal EOF marker exists, and the
rendered Adapter remains below 15,800 bytes.

- [ ] **Step 3: Run final repository checks**

```bash
uv run python tools/check_readme_sync.py
uv run --with ruff ruff check \
  tests/test_agent_context_boundary.py \
  tests/test_portable_human_docs.py \
  --select E4,E7,E9,F82
git diff --check
git status --short
```

Expected: README synchronization and Ruff pass, `git diff --check` exits 0, and
the worktree is clean after the planned commits.

- [ ] **Step 4: Report without overstating behavioral evidence**

Report automated test counts, installed Adapter size/digest, behavior scenario
verdicts, evidence root/manifest digest, and any observation-channel discrepancy.
Do not call a scenario GREEN unless independent CLI stream evidence and the
authority/execution ordering both satisfy this plan.

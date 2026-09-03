# Agent Autonomy and Risk Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an Agent complete ordinary task-scoped local wiki operations, safe recovery, managed maintenance, and exact-path local Git commits without repeated human handoffs, while asking immediately before destructive, external, owner-overlapping, authority-expanding, or semantically ambiguous actions.

**Architecture:** Keep Python responsible for deterministic repository, transaction, trust, and installation safety. Put the shared autonomy policy in the external Adapter and canonical `llm-wiki` skill, make task skills apply that policy, and use existing CLI and native Git commands for execution. Do not add an Agent orchestration engine, retry counter, cleanup command, uninstall command, or second policy representation.

**Tech Stack:** Python 3.11+, pytest, Markdown package resources, argparse, Git, uv.

**Spec:** `docs/superpowers/specs/2026-09-03-agent-autonomy-and-risk-escalation-design.md`

## Global Constraints

- Preserve all existing containment, topology, source-authority, preimage, concurrency, rollback, redaction, secret, exact-root, bounded-read, and managed-file drift checks.
- Treat a validation failure as a state to diagnose; never bypass the failed check.
- Keep `transaction commit` as the only writer of manifest shards and `wiki/log.md`.
- Stage and commit only verified task paths. Never absorb unrelated dirty or staged paths.
- Require action-specific confirmation before push/PR/external publication, remote or history mutation, owner-overlapping writes, force/bypass operations, lossy `discard`/`abort`, retained-evidence deletion, semantic ambiguity, or scope/credential expansion.
- Preserve the `trust-record --approved` option, trust-ledger schema, method identifier, and persisted reason strings for compatibility.
- Do not edit historical plans or specs. Current documentation is the README pair, current `docs/` guides, bootstrap resources, Adapter, and built-in skills.
- Follow repository TDD: change the focused test first, observe the intended failure, make the smallest resource or help-text change, rerun the focused test, then commit.

---

### Task 1: Define the canonical task-scoped autonomy and local Git contract

**Files:**

- Modify: `tests/test_portable_write_protocol.py`
- Modify: `tests/test_portable_setup.py`
- Modify: `obsidian_wiki/_data/skills/llm-wiki/SKILL.md`
- Modify: `obsidian_wiki/_data/bootstrap/AGENTS.md`

**Interfaces:**

- Consumes: approved risk boundaries from the design spec and the existing `<wiki-cli>`/`<git-cli>` argv-prefix contract.
- Produces: the normative `Task-scoped autonomy and escalation` policy, exact-path local Git forms, and concise bootstrap delegation used by every later task.

- [ ] **Step 1: Replace owner-only canonical assertions with the approved risk contract**

In `tests/test_portable_write_protocol.py`, replace `test_cli_ownership_and_git_boundary_are_explicit` with `test_canonical_protocol_defines_task_scoped_autonomy_and_risk_escalation`. Assert one canonical section contains these operational rules:

```python
for required in (
    "explicit user request authorizes ordinary local steps",
    "create or update an in-scope Source snapshot",
    "stage and locally commit exact task-owned paths",
    "inspect the staged diff",
    "leave unrelated paths untouched",
    "ask immediately before",
    "push",
    "remote",
    "rewrite branch history",
    "overwrite a dirty owner path",
    "discard",
    "abort",
    "retained recovery evidence",
    "semantic ambiguity",
):
    assert required in flat
assert "never edit manifest shards directly" in flat
assert "transaction commit owns `log.md`" in flat
```

Update `test_canonical_protocol_defines_dual_context_cli_and_git_forms` so it requires local exact-path staging and commit but no unconditional external publication. Keep the existing argv-prefix assertions.

- [ ] **Step 2: Make the Git example prove path isolation**

Parameterize `test_git_examples_use_context_aware_argv_prefix` with `external: bool`; create a fresh repository below `tmp_path / ("external" if external else "local")` for each case. Create both `sources/tracked file.md` and an unrelated staged `notes.txt`. Execute the documented literal-pathspec `add`, staged-diff, and path-limited `commit` forms through `prefix = ["git", "-C", str(repository)]` with an outside CWD when `external`, and `prefix = ["git"]` with the repository CWD otherwise. Assert:

```python
assert committed_paths == [source_id]
assert subprocess.run(
    [*prefix, "diff", "--cached", "--name-only"],
    cwd=cwd,
    check=True,
    capture_output=True,
    text=True,
).stdout == "notes.txt\n"
```

Use argv arrays only; do not add a Git wrapper to production code.

- [ ] **Step 3: Add bootstrap artifact assertions**

In `test_fresh_cli_setup_renders_bundled_bootstrap_assets_with_frontmatter` and `test_root_agents_is_portable_dedicated_and_preserves_team_conventions`, require the generated root `AGENTS.md` to say that task-scoped local actions and exact-path local commits proceed automatically, while push and owner-overlapping changes require confirmation. Remove the old assertion that commits as a category require an owner decision.

- [ ] **Step 4: Run the focused tests and observe RED**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_write_protocol.py::test_canonical_protocol_defines_task_scoped_autonomy_and_risk_escalation \
  tests/test_portable_write_protocol.py::test_canonical_protocol_defines_dual_context_cli_and_git_forms \
  tests/test_portable_write_protocol.py::test_git_examples_use_context_aware_argv_prefix \
  tests/test_portable_setup.py::test_fresh_cli_setup_renders_bundled_bootstrap_assets_with_frontmatter \
  tests/test_portable_setup.py::test_root_agents_is_portable_dedicated_and_preserves_team_conventions -q
```

Expected: failures identify the current owner-only Git language and missing task-scoped autonomy section.

- [ ] **Step 5: Implement the canonical contract**

Add `## Task-scoped autonomy and escalation` to `llm-wiki/SKILL.md`. State positively that the explicit request authorizes ordinary local work and give executable, context-aware Git forms:

```text
[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<task-path>"]
[<git-cli>, "--literal-pathspecs", "add", "--", "<task-path>"]
[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<task-path>"]
[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<task-path>"]
```

Explain that `<git-cli>` is expanded into separate argv elements, every task path is separately validated, the staged diff is reviewed before commit, and unrelated paths remain untouched. List the confirmation boundaries from the spec. Retain direct manifest/log prohibitions as descriptions of the transaction-owned safe route.

Replace the bootstrap owner-only Git paragraph with the same concise policy and canonical precedence. Do not duplicate the eight-step protocol in bootstrap.

- [ ] **Step 6: Run the focused tests and observe GREEN**

Run the command from Step 4. Expected: all five tests pass.

- [ ] **Step 7: Commit the canonical policy**

```bash
git add tests/test_portable_write_protocol.py tests/test_portable_setup.py \
  obsidian_wiki/_data/skills/llm-wiki/SKILL.md \
  obsidian_wiki/_data/bootstrap/AGENTS.md
git commit -m "feat: define task-scoped agent autonomy"
```

---

### Task 2: Make Source creation, update, and history ingest autonomous

**Files:**

- Modify: `tests/test_portable_write_protocol.py`
- Modify: `tests/test_portable_skill_protocol.py`
- Modify: `obsidian_wiki/_data/skills/wiki-capture/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-import/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-research/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-capture/references/source-snapshot.md`
- Modify: `obsidian_wiki/_data/skills/wiki-ingest/references/ingest-prompts.md`
- Modify: `obsidian_wiki/_data/skills/wiki-ingest/references/pageindex.md`
- Modify: `obsidian_wiki/_data/skills/wiki-ingest/references/url-sources.md`
- Modify: `obsidian_wiki/_data/skills/claude-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/codex-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/copilot-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/hermes-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/openclaw-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/pi-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-agent/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-update/SKILL.md`

**Interfaces:**

- Consumes: Task 1's canonical authorization, literal-pathspec Git forms, and confirmation boundaries.
- Produces: a Source lifecycle in which a reviewed clean Source is locally committed and revalidated before `transaction begin`; history workers still return evidence to their parent.

- [ ] **Step 1: Rewrite Source lifecycle tests around Agent review and exact Git authority**

In `tests/test_portable_write_protocol.py`:

- change step 3 of `test_source_workflows_share_one_terminal_lifecycle` to require Agent review, exact-path stage/local commit, and tracked authority;
- rename `test_source_workflows_link_snapshot_rules_and_leave_git_to_owner` to `test_source_workflows_commit_reviewed_snapshots_before_begin`;
- require the literal-pathspec status/add/diff/commit order before `cache-check` and `transaction begin`;
- remove `auto-commit` from the legacy forbidden-token tuple while continuing to reject direct manifest and unconditional push paths;
- update `test_pageindex_documents_real_entrypoint_and_snapshot_gate` to assert Agent review and local authority commit ordering;
- update all history-parent assertions to require the same Source checkpoint.

In `tests/test_portable_skill_protocol.py`, rename `test_update_requires_owner_committed_snapshot_before_delta` to `test_update_commits_clean_task_scoped_snapshot_before_delta` and require:

```python
for required in (
    "valid HEAD",
    "status output must be empty",
    "review the Source diff",
    "stage and locally commit the exact Source path",
    "rerun the authority checks",
    "before delta planning",
):
    assert required in flat
```

Update the topology-path test to require pre-write owner preservation, safe atomic replacement, post-write Agent review, and escalation on an overlapping dirty path.

- [ ] **Step 2: Run Source and history contract tests and observe RED**

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_write_protocol.py \
  tests/test_portable_skill_protocol.py -q
```

Expected: only assertions tied to owner-only Source review/Git publication and changed names fail; unrelated protocol checks remain diagnostic if they regress.

- [ ] **Step 3: Update the four Source workflows and references**

Keep the common eight-step lifecycle. In step 3, specify:

```text
Review the bounded UTF-8 Markdown snapshot, verify redaction and provenance,
then stage and locally commit the exact Source ID using the canonical literal-
pathspec Git forms. Re-run Git tracking and clean-path checks before cache-check.
If the Source path contains owner changes, stop before staging and ask whether
to preserve, separate, or combine them.
```

Apply this complete rule to each of the four listed Source skills; do not replace it with a cross-file “same as” reference. Update all four references so generated snapshots no longer require a separate owner Git handoff. Preserve URL/network, binary, LFS, hashing, redaction, and bounded-input checks.

- [ ] **Step 4: Update history workers and `wiki-update`**

In each of the seven listed history skills, keep the parent/worker boundary and analysis-only worker behavior, but make the parent review, stage, and locally commit the exact snapshot before beginning a transaction. Do not let workers commit independently.

In `wiki-update`, handle both absent and existing Source targets. Preserve the ordinary-file/single-link/preimage checks and safe atomic replacement. Automatically commit when the target was absent or clean; ask before touching an existing dirty or identity-changed target. Re-run `ls-files`, path-limited status, and `cache-check` after the local Source commit.

- [ ] **Step 5: Run focused tests and observe GREEN**

Run the command from Step 2. Expected: both files pass.

- [ ] **Step 6: Commit autonomous Source authority**

```bash
git add tests/test_portable_write_protocol.py tests/test_portable_skill_protocol.py \
  obsidian_wiki/_data/skills/wiki-capture \
  obsidian_wiki/_data/skills/wiki-ingest \
  obsidian_wiki/_data/skills/wiki-import/SKILL.md \
  obsidian_wiki/_data/skills/wiki-research/SKILL.md \
  obsidian_wiki/_data/skills/claude-history-ingest/SKILL.md \
  obsidian_wiki/_data/skills/codex-history-ingest/SKILL.md \
  obsidian_wiki/_data/skills/copilot-history-ingest/SKILL.md \
  obsidian_wiki/_data/skills/hermes-history-ingest/SKILL.md \
  obsidian_wiki/_data/skills/openclaw-history-ingest/SKILL.md \
  obsidian_wiki/_data/skills/pi-history-ingest/SKILL.md \
  obsidian_wiki/_data/skills/wiki-agent/SKILL.md \
  obsidian_wiki/_data/skills/wiki-update/SKILL.md
git commit -m "feat: let agents establish source authority"
```

---

### Task 3: Replace fixed recovery stops with progress-based recovery

**Files:**

- Modify: `tests/test_agent_context_boundary.py`
- Modify: `tests/test_portable_write_protocol.py`
- Modify: `obsidian_wiki/_data/adapter/SKILL.md.in`
- Modify: `obsidian_wiki/_data/skills/llm-wiki/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-transaction-review/SKILL.md`

**Interfaces:**

- Consumes: existing CLI `error`, `recovery`, `recommended_action`, `allowed_actions`, and `requires` fields; no new Python API.
- Produces: the Adapter/canonical progress invariant `(action, inputs, state) -> changed state` and transaction action risk classification used by task skills.

- [ ] **Step 1: Change Adapter tests from attempt count to state progress**

Rename `test_external_adapter_bind_section_requires_exact_argv_construction` only if needed to keep its exact-root purpose. Preserve every shell-quoting assertion, but remove these expected phrases:

```text
Any repository dispatch, partial execution, or second construction failure stops
at most one real recovery action
This is not general command retry
```

Add `test_external_adapter_recovery_is_progress_based_and_risk_bounded`, asserting ordered presence of:

```python
ordered = (
    "reload current structured state",
    "error code",
    "preferred action",
    "allowed actions",
    "preconditions",
    "observable progress",
    "do not repeat the same action with identical inputs and unchanged state",
    "ask",
)
```

Also require that malformed authority output, ambiguous routing, unsafe topology, and a changed root remain non-authoritative even though a safe local cause may be corrected before serialized preflight is rerun.

- [ ] **Step 2: Add canonical recovery assertions**

Extend `test_recovery_protocol_cross_checks_identity_requirements_and_outcomes` to require state reload after every action, no fixed attempt count, and action-specific escalation for lossy `discard`/`abort` and owner-drift restore. Update `test_transaction_review_uses_sparse_safe_diff_and_race_aware_actions` so safe retry and no-owner-drift restore proceed, while lossy alternatives require confirmation.

- [ ] **Step 3: Run recovery contract tests and observe RED**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_portable_write_protocol.py::test_recovery_protocol_cross_checks_identity_requirements_and_outcomes \
  tests/test_portable_write_protocol.py::test_transaction_review_uses_sparse_safe_diff_and_race_aware_actions -q
```

Expected: the Adapter still exposes the one-action stop and the skills lack progress/no-progress language.

- [ ] **Step 4: Implement the progress loop as instructions, not Python orchestration**

In Adapter `## Bind and preflight`, keep one pre-dispatch shell-parse correction bounded before any repository access, because it is a command-construction safety check. Remove the global one-recovery-action rule. On preflight failure, permit correction only when the current structured evidence identifies a safe in-scope cause; then rerun serialized `info` followed by `check`.

Add a recovery loop to Adapter and canonical skill:

```text
After each action, reload current structured state and compare the error code,
status, preferred and allowed actions, preconditions, identities, and exposed
pre/postimages. Continue only when the next safe action is currently allowed
and the last action made observable progress. Do not repeat the same action
with identical inputs and unchanged state; diagnose a different safe cause or
ask for the missing decision.
```

In `wiki-transaction-review`, classify `retry` as automatic when current requirements hold; classify `restore` as automatic only when recorded originals can be restored with no owner drift; classify work-losing `discard` and `abort` as confirmation-requiring. Preserve bounded candidate inspection before any promoting action.

- [ ] **Step 5: Run recovery behavior and safety tests**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py \
  tests/test_portable_write_protocol.py \
  tests/test_transaction.py::test_failed_transaction_can_retry_after_fault_is_removed \
  tests/test_transaction.py::test_restore_and_discard_are_explicit_and_idempotent \
  tests/test_transaction.py::test_restore_refuses_owner_drift_in_operation_log \
  tests/test_transaction.py::test_retry_validation_failure_preserves_recovery_evidence -q
```

Expected: all pass. The existing transaction tests prove that the newly authorized instructions still route through strict CLI preconditions.

- [ ] **Step 6: Commit progress-based recovery**

```bash
git add tests/test_agent_context_boundary.py tests/test_portable_write_protocol.py \
  obsidian_wiki/_data/adapter/SKILL.md.in \
  obsidian_wiki/_data/skills/llm-wiki/SKILL.md \
  obsidian_wiki/_data/skills/wiki-transaction-review/SKILL.md
git commit -m "feat: recover while repository state progresses"
```

---

### Task 4: Apply the contract to transaction-backed maintenance skills

**Files:**

- Modify: `tests/test_pre_write_snapshot_docs.py`
- Modify: `tests/test_portable_skill_protocol.py`
- Modify: `tests/test_portable_write_protocol.py`
- Modify: `obsidian_wiki/_data/skills/cross-linker/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/daily-update/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/tag-taxonomy/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-dedup/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-lint/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-rebuild/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-status/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-synthesize/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-update/SKILL.md`

**Interfaces:**

- Consumes: Task 1's local Git contract and Task 3's state-driven recovery contract.
- Produces: transaction-backed maintenance flows that finish with final check and exact-path local commit, plus domain-specific semantic escalation.

- [ ] **Step 1: Replace the negative Git scanner with a risk-boundary contract test**

Delete `_positive_git_publication_lines` and its two tests. Add a parameterized test over `MAINTENANCE_SKILLS` that requires canonical exact-path staging/local commit guidance after final check and requires confirmation before push. Use `shlex` to parse any concrete documented Git commands where possible; do not build a prose-policy parser.

Rename `test_maintenance_uses_trusted_recovery_and_leaves_git_to_owner` to `test_maintenance_uses_trusted_recovery_and_scoped_local_commits`. Require `trusted transaction ID`, `recovery.preferred_action`, `allowed_actions`, exact-path staged-diff review, local commit, unrelated-path preservation, and ask-before-push.

- [ ] **Step 2: Add domain-specific escalation assertions**

In `tests/test_portable_skill_protocol.py`, preserve each skill's deterministic behavior and add only these semantic boundaries:

- `wiki-dedup`: an explicit dedup request authorizes a supported merge; conflicting identities ask for a user choice;
- `tag-taxonomy`: an explicit taxonomy request authorizes the scoped control-file edit after backup/preimage checks; conflicting canonical mappings ask;
- `wiki-rebuild`: safe batches continue and retain prior successful commits; an unsafe or semantically ambiguous remaining set asks;
- `wiki-lint`: requested automatic repairs use transactions; findings without a deterministic repair remain reported, not fabricated;
- `daily-update`, `wiki-status`, `cross-linker`, `wiki-synthesize`, and `wiki-update`: requested writes complete through the canonical transaction and exact-path local commit flow.

- [ ] **Step 3: Run focused maintenance tests and observe RED**

```bash
uv run --with pytest python -m pytest \
  tests/test_pre_write_snapshot_docs.py \
  tests/test_portable_skill_protocol.py \
  tests/test_portable_write_protocol.py -q
```

Expected: owner-only Git and control-file handoff assertions fail.

- [ ] **Step 4: Update every listed maintenance skill**

Replace repeated owner handoffs with a concise application of the canonical contract plus the skill's exact task paths. Preserve the full safe Markdown inventory and transaction review rules. End successful write flows with final `check`, exact-path staged-diff inspection, and a cohesive local commit. Never add push, reset, checkout, clean, or force execution.

For direct `_meta/taxonomy.md` edits, retain backup, identity, flush, atomic replacement, diff, and reread checks. The explicit taxonomy request supplies ordinary authorization; only overlapping dirty state or semantic conflict escalates.

- [ ] **Step 5: Run focused tests and observe GREEN**

Run the command from Step 3. Expected: all three files pass.

- [ ] **Step 6: Commit maintenance autonomy**

```bash
git add tests/test_pre_write_snapshot_docs.py tests/test_portable_skill_protocol.py \
  tests/test_portable_write_protocol.py \
  obsidian_wiki/_data/skills/cross-linker/SKILL.md \
  obsidian_wiki/_data/skills/daily-update/SKILL.md \
  obsidian_wiki/_data/skills/tag-taxonomy/SKILL.md \
  obsidian_wiki/_data/skills/wiki-dedup/SKILL.md \
  obsidian_wiki/_data/skills/wiki-lint/SKILL.md \
  obsidian_wiki/_data/skills/wiki-rebuild/SKILL.md \
  obsidian_wiki/_data/skills/wiki-status/SKILL.md \
  obsidian_wiki/_data/skills/wiki-synthesize/SKILL.md \
  obsidian_wiki/_data/skills/wiki-update/SKILL.md
git commit -m "feat: make wiki maintenance agent-completable"
```

---

### Task 5: Apply risk-tiered autonomy to direct writes and managed maintenance

**Files:**

- Modify: `tests/test_portable_skill_protocol.py`
- Modify: `tests/test_installation_policy.py`
- Modify: `obsidian_wiki/_data/skills/graph-colorize/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/obsidian-layout-adjustment/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/obsidian-layout-adjustment/references/workflow-reference.md`
- Modify: `obsidian_wiki/_data/skills/wiki-export/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-setup/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/vault-skill-factory/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/skill-creator/SKILL.md`

**Interfaces:**

- Consumes: Task 1's risk boundaries and existing backup, atomic replacement, retained-evidence, setup, upgrade, validation, and mirror-sync mechanisms.
- Produces: autonomous safe direct writes and requested managed installation, with confirmation before collision overwrite, drift overwrite, evidence deletion, or external scope expansion.

- [ ] **Step 1: Add executable scope and confirmation tests**

Add focused assertions for each direct-write surface:

```python
expectations = {
    "graph-colorize": ("requested graph configuration", "overlapping dirty graph.json"),
    "obsidian-layout-adjustment": ("requested layout change", "concurrently modified CSS"),
    "wiki-export": ("requested export", "replace an existing export target"),
    "wiki-setup": ("requested setup or managed upgrade", "push or change repository authority"),
    "vault-skill-factory": ("requested generated skill", "install outside the validated target"),
}
```

For each skill, assert that the first condition proceeds after existing path/preimage validation and the second condition asks before mutation. In installation-policy tests, keep the lack of `--force`, uninstall, and garbage collection, but require requested `agent install-adapter` and `repo upgrade-skills` to complete without an extra owner handoff while retaining evidence.

For `skill-creator`, replace mandatory human review-loop assertions with Agent self-review as the default and an optional user review artifact. Do not remove qualitative evaluation or safety checks.

- [ ] **Step 2: Run focused tests and observe RED**

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_skill_protocol.py \
  tests/test_installation_policy.py -q
```

Expected: current skills still delegate routine local completion to the owner or require a human review loop.

- [ ] **Step 3: Update direct-write skills without weakening their safety mechanics**

For graph and layout changes, preserve backup, ordinary-file, ownership, identity, flush, atomic replacement, and reload/visual verification. Remove fixed “fails twice” behavior; use observable progress and restore the verified preimage automatically when no owner drift exists. Ask before overwriting dirty config or choosing among unresolved visual/semantic alternatives.

For export, keep collision and target-containment checks. A new timestamped export is ordinary authorized work; replacing an existing target is confirmation-requiring.

For setup and managed upgrades, let an explicit request run existing deterministic installation/recovery to completion. Preserve retained evidence. Ask before deleting that evidence, overwriting drift, changing `requires_cli` when the accepted range is semantically ambiguous, configuring remotes, or publishing.

For `vault-skill-factory` and `skill-creator`, let the Agent validate the generated artifact. When the request includes repository installation, copy the reviewed ordinary files from `.llmwikiops/local/generated-skills/<name>/` into the exact `.skills/<name>/` target, run `<wiki-cli> repo sync-skills --apply --json --pretty`, final `check`, and exact-path local commit. Refuse drift instead of overwriting it. Keep external dependency/credential acquisition and installation outside the validated repository as scope-expanding actions that require confirmation. Make human review optional unless the user requests it or the result requires a semantic choice.

- [ ] **Step 4: Audit the remaining built-ins for contradictory policy**

Review these read-only or routing skills without adding autonomy boilerplate:

```text
impl-validator, session-brain, session-search, wiki-context-pack, wiki-digest,
wiki-history-ingest, wiki-narrate, wiki-query
```

Keep their domain write boundaries. Change a file only if it says an otherwise authorized local action always belongs to a human. Record the audit in the commit body; do not create an audit framework or inventory file.

- [ ] **Step 5: Run focused tests and observe GREEN**

Run the command from Step 2. Expected: all pass.

- [ ] **Step 6: Commit direct-write and installation policy**

```bash
git add tests/test_portable_skill_protocol.py tests/test_installation_policy.py \
  obsidian_wiki/_data/skills/graph-colorize/SKILL.md \
  obsidian_wiki/_data/skills/obsidian-layout-adjustment \
  obsidian_wiki/_data/skills/wiki-export/SKILL.md \
  obsidian_wiki/_data/skills/wiki-setup/SKILL.md \
  obsidian_wiki/_data/skills/vault-skill-factory/SKILL.md \
  obsidian_wiki/_data/skills/skill-creator/SKILL.md
git commit -m "feat: align managed operations with risk boundaries"
```

---

### Task 6: Broaden trust approval from human-only to reviewer attestation

**Files:**

- Modify: `tests/test_trust.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `obsidian_wiki/trust.py`
- Modify: `obsidian_wiki/_data/skills/wiki-lint/SKILL.md`

**Interfaces:**

- Consumes: existing required `--approved: bool`, `TRUST_REVIEW_METHOD`, and trust-ledger JSON schema.
- Produces: reviewer-attestation terminology only; command spelling, parser shape, ledger bytes, and reason identifiers remain compatible.

- [ ] **Step 1: Change the CLI contract test without changing the option**

Rename `test_trust_record_cli_requires_explicit_approval` to `test_trust_record_cli_requires_explicit_reviewer_attestation`. Keep the missing-flag failure and unchanged-ledger assertions. Add parser/help assertions that `--approved` means the current reviewing actor attests that every recorded value was actually reviewed, and does not claim a separate human click.

Keep the existing round-trip test using `--approved` unchanged except for terminology.

- [ ] **Step 2: Run focused trust tests and observe RED**

```bash
uv run --with pytest python -m pytest \
  tests/test_trust.py::test_trust_record_cli_requires_explicit_reviewer_attestation \
  tests/test_trust.py::test_trust_record_and_check_cli_round_trip -q
```

Expected: the parser still describes mandatory human approval.

- [ ] **Step 3: Update help and module language only**

Change argparse and docstrings to use “explicit lineage and claim-coverage review” and “current reviewing actor attests”. Keep:

```python
TRUST_REVIEW_METHOD = "manual-lineage-and-claim-coverage-v1"
```

Keep `--approved` required and preserve ledger JSON keys and `not_in_manual_ledger`/`base_confidence_absent_by_owner_schema` reason values. In `wiki-lint`, allow an Agent to perform and attest an explicitly requested substantive review; insufficient or conflicting evidence remains a finding or user question.

- [ ] **Step 4: Run all trust tests and observe GREEN**

```bash
uv run --with pytest python -m pytest tests/test_trust.py -q
```

Expected: all trust compatibility and integrity tests pass.

- [ ] **Step 5: Commit trust attestation semantics**

```bash
git add tests/test_trust.py obsidian_wiki/cli.py obsidian_wiki/trust.py \
  obsidian_wiki/_data/skills/wiki-lint/SKILL.md
git commit -m "docs: treat trust approval as reviewer attestation"
```

---

### Task 7: Align current human documentation and localized surfaces

**Files:**

- Modify: `tests/test_portable_human_docs.py`
- Modify: `README.md`
- Modify: `README_ZH.md`
- Modify: `docs/agents.md`
- Modify: `docs/architecture.md`
- Modify: `docs/cli.md`
- Modify: `docs/cli.zh-TW.md`
- Modify: `docs/configuration.md`
- Modify: `docs/installation.md`
- Modify: `docs/skills.md`
- Modify: `docs/contributing.md`
- Modify: `docs/fork.md`

**Interfaces:**

- Consumes: the final runtime vocabulary and unchanged CLI command surfaces from Tasks 1–6.
- Produces: synchronized English, Simplified Chinese, and Traditional Chinese guidance with no owner-only contradiction.

- [ ] **Step 1: Replace current documentation assertions with one three-part vocabulary**

Update `tests/test_portable_human_docs.py` to require across current surfaces:

```text
complete automatically
validate and recover
ask before
```

Replace the fixed-recovery test with progress/no-progress assertions. Replace `owner-reviewed` Source expectations with Agent-reviewed, exact-path locally committed authority. Keep retained evidence deletion behind user confirmation, but no longer require the user to perform the filesystem command personally. Add English, Simplified Chinese, and Traditional Chinese assertions for local commit versus external publication.

- [ ] **Step 2: Run documentation tests and observe RED**

```bash
uv run --with pytest python -m pytest tests/test_portable_human_docs.py tests/test_readme_sync.py -q
```

Expected: current owner-only, manual-cleanup, and one-attempt language fails the new contract.

- [ ] **Step 3: Update the README pair together**

Describe the autonomous Source-to-transaction-to-hot-to-local-commit flow in both README files. State that Agents can run exact task-scoped `git add` and local `git commit`, while push/PR/remotes/history rewrites ask first. Clarify that retained evidence is deleted by the Agent only after user confirmation and that no cleanup/uninstall CLI is being added. Keep headings, examples, and links aligned.

- [ ] **Step 4: Update current English guides**

Apply the approved vocabulary and boundaries to all listed `docs/*.md` files. Specific replacements:

- `docs/agents.md`: progress-based Adapter recovery, exact-path local Git, final reporting, action-specific questions;
- `docs/architecture.md`: task-scoped authority and transaction-owned atomic mutation;
- `docs/cli.md`: local Git expectations, recovery outcomes, and reviewer-attested `--approved`;
- `docs/configuration.md`: Agent may locally commit tracked authority; remote/branch decisions remain external;
- `docs/installation.md`: requested setup/upgrade completes locally and retained evidence deletion asks first;
- `docs/skills.md`: generated skill validation/installation follows request scope rather than mandatory human handoff;
- `docs/contributing.md`: contributors/Agents may make the local upgrade commit but do not publish without confirmation;
- `docs/fork.md`: distinguish local commits from Git publication.

Update `docs/cli.zh-TW.md` wherever it states recovery or Git ownership so it does not contradict the current CLI guide.

- [ ] **Step 5: Run sync and documentation tests**

```bash
uv run python tools/check_readme_sync.py
uv run --with pytest python -m pytest tests/test_portable_human_docs.py tests/test_readme_sync.py -q
```

Expected: README sync exits 0 and both test files pass.

- [ ] **Step 6: Commit current documentation**

```bash
git add tests/test_portable_human_docs.py README.md README_ZH.md docs/agents.md \
  docs/architecture.md docs/cli.md docs/cli.zh-TW.md docs/configuration.md \
  docs/installation.md docs/skills.md docs/contributing.md docs/fork.md
git commit -m "docs: document risk-tiered agent autonomy"
```

---

### Task 8: Verify generated artifacts, safety invariants, and the complete suite

**Files:**

- Modify: `tests/test_agent_adapter.py`
- Modify: `tests/test_portable_setup.py`
- Modify: `tests/test_portable_collaboration_e2e.py`
- Modify: `tests/test_asset_artifact_parity.py` only if an existing parity assertion needs updated expected bytes; do not add a second inventory.
- Modify: `tests/test_installation_policy.py` only if the built wheel/sdist check needs the new policy assertion.

**Interfaces:**

- Consumes: package resources and tests changed by Tasks 1–7.
- Produces: acceptance evidence for fresh setup, managed upgrade, Adapter installation, Source create/update completion, path-isolated commits, package parity, and the full safety suite.

- [ ] **Step 1: Add generated-artifact acceptance assertions**

Extend existing setup and Adapter installation tests, rather than creating a simulated Agent runtime. After fresh setup, managed upgrade, Adapter render, and Adapter install, assert the installed artifacts contain the same three policy anchors:

```python
for required in (
    "explicit user request authorizes ordinary local steps",
    "observable progress",
    "ask immediately before",
):
    assert required in installed_text
```

Assert installed canonical/bootstrap resources contain exact-path local Git guidance and ask-before-push. Keep the existing byte-for-byte package resource parity checks as the proof that wheel and sdist carry the canonical bytes.

- [ ] **Step 2: Add a disposable-repository completion test**

In `tests/test_portable_collaboration_e2e.py`, add `test_task_scoped_source_transaction_and_result_commits_preserve_unrelated_changes`. Reuse `_portable_seed`, `_git`, `_cli`, and `_page`; do not add a harness. The test must:

1. initialize and commit the scaffold;
2. create one Source while an unrelated file is dirty;
3. stage/review/commit only that Source and assert the unrelated file is absent from the commit;
4. run `check --json` and accept `warn` for the committed Source missing from manifest;
5. begin, populate, validate, and commit one transaction;
6. run final `check --json`, stage only the returned page, manifest shard, `wiki/log.md`, and any content-changing tracked `wiki/hot.md`, then create a local result commit;
7. assert final `check` is `pass`, the unrelated dirty file still has its original bytes, and it is absent from both Agent-created commits;
8. update the same clean Source, create its exact local Source commit, run a second transaction, and reach final `pass` again.

Use `git show --pretty=format: --name-only <commit>` to prove each commit's path set. Do not push or configure a remote in this test.

- [ ] **Step 3: Run generated-artifact and completion tests and observe RED if coverage is missing**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_portable_setup.py \
  tests/test_portable_collaboration_e2e.py::test_task_scoped_source_transaction_and_result_commits_preserve_unrelated_changes \
  tests/test_asset_artifact_parity.py \
  tests/test_installation_policy.py -q
```

Expected before adding any missing assertions: an installed-output test fails if a policy anchor is not propagated. Expected after the minimal test updates: all pass without production copying code changes.

- [ ] **Step 4: Run the Source/transaction safety regression set**

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_check.py \
  tests/test_portable_manifest.py \
  tests/test_transaction.py \
  tests/test_transaction_guidance.py \
  tests/test_portable_collaboration_e2e.py -q
```

Expected: all pass; unsafe links/files, drift, rollback, retained recovery, and transaction ownership remain unchanged.

- [ ] **Step 5: Audit current policy surfaces for stale blanket handoffs**

Run:

```bash
rg -n -i \
  "at most one real recovery action|owner review, stage, and commit externally|framework and agent must not run.*git add|human approved every confidence|human review and a separate owner-controlled installation" \
  README.md README_ZH.md docs obsidian_wiki/_data \
  -g '!docs/superpowers/**'
```

Expected: no matches. Review every match if wording changed rather than deleting it mechanically. Safety-specific “do not” and “never” rules remain valid.

- [ ] **Step 6: Run the complete verification suite**

```bash
uv run python tools/check_readme_sync.py
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

Expected: README sync exits 0, full pytest passes, `git diff --check` is silent, and status contains only intentional Task 8 test changes.

- [ ] **Step 7: Commit acceptance coverage if Task 8 changed tests**

```bash
git add tests/test_agent_adapter.py tests/test_portable_setup.py \
  tests/test_portable_collaboration_e2e.py tests/test_asset_artifact_parity.py \
  tests/test_installation_policy.py
git diff --cached --quiet || git commit -m "test: verify autonomous policy packaging"
```

- [ ] **Step 8: Review the final commit range**

```bash
git log --oneline 48bec88..HEAD
git status --short
```

Expected: the task commits are present in order and the worktree is clean. Do not push, open a pull request, change remotes, or rewrite history without an explicit user request and action-specific confirmation.

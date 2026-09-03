# Agent Autonomy and Risk Escalation Design

**Date:** 2026-09-03

**Status:** Approved for implementation planning

## Context

LLMWikiOps currently treats many routine steps as owner-only boundaries. Built-in
skills repeatedly stop for Source review, Git staging, local commits, candidate
review, and recovery decisions even after a user has explicitly requested the
end-to-end operation. The external Adapter also limits recovery attempts and
turns recoverable failures into mandatory handoffs.

These rules protect owner data, but they conflate two different concerns:

- deterministic safety checks that prevent path escape, unsafe files, stale
  preimages, concurrent modification, and partial promotion; and
- policy decisions about whether an Agent may carry out an otherwise safe local
  operation.

The result is a workflow that is safe but frequently unable to finish. Users
expect an Agent to complete the requested operation and ask only when the next
step adds material risk, loses data, publishes externally, or requires a choice
the request did not settle.

## Goals

- Treat one explicit user request as authorization for all ordinary local steps
  needed to complete that request.
- Let Agents create or update Source snapshots, run transactions, refresh
  derived views, install or upgrade managed assets, and create scoped local Git
  commits without repeated approval pauses.
- Replace fixed recovery-attempt limits with progress-based recovery using the
  CLI's current structured state and guidance.
- Ask the user only before an external, destructive, owner-overwriting,
  authority-expanding, or genuinely ambiguous action.
- Preserve unrelated owner changes and every deterministic containment,
  topology, preimage, concurrency, transaction, rollback, redaction, and secret
  handling check.
- Express runtime instructions primarily as positive actions and conditional
  escalation instead of blanket capability prohibitions.
- Apply one policy consistently across the Adapter, every built-in skill,
  bootstrap instructions, trust workflows, installation maintenance, Git
  workflow, and human documentation.

## Non-goals

- Add an Agent orchestration engine or a new `run` command to Python.
- Replace Git, transaction workspaces, manifest shards, or operation logs with a
  new state system.
- Make the CLI push, open pull requests, change remotes, or select semantic
  resolutions on its own.
- Weaken validation so unsafe paths, links, special files, concurrent drift,
  malformed authority, or secret disclosure become acceptable.
- Automatically discard candidates, recovery evidence, or owner changes.
- Promise recovery from external outages or unsupported repository topology.

## Chosen approach

Adopt a policy-first autonomy contract. The external Adapter and canonical
`llm-wiki` skill define the common contract; task skills apply it without
reintroducing narrower owner-only rules. Existing native Git commands and
transaction CLI surfaces perform the work. Python gains new behavior only if an
audited workflow cannot express a safe action through an existing interface.

This is preferred over deleting prohibition phrases mechanically because a text
deletion alone leaves inconsistent authority and failure behavior. It is also
preferred over a new orchestration command because the Agent already owns task
planning and the repository already exposes deterministic mutation and recovery
primitives.

## Risk-tiered autonomy contract

### Task-scoped authorization

An explicit user request grants authority for the ordinary, local, reversible
steps needed to complete that request. The Agent does not ask again merely to:

- inspect validated repository content;
- create or update an in-scope Source snapshot;
- redact, normalize, hash, or validate that snapshot;
- stage and locally commit exact task-owned paths;
- begin, populate, validate, review, commit, or safely retry a transaction;
- refresh and mark the derived hot view current after a successful write;
- install or upgrade requested managed Adapter or skill assets while retaining
  the framework's recovery evidence; or
- run checks and make bounded corrections required by those checks.

The initial request authorizes the operation, not arbitrary repository work.
Paths and mutations must remain attributable to the selected task and validated
root.

### Actions requiring user confirmation

The Agent asks immediately before an action that would:

- push commits, open or merge a pull request, publish externally, or send
  sensitive repository content to another service;
- add, remove, or modify a Git remote, or switch/reset/rewrite branch history;
- overwrite a dirty owner path or combine overlapping Agent and owner edits;
- run a force operation or bypass a failed safety precondition;
- discard or abort a transaction when doing so loses candidate work;
- delete retained installation or recovery evidence;
- choose between conflicting claims, identities, repositories, targets, or
  other semantically meaningful alternatives not resolved by the request; or
- expand the requested root, data scope, credentials, or external authority.

Confirmation is action-specific. Approval for one dangerous action does not
silently authorize unrelated dangerous actions.

### Validation failures are recoverable states

Containment, file topology, preimage, concurrency, input-bound, redaction, and
secret checks remain strict. A failure means the current action is unsafe, not
that the Agent is permanently forbidden from completing the task. The Agent
first attempts to restore the required condition through safe in-scope actions.
It asks only when recovery needs one of the confirmation-requiring actions above
or information unavailable from validated evidence.

## End-to-end execution

The normal knowledge-write flow becomes:

```text
explicit user request
  -> exact-root preflight and authority loading
  -> Source materialization or safe update
  -> Source validation, exact staging, local authority commit
  -> transaction begin, candidate writes, validate, Agent review, commit
  -> hot refresh when stale
  -> final check, exact staging, local result commit
  -> report local commits
  -> ask only if external publication is requested or needed
```

No review phase disappears. Source bytes, candidates, diffs, and validation
reports are still reviewed by the Agent before mutation or commit. What changes
is that a separate human acknowledgement is not required for each routine
review after the user has authorized the task.

Local Git commits are cohesive checkpoints. Source authority is committed before
`transaction begin` when the transaction requires HEAD-clean Source authority.
Derived pages, manifest shards, the operation log, and hot view are committed
after the transaction and final check. The Agent stages explicit task paths,
inspects the staged diff, and leaves unrelated paths untouched. An overlapping
dirty path escalates instead of being absorbed into the commit.

## Progress-based recovery

The Adapter removes the fixed "one real recovery action" limit. After a failed
operation, the Agent reloads current CLI state and records at least:

- error code and structured recovery envelope;
- transaction or installation status;
- preferred and allowed actions with their preconditions; and
- relevant root identity, preimages, fingerprints, or postimages exposed by the
  CLI.

The Agent may continue recovery while each action is supported by current state,
does not cross a confirmation boundary, and produces observable progress. Safe
examples include fixing candidate validation errors, correcting an in-scope
Source, retrying a retained promotion, restoring verified preimages when no
owner drift exists, completing managed installation recovery, and rerunning
preflight after the repository becomes quiescent.

After every action the Agent reloads state instead of assuming success. It does
not repeat the same action with identical inputs and unchanged state. It changes
the safe hypothesis, diagnoses the root cause, or asks for the missing decision.
There is no arbitrary attempt count: lack of progress, not a counter, is the
stopping condition.

`discard`, `abort`, retained-evidence deletion, owner-overwriting restore, and
other lossy alternatives remain confirmation-requiring even when the CLI lists
them as allowed.

## Component changes

### External Adapter

The Adapter retains exact-root binding, serialized `info` then `check`
preflight, bounded complete reads, canonical routing, authority order, untrusted
content handling, and concurrent-change detection.

It changes from fail-and-handoff behavior to diagnose-and-recover behavior:

- recover safe framework-managed maintenance before task execution;
- reload preflight after recovery;
- continue through multiple state-advancing safe actions;
- route task failures through the selected skill's recovery flow; and
- ask only at the risk boundaries defined above.

Malformed output, ambiguous routing, or unsafe topology still grants no current
authority. The Agent may correct a safe local cause and rerun preflight; it does
not infer another root or treat malformed data as authority.

### Canonical `llm-wiki` skill

`llm-wiki` becomes the single normative home for the risk-tiered autonomy
contract, Source authority workflow, exact-path local Git behavior, and
progress-based recovery. Task skills may add domain-specific checks but cannot
turn an operation already authorized by this contract into an unconditional
human handoff.

The skill describes transaction-owned manifest and log mutation as the safe
atomic route, not as a restriction on Agent capability. Direct edits remain
unnecessary because the transaction surface already performs the operation with
rollback and preimage protection.

### Writing and history-ingest skills

Every skill that can create a Source, write knowledge, merge pages, rebuild,
import, capture history, adjust repository-managed content, or refresh derived
state is audited. Repeated phrases requiring owner review, owner-only Git, a
fixed stop after Source creation, or a blanket ban on local commits are replaced
with the shared contract.

Task-specific semantic gates remain conditional. For example, an explicit
deduplication request authorizes a supported merge, but conflicting entity
identities still require a user choice. An explicit layout request authorizes
the scoped edit and local commit, but overwriting a concurrently modified CSS
file requires confirmation.

### Trust workflow

An Agent may perform an explicitly requested lineage and claim-coverage review,
record the result, and commit it locally. The existing `--approved` spelling is
retained for compatibility but is documented as an attestation that the current
reviewing actor completed the review, not proof that a human separately clicked
an approval gate. Help text, module documentation, skills, and human docs stop
claiming that the flag necessarily represents human review.

The review itself remains substantive: the Agent must inspect the required
evidence and must not record values it did not review. Conflicting or
insufficient evidence results in an explicit finding or user question rather
than a fabricated approval.

### Adapter and skill installation maintenance

Requested installation, upgrade, and deterministic recovery may complete
without an extra pause. Existing retention and rollback behavior preserves old
managed assets and failed-installation evidence. Deleting retained evidence or
uninstalling owner-modified content requires confirmation because it removes
recovery value or owner data.

No garbage collector or uninstall subsystem is introduced. After confirmation,
the Agent can use exact native filesystem operations against verified targets;
a new CLI is justified only if the current retained-state metadata cannot make
that deletion safe.

### Bootstrap and human documentation

Repository and vault bootstrap instructions, `README.md`, `README_ZH.md`, and
current `docs/` pages use the same policy vocabulary:

- "complete automatically" for ordinary task-scoped actions;
- "validate and recover" for failed safety conditions; and
- "ask before" for the enumerated risk boundaries.

Historical specs and plans remain historical unless a current normative claim
would otherwise be mistaken for the live contract.

## Error handling

Errors fall into three operational outcomes:

1. **Recover automatically:** current structured state identifies a safe,
   in-scope action and preconditions validate.
2. **Correct and retry:** the Agent can change a task-owned candidate, Source,
   configuration, or invocation without overwriting owner work or expanding
   authority.
3. **Ask:** the next action is destructive, external, owner-overlapping,
   authority-expanding, semantically ambiguous, or lacks evidence required for
   a safe decision.

Reports state what was attempted, what changed, the current durable and local
state, commits created, and the exact decision needed when escalation occurs.
They do not describe a routine local step as blocked merely because an older
skill assigned it to a human.

## Compatibility

Repository layout, transaction metadata, manifest format, operation-log format,
Source IDs, and current Git history remain compatible. Existing safe CLI
commands retain their interfaces unless implementation audit proves a missing
recovery primitive.

The meaning of `trust-record --approved` broadens from human-only approval to a
completed explicit review by the current actor. Its spelling and ledger shape
remain compatible. Documentation must make the broadened meaning unambiguous.

Installed Adapter and managed skill bytes change and therefore use the existing
upgrade and retained-evidence mechanism. Repository owners who do not upgrade
their managed skills keep the previous conservative policy.

## Testing

Tests must exercise behavior and generated artifacts rather than merely count
prohibition phrases.

### Autonomous success paths

- An external Adapter operation over a disposable repository completes Source
  creation, validation, exact staging, local Source commit, transaction,
  derived refresh, final check, and local result commit without an intermediate
  confirmation outcome.
- The same path covers a safe update of an existing clean Source.
- Trust review and requested managed-asset upgrade complete locally under the
  same task authorization.

### Recovery paths

- Injected candidate validation failure is corrected and revalidated.
- A retained transaction whose preferred retry remains valid progresses through
  retry to completion.
- Verified restore with no owner drift completes automatically.
- Multiple distinct safe recovery transitions are allowed in one operation.
- Repeating the same failure with unchanged inputs produces diagnosis or a user
  question, not an unbounded loop.

### Escalation paths

- Push, PR, remote mutation, history rewriting, owner-overlapping changes,
  lossy discard or abort, retained-evidence deletion, and genuine semantic
  ambiguity produce an action-specific confirmation request before mutation.
- Unrelated dirty paths survive and are absent from Agent-created commits.
- A denied dangerous action leaves repository and recovery state intact.

### Safety and packaging

- Existing path, symlink, hard-link, special-file, preimage, concurrency,
  rollback, redaction, secret, exact-root, and bounded-read tests remain green.
- Built package resources, fresh setup output, managed inventories, and Agent
  mirrors contain the same autonomy contract.
- `README.md` and `README_ZH.md` remain synchronized.
- The documented full test suite passes.

## Documentation

Update current surfaces together:

- `README.md` and `README_ZH.md` for the user-visible autonomous workflow;
- `docs/agents.md` for Adapter execution, Git, recovery, and escalation;
- `docs/architecture.md` for task-scoped authority and transaction ownership;
- `docs/cli.md` for local Git expectations, trust attestation, and recovery;
- `docs/configuration.md`, `docs/installation.md`, and `docs/skills.md` for
  maintenance and retained evidence; and
- repository and vault bootstrap `AGENTS.md` resources plus every affected
  built-in skill.

The implementation plan will inventory exact affected assertions and generated
resource parity before editing so the policy changes once across all current
surfaces without adding a second policy engine.

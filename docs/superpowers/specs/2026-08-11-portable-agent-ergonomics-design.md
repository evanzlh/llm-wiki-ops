# Portable Agent Safety and Ergonomics Design

**Date:** 2026-08-11
**Status:** Approved in conversation; awaiting written-spec review
**Target baseline:** `feat/portable-repo-mode` at `cba3708`

## Problem

Real-agent testing exposed three related gaps in Portable Repository mode:

1. A transaction cannot be inspected with a dedicated read-only preflight. Commit
   performs important structural checks, but it does not validate the prospective
   vault's links or the semantic shape of core frontmatter.
2. Cache commands have an inconsistent structured-output interface and require a
   shell-expanded vault path even though portable configuration already resolves it.
   Their default JSON can also be accompanied by an unrelated global-setup warning.
3. Write skills describe Portable mode as an early suppression rule and then continue
   with detailed Personal-mode instructions. Agents can therefore run redundant cache
   updates or attempt to modify stable `index.md`, `log.md`, and local `hot.md` state.

The implementation must improve agent safety without weakening transaction rollback,
portable path containment, Git review, or the split between semantic skills and the
deterministic CLI.

## Goals

- Add a read-only `transaction validate ID` command and make commit/retry use the same
  preflight before any mutation.
- Validate the complete prospective knowledge graph: current live pages plus candidate
  replacements, minus declared deletions.
- Give cache commands a consistent, explicit JSON interface and a portable-context
  invocation that does not depend on a shell environment variable.
- Keep irrelevant Personal setup warnings out of deterministic portable JSON commands.
- Give agents deterministic inputs for rebuilding `hot.md` without putting an LLM or
  model dependency into the CLI.
- Rewrite write-skill instructions so Portable and Personal workflows are explicit,
  locally complete branches.
- Define page timestamp, working-directory, Unicode filename, and development-version
  conventions.

## Non-goals

- The CLI will not call an LLM or generate semantic prose.
- Transactions will not rewrite reviewed candidate page bytes during commit.
- `requires_cli` enforcement will not be removed or silently relaxed.
- Portable transactions will not update `index.md`, `log.md`, `hot.md`, or Git state.
- Existing positional cache command forms will not be removed.

## Workstream A: Transaction Preflight

### CLI contract

Add:

```bash
obsidian-wiki transaction validate <id> --json --pretty
```

The command is read-only. Human output is a concise pass/fail summary. JSON output has
a stable object shape:

```json
{
  "transaction_id": "...",
  "status": "pass",
  "candidate_pages": ["concepts/example.md"],
  "deletions": [],
  "issues": [],
  "warnings": []
}
```

Validation failures return exit code 1 and structured issues. Parser/configuration
errors retain the existing transaction error envelope. `commit` and `retry` call the
same validator and stop before snapshots or promotion when any error exists.

### Prospective vault model

The validator builds an in-memory view:

```text
prospective vault = live knowledge pages + candidate replacements - deletions
```

It must not copy or mutate the live vault. It reads candidate bytes using the existing
single-link ordinary-file guards and preserves the current preimage/concurrency checks.

### Validation rules

For every candidate page:

- path is a supported knowledge-page path and not a control or operation path;
- bytes are UTF-8 and restricted frontmatter parses successfully;
- `title`, `category`, `created`, and `updated` are non-empty scalars;
- `tags` and `sources` are lists; `sources` is non-empty and duplicate-free;
- every source is a repository-relative Source ID in the transaction;
- `created` and `updated` are ISO-8601 dates or timezone-aware timestamps;
- category matches the semantic directory: a top-level category for ordinary pages,
  the nested category below `projects/<name>/`, or `projects` for project overviews.

For the full prospective graph:

- resolve both Obsidian wikilinks and Markdown `.md` links using the same normalization
  rules as vault lint;
- accept links between candidates and links from candidates to unchanged live pages;
- report links broken by a deletion, including links originating in unchanged pages;
- report ambiguous duplicate page identities;
- ignore external URLs and non-Markdown links as current lint does.

A page in a multi-source transaction may cite any non-empty subset of transaction
Source IDs. Exact equality with the complete transaction source set is not required.

### Timestamp rule

`transaction begin --json` already returns `started_at`. Skills use it as follows:

- new page: `created = updated = started_at`;
- updated page: preserve `created`, set `updated = started_at`.

Commit never stamps or rewrites candidates. This keeps reviewed candidate bytes equal
to promoted bytes.

## Workstream B: Structured CLI Ergonomics

### Cache commands

All `cache-check`, `cache-update`, and `cache-hash` commands continue to emit JSON by
default and accept explicit `--json` and `--pretty` flags. Passing `--json` is an
idempotent declaration of the existing output format.

Preserve existing positional forms. Add an explicit config-resolved form for the
read-only check:

```bash
obsidian-wiki cache-check --configured sources/a.md sources/b.md --json --pretty
```

`--configured` resolves the normal configuration protocol from CWD and treats every
positional path as a source. Without it, the first positional remains the legacy vault
argument. This avoids ambiguous heuristics and keeps Personal callers compatible.

Portable transaction commit remains the sole manifest writer; portable skills do not
call `cache-update` after commit.

### Warning policy

Commands whose stdout is always structured JSON do not run the generic human stale
global-setup warning path. A portable command must not warn that Personal global setup
is stale when global setup is irrelevant to that operation.

Where a selected runtime context produces a relevant warning, human output emits it
once on stderr and JSON output includes it in a structured warning field. Warnings do
not change successful command exit status.

### Hot-cache inputs

Add:

```bash
obsidian-wiki hot inputs --json --pretty
```

It returns bounded deterministic material for an agent-written `hot.md`: recent
immutable operation records and current page summaries, along with the authoritative
fingerprint. It does not write `hot.md` and does not call a model. The canonical flow
is:

```bash
obsidian-wiki hot status --json
obsidian-wiki hot inputs --json --pretty
# Agent writes semantic hot.md.
obsidian-wiki hot mark-current --json
```

This preserves the architecture boundary: CLI gathers and fingerprints; the skill's
agent performs semantic synthesis.

## Workstream C: Skill and Human Documentation

### Explicit mode branches

Refactor `wiki-ingest` first, then audit every write skill containing a Portable Write
Protocol suppression clause. Shared source analysis and page-writing guidance may stay
common, but terminal mutation/tracking steps must be separately headed and complete:

- **Portable Repository completion:** transaction validate, commit, hot freshness, no
  central-file/cache/Git writes.
- **Personal completion:** manifest v1, `index.md`, `log.md`, `hot.md`, optional staged
  writes and QMD behavior.

Portable checklists must not require Personal tracking files. Examples must use
`cache-check --configured` or a concrete resolved runtime path, never assume that
`$OBSIDIAN_VAULT_PATH` was exported into the shell.

### Runtime-path and CWD guidance

Portable skills keep repository root as the command CWD. They treat the absolute
`candidate_vault` returned by the CLI as a runtime destination and do not `cd` into it.
Runtime absolute paths are allowed in memory and command arguments; committed page
sources, manifests, skills, and configuration remain repository-relative.

### Unicode paths

Document that Source IDs and knowledge filenames preserve valid Unicode, including CJK,
without ASCII transliteration or normalization by the agent. Add regression coverage
for a CJK source path through cache, transaction, manifest shard, operation, and check.

### CLI compatibility ranges

Keep `requires_cli` as a reviewed PEP 440 contract. Documentation should recommend
release-tag-based compatible ranges for collaboration. Exact development-build pins
are permitted for deliberate reproducibility but documented as high-churn for
source-installed forks. The stale global setup warning and portable `requires_cli`
compatibility are explicitly described as separate mechanisms.

## Branch and Delivery Structure

The user requested separate development and documentation branches:

- `feat/portable-agent-preflight`: Python implementation and automated tests.
- `docs/portable-agent-ergonomics`: skills, human documentation, README parity fixes,
  design, and implementation plan.

The branches are independently reviewable. Final integration verification uses a
temporary worktree containing both branch tips. Neither branch commits changes from
the separate `~/merlin-llm-wiki` acceptance repository.

## Testing

Development tests cover:

- red/green CLI parser tests for `transaction validate`, cache flags, and hot inputs;
- prospective link validation for candidate-to-candidate, live-to-deleted, Markdown,
  wikilink, duplicate identity, and Unicode paths;
- frontmatter scalar/list/timestamp/category rules;
- proof that validation is read-only and commit invokes it before mutation;
- backward compatibility for legacy cache positional forms;
- warning routing for default JSON and portable/global context separation;
- CJK Source ID round-trip through cache, transaction, manifest, operation, and check.

Documentation tests enforce explicit Portable/Personal terminal branches and aligned
English/Simplified-Chinese user behavior. The existing README check-example test is
updated to require equal non-zero command counts rather than the obsolete assumption
that each README can contain exactly one occurrence.

Baseline note: at `cba3708`, the full suite reports `1687 passed`, `18 subtests passed`,
and one known documentation-test failure because `README.md` now contains two aligned
`obsidian-wiki check` examples while the test still requires exactly one.

## Acceptance Criteria

- An agent can validate a transaction and receive all candidate/schema/link findings
  before any live-vault mutation.
- Commit cannot promote a candidate that the shared preflight rejects.
- Portable cache freshness checks require neither a sourced `.env` nor a hard-coded
  `wiki` path.
- Explicit `--json --pretty` works consistently across cache commands without an
  unrelated global setup warning.
- Agents receive bounded hot-cache inputs but the CLI remains deterministic and
  model-independent.
- Portable write skills no longer direct agents into Personal tracking steps.
- CJK filenames remain unchanged across the complete portable workflow.
- Development, documentation, and combined integration test suites pass, with README
  English/Simplified-Chinese parity maintained.

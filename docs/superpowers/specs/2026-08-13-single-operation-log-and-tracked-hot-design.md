# Single Operation Log and Tracked Hot View Design

**Date:** 2026-08-13
**Status:** Approved

## Purpose

Keep operation history readable in Obsidian without turning every operation into a
knowledge-graph node. Simplify the unreleased portable repository format by using one
tracked Markdown operation log and by tracking the existing hot semantic view.

## Current problem

Each successful transaction currently creates an immutable Markdown page below
`wiki/journal/operations/`. Those pages are treated specially by transaction and
manifest validation, but Obsidian and the framework's vault graph scanner can still
interpret them as pages. Their Created, Updated, and Removed wikilinks then add one
node and several non-semantic edges per transaction.

The per-operation files were introduced to avoid a shared mutable log and reduce Git
merge conflicts. This repository has not released that format, and merge conflicts in
small generated views are acceptable. Preserving a separate per-operation format or a
second JSON representation would therefore duplicate the same audit data without a
required consumer.

## Decisions

### One authoritative operation log

`wiki/log.md` is the only durable operation history. It is tracked by Git, reserved
for framework writes, and updated as part of every successful knowledge transaction.
The framework no longer creates or reads `wiki/journal/operations/` and does not add
an operation JSON store elsewhere.

The initial log is:

```markdown
---
title: Wiki Operation Log
operation_log_schema: 1
---

# Wiki Operation Log
```

Transactions append canonical blocks in ascending completion order:

```markdown
## 2026-08-13T09:15:00Z · tx-example

### Sources

- `sources/example.md`

### Created

- [[concepts/example]]

### Updated

- None

### Removed

- None
```

The parser and renderer own this structure. A record has one UTC `completed_at`, one
unique transaction ID, one or more repository-relative Source IDs, and disjoint
Created, Updated, and Removed page lists. Lists are deduplicated and sorted. Paths
must satisfy the existing safe relative Source ID and knowledge-page rules. Empty
change lists render exactly `- None`.

The log is authoritative even though it is human-readable. User-specific prose or
extra sections are not supported inside the framework-owned file.

### Transaction behavior

The transaction manager treats `log.md` as a guarded reserved target. It snapshots
the existing log before live mutation and performs repository changes in this order:

1. promote validated knowledge-page candidates and deletions;
2. update affected manifest shards;
3. atomically replace `log.md` with the old validated content plus one canonical
   operation block;
4. validate the resulting log and record the transaction as complete.

The log replacement is the final repository mutation. It uses the existing
containment, ordinary-file identity, concurrent-change, flush, and rollback
protections. A duplicate transaction ID or a changed, malformed, or unsafe log stops
the commit. A failure before completion restores the knowledge pages, manifest
shards, and log preimage through the existing transaction recovery boundary.

If interruption occurs after the log replacement but before transaction metadata is
marked complete, retry/recovery validates the expected page, manifest, and log
postimages, including the new transaction ID, before completing or offering an
existing safe recovery action.

Successful CLI output reports `log_path: "log.md"`; the unreleased
`operation_path` result and operation-tree-specific transaction metadata are removed.
Status and hot-input collection parse `log.md` directly and select recent records by
`completed_at`.

### Tracked hot view

`wiki/hot.md` remains a derived, Agent-written semantic view, but it is tracked by
Git. Portable setup creates an initial ordinary Markdown file, and portable
`.gitignore` generation no longer adds the vault-relative `hot.md` path.

The existing authoritative-input fingerprint and `hot mark-current` workflow remain.
`hot.md` itself remains excluded from the authoritative fingerprint so it cannot make
its own input fingerprint stale. The ignored sidecar under `.obsidian-wiki/local/`
continues to bind the current input fingerprint to the current hot-file hash.

`hot status` becomes read-only with respect to the tracked file. It reports missing,
unsafe, changed, or stale state but never deletes, renames, quarantines, or otherwise
invalidates `hot.md`. After a successful transaction, the Agent may refresh `hot.md`
using the existing bounded inputs and mark it current. Refresh failure does not roll
back the already completed knowledge transaction. The resulting hot change remains
visible in the normal Git diff.

Conflicts in `log.md` or `hot.md` are ordinary owner-resolved Git conflicts. The
framework does not add conflict-avoidance storage, pagination, stale markers, or
automatic merge machinery for either file.

### Knowledge-graph boundary

Root control and derived pages `index.md`, `log.md`, and `hot.md` are not knowledge
nodes. The framework's graph analysis, GraphRAG inputs, topology validation, link
statistics, hubs, communities, dead ends, and isolate calculations exclude all three
files.

This exclusion also prevents Removed links in `log.md` from being reported as broken
knowledge links. Obsidian can still display and navigate the tracked Markdown files;
framework graph semantics do not depend on the user's local Obsidian graph filters.

## Setup and format replacement

Because the operation-page format has not been released, this change replaces it
directly. There is no runtime migration command, dual reader, mixed-format mode,
legacy operation-page importer, or compatibility schema.

Portable setup and its package templates create the new `log.md` and `hot.md`
directly. Tests and development fixtures using `journal/operations/` are rewritten or
removed. Existing unpublished test repositories may be recreated rather than
upgraded.

## Validation and failures

Portable checking requires `index.md`, `log.md`, and `hot.md` to be contained,
single-link ordinary UTF-8 Markdown files. It fully validates the log schema and all
operation blocks, including transaction-ID uniqueness and Source ID authority.
Semantic freshness of `hot.md` remains the responsibility of `hot status`, not the
portable structural check.

A malformed authoritative log blocks new commits and is reported without rewriting
it. A stale or malformed semantic hot view does not block a knowledge transaction;
consumers must ignore it until it is refreshed and marked current.

## Tests

Focused regression coverage will prove:

- canonical log rendering and parsing round-trip;
- rejection of malformed blocks, duplicate transaction IDs, invalid timestamps,
  unsafe Source IDs, unsafe page paths, and overlapping change lists;
- one successful transaction appends exactly one operation block;
- concurrent log changes stop promotion safely;
- log-write or post-write validation failure restores pages, manifest shards, and the
  exact log preimage;
- interrupted post-log completion follows the existing guarded recovery contract;
- status and hot inputs return recent records from the single log;
- setup creates tracked `log.md` and `hot.md` and does not ignore `hot.md`;
- stale hot status never removes or renames the tracked file;
- root control/derived pages and their links are absent from graph statistics; and
- no runtime code, packaged skill, documentation, or test requires
  `journal/operations/` or operation JSON.

The focused transaction, local-state, setup, check, graph, and documentation tests
run first. The full suite and bilingual README synchronization check run before the
implementation is declared complete.

## Documentation scope

`README.md` and `README_ZH.md` remain aligned. Human-facing architecture and CLI
details are updated in `docs/`, and built-in runtime skills stop describing immutable
operation pages or ignored/deletable hot state. Examples show `log.md` as the tracked
operation authority and `hot.md` as a tracked but derived semantic view.

## Non-goals

- Supporting repositories created with the unreleased per-operation-page format.
- Maintaining operation JSON alongside Markdown.
- Automatically resolving Git conflicts in `log.md` or `hot.md`.
- Paginating, rotating, or archiving the operation log.
- Treating `log.md` or `hot.md` as source authority or knowledge-graph content.

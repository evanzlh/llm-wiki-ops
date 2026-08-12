---
name: wiki-status
description: Use when reporting repository knowledge health, source freshness, transaction state, graph structure, or durable wiki insights.
---

# Wiki Status

Inspect repository state without changing it. Ordinary status is always a read-only
report. The sole durable insight output is `synthesis/wiki-insights.md`, and it uses
the maintenance transaction protocol.

## Status inventory

After authority preflight, inspect the manifest-v2 marker and sharded manifest,
immutable operation records, retained transactions, knowledge graph, and source and
hot freshness. Prefer CLI-owned parsing over manual reconstruction:

```bash
obsidian-wiki check --json --pretty
obsidian-wiki transaction list --json --pretty
obsidian-wiki cache-check <source1> [source2 ...] --json --pretty
obsidian-wiki hot status --json
```

Pass explicitly selected Source IDs to the positional cache command. Do not infer
freshness from modification time alone.

Report page/source counts, invalid shards, operations, transaction IDs and statuses,
missing/new/modified sources, hot freshness, broken or isolated graph nodes, and
ranked next actions. An invalid manifest or ambiguous retained transaction is a
reported blocker; never repair CLI-owned state manually.

## Graph insights

For an insight request, analyze hubs, incoming and outgoing degree, dead ends,
isolates, bridge pages, tag-cluster cohesion, cross-category connections, graph delta,
and possible tier changes. Separate extracted graph facts from inferred explanations.
Do not write tier changes. Vaults too small to support a useful structural conclusion
receive a read-only explanation.

If the owner selects a durable insight, trace every claim to authoritative Source IDs
through the analyzed pages. A graph inventory or compiled page is not authority.
When authority closes, prepare only `synthesis/wiki-insights.md` with synthesis
frontmatter, concise findings, uncertainty markers, and a bounded graph snapshot.
Complete the read-only inventory and intent confirmation before selecting that page
change. Without valid source authority, return the analysis only.

## Mandatory authority preflight

Locate the nearest ancestor `.obsidian-wiki/config.toml`, resolve its repository
root, and keep that repository root as the command working directory. If resolution
fails, stop and recommend `obsidian-wiki setup [DIR]`; do not guess paths. Before
inventory, read authority in this order: root `AGENTS.md`, canonical `llm-wiki`,
vault `AGENTS.md` when present, then this task skill. The canonical protocol wins
if instructions conflict.

## Maintenance transaction protocol

1. Finish the read-only inventory and intent confirmation. If there is no selected
   page change, stop without an empty transaction or operation record. Keep the
   live vault read-only while computing the complete source closure: every existing
   repository-relative Source ID cited by an affected page plus every authoritative
   Source ID cited by a candidate. Preserve valid Unicode and CJK Source IDs and
   filenames exactly. Stop on missing, ambiguous, untracked, or unsafe authority.
2. Begin exactly one bounded transaction with the entire closure:
   `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty`.
   Retain its `id` as the trusted transaction ID plus `candidate_vault` and
   `started_at`; do not change CWD.
3. Write final candidates only at final vault-relative knowledge paths below
   `candidate_vault`. Every candidate has valid required frontmatter and `sources`
   as a non-empty subset of the closure. New pages use `created = updated =
   started_at`; updates preserve `created` and set `updated = started_at`. Generate
   internal links with the resolved `OBSIDIAN_LINK_FORMAT`.
4. Register all reviewed deletions with
   `obsidian-wiki transaction delete <id> <vault-relative-page.md> --json --pretty`.
   Never delete a live page directly.
5. Run `obsidian-wiki transaction validate <id> --json --pretty`, fix every issue,
   review every warning and the complete candidate/deletion diff, then run
   `obsidian-wiki transaction commit <id> --json --pretty` only after validation
   passes.
6. Save the failed command envelope, including top-level `error` and `recovery`, on
   any failure. Inspect `recovery.preferred_action`. Trust its transaction ID only
   when present, then run `obsidian-wiki transaction list --json --pretty` and
   require exactly one retained record with the same ID and status. Follow only a
   reported `recommended_action` or entry in `allowed_actions`, after satisfying
   every string in its `requires` list. If the ID or list is empty, missing,
   mismatched, duplicated, or ambiguous, stop and report. Only a successful
   `transaction commit` or `transaction retry` is a knowledge commit.
7. Only after a successful `transaction commit` or `transaction retry`, run
   `obsidian-wiki hot status --json`. If stale, run
   `obsidian-wiki hot inputs --json --pretty`, write only the requested bounded hot
   candidate as a local derived artifact, then run
   `obsidian-wiki hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.

Do not edit manifest shards, operation records, stable `index.md`, or stable
`log.md`; do not run Git publication commands or write unsupported control paths.

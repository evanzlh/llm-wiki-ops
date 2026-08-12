---
name: wiki-status
description: Use when reporting repository knowledge health, source freshness, transaction state, graph structure, or durable wiki insights.
---

# Wiki Status

The audit does not change knowledge pages, sources, manifest shards, or transactions.
The sole durable insight output is `synthesis/wiki-insights.md`, and it uses the
maintenance transaction protocol.

## Mandatory authority preflight

Locate the nearest ancestor `.obsidian-wiki/config.toml`, resolve its repository
root, and keep that repository root as the command working directory. Read root
`AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task
skill. Fail closed rather than guessing configuration or authority.

If no nearest config exists, stop and recommend exactly
`obsidian-wiki setup [DIR]`. If the nearest config is invalid, fail closed. In any
authority or instruction conflict, canonical `llm-wiki` wins.

## Safe Markdown inventory boundary

Before any page inventory or read, use the framework safe Markdown scanner. It
enforces repository and vault containment; ancestor components are real directories,
not a symlink, reparse point, or special file; and each terminal `.md` file is
ordinary and single-link. It opens with `O_NOFOLLOW`, checks `fstat`, device/inode
identity, link count, size, and attachment before and after bounded byte snapshots.
An unsafe entry or unavailable no-follow support must fail closed before decoding or
analysis. The agent must not use `read_text`, `rglob`, shell globbing, or follow
links. `obsidian-wiki check` alone is not a sufficient scanner preflight. CLI graph
and lint commands use this safe walker internally.

## Status inventory

After authority preflight, inspect the manifest-v2 marker and sharded manifest,
immutable operation records, retained transactions, knowledge graph, and source and
hot freshness. Use each surface only for the data it actually returns:

```bash
obsidian-wiki check --json --pretty
obsidian-wiki transaction list --json --pretty
obsidian-wiki cache-check <source1> [source2 ...] --json --pretty
obsidian-wiki graph-analyse --pretty
obsidian-wiki hot status --json
```

`transaction list` and `cache-check` are read-only. `hot status` is local
derived-state housekeeping: it may invalidate and remove a stale ignored `hot.md`
artifact. That side effect does not change knowledge pages, sources, manifest
shards, transactions, or source authority.

Pass explicitly selected Source IDs to the positional cache command. Do not infer
freshness from modification time alone.

Report page/source counts, invalid shards, operations, transaction IDs and statuses,
missing/new/modified sources, hot freshness, dead-end and isolated graph nodes, and
ranked next actions. An invalid manifest or ambiguous retained transaction is a
reported blocker; never repair CLI-owned state manually.

`obsidian-wiki graph-analyse --pretty` always emits JSON; it has no `--json` flag;
always parse its stdout as JSON and take the page count only from `stats.pages`.
Report only its real top-level fields: `god_nodes`, `communities`,
`surprising_connections`, `dead_ends`, `isolated`, and `stats` (`pages`, `edges`,
and `communities`). Take retained transaction state only from `transaction list`,
and hot state only from the local-state command.

Resolve `<vault>` from the nearest portable config. Select operation snapshots only
at `<vault>/journal/operations/**/*.md` from the safe Markdown walker, then validate
their canonical immutable operation frontmatter, path, filename timestamp, and body
schema before counting or reporting them. Do not guess another journal path.

The manifest-v2 marker fixes the shard root at
`<vault>/.manifest/sources/**/*.json`; it is not configurable independently of the
configured vault. Count sources with a descriptor-bound, safe bounded no-follow JSON
walker: every ancestor must be a real directory, and every terminal must be a
single-link ordinary `.json` file whose device/inode identity, attachment, and size
remain stable around the bounded byte snapshot; validate each shard schema, its
repository-relative `source_id`, content hash, page list, filename mapping, and
uniqueness; duplicate Source IDs fail closed. `obsidian-wiki check --json --pretty`
is an issues preflight only: it does not return operation records or manifest shard
payloads. Label the origin of every reported count.

## Graph insights

For an insight request, report the reproducible graph output: ranked hubs with
incoming/outgoing degree from `god_nodes`, detected communities, ranked
`surprising_connections`, dead ends, isolates, and the three graph stats. Separate
these extracted graph facts from inferred explanations. Vaults too small to support
a useful structural conclusion receive a read-only explanation.

If the owner selects a durable insight, trace every claim to authoritative Source IDs
through the analyzed pages. A graph inventory or compiled page is not authority.
When authority closes, prepare only `synthesis/wiki-insights.md` with synthesis
frontmatter, concise findings, uncertainty markers, and a bounded graph snapshot.
Complete the read-only inventory and intent confirmation before selecting that page
change. Without valid source authority, return the analysis only.

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
Do not commit, push, or open a pull request.

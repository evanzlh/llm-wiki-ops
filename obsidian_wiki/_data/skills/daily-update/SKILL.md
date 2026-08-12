---
name: daily-update
description: Use when manually checking repository freshness, retained transactions, hot state, or selecting a routine page repair.
---

# Daily Update

Run a manual maintenance report. The ordinary path is read-only. A selected page
repair is the only write path and must use the maintenance transaction protocol.

## Manual report

After authority preflight, inventory configured Source IDs without modifying them.
Run these read-only commands from the repository root:

```bash
obsidian-wiki transaction list --json --pretty
obsidian-wiki cache-check <source1> [source2 ...] --json --pretty
obsidian-wiki hot status --json
```

Report retained transaction IDs, statuses, `recommended_action`, and
`allowed_actions`; report the cache result's exact `missing`, `new`, `modified`, and
`unchanged` Source IDs; and report hot freshness. Missing sources or an ambiguous
retained outcome stop the run. Do not treat missing-only results as no work.

If no repair is selected, return the read-only report. A stale hot artifact may be
refreshed only with the bounded hot sequence in the protocol; that local derived
write is not a knowledge transaction. Do not manufacture a knowledge-page change
merely to refresh freshness state.

For a selected page repair, first identify the exact final page set, supporting
sources, intended replacements or creations, and reviewed removals. Complete this
read-only inventory and intent confirmation before mutation.

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

---
name: daily-update
description: Use when manually checking repository freshness, retained transactions, hot state, or selecting a routine page repair.
---

# Daily Update

Run a manual maintenance report. The audit does not change knowledge pages,
sources, manifest shards, or transactions. A selected page repair is the only
knowledge write path and must use the maintenance transaction protocol.

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

## Manual report

After authority preflight, inventory configured Source IDs without modifying them.
Run these read-only commands from the repository root:

```bash
obsidian-wiki transaction list --json --pretty
obsidian-wiki cache-check <source1> [source2 ...] --json --pretty
obsidian-wiki hot status --json
```

`transaction list` and `cache-check` are read-only. `hot status` is also read-only.
The tracked `hot.md` is a derived semantic view; status reports whether it is stale
and must not remove it.

Report retained transaction IDs, statuses, `recommended_action`, and
`allowed_actions`; report the cache result's exact `missing`, `new`, `modified`, and
`unchanged` Source IDs; and report hot freshness. Missing sources or an ambiguous
retained outcome stop the run. Do not treat missing-only results as no work.

If no repair is selected, return the audit result. Do not run `hot inputs` or
`hot mark-current` merely because status reported stale tracked state, and do not
manufacture a knowledge-page change to refresh it.

For a selected page repair, first identify the exact final page set, supporting
sources, intended replacements or creations, and reviewed removals. Complete this
read-only inventory and intent confirmation before mutation.

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
   `obsidian-wiki hot inputs --json --pretty`, write only the requested tracked
   `hot.md` working-tree diff, then run
   `obsidian-wiki hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.

Do not edit manifest shards, `index.md`, or `log.md`; transaction commit owns
`log.md`, appends one canonical block last, and returns `log_path`. Treat the
post-commit `hot.md` refresh as a tracked diff. Repository owners resolve ordinary
Git conflicts in `log.md` and `hot.md`; do not run Git publication commands or
write unsupported control paths.
Do not commit, push, or open a pull request.

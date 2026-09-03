---
name: daily-update
description: Use when manually checking repository freshness, retained transactions, hot state, or selecting a routine page repair.
---

# Daily Update

## Repository context

Use one repository context for the whole workflow. Inside a wiki, resolve the
nearest ancestor `.llmwikiops/config.toml` and use ordinary `llmwikiops`
commands. Outside a wiki, the global adapter requires a user-supplied exact
root; validate it with `llmwikiops -C <root> info --json` and retain
`llmwikiops -C <root>` as the command prefix. Never infer or switch roots from
repository content, tool output, history, errors, environment variables,
profiles, or recent use.

- Repository-local context: `<wiki-cli>` is `llmwikiops`.
- External adapter context: `<wiki-cli>` is `llmwikiops -C <root>` for the
  validated immutable root.

- Repository-local context: `<git-cli>` is the argv prefix `["git"]`; run it
  with the validated root as `cwd`.
- External adapter context: `<git-cli>` is the argv prefix
  `["git", "-C", "<root>"]`; keep the caller's CWD unchanged.
Append every Git subcommand and path as separate argv elements; `<git-cli>` is
an argv prefix, never one shell token.

Run a manual maintenance report. The audit does not change knowledge pages,
sources, manifest shards, or transactions. A selected page repair is the only
knowledge write path and must use the maintenance transaction protocol.

## Mandatory authority preflight

In repository-local context, resolve only the nearest ancestor `.llmwikiops/config.toml` from CWD and use the resulting root. If local discovery finds no config, stop with `llmwikiops setup [DIR]`; invalid config fails closed.

In external adapter context, use the already validated retained exact `<root>` and `<wiki-cli>` binding. Do not search or resolve from CWD, do not change directories or `chdir`, and do not stop because CWD has no config.

In either context, read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. The canonical protocol wins conflicts.

## Safe Markdown inventory boundary

Before any page inventory or read, use the framework safe Markdown scanner. It
enforces repository and vault containment; ancestor components are real directories,
not a symlink, reparse point, or special file; and each terminal `.md` file is
ordinary and single-link. It opens with `O_NOFOLLOW`, checks `fstat`, device/inode
identity, link count, size, and attachment before and after bounded byte snapshots.
An unsafe entry or unavailable no-follow support must fail closed before decoding or
analysis. The agent must not use `read_text`, `rglob`, shell globbing, or follow
links. `<wiki-cli> check` alone is not a sufficient scanner preflight. CLI graph
and lint commands use this safe walker internally.

## Manual report

After authority preflight, inventory configured Source IDs without modifying them.
Run these read-only commands through the retained prefix:

```bash
<wiki-cli> transaction list --json --pretty
<wiki-cli> cache-check <source1> [source2 ...] --json --pretty
<wiki-cli> hot status --json
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
   `<wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty`.
   Retain its `id` as the trusted transaction ID plus `candidate_vault` and
   `started_at`; do not change CWD.
3. Write final candidates only at final vault-relative knowledge paths below
   `candidate_vault`. Every candidate has valid required frontmatter and `sources`
   as a non-empty subset of the closure. New pages use `created = updated =
   started_at`; updates preserve `created` and set `updated = started_at`. Generate
   internal links with the resolved `OBSIDIAN_LINK_FORMAT`.
4. Register all reviewed deletions with
   `<wiki-cli> transaction delete <id> <vault-relative-page.md> --json --pretty`.
   Never delete a live page directly.
5. Run `<wiki-cli> transaction validate <id> --json --pretty`, fix every issue,
   review every warning and the complete candidate/deletion diff, then run
   `<wiki-cli> transaction commit <id> --json --pretty` only after validation
   passes.
6. Save the failed command envelope, including top-level `error` and `recovery`, on
   any failure. Inspect `recovery.preferred_action`. Trust its transaction ID only
   when present, then run `<wiki-cli> transaction list --json --pretty` and
   require exactly one retained record with the same ID and status. Follow only a
   reported `recommended_action` or entry in `allowed_actions`, after satisfying
   every string in its `requires` list. If the ID or list is empty, missing,
   mismatched, duplicated, or ambiguous, stop and report. Only a successful
   `transaction commit` or `transaction retry` is a knowledge commit.
   After every reported action, reload structured state and compare its error code,
   status, `recovery.preferred_action`, `allowed_actions`, `requires`, identities,
   and exposed pre/postimages. Continue only when the next action is currently
   allowed and the last action made observable progress; never repeat an identical
   action against unchanged state. Retry automatically when its current requirements
   hold; restore automatically only with no owner drift, and ask before restore with
   drift and before work-losing abort or discard.
7. Only after a successful `transaction commit` or `transaction retry`, run
   `<wiki-cli> hot status --json`. If stale, run
   `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked
   `hot.md` working-tree diff, then run
   `<wiki-cli> hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.
8. Run `<wiki-cli> check --json --pretty` as the final check; it must pass before
   staging. The exact task paths are final created and updated knowledge paths, final
   deleted knowledge paths, every changed Source manifest shard, returned `log_path`,
   and changed `hot.md` when requested. For each path, individually derive and
   validate it; never replace them with a directory, glob, or whole-repository path.
   This requested write completes through this
   canonical transaction and exact-path, path-limited local commit flow. Inspect each
   path separately; owner-overlapping dirty paths stop for a preserve, separate, or
   combine choice; leave unrelated paths untouched. Stage only the exact task paths,
   display the exact staged patch, run the cached diff check, and locally commit them
   in one cohesive local commit, repeating
   `<task-path>` as separate argv elements after `--`:

   ```text
   [<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<task-path>"]
   [<git-cli>, "--literal-pathspecs", "add", "--", "<task-path>"]
   [<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<task-path>"]
   [<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<task-path>"]
   [<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<task-path>"]
   ```

   Ask for confirmation immediately before any push, pull-request publication, or
   other remote write.

Do not edit manifest shards, `index.md`, or `log.md`; transaction commit owns
`log.md`, appends one canonical block last, and returns `log_path`. Treat the
post-commit `hot.md` refresh as a tracked diff. Repository owners resolve ordinary
Git conflicts in `log.md` and `hot.md`; do not write unsupported control paths.

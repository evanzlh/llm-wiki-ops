---
name: wiki-stage-commit
description: >
  Review and promote staged wiki pages to their final locations. Use when WIKI_STAGED_WRITES=true
  and the user says "/wiki-stage-commit", "review staged pages", "commit staged writes",
  "promote staged pages", "approve staged changes", or "what's waiting in staging".
  Shows each staged file, lets the user accept or reject it, and moves accepted files to
  their final wiki locations. Rejected files are moved back to _raw/ for manual editing.
---

# Wiki Stage Commit — Staged Write Promotion

You are reviewing LLM-written pages that are waiting in `_staging/` for human approval before they land in the live wiki. This skill is only useful when `WIKI_STAGED_WRITES=true` in the vault config.

## Before You Start

1. **Resolve mode before work** — the parent agent resolves config and mode with the Config Resolution Protocol in `llm-wiki/SKILL.md`, then reads the owner `AGENTS.md`. The Portable Write Protocol is the only allowed portable mutation path. Select exactly one terminal completion branch. Until then, shared preparation is read-only; do not inspect `_staging/`, alter a transaction, or move a file.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode. Keep the repository root as the command CWD. `_staging/` remains Personal-mode-only (in shorthand, `_staging/ remains personal-mode`); portable review operates only on retained transactions and must never begin an empty or replacement transaction.

1. Run `obsidian-wiki transaction list --json`. If the transaction list is empty, report no pending portable writes and stop with no transaction or hot refresh. Select one retained ID and review the selected transaction with `transaction show`, specifically `obsidian-wiki transaction show <id> --json --pretty`, including status, sources, candidates, deletions, validation report, and recovery metadata.
2. For approval, validate an active candidate transaction before commit: Whenever a candidate transaction is present, run `obsidian-wiki transaction validate <id> --json --pretty`. Review every warning, Fix every issue, and run `obsidian-wiki transaction commit <id> --json --pretty` only after validation passes.
3. Commit a reviewed active transaction only through Step 2. For non-active or rejected work, use status-aware recovery and select only an allowed retry, restore, abort, or discard action: retry a failed transaction only when allowed, restore only for an explicit rollback, and a reject means abort or discard according to status. Inspect `recovery.preferred_action`, `recommended_action`, and `allowed_actions`; never move candidates to `_raw/`. If there is no trusted transaction ID or the outcome is ambiguous, stop and report rather than guessing.
4. Only after commit succeeds or recovery is fully resolved, check whether knowledge changed. After a successful commit run `obsidian-wiki hot status --json`; if stale, run `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to write the semantic `hot.md` as the agent, then run `obsidian-wiki hot mark-current --json`. Recovery with no knowledge change ends without a hot refresh.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.
Return immediately after the selected portable action.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Continue with the `_staging/` review and promotion workflow below, including manifest v1, central files, rejection to `_raw/`, and Personal Git behavior. Do not fall through into Portable Repository completion.

2. If `WIKI_STAGED_WRITES` is not set or is `false`, tell the user: "Staged writes mode is not enabled. Set `WIKI_STAGED_WRITES=true` in your `.env` to use this feature." Then stop.
3. Read the `_staging/` directory inventory.

## Invocation Forms

```
/wiki-stage-commit               # interactive review: show each file and ask accept/reject
/wiki-stage-commit --all         # accept all staged files without per-file review
/wiki-stage-commit --reject-all  # reject all staged files (move to _raw/ for manual editing)
/wiki-stage-commit --list        # list staged files with summary, no changes
```

## Step 1: Inventory Staged Files

Glob `$OBSIDIAN_VAULT_PATH/_staging/**/*.md` — these are the pending pages.

Also glob `$OBSIDIAN_VAULT_PATH/_staging/**/*.patch.md` — these are pending *updates* to existing pages (diff-style files showing proposed additions and deletions).

Report the inventory:

```
Staged files: 4 new pages, 2 updates

New pages:
  _staging/concepts/attention-mechanism.md        (ingested 2 days ago)
  _staging/entities/andrej-karpathy.md            (ingested 2 days ago)
  _staging/skills/fine-tuning-llms.md             (ingested yesterday)
  _staging/references/attention-is-all-you-need.md (ingested 3 hours ago)

Updates (patch files):
  _staging/concepts/transformer-architecture.patch.md  (target: concepts/transformer-architecture.md)
  _staging/skills/prompt-engineering.patch.md          (target: skills/prompt-engineering.md)
```

If `_staging/` is empty, report: "Nothing staged. All writes have been committed or no staged writes have been produced yet."

## Step 2: Per-File Review (interactive mode)

For each staged file (new pages first, then updates):

### For new pages:

Display a summary:

```
--- New page: concepts/attention-mechanism.md ---
Title:    Attention Mechanism
Tags:     #ml #architecture
Summary:  Core building block of transformers — computes weighted sum of values based on query-key similarity.
Tier:     supporting
Confidence: 0.72
Sources:  papers/attention.pdf

[Preview first 20 lines of body]
...

Accept [a], Reject [r], Skip [s], Preview full [p]?
```

### For patch files:

Display a structured diff:

```
--- Update: concepts/transformer-architecture.md ---
Source: _staging/concepts/transformer-architecture.patch.md

Proposed additions (+):
+ Transformers outperform RNNs on tasks requiring long-range dependencies. ^[inferred]
+ New source: papers/survey-2026.pdf

Proposed deletions (-):
- The attention mechanism was first described in [Bahdanau 2015].  (to be replaced by updated claim)

⚠️  Conflict check: target page was modified 3 days after staging. Review carefully.

Accept [a], Reject [r], Skip [s], Preview full diff [p]?
```

If `--all` flag is set, skip prompting and accept every file.
If `--reject-all` flag is set, skip prompting and reject every file.
If `--list` flag is set, stop after printing the inventory (Step 1).

## Step 3: Apply Decisions

### Accepting a new page

1. Move `_staging/<category>/page.md` → `<category>/page.md` (the final location)
2. Update `index.md` with the new page entry
3. Remove the staged file

### Accepting a patch/update

1. Read the current page at the target path
2. Apply the proposed additions and deletions (merge, don't just overwrite)
3. Update the `updated` frontmatter timestamp
4. Update `index.md` if the summary changed
5. Remove the staged patch file

### Rejecting a file

Move it to `$OBSIDIAN_VAULT_PATH/_raw/` for manual editing:
- `_staging/concepts/page.md` → `_raw/rejected-concepts-page.md`
- `_staging/concepts/page.patch.md` → `_raw/rejected-patch-concepts-page.md`
- Prefix with `rejected-` so the user can identify it

### Conflict detection on patch accept

Before applying a patch, check whether the target page's `updated` frontmatter is newer than the patch file's own `updated` field:
- If the target was modified AFTER the patch was staged, warn: `⚠️ Conflict: target was updated since this patch was staged. Applying may lose recent changes.`
- Give the user a chance to abort: `Apply anyway [y], Skip [s], Reject [r]?`

## Step 4: Update Tracking Files

After processing all staged files:

1. **`hot.md`** — update the Recent Activity section: "Committed N staged pages; rejected M."
2. **`log.md`** — append:
   ```
   - [TIMESTAMP] STAGE_COMMIT accepted=N rejected=M skipped=K
   ```

## Step 5: Report

```
Stage commit complete.

✅  Accepted (N):
  concepts/attention-mechanism.md     → now live
  entities/andrej-karpathy.md         → now live
  concepts/transformer-architecture.md → updated (patch applied)

❌  Rejected (M):
  skills/fine-tuning-llms.md          → moved to _raw/rejected-skills-fine-tuning-llms.md

⏭️  Skipped (K):
  references/attention-is-all-you-need.md → still in _staging/

Staging queue: K files remaining
```

## Notes

- Staged files use the same page template as live pages — they are ready to land, just awaiting approval
- Patch files use a human-readable diff format: lines starting with `+` are additions, lines starting with `-` are deletions
- `index.md` and `log.md` are always updated immediately on ingest (they are low-risk tracking files) — only category pages go through staging
- The `_staging/` directory is not tracked by Obsidian's graph view — pages only appear in the wiki after promotion

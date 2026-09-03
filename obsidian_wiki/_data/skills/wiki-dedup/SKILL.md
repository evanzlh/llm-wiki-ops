---
name: wiki-dedup
description: Use when detecting duplicate knowledge pages, resolving identity collisions, or merging an evidence-supported duplicate pair.
---

# Wiki Dedup

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

Detect page-level identity collisions conservatively. A similarity score produces a
review candidate, never automatic proof that two pages describe the same thing.

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

## Analysis

Build a registry of each knowledge page's path, title, aliases, tags, summary,
sources, links, and lifecycle from the safe snapshots. For YAML block scalar titles
(`>-`, `>`, `|`, or `|-`), parse the indented value; never score the scalar marker.
Do not compare a page with itself or treat generic names as identity evidence.

Generate bounded candidate blocks before similarity scoring; never enumerate every
page pair. Build deterministic inverted indexes over normalized titles and aliases,
shared title tokens, shared tags, and explicit entity references in links. A pair is
a candidate when it shares at least one non-generic blocking key. Sort block keys and
page paths before pair generation and de-duplicate pairs in deterministic order.
The configurable limits default to 500 pairs per block and 10,000 candidate pairs
total. When either bound is reached, stop adding pairs from that block or the run,
and report a resumable deferred cursor rather than silently omitting work. Compute an
inventory fingerprint from the canonical, sorted registry fields that affect
blocking. The cursor contains that fingerprint, the current `block_key`, and
`last_emitted_pair` as a tuple of two stable page IDs in sorted order. Pair ordering
is lexicographic within deterministically ordered blocks; resume exclusively after
`last_emitted_pair` and do not re-emit completed earlier blocks. This gives no
duplicate or skipped pair when a block exceeds 500 pairs.

If the 10,000 total cap stops mid-block, emit the same cursor for that exact block
and pair, then use the identical exclusive resume rule on the next run. If any
blocking field or stable page ID changes, the recomputed inventory fingerprint
invalidates the cursor; fail closed and restart candidate generation from a fresh
inventory. A missing pair, block-key mismatch, malformed cursor, or fingerprint
mismatch also stops instead of guessing a resume position.

For every candidate pair, tokenize lowercase titles on spaces, hyphens, underscores,
and punctuation. Compute these deterministic features:

| Feature | Value |
|---|---:|
| `token_jaccard` | Jaccard similarity, weighted by 0.65 |
| `edit_similarity` | normalized Levenshtein similarity `1 - edits/max(lengths)`, weighted by 0.40 |
| `substring_signal` | 0.50 when one normalized title contains the other, otherwise 0 |
| `alias_bonus` | 0.65 for an exact normalized title-to-alias cross-match, otherwise 0 |
| same category | +0.10 |
| three or more shared tags | +0.15 |
| two shared tags | +0.05 |
| same dominant first tag | +0.05 |

Use exactly:

`score = min(1.0, max(0.65 * token_jaccard, 0.40 * edit_similarity, substring_signal) + alias_bonus + semantic_bonus)`.

Flag scores at least 0.75. Classify 0.90 or above as high confidence and
0.75–0.89 as medium confidence. Record every feature value so the result is
reproducible; the score only selects semantic review and never authorizes a merge.
Then read both complete safe snapshots and distinguish:

- true duplicates describing the same entity or concept;
- complementary pages that should cross-link instead;
- homonyms that must remain separate;
- versioned or scoped pages whose distinctions must be preserved.

For a proposed merge, identify the canonical final path, combined non-conflicting
content, union of authoritative sources, preserved aliases, required backlink
replacements, and the exact secondary page removal. Surface contradictions and do
not choose between conflicting claims without authority. Do not leave redirect
stubs: update inbound links in candidate replacements and declare the obsolete page
as a reviewed deletion.

An explicit dedup request authorizes a supported merge when the evidence establishes
one identity and the final path, combined content, backlink repairs, and deletion are
deterministic; conflicting identities or claims are semantic ambiguity: ask the user
to choose rather than merging them.

Report pair scores, evidence, verdicts, unresolved conflicts, final-page selection,
backlink impact, and proposed removals. Complete this read-only inventory and intent
confirmation before any merge. Audit-only mode stops after the report.

## Maintenance transaction protocol

For every promotion guard and final step 8, resolve the configured vault root
once relative to the validated repository root, require strict containment, and
derive its normalized non-empty repository-relative vault prefix; never hardcode
`wiki/`. Reject absolute, escaping, NUL, backslash, dot-segment,
empty-component, or ambiguous values. Prefix every validated vault-relative
candidate, deletion, canonical log, `created`, `updated`, `removed`,
`log_path`, and changed `hot.md` path with that vault prefix before
root-scoped literal-path Git. Manifest shards are already repository-relative
and must remain unprefixed.

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
   and review every warning plus every current page in `candidate_pages` and the
   current deletion set. Immediately before the initial promotion, repeat this
   fresh validation and bounded review. Then establish the pre-promotion overlap
   guard: individually derive every candidate page and deletion target, every
   affected manifest shard, and the configured vault's canonical log target that
   the result will identify as vault-relative `log_path`. Check each exact
   repository-relative path with
   `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<promotion-path>"]`.
   Every output must be empty. On any staged or unstaged change, stop before
   transaction mutation for a preserve, separate, or combine decision. Only then
   run `<wiki-cli> transaction commit <id> --json --pretty`.
6. Save the failed command envelope, including top-level `error` and `recovery`, on
   any failure. Inspect `recovery.preferred_action`. Trust its transaction ID only
   when present, then run `<wiki-cli> transaction show <id> --json --pretty` and
   require the retained record to have the same ID and status. Follow only a
   reported `recommended_action` or entry in `allowed_actions`, after satisfying
   every string in its `requires` list. If the ID or list is empty, missing,
   mismatched, duplicated, or ambiguous, stop and report. Only a successful
   `transaction commit` or `transaction retry` is a knowledge commit.
   After every reported action, reload structured state and compare its error code,
   status, `recovery.preferred_action`, `allowed_actions`, `requires`, identities,
   and exposed pre/postimages. Continue only when the next action is currently
   allowed and the last action made observable progress; never repeat an identical
   action against unchanged state. Immediately before every promotion-capable retry,
   rerun fresh validation, bounded-review every page in the current `candidate_pages`
   and the current deletion set, and repeat the pre-promotion overlap guard.
   Failed-state checks alone are insufficient. Retry automatically when its current
   requirements hold; restore automatically only with no owner drift, and ask before
   restore with drift and before work-losing abort or discard.
7. Only after a successful `transaction commit` or `transaction retry`, run
   `<wiki-cli> hot status --json`. If stale, establish the pre-hot-write overlap
   guard on the exact tracked hot path with
   `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<hot-path>"]`.
   Require empty output; on any staged or unstaged change, stop before hot mutation
   for a preserve, separate, or combine decision. Then run
   `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked
   `hot.md` working-tree diff, then run
   `<wiki-cli> hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.
8. Run `<wiki-cli> check --json --pretty` as the final check; it must pass before
   staging. The exact task paths are final created and updated knowledge paths, final
   deleted knowledge paths, every changed Source manifest shard, returned `log_path`,
   and changed `hot.md` when requested. Convert every validated vault-relative
   knowledge, `log_path`, and changed `hot.md` value by prefixing it with the
   retained repository-relative vault prefix; keep already repository-relative
   manifest shards unprefixed. For each converted path, individually derive and
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

Do not edit manifest shards, `index.md`, or `log.md` directly; transaction commit
owns the canonical log append. Do not write unsupported control paths.

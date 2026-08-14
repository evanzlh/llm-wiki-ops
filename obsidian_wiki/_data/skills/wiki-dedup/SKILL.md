---
name: wiki-dedup
description: Use when detecting duplicate knowledge pages, resolving identity collisions, or merging an owner-approved duplicate pair.
---

# Wiki Dedup

Detect page-level identity collisions conservatively. A similarity score produces a
review candidate, never automatic proof that two pages describe the same thing.

## Mandatory authority preflight

Locate the nearest ancestor `.obsidian-wiki/config.toml`, resolve its repository
root, and keep that repository root as the command working directory. Read root
`AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task
skill. Fail closed rather than guessing configuration or authority.

If no nearest config exists, stop and recommend exactly
`llmwikiops setup [DIR]`. If the nearest config is invalid, fail closed. In any
authority or instruction conflict, canonical `llm-wiki` wins.

## Safe Markdown inventory boundary

Before any page inventory or read, use the framework safe Markdown scanner. It
enforces repository and vault containment; ancestor components are real directories,
not a symlink, reparse point, or special file; and each terminal `.md` file is
ordinary and single-link. It opens with `O_NOFOLLOW`, checks `fstat`, device/inode
identity, link count, size, and attachment before and after bounded byte snapshots.
An unsafe entry or unavailable no-follow support must fail closed before decoding or
analysis. The agent must not use `read_text`, `rglob`, shell globbing, or follow
links. `llmwikiops check` alone is not a sufficient scanner preflight. CLI graph
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

Report pair scores, evidence, verdicts, unresolved conflicts, final-page selection,
backlink impact, and proposed removals. Complete this read-only inventory and intent
confirmation before any merge. Audit-only mode stops after the report.

## Maintenance transaction protocol

1. Finish the read-only inventory and intent confirmation. If there is no selected
   page change, stop without an empty transaction or operation record. Keep the
   live vault read-only while computing the complete source closure: every existing
   repository-relative Source ID cited by an affected page plus every authoritative
   Source ID cited by a candidate. Preserve valid Unicode and CJK Source IDs and
   filenames exactly. Stop on missing, ambiguous, untracked, or unsafe authority.
2. Begin exactly one bounded transaction with the entire closure:
   `llmwikiops transaction begin --source <source1> [source2 ...] --json --pretty`.
   Retain its `id` as the trusted transaction ID plus `candidate_vault` and
   `started_at`; do not change CWD.
3. Write final candidates only at final vault-relative knowledge paths below
   `candidate_vault`. Every candidate has valid required frontmatter and `sources`
   as a non-empty subset of the closure. New pages use `created = updated =
   started_at`; updates preserve `created` and set `updated = started_at`. Generate
   internal links with the resolved `OBSIDIAN_LINK_FORMAT`.
4. Register all reviewed deletions with
   `llmwikiops transaction delete <id> <vault-relative-page.md> --json --pretty`.
   Never delete a live page directly.
5. Run `llmwikiops transaction validate <id> --json --pretty`, fix every issue,
   review every warning and the complete candidate/deletion diff, then run
   `llmwikiops transaction commit <id> --json --pretty` only after validation
   passes.
6. Save the failed command envelope, including top-level `error` and `recovery`, on
   any failure. Inspect `recovery.preferred_action`. Trust its transaction ID only
   when present, then run `llmwikiops transaction list --json --pretty` and
   require exactly one retained record with the same ID and status. Follow only a
   reported `recommended_action` or entry in `allowed_actions`, after satisfying
   every string in its `requires` list. If the ID or list is empty, missing,
   mismatched, duplicated, or ambiguous, stop and report. Only a successful
   `transaction commit` or `transaction retry` is a knowledge commit.
7. Only after a successful `transaction commit` or `transaction retry`, run
   `llmwikiops hot status --json`. If stale, run
   `llmwikiops hot inputs --json --pretty`, write only the requested tracked
   `hot.md` working-tree diff, then run
   `llmwikiops hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.

Do not edit manifest shards, `index.md`, or `log.md` directly; transaction commit
owns the canonical log append. Do not run Git publication commands or write unsupported control paths.
Do not commit, push, or open a pull request.

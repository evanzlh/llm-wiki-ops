---
name: tag-taxonomy
description: Use when auditing wiki tags, normalizing tag vocabulary, or proposing tags for knowledge pages.
---

# Tag Taxonomy

Maintain a controlled vocabulary without erasing meaningful distinctions. Audits
and tag proposals are read-only; accepted page normalization is transactional.

## Mandatory authority preflight

Locate the nearest ancestor `.obsidian-wiki/config.toml`, resolve its repository
root, and keep that repository root as the command working directory. Read root
`AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task
skill. Fail closed rather than guessing configuration or authority.

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

## Analysis

Read `_meta/taxonomy.md` through the safe scanner as the authoritative vocabulary
when present, then scan knowledge-page frontmatter. Build a frequency table with
canonical tags, synonyms, unknown tags, casing variants, hierarchical variants,
and pages with excessive or missing tags. Treat visibility, lifecycle, provenance,
and other schema fields as separate metadata rather than ordinary topic tags.

Normalize only explicit aliases or clear spelling/case variants. Preserve a
specific child tag when replacing it with a broader parent would lose information.
Unknown tags require an owner decision: map to an existing canonical tag, accept a
new taxonomy term, or leave unchanged. Never silently invent a taxonomy rule.

The transaction validator rejects `_meta/` candidates, so the agent must not write
`_meta/taxonomy.md` into `candidate_vault`. If the owner approves a new term, stop
page normalization while the owner separately performs an explicit control-file edit
with a safe backup and Git diff review. After that owner-controlled edit is complete,
re-read `_meta/taxonomy.md` through the safe scanner, then plan the page-normalization
transaction. If the owner does not approve the new term, use existing canonical
mappings only or leave the unknown tag unchanged.

For a new page, propose the smallest useful set: usually one broad domain tag and
one or two specific topic tags. Report every proposed old-to-new mapping, affected
page, unknown term, and unresolved ambiguity. Complete this read-only inventory and
intent confirmation before selecting fixes. A pure audit stops after its report.

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

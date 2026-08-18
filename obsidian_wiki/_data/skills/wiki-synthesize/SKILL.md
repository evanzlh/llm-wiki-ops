---
name: wiki-synthesize
description: Use when discovering cross-page synthesis opportunities or drafting an owner-selected synthesis page from repository knowledge.
---

# Wiki Synthesize

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

For Git, use `git -C <root>` before Git subcommands in external context; in
repository-local context, run Git from the repository root.

Discover connections that are not already explained by a single source page. The
scan and opportunity report are read-only; selected synthesis and backlinks are
transactional.

## Mandatory authority preflight

Locate the nearest ancestor `.llmwikiops/config.toml`, resolve its repository
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
links. `<wiki-cli> check` alone is not a sufficient scanner preflight. CLI graph
and lint commands use this safe walker internally.

## Analysis

Build a co-occurrence map from internal links, tags, project contexts, typed
relationships, and explicit concept mentions. Exclude control and derived artifacts.
Filter pairs already explained by a synthesis page or a substantive existing
relationship. Rank remaining pairs by repeated co-occurrence, cross-category reach,
bridge value, source diversity, and unresolved tensions rather than word overlap
alone.

For each strong opportunity, report both concepts, supporting pages and sources,
the proposed connection, confidence, strongest objection, and unanswered questions.
Mark statements as extracted, inferred, or ambiguous. Never present an inference as
a source-backed fact, and do not synthesize from shared tags alone.

An accepted draft should explain the connection, where it appears, the cross-cutting
insight, tensions and trade-offs, strongest objection, open questions, and related
pages. Cite all supporting repository-relative Source IDs. Candidate backlink edits
must be substantive and individually listed; do not touch unrelated pages merely to
increase link counts.

Complete this read-only inventory and intent confirmation before selecting synthesis
or backlink changes. If the user requests opportunities only or accepts none, stop
after the report.

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
7. Only after a successful `transaction commit` or `transaction retry`, run
   `<wiki-cli> hot status --json`. If stale, run
   `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked
   `hot.md` working-tree diff, then run
   `<wiki-cli> hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.

Do not edit manifest shards, `index.md`, or `log.md` directly; transaction commit
owns the canonical log append. Do not run Git publication commands or write unsupported control paths.
Do not commit, push, or open a pull request.

---
name: wiki-agent
description: Use when answering a focused question from selected sessions of a named supported coding agent.
---

# Targeted Agent History

Find a bounded set of sessions relevant to a query, preserve reviewed evidence, update knowledge through one repository transaction, then answer. For complete tool schemas, use the retained tool-specific history skill selected by `wiki-history-ingest`. Follow [source snapshot rules](../wiki-capture/references/source-snapshot.md).

## Inventory and rank without broad reads

Resolve exactly one tool: Claude, Codex, Copilot, Hermes, OpenClaw, or Pi. Use only its native lightweight inventory (session index/metadata, SQLite summaries, memory headings, or Pi session headers). Do not scan full transcripts to rank. Score title/name match, explicit project attribution, summary keywords, recency with a floor for strong old matches, and whether stable tool/session identity plus content hash already has a snapshot. Select at most five sessions unless the user explicitly authorizes another bound.

Open only explicitly selected session files or immutable database rows. Use targeted matches plus bounded context, not an unbounded full read. Apply the retained tool parser and redaction rules without changing extraction semantics. Separate projects using native cwd/workspace/session metadata. Retain relevant decisions, corrections, alternatives, commands, and ambiguity; redact every secret, credential, private passage, and irrelevant payload. Preserve Unicode and state omissions.

## Parent and worker boundary

The parent owns tool routing, selection, snapshot materialization, all repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, hot refresh, and the synthesized answer. Workers are analysis-only over immutable inputs naming explicitly selected session files/row IDs and bounded ranges. Workers return evidence/proposals only and never expand discovery, write snapshots/pages, run list actions, or mutate state.

## Stable slice identity

For a targeted slice, use stable tool/session identity plus a stable query/slice identifier derived from normalized query terms and source-internal boundaries. Never derive identity from an absolute cache path. Repeating the same slice updates the same Source ID; distinct queries may produce distinct reviewed slices.

## Repository-native completion

The cache and every absolute cache path remain transient and never appear in snapshot or page provenance.

1. Parent writes each bounded reviewable UTF-8 Markdown snapshot below `sources/history/<tool>/`, recording `source_tool`, stable tool/session identity, stable slice identity, `captured_at`, `content_hash`, and `format`. Include only relevant excerpts and repository-relative labels; redact secret, private, and irrelevant content.
2. Validate every Unicode Source ID as a non-empty POSIX repository-relative path below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty status output. Tracked is not reviewed: stop for owner review, stage, and commit externally, then rerun.
3. Parent deduplicates live-page sources, accepted slice snapshots, and final candidate citations into the complete source closure and fails closed on missing/unsafe evidence.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates below the runtime candidate vault with non-empty accepted sources and declares deletions through CLI.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews every warning and prospective diff, then runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`, matches exactly one retained record, satisfies reported requirements, and follows only reported recovery.
8. Only after successful knowledge commit, inspect `obsidian-wiki hot status --json`, inputs, and mark-current when required. Then answer with selected session identities, evidence-backed synthesis, snapshot Source IDs, omissions/gaps, and page changes. Do not commit, push, or open a pull request.

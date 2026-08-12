---
name: codex-history-ingest
description: Use when mining selected Codex rollout sessions for durable repository knowledge.
---

# Codex History Ingest

Mine selected Codex sessions while keeping the cache transient. Read [Codex data format](references/codex-data-format.md) and [source snapshot rules](../wiki-capture/references/source-snapshot.md).

## Discovery and parsing

Inventory `~/.codex/session_index.jsonl`, `~/.codex/sessions/**/rollout-*.jsonl`, and archived rollouts only when explicitly requested. Use the index for stable thread ID, name, and freshness, then open only explicitly selected session files. Parse one JSON object per line: `session_meta` establishes ID/cwd/model; `turn_context` establishes project and branch context; `event_msg` and `response_item` carry user/assistant content. Keep text and reasoning summaries that support durable decisions. Skip token counts, progress, raw tool payloads, environment dumps, and encrypted content. Attribute projects from recorded `cwd`, never from cache-directory guesses.

Append selection compares stable tool/session identity and content hash against snapshots; Full may reconsider unchanged sessions without changing extraction semantics. Bound sessions, bytes/lines, time range, and excerpts. Redact secrets, credentials, private material, and irrelevant content. Preserve valid Unicode exactly.

## Parent and worker boundary

The parent owns selection, snapshot materialization, repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files and bounded ranges. They return evidence and proposals only; they do not discover, write, list, or mutate.

## Repository-native completion

Every absolute cache path is transient and must never appear in snapshot or page provenance.

1. Parent writes each bounded reviewable UTF-8 Markdown snapshot below `sources/history/<tool>/` (`sources/history/codex/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant material.
2. Validate each Unicode Source ID as a non-empty POSIX repository-relative path below configured sources using source_id semantics. Reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty output. Tracked is not reviewed: stop for owner review, stage, and commit externally, then rerun.
3. The parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure and fails closed on any unsafe source.
4. The parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. The parent alone writes final candidates below the returned candidate vault, with non-empty accepted sources, and declares deletions through the transaction CLI.
6. The parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews all prospective changes, then runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. On failure, refresh `obsidian-wiki transaction list --json --pretty`, match one retained record, verify reported requirements, and use only its allowed recovery. Never invent an action.
8. After successful knowledge commit only, inspect `obsidian-wiki hot status --json`, review inputs, mark current if required, and report sessions, snapshots, omissions, and page changes. Do not commit, push, or open a pull request.

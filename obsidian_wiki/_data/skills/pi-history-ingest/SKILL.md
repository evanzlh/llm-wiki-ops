---
name: pi-history-ingest
description: Use when mining selected Pi agent session history for durable repository knowledge.
---

# Pi History Ingest

Mine selected Pi JSONL sessions and materialize repository-reviewed evidence. Follow [source snapshot rules](../wiki-capture/references/source-snapshot.md).

## Discovery and parsing

Resolve the session root from `PI_CODING_AGENT_SESSION_DIR` when that override is set; otherwise use `~/.pi/agent/sessions/`. Inventory `<root>/--<cwd>--/<timestamp>_<uuid>.jsonl`; the filesystem is the index. Decode the directory only as an initial project hint, then prefer the session header `cwd`. Read the first line and require a `session` header before selecting. Use `session_info` events for the latest human session name. Open only explicitly selected session files.

### Tree-JSONL structure

The first line is a session header with `cwd`, `version`, `id`, `timestamp`, and optional `parentSession`. Subsequent entries form a tree: each has `id` and normally a `parentId`. Parse all bounded entries into an ID map, identify the current leaf (the last leaf/message when no separate pointer exists), walk its `parentId` chain to the root, then reverse that chain. Analyze only this active branch in chronological order. Retain entry timestamps and JSONL line numbers so evidence ordering and omissions remain reviewable. A cycle, duplicate ID, missing parent, malformed header, or ambiguous current leaf fails closed rather than silently merging branches.

Entry types and handling:

- `message` is the primary evidence record. Roles include `user`, `assistant`, `toolResult`, and `bashExecution`.
- `session_info` contributes the display name, never factual knowledge.
- `compaction` and `branch_summary` contain high-signal summaries; retain their source entry identity and distinguish them from verbatim turns.
- `model_change`, `thinking_level_change`, `custom`, and `label` are operational state and must be filtered. A `custom_message` is context-only unless explicitly relevant.

### Message content fields

- A `user` message `content` is a string or ordered `(TextContent | ImageContent)[]`; retain text in array order and skip images unless explicitly authorized for transcription.
- An `assistant` message `content` is an ordered `(TextContent | ThinkingContent | ToolCall)[]`; retain visible text, skip thinking, and summarize relevant tool names/actions without copying sensitive arguments.
- A `toolResult` has ordered `(TextContent | ImageContent)[]`; summarize the outcome and bound raw output.
- A `bashExecution` carries `command`, `output`, and `exitCode`; keep the command and outcome when durable, truncate/redact sensitive output.
- Compaction/branch summary message variants carry a `summary` string.

Use the header `cwd` as the primary project attribution, the decoded directory only as a cross-check, and `session_info.name` only as a topic hint. Preserve message/block order, source-internal `timestamp`, entry ID, and line number in the private evidence ledger. Redact tokens, passwords, credentials, private identifiers, sensitive paths/environment values, and irrelevant tool payloads before snapshot proposals.

Append compares stable tool/session identity and content hash with existing snapshots; Full changes bounded selection only. State session/byte/line/time limits and omissions. Redact secrets, credentials, private material, and irrelevant content. Preserve valid Unicode exactly.

## Parent and worker boundary

The parent owns selection, snapshot materialization, repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files and bounded ranges. They return evidence/proposals only; they never discover, write, list, or mutate.

## Repository-native completion

Every absolute cache path is transient and forbidden from snapshot/page provenance.

1. Parent writes bounded reviewable UTF-8 Markdown snapshot files under `sources/history/<tool>/` (`sources/history/pi/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant material.
2. Validate Unicode Source IDs as non-empty POSIX repository-relative paths below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty status. Tracked is not reviewed: stop for owner review, stage, and commit externally, then rerun.
3. Parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure and fails closed on unsafe input.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates under the runtime candidate vault with non-empty accepted sources and declares deletions through CLI.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews all changes, and runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`, matches exactly one record, and follows only reported recovery after satisfying requirements.
8. After successful knowledge commit, inspect `obsidian-wiki hot status --json`, inputs, and mark-current when required; report sessions, Source IDs, omissions, changes, and recovery. Do not commit, push, or open a pull request.

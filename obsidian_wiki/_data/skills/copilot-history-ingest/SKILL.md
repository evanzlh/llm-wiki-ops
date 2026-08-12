---
name: copilot-history-ingest
description: Use when mining selected GitHub Copilot CLI or VS Code chat sessions for durable repository knowledge.
---

# Copilot History Ingest

Use [Copilot data format](references/copilot-data-format.md) for SQLite/JSONL details and [source snapshot rules](../wiki-capture/references/source-snapshot.md) for evidence.

## Discovery and parsing

Inventory `~/.copilot/session-store.db`, `~/.copilot/session-state/<uuid>/`, and VS Code `workspaceStorage/*/GitHub.copilot-chat/` without opening every transcript. Query the SQLite store read-only for `sessions`, `turns`, `checkpoints`, `session_files`, `session_refs`, and FTS `search_index`. Prefer checkpoint summaries, then session summaries, then selected turns. Per-session `workspace.yaml`, `vscode.metadata.json`, `index.md`, and checkpoints provide identity/title; `events.jsonl` and transcript JSONL use `session.start`, `user.message`, `assistant.message`, and tool events. Decode memory directory names only to associate an existing session ID.

Open only explicitly selected session files or database rows. Attribute projects from `session.start.data.context.cwd`, SQLite workspace/cwd fields, and branch; never reverse engineer workspace hashes or persist absolute cache paths. Append compares stable tool/session identity and content hash with snapshots; Full broadens bounded analysis only. State session/row/byte/line/time limits. Redact secrets, private personal material, and irrelevant tool output. Preserve Unicode.

## Parent and worker boundary

The parent owns selection, snapshots, all repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files/row IDs and bounds. Workers return evidence/proposals only and never discover extra inputs, write, list, or mutate.

## Repository-native completion

Every absolute cache path is transient and forbidden in snapshot/page provenance.

1. Parent writes bounded reviewable UTF-8 Markdown snapshot evidence below `sources/history/<tool>/` (`sources/history/copilot/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant material.
2. Validate Unicode Source IDs as non-empty POSIX repository-relative paths below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty status. Tracked is not reviewed: stop for owner review, stage, and commit externally, then rerun.
3. Parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure and fails closed on mismatch.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates under the runtime candidate vault with non-empty accepted sources and declares deletions via CLI.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews the prospective diff, then runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`, matches exactly one record, checks requirements, and follows only reported recovery.
8. After successful knowledge commit only, inspect `obsidian-wiki hot status --json`, inputs, and mark-current if needed; report sessions, Source IDs, omissions, and changes. Do not commit, push, or open a pull request.

---
name: pi-history-ingest
description: Use when mining selected Pi agent session history for durable repository knowledge.
---

# Pi History Ingest

Mine selected Pi JSONL sessions and materialize repository-reviewed evidence. Follow [source snapshot rules](../wiki-capture/references/source-snapshot.md).

## Discovery and parsing

Inventory `~/.pi/agent/sessions/--<cwd>--/<timestamp>_<uuid>.jsonl`; the filesystem is the index. Decode the directory only as an initial project hint, then prefer the session header `cwd`. Read the first JSONL record and `session_info` events for stable UUID, start time, model, and human session name before selecting. Open only explicitly selected session files. Parse user/assistant messages and relevant tool results; keep durable decisions, corrections, commands, and unresolved ambiguity, while skipping progress, large generated payloads, telemetry, and repeated output.

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

---
name: openclaw-history-ingest
description: Use when mining selected OpenClaw memory or session history for durable repository knowledge.
---

# OpenClaw History Ingest

Use [OpenClaw data format](references/openclaw-data-format.md) for exact formats and [source snapshot rules](../wiki-capture/references/source-snapshot.md) for evidence.

## Discovery and parsing

Inventory `~/.openclaw/workspace/MEMORY.md`, bounded daily `workspace/memory/YYYY-MM-DD.md`, optional `DREAMS.md`, `agents/<agentId>/sessions/sessions.json`, and session JSONL. Prefer curated memory for triage. Use `sessions.json` for stable session ID, human label, channel, and freshness before opening only explicitly selected session files. Parse base and `-topic-<threadId>` JSONL identically, associating user/assistant/tool records with the native session ID. Attribute projects from explicit memory headings/session metadata, not directory guesses. Treat `openclaw.json` as configuration only and never extract tokens or provider credentials.

Append compares stable tool/session identity/content hash against snapshots; Full changes bounded selection only. Declare session/day/byte/line/time limits and omissions. Redact secrets, private material, irrelevant content, channel identifiers, and credentials; preserve Unicode.

## Parent and worker boundary

The parent owns selection, snapshots, repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files and bounded ranges. They return evidence/proposals only and never discover, write, list, or mutate.

## Repository-native completion

An absolute cache path is transient and never provenance.

1. Parent writes bounded reviewable UTF-8 Markdown snapshot files under `sources/history/<tool>/` (`sources/history/openclaw/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant data.
2. Validate every Unicode Source ID as a non-empty POSIX repository-relative path below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty status. Stop for owner review, stage, and commit externally, then rerun when new/dirty; tracked is not reviewed.
3. Parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates under the runtime candidate vault with non-empty sources and declares deletions through CLI.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews diff/warnings, and runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`, matches exactly one record, satisfies requirements, and follows only reported recovery.
8. After successful commit only, inspect `obsidian-wiki hot status --json`, inputs, and mark-current when needed; report selections, snapshots, omissions, changes, and recovery. Do not commit, push, or open a pull request.

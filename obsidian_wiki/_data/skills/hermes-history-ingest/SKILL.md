---
name: hermes-history-ingest
description: Use when mining selected Hermes memory or session history for durable repository knowledge.
---

# Hermes History Ingest

Use [Hermes data format](references/hermes-data-format.md) for schemas and [source snapshot rules](../wiki-capture/references/source-snapshot.md) for repository evidence.

## Discovery and parsing

Inventory `~/.hermes/memories/**/*.md`, memory JSON, and `~/.hermes/sessions/**/*.jsonl` when session logging exists. Ignore `.hub/`, installed-skill manifests, telemetry, and config credentials. Prefer human-reviewed memory for triage, then verify against only explicitly selected session files. Parse JSONL by `session_meta`, user/assistant messages, and relevant tool pairs. Use native session ID, internal timestamps, and recorded cwd/project metadata for identity and project attribution. Never infer a project from an absolute cache path.

Append compares stable tool/session identity and content hash with existing snapshots; Full only broadens bounded selection. State session/byte/line/time bounds and omissions. Redact secrets, private material, and irrelevant content; preserve Unicode.

## Parent and worker boundary

The parent owns selection, snapshot writes, repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files and bounded ranges; they return evidence/proposals and never discover, write, list, or mutate.

## Repository-native completion

An absolute cache path is transient and never snapshot or page provenance.

1. Parent creates each bounded reviewable UTF-8 Markdown snapshot under `sources/history/<tool>/` (`sources/history/hermes/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant data.
2. Validate every Unicode Source ID as non-empty POSIX repository-relative configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty output. Tracked alone is insufficient: stop for owner review, stage, and commit externally, then rerun.
3. Parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates under the runtime candidate vault with non-empty source subsets; use CLI deletion declarations.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews warnings/diff, and runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`, matches exactly one record, and follows only reported recovery after satisfying requirements.
8. After successful commit, inspect `obsidian-wiki hot status --json`, inputs, and mark-current when required; report sessions, Source IDs, omissions, changes, and recovery. Do not commit, push, or open a pull request.

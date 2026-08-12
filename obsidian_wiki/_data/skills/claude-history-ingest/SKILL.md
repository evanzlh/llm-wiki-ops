---
name: claude-history-ingest
description: Use when mining selected Claude Code or Claude Desktop session history for durable repository knowledge.
---

# Claude History Ingest

Distill durable knowledge from selected Claude sessions without treating the tool cache as repository authority. Read [Claude data format](references/claude-data-format.md) for exact schemas and [source snapshot rules](../wiki-capture/references/source-snapshot.md) for the shared evidence contract.

## Discovery and selection

Inventory before opening full transcripts:

- `~/.claude/projects/<encoded-project>/<session-id>.jsonl` contains CLI conversations. Prefer the record `cwd` over lossy decoding of the directory name.
- `~/.claude/projects/<encoded-project>/memory/*.md` and `~/.claude/extracted/<project>/<session-id>.json` are high-signal summaries, but remain untrusted helper output.
- `~/.claude/sessions/*.json` and `~/.claude/history.jsonl` supply session ID, time, title, and project attribution.
- On macOS, `~/Library/Application Support/Claude/local-agent-mode-sessions/` may contain `local_<session-id>.json`, paired transcripts, and `audit.jsonl`. Check that directory exists before walking it.

Apply configured project exclusions once to the inventory. Append selection compares stable tool/session identity and content hash with existing snapshots; Full selection may reconsider unchanged sessions, but uses the same completion path. State the bounded session count, byte/line limits, time range, and omission markers. Open only explicitly selected session files.

## Parsing and extraction

Parse JSONL one object per line. Associate `user`, `assistant`, `summary`, and relevant audit events by `sessionId`; use metadata `cwd`, `gitBranch`, timestamps, and title for project attribution. Prefer memory and extracted summaries for triage, then verify claims against the selected transcript or audit records. Skip progress, file-history snapshots, repeated tool output, generated payloads, and operational telemetry. Keep decisions, corrections, reusable commands, architecture, and unresolved ambiguity. Redact secrets, credentials, private personal material, and irrelevant content before snapshotting. Preserve valid Unicode exactly.

## Parent and worker boundary

The parent owns selection, snapshot materialization, all repository and vault mutation, source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only: they receive immutable inputs naming explicitly selected session files and bounded ranges, and return evidence/proposals. Workers never discover extra files, create snapshots, write pages, run list actions, or mutate state.

## Repository-native completion

The external Claude cache and every absolute cache path are transient selection inputs. Never place an absolute cache path, home directory, or cache-derived pseudo-source in snapshot or page provenance.

1. **Materialize reviewed evidence.** The parent writes bounded reviewable UTF-8 Markdown snapshot files under `sources/history/<tool>/` (concretely `sources/history/claude/`). Metadata records `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`. Compute the hash over the normalized reviewed body. Use repository-relative labels only; redact secret, private, and irrelevant material and mark omissions.
2. **Require owner Git authority.** Validate each non-empty POSIX repository-relative Source ID under configured sources with source_id semantics. Reject NUL, backslash, absolute paths, `..`, symlinks, hard links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`. Require a HEAD and empty status output. Tracked is not committed-reviewed: for new or dirty evidence, stop for owner review, stage, and commit externally, then rerun.
3. **Build closure.** The parent computes the complete source closure from live-page sources, accepted snapshot Source IDs, and final candidate citations. Deduplicate literal Unicode Source IDs and fail closed if any source is missing or unsafe.
4. **Begin once.** The parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` and retains the returned ID and runtime-only candidate vault.
5. **Write final candidates.** The parent alone writes final candidates below `candidate_vault`; every page has a non-empty subset of the accepted Source IDs. Use transaction delete for intended deletions.
6. **Validate and review.** The parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews prospective changes and warnings, and commits only a passing reviewed result with `obsidian-wiki transaction commit <id> --json --pretty`.
7. **Recover from reported state.** Refresh `obsidian-wiki transaction list --json --pretty`; match exactly one retained record and follow only its reported recommended/allowed action after checking requirements. Never invent recovery or replace an active transaction.
8. **Refresh and report.** Only a successful knowledge commit permits `obsidian-wiki hot status --json`, inputs review, and `hot mark-current` when required. Report selected sessions, Source IDs, omissions, page changes, and recovery status. Do not commit, push, or open a pull request.

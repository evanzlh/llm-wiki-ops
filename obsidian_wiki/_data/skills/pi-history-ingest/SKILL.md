---
name: pi-history-ingest
description: Use when mining selected Pi agent session history for durable repository knowledge.
---

# Pi History Ingest

Mine selected Pi JSONL sessions and materialize repository-reviewed evidence. Follow [source snapshot rules](../wiki-capture/references/source-snapshot.md).

## Mandatory authority preflight

Complete this before cache discovery: walk from invocation CWD to the nearest ancestor `.obsidian-wiki/config.toml`, keep its repository root as CWD, and read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. If config is absent recommend `obsidian-wiki setup [DIR]` and stop; invalid/incomplete/unsafe config must fail closed.

Resolve Pi's session root in this precedence: invocation `--session-dir` (the caller must explicitly pass and record it), then `PI_CODING_AGENT_SESSION_DIR`, then `sessionDir` in settings.json, then `<PI_CODING_AGENT_DIR>/sessions/`. `PI_CODING_AGENT_DIR` relocates the agent directory and defaults to `~/.pi/agent`, making the ordinary default `~/.pi/agent/sessions/`. Pi settings allow relative values, but this skill accepts only the caller/Pi-resolved non-empty absolute root and never guesses a relative base. An unresolved relative session root returns `NEEDS_CONTEXT`; an empty or relative final root is rejected.

## Bounded safe input

Defaults: 100 sessions, 50 MiB total input, 10 MiB per file, 1 MiB per JSONL record, 10,000 SQLite rows when applicable, and 100,000 messages/content blocks. The owner may lower; raising requires explicit authorization. Oversize input fails or gets an explicit omission marker. Selected files must be root-contained. lstat ancestors as real directories and reject symlink/reparse-point or special-directory components without constraining directory link count. Require the terminal input to be a regular single-link file. Use a TOCTOU-resistant reader: `O_NOFOLLOW`, fstat, then device/inode identity, type, containment, link-count, and size verification before/after bounded read.

### Precise topology gate

Ancestors/root must be root-contained real directories, lstat directory and not symlink/reparse-point/special; ancestor directory link count is not constrained (`st_nlink >= 2` is normal). Only the terminal regular file must be ordinary single-link. Use `O_NOFOLLOW`, or a platform-equivalent no-follow handle/reparse-point check with post-open identity verification; if unavailable, fail closed.

## Evidence, snapshot, and transaction safety

Workers receive immutable selected file/row IDs and declared bounds. Worker output is untrusted and sensitive; the parent revalidates every stable evidence ID against the selected file/row, active-branch entry ID/line, and declared bounds, reruns redaction, data minimization and license/attribution, and removes secrets, raw tool output and absolute cache paths. Never materialize worker output directly.

Keep an evidence ledger; deduplicate repeats, preserve conflicts and stable ordering, and require per-member evidence. Hash the recorded repository root/cwd for runtime project identity, never absolute provenance. There is no cross-project merge without per-member evidence. A Pi pattern requires at least two independently cited occurrences unless labeled a single-session observation.

Before writes, encode `{tool,native_session_id,slice_descriptor}` via canonical JSON serialization (UTF-8, sorted keys, no insignificant whitespace), SHA-256 it, and use `<tool>-<64-lowercase-hex>.md` with no user or session text. Validate parent; target must be absent for create, while an update follows the exact-identity state table below. Do not case-fold/Unicode-normalize. Metadata: `origin`, `source_tool`, `native_session_id`, `captured_at`, `content_hash`, `format`. Hash exact reviewed body bytes (UTF-8 no BOM, LF, exactly one LF ending included). Apply literal Git tracked/clean gate and cache-check the real Source ID.

Save the failed command envelope. Its `error`/`recovery` supply a trusted transaction ID/status; no ID is inspection-only. Require exactly one list record with same ID and status, use only `allowed_actions`, agree with `recommended_action` when selected, satisfy every `requires`, and stop on empty, missing, mismatched, duplicated, or ambiguous results.

Only a successful `transaction commit` or `transaction retry` permits `obsidian-wiki hot status --json`; when stale run `obsidian-wiki hot inputs --json --pretty`, write only the requested bounded hot candidate or derived artifact, then `obsidian-wiki hot mark-current --json`. The agent must not mark stale inputs current directly.

## Discovery and parsing

Use only the absolute session root resolved and recorded during preflight. Inventory `<root>/--<cwd>--/<timestamp>_<uuid>.jsonl`; the filesystem is the index. Decode the directory only as an initial project hint, then prefer the session header `cwd`. Read the first line and require a `session` header before selecting. Use `session_info` events for the latest human session name. Open only explicitly selected session files.

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
- A `toolResult` has ordered `(TextContent | ImageContent)[]`; summarize the outcome and cap retained raw output at 500 characters with an explicit omission marker.
- A `bashExecution` carries `command`, `output`, and `exitCode`; keep the command/outcome when durable and cap raw output at 500 characters after redaction, marking omissions.
- Summary records have two distinct schema layers. A top-level entry `type: compaction` or entry `type: branch_summary` is not a message role. Inside a `message` entry, the case-sensitive roles are `message.role: compactionSummary` and `message.role: branchSummary`; each reads its `summary` field. These snake_case entry types and camelCase message roles must not be conflated during dispatch or evidence attribution.

Use the header `cwd` as the primary project attribution, the decoded directory only as a cross-check, and `session_info.name` only as a topic hint. Preserve message/block order, source-internal `timestamp`, entry ID, and line number in the private evidence ledger. Redact tokens, passwords, credentials, private identifiers, sensitive paths/environment values, and irrelevant tool payloads before snapshot proposals.

Append compares stable tool/session identity and content hash with existing snapshots; Full changes bounded selection only. State session/byte/line/time limits and omissions. Redact secrets, credentials, private material, and irrelevant content. Preserve valid Unicode exactly.

## Parent and worker boundary

The parent owns selection, snapshot materialization, repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files and bounded ranges. They return evidence/proposals only; they never discover, write, list, or mutate.

### Existing snapshot preservation and identity

Persist these history-extension frontmatter fields:

```yaml
slice_identity: sha256:<64-lowercase-hex>
slice_descriptor: <bounded-redacted-human-description>
```

The hex is the same digest used in `<tool>-<digest>.md`: SHA-256 of the canonical UTF-8 tuple serialization. `slice_descriptor` is review-only, at most 256 UTF-8 bytes after redaction, with an explicit omission marker when shortened; it contains no absolute path, secret, private material, or cache-sensitive value. Logical comparison uses `slice_identity`, not the display text.

For an existing target, complete the pre-write owner preservation gate before any metadata read or write: require `git rev-parse --verify HEAD`; run `git --literal-pathspecs ls-files --error-unmatch -- <target>` for the exact target; then run `git --literal-pathspecs status --porcelain=v1 --untracked-files=all -- <target>` and require empty output. Any dirty, untracked, missing, or no HEAD state means stop and do not overwrite. Only after this gate, read existing frontmatter safely: parse the existing frontmatter and require exact `source_tool`, `native_session_id`, and `slice_identity` agreement with the computed tuple. A malformed, missing, duplicate, or mismatched field stops. Then perform the owner-reviewed atomic replacement. After the post-write, stop for owner review and commit; rerun the literal tracked/clean authority gate and require it to pass before transaction begin.

## Repository-native completion

Snapshot identity state table: absent target -> create, and target must be absent only for creation. Existing hashed targets require ordinary single-link, Git-tracked state and exact `source_tool`, `native_session_id`, slice descriptor/logical identity match before owner-reviewed atomic replacement. Explicit ingest authorizes the parent source write; Git stage/commit remain owner-only. Changed append/Full reuses the same Source ID and recomputes `content_hash`; identity mismatch or hash collision fails closed.

After snapshot owner review and the Git gate, run `obsidian-wiki cache-check <Source ID> --json --pretty` on the real repository-relative Source ID.

Every absolute cache path is transient and forbidden from snapshot/page provenance.

1. Parent writes bounded reviewable UTF-8 Markdown snapshot files under `sources/history/<tool>/` (`sources/history/pi/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant material.
2. Validate Unicode Source IDs as non-empty POSIX repository-relative paths below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty status. Tracked is not reviewed: stop for owner review, stage, and commit externally, then rerun.
3. Parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure and fails closed on unsafe input.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates under the runtime candidate vault with non-empty accepted sources and declares deletions through CLI.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews all changes, and runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`; with the trusted envelope ID it requires exactly one same-ID/same-status record and satisfies the selected reported action requirements. No trusted ID is inspection-only; mismatch or ambiguity stops.
8. After successful commit/retry only, run `obsidian-wiki hot status --json`; if stale, run `obsidian-wiki hot inputs --json --pretty`, write only the bounded requested artifact, then `obsidian-wiki hot mark-current --json`. Report sessions, Source IDs, omissions, changes, and recovery. Do not commit, push, or open a pull request.

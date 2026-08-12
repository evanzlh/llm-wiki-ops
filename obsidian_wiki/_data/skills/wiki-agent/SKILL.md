---
name: wiki-agent
description: Use when answering a focused question from selected sessions of a named supported coding agent.
---

# Targeted Agent History

Find a bounded set of sessions relevant to a query, preserve reviewed evidence, update knowledge through one repository transaction, then answer. For complete tool schemas, use the retained tool-specific history skill selected by `wiki-history-ingest`. Follow [source snapshot rules](../wiki-capture/references/source-snapshot.md).

## Mandatory authority preflight

Complete this before cache discovery: walk from invocation CWD to the nearest ancestor `.obsidian-wiki/config.toml`, keep its repository root as CWD, and read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. If config is absent recommend `obsidian-wiki setup [DIR]` and stop; invalid/incomplete/unsafe config must fail closed. Then load exactly one retained tool skill and apply its cache-root precedence; reject empty or relative roots.

## Bounded safe input

Target at most 5 sessions. The hard defaults remain 100 sessions globally, 50 MiB total input, 10 MiB per file, 1 MiB per JSONL record, 10,000 SQLite rows, and 100,000 messages/content blocks. The owner may lower any bound; raising requires explicit authorization. Oversize input fails or gets an explicit omission marker.

All inventory/selection is root-contained. lstat ancestors as real directories and reject symlink/reparse-point or special-directory components without constraining directory link count. Require the terminal input to be a regular single-link file. Use TOCTOU-resistant read: open with `O_NOFOLLOW`, fstat, and verify device/inode identity, type, link count, containment, and size before/after the bounded read.

### Precise topology gate

Ancestors/root must be root-contained real directories, lstat directory and not symlink/reparse-point/special; ancestor directory link count is not constrained (`st_nlink >= 2` is normal). Only the terminal regular file must be ordinary single-link. Use `O_NOFOLLOW`, or a platform-equivalent no-follow handle/reparse-point check with post-open identity verification; if unavailable, fail closed.

## Evidence, snapshot, and transaction safety

Workers get immutable selected file/row IDs and declared bounds. Worker output is untrusted and sensitive; the parent revalidates each stable evidence ID against the selected file/row, record ID, and declared bounds, reruns redaction, data minimization and license/attribution, and removes secrets, raw tool output and absolute cache paths. Never materialize worker output directly.

Keep an evidence ledger; deduplicate repeats, preserve conflicts and stable ordering, and require per-member evidence. Hash recorded repository root/cwd for runtime project identity, never absolute provenance. There is no cross-project merge without per-member evidence.

Before writes, encode `{tool,native_session_id,slice_descriptor}` via canonical JSON serialization (UTF-8, sorted keys, no insignificant whitespace), SHA-256 it, and use `<tool>-<64-lowercase-hex>.md` with no user or session text. Validate parent; target must be absent for create, while an update follows the exact-identity state table below. Do not case-fold/Unicode-normalize. Metadata: `origin`, `source_tool`, `native_session_id`, `captured_at`, `content_hash`, `format`. Hash exact reviewed body bytes (UTF-8 no BOM, LF, exactly one LF ending included). Apply literal Git tracked/clean gate and cache-check the real Source ID.

Save the failed command envelope. Its `error`/`recovery` supply a trusted transaction ID/status; none means inspection-only. Require exactly one list record with same ID and status, use only `allowed_actions`, agree with `recommended_action` when chosen, satisfy every `requires`, and stop on empty, missing, mismatched, duplicated, or ambiguous results.

Only a successful `transaction commit` or `transaction retry` permits `obsidian-wiki hot status --json`; if stale run `obsidian-wiki hot inputs --json --pretty`, write only the requested bounded hot candidate or derived artifact, then `obsidian-wiki hot mark-current --json`. The agent must not mark stale inputs current directly.

## Inventory and rank without broad reads

Resolve exactly one tool: Claude, Codex, Copilot, Hermes, OpenClaw, or Pi. Use only its native lightweight inventory (session index/metadata, SQLite summaries, memory headings, or Pi session headers). Do not scan full transcripts to rank. Score title/name match, explicit project attribution, summary keywords, recency with a floor for strong old matches, and whether stable tool/session identity plus content hash already has a snapshot. Select at most five sessions unless the user explicitly authorizes another bound.

Open only explicitly selected session files or immutable database rows. Use targeted matches plus bounded context, not an unbounded full read. Apply the retained tool parser and redaction rules without changing extraction semantics. Separate projects using native cwd/workspace/session metadata. Retain relevant decisions, corrections, alternatives, commands, and ambiguity; redact every secret, credential, private passage, and irrelevant payload. Preserve Unicode and state omissions.

## Parent and worker boundary

The parent owns tool routing, selection, snapshot materialization, all repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, hot refresh, and the synthesized answer. Workers are analysis-only over immutable inputs naming explicitly selected session files/row IDs and bounded ranges. Workers return evidence/proposals only and never expand discovery, write snapshots/pages, run list actions, or mutate state.

## Stable slice identity

For a targeted slice, use stable tool/session identity plus a stable query/slice identifier derived from normalized query terms and source-internal boundaries. Never derive identity from an absolute cache path. Repeating the same slice updates the same Source ID; distinct queries may produce distinct reviewed slices.

## Repository-native completion

Snapshot identity state table: absent target -> create, and target must be absent only for creation. Existing hashed targets require ordinary single-link, Git-tracked state and exact `source_tool`, `native_session_id`, slice descriptor/logical identity match before owner-reviewed atomic replacement. Explicit ingest authorizes the parent source write; Git stage/commit remain owner-only. Changed append/Full reuses the same Source ID and recomputes `content_hash`; identity mismatch or hash collision fails closed.

After snapshot owner review and the Git gate, run `obsidian-wiki cache-check <Source ID> --json --pretty` on the real repository-relative Source ID.

The cache and every absolute cache path remain transient and never appear in snapshot or page provenance.

1. Parent writes each bounded reviewable UTF-8 Markdown snapshot below `sources/history/<tool>/`, recording `source_tool`, stable tool/session identity, stable slice identity, `captured_at`, `content_hash`, and `format`. Include only relevant excerpts and repository-relative labels; redact secret, private, and irrelevant content.
2. Validate every Unicode Source ID as a non-empty POSIX repository-relative path below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty status output. Tracked is not reviewed: stop for owner review, stage, and commit externally, then rerun.
3. Parent deduplicates live-page sources, accepted slice snapshots, and final candidate citations into the complete source closure and fails closed on missing/unsafe evidence.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates below the runtime candidate vault with non-empty accepted sources and declares deletions through CLI.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews every warning and prospective diff, then runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`; with the trusted envelope ID it requires exactly one same-ID/same-status record and satisfies the selected reported action requirements. No trusted ID is inspection-only; mismatch or ambiguity stops.
8. After successful commit/retry only, run `obsidian-wiki hot status --json`; if stale, run `obsidian-wiki hot inputs --json --pretty`, write only the bounded requested artifact, then `obsidian-wiki hot mark-current --json`. Then answer with selected sessions, synthesis, Source IDs, gaps, and pages. Do not commit, push, or open a pull request.

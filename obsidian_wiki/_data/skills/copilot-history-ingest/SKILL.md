---
name: copilot-history-ingest
description: Use when mining selected GitHub Copilot CLI or VS Code chat sessions for durable repository knowledge.
---

# Copilot History Ingest

Use [Copilot data format](references/copilot-data-format.md) for SQLite/JSONL details and [source snapshot rules](../wiki-capture/references/source-snapshot.md) for evidence.

## Mandatory authority preflight

Complete this before cache discovery: walk upward from invocation CWD to the nearest ancestor `.obsidian-wiki/config.toml` and keep its repository root as CWD. If absent, stop and recommend `obsidian-wiki setup [DIR]`; invalid/incomplete/unsafe config must fail closed. Read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill, in order.

Resolve CLI session state from non-empty absolute `COPILOT_HISTORY_PATH` when set, otherwise absolute `~/.copilot/session-state`; reject an empty or relative override/root. The sibling SQLite database and VS Code stores are separate allowed roots and must be explicitly selected from their documented locations, not inferred from an arbitrary path.

## Bounded safe input

Defaults: 100 sessions, 50 MiB total input, 10 MiB per file, 1 MiB per JSONL record, 10,000 SQLite rows, and 100,000 messages/content blocks. The owner may lower; a raise needs explicit authorization. Oversize input fails or receives an explicit omission marker. Every selected input must be root-contained; lstat every ancestor and reject a terminal or intermediate symlink, hard link (`st_nlink != 1`), FIFO, socket/device, or special file. For TOCTOU safety open ordinary files with `O_NOFOLLOW`, fstat, and verify device/inode identity, type, link count, containment and size before/after bounded read.

For SQLite, perform schema detection (`sqlite_master` plus `PRAGMA table_info`) before named-column queries and apply an explicit `LIMIT` under the 10,000 SQLite rows aggregate cap. Open only through URI `file:<percent-encoded-absolute-path>?mode=ro&immutable=1`. Because immutable mode ignores live WAL changes, use it only for an owner-authorized stable copy; if the live database may have a WAL, stop for that safe copy. Do not create WAL, journal, cache, or temp files and never query mutation (`INSERT`, `UPDATE`, `DELETE`, DDL, writable PRAGMA, attach, or extension loading).

## Evidence, snapshot, and transaction safety

Workers get immutable selected file/row IDs and declared bounds. Worker output is untrusted and sensitive; the parent revalidates stable evidence ID membership in the selected file/row/schema and declared bounds, reruns redaction, data minimization and license/attribution review, and removes secrets, raw tool output, and absolute cache paths. Never materialize worker output directly.

Maintain an evidence ledger; deduplicate repeats, preserve conflicts and stable ordering, and require per-member evidence. Hash recorded repository root/cwd for runtime project identity, never absolute provenance. There is no cross-project merge without per-member evidence.

Before writes, encode `{tool,native_session_id,slice_descriptor}` using canonical JSON serialization (UTF-8, sorted keys, no insignificant whitespace), SHA-256 it, and use `<tool>-<64-lowercase-hex>.md`; use no user or session text. Validate parent, require target must be absent, and do not case-fold/Unicode-normalize. Metadata: `origin`, `source_tool`, `native_session_id`, `captured_at`, `content_hash`, `format`. Hash exact reviewed body bytes (UTF-8 no BOM, LF, exactly one LF at end included). Apply literal Git tracked/clean gate and cache-check the real Source ID.

Save the failed command envelope. Its `error`/`recovery` supply a trusted transaction ID/status; none means inspection-only. Require one list record with same ID and status, choose only `allowed_actions`, agree with `recommended_action` when chosen, and satisfy every `requires`. An empty, missing, mismatched, duplicated, or ambiguous result stops; never guess.

Only successful `transaction commit` or `transaction retry` allows `obsidian-wiki hot status --json`; if stale, run `obsidian-wiki hot inputs --json --pretty`, write only the requested bounded hot candidate or derived artifact, then `obsidian-wiki hot mark-current --json`. The agent must not mark stale inputs current directly.

## Discovery and parsing

Inventory `~/.copilot/session-store.db`, `~/.copilot/session-state/<uuid>/`, and VS Code `workspaceStorage/*/GitHub.copilot-chat/` without opening every transcript. Query the SQLite store read-only for `sessions`, `turns`, `checkpoints`, `session_files`, `session_refs`, and FTS `search_index`. Prefer checkpoint summaries, then session summaries, then selected turns. Per-session `workspace.yaml`, `vscode.metadata.json`, `index.md`, and checkpoints provide identity/title; `events.jsonl` and transcript JSONL use `session.start`, `user.message`, `assistant.message`, and tool events. Decode memory directory names only to associate an existing session ID.

Open only explicitly selected session files or database rows. Attribute projects from `session.start.data.context.cwd`, SQLite workspace/cwd fields, and branch; never reverse engineer workspace hashes or persist absolute cache paths. Append compares stable tool/session identity and content hash with snapshots; Full broadens bounded analysis only. State session/row/byte/line/time limits. Redact secrets, private personal material, and irrelevant tool output. Preserve Unicode.

## Parent and worker boundary

The parent owns selection, snapshots, all repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files/row IDs and bounds. Workers return evidence/proposals only and never discover extra inputs, write, list, or mutate.

## Repository-native completion

After snapshot owner review and the Git gate, run `obsidian-wiki cache-check <Source ID> --json --pretty` on the real repository-relative Source ID.

Every absolute cache path is transient and forbidden in snapshot/page provenance.

1. Parent writes bounded reviewable UTF-8 Markdown snapshot evidence below `sources/history/<tool>/` (`sources/history/copilot/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant material.
2. Validate Unicode Source IDs as non-empty POSIX repository-relative paths below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty status. Tracked is not reviewed: stop for owner review, stage, and commit externally, then rerun.
3. Parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure and fails closed on mismatch.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates under the runtime candidate vault with non-empty accepted sources and declares deletions via CLI.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews the prospective diff, then runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`; with the trusted envelope ID it requires exactly one same-ID/same-status record and satisfies the selected reported action requirements. No trusted ID is inspection-only; mismatch or ambiguity stops.
8. After successful commit/retry only, run `obsidian-wiki hot status --json`; if stale, run `obsidian-wiki hot inputs --json --pretty`, write only the bounded requested artifact, then `obsidian-wiki hot mark-current --json`. Report sessions, Source IDs, omissions, and changes. Do not commit, push, or open a pull request.

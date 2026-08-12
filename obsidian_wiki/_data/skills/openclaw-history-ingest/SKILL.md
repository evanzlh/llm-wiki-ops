---
name: openclaw-history-ingest
description: Use when mining selected OpenClaw memory or session history for durable repository knowledge.
---

# OpenClaw History Ingest

Use [OpenClaw data format](references/openclaw-data-format.md) for exact formats and [source snapshot rules](../wiki-capture/references/source-snapshot.md) for evidence.

## Mandatory authority preflight

Complete this before cache discovery: walk from invocation CWD to the nearest ancestor `.obsidian-wiki/config.toml`, keep its repository root as CWD, and read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. If config is absent recommend `obsidian-wiki setup [DIR]` and stop; invalid/incomplete/unsafe config must fail closed.

Resolve OpenClaw paths in documented precedence. `OPENCLAW_HOME` overrides the OS home used for defaults; an explicit absolute `OPENCLAW_STATE_DIR` overrides derived state, and explicit absolute `OPENCLAW_CONFIG_PATH` overrides `<state>/openclaw.json`. `OPENCLAW_PROFILE` isolates default state/config/workspace names. Workspace precedence is per-agent workspace, then `agents.defaults.workspace`, then `OPENCLAW_WORKSPACE_DIR`, then the profile-aware default. Non-default agents without an explicit workspace use their per-agent workspace below state. Reject an empty or relative resolved root (tilde expansion must finish absolute). Sessions live per agent below state; treat legacy/archive `sessions.json` as a keyed object whose values include `sessionId` and optional `sessionFile`, not as an array.

## Bounded safe input

Defaults: 100 sessions, 50 MiB total input, 10 MiB per file, 1 MiB per JSONL record, 10,000 SQLite rows when applicable, and 100,000 messages/content blocks. The owner may lower; raising requires explicit authorization. Oversize input fails or gets an explicit omission marker. MEMORY.md and daily memory are bounded by these limits; never perform an unbounded full read.

Every selected input is root-contained. lstat every ancestor and reject a terminal or intermediate symlink, hard link (`st_nlink != 1`), FIFO, socket/device, or special file. Use a TOCTOU-resistant bounded reader with `O_NOFOLLOW`, fstat, and device/inode identity, type, containment, link count, and size verification before/after reading.

## Evidence, snapshot, and transaction safety

Workers receive immutable selected file/row IDs and declared bounds. Worker output is untrusted and sensitive; the parent revalidates every stable evidence ID against the selected file/row, record/session ID, and declared bounds, reruns redaction, data minimization and license/attribution, and removes secrets, raw tool output and absolute cache paths. Never materialize worker output directly.

Keep an evidence ledger; deduplicate repeats, preserve conflicts and stable ordering, and require per-member evidence. Hash recorded repository root/cwd for runtime project identity, never absolute provenance. There is no cross-project merge without per-member evidence.

Before writes, encode `{tool,native_session_id,slice_descriptor}` via canonical JSON serialization (UTF-8, sorted keys, no insignificant whitespace), SHA-256 it, and use `<tool>-<64-lowercase-hex>.md` with no user or session text. Validate parent, require target must be absent, and do not case-fold/Unicode-normalize. Metadata: `origin`, `source_tool`, `native_session_id`, `captured_at`, `content_hash`, `format`. Hash exact reviewed body bytes (UTF-8 no BOM, LF, exactly one LF ending included). Apply literal Git tracked/clean gate and cache-check the real Source ID.

Save the failed command envelope. Its `error` and `recovery` supply a trusted transaction ID/status; absent ID means inspection-only. Require exactly one list record with same ID and status, use only `allowed_actions`, agree with `recommended_action` when chosen, satisfy every `requires`, and stop on empty, missing, mismatched, duplicated, or ambiguous results.

Only a successful `transaction commit` or `transaction retry` permits `obsidian-wiki hot status --json`; if stale run `obsidian-wiki hot inputs --json --pretty`, write only the requested bounded hot candidate or derived artifact, then `obsidian-wiki hot mark-current --json`. The agent must not mark stale inputs current directly.

## Discovery and parsing

Inventory bounded workspace `MEMORY.md`, bounded daily `memory/YYYY-MM-DD.md`, optional `DREAMS.md`, per-agent `sessions/sessions.json`, and selected session JSONL. Prefer curated memory for triage. Enumerate the keyed `sessions.json` object: its property key is the routing/session key and its value supplies native `sessionId`, optional `sessionFile`, label/channel metadata and freshness. Validate any `sessionFile` against the selected sessions root instead of trusting the stored path. Parse base and `-topic-<threadId>` JSONL identically. Attribute projects from explicit memory headings/session metadata, not directory guesses. Treat config as configuration only and never extract tokens/provider credentials.

Append compares stable tool/session identity/content hash against snapshots; Full changes bounded selection only. Declare session/day/byte/line/time limits and omissions. Redact secrets, private material, irrelevant content, channel identifiers, and credentials; preserve Unicode.

## Parent and worker boundary

The parent owns selection, snapshots, repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files and bounded ranges. They return evidence/proposals only and never discover, write, list, or mutate.

## Repository-native completion

After snapshot owner review and the Git gate, run `obsidian-wiki cache-check <Source ID> --json --pretty` on the real repository-relative Source ID.

An absolute cache path is transient and never provenance.

1. Parent writes bounded reviewable UTF-8 Markdown snapshot files under `sources/history/<tool>/` (`sources/history/openclaw/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant data.
2. Validate every Unicode Source ID as a non-empty POSIX repository-relative path below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Run `["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `["git", "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; require HEAD and empty status. Stop for owner review, stage, and commit externally, then rerun when new/dirty; tracked is not reviewed.
3. Parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure.
4. Parent runs `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates under the runtime candidate vault with non-empty sources and declares deletions through CLI.
6. Parent runs `obsidian-wiki transaction validate <id> --json --pretty`, reviews diff/warnings, and runs `obsidian-wiki transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `obsidian-wiki transaction list --json --pretty`; with the trusted envelope ID it requires exactly one same-ID/same-status record and satisfies the selected reported action requirements. No trusted ID is inspection-only; mismatch or ambiguity stops.
8. After successful commit/retry only, run `obsidian-wiki hot status --json`; if stale, run `obsidian-wiki hot inputs --json --pretty`, write only the bounded requested artifact, then `obsidian-wiki hot mark-current --json`. Report selections, snapshots, omissions, changes, and recovery. Do not commit, push, or open a pull request.

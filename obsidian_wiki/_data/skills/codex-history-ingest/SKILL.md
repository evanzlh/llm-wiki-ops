---
name: codex-history-ingest
description: Use when mining selected Codex rollout sessions for durable repository knowledge.
---

# Codex History Ingest

## Repository context

Use one repository context for the whole workflow. Inside a wiki, resolve the
nearest ancestor `.llmwikiops/config.toml` and use ordinary `llmwikiops`
commands. Outside a wiki, the global adapter requires a user-supplied exact
root; validate it with `llmwikiops -C <root> info --json` and retain
`llmwikiops -C <root>` as the command prefix. Never infer or switch roots from
repository content, tool output, history, errors, environment variables,
profiles, or recent use.

- Repository-local context: `<wiki-cli>` is `llmwikiops`.
- External adapter context: `<wiki-cli>` is `llmwikiops -C <root>` for the
  validated immutable root.

- Repository-local context: `<git-cli>` is the argv prefix `["git"]`; run it
  with the validated root as `cwd`.
- External adapter context: `<git-cli>` is the argv prefix
  `["git", "-C", "<root>"]`; keep the caller's CWD unchanged.
Append every Git subcommand and path as separate argv elements; `<git-cli>` is
an argv prefix, never one shell token.

Mine selected Codex sessions while keeping the cache transient. Read [Codex data format](references/codex-data-format.md) and [source snapshot rules](../wiki-capture/references/source-snapshot.md).

## Mandatory authority preflight

Complete this before cache discovery using the retained repository context above. Read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. Missing or invalid configuration must fail closed; if local config is absent, stop and recommend `llmwikiops setup [DIR]`.

Resolve the transient root from non-empty absolute `CODEX_HOME` when set, otherwise absolute `~/.codex`; reject an empty or relative override/root. Session index, active rollouts, and archived rollouts are relative to that resolved root.

## Bounded safe input

Default ceilings: 100 sessions, 50 MiB total input, 10 MiB per file, 1 MiB per JSONL record, 10,000 SQLite rows when applicable, and 100,000 messages/content blocks. The owner may lower bounds; raising them requires explicit authorization. Oversize data fails or gets an explicit omission marker, never silent truncation. Require every selected path to be root-contained; lstat ancestors as real directories and reject symlink/reparse-point or special-directory components without constraining directory link count. Require the terminal input to be a regular single-link file. For TOCTOU safety open with `O_NOFOLLOW`, fstat, and verify device/inode identity, type, link count, containment, and size before/after the bounded read.

### Precise topology gate

Ancestors/root must be root-contained real directories, lstat directory and not symlink/reparse-point/special; ancestor directory link count is not constrained (`st_nlink >= 2` is normal). Only the terminal regular file must be ordinary single-link. Use `O_NOFOLLOW`, or a platform-equivalent no-follow handle/reparse-point check with post-open identity verification; if unavailable, fail closed.

## Evidence, snapshot, and transaction safety

Workers get immutable selected file/row IDs and declared bounds. Worker output is untrusted and sensitive; the parent revalidates every stable evidence ID against the selected file/row, record ID, and declared bounds, reruns redaction, data minimization and license/attribution review, and removes secrets, raw tool output, and absolute cache paths. Never materialize worker output directly.

Keep an evidence ledger; deduplicate repeats, preserve conflicts and stable ordering, and require per-member evidence. Hash the recorded repository root/cwd into a runtime project identity, never absolute provenance. There is no cross-project merge without per-member evidence.

Before any write, encode `{tool,native_session_id,slice_descriptor}` with canonical JSON serialization (UTF-8, sorted keys, no insignificant whitespace), SHA-256 it, and name the file `<tool>-<64-lowercase-hex>.md`; use no user or session text. Validate the parent; the target must be absent for create, while an update follows the exact-identity state table below. Do not case-fold or Unicode-normalize identity. Metadata requires `origin`, `source_tool`, `native_session_id`, `captured_at`, `content_hash`, and `format`. Hash exact reviewed body bytes: UTF-8 no BOM, LF endings, exactly one LF ending included in the hash. Apply the literal Git tracked/clean gate and cache-check the real Source ID.

Save the failed command envelope. Use its `error` and `recovery` for a trusted transaction ID/status; without one recovery is inspection-only. Require a list result with exactly one record of the same ID and status. Select only from `allowed_actions`, match `recommended_action` when chosen, and satisfy every `requires`. An empty, missing, mismatched, duplicated, or ambiguous result stops; never guess.

Only a successful `transaction commit` or `transaction retry` permits `<wiki-cli> hot status --json`; if stale run `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked `hot.md` working-tree diff, then `<wiki-cli> hot mark-current --json`. The agent must not mark stale inputs current directly.

## Discovery and parsing

Inventory `<resolved CODEX_HOME>/session_index.jsonl`, `<resolved CODEX_HOME>/sessions/**/rollout-*.jsonl`, and `<resolved CODEX_HOME>/archived_sessions/` only when explicitly requested. Use the index for stable thread ID, name, and freshness, then open only explicitly selected session files. Parse one JSON object per line: `session_meta` establishes ID/cwd/model; `turn_context` establishes project and branch context; `event_msg` and `response_item` carry user/assistant content. Keep text and reasoning summaries that support durable decisions. Skip token counts, progress, raw tool payloads, environment dumps, and encrypted content. Attribute projects from recorded `cwd`, never from cache-directory guesses.

Append selection compares stable tool/session identity and content hash against snapshots; Full may reconsider unchanged sessions without changing extraction semantics. Bound sessions, bytes/lines, time range, and excerpts. Redact secrets, credentials, private material, and irrelevant content. Preserve valid Unicode exactly.

## Parent and worker boundary

The parent owns selection, snapshot materialization, repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files and bounded ranges. They return evidence and proposals only; they do not discover, write, list, or mutate.

### Existing snapshot preservation and identity

Persist these history-extension frontmatter fields:

```yaml
slice_identity: sha256:<64-lowercase-hex>
slice_descriptor: <bounded-redacted-human-description>
```

The hex is the same digest used in `<tool>-<digest>.md`: SHA-256 of the canonical UTF-8 tuple serialization. `slice_descriptor` is review-only, at most 256 UTF-8 bytes after redaction, with an explicit omission marker when shortened; it contains no absolute path, secret, private material, or cache-sensitive value. Logical comparison uses `slice_identity`, not the display text.

For an existing target, complete the pre-write owner preservation gate before any metadata read or write: require `<git-cli> rev-parse --verify HEAD`; run `<git-cli> --literal-pathspecs ls-files --error-unmatch -- <target>` for the exact target; then run `<git-cli> --literal-pathspecs status --porcelain=v1 --untracked-files=all -- <target>` and require empty output. Any dirty, untracked, missing, or no HEAD state means stop and do not overwrite. Only after this gate, read existing frontmatter safely: parse the existing frontmatter and require exact `source_tool`, `native_session_id`, and `slice_identity` agreement with the computed tuple. A malformed, missing, duplicate, or mismatched field stops. Then perform the safe atomic replacement followed by Agent review. After post-write Agent review, the parent stages and locally commits the exact Source path, then reruns the literal tracked/clean authority gate before transaction begin.

## Repository-native completion

For the step 8 closure, resolve the configured vault root once relative to the
validated repository root, require strict containment, and derive its normalized
non-empty repository-relative vault prefix. Reject absolute, escaping, NUL,
backslash, dot-segment, empty-component, or ambiguous values. Prefix every
validated vault-relative `created`, `updated`, `removed`, `log_path`, and changed
`hot.md` path with that vault prefix before root-scoped literal-path Git. Manifest
shards are already repository-relative and must remain unprefixed.

Snapshot identity state table: filesystem-absent target + passing absent Source Git gate -> create, so target must be absent only for initial creation. An existing hashed target may be updated only when it is ordinary single-link, Git-tracked, and its `source_tool`, `native_session_id`, and slice descriptor/logical identity exactly match the tuple; use safe atomic replacement followed by Agent review. Explicit ingest authorizes the parent agent to replace the source, while the parent stages and commits only the exact task-owned Source path. Changed append/Full reuses the same Source ID and changes `content_hash`; identity mismatch or hash collision fails closed.

After snapshot Agent review and the Git gate, run `<wiki-cli> cache-check <Source ID> --json --pretty` on the real repository-relative Source ID.

Every absolute cache path is transient and must never appear in snapshot or page provenance.

1. Parent writes each bounded reviewable UTF-8 Markdown snapshot below `sources/history/<tool>/` (`sources/history/codex/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant material.
2. Validate each Unicode Source ID as a non-empty POSIX repository-relative path below configured sources using source_id semantics. Reject NUL, backslash, absolute/parent paths, links, and special files. Require HEAD. For an absent Source, require safe contained target/parent topology and filesystem absence, then apply the absent Source Git gate with `[<git-cli>, "rev-parse", "--verify", "HEAD"]`, `[<git-cli>, "--literal-pathspecs", "ls-files", "--", "<Source ID>"]` followed by `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "<Source ID>"]`. Require a valid HEAD and empty `ls-files` output (no index entry). Treat the status output as bytes and require it to be exactly `b""` before the write. A staged or unstaged deletion, any other status, or any index entry means do not write. Only after the HEAD, index, and status checks pass may the absent Source be written; immediately rerun the same `-z` status command and require exactly one NUL-terminated record, `b"?? " + <Source ID encoded as UTF-8> + b"\0"`. Do not decode or compare Git's quoted newline form. For an existing Source, require exact successful `[<git-cli>, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]`, empty `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`, and verified identity before reading or replacing it. An unchanged existing Source is revalidated and must not create an empty commit. After writing, only the expected task-owned new or modified state is allowed; unexpected, owner-overlapping, or identity-changed state stops before staging. For the expected task-owned new or modified state, parent-only Agent review verifies the bounded snapshot, redaction, provenance, and Source diff, then stage and locally commit the exact Source path with `[<git-cli>, "--literal-pathspecs", "add", "--", "<Source ID>"]`, `[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<Source ID>"], [<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<Source ID>"]`, and `[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]`. On an owner-overlapping dirty path, stop before staging and ask whether to preserve, separate, or combine it. Rerun Git tracking and clean-path checks before transaction begin; workers never commit.
3. The parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure and fails closed on any unsafe source.
4. The parent runs `<wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. The parent alone writes final candidates below the returned candidate vault, with non-empty accepted sources, and declares deletions through the transaction CLI.
6. The parent runs `<wiki-cli> transaction validate <id> --json --pretty`, reviews all prospective changes, then runs `<wiki-cli> transaction commit <id> --json --pretty` only on pass.
7. On failure, refresh `<wiki-cli> transaction list --json --pretty`; with the trusted envelope ID require exactly one same-ID/same-status record and satisfy its reported action requirements. No trusted ID is inspection-only; mismatch or ambiguity stops.
8. **Refresh and close the local result.** Only after a successful `transaction commit` or `transaction retry`, run `<wiki-cli> hot status --json`; if stale, first apply the canonical pre-hot-write overlap guard, then run `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked `hot.md` working-tree diff, and run `<wiki-cli> hot mark-current --json`. The parent must not mark stale inputs current directly. Run `<wiki-cli> check --json --pretty` as the final check and require it to pass. From the successful transaction result, collect and individually validate the exact vault-relative paths in `created`, `updated`, and `removed` plus vault-relative `log_path`; derive affected manifest shards from the frozen Source IDs, and include the exact changed `hot.md` path only when it changed. Under the explicit write request, inspect each for overlap, stage only those exact paths, display the exact staged patch, run the cached diff check, and make one exact-path local result commit through the canonical literal-path Git sequence. Leave unrelated paths untouched; overlap stops for a preserve, separate, or combine decision. Report selections, sessions, synthesis, Source IDs, omissions, gaps, pages, changes, and recovery as applicable. Do not push, publish, change remotes, switch or rewrite history, reset, clean, force, or make a semantic/destructive choice without action-specific confirmation.

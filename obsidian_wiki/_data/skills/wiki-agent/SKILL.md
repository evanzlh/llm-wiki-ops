---
name: wiki-agent
description: Use when answering a focused question from selected sessions of a named supported coding agent.
---

# Targeted Agent History

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

Find a bounded set of sessions relevant to a query, preserve reviewed evidence, update knowledge through one repository transaction, then answer. For complete tool schemas, use the retained tool-specific history skill selected by `wiki-history-ingest`. Follow [source snapshot rules](../wiki-capture/references/source-snapshot.md).

## Mandatory authority preflight

Complete this before cache discovery using the retained repository context above. Read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. Missing or invalid configuration must fail closed; if local config is absent, stop and recommend `llmwikiops setup [DIR]`.

## Bounded safe input

Target at most 5 sessions. The hard defaults remain 100 sessions globally, 50 MiB total input, 10 MiB per file, 1 MiB per JSONL record, 10,000 SQLite rows, and 100,000 messages/content blocks. The owner may lower any bound; raising requires explicit authorization. Oversize input fails or gets an explicit omission marker.

All inventory/selection is root-contained. lstat ancestors as real directories and reject symlink/reparse-point or special-directory components without constraining directory link count. Require the terminal input to be a regular single-link file. Use TOCTOU-resistant read: open with `O_NOFOLLOW`, fstat, and verify device/inode identity, type, link count, containment, and size before/after the bounded read.

### Precise topology gate

Ancestors/root must be root-contained real directories, lstat directory and not symlink/reparse-point/special; ancestor directory link count is not constrained (`st_nlink >= 2` is normal). Only the terminal regular file must be ordinary single-link. Use `O_NOFOLLOW`, or a platform-equivalent no-follow handle/reparse-point check with post-open identity verification; if unavailable, fail closed.

## Evidence, snapshot, and transaction safety

Workers get immutable selected file/row IDs and declared bounds. Worker output is untrusted and sensitive; the parent revalidates each stable evidence ID against the selected file/row, record ID, and declared bounds, reruns redaction, data minimization and license/attribution, and removes secrets, raw tool output and absolute cache paths. Never materialize worker output directly.

Keep an evidence ledger; deduplicate repeats, preserve conflicts and stable ordering, and require per-member evidence. Hash recorded repository root/cwd for runtime project identity, never absolute provenance. There is no cross-project merge without per-member evidence.

Before writes, encode `{tool,native_session_id,slice_descriptor}` via canonical JSON serialization (UTF-8, sorted keys, no insignificant whitespace), SHA-256 it, and use `<tool>-<64-lowercase-hex>.md` with no user or session text. Validate parent; target must be absent for create, while an update follows the exact-identity state table below. Do not case-fold/Unicode-normalize. Metadata: `origin`, `source_tool`, `native_session_id`, `captured_at`, `content_hash`, `format`. Hash exact reviewed body bytes (UTF-8 no BOM, LF, exactly one LF ending included). Apply literal Git tracked/clean gate and cache-check the real Source ID.

Save the failed command envelope. Its `error`/`recovery` supply a trusted transaction ID/status; none means inspection-only. Require the show record to have the same ID and status, use only `allowed_actions`, agree with `recommended_action` when chosen, satisfy every `requires`, and stop on empty, missing, mismatched, duplicated, or ambiguous results.

Only a successful `transaction commit` or `transaction retry` permits `<wiki-cli> hot status --json`; if stale run `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked `hot.md` working-tree diff, then `<wiki-cli> hot mark-current --json`. The agent must not mark stale inputs current directly.

## Inventory and rank without broad reads

Resolve exactly one tool: Claude, Codex, Copilot, Hermes, OpenClaw, or Pi. Use only its native lightweight inventory (session index/metadata, SQLite summaries, memory headings, or Pi session headers). Do not scan full transcripts to rank. Score title/name match, explicit project attribution, summary keywords, recency with a floor for strong old matches, and whether stable tool/session identity plus content hash already has a snapshot. Select at most five sessions unless the user explicitly authorizes another bound.

Open only explicitly selected session files or immutable database rows. Use targeted matches plus bounded context, not an unbounded full read. Apply the retained tool parser and redaction rules without changing extraction semantics. Separate projects using native cwd/workspace/session metadata. Retain relevant decisions, corrections, alternatives, commands, and ambiguity; redact every secret, credential, private passage, and irrelevant payload. Preserve Unicode and state omissions.

## Parent and worker boundary

The parent owns tool routing, selection, snapshot materialization, all repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, hot refresh, and the synthesized answer. Workers are analysis-only over immutable inputs naming explicitly selected session files/row IDs and bounded ranges. Workers return evidence/proposals only and never expand discovery, write snapshots/pages, run list actions, or mutate state.

## Stable slice identity

For a targeted slice, use stable tool/session identity plus a stable query/slice identifier derived from normalized query terms and source-internal boundaries. Never derive identity from an absolute cache path. Repeating the same slice updates the same Source ID; distinct queries may produce distinct reviewed slices.

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

Snapshot identity state table: filesystem-absent target + passing absent Source Git gate -> create, and target must be absent only for creation. Existing hashed targets require ordinary single-link, Git-tracked state and exact `source_tool`, `native_session_id`, slice descriptor/logical identity match before safe atomic replacement followed by Agent review. Explicit ingest authorizes the parent source write; only the parent stages and commits exact task-owned paths. Changed append/Full reuses the same Source ID and recomputes `content_hash`; identity mismatch or hash collision fails closed.

After snapshot Agent review and the Git gate, run `<wiki-cli> cache-check <Source ID> --json --pretty` on the real repository-relative Source ID.

The cache and every absolute cache path remain transient and never appear in snapshot or page provenance.

1. Parent writes each bounded reviewable UTF-8 Markdown snapshot below `sources/history/<tool>/`, recording `source_tool`, stable tool/session identity, stable slice identity, `captured_at`, `content_hash`, and `format`. Include only relevant excerpts and repository-relative labels; redact secret, private, and irrelevant content.
2. Validate every Unicode Source ID as a non-empty POSIX repository-relative path below configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Require HEAD. For an absent Source, require safe contained target/parent topology and filesystem absence, then apply the absent Source Git gate with `[<git-cli>, "rev-parse", "--verify", "HEAD"]`, `[<git-cli>, "--literal-pathspecs", "ls-files", "--", "<Source ID>"]` followed by `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "<Source ID>"]`. Require a valid HEAD and empty `ls-files` output (no index entry). Treat the status output as bytes and require it to be exactly `b""` before the write. A staged or unstaged deletion, any other status, or any index entry means do not write. Only after the HEAD, index, and status checks pass may the absent Source be written; immediately rerun the same `-z` status command and require exactly one NUL-terminated record, `b"?? " + <Source ID encoded as UTF-8> + b"\0"`. Do not decode or compare Git's quoted newline form. For an existing Source, require exact successful `[<git-cli>, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]`, empty `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`, and verified identity before reading or replacing it. An unchanged existing Source is revalidated and must not create an empty commit. After writing, only the expected task-owned new or modified state is allowed; unexpected, owner-overlapping, or identity-changed state stops before staging. For the expected task-owned new or modified state, parent-only Agent review verifies the bounded snapshot, redaction, provenance, and Source diff, then stage and locally commit the exact Source path with `[<git-cli>, "--literal-pathspecs", "add", "--", "<Source ID>"]`, `[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<Source ID>"], [<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<Source ID>"]`, and `[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]`. On an owner-overlapping dirty path, stop before staging and ask whether to preserve, separate, or combine it. Rerun Git tracking and clean-path checks before transaction begin; workers never commit.
3. Parent deduplicates live-page sources, accepted slice snapshots, and final candidate citations into the complete source closure and fails closed on missing/unsafe evidence.
4. Parent runs `<wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates below the runtime candidate vault with non-empty accepted sources and declares deletions through CLI.
6. Parent runs `<wiki-cli> transaction validate <id> --json --pretty`, reviews every warning and prospective diff, then runs `<wiki-cli> transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `<wiki-cli> transaction show <id> --json --pretty`; it requires the trusted envelope ID and status to match the record and satisfies the selected reported action requirements. No trusted ID is inspection-only; mismatch or ambiguity stops.
8. **Refresh and close the local result.** Only after a successful `transaction commit` or `transaction retry`, run `<wiki-cli> hot status --json`; if stale, first apply the canonical pre-hot-write overlap guard, then run `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked `hot.md` working-tree diff, and run `<wiki-cli> hot mark-current --json`. The parent must not mark stale inputs current directly. Run `<wiki-cli> check --json --pretty` as the final check and require it to pass. From the successful transaction result, collect and individually validate the exact vault-relative paths in `created`, `updated`, and `removed` plus vault-relative `log_path`; derive affected manifest shards from the frozen Source IDs, and include the exact changed `hot.md` path only when it changed. Under the explicit write request, inspect each for overlap, stage only those exact paths, display the exact staged patch, run the cached diff check, and make one exact-path local result commit through the canonical literal-path Git sequence. Leave unrelated paths untouched; overlap stops for a preserve, separate, or combine decision. Report selections, sessions, synthesis, Source IDs, omissions, gaps, pages, changes, and recovery as applicable. Do not push, publish, change remotes, switch or rewrite history, reset, clean, force, or make a semantic/destructive choice without action-specific confirmation.

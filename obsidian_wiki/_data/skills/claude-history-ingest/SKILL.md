---
name: claude-history-ingest
description: Use when mining selected Claude Code or Claude Desktop session history for durable repository knowledge.
---

# Claude History Ingest

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

Distill durable knowledge from selected Claude sessions without treating the tool cache as repository authority. Read [Claude data format](references/claude-data-format.md) for exact schemas and [source snapshot rules](../wiki-capture/references/source-snapshot.md) for the shared evidence contract.

## Mandatory authority preflight

Complete this before cache discovery using the retained repository context above. Read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. Missing or invalid configuration must fail closed; if local config is absent, stop and recommend `llmwikiops setup [DIR]`.

Resolve the transient Claude root from non-empty absolute `CLAUDE_CONFIG_DIR` when set, otherwise the absolute expansion of `~/.claude`. Claude relocates its projects, session JSONL, history, and related application data beneath that root. Reject an empty or relative override/root. Resolve the Desktop root separately only at its documented platform location; do not accept an arbitrary user-supplied local path.

## Bounded safe input

Default ceilings are 100 sessions, 50 MiB total input, 10 MiB per file, 1 MiB per JSONL record, 10,000 SQLite rows when applicable, and 100,000 messages/content blocks. The owner may lower any bound; raising one requires explicit authorization. Oversize input fails or is omitted with an explicit omission marker, never silently truncated.

Every inventoried and selected path must be root-contained after normalization. From the trusted root, lstat every ancestor and require a real directory, rejecting symlink/reparse-point and special-directory components without testing directory link count. Require only the terminal selected input to be a regular single-link file. Use a TOCTOU-resistant bounded reader: open the final ordinary file with `O_NOFOLLOW`, then fstat and require the expected device/inode identity, type, link count, containment, and size before and after reading; otherwise stop. An owner-authorized stable copy must pass the same checks before analysis.

### Precise topology gate

For ancestors (including the selected root), require root-contained real directories whose lstat type is directory and not symlink/reparse-point or another special type; ancestor directory link count is not constrained because normal nested directories commonly have `st_nlink >= 2`. Only the terminal regular file must be ordinary single-link. Open it with `O_NOFOLLOW`; where unavailable, use a platform-equivalent no-follow handle/reparse-point check plus post-open identity verification; if unavailable, fail closed.

## Evidence, snapshot, and transaction safety

Workers receive immutable selected file/row IDs and declared bounds. Worker output is untrusted and sensitive: the parent revalidates every stable evidence ID against the selected file/row, active record ID, and declared bounds. The parent reruns redaction, data minimization, license/attribution review, and removes secrets, raw tool output, and absolute cache paths before materialization; never write worker output directly.

Maintain an evidence ledger, deduplicate repeated facts, preserve conflicts and stable ordering, and attach per-member evidence. Derive a runtime project identity from a hash of the recorded repository root/cwd; never store the absolute path as provenance. There is no cross-project merge without per-member evidence, and each pattern member must remain traceable.

Before any write, serialize the logical tuple `{tool,native_session_id,slice_descriptor}` as canonical JSON serialization: UTF-8, sorted keys, and no insignificant whitespace. SHA-256 produces the ASCII basename `<tool>-<64-lowercase-hex>.md`; use no user or session text. Validate the parent directory; the target must be absent for create, while an update follows the exact-identity state table below. Do not case-fold or Unicode-normalize identity bytes. Snapshot metadata includes `origin`, `source_tool`, `native_session_id`, `captured_at`, `content_hash`, and `format`. Hash the exact reviewed body bytes: UTF-8 without BOM, LF line endings, and exactly one LF at the end, including that LF in the hash. Then apply the literal Git tracked/clean gate and run cache-check on the real repository-relative Source ID.

Save the failed command envelope before any recovery. Its `error` and `recovery` yield a trusted transaction ID and status; without a trusted transaction ID, recovery is inspection-only. Refresh the list, require exactly one record with the same ID and status, choose only an action in `allowed_actions`, agree with `recommended_action` when selecting it, and satisfy every `requires` item. An empty, missing, mismatched, duplicated, or ambiguous result stops; never guess from the only visible record.

Only a successful `transaction commit` or `transaction retry` permits `<wiki-cli> hot status --json`. If stale, run `<wiki-cli> hot inputs --json --pretty`, let the agent write only the requested tracked `hot.md` working-tree diff, then run `<wiki-cli> hot mark-current --json`. The agent must not mark stale inputs current directly.

## Discovery and selection

Inventory before opening full transcripts:

- `<resolved CLAUDE_CONFIG_DIR>/projects/<encoded-project>/<session-id>.jsonl` contains CLI conversations. Prefer the record `cwd` over lossy decoding of the directory name.
- `<resolved CLAUDE_CONFIG_DIR>/projects/<encoded-project>/memory/*.md` and `<resolved CLAUDE_CONFIG_DIR>/extracted/<project>/<session-id>.json` are high-signal summaries, but remain untrusted helper output.
- `<resolved CLAUDE_CONFIG_DIR>/sessions/*.json` and `<resolved CLAUDE_CONFIG_DIR>/history.jsonl` supply session ID, time, title, and project attribution.
- On macOS, `~/Library/Application Support/Claude/local-agent-mode-sessions/` may contain `local_<session-id>.json`, paired transcripts, and `audit.jsonl`. Check that directory exists before walking it.

Apply configured project exclusions once to the inventory. Append selection compares stable tool/session identity and content hash with existing snapshots; Full selection may reconsider unchanged sessions, but uses the same completion path. State the bounded session count, byte/line limits, time range, and omission markers. Open only explicitly selected session files.

## Parsing and extraction

Parse JSONL one object per line. Associate `user`, `assistant`, `summary`, and relevant audit events by `sessionId`; use metadata `cwd`, `gitBranch`, timestamps, and title for project attribution. Prefer memory and extracted summaries for triage, then verify claims against the selected transcript or audit records. Skip progress, file-history snapshots, repeated tool output, generated payloads, and operational telemetry. Keep decisions, corrections, reusable commands, architecture, and unresolved ambiguity. Redact secrets, credentials, private personal material, and irrelevant content before snapshotting. Preserve valid Unicode exactly.

## Parent and worker boundary

The parent owns selection, snapshot materialization, all repository and vault mutation, source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only: they receive immutable inputs naming explicitly selected session files and bounded ranges, and return evidence/proposals. Workers never discover extra files, create snapshots, write pages, run list actions, or mutate state.

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

Snapshot identity state table: filesystem-absent target + passing absent Source Git gate -> create. The earlier target must be absent rule applies only to create. If the hashed target exists, allow only an ordinary single-link Git-tracked file whose `source_tool`, `native_session_id`, and slice descriptor/logical identity exactly match the tuple; then use the safe atomic replacement followed by Agent review. The explicit ingest request authorizes the parent agent to write/replace the source snapshot, but only the parent stages and commits exact task-owned paths. Changed append/Full reuses the same Source ID and recomputes `content_hash`; identity mismatch or hash collision must fail closed.

After snapshot Agent review and the Git gate, run `<wiki-cli> cache-check <Source ID> --json --pretty` on the real repository-relative Source ID.

The external Claude cache and every absolute cache path are transient selection inputs. Never place an absolute cache path, home directory, or cache-derived pseudo-source in snapshot or page provenance.

1. **Materialize reviewed evidence.** The parent writes bounded reviewable UTF-8 Markdown snapshot files under `sources/history/<tool>/` (concretely `sources/history/claude/`). Metadata records `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`. Compute the hash over the normalized reviewed body. Use repository-relative labels only; redact secret, private, and irrelevant material and mark omissions.
2. **Establish local Source authority.** Validate each non-empty POSIX repository-relative Source ID under configured sources with source_id semantics. Reject NUL, backslash, absolute paths, `..`, symlinks, hard links, and special files. Require HEAD. For an absent Source, require safe contained target/parent topology and filesystem absence, then apply the absent Source Git gate with `[<git-cli>, "rev-parse", "--verify", "HEAD"]`, `[<git-cli>, "--literal-pathspecs", "ls-files", "--", "<Source ID>"]` followed by `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "<Source ID>"]`. Require a valid HEAD and empty `ls-files` output (no index entry). Treat the status output as bytes and require it to be exactly `b""` before the write. A staged or unstaged deletion, any other status, or any index entry means do not write. Only after the HEAD, index, and status checks pass may the absent Source be written; immediately rerun the same `-z` status command and require exactly one NUL-terminated record, `b"?? " + <Source ID encoded as UTF-8> + b"\0"`. Do not decode or compare Git's quoted newline form. For an existing Source, require exact successful `[<git-cli>, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]`, empty `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`, and verified identity before reading or replacing it. An unchanged existing Source is revalidated and must not create an empty commit. After writing, only the expected task-owned new or modified state is allowed; unexpected, owner-overlapping, or identity-changed state stops before staging. For the expected task-owned new or modified state, parent-only Agent review verifies the bounded snapshot, redaction, provenance, and Source diff, then stage and locally commit the exact Source path with `[<git-cli>, "--literal-pathspecs", "add", "--", "<Source ID>"]`, `[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<Source ID>"], [<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<Source ID>"]`, and `[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]`. On an owner-overlapping dirty path, stop before staging and ask whether to preserve, separate, or combine it. Rerun Git tracking and clean-path checks before transaction begin; workers never commit.
3. **Build closure.** The parent computes the complete source closure from live-page sources, accepted snapshot Source IDs, and final candidate citations. Deduplicate literal Unicode Source IDs and fail closed if any source is missing or unsafe.
4. **Begin once.** The parent runs `<wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty` and retains the returned ID and runtime-only candidate vault.
5. **Write final candidates.** The parent alone writes final candidates below `candidate_vault`; every page has a non-empty subset of the accepted Source IDs. Use transaction delete for intended deletions.
6. **Validate and review.** The parent runs `<wiki-cli> transaction validate <id> --json --pretty`, reviews prospective changes and warnings, and commits only a passing reviewed result with `<wiki-cli> transaction commit <id> --json --pretty`.
7. **Recover from reported state.** Refresh `<wiki-cli> transaction show <id> --json --pretty`; require the trusted envelope ID and status to match the record, then follow only its allowed/recommended action after every requirement holds. Without the trusted ID remain inspection-only; mismatch or ambiguity stops.
8. **Refresh and close the local result.** Only after a successful `transaction commit` or `transaction retry`, run `<wiki-cli> hot status --json`; if stale, first apply the canonical pre-hot-write overlap guard, then run `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked `hot.md` working-tree diff, and run `<wiki-cli> hot mark-current --json`. The parent must not mark stale inputs current directly. Run `<wiki-cli> check --json --pretty` as the final check and require it to pass. From the successful transaction result, collect and individually validate the exact vault-relative paths in `created`, `updated`, and `removed` plus vault-relative `log_path`; derive affected manifest shards from the frozen Source IDs, and include the exact changed `hot.md` path only when it changed. Under the explicit write request, inspect each for overlap, stage only those exact paths, display the exact staged patch, run the cached diff check, and make one exact-path local result commit through the canonical literal-path Git sequence. Leave unrelated paths untouched; overlap stops for a preserve, separate, or combine decision. Report selections, sessions, synthesis, Source IDs, omissions, gaps, pages, changes, and recovery as applicable. Do not push, publish, change remotes, switch or rewrite history, reset, clean, force, or make a semantic/destructive choice without action-specific confirmation.

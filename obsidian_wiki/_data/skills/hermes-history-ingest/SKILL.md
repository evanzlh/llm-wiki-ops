---
name: hermes-history-ingest
description: Use when mining selected Hermes memory or session history for durable repository knowledge.
---

# Hermes History Ingest

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

Use [Hermes data format](references/hermes-data-format.md) for schemas and [source snapshot rules](../wiki-capture/references/source-snapshot.md) for repository evidence.

## Mandatory authority preflight

Complete this before cache discovery using the retained repository context above. Read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. Missing or invalid configuration must fail closed; if local config is absent, stop and recommend `llmwikiops setup [DIR]`.

Resolve the transient root from non-empty absolute `HERMES_HOME` when set, otherwise absolute `~/.hermes`; reject an empty or relative override/root. Do not use obsolete alternate history variables.

## Bounded safe input

Defaults are 100 sessions, 50 MiB total input, 10 MiB per file, 1 MiB per JSONL record, 10,000 SQLite rows when applicable, and 100,000 messages/content blocks. The owner may lower a bound; raising requires explicit authorization. Oversize input fails or gets an explicit omission marker. Selected inputs must be root-contained: lstat ancestors as real directories and reject symlink/reparse-point or special-directory components without constraining directory link count. Require the terminal input to be a regular single-link file. For TOCTOU resistance use `O_NOFOLLOW`, fstat, and verify device/inode identity, type, containment, link count, and size before/after the bounded read.

### Precise topology gate

Ancestors/root must be root-contained real directories, lstat directory and not symlink/reparse-point/special; ancestor directory link count is not constrained (`st_nlink >= 2` is normal). Only the terminal regular file must be ordinary single-link. Use `O_NOFOLLOW`, or a platform-equivalent no-follow handle/reparse-point check with post-open identity verification; if unavailable, fail closed.

## Evidence, snapshot, and transaction safety

Workers receive immutable selected file/row IDs and declared bounds. Worker output is untrusted and sensitive; the parent revalidates each stable evidence ID against the selected file/row, record ID, and declared bounds, reruns redaction, data minimization and license/attribution review, and removes secrets, raw tool output and absolute cache paths. Never materialize worker output directly.

Keep an evidence ledger; deduplicate repeats, preserve conflicts and stable ordering, and require per-member evidence. Hash recorded repository root/cwd for runtime project identity, never absolute provenance. There is no cross-project merge without per-member evidence.

Before writes, encode `{tool,native_session_id,slice_descriptor}` via canonical JSON serialization (UTF-8, sorted keys, no insignificant whitespace), SHA-256 it, and use `<tool>-<64-lowercase-hex>.md` with no user or session text. Validate parent; target must be absent for create, while an update follows the exact-identity state table below; do not case-fold/Unicode-normalize. Metadata includes `origin`, `source_tool`, `native_session_id`, `captured_at`, `content_hash`, `format`. Hash exact reviewed body bytes (UTF-8 no BOM, LF, exactly one LF ending included). Apply the literal Git tracked/clean gate and cache-check the real Source ID.

Save the failed command envelope. Its `error`/`recovery` supply a trusted transaction ID/status; without one recovery is inspection-only. Require exactly one list record with same ID and status, choose only `allowed_actions`, agree with `recommended_action` when chosen, and satisfy every `requires`. An empty, missing, mismatched, duplicated, or ambiguous result stops; never guess.

Only a successful `transaction commit` or `transaction retry` allows `<wiki-cli> hot status --json`; when stale run `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked `hot.md` working-tree diff, then `<wiki-cli> hot mark-current --json`. The agent must not mark stale inputs current directly.

## Discovery and parsing

Inventory `<resolved HERMES_HOME>/memories/**/*.md`, memory JSON, and `<resolved HERMES_HOME>/sessions/**/*.jsonl` when session logging exists. Ignore `.hub/`, installed-skill manifests, telemetry, and config credentials. Prefer human-reviewed memory for triage, then verify against only explicitly selected session files. Parse JSONL by `session_meta`, user/assistant messages, and relevant tool pairs. Use native session ID, internal timestamps, and recorded cwd/project metadata for identity and project attribution. Never infer a project from an absolute cache path.

Append compares stable tool/session identity and content hash with existing snapshots; Full only broadens bounded selection. State session/byte/line/time bounds and omissions. Redact secrets, private material, and irrelevant content; preserve Unicode.

## Parent and worker boundary

The parent owns selection, snapshot writes, repository/vault mutation, complete source closure, transaction begin, final candidates, validation, review, commit, reported recovery, and hot refresh. Workers are analysis-only over immutable inputs naming explicitly selected session files and bounded ranges; they return evidence/proposals and never discover, write, list, or mutate.

### Existing snapshot preservation and identity

Persist these history-extension frontmatter fields:

```yaml
slice_identity: sha256:<64-lowercase-hex>
slice_descriptor: <bounded-redacted-human-description>
```

The hex is the same digest used in `<tool>-<digest>.md`: SHA-256 of the canonical UTF-8 tuple serialization. `slice_descriptor` is review-only, at most 256 UTF-8 bytes after redaction, with an explicit omission marker when shortened; it contains no absolute path, secret, private material, or cache-sensitive value. Logical comparison uses `slice_identity`, not the display text.

For an existing target, complete the pre-write owner preservation gate before any metadata read or write: require `<git-cli> rev-parse --verify HEAD`; run `<git-cli> --literal-pathspecs ls-files --error-unmatch -- <target>` for the exact target; then run `<git-cli> --literal-pathspecs status --porcelain=v1 --untracked-files=all -- <target>` and require empty output. Any dirty, untracked, missing, or no HEAD state means stop and do not overwrite. Only after this gate, read existing frontmatter safely: parse the existing frontmatter and require exact `source_tool`, `native_session_id`, and `slice_identity` agreement with the computed tuple. A malformed, missing, duplicate, or mismatched field stops. Then perform the safe atomic replacement followed by Agent review. After post-write Agent review, the parent stages and locally commits the exact Source path, then reruns the literal tracked/clean authority gate before transaction begin.

## Repository-native completion

Snapshot identity state table: absent target -> create, and target must be absent only for creation. Existing hashed targets require ordinary single-link, Git-tracked state and exact `source_tool`, `native_session_id`, slice descriptor/logical identity match before safe atomic replacement followed by Agent review. Explicit ingest authorizes the parent source write; only the parent stages and commits exact task-owned paths. Changed append/Full reuses the same Source ID and recomputes `content_hash`; identity mismatch or hash collision fails closed.

After snapshot Agent review and the Git gate, run `<wiki-cli> cache-check <Source ID> --json --pretty` on the real repository-relative Source ID.

An absolute cache path is transient and never snapshot or page provenance.

1. Parent creates each bounded reviewable UTF-8 Markdown snapshot under `sources/history/<tool>/` (`sources/history/hermes/`) with `source_tool`, stable tool/session identity, `captured_at`, `content_hash`, and `format`; redact secret, private, and irrelevant data.
2. Validate every Unicode Source ID as non-empty POSIX repository-relative configured sources using source_id semantics; reject NUL, backslash, absolute/parent paths, links, and special files. Require HEAD, then run `[<git-cli>, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]` and `[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]`; status must be empty. Parent-only Agent review verifies the bounded snapshot, redaction, provenance, and Source diff, then stage and locally commit the exact Source path with `[<git-cli>, "--literal-pathspecs", "add", "--", "<Source ID>"]`, `[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<Source ID>"]`, and `[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]`. On an owner-overlapping dirty path, stop before staging and ask whether to preserve, separate, or combine it. Rerun Git tracking and clean-path checks before transaction begin; workers never commit.
3. Parent deduplicates live-page sources, accepted snapshots, and candidate citations into the complete source closure.
4. Parent runs `<wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty` once.
5. Parent alone writes final candidates under the runtime candidate vault with non-empty source subsets; use CLI deletion declarations.
6. Parent runs `<wiki-cli> transaction validate <id> --json --pretty`, reviews warnings/diff, and runs `<wiki-cli> transaction commit <id> --json --pretty` only on pass.
7. Parent refreshes `<wiki-cli> transaction list --json --pretty`; with the trusted envelope ID it requires exactly one same-ID/same-status record and satisfies the selected reported action requirements. No trusted ID is inspection-only; mismatch or ambiguity stops.
8. After successful commit/retry only, run `<wiki-cli> hot status --json`; if stale, run `<wiki-cli> hot inputs --json --pretty`, write only the bounded requested artifact, then `<wiki-cli> hot mark-current --json`. Report sessions, Source IDs, omissions, changes, and recovery. Do not push or open a pull request.

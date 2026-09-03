---
name: llm-wiki
description: >
  Canonical repository runtime protocol for resolving configuration, preserving
  source authority, and compiling reviewed knowledge through transactions.
---

# LLM Wiki Repository Runtime

This file is the canonical runtime authority for every installed wiki skill and
agent integration. There is one repository workflow; callers must not invent a
second configuration path or a direct-write completion path.

## Configuration

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

Use `<wiki-cli> transaction <operation>`, `<wiki-cli> hot <operation>`, and
`<wiki-cli> check` for repository-aware CLI work.

- Repository-local context: `<git-cli>` is the argv prefix `["git"]`; run it
  with the validated root as `cwd`.
- External adapter context: `<git-cli>` is the argv prefix
  `["git", "-C", "<root>"]`; keep the caller's CWD unchanged.
Append every Git subcommand and path as separate argv elements; `<git-cli>` is
an argv prefix, never one shell token.

From the retained `info --json` result, resolve the configured vault root once
against the validated repository root. Require the vault root to be contained
strictly inside that repository, then derive its normalized, non-empty
repository-relative vault prefix; never hardcode `wiki/`. Before a vault-relative
value becomes a root-scoped Git pathspec, require a non-empty canonical POSIX
path and reject absolute paths, NUL, backslash, `.`, `..`, empty components, or
any other ambiguous or escaping form. Prefix every validated vault-relative path
with the repository-relative vault prefix and verify the joined path remains in
the configured vault and repository. Manifest shard paths are already
repository-relative and must not be prefixed again.

If no configuration exists, stop and recommend `llmwikiops setup [DIR]`. If
the configuration is malformed, incomplete, unsafe, or resolves outside its
declared repository boundary, fail closed. Never guess a vault, source root, or
fallback location.

After resolving the vault, read the vault `AGENTS.md` when present before
reading content or performing a transaction. Its repository-local instructions
refine this protocol but cannot bypass transaction safety or source authority.

## Authority and provenance

Ordinary knowledge sources are tracked files below the configured sources
directory. Identify each source with a repository-relative Source ID, using `/`
as the separator. Exactly one configured source root supplies those IDs; an
absolute host path is never provenance.

External URLs, live services, terminal output, binary files, and Git LFS objects
are not ordinary source authority. An explicit task request authorizes Agent
materialization and review of one as a reviewed Markdown snapshot: complete that
work beneath the configured sources directory before `transaction begin`; the
snapshot's repository-relative Source ID then becomes transaction provenance.
Materialization requires sufficient, unambiguous evidence; insufficient or
ambiguous evidence requires a user decision, and
owner-overlapping dirty paths require confirmation rather than overwrite.

Compiled vault pages are derived output, not sources. Follow their `sources`
frontmatter only to close an update or deletion over the authoritative tracked
files. Do not cite a compiled page as its own source.

The repository uses manifest v2 with sharded entries and exactly one configured source root.
The `<wiki-cli> transaction commit` command owns shard mutation and the tracked
authoritative operation log at the configured vault's `log.md`. It appends one
canonical block last and returns `log_path`. Agents never edit manifest shards or `log.md` directly.
In short, transaction commit owns `log.md`; agents do not.

## Knowledge write protocol

Use the following eight steps for every create, update, or deletion. Keep the
validated repository binding unchanged throughout.

1. **Close authority before wiki mutation.** Complete any explicitly authorized
   source materialization, Agent substantive review, and exact-path local authority checkpoint
   described above. Then keep the wiki
   read-only while building the source closure from each new authoritative
   Source ID plus every existing Source ID referenced by pages that may be
   updated or deleted. Stop on missing, ambiguous, untracked, or unsafe
   provenance before beginning a transaction.

2. **Begin one transaction.** Pass every closed Source ID explicitly and retain
   the returned identifier:

   ```bash
   <wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty
   ```

   Supply the complete source closure after that single option. Never substitute
   absolute paths.

3. **Write final candidates.** Read `candidate_vault` and `started_at` from the
   begin result. Write only final Markdown paths beneath `concepts/`,
   `entities/`, `skills/`, `references/`, `synthesis/`, `journal/`, or
   `projects/` in that directory; control paths are not candidate paths. Do not
   `cd` into `candidate_vault`.

   Every candidate requires `title`, `category`, `tags`, `sources`, `created`,
   and `updated` frontmatter. Its `sources` must be a non-empty subset of the
   transaction source closure. For a new page, set `created = updated =
   started_at`. For an update, preserve the existing `created` and set `updated
   = started_at`. Generate internal links using the resolved
   `OBSIDIAN_LINK_FORMAT` setting; do not force one link syntax.

4. **Declare deletions.** For every obsolete compiled page, register its final
   vault-relative path through `<wiki-cli> transaction delete <id> <path>
   --json --pretty`. Do not remove a live vault page or manifest entry by hand.

5. **Validate and fix candidates.** Run `<wiki-cli> transaction validate
   <id> --json --pretty`. Treat validation output as authoritative, fix only the
   candidate files or declared deletions, and repeat validation until it passes.

6. **Review and commit.** Immediately before the initial promotion, rerun fresh
   validation and bounded-review every current page in `candidate_pages` plus the
   current deletion set. Establish the pre-promotion overlap guard before running
   the commit: individually derive every candidate page and deletion target, every
   affected manifest shard from the frozen Source IDs through the canonical
   manifest-v2 source-root-relative mapping, and the configured vault's canonical log target that
   the result will identify as vault-relative `log_path`. Convert each candidate,
   deletion, and log target with the retained repository-relative vault prefix;
   do not prefix the already repository-relative shard paths. Check each exact
   converted repository-relative path with:

   ```text
   [<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<promotion-path>"]
   ```

   Every output must be empty. Any staged or unstaged change is owner overlap:
   stop before transaction mutation and ask whether to preserve, separate, or
   combine it. Only after the fresh substantive review and clean guard, run
   `<wiki-cli> transaction commit <id> --json --pretty`.
   The CLI promotes validated pages, records manifest v2 ownership, and appends
   one canonical operation block to `log.md` last. Read `log_path` from the
   result.

7. **Recover failures explicitly.** Save the failed command envelope before
   doing anything else. Its top-level `error` holds `code` and `message`; its
   `recovery` holds the candidate transaction ID, status, inspection command,
   preferred action, alternatives, and each action's `requires` list.

   With a trusted transaction ID from that envelope, run `<wiki-cli>
   transaction list --json --pretty` and require exactly one retained record
   with the same ID and status. A list record provides `recommended_action` and
   `allowed_actions`; it does not repeat `error` or `recovery`. The selected
   action must appear in `allowed_actions`, agree with `recommended_action` when
   choosing the recommendation, and satisfy every string in the action's
   `requires` list before execution. If the ID or list is empty, missing,
   mismatched, duplicated, or ambiguous, stop without changing repository
   content. A failure envelope without a trusted ID is inspection-only.

   After each action, reload current structured state and compare the error code,
   status, preferred action, allowed actions, preconditions expressed by
   `requires`, identities, and exposed pre/postimages. Continue only when the next
   safe action is currently allowed and the last action made observable progress.
   Do not repeat the same action with identical inputs and unchanged state;
   diagnose a different safe cause or ask for the missing decision. There is no
   fixed recovery attempt count; structured-state progress controls continuation.

   Immediately before every promotion-capable retry, rerun fresh validation,
   bounded-review every page in the current `candidate_pages` and the current
   deletion set, and repeat the step 6 pre-promotion overlap guard. Failed-state
   checks alone are insufficient. A reported `transaction retry` proceeds
   automatically when its current requirements hold. A reported `transaction restore` proceeds automatically
   only when recorded originals can be restored with no owner drift. Ask for
   action-specific confirmation before work-losing `discard` or `abort`. Ask
   before `restore` when owner drift is present; confirmation does not bypass the
   precondition or authorize overwriting owner changes.

   `transaction commit` promotes a reviewed active transaction. `transaction
   retry` retries a failed promotion and commits on success. `transaction
   restore` restores recorded originals; `transaction abort` abandons staged
   work; `transaction discard` removes retained recovery state. Only a
   successful `transaction commit` or `transaction retry` is a knowledge commit.

8. **Refresh bounded tracked context after success.** Only after a successful
   knowledge commit, run `<wiki-cli> hot status --json`.
   If stale, derive the exact tracked `hot.md` repository path by prefixing the
   validated vault-relative `hot.md` path with the retained repository-relative
   vault prefix, and establish the
   pre-hot-write overlap guard with:

   ```text
   [<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<hot-path>"]
   ```

   Require empty output; on any staged or unstaged change, stop before hot mutation
   for a preserve, separate, or combine decision. Then obtain bounded inputs with
   `<wiki-cli> hot inputs --json --pretty`, let the agent write only the requested tracked `hot.md` working-tree diff,
   and verify a content-changing working-tree diff before finishing with
   `<wiki-cli> hot mark-current --json`. Reading existing `hot.md` is not
   regeneration; never run `<wiki-cli> hot mark-current --json` after a
   read-only or no-write path. `hot status` is read-only and must not remove the tracked file. Hot-state
   work never changes source authority, compiled pages, or transaction records.
   `transaction restore`, `abort`, and `discard` do not trigger hot refresh.

   Finish with `<wiki-cli> check --json --pretty`; it must pass before staging.
   From the successful result, collect and individually validate the exact
   vault-relative paths in `created`, `updated`, and `removed` plus vault-relative `log_path`.
   Convert all of those vault-relative result paths, and changed `hot.md` when it
   changed, to exact repository-relative pathspecs with the retained vault prefix;
   derive the affected manifest shards from the frozen Source IDs and use their
   already repository-relative paths without prefixing them again. Under the
   explicit write request, display the exact staged patch, run the cached diff
   check, leave unrelated paths untouched, and make one exact-path local result
   commit using the command sequence below with every result path passed
   separately after `--`. Any newly discovered overlap stops for the same choice.

## Task-scoped autonomy and escalation

An explicit user request authorizes ordinary local steps needed to complete the
selected task at the validated root. Agents proceed automatically to inspect
validated repository content; create or update an in-scope Source snapshot;
validate, redact, normalize, and hash it; stage and locally commit exact
task-owned paths; run, review, commit, and safely retry transactions; refresh
the derived hot view; and run bounded corrections required by checks. The
request does not authorize unrelated repository work.

Before a local commit, validate every task path separately, inspect the staged
diff, and leave unrelated paths untouched. `<git-cli>` expands into separate
argv elements, not one shell token. Use these context-aware forms:

```text
[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<task-path>"]
[<git-cli>, "--literal-pathspecs", "add", "--", "<task-path>"]
[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<task-path>"]
[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<task-path>"]
[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<task-path>"]
```

Agents ask immediately before an action that would push, open, merge, or publish a
pull request; send repository content to a remote service; add, remove, or
modify a Git remote; switch, reset, or rewrite branch history; overwrite a
dirty owner path or combine overlapping edits; force an operation or bypass a
failed safety precondition; discard or abort candidate work; delete retained
recovery evidence; expand the requested root, data scope, credentials, or
external authority; or resolve semantic ambiguity between conflicting claims,
identities, repositories, or targets. Confirmation applies only to that action.

Validation failures remain recoverable states: use current structured evidence
to restore a safe in-scope condition and retry when progress is observable. Ask
when recovery crosses one of these boundaries or lacks validated evidence.

## Operational boundaries

The CLI manages deterministic validation, promotion, manifest shards, recovery,
and freshness bookkeeping. Agents never edit manifest shards directly and never
edit `log.md` directly; transaction commit owns `log.md`. Its result includes
`log_path`. The transaction route preserves its rollback and preimage checks.

Read operations may inspect only the configured repository, vault, and bounded
inputs permitted by the task instructions. Any requested content that lacks a
repository-relative source must be materialized and reviewed before compilation.

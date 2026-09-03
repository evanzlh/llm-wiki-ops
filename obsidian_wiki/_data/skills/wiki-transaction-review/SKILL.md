---
name: wiki-transaction-review
description: Use when users ask to inspect, approve, reject, or recover a repository-local wiki transaction.
---

# Wiki transaction review

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

From the retained `info --json` result, resolve the configured vault root once
against the validated repository root. Require it to be strictly contained in
the repository and derive its normalized, non-empty repository-relative vault
prefix; never hardcode `wiki/`. Validate every vault-relative Git target as a
non-empty canonical POSIX path, rejecting absolute, NUL, backslash, `.`, `..`,
empty-component, ambiguous, or escaping values. Prefix it with that vault prefix
and verify the joined path remains contained. Manifest shards are already
repository-relative and must not be prefixed again.

Use this skill to inspect or resolve CLI-owned retained transaction state. It
does not create candidate knowledge or invent filesystem paths or commands.

## Resolve authority

In repository-local context, resolve only the nearest ancestor
`.llmwikiops/config.toml` from CWD and use the resulting root. If local discovery
finds no config, stop with `llmwikiops setup [DIR]`; invalid config fails closed.

In external adapter context, use the already validated retained exact `<root>`
and `<wiki-cli>` binding. Do not search or resolve from CWD, do not change
directories or `chdir`, and do not stop because CWD has no config.

In either context, read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md`
when present, then this task skill. The canonical protocol wins conflicts.

## Inspect the returned record

Run `<wiki-cli> transaction list --json --pretty`. Do not infer a
transaction or directory from a filesystem scan, prior message, or remembered
identifier. Select only a returned retained record, including when its status
is `active`, `promoting`, `failed`, `complete`, or `restored`.

Show its transaction ID, status, the list record's `source_ids`, absolute
`candidate_vault`, `deletions`, `recommended_action`, and `allowed_actions`.
The list record has no candidate-page inventory. Display each action's
`command`, `reason`, and `requires` without converting status into a command.

## Validate and build the sparse diff

For an active record under candidate review, run `<wiki-cli> transaction
validate <id> --json --pretty`. Require the report to match the trusted ID and
record deletions, and report every issue and warning. Only the validation
report's `candidate_pages` is authoritative for candidate review. The candidate
vault is a sparse tree, not a replacement vault.

Before reading content, require every candidate and deletion to be a canonical
vault-relative path: reject an absolute path, `..`, or an empty component.
Reject a symbolic link, hard link, directory, or special file at a candidate
or existing target; never follow it. For each returned candidate page, compare
exactly `candidate_vault/<relative>` with the configured vault's
`<relative>` and show its prospective addition or update. Review record
deletions separately against their configured-vault targets. Do not recursively
diff either tree or treat a live page absent from `candidate_pages` as deleted.
Candidate editing belongs to a separately invoked owning write workflow.

## Branch on request scope

An inspection-only request must remain read-only after the sparse review: report the
record, issues, warnings, prospective diff, deletions, and available actions, but
do not execute one. An explicit completion or recovery request authorizes the
ordinary local completion steps below, including Agent substantive review,
transaction commit or safe retry, bounded hot refresh, final check, and one
exact-path local result commit. It does not authorize a lossy or semantic choice,
owner-overlapping changes, a remote write, or history rewriting.

For an explicitly requested completion of an active record, immediately rerun
`<wiki-cli> transaction validate <id> --json --pretty`, reread and
bounded-inspect every current page in `candidate_pages` plus the current deletion
set, and display the final prospective diff. A status or validation envelope
alone is not Agent substantive review. Then refresh the list immediately. Match
exactly one retained record with the
same ID; require the refreshed record's status, `source_ids`, `candidate_vault`,
`deletions`, `recommended_action`, and `allowed_actions` to be unchanged. Find
the commit action in `allowed_actions`, show its reason, and satisfy every string
in its `requires`. If anything changed or is ambiguous, stop and re-review. With
no intervening work, apply the canonical pre-promotion overlap guard to every
candidate page and deletion target, affected manifest shard, and the configured
vault's canonical log target before any transaction mutation. Convert candidate,
deletion, and log targets with the retained repository-relative vault prefix but
do not prefix the already repository-relative shard paths, then immediately run
`<wiki-cli> transaction commit <id> --json --pretty`.

For rejection, first show all reported actions and requirements. A generic
"reject" is not authorization: the user explicitly selects and confirms one
specific destructive action. Explain and confirm `abort` even when it is the
only active action. Never infer `discard` for a failed record. Refresh first,
then execute only the still-reported selection after all `requires` hold.

## Recover failures

Save any failed command envelope, refresh the list, and match exactly one
retained record to its trusted transaction ID. Cross-check the envelope with
the refreshed record's status, `recommended_action`, and `allowed_actions`.
Execute only the reported recommendation or an applicable reported action after
all `requires` hold and any action-specific confirmation below. Retry
automatically when its current requirements hold. Restore automatically only
when recorded originals can be restored with no owner drift. The work-losing
`discard` and `abort` require action-specific confirmation. If owner drift
prevents restore, ask for a decision without overwriting owner changes or
bypassing the failed requirement. This applies to active, promoting, failed,
complete, and restored recovery. On missing or mismatched identity, conflict,
multiple matches, or any ambiguous outcome, stop without mutation.

Immediately before every promotion-capable retry, run fresh validation and
bounded-inspect every page returned in `candidate_pages`, using the current
`candidate_pages` and current deletion set under the sparse-diff rules above,
then repeat the canonical
pre-promotion overlap guard. Failed-state checks alone are insufficient. Before
a retry or other recovery action that promotes candidates, this fresh review is
mandatory. A status or validation envelope alone is not candidate review.

## Close a successful local result

After a successful `transaction commit` or `transaction retry`, run
`<wiki-cli> hot status --json`. If stale, derive the exact repository-relative
hot path by prefixing validated vault-relative `hot.md` with the retained vault
prefix, apply the canonical pre-hot-write overlap guard, run
`<wiki-cli> hot inputs --json --pretty`, write only the requested tracked
`hot.md` working-tree diff, and run `<wiki-cli> hot mark-current --json`. Then run
`<wiki-cli> check --json --pretty` and require it to pass. Collect from the
successful result the exact vault-relative paths in `created`, `updated`, and
`removed` plus vault-relative `log_path`. Prefix every validated vault-relative
result path and changed `hot.md` with the retained repository-relative vault
prefix; derive affected manifest shards from the frozen Source IDs and keep their
already repository-relative paths unprefixed. Individually validate and inspect
those exact converted paths, stage only them,
display the exact staged patch, run the cached diff check, and make one exact-path
local result commit through the canonical literal-path Git sequence. Leave
unrelated paths untouched.

The CLI owns promotion and recovery. Do not edit managed manifests or stable
generated pages directly. Ask for action-specific confirmation only for a lossy
`abort`, `discard`, or drifted restore; semantic ambiguity; an owner-overlapping
dirty path or combined edits; push, pull-request publication, another remote
write, or remote changes; or switching, resetting, cleaning, forcing, or actions
that rewrite history. Do not infer those permissions from the completion request.

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

## Approve or reject

Immediately before requesting explicit user approval, validate again, reread
the latest `candidate_pages`, and display the final prospective diff and
deletions. Approval applies only to that review.

After approval, refresh the list immediately. Match exactly one retained record
with the same ID; require its status, `source_ids`, `candidate_vault`,
`deletions`, `recommended_action`, and `allowed_actions` to be unchanged. Find
the commit action in `allowed_actions`, show its reason, and satisfy every
string in its `requires`. If anything changed or is ambiguous, stop and
re-review. With no intervening work, immediately run
`<wiki-cli> transaction commit <id> --json --pretty`. The CLI revalidates,
but user approval is not digest-bound; do not claim a stronger guarantee.

For rejection, first show all reported actions and requirements. A generic
"reject" is not authorization: the user explicitly selects and confirms one
specific destructive action. Explain and confirm `abort` even when it is the
only active action. Never infer `discard` for a failed record. Refresh first,
then execute only the still-reported selection after all `requires` hold.

## Recover failures

Save any failed command envelope, refresh the list, and match exactly one
retained record to its trusted transaction ID. Cross-check the envelope with
the refreshed record's status, `recommended_action`, and `allowed_actions`.
Execute only the reported recommendation or an applicable reported action the
user explicitly selects, after all `requires` hold. This applies to active,
promoting, failed, complete, and restored recovery. On missing or mismatched
identity, conflict, multiple matches, or any ambiguous outcome, stop without
mutation.

Before a retry or other recovery action that promotes candidates, validate and
bounded-inspect every page returned in `candidate_pages` using the sparse-diff
rules above. A status or validation envelope alone is not candidate review.

The CLI owns promotion and recovery. Do not edit managed manifests or stable
generated pages directly. Do not commit, push, or open a pull request with Git.

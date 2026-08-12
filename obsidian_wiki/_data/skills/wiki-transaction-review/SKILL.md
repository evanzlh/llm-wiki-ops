---
name: wiki-transaction-review
description: Use when users ask to inspect, approve, reject, or recover a repository-local wiki transaction.
---

# Wiki transaction review

Use this skill when the user asks to inspect, approve, reject, or recover a
pending repository-local wiki transaction. It reviews CLI-owned transaction
state; it does not create candidate knowledge or invent recovery commands.

## Resolve authority first

1. Find the nearest ancestor `.obsidian-wiki/config.toml` from the current
   working directory. Fail closed if none exists.
2. Use the configured repository root as the command working directory.
3. Read the root `AGENTS.md`, then the vault `AGENTS.md` when present, and then
   the repository-canonical `llm-wiki` skill before reviewing transaction
   state. These repository-local authorities override any bundled copy.

## Inspect active work

Run `obsidian-wiki transaction list --json --pretty` from the repository root.
Do not infer a transaction from a directory name, filesystem scan, prior
message, or remembered identifier. Use only records returned by the command.

For each active record, show the user its transaction ID, status, sources,
absolute `candidate_vault`, candidate pages, declared deletions,
`recommended_action`, and `allowed_actions`. Treat every action as structured
data containing `command`, `reason`, and `requires`; do not reduce it to a
status-based command table.

Review all candidate page content below `candidate_vault` without changing the
process working directory. Compare it with the current repository and present
a prospective diff, including additions, replacements, and deletions. Keep the
absolute candidate path in memory rather than writing it into knowledge pages.

## Validate and decide

Run `obsidian-wiki transaction validate <id> --json --pretty` after content
review. Report every validation error and warning. Fixing candidate content is
outside this review-only skill unless the user separately invokes an owning
write workflow.

Run `obsidian-wiki transaction commit <id> --json --pretty` only when validation
passes and there is explicit user approval of the reviewed prospective diff.
User approval without successful validation is not commit authorization.

If the user rejects the candidate, refresh the list and use `abort` or
`discard` only when the reported status exposes that exact command in
`allowed_actions` and every string in its `requires` list is satisfied. Never
assume either rejection command is valid from the status name alone.

## Failure recovery

On any command failure, save its response envelope, then refresh with
`obsidian-wiki transaction list --json --pretty`. Match exactly one refreshed
record to the trusted transaction ID. Cross-check the envelope's preferred
action with the refreshed record's status, `recommended_action`, and
`allowed_actions`.

Execute only the reported recommended action, or an applicable reported
allowed action selected by the user, after satisfying every string in that
action's `requires` list. Never construct `retry`, `restore`, `abort`, or
`discard` from memory. If the transaction ID is missing or mismatched, more
than one record could match, the action reports conflict, or the outcome is
ambiguous, stop and report the uncertainty without further mutation.

The transaction CLI owns knowledge promotion and recovery. Do not edit managed
manifests or stable generated pages directly. Do not commit, push, or open a
pull request with Git.

---
name: wiki-setup
description: Initialize, clone, inspect, or upgrade an LLMWikiOps repository.
---

# Wiki Repository Setup

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

## New-repository bootstrap exception

The new-repository bootstrap exception applies before any repository binding:
before setup there is no `<wiki-cli>` or validated root. Run the bare,
repository-independent `llmwikiops setup <dir>` command. After it succeeds,
validate the created root with `llmwikiops -C <dir> info --json` and retain
`llmwikiops -C <dir>` as `<wiki-cli>` for the rest of the workflow.

Use the installed CLI for deterministic repository setup. Run
`llmwikiops setup <dir>` to initialize the target directory, or clone an
existing wiki repository and inspect its checked-in `.llmwikiops/config.toml`.
Do not invent configuration files or copy runtime assets manually.

A requested setup or managed upgrade proceeds automatically after its existing path
and preimage validation. An explicit request authorizes the ordinary local commands,
safe deterministic recovery, validation, and exact-path local commit needed to
complete without an extra owner handoff. It does not authorize unrelated repository
work.

After setup or clone, retain the validated repository context. The setup output
reports the repository and vault paths; it does not print a check command. Run
`<wiki-cli> doctor` for diagnostics and `<wiki-cli> check`
for deterministic validation. Read repository `AGENTS.md`, then load
`.skills/llm-wiki/SKILL.md` before any task skill.

`.skills/` is the only editable canonical skill tree. Agent-specific skill
locations are managed mirrors created by the CLI; do not edit a mirror or
replace it with an ad hoc copy. Repository agent bootstrap files delegate back
to `AGENTS.md` and the canonical skill tree.

Use `<wiki-cli> repo sync-skills` to compare managed mirrors with `.skills/`;
`sync-skills` is read-only by default, and only `<wiki-cli> repo sync-skills
--apply` applies its reported mirror changes. Use `<wiki-cli> repo
upgrade-skills` to replace framework-owned skills and bootstrap regions from the
installed CLI. `upgrade-skills` applies immediately and has no dry-run mode.

When explicitly requested, `llmwikiops agent install-adapter --agent <target>` is an
authorized managed integration write and runs its existing deterministic validation
and recovery to completion. Use that exact command surface. Retain recovery evidence
produced by setup, Adapter installation, `sync-skills`, or `upgrade-skills`. Ask before
deleting retained evidence; confirmation applies only to that deletion.

The checked-in `requires_cli` constraint is a two-step compatibility boundary.
On a review branch, first upgrade the CLI through the project's installation
workflow. When the repository's accepted version range is unambiguous, make the
task-scoped change: deliberately edit `.llmwikiops/config.toml` so `requires_cli`
accepts the installed version, then rerun `<wiki-cli> repo upgrade-skills`. Ask before
changing `requires_cli` when the accepted range is semantically ambiguous.
The command does not bypass compatibility checks or rewrite `requires_cli`.
After any applied maintenance, rerun doctor and check, inspect the Git diff, and use
the canonical exact-path local commit flow for task-owned tracked changes. Ask before
overwriting drift. Ask before any action that would push or change repository
authority, including configuring a remote, rewriting branch history, publishing, or
opening a pull request.

---
name: wiki-setup
description: Initialize, clone, inspect, or upgrade an LLMWikiOps repository.
---

# Wiki Repository Setup

Use the installed CLI for deterministic repository setup. Run
`llmwikiops setup [DIR]` to initialize the target directory, or clone an
existing wiki repository and inspect its checked-in `.llmwikiops/config.toml`.
Do not invent configuration files or copy runtime assets manually.

After setup or clone, keep the repository root as the working directory. The
setup output reports the repository and vault paths; it does not print a check
command. Run `llmwikiops doctor` for diagnostics and `llmwikiops check`
for deterministic validation. Read repository `AGENTS.md`, then load
`.skills/llm-wiki/SKILL.md` before any task skill.

`.skills/` is the only editable canonical skill tree. Agent-specific skill
locations are managed mirrors created by the CLI; do not edit a mirror or
replace it with an ad hoc copy. Repository agent bootstrap files delegate back
to `AGENTS.md` and the canonical skill tree.

Use `llmwikiops repo sync-skills` to compare managed mirrors with `.skills/`;
`sync-skills` is read-only by default, and only `llmwikiops repo sync-skills
--apply` applies its reported mirror changes. Use `llmwikiops repo
upgrade-skills` to replace framework-owned skills and bootstrap regions from the
installed CLI. `upgrade-skills` applies immediately and has no dry-run mode.

The checked-in `requires_cli` constraint is a two-step compatibility boundary.
On a review branch, first upgrade the CLI through the project's installation
workflow, then deliberately edit `.llmwikiops/config.toml` so `requires_cli`
accepts that installed version, and rerun `llmwikiops repo upgrade-skills`.
The command does not bypass compatibility checks or rewrite `requires_cli`.
After any applied maintenance, rerun doctor and check and inspect the Git diff.
Publishing, committing, pushing, or opening a pull request remains an explicit
external Git review decision by the repository owner, never a setup side effect.

---
name: wiki-setup
description: Initialize, clone, inspect, or upgrade an Obsidian Wiki repository.
---

# Wiki Repository Setup

Use the installed CLI for deterministic repository setup. Run
`obsidian-wiki setup [DIR]` to initialize the target directory, or clone an
existing wiki repository and inspect its checked-in `.obsidian-wiki/config.toml`.
Do not invent configuration files or copy runtime assets manually.

After setup or clone, keep the repository root as the working directory. Run
`obsidian-wiki doctor` for diagnostics and the CLI `check` command shown by the
setup result before using any wiki skill. Read the repository `AGENTS.md`, then
follow `.skills/llm-wiki/SKILL.md` as the canonical runtime authority.

`.skills/` is the only editable canonical skill tree. Agent-specific skill
locations are managed mirrors created by the CLI; do not edit a mirror or
replace it with an ad hoc copy. Repository agent bootstrap files delegate back
to `AGENTS.md` and the canonical skill tree.

Use the CLI upgrade workflow when the installed framework assets change. Review
its proposed changes, preserve owner edits, rerun doctor and check, and inspect
the resulting Git diff. Publishing, committing, pushing, or opening a pull
request remains an explicit external Git review decision by the repository
owner, never a setup side effect.

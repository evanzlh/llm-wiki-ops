# LLMWikiOps Independence and Attribution

## Attribution

LLMWikiOps is independently maintained at [evanzlh/llm-wiki-ops](https://github.com/evanzlh/llm-wiki-ops). Its history begins from [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) at commit [`5ef66b6`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab). The full Git history and MIT license are preserved.

LLMWikiOps is not affiliated with or maintained by the former upstream. It does not track future upstream changes, fetch or merge from that repository, or submit pull requests back to it. The historical fork base is attribution, not an ongoing support relationship.

## Product direction

LLMWikiOps focuses on clone-ready, multi-contributor knowledge repositories. Configuration, source material, source snapshots, generated pages, skills, and agent instructions are repository-relative and reviewable in Git. Transactions make agent writes deterministic to validate, recoverable on failure, and visible as ordinary working-tree changes.

Ordinary task-scoped work completes automatically: an Agent may inspect, update, validate, and locally commit exact task-owned paths. Failed safety conditions trigger validate and recover steps without bypass, continuing only while structured state shows progress. Ask before external publication, destructive or work-losing actions, owner-overlapping changes, authority-expanding actions, or semantic decisions.

## Compatibility

The supported surface is a single repository product created by `llmwikiops setup [DIR]`. Knowledge repositories declare an accepted CLI range through `requires_cli`; commands fail closed when the installed version does not satisfy it.

Historical design records are preserved for context and marked when superseded. They are not compatibility promises. The authoritative current surfaces are the CLI help, package behavior, tests, README pair, and current `docs/` pages.

## Installation policy

Install a non-editable build from a local clone with `uv tool install --link-mode copy .`. Knowledge repositories do not vendor the executable. See [Installation](installation.md).

## Publication and future scope

The CLI never performs Git publication. Agents and contributors may make task-scoped local commits. A local commit is not Git publication; ask before pushing, opening or merging a pull request, modifying remotes, or rewriting branch history. Support, issues, and releases belong to [evanzlh/llm-wiki-ops](https://github.com/evanzlh/llm-wiki-ops), not the former upstream. A Dashboard is not part of the package and has no stub; any future work requires its own approved design.

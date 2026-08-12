# Fork Relationship and Rationale

## Attribution

This project is an independently maintained fork of [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki), based on commit [`5ef66b6`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab). It preserves upstream Git history and the MIT license, but it is not an official upstream release.

## Product direction

This fork focuses on clone-ready, multi-contributor knowledge repositories. Configuration, source material, source snapshots, generated pages, skills, and agent instructions are repository-relative and reviewable in Git. Transactions make agent writes deterministic to validate, recoverable on failure, and visible as ordinary working-tree changes.

## Compatibility

The supported product is the one repository layout created by `obsidian-wiki setup [DIR]`. Knowledge repositories declare an accepted CLI range through `requires_cli`; commands fail closed when the installed version does not satisfy it.

Historical design records are preserved for context and marked when superseded. They are not compatibility promises. The authoritative current surfaces are the CLI help, package behavior, tests, README pair, and current `docs/` pages.

## Installation policy

Install a non-editable build from a local clone with `uv tool install --link-mode copy .`. Knowledge repositories do not vendor the executable. See [Installation](installation.md).

## Publication and future scope

The CLI never performs Git publication. Repository owners decide how to commit, push, and review changes. A Dashboard is not part of the package and has no stub; any future work requires its own approved design.

# obsidian-wiki

> An independently maintained fork of [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki), based on commit [`5ef66b6`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab). See the [fork rationale](docs/fork.md).

[English](README.md) | [简体中文](README_ZH.md)

A portable, Git-native framework for compiling tracked sources into an AI-maintained Obsidian knowledge graph.

## Product model

Every knowledge base has one repository layout, one repository-relative configuration, and one tracked skill tree. Sources, source snapshots, manifest v2 shards, generated pages, and agent instructions travel together through branches and pull requests. Local transaction workspaces and `wiki/hot.md` stay ignored.

## Install

Supported hosts are Linux or macOS. Repository and vault safety depends on
POSIX descriptor-relative filesystem operations; unsupported platforms fail closed.

Install a non-editable build from a local framework clone:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install --link-mode copy .
```

This is a fresh install from a local clone; no package-index release is supported. The installed command does not depend on the clone remaining in place. Reinstallation from a framework clone is part of the reviewed upgrade/development flow below. See [Installation](docs/installation.md) for details.

## Create a knowledge repository

```bash
obsidian-wiki setup ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
```

Open `wiki/` in Obsidian. Commands resolve the nearest ancestor `.obsidian-wiki/config.toml`, so they work from the repository root or a nested directory. Setup also installs the canonical `.skills/` tree and complete agent mirrors.

Setup does not initialize Git. Before collaboration, the owner initializes the knowledge repository and reviews, stages, and commits the scaffold; see [Installation](docs/installation.md#create-a-repository).

## Collaborate safely

Source snapshots are owner-reviewed and tracked before `transaction begin`. Agents then write through local transactions. Validation happens before promotion; failures retain recovery state. A successful commit promotes candidate pages, upserts manifest shards, and writes an operation record while keeping stable `wiki/index.md` and `wiki/log.md`. A transaction never modifies tracked source snapshots. The CLI never commits, pushes, or opens a pull request: owners review the working-tree diff and handle Git publication externally.

Manifest shard updates use a repository-local lock and bounded recovery journal. Every
writer in the same working tree must cooperate by using the repository transaction
interface and its lock. Detected changes with a different identity or content become a
preserved conflict instead of being deleted. POSIX does not provide a portable
inode-conditional unlink, so a same-user process that bypasses the lock can still race
the final name check and cleanup syscall; that unsupported race has no kernel-level CAS
guarantee.

`transaction begin` freezes the selected source hashes; commit fails and requires a
restart if a source changes while candidates are prepared. If a detected manifest
conflict leaves a fixed recovery journal, inspect the live shard and working-tree diff,
then explicitly keep that version with
`obsidian-wiki manifest resolve-conflict --keep-live`. Only recovery artifacts whose
recorded identity and content still match are removed.

Use this two-step CLI and repository upgrade protocol. An owner starts a branch, installs the new CLI from the framework clone, then reads the tracked `requires_cli` constraint. Repository commands fail closed while that PEP 440 constraint excludes the installed version, so the owner must explicitly review and edit `.obsidian-wiki/config.toml` to accept the transition version before running maintenance. `repo upgrade-skills` does not rewrite `requires_cli`. After validation and diff inspection, collaborators review the complete change and the owner decides whether to commit it.

```bash
git switch -c upgrade-obsidian-wiki
cd /path/to/obsidian-wiki
uv tool install --force --reinstall --link-mode copy .
cd /path/to/team-knowledge
${EDITOR:?} .obsidian-wiki/config.toml
obsidian-wiki repo upgrade-skills
obsidian-wiki doctor
obsidian-wiki check
git diff
git commit -m "Upgrade obsidian-wiki"
```

The current product surface is the repository workflow documented here and in `docs/`. A Dashboard is intentionally absent; any future Dashboard requires a separate design and implementation, with no placeholder in this release.

## Documentation

- [Documentation index](docs/README.md)
- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [CLI reference](docs/cli.md)
- [Agent protocol](docs/agents.md)
- [Skills](docs/skills.md)
- [Contributing](docs/contributing.md)
- [Fork relationship](docs/fork.md)

## License

The original work is by Ar9av and contributors. This fork preserves the upstream Git history and MIT license; see [LICENSE](LICENSE).

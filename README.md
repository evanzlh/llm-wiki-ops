# obsidian-wiki

> An independently maintained fork of [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki), based on commit [`5ef66b6`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab). See the [fork rationale](docs/fork.md).

[English](README.md) | [简体中文](README_ZH.md)

A portable, Git-native framework for compiling tracked sources into an AI-maintained Obsidian knowledge graph.

## Product model

Every knowledge base has one repository layout, one repository-relative configuration, and one tracked skill tree. Sources, source snapshots, manifest v2 shards, generated pages, and agent instructions travel together through branches and pull requests. Local transaction workspaces and `wiki/hot.md` stay ignored.

## Install

Install a non-editable build from a local framework clone:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install --link-mode copy .
uv tool install --force --reinstall --link-mode copy .
```

The installed command does not depend on the clone remaining in place. See [Installation](docs/installation.md) for prerequisites and upgrades.

## Create a knowledge repository

```bash
obsidian-wiki setup ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
```

Open `wiki/` in Obsidian. Commands resolve the nearest ancestor `.obsidian-wiki/config.toml`, so they work from the repository root or a nested directory. Setup also installs the canonical `.skills/` tree and complete agent mirrors.

## Collaborate safely

Agents write through local transactions. Validation happens before candidate pages are promoted; failures retain recovery state. Successful writes keep stable `wiki/index.md` and `wiki/log.md`, update tracked source snapshots and manifest v2 shards, and leave an immutable operation record. The CLI never commits, pushes, or opens a pull request: owners review the working-tree diff and handle Git publication externally.

Upgrade managed skills explicitly after installing a compatible framework release:

```bash
obsidian-wiki repo upgrade-skills
obsidian-wiki doctor
obsidian-wiki check
git diff
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

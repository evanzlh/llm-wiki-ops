# obsidian-wiki

> An independently maintained fork of [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki), based on commit [`5ef66b6bec8b26bab6594ac37fb4d8371469fbab`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab). This is not an official upstream release and does not track future upstream changes. See [Fork relationship and rationale](docs/fork.md).

[English](README.md) | [简体中文](README_ZH.md)

A skill-based framework for compiling source material into an AI-maintained Obsidian knowledge graph.

## Why this fork

This fork focuses on Git-native, multi-contributor knowledge bases: sources and the compiled vault live in one repository, contributors work on branches, and generated changes go through pull-request review.

## Fork features

- Portable Repository mode with repository-relative configuration
- Tracked repository-local skills and multi-agent bootstrap files
- Stable repository-relative Source IDs and sharded manifest state
- Recoverable local transactions, immutable operation pages, and rebuildable local hot state
- Dry-run-first legacy migration with byte-for-byte rollback snapshots
- Clone-stable source bytes and merge-friendly concurrent branches
- Deterministic, LLM-free validation for any CI platform

## Install

The only supported installation is a non-editable build from a local clone:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install --link-mode copy .
```

The installed CLI does not depend on the clone remaining in place. To upgrade, pull the clone and run `uv tool install --force --link-mode copy .`.

## Start a portable team wiki

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
obsidian-wiki repo upgrade-skills  # after installing a newer framework CLI
```

Open `team-knowledge/wiki/` as the Obsidian vault. Contributors clone the knowledge repository, install the CLI as a uv tool from their own framework clone, run `obsidian-wiki doctor`, and use the tracked repository-local skills from their preferred agent. The knowledge repository contains no `.venv` or vendored CLI.

Portable agents stage writes in ignored local transaction workspaces and promote reviewed candidates into the working tree. Ordinary writes leave `wiki/index.md` and `wiki/log.md` stable, keep `wiki/hot.md` local and ignored, and append an immutable operation page. Transaction commands do not commit or push; review the Git diff and publish through your normal branch and pull-request workflow. See [Architecture](docs/architecture.md#portable-write-lifecycle) and the [CLI transaction reference](docs/cli.md#portable-transactions-and-local-hot-state).

## Migrate an existing repository

When a legacy vault and its sources are already separate directories in one repository, run the read-only analysis first:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources
```

Before applying, require the enclosing Git root to equal `--root`, commit the complete legacy baseline, and confirm a clean worktree. Then run the exact apply command printed by dry-run:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources --apply
```

Migration never imports external sources or publishes Git changes. See the [dry-run, blocker, and rollback reference](docs/cli.md#legacy-to-portable-migration).

## Personal mode

The existing personal workflow remains available through the source-installed CLI:

```bash
obsidian-wiki setup --vault ~/brain
```

## Documentation

- [Installation](docs/installation.md)
- [Portable configuration](docs/configuration.md)
- [Agent compatibility](docs/agents.md)
- [CLI reference](docs/cli.md)
- [Architecture](docs/architecture.md)
- [Skills](docs/skills.md)
- [Fork relationship and rationale](docs/fork.md)

## Upstream and license

The original work is by Ar9av and contributors. This fork preserves the upstream Git history and MIT license. See [docs/fork.md](docs/fork.md) for attribution and compatibility details.

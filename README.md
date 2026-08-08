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
- Transactional writes, merge-friendly operation logs, and rebuildable hot state
- Deterministic, LLM-free validation for any CI platform

## Install

The only supported installation is a non-editable build from a local clone:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
```

The installed CLI does not depend on the clone remaining in place. To upgrade, pull the clone and run `uv tool install --force .`.

## Start a portable team wiki

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
obsidian-wiki repo upgrade-skills  # after installing a newer framework CLI
```

Open `team-knowledge/wiki/` as the Obsidian vault. Contributors clone the knowledge repository, install the CLI as a uv tool from their own framework clone, run `obsidian-wiki doctor`, and use the tracked repository-local skills from their preferred agent. The knowledge repository contains no `.venv` or vendored CLI.

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

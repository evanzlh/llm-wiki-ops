# LLMWikiOps

> LLM-oriented operational framework for durable Markdown knowledge bases.

LLMWikiOps is independently maintained at [evanzlh/llm-wiki-ops](https://github.com/evanzlh/llm-wiki-ops). It preserves the history and MIT license of [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki), based on commit [`5ef66b6`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab). See the [fork rationale](docs/fork.md).

[English](README.md) | [简体中文](README_ZH.md)

A portable, Git-native framework for compiling tracked sources into an AI-maintained Obsidian knowledge graph.

## Product model

Every knowledge base has one repository layout, one repository-relative configuration, and one tracked skill tree. Sources, source snapshots, manifest v2 shards, generated pages, the authoritative `wiki/log.md`, the derived `wiki/hot.md`, and agent instructions travel together through branches and pull requests. Only local transaction and recovery state stays ignored.

## Install

Supported hosts are Linux or macOS. Repository and vault safety depends on
POSIX descriptor-relative filesystem operations; unsupported platforms fail closed.

Install a non-editable build from a local framework clone:

```bash
git clone https://github.com/evanzlh/llm-wiki-ops.git
cd llm-wiki-ops
uv tool install --link-mode copy .
```

This is a fresh install from a local clone; no package-index release is supported. The installed command does not depend on the clone remaining in place. Reinstallation from a framework clone is part of the reviewed upgrade/development flow below. See [Installation](docs/installation.md) for details.

Inside a wiki, repository-aware commands use nearest-ancestor CWD discovery. Outside a wiki, use an explicitly installed global adapter and mandatory `-C` / `--repo` on every repository-aware command. The repository root is always supplied for that invocation; there is no default or remembered wiki.

```bash
llmwikiops agent install-adapter --agent codex
llmwikiops -C /absolute/path/to/wiki info --json
llmwikiops -C /absolute/path/to/wiki check --json
llmwikiops -C /absolute/path/to/wiki query --mode find --term "topic" --json
llmwikiops -C /absolute/path/to/wiki transaction list --json
```

External Adapter authority reads support only a user-controlled local, quiescent
repository. Run `info --json` and then `check --json`; the latter returns the
repository-authoritative skill routing catalog produced by deterministic Python
validation. The Agent does not enumerate skills or parse their frontmatter. Shared-writable repositories must not be used and are categorically unsupported. Concurrent modification and network-sync activity are unsupported during an operation; if either occurs, stop, make the otherwise supported local repository quiescent, and restart.

Installing the CLI does not install the Adapter or write Agent integration files in the home directory. The explicit `agent install-adapter` command installs one optional global router for one Agent; see [Installation](docs/installation.md#install-the-external-wiki-adapter).

Adapter upgrades and failed-installation recovery retain verified evidence below the target Agent configuration directory's `.llmwikiops-retained/` tree rather than deleting it automatically. Retained evidence can accumulate and consume disk space. An Agent deletes retained evidence only after user confirmation; LLMWikiOps provides no automatic garbage collection, cleanup command, or uninstall command.

## Create a knowledge repository

```bash
llmwikiops setup ./team-knowledge
cd ./team-knowledge
llmwikiops doctor
llmwikiops check
```

Open `wiki/` in Obsidian. Commands resolve the nearest ancestor `.llmwikiops/config.toml`, so they work from the repository root or a nested directory. Setup also installs the canonical `.skills/` tree and complete agent mirrors.

**Protocol incompatibility.** The former `.obsidian-wiki/` state is not detected,
read, migrated, or deleted. A repository containing only it is uninitialized; when
both directories exist, `.llmwikiops/` is the only authority. Explicitly run
`llmwikiops setup` and review its new files; do not manually copy former state.

Setup does not initialize Git. Before collaboration, initialize the knowledge repository and validate, stage, and locally commit the scaffold at exact paths; an Agent may complete those requested task-scoped steps. See [Installation](docs/installation.md#create-a-repository).

## Collaborate safely

Source snapshots are Agent-reviewed, tracked and locally committed at exact paths before `transaction begin`. Agents then write through local transactions. Validation happens before promotion; failures retain recovery state. A successful commit promotes candidate pages, upserts manifest shards, and finally appends one canonical block to the tracked authoritative operation log at `wiki/log.md`; JSON output returns its `log_path`. A transaction never modifies tracked source snapshots. The tracked `wiki/hot.md` is a derived semantic view: `hot status` is read-only and must not remove it.

Ordinary task-scoped work completes automatically: an Agent may inspect, update, validate, and locally commit exact task-owned paths. Failed safety conditions trigger validate and recover steps without bypass, continuing only while structured state shows progress. Ask before external publication, destructive or work-losing actions, owner-overlapping changes, authority-expanding actions, or semantic decisions. A local commit is not Git publication; pushing, opening or merging pull requests, remote mutation, and history rewrites still require confirmation.

Manifest shard updates use a repository-local lock and bounded recovery journal. Every
writer in the same working tree must cooperate by using the repository transaction
interface and its lock. Detected changes with a different identity or content become a
preserved conflict instead of being deleted. POSIX does not provide a portable
inode-conditional unlink, so a same-user process that bypasses the lock can still race
the final name check and cleanup syscall; that unsupported race has no kernel-level CAS
guarantee.

`transaction begin` freezes the selected source hashes; commit fails and requires a
restart if a source changes while candidates are prepared. If a detected manifest
conflict leaves recovery state, inspect its structured evidence and use the currently
allowed action. Reload state after each action and continue only while it makes
observable progress; never repeat an unchanged action with identical inputs. Only
recovery artifacts whose recorded identity and content still match are removed.

Use this two-step CLI and repository upgrade protocol. After any required branch switch is separately confirmed, install the new CLI from the framework clone, then read the tracked `requires_cli` constraint. Repository commands fail closed while that PEP 440 constraint excludes the installed version; when the accepted range is unambiguous, an Agent may make the task-scoped edit before maintenance. `repo upgrade-skills` does not rewrite `requires_cli`. After validation and diff inspection, the Agent may make the exact-path local upgrade commit; publishing it remains a separate confirmed action.

```bash
git switch -c upgrade-llmwikiops
cd /path/to/llm-wiki-ops
uv tool install --force --reinstall --link-mode copy .
cd /path/to/team-knowledge
${EDITOR:?} .llmwikiops/config.toml
llmwikiops repo upgrade-skills
llmwikiops doctor
llmwikiops check
git diff
git commit -m "Upgrade LLMWikiOps"
```

The current product surface is the repository workflow documented here and in `docs/`. A Dashboard is intentionally absent; any future Dashboard requires a separate design and implementation, with no placeholder in this release.

Query discovery is explicit: run `llmwikiops query --describe --json` before querying. Agents execute `llmwikiops query --mode find --term "注意力机制" --json --pretty`; query-language/v1 fixes the English shell while accepting operands in any language. See the [CLI reference](docs/cli.md).

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

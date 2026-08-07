# Installation

## Prerequisites

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/) before continuing. This project supports one installation path: a non-editable build from a local source clone.

## Install from a clone

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
```

The installed CLI carries the skills, bootstrap files, and hook assets that setup needs. It does not depend on the clone remaining at the same path.

Install it as a user/system uv tool, not inside the knowledge repository. Each
contributor performs this framework-clone installation on their own machine. A
portable knowledge repository does not contain `.venv`, a vendored CLI package,
or another runtime copy.

## Verify

```bash
obsidian-wiki --version
```

The version output identifies this independently maintained implementation. Use `obsidian-wiki doctor` after configuring a vault to validate its config, structure, and agent integration.

## Upgrade

Run these commands in the clone used to build the tool:

```bash
git pull
uv tool install --force .
```

## Create a portable repository

Portable Repository mode keeps configuration, sources, vault content, and agent skills inside a clone-ready knowledge repository:

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
```

Open `team-knowledge/wiki/` as the Obsidian vault. The generated repository uses repository-relative configuration and does not write personal global config or global agent directories.

The first-release CLI support boundary is Linux and macOS. The committed
representation stays platform-neutral: agent adapters are regular Markdown
files rather than symlinks and require no link privileges.

## Use an existing portable repository

After cloning a portable knowledge repository, work from anywhere inside it. The CLI discovers `.obsidian-wiki/config.toml` while walking up to the repository root:

```bash
cd /path/to/team-knowledge
obsidian-wiki doctor
obsidian-wiki query "what decisions shaped this project?"
```

Repository-local skills and bootstrap files are tracked with the knowledge repository. See [Agent Compatibility](agents.md) for how each agent discovers them and [Configuration](configuration.md) for portable precedence.

After installing a newer framework CLI, refresh only the managed repository
skills and adapters, review the diff, and commit it on a branch:

```bash
obsidian-wiki repo upgrade-skills
git status --short
```

## Personal mode

Personal mode keeps the existing global configuration and agent-wide skill links. Run it only when you want this machine-wide behavior:

```bash
obsidian-wiki setup --vault ~/brain
obsidian-wiki doctor
```

`obsidian-wiki setup` writes `~/.obsidian-wiki/config` and connects the installed bundled skills to supported agents. See the [CLI reference](cli.md) for setup flags and other commands.

For multiple personal vaults, keep named configs such as
`~/.obsidian-wiki/config.work` and route one request with an `@name` token:

```text
@work update wiki
wiki-query @personal what do I know about MCP security
```

All supported agents can use this syntax because setup gives them the same
Config Resolution Protocol. Claude Code, Cursor, Windsurf, Codex, Gemini,
Kiro, Hermes, OpenClaw, Copilot CLI, Pi, and generic `AGENTS.md` agents all
inherit the routing behavior.

# Installation

## Prerequisites

- Git
- Python 3.9 or newer
- [uv](https://docs.astral.sh/uv/)
- Obsidian for viewing the generated `wiki/` directory

## Install from a clone

Install a non-editable copy so the command remains usable if the clone moves:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install --link-mode copy .
obsidian-wiki --version
```

Framework contributors can run commands from the checkout with `uv run python -m obsidian_wiki`.

## Create a repository

Setup accepts an optional directory and uses the current directory when it is omitted:

```bash
obsidian-wiki setup ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
```

Open `wiki/` in Obsidian. Commit the generated repository scaffold before collaborative work.

## Join an existing repository

Clone it, install a CLI version accepted by `requires_cli`, and validate before editing:

```bash
git clone <knowledge-repository-url> team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
```

The repository contains its own configuration, source material, canonical skills, mirrors, and bootstrap instructions. It does not vendor the executable.

After cloning, you can work from anywhere inside it: each repository-aware command discovers `.obsidian-wiki/config.toml` while walking up to the nearest configured ancestor. The repository-local skills and bootstrap files remain the authority from nested directories.

## Upgrade

First update the framework clone and reinstall the CLI:

```bash
git pull --ff-only
uv tool install --force --reinstall --link-mode copy .
```

Then create a branch in each knowledge repository, review the `requires_cli` constraint, and refresh managed files:

```bash
obsidian-wiki repo upgrade-skills
obsidian-wiki doctor
obsidian-wiki check
git diff
```

`repo upgrade-skills` preserves custom skills and refuses owner-modified managed files. The owner decides whether and how to publish the reviewed Git changes.

## CI

Install the same accepted release, then run `obsidian-wiki doctor --strict` and `obsidian-wiki check --strict`. Both commands are deterministic and require no model credentials.

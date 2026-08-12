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

Setup accepts an optional directory and uses the current directory when it is omitted. It does not initialize Git; repository creation and publication remain owner actions. One executable workflow is to initialize an otherwise empty target first, then scaffold it:

```bash
mkdir ./team-knowledge
git -C ./team-knowledge init
obsidian-wiki setup ./team-knowledge
```

From the same parent directory, the owner can review and record the scaffold explicitly:

```bash
git -C ./team-knowledge status
git -C ./team-knowledge add --all
git -C ./team-knowledge commit -m "Initialize knowledge repository"
```

Then enter the knowledge repository, validate it, and open `wiki/` in Obsidian:

```bash
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
```

Adding, committing, configuring a remote, and pushing are external Git publication steps, not framework actions.

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

Use this two-step CLI and repository upgrade protocol. First create an owner-controlled branch in the knowledge repository, update the separate framework clone, and reinstall the CLI:

```bash
cd /path/to/team-knowledge
git switch -c upgrade-obsidian-wiki
cd /path/to/obsidian-wiki
git pull --ff-only
uv tool install --force --reinstall --link-mode copy .
```

Return to the knowledge repository and read its tracked `requires_cli`. Resolution fails closed if the old PEP 440 constraint excludes the installed CLI. Before any repository command, the owner must explicitly review and edit the constraint to accept the transition version. A range that accepts both collaborator versions can support a staged rollout; every collaborator must ultimately install an accepted version. Then refresh managed files:

```bash
cd /path/to/team-knowledge
${EDITOR:?} .obsidian-wiki/config.toml
obsidian-wiki repo upgrade-skills
obsidian-wiki doctor
obsidian-wiki check
git diff
git commit -m "Upgrade obsidian-wiki"
```

`repo upgrade-skills` does not bypass compatibility checks and does not rewrite `requires_cli`. It preserves custom skills and refuses owner-modified managed files. Collaborators review the configuration and managed-file diff before the owner commits or publishes it.

## CI

Install the same accepted release, then run `obsidian-wiki doctor --strict` and `obsidian-wiki check --strict`. Both commands are deterministic and require no model credentials.

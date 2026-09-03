# Installation

## Prerequisites

- Linux or macOS. The filesystem safety boundary requires POSIX
  descriptor-relative operations and fails closed on unsupported platforms.
- Git
- Python 3.9 or newer
- [uv](https://docs.astral.sh/uv/)
- Obsidian for viewing the generated `wiki/` directory

## Install from a clone

Install a non-editable copy so the command remains usable if the clone moves:

```bash
git clone https://github.com/evanzlh/llm-wiki-ops.git
cd llm-wiki-ops
uv tool install --link-mode copy .
llmwikiops --version
```

Framework contributors can run commands from the checkout with `uv run python -m obsidian_wiki`.

Installing or reinstalling the CLI performs no home-directory integration writes. The explicit `llmwikiops agent install-adapter --agent <target>` command is the only global integration write.

## Install the external-wiki Adapter

Inside a wiki, repository-aware commands use nearest-ancestor CWD discovery. Outside a wiki, use an explicitly installed global adapter and mandatory `-C` / `--repo` on every repository-aware command.

External Adapter authority reads require a user-controlled local, quiescent repository. The owner guarantees that concurrent mutation cannot occur; shared-writable repositories, network-sync activity, and network filesystems requiring concurrent consistency are unsupported.

Install the optional router for one agent per command:

```bash
llmwikiops agent install-adapter --agent codex
```

`--agent` is required and accepts exactly one of `codex`, `claude`, `cursor`, `windsurf`, `opencode`, `pi`, or `kiro`. Run a separate command for every Agent that needs the Adapter. CLI installation, `setup`, and upgrade do not automatically install it. There is no automatic target detection, default target, `--all`, custom destination, or repository argument. There is no `--force` and no uninstall command. Conflicting unmanaged or user-modified destinations fail closed; ask before moving or removing the overlapping path.

The destination registry is fixed:

| `--agent` | Adapter destination |
|---|---|
| `codex` | `$CODEX_HOME/skills/llm-wiki-ops/` or `~/.codex/skills/llm-wiki-ops/` when unset |
| `claude` | `~/.claude/skills/llm-wiki-ops/` |
| `cursor` | `~/.cursor/skills/llm-wiki-ops/` |
| `windsurf` | `~/.codeium/windsurf/skills/llm-wiki-ops/` |
| `opencode` | `~/.config/opencode/skills/llm-wiki-ops/` |
| `pi` | `~/.pi/agent/skills/llm-wiki-ops/` |
| `kiro` | `~/.kiro/skills/llm-wiki-ops/` |

The `CODEX_HOME` override must be an absolute path. When `CODEX_HOME` is unset, Codex uses the invoking user's `~/.codex`; other targets always use the listed home-relative location. The CLI accepts no custom destination.

During an upgrade or recovery, verified old managed trees and interrupted-installation evidence move out of the active namespace to `<agent-config>/.llmwikiops-retained/.llmwikiops-retained-<token>`, where `<agent-config>` is the directory containing that target's `skills/` directory. This retention is the evidence and recovery boundary: the installer does not automatically call `unlink` or `rmdir`, performs no automatic garbage collection, and provides no cleanup command. Retained directories can accumulate and consume disk space. An Agent deletes retained evidence only after user confirmation that the evidence is no longer needed; there is no uninstall command.

The Adapter stores no packaged skill metadata: installation no longer reads or embeds it. It does not store a wiki path or install a wiki's task skill tree globally. For every external operation, explicitly name the exact repository root and put the global option before the subcommand:

```bash
llmwikiops -C /absolute/path/to/wiki info --json
llmwikiops -C /absolute/path/to/wiki check --json
llmwikiops -C /absolute/path/to/wiki query --mode find --term "topic" --json
llmwikiops -C /absolute/path/to/wiki transaction list --json
```

The environment conditions are owner guarantees; the Adapter does not mechanically prove quiescence. `info --json` and `check --json` mechanically perform static validation of the root, configuration, accepted CLI version, and repository topology. `check --json` may deterministically finish an already-recorded framework skill-maintenance recovery, so it is not promised to be purely read-only. Start no direct Agent reads or task-directed writes until both preflight commands succeed. If concurrent mutation or network-sync activity is detected or suspected, stop and restart the operation against a quiescent repository.

The selected directory itself must directly contain `.llmwikiops/config.toml`; an unconfigured child is rejected without ancestor fallback. No default, profile, environment, or recently used repository is consulted.

Ordinary task-scoped work completes automatically: an Agent may inspect, update, validate, and locally commit exact task-owned paths. Failed safety conditions trigger validate and recover steps without bypass, continuing only while structured state shows progress. Ask before external publication, destructive or work-losing actions, owner-overlapping changes, authority-expanding actions, or semantic decisions.

## Create a repository

Setup accepts an optional directory and uses the current directory when it is omitted. It does not initialize Git. A requested setup completes locally after existing-path validation; one executable workflow is to initialize an otherwise empty target first, then scaffold it:

```bash
mkdir ./team-knowledge
git -C ./team-knowledge init
llmwikiops setup ./team-knowledge
```

From the same parent directory, an Agent handling the explicit setup request can validate and record the scaffold locally:

```bash
git -C ./team-knowledge status --short
git -C ./team-knowledge --literal-pathspecs add -- <setup-path> ...
git -C ./team-knowledge --literal-pathspecs diff --cached --check -- <same-paths>
git -C ./team-knowledge --literal-pathspecs commit -m "Initialize knowledge repository" -- <same-paths>
```

Replace the placeholders with separate argv elements for every path created by this
setup; inspect the staged diff and leave any unrelated path untouched.

Then enter the knowledge repository, validate it, and open `wiki/` in Obsidian:

```bash
cd ./team-knowledge
llmwikiops doctor
llmwikiops check
```

The exact-path local commit is part of completing the requested setup. Configuring a remote and pushing are separate Git publication steps.

## Join an existing repository

Clone it, install a CLI version accepted by `requires_cli`, and validate before editing:

```bash
git clone <knowledge-repository-url> team-knowledge
cd ./team-knowledge
llmwikiops doctor
llmwikiops check
```

The repository contains its own configuration, source material, canonical skills, mirrors, and bootstrap instructions. It does not vendor the executable.

After cloning, you can work from anywhere inside it: each repository-aware command discovers `.llmwikiops/config.toml` while walking up to the nearest configured ancestor. The repository-local skills and bootstrap files remain the authority from nested directories.

## Upgrade

Use this two-step CLI and repository upgrade protocol on the selected local branch. After any required branch switch or remote update is separately confirmed, update the separate framework clone and reinstall the CLI:

```bash
cd /path/to/team-knowledge
git switch -c upgrade-llmwikiops
cd /path/to/llm-wiki-ops
git pull --ff-only
uv tool install --force --reinstall --link-mode copy .
```

Return to the knowledge repository and read its tracked `requires_cli`. Resolution fails closed if the old PEP 440 constraint excludes the installed CLI. When the accepted range is unambiguous, the Agent may make the task-scoped edit to accept the transition version. A range that accepts both collaborator versions can support a staged rollout; every collaborator must ultimately install an accepted version. Then refresh managed files:

```bash
cd /path/to/team-knowledge
${EDITOR:?} .llmwikiops/config.toml
llmwikiops repo upgrade-skills
llmwikiops doctor
llmwikiops check
git diff
git commit -m "Upgrade LLMWikiOps"
```

`repo upgrade-skills` does not bypass compatibility checks and does not rewrite `requires_cli`. It preserves custom skills and refuses user-modified managed files. A requested managed upgrade completes through validation and an exact-path local commit; publication asks first.

A local commit is not Git publication; ask before branch switching, remote changes, pushes, pull requests, or history rewrites.

## CI

Install the same accepted release, then run `llmwikiops doctor --strict` and `llmwikiops check --strict`. Both commands are deterministic and require no model credentials.

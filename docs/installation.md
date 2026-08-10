# Installation

## Prerequisites

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/) before continuing. This project supports one installation path: a non-editable build from a local source clone.

## Install from a clone

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install --link-mode copy .
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
uv tool install --force --reinstall --link-mode copy .
```

## Create a portable repository

Portable Repository mode keeps configuration, sources, vault content, and agent skills inside a clone-ready knowledge repository:

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
```

Open `team-knowledge/wiki/` as the Obsidian vault. The generated repository uses repository-relative configuration and does not write personal global config or global agent directories.

Portable setup accepts a missing target, an empty target, or a target containing only an ordinary `.git` directory. It preserves an existing `.git` directory and rejects arbitrary non-portable content; legacy layouts need explicit migration with `obsidian-wiki repo migrate`.

Setup does not run `git init`, commit, or configure a remote. For a new repository, run setup first and then `git init`; a target containing only `.git` is supported for compatibility and keeps its existing Git metadata.

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

After installing a newer framework CLI, use this two-step portable CLI upgrade protocol.
Do the work on a branch. First, deliberately update the tracked `requires_cli`
value in `.obsidian-wiki/config.toml` to a reviewed PEP 440 constraint that
accepts the installed version. Second, refresh only the managed repository
skills and adapters, validate the result, and review the complete diff:

```bash
git switch -c upgrade-portable-cli
# Edit .obsidian-wiki/config.toml so requires_cli accepts the installed version.
obsidian-wiki repo upgrade-skills
obsidian-wiki check
git diff
```

Every collaborator must install a CLI version that satisfies the repository's
updated constraint before using portable commands. `repo upgrade-skills` does
not bypass compatibility checks and does not automatically rewrite
`requires_cli`; the config and managed-file changes remain ordinary tracked
changes. Review and commit them through the branch and pull-request workflow.

## Migrate a co-located legacy repository

Migration is for an existing Git-oriented layout where the legacy vault and a
single source directory are already separate children of the same repository.
It does not gather files from elsewhere. Before invoking the CLI, the operator
must deliberately relocate or capture every authoritative source as a bounded
snapshot below the repository's source directory and update its legacy
provenance. Migration itself never moves or copies that material. Then run the
read-only analysis:

```bash
cd /path/to/knowledge-base
obsidian-wiki repo migrate --root . --vault wiki --sources sources
```

The command reports source mappings, page rewrites, manifest shards, warnings,
and blockers without changing any file. Fix every blocker and review the plan;
then ensure the enclosing Git top level equals `--root`, commit the complete
legacy baseline (including intended sources), and confirm a clean worktree.
That commit is the supported post-success rollback point. Only then run the
exact command the analyzer prints:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources --apply
obsidian-wiki check
git diff
```

Apply converts the legacy manifest and page provenance, installs portable
repository assets, makes `index.md` and `log.md` stable query surfaces, removes
the existing legacy `hot.md`, and appends one migration operation page. It
does not initialize Git, stage, commit, or push. A failed apply attempts to
restore the original files byte-for-byte. If rollback is incomplete, it reports
that state and retains evidence for manual diagnosis. A successful apply
reports retained recovery data below `.obsidian-wiki/local/migrations/`; that
internal layout is not a supported restore interface. Keep it until the diff is
accepted and committed. See the [CLI migration reference](cli.md#legacy-to-portable-migration)
for blocker meanings.

Use Markdown, text, or small reviewable text/structured snapshots for
collaborative source material, and commit their exact bytes with ordinary Git.
The analyzer checks ordinary files but not Git-index membership or LFS
signatures; review `git status` and `git ls-files` before publishing. Binary
PDFs/images remain Personal-mode inputs unless converted into a reviewable text
snapshot. A Git LFS pointer contains only metadata for an external object, so
agents must not compile it as source contents.

## Provider-neutral CI validation

After the knowledge repository has been checked out beside the framework
clone, this sequence builds the CLI from source and validates the local
portable repository:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install --link-mode copy .
cd ../knowledge-base
obsidian-wiki check
```

It invokes no LLM and no hosting-provider API; `check` reads local files and
read-only Git facts only. The `git clone` is ordinary Git transport used to
obtain the framework source.

In production CI, the knowledge-repository maintainer must pin the framework
checkout to a concrete fork release tag whose version satisfies the tracked
`requires_cli` constraint, then run the same `uv tool install --link-mode copy .`. The generic
sequence above remains runnable before this fork has its first release tag, but
it is a pre-release convenience rather than a production pin. Once a tag
exists, insert this before installation:

```bash
git checkout --detach <fork-release-tag>
uv tool install --link-mode copy .
```

`obsidian-wiki check` then fails closed if that tagged CLI does not satisfy
`requires_cli`.

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

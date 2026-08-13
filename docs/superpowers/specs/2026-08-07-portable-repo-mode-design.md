> **Superseded (2026-08-12):** Current behavior is defined by the
> [Portable-Only Repository Design](2026-08-12-portable-only-design.md).

# Portable Repository Mode Design

**Status:** Approved for implementation planning

**Date:** 2026-08-07

**Scope:** A Git-managed, multi-contributor knowledge repository containing both authoritative sources and a compiled Obsidian vault

## Summary

Evolve `evanzlh/obsidian-wiki`, an independently maintained fork of `Ar9av/obsidian-wiki`, around a first-class **Portable Repository mode** alongside the existing personal/global mode. A portable knowledge repository keeps its sources, vault, configuration, canonical skills, and agent bootstrap files in one Git repository. Contributors install this fork's `obsidian-wiki` CLI from a local source clone as a system-level tool, then work without a global vault configuration or absolute paths.

The mode is optimized for branch-and-PR collaboration. Authoritative state is sharded where practical, centralized hot files are either stable views or local rebuildable caches, and all deterministic validation is exposed through one platform-neutral command.

## Context

The existing setup is designed primarily for a personal vault:

- `obsidian-wiki setup` writes `~/.obsidian-wiki/config`.
- The resolved vault and source paths are commonly absolute.
- Skills are normally installed globally into each agent's discovery directory.
- `.manifest.json`, `log.md`, `hot.md`, and `index.md` are shared write targets.
- Some Git behavior assumes the vault itself is the Git repository root.

Those defaults create problems for a team knowledge base:

- A clone should not require machine-specific path configuration.
- The source material and compiled wiki must be versioned together.
- Different contributors must be able to compile sources on their own branches.
- Generated changes must be reviewed through ordinary pull requests.
- Concurrent work on unrelated sources should not repeatedly conflict in central state files.

## Fork Identity

This repository is a fork of [`Ar9av/obsidian-wiki`](https://github.com/Ar9av/obsidian-wiki) at commit `5ef66b6bec8b26bab6594ac37fb4d8371469fbab`. From that baseline, `evanzlh/obsidian-wiki` evolves independently and does not promise to merge future upstream changes.

The fork retains the `obsidian-wiki` Python distribution and CLI names for command compatibility, but identifies its implementation as `evanzlh/obsidian-wiki`. The upstream relationship remains visible for attribution and history; the fork must not present itself as an official upstream release.

## Goals

1. Keep `sources/` and the compiled `wiki/` in the same Git repository.
2. Make every committed path independent of clone location and username.
3. Require no repository-local Python environment or vendored CLI runtime.
4. Keep configuration, skills, and agent instructions inside the repository.
5. Support the project's current agent compatibility surface on Linux and macOS.
6. Let any contributor use any agent or LLM API to prepare a branch.
7. Make Git diff and pull-request review the content quality boundary.
8. Minimize meaningless merge conflicts in derived state.
9. Provide deterministic, platform-neutral validation that never invokes an LLM.
10. Preserve the existing personal/global workflow without a silent migration.
11. Make the upstream relationship, fork motivation, independent status, and new behavior prominent in project documentation and package metadata.
12. Use one source-clone installation path and remove competing installation and publication paths.

## Non-goals

- Reproducible or byte-identical LLM prose across agents or models
- Recording the agent, model, API provider, or generation tool in wiki content
- Running LLM generation in CI
- Bundling Python, a `.venv`, or the `obsidian-wiki` CLI in the knowledge repository
- Supporting Windows in the first portable-mode release
- Supporting Git LFS, large binary corpora, or external object storage in the first release
- Binding the workflow to GitHub, GitLab, or another hosting provider
- Treating live URLs or machine-external files as long-term authoritative sources
- Publishing this fork to PyPI or another package index
- Installing the tool directly from a package index, remote Git URL, skills registry, or editable source tree
- Promising future synchronization with the upstream repository

## Selected Approach

Implement portable repositories as a first-class mode in `obsidian-wiki`. Do not emulate portability with a wrapper that repeatedly translates absolute paths, and do not use a Git submodule or remote package reference to vendor the framework into each knowledge repository.

The system-level CLI is a prerequisite, like Git or Obsidian. Its only supported installation flow is a non-editable build from a local clone of this fork:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
```

`uv` copies the built tool into its user-level isolated tool environment. The installed CLI does not depend on the source directory continuing to exist. The repository declares both the required fork implementation and a compatible CLI range. The CLI refuses an upstream or otherwise incompatible build even if its version number happens to match.

## Repository Layout

```text
knowledge-base/
├── .obsidian-wiki/
│   ├── config.toml                 # tracked portable configuration
│   └── local/                      # ignored, rebuildable machine state
├── .skills/                        # tracked canonical skill snapshot
├── .agents/skills/                 # relative adapters to .skills/
├── .claude/skills/
├── .cursor/skills/
├── .windsurf/skills/
├── .pi/skills/
├── .kiro/skills/
├── .agent/                         # agent-specific rules/workflows
├── .github/copilot-instructions.md
├── sources/                        # authoritative source material
├── wiki/                           # the Obsidian vault
├── AGENTS.md                       # canonical repository bootstrap
├── CLAUDE.md                       # alias or thin adapter
├── GEMINI.md                       # alias or thin adapter
├── .hermes.md                      # alias or thin adapter
├── .gitignore
├── README.md                       # English landing page
└── README_ZH.md                    # Simplified Chinese landing page
```

`wiki/`, not the repository root, is opened as the Obsidian vault. The enclosing repository is the Git boundary.

`.skills/` is the only editable skill copy. Agent-specific skill directories use relative symlinks where the agent supports repository-local discovery. Bootstrap files direct agents that do not natively register repository-local skills to open the matching `.skills/<name>/SKILL.md` based on user intent. Portable compatibility guarantees that a file-capable agent can follow the workflow; it does not guarantee native slash-command registration in agents that only register global skills.

## Initialization and First Use

Create a portable repository with an explicit mode flag:

```bash
obsidian-wiki setup --portable ./team-knowledge
```

This command:

- creates the tracked repository structure and portable configuration;
- initializes the vault and manifest v2 marker;
- copies the installed CLI's bundled skills into `.skills/`;
- creates relative agent adapters and bootstrap files;
- writes ignore rules for local derived state;
- does not write under `~/.obsidian-wiki/`;
- does not create a `.venv` or install the CLI;
- does not run `git init`, commit, push, or configure a remote.

After cloning an existing portable repository:

```bash
cd team-knowledge
obsidian-wiki doctor
```

No knowledge-repository setup command is required after clone. The contributor must already have installed this fork's CLI from a separate source clone. Local cache directories are created lazily on first use.

## Portable Configuration

Example `.obsidian-wiki/config.toml`:

```toml
schema_version = 1
implementation = "evanzlh/obsidian-wiki"
requires_cli = ">=0.8,<0.9"

[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
```

The version range above is illustrative; implementation will select the first compatible fork release. `implementation` prevents an upstream installation with a coincidentally compatible version from operating on a portable repository.

The TOML file is canonical for every existing option that can affect committed output, including link format, category policy, and schema rules. Machine-specific caches and credentials are not represented there. Portable skills resolve the repository and their canonical `.skills/` content through this file; they do not require an absolute `OBSIDIAN_WIKI_REPO` value.

### Repository root

The repository root is the ancestor directory for which `<root>/.obsidian-wiki/config.toml` exists. Paths are resolved against that root, never against the process working directory and never against the `.obsidian-wiki/` directory itself.

The CLI walks upward from the current working directory, so commands behave identically when invoked from the repository root, `sources/`, `wiki/`, or their descendants.

### Resolution precedence

Configuration resolution becomes:

1. An explicit `@name` vault override
2. The nearest ancestor `.obsidian-wiki/config.toml`
3. The nearest legacy `.env` containing `OBSIDIAN_VAULT_PATH`
4. `~/.obsidian-wiki/config`
5. Setup guidance if none exists

`@name` stays highest because it is an explicit per-invocation request to target another vault. Without an override, work inside a portable repository cannot accidentally write to the user's personal global vault.

After resolving portable configuration, agents continue to read `<vault>/AGENTS.md` when present. Its vault-specific content conventions override framework defaults just as they do in personal mode.

### Path constraints

Portable mode rejects:

- absolute configured paths;
- lexical `..` escapes from the repository root;
- symlink-resolved paths that escape the repository root;
- a vault nested inside a source root;
- a source root nested inside the vault;
- authoritative sources outside configured source roots.

The first version uses `sources/` as the primary source root. The array representation leaves room for a later schema to support additional in-repository source roots without reintroducing absolute paths.

The vault's `_raw/` directory remains an optional short-lived staging area, not a durable Source root. Material intended to be shared and reproducibly compiled must be promoted into `sources/`.

## CLI Installation, Identity, and Skill Versioning

The CLI is globally available after a non-editable source installation, but the repository pins agent behavior by tracking a canonical skill snapshot. Installing a newer CLI must not silently rewrite repository skills.

Install:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
```

Upgrade:

```bash
cd obsidian-wiki
git pull
uv tool install --force .
```

The source directory may be moved or deleted after installation. `uv tool install --editable .` is unsupported because it would make the CLI depend on that directory.

The CLI version output, `doctor`, and `check` expose and validate the implementation identifier `evanzlh/obsidian-wiki`. Version tags belong to the fork's independent release line; inherited upstream history is not an update channel.

Skill upgrades are explicit:

```bash
obsidian-wiki repo upgrade-skills
```

The command updates only canonical skills and tool-managed adapter regions, producing an ordinary Git diff for review. It must not overwrite owner-maintained repository conventions.

Root bootstrap files distinguish two regions:

1. A tool-managed region for portable configuration, routing, and safety rules
2. A team-maintained region for terminology, writing style, content boundaries, and review policy

Aliases and agent-specific bootstrap files should refer to the canonical instructions instead of copying independently maintained policy text.

## Source Identity

A committed Source ID is the normalized, POSIX-style path from the repository root:

```text
sources/design/architecture.md
sources/meetings/2026-08-07.md
```

Source IDs never contain a clone-specific absolute path. Live URLs and files elsewhere on a contributor's machine are import inputs, not durable Source IDs. They must first be snapshotted into `sources/`.

Page frontmatter uses these IDs directly:

```yaml
---
title: Portable Repository
category: concepts
tags:
  - knowledge-management
sources:
  - sources/design/portable-repo.md
created: 2026-08-07
updated: 2026-08-07
---
```

No model, agent, API, or generation-tool provenance field is added. Git commits and pull requests provide operational provenance.

## Manifest v2

Portable repositories use a sharded manifest. The existing single-file format remains supported in personal mode.

`wiki/.manifest.json` is a stable format marker:

```json
{
  "schema_version": 2,
  "storage": "sharded",
  "entries": ".manifest/sources"
}
```

Each source owns one entry below `wiki/.manifest/sources/`, mirroring its path below the configured `sources/` root:

```text
wiki/.manifest/sources/design/architecture.md.json
wiki/.manifest/sources/meetings/2026-08-07.md.json
```

Example entry:

```json
{
  "source_id": "sources/design/architecture.md",
  "content_hash": "sha256:...",
  "pages": [
    "concepts/portable-repository.md",
    "references/repository-layout.md"
  ],
  "compiled_at": "2026-08-07T15:30:00+08:00"
}
```

`pages` contains paths relative to the vault root. JSON serialization is canonical and stable. Freshness depends on the content hash, not filesystem modification time.

Status is defined as:

```text
source hash equals manifest hash    -> synchronized
source hash differs                 -> stale
source has no manifest entry        -> uncompiled
manifest source no longer exists    -> orphaned and pending cleanup
```

Different sources can support the same page. Recompiling one source must preserve other valid source associations on that page. The manifest-to-page and page-to-manifest relationships are validated in both directions.

A source move changes its Source ID. When the old and new content hashes match uniquely, the CLI may propose a rename that migrates the entry and page frontmatter. Ambiguous identical-content cases require explicit resolution.

## Authoritative and Derived State

Tracked authoritative state is:

```text
sources/**
wiki/** knowledge pages
wiki/.manifest/sources/**
wiki/journal/operations/**
```

Rebuildable local state is:

```text
wiki/hot.md
.obsidian-wiki/local/**
```

### Operation log

Writes no longer append to a shared mutable `log.md`. Each successful write transaction creates a separate journal entry:

```text
wiki/journal/operations/2026/08/20260807T073000Z-a81f.md
```

The entry records source IDs and pages created, updated, or removed. It does not record the model, agent, API, or operator. The timestamp plus collision-resistant suffix prevents unrelated branches from choosing the same path.

`wiki/log.md` is a stable landing page containing an Obsidian built-in query over `journal/operations/`; it is not rewritten after each operation.

### Index

`wiki/index.md` is also stable. Category sections use Obsidian built-in query blocks, for example:

````markdown
## Concepts

```query
path:"concepts"
```
````

The index therefore reflects new pages without a centralized generated list. CLI commands scan page frontmatter directly and never depend on rendered Obsidian query results.

### Hot cache

`wiki/hot.md` becomes a local, ignored semantic cache:

- it is generated lazily on the first relevant read or write operation;
- it is rebuilt from current page summaries and recent operation entries;
- it is invalidated when the branch or authoritative-state fingerprint changes;
- it never drives freshness or synchronization decisions;
- it may differ between contributors and models;
- it must not be committed.

## Write Transactions and Concurrency

An ingest or update follows this sequence:

1. Resolve and validate the portable repository.
2. Acquire a repository-local lock in `.obsidian-wiki/local/`.
3. Record preimage hashes for every prospective tracked target.
4. Generate candidate pages and metadata under a local transaction directory.
5. Validate frontmatter, Source IDs, links, manifest relationships, and output paths.
6. Recheck target preimage hashes to detect concurrent modification.
7. Snapshot replaced files locally.
8. Promote pages, then manifest entries, then the operation record.
9. Mark the transaction complete and release the lock.

The operation record is last because it represents a completed knowledge-base change. Multi-file filesystem updates cannot be literally atomic, so the transaction manager provides all-or-nothing behavior through preimage checks, local backups, and rollback.

A dirty worktree is allowed because editing sources before ingest is the normal workflow. The transaction refuses to overwrite a target that changed after the transaction started.

Two agents cannot write concurrently in one working tree. Contributors working in separate clones, branches, or worktrees proceed independently and reconcile through Git.

The CLI exposes diagnostics to list, retry, restore, or discard incomplete local transactions. Git remains the durable recovery mechanism; local snapshots protect only against a failed run leaving a half-written worktree.

## Contributor Workflow

```text
create a branch
  -> add or edit files under sources/
  -> ask any supported agent to ingest the selected source
  -> agent follows repository-local skills
  -> deterministic helpers update pages, manifest shards, and an operation entry
  -> run obsidian-wiki check
  -> inspect the Git diff
  -> commit and open a pull request
```

The CLI does not commit, push, open a pull request, or select an LLM. Simultaneous edits to the same knowledge page are meaningful semantic conflicts and are resolved by reviewers.

## Platform-neutral Validation

Portable mode provides one read-only command:

```bash
obsidian-wiki check
```

It never invokes an LLM or a hosting-provider API. It validates:

- configuration schema and CLI compatibility;
- repository containment and absence of committed absolute paths;
- Source IDs and configured source roots;
- source content hashes and manifest freshness;
- duplicate, malformed, or orphaned manifest entries;
- existence of manifest-referenced pages;
- required page frontmatter and internal links;
- bidirectional page/source/manifest relationships;
- operation-entry schema;
- canonical skills and managed agent adapters;
- accidental tracking of `hot.md`, caches, snapshots, locks, or transactions.

The command returns a nonzero exit status for errors. In portable mode, an uncompiled source, stale source hash, or orphaned manifest entry is an error rather than an advisory status. `obsidian-wiki status` may present the same conditions interactively, but `check` enforces a fully synchronized pull-request state.

Any CI system can clone a compatible source tag or commit, install it, and call the check. A knowledge-repository pipeline performs the clone in temporary CI space rather than committing the CLI source into the knowledge repository:

```bash
git clone --branch <compatible-fork-tag> https://github.com/evanzlh/obsidian-wiki.git /tmp/obsidian-wiki-cli
uv tool install /tmp/obsidian-wiki-cli
obsidian-wiki check
```

Repository documentation may include CI examples, but no provider-specific workflow is part of the required design.

## Fork Documentation and Distribution Surface

The fork replaces the upstream translation and installation surface:

```text
README.md       # English
README_ZH.md    # Simplified Chinese
docs/fork.md    # attribution, motivation, differences, and evolution policy
```

`README_TW.md` is removed. `README.md` and `README_ZH.md` are one documentation surface and must keep headings, examples, links, installation behavior, and feature claims aligned. The existing advisory translation-drift check is retargeted from `README_TW.md` to `README_ZH.md`; it remains non-blocking.

Both README files place a fork notice near the top that states:

- the upstream repository and exact fork baseline;
- that this is an independently maintained, unofficial derivative;
- why the fork exists: Git- and pull-request-oriented multi-contributor knowledge bases;
- the fork's major new features;
- where to read the full relationship and compatibility policy.

`docs/fork.md` expands those points and documents the independent-evolution policy. `pyproject.toml` retains the original author and license attribution, adds the fork maintainer, points Homepage, Repository, and Issues to `evanzlh/obsidian-wiki`, and adds an Upstream project URL.

The MIT `LICENSE` remains unchanged. The PyPI badge and publishing workflow are removed. `setup.sh`, package-index installation, direct Git URL installation, skills-registry installation, and their documentation, tests, code comments, and error guidance are removed. Build metadata remains because `uv tool install .` must build a wheel internally; the wheel is an implementation artifact, not a published distribution channel.

## Backward Compatibility

- Existing `obsidian-wiki setup` behavior remains the personal/global default.
- Legacy `.env` and `~/.obsidian-wiki/config` resolution continues unchanged below portable configuration in precedence.
- Personal vaults continue using manifest v1 unless explicitly migrated.
- Portable repositories are created directly with manifest v2.
- A newer CLI must not silently convert configuration or manifest schemas.
- Existing setup behavior is available only through the source-installed CLI; legacy installation entry points are not retained.

## Explicit Migration

Migration is an explicit repository operation, conceptually:

```bash
obsidian-wiki repo migrate --root .
```

It defaults to a dry run and reports:

- absolute sources that map cleanly into `sources/`;
- external sources that must first be copied or snapshotted;
- manifest entries that will be sharded;
- page frontmatter that will change;
- agent adapters and configuration files that will be created;
- naming, page, or Source ID conflicts.

Applying the migration requires explicit confirmation. It produces a normal working-tree diff, never commits automatically, and never deletes files outside the repository. An external source that cannot be mapped blocks migration rather than being retained as an absolute path.

## Security and Privacy

- API keys and agent credentials remain in the agent or operating-system configuration and are never written to the repository.
- Path containment is checked after symlink resolution.
- Transaction promotion cannot write outside the repository or configured vault.
- Imported live content must become a reviewable file under `sources/` before compilation.
- The existing optional `visibility/` tags remain content policy; they are not a substitute for repository access control.

## Alternatives Rejected

### Compatibility wrapper around absolute-path behavior

A wrapper could resolve repository-relative paths into temporary absolute environment variables and normalize output afterward. This is initially smaller but leaves two path models and is easy for skills, frontmatter writers, manifest helpers, or Git synchronization code to bypass.

### Vendored runtime or repository `.venv`

Bundling the CLI or a project environment makes every knowledge repository heavier and creates an unnecessary runtime-upgrade surface. The agreed prerequisite is a system-level CLI installed with `uv tool`.

### Git submodule or remote dependency

A submodule or Git dependency pins the framework but makes clone incomplete until another fetch succeeds. It also does not solve repository-local configuration, sharded state, or agent bootstrapping by itself.

## Acceptance Criteria

The design is successfully implemented when:

1. Two contributors can clone the same repository at different absolute paths and obtain identical committed output paths.
2. Neither contributor needs `~/.obsidian-wiki/config` or global skill installation for repository work.
3. The repository contains no `.venv` or vendored CLI runtime.
4. Unrelated source ingests modify separate manifest and operation files.
5. `index.md` and `log.md` do not change on ordinary ingest, and `hot.md` is ignored and rebuildable.
6. `obsidian-wiki check` detects stale sources, invalid provenance relationships, path escapes, and accidentally tracked local state without invoking an LLM.
7. Existing personal/global setup and manifest v1 tests continue to pass.
8. Skill and bootstrap upgrades produce reviewable diffs without overwriting team-maintained instructions.
9. The documented workflow works on Linux and macOS with a system-installed compatible CLI.
10. `doctor` and `check` reject an upstream CLI that lacks the fork implementation identifier.
11. A non-editable `uv tool install .` continues to work after the source clone is moved or deleted.
12. README and project metadata clearly attribute upstream, explain the fork, and link its independent feature set.
13. `README_TW.md`, PyPI publication, `setup.sh`, and every supported or documented installation path other than clone plus `uv tool install .` are absent.

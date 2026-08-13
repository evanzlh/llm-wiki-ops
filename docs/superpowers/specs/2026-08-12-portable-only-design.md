# Portable-Only Repository Design

**Date:** 2026-08-12
**Status:** Approved
**Target baseline:** `feat/portable-repo-mode` at `3373379`
**Implementation branch:** `feat/portable-only`

## Summary

Make Portable Repository the only supported `obsidian-wiki` product architecture and
remove Personal mode completely. There is no replacement mode switch, migration path,
deprecated alias, or compatibility shim. Repository-local sources, configuration,
skills, compiled knowledge, local transactions, and ordinary Git review become the
single mental model for users and runtime agents.

This work is intentionally split into two sequential projects:

1. `feat/portable-only` removes Personal mode and establishes a stable portable-only
   core; and
2. `feat/portable-bases-dashboard`, based on that core, adds native Obsidian Bases
   dashboards through a separate configuration-transaction protocol before release.

The Dashboard project receives its own focused design and implementation plan. The
present design fixes only the boundary the portable-only core must preserve for it.

## Problem

The current project supports Personal and Portable Repository modes concurrently.
Configuration resolution, CLI arguments, Python APIs, runtime skills, manifests,
documentation, setup, caches, Git behavior, and recovery instructions repeatedly
branch on the selected mode. An agent managing a wiki must first determine which
mode it is in, then remember different authority, write, tracking, and completion
rules. Many packaged skills contain two full terminal workflows.

That compatibility surface has three costs:

- agents can mix Personal direct writes with Portable transactions;
- every feature and safety fix must be implemented and tested against two state
  models; and
- Personal-only features keep global paths, monolithic manifests, central mutable
  files, framework-owned Git operations, and vault-local staging concepts alive in
  otherwise portable code.

The product has selected repository portability and branch-and-PR review as its
long-term model. Keeping Personal mode no longer provides enough value to justify
the ambiguity and maintenance burden.

## Goals

- Make one repository-local architecture the only supported wiki workflow.
- Remove Personal mode from CLI, Python APIs, packaged runtime skills, setup,
  documentation, tests, and internal branches.
- Resolve exactly one nearest-ancestor `.obsidian-wiki/config.toml` from CWD.
- Make tracked `sources/` the only durable authority for generated knowledge.
- Route every agent-written knowledge-page mutation through portable transactions.
- Leave Git publication entirely to users or external tooling.
- Preserve deterministic validation, recoverability, owner changes, and path safety.
- Retain explicit quick capture, rebuild, import, and binary-input workflows after
  adapting them to tracked sources and transactions.
- Keep native Obsidian Bases dashboards possible without treating configuration as
  knowledge content.
- Keep current behavior documentation bilingual and aligned.

## Non-goals

- Migrating a Personal vault or manifest into a portable repository.
- Reading Personal manifest v1 after the cutover.
- Preserving old commands, flags, environment variables, profile files, or Python
  entry points behind warnings or deprecated aliases.
- Running Git add, commit, push, remote configuration, or repository initialization.
- Providing vault archives, whole-vault restore, or bulk-clear operations.
- Persisting narration readouts.
- Maintaining cron, launchd, terminal-notification, or QMD integration.
- Supporting Dataview or DataviewJS.
- Implementing the final Dashboard UX in the portable-only project.

## Selected Delivery Approach

Use two stages and two branches rather than combining mode removal and Dashboard
development into one change set:

```text
feat/portable-repo-mode @ 3373379
    `-- feat/portable-only
            `-- feat/portable-bases-dashboard
```

The first branch must be independently stable and releasable as a portable-only core.
The second branch begins only after that baseline passes full acceptance. This keeps
legacy deletion and new configuration-write behavior independently reviewable and
recoverable.

## Product Model

After this change, the product no longer asks users or agents to select a mode.
"Portable Repository" remains a useful description of the architecture, but is not
presented as one option alongside Personal mode.

The only supported knowledge repository has this shape:

```text
repository/
|-- .obsidian-wiki/
|   |-- config.toml
|   |-- managed-skills.json
|   `-- local/                 # ignored local state
|-- .skills/                   # canonical runtime skills
|-- .claude/skills/            # managed complete mirror
|-- .cursor/skills/
|-- .windsurf/skills/
|-- .agents/skills/
|-- .pi/skills/
|-- .kiro/skills/
|-- sources/                   # tracked authoritative inputs
`-- wiki/                      # Obsidian vault and compiled knowledge
```

Core invariants are:

- configuration and all durable paths are repository-relative;
- `sources/` contains the durable inputs from which knowledge pages are compiled;
- agent-written knowledge changes begin in ignored local transaction workspaces and
  are promoted only after validation and review;
- `.obsidian-wiki/local/` contains local transactions, recovery evidence, locks, and
  derived state, and is never authoritative knowledge;
- transaction completion produces an ordinary working-tree diff but never publishes
  it; and
- the enclosing Git repository is the history and collaboration boundary.

The vault no longer creates or interprets `_raw/`, `_staging/`, `_archives/`, or
`_readouts/`. `wiki/hot.md` remains an ignored, locally rebuildable semantic cache.
`wiki/index.md` and `wiki/log.md` remain stable query surfaces rather than shared
per-operation write targets.

`PortableConfig`, `portable.py`, and similar names may remain because they identify the
product architecture, not a runtime mode branch. `ResolvedConfig.mode` and equivalent
selection state must not remain.

Portable configuration `schema_version = 1` remains supported. It is the TOML schema
version and is unrelated to the Personal monolithic manifest v1 that this design
removes. Portable manifest v2 and its sharded-entry schema remain the compilation
ledger.

## Configuration Resolution

All repository-aware commands walk upward from CWD and load the nearest
`.obsidian-wiki/config.toml`. They do not accept a vault or repository override. To
operate on another wiki, the caller changes into that repository.

`resolve_config()` no longer accepts a vault argument, home directory, profile name,
or environment fallback. It directly returns `PortableConfig`. Remove:

- `ResolvedConfig` and its mode enum;
- explicit vault selection;
- inline `@name` targeting;
- nearest `.env` parsing;
- `~/.obsidian-wiki/config` and `config.<name>` parsing;
- named and global profile helpers; and
- portable-context override warnings.

Runtime inspection retains only `resolved`, `unconfigured`, and `error`. When no
configuration is found, guidance is `obsidian-wiki setup [DIR]`. A malformed nearest
configuration fails closed; resolution does not skip it and continue to an ancestor.

Repository-relative containment, implementation identity, PEP 440 CLI compatibility,
source/vault non-overlap, ordinary-file, link, and special-file checks remain in
force. Remove settings that exist only for deleted concepts, including
`OBSIDIAN_RAW_DIR`.

## CLI Contract

### Setup and default invocation

The only setup syntax is:

```bash
obsidian-wiki setup [DIR]
```

`DIR` defaults to CWD. Remove `--portable`, `--vault`, `--project`,
`--project-only`, `--copy`, and `--remote`. Invoking `obsidian-wiki` without a
subcommand displays help instead of implicitly running setup.

Setup does not install global skills, write under `~/.obsidian-wiki`, initialize Git,
or configure a remote.

### Repository-aware commands

The following commands use the nearest repository config and remove vault overrides:

| Command | Portable-only contract |
|---|---|
| `info` | Remove `--vault`; report the current repository configuration. |
| `doctor` | Remove `--vault` and `--project`. |
| `query` | Remove `--vault`. |
| `context-pack` | Remove `--vault`; retain `context` only if it is a current, non-Personal alias. |
| `graph-query` | Remove the duplicate command; `query` owns configured graph questions. |
| `graph-analyse` | Remove positional vault and use current config. |
| `lint` | Remove positional vault and use current config. |
| `trust-record` | Remove positional vault and use current config. |
| `trust-check` | Remove positional vault and use current config. |
| `batch-plan` | Use configured sources; retain only batch-planning options. |
| `cache-check` | Accept `SOURCE...` and use the current sharded manifest. |
| `cache-update` | Delete; transaction commit owns manifest updates. |
| `check` | Validate the current repository. |
| `transaction` | Operate only on the current repository. |
| `hot` | Operate only on the current repository. |
| `repo sync-skills` | Retain. |
| `repo upgrade-skills` | Retain. |

Delete `repo migrate`, `sync`, and `sync-setup` together with their implementations.

Independent tools such as `ast-extract`, `cache-hash`, and `sessions-*` remain. They
do not resolve a wiki repository and do not constitute a Personal mode.

### Compatibility policy

This is a hard cut:

- deleted commands and flags are absent from argparse;
- old invocations receive standard unknown-command or unknown-argument errors;
- no placeholder command explains that Personal mode was removed;
- no deprecated Python aliases or optional legacy branches remain; and
- the framework never guesses, rewrites, or migrates Personal data.

Structured JSON error contracts remain for supported commands. Human errors remain
concise and omit tracebacks.

## Knowledge Write Lifecycle

The only knowledge compilation lifecycle is:

```text
tracked source
    -> cache-check
    -> transaction begin
    -> agent writes candidate pages
    -> validate and review
    -> transaction commit
    -> manifest shards + immutable operation page + working-tree diff
    -> optional local hot.md refresh
```

Transaction begin freezes complete authoritative source closure and records affected
preimages. Candidate pages may cite only Source IDs in that closure. New, replacement,
and reviewed deletion candidates are validated against the complete prospective graph.
Commit updates affected knowledge pages and manifest shards and writes one immutable
operation page. Ordinary commits do not rewrite `index.md`, `log.md`, or `hot.md`.

On failure, callers follow only the transaction envelope's preferred, recommended,
and allowed recovery actions. They do not infer a recovery operation from partial
filesystem state. Retry, restore, abort, and discard retain their existing status
preconditions.

### Quick capture

`wiki-capture --quick` remains, but writes a normal tracked source snapshot instead of
`_raw/`:

```text
sources/inbox/YYYY-MM-DD-<slug>.md
```

The snapshot records origin, capture time, captured text, and an original-content hash
when one is available. Quick capture creates no knowledge page, empty transaction,
manifest update, operation page, or hot refresh. It remains visibly pending as a new
source until a later `wiki-ingest` transaction compiles it.

### External and binary inputs

URLs, PDFs, images, and other opaque inputs are transient session inputs. Before any
knowledge transaction, the agent produces a small reviewable UTF-8 Markdown snapshot
below a configured source root. The snapshot contains sufficient extracted text,
origin, format, and hash information for review. Only its repository-relative Source
ID is durable provenance.

The framework does not treat binary files, Git LFS pointers, live URLs, external
absolute paths, or inaccessible source locators as authoritative sources. If reliable
extraction and review cannot be completed, the workflow stops before creating a
knowledge transaction.

### Ingest and correction

Append ingest compiles new and modified sources. Full ingest may deliberately
recompile selected sources regardless of freshness, but still uses transactions.
Correction reuses verified existing authoritative sources whenever they support the
change; it must not invent an unrelated conversation snapshot merely to satisfy the
transaction interface. The CLI alone owns marker and shard writes.

### Rebuild

`wiki-rebuild` becomes transaction-backed page rebuild. It may replace, create, or
delete an explicitly reviewed page set derived from declared sources. Large rebuilds
may use several bounded, independently reviewable transactions.

Remove archive-only, whole-vault clear, archive-and-rebuild, and archive restore.
Never create `_archives/` or approximate these operations through direct copies.
Historical restoration belongs to external Git branches, tags, or reverts.

### Narration

`wiki-narrate` is read-only and returns narration in the conversation. Remove `--save`
and `_readouts/`. If narration should become durable knowledge, the user explicitly
starts capture or ingest instead.

## Runtime Skill Model

Every packaged runtime skill exposes one configuration protocol and one terminal
completion path. Remove instructions to resolve a mode, choose between Personal and
Portable branches, or avoid falling through from one branch to another. Write-oriented
skills reference one canonical transaction protocol rather than embedding two full
completion systems.

### Skills removed or replaced

| Skill | Result |
|---|---|
| `wiki-switch` | Delete with named/global vault profiles. |
| `memory-bridge` | Delete; Portable manifest deliberately omits generator-tool provenance. |
| `wiki-stage-commit` | Delete and replace with `wiki-transaction-review`. |
| `wiki-dashboard` | Remove in the core project; reintroduce as Bases-only in the Dashboard project. |

`wiki-transaction-review` lists active and recovery transactions, shows candidates,
deletions, validation issues, and expected impact, and performs approved validation,
commit, rejection, or status-directed recovery. It never reads or creates `_staging/`.

### Skills retained and rewritten

- `llm-wiki` becomes the single repository authority and write protocol.
- `wiki-setup` describes only `obsidian-wiki setup [DIR]`.
- `daily-update` remains a manually invoked freshness, transaction, and hot-state
  check. Remove cron, launchd, terminal notifications, global state, and QMD.
- capture, ingest, history ingest, import, and research skills first select or create a
  tracked source, then use a transaction.
- update, deduplication, cross-linking, taxonomy, synthesis, and rebuild skills perform
  all knowledge-page changes through transactions.
- status, query, context pack, lint, digest, and export understand only Portable
  manifest shards, operation pages, and local derived state.
- narration is read-only and conversation-only.
- `wiki-export` and `vault-skill-factory` put generated review artifacts under ignored
  `.obsidian-wiki/local/` directories rather than the vault knowledge space.
- `graph-colorize` and `obsidian-layout-adjustment` are explicit Obsidian configuration
  workflows, not knowledge transactions. Backups live under `.obsidian-wiki/local/`,
  mutations are reviewed as ordinary Git diffs, and person-specific wording is removed.

Systematically remove from retained skill bodies and references:

- Personal/Portable terminal branching and mode terminology;
- `_raw`, `_staging`, `_archives`, and `_readouts` semantics;
- Personal manifest v1 and `cache-update`;
- `@name`, `.env`, and global config;
- QMD, cron, launchd, and terminal notification support;
- framework-owned Git publication; and
- Personal-only central-file writes.

## Python Implementation

Delete `obsidian_wiki/migration.py`, `obsidian_wiki/sync.py`, Personal setup and global
skill installation code, manifest-v1 readers and writers, `cache-update`, legacy
configuration parsers, and Personal Git mutation helpers. Delete the associated test
fixtures instead of preserving unreachable implementations.

`cache.py` retains portable freshness classification and standalone hashing. It no
longer accepts an optional `PortableConfig` that selects between two implementations;
sharded manifest behavior is unconditional for wiki cache operations. Manifest shard
updates remain private to transaction completion.

Retain portable setup, managed skill inventory and mirrors, manifest v2, transactions,
transaction validation and guidance, operation pages, local hot state, lint, trust,
graph analysis, query, context packing, and read-only Git inspection. Git helpers may
identify the root, branch, or worktree and detect drift for validation or fingerprints,
but must not initialize, stage, commit, push, or change remotes.

## Setup and Filesystem Safety

`obsidian-wiki setup [DIR]` retains deterministic, recoverable installation:

- build a new target completely in a sibling staging directory and then promote it;
- permit safe idempotent setup of an existing portable repository;
- preserve owner files and custom canonical skills;
- refuse unknown managed drift and direct the user to explicit skill upgrade tools;
- reject containment escapes, symlinks, reparse points, hard links, and special files;
- restore the original target after ordinary failure; and
- retain recovery evidence when cleanup itself fails.

Safety checks apply to configuration paths, sources, vault pages, manifest entries,
managed skills, mirrors, transactions, and local state. Removing Personal compatibility
must not weaken existing race, replacement, ownership, or rollback tests.

## Dashboard Boundary

The portable-only core reserves `wiki/_meta/` as protected, non-knowledge configuration
space and retains `*.base diff merge` Git attributes. It does not ship a working
dashboard skill.

The follow-up project supports only native Obsidian Bases at `wiki/_meta/*.base`.
Dataview, DataviewJS, plugin installation, and plugin configuration are out of scope.
Bases files do not become knowledge pages and do not cite sources or update manifests,
operation pages, index, log, or hot state.

Dashboard writes use an independent configuration transaction:

```text
agent generates candidate .base
    -> deterministic syntax and path validation
    -> existing/candidate diff preview
    -> explicit approval
    -> atomic apply to wiki/_meta/*.base
```

Candidates, locks, journals, preimages, and recovery state live below
`.obsidian-wiki/local/dashboard-transactions/`. The implementation permits only
ordinary `.base` files immediately below the allowed configuration tree, rejects
escape links, hard links, special files, and target drift, and supports explicitly
approved create, replace, and delete. Apply leaves only a working-tree diff and never
performs Git publication. `obsidian-wiki check` validates committed Bases files at a
schema depth chosen in the follow-up design.

Command names, templates, field-selection UX, and exact Bases validation depth remain
decisions for the Dashboard design and are not implicit extension points in this core
project.

## Documentation Policy

Rewrite every current behavior surface as portable-only:

- `README.md` and `README_ZH.md`;
- installation, configuration, architecture, CLI, agents, skills, fork, contributing,
  and documentation indexes;
- affected Traditional Chinese CLI documentation;
- runtime bootstrap templates; and
- packaged skill bodies and references.

Keep the two READMEs aligned in headings, examples, links, and behavior, and run
`tools/check_readme_sync.py`.

Historical files under `docs/superpowers/specs/` and `docs/superpowers/plans/` retain
their original body. Add a prominent Superseded banner to every affected historical
document linking to this design. Historical statements are not current compatibility
promises. Personal terminology is allowed only in those marked historical records and
in explicit removal rationale in this design and its implementation plan.

## Testing and Acceptance

Use test-driven development. Replace mode-sensitive tests with portable-only contract
tests before changing implementation. Coverage includes:

- `setup [DIR]`, default CWD, idempotence, rollback, and owner preservation;
- nearest-ancestor configuration and nested repositories;
- refusal to fall back to `.env` or user-global configuration;
- absence of deleted CLI commands, flags, and Python legacy entry points;
- rejection rather than interpretation of Personal manifest v1;
- portable-only cache freshness and transaction-owned shards;
- quick capture producing a tracked pending source and later ingest producing a shard;
- exactly one transaction completion path in every knowledge-writing skill;
- presence of `wiki-transaction-review` and absence of `wiki-stage-commit`,
  `wiki-switch`, `memory-bridge`, and the temporary dashboard skill;
- canonical and mirrored skill parity;
- containment, link, hardlink, special-file, drift, race, and recovery safety; and
- an end-to-end collaboration flow from setup through a reviewable transaction diff.

Final verification includes:

```bash
uv run --with pytest python -m pytest tests/test_portable_setup.py -q
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
uv run python tools/check_readme_sync.py
git diff --check
```

Install the CLI non-editably from the worktree and smoke-test help, setup, doctor,
check, cache, transaction, and managed-skill commands against a temporary repository.

Acceptance requires:

- no executable Personal workflow in current code, tests, docs, bootstrap, or packaged
  skills;
- no user-visible mode decision;
- no legacy config or manifest fallback;
- no framework-owned Git publication;
- one safe, documented source-to-transaction write lifecycle;
- all current documentation consistent with the portable-only contract; and
- the focused and full suites passing from a clean worktree.

## Implementation Commit Boundaries

Organize the portable-only work into independently reviewable commits:

1. configuration resolution and CLI contract;
2. setup, cache, and legacy module deletion;
3. transaction-facing workflow adaptations;
4. packaged runtime skill deletion, rename, and rewrite;
5. bootstrap, current documentation, and historical Superseded banners; and
6. regression cleanup, end-to-end verification, and consistency fixes.

Each commit should keep the suite runnable and avoid a long-lived half-Personal,
half-Portable product state. The implementation plan may refine task granularity but
must preserve these review boundaries and the approved product contract above.

# External Wiki Adapter and Explicit Repository Selection

**Date:** 2026-08-18
**Status:** Approved design

## Summary

LLMWikiOps currently selects a knowledge repository only by walking upward from
the CLI process working directory to the nearest `.llmwikiops/config.toml`.
Repository-local agent instructions and skill discovery follow the same working
directory boundary. This is safe and portable inside a wiki repository, but an
agent working in an unrelated project cannot use another wiki without changing
the working directory of each shell process, and it does not automatically see
the target repository's `AGENTS.md` or skills.

Add two complementary capabilities:

1. a global, explicitly installed `llm-wiki-ops` adapter skill that routes an
   agent into a user-specified external wiki and loads that repository's live
   authority chain; and
2. a global CLI option, `-C REPOSITORY` / `--repo REPOSITORY`, that selects one
   exact repository root for a repository-aware command without changing the
   caller's working directory.

There is no default wiki, named profile, environment-selected wiki, recent-wiki
fallback, or automatic filesystem discovery. External operation always begins
with a repository path explicitly supplied by the user.

## Goals

- Let an agent whose main working directory is outside a wiki perform every
  supported repository operation: query, context packing, ingest, transaction
  writes and recovery, validation, manifest maintenance, hot refresh, and skill
  maintenance.
- Make repository selection explicit, inspectable, and repeatable in every CLI
  command used by the external workflow.
- Load the target repository's owner instructions, canonical runtime protocol,
  vault instructions, and selected task skill before performing the task.
- Give the global adapter enough built-in routing information to choose a
  framework skill without first loading every target skill body.
- Preserve custom repository skills and repository-version authority.
- Preserve the existing CWD-based workflow for callers already inside a wiki.
- Install the global adapter only through an explicit, one-agent-at-a-time CLI
  command without overwriting owner changes.

## Non-goals

- Reintroducing Personal mode, global wiki configuration, named profiles,
  inline wiki aliases, or environment-based repository selection.
- Installing or mirroring a wiki repository's complete skill tree globally.
- Selecting a wiki from note contents, tool output, session history, or recent
  use.
- Changing repository-relative source provenance into absolute host paths.
- Automatically installing the adapter during CLI installation or upgrade.
- Automatically detecting installed agents or installing for several agents in
  one command.
- Providing a force-overwrite or uninstall command in the first release.
- Changing Git publication boundaries: commits, pushes, and pull requests remain
  owner-controlled external operations.

## Current Behavior

`resolve_config()` starts at process CWD, walks upward, and loads the nearest
`.llmwikiops/config.toml`. Repository-aware commands expose no repository or
vault override. Initialized repositories contain canonical `.skills/`, complete
project-local agent mirrors, and bootstrap instructions; framework and user home
directories intentionally contain no wiki runtime installation.

This design retains those defaults. It adds one opt-in adapter for external use
and one explicit, per-invocation repository selector.

## Chosen Approach

### Alternatives considered

1. **Adapter sets the shell tool working directory.** This needs no CLI change,
   but depends on host-specific tool support, hides the selected repository from
   command logs, and is easy to omit during multi-step transaction recovery.
2. **Global `-C` / `--repo` plus the adapter.** This makes repository identity
   part of every command, works in ordinary shells and agent tools, and keeps
   adapter routing separate from deterministic CLI selection. This is selected.
3. **A wrapper subcommand such as `llmwikiops at REPO ...`.** This is explicit
   but complicates nested parsing, help, JSON error intent, and recovery command
   rendering without adding safety over a global option.

## Architecture

### 1. Exact repository selector

The CLI accepts one global repository option before the subcommand:

```bash
llmwikiops -C /absolute/path/to/wiki-root query --mode find --term "topic" --json
llmwikiops --repo /absolute/path/to/wiki-root transaction list --json
```

`-C` and `--repo` are aliases for the same single-valued option. Repetition in
any alias combination is an argument error rather than last-value-wins.

When the option is present, the requested path is converted to an absolute path
relative to invocation CWD without following links. The adapter always supplies
an already normalized absolute path. The selector then:

1. requires the requested path itself to be an ordinary, safely inspectable
   directory;
2. requires `<requested>/.llmwikiops/config.toml` to exist directly;
3. loads that exact configuration with the existing implementation identity,
   PEP 440 compatibility, stable-directory-identity, containment, link, special
   file, and configured-path checks; and
4. requires the loaded `PortableConfig.root` identity to equal the requested
   directory identity.

It never invokes ancestor discovery when an explicit repository is present. A
missing or invalid direct config cannot fall back to a parent, CWD, environment
variable, user config, profile, or another repository.

When the option is absent, existing nearest-ancestor CWD resolution remains
unchanged.

The selected `PortableConfig` is resolved once per command and passed through
the command implementation. Repository-aware code must not call `Path.cwd()` a
second time to reinterpret repository-relative inputs. Source IDs, vault paths,
transaction state, Git pathspecs, and local state remain relative to the selected
repository authority.

### Repository-aware command scope

The explicit selector applies to these command families:

- `info`, `doctor`, and `check`;
- `repo sync-skills` and `repo upgrade-skills`;
- every `transaction` operation;
- `manifest resolve-conflict`;
- every `hot` operation;
- `batch-plan`, `graph-analyse`, and `cache-check`;
- `lint`, `trust-record`, and `trust-check`;
- `query`, `context-pack`, and the `context` alias.

If `query --describe` is combined with an explicit repository, the repository
is still validated so the option is never silently ignored.

Repository-independent commands reject `-C` / `--repo`: `setup`, `list`,
`agent install-adapter`, `ast-extract`, `cache-hash`, and `sessions-*`. Version
printing remains repository-independent.

The option does not change the parent shell or agent session CWD. Internal path
resolution behaves as if the selected repository were the repository execution
context.

### 2. Global adapter skill

The installed skill name and directory are `llm-wiki-ops`. Its frontmatter
description triggers only when the user asks to access or operate an LLMWikiOps
repository outside the current workspace and explicitly supplies its root path.
The adapter must not infer or remember a repository path.

The adapter is a router, not a global copy of repository runtime instructions.
Its generated `SKILL.md` contains:

- the external-repository selection and authority-loading protocol;
- the immutable repository-binding rule;
- CLI, Git, and file-operation command forms; and
- a deterministic routing catalog containing the exact `name` and complete
  frontmatter `description` of every direct built-in skill bundled with the
  installed CLI.

The routing catalog is rendered during `agent install-adapter` from the same
validated package-resource skill tree used by setup and upgrades. It is not a
second manually maintained inventory. Generation fails if any built-in skill is
unsafe, lacks valid frontmatter, has a directory/name mismatch, or duplicates a
name. Tests require byte-stable output and exact inventory parity.

The adapter embeds built-in descriptions, not built-in task bodies. Task bodies
are always read from the selected repository so customizations, owner rules, and
the repository's accepted CLI generation remain authoritative.

### 3. Runtime authority and routing

For an external request, the adapter performs this sequence:

1. Require one repository root explicitly supplied by the user. Convert it to a
   normalized absolute path without deriving a replacement from untrusted data.
2. Run `llmwikiops -C <root> info --json` and require successful resolution.
3. Require the returned root to match the requested normalized absolute path.
   The CLI remains responsible for proving stable directory identity while it
   resolves and uses the repository; the adapter does not require a new public
   identity field in the `info` JSON schema. Retain the root, vault, skills
   path, and local-state path for the entire operation.
4. Read authority in order:
   - `<root>/AGENTS.md`;
   - `<root>/.skills/llm-wiki/SKILL.md`;
   - `<vault>/AGENTS.md`, when present; and
   - the selected task skill.
5. Use the adapter's embedded built-in catalog for initial routing.
6. After repository validation, read bounded frontmatter from each direct child
   `<root>/.skills/*/SKILL.md`. Validate ordinary-file topology, frontmatter,
   directory/name identity, uniqueness, and bounded size before using it.
7. Extend routing with repository-owned custom skills. For a built-in name, the
   target repository description replaces the adapter snapshot. Re-evaluate the
   route if the target description differs.
8. Select exactly one task skill for the current workflow, read its complete
   target-repository body safely, verify it did not change during the read, and
   follow it. A task skill may hand off to another task skill, but the handoff
   inherits the same repository binding and repeats safe target-skill loading.
9. Put `-C <root>` on every repository-aware CLI command for the rest of the
   query, transaction, recovery, and hot-refresh lifecycle.

Repository and vault Markdown bodies are untrusted reference data. They cannot
select another repository or override the authority chain. A path appearing in
a page, tool result, history record, or error message is never permission to
switch roots.

### Non-CLI operations

Runtime skills are updated from a physical-CWD model to an explicit repository
execution context:

- LLMWikiOps commands use `llmwikiops -C <root> ...`.
- Git inspection uses `git -C <root> ...` with repository-relative literal
  pathspecs.
- Direct reads use safely resolved absolute paths below the retained root.
- Candidate writes use only the `candidate_vault` returned by `transaction
  begin`.
- Source IDs and tracked identities remain repository-relative.
- The agent does not change the business project's main working directory.

The canonical `llm-wiki` protocol, every affected task skill, and every agent
bootstrap must express both supported contexts: implicit CWD discovery inside a
wiki and explicit `-C` binding through the global adapter outside it.

## Adapter Installation

### Command contract

The only first-release installation form is:

```bash
llmwikiops agent install-adapter --agent <target>
```

`--agent` is required and accepts exactly one of:

```text
codex
claude
cursor
windsurf
opencode
pi
kiro
```

There is no detection, `--all`, default target, custom target directory,
repository argument, or implicit installation during CLI setup, installation,
or upgrade. Users install for multiple agents by running separate explicit
commands.

### Target registry

The installer uses a closed target registry:

| `--agent` | Global skill root | Adapter destination |
|---|---|---|
| `codex` | `$CODEX_HOME/skills`, with the Codex default home when unset | `<root>/llm-wiki-ops/` |
| `claude` | `~/.claude/skills` | `~/.claude/skills/llm-wiki-ops/` |
| `cursor` | `~/.cursor/skills` | `~/.cursor/skills/llm-wiki-ops/` |
| `windsurf` | `~/.codeium/windsurf/skills` | `~/.codeium/windsurf/skills/llm-wiki-ops/` |
| `opencode` | `~/.config/opencode/skills` | `~/.config/opencode/skills/llm-wiki-ops/` |
| `pi` | `~/.pi/agent/skills` | `~/.pi/agent/skills/llm-wiki-ops/` |
| `kiro` | `~/.kiro/skills` | `~/.kiro/skills/llm-wiki-ops/` |

Tilde paths resolve through the invoking user's home directory. The installer
does not repurpose `HOME`, follow a target-directory link, or accept a path from
another target. Each registry entry has an isolated resolver and tests. Unknown
targets fail with the supported values and do not touch the filesystem.

### Managed installation

An installed adapter directory contains:

```text
llm-wiki-ops/
├── SKILL.md
└── .llmwikiops-managed.json
```

The managed record contains a schema version, CLI implementation and version,
target name, generated file inventory, and SHA-256 content digests. It contains
no wiki path and no build-machine absolute path.

Installation is a recoverable staged replacement inside the target skill root:

- **Missing destination:** validate the parent topology, stage complete ordinary
  files, verify bytes and identities, then atomically promote.
- **Current managed bytes equal generated bytes:** report unchanged without
  rewriting.
- **Older managed installation, no drift:** replace atomically with the newly
  generated adapter and record.
- **Owner modification, malformed/missing managed record, unknown files, unsafe
  topology, or unmanaged collision:** fail without overwriting or deleting.
- **Interrupted staging or promotion:** recover or remove only artifacts whose
  recorded identity and content still match; preserve ambiguous state for owner
  inspection.

There is no `--force`. The owner must review and move or remove a conflicting
directory before retrying. The first release does not add uninstall; manual
removal remains an explicit owner action.

## Failure Model

### CLI selection failures

Explicit repository selection fails closed when:

- the value is empty, repeated, unavailable, or not a directory;
- the requested root or direct config topology is unsafe;
- the requested root lacks a direct `.llmwikiops/config.toml`;
- config schema, implementation, or `requires_cli` is invalid;
- configured vault, sources, skills, or local state is unsafe or escapes the
  repository;
- a repository-independent command receives the option; or
- a command attempts to resolve a second repository after binding.

Existing human or structured error renderers remain authoritative for each
command family. Query errors retain their query-language envelope; transaction
argument and operation errors retain their recovery envelope. The new selector
must not leak tracebacks or introduce a second JSON error schema.

### Adapter routing failures

The adapter stops before the task command when:

- the user did not explicitly provide a root;
- `info` returns a different root or invalid context;
- an authority file is missing where required, unsafe, or changes during read;
- the built-in catalog is malformed;
- target skill frontmatter is invalid, duplicate, unsafe, or ambiguous;
- the selected skill does not exist in the target repository;
- repository description changes make the route ambiguous;
- the selected skill changes while being loaded; or
- a handoff attempts to change repositories.

No failure permits a fallback wiki. Ambiguous skill routing is returned to the
user for an explicit choice.

## Compatibility and Product Boundaries

This is a narrow extension of portable-only operation, not a second mode:

- portable repositories remain self-contained and clone-ready;
- all tracked configuration paths and provenance stay repository-relative;
- the explicit host path exists only in the command/runtime context and is never
  persisted into tracked repository content;
- the existing nearest-ancestor resolver remains the default for repository-local
  use;
- repository commands still enforce `requires_cli`;
- the global adapter is optional and contains no selected wiki state;
- ordinary repository collaboration still needs no global installation; and
- global installation contains only one adapter, never a wiki's task skill tree.

Documentation that currently says global wiki installation is unnecessary is
refined, not reversed: it remains unnecessary for repository-local use and is an
explicit opt-in only for external-repository routing.

## Documentation Changes

Keep `README.md` and `README_ZH.md` aligned and update:

- `docs/cli.md` with global option placement, exact-root semantics, supported
  command families, examples, and adapter installation;
- `docs/agents.md` with external authority loading and immutable binding;
- `docs/architecture.md` with the optional global router boundary;
- `docs/configuration.md` with explicit selection precedence and the continued
  absence of a user-level wiki config;
- `docs/installation.md` with one-agent-at-a-time adapter installation and
  upgrade behavior;
- `docs/skills.md` with generated built-in routing catalog behavior;
- canonical `llm-wiki`, affected task skills, and all agent bootstraps with the
  dual repository-context protocol; and
- portable-only design language that currently prohibits every explicit
  repository selector, while retaining its ban on defaults and profiles.

Human-facing documentation uses this distinction consistently:

```text
Inside a wiki: nearest-ancestor CWD discovery.
Outside a wiki: explicitly installed global adapter plus mandatory -C/--repo.
```

## Testing Strategy

### CLI resolution

- Prove `-C` and `--repo` select the same exact repository.
- Prove `info --json` need not expose filesystem identity metadata for the
  adapter to validate its normalized root while the CLI enforces identity
  internally.
- Reject repeats across both aliases.
- Put different configured repositories at invocation CWD and explicit root;
  prove the explicit root is the only one read or written.
- Give an unconfigured child of a configured parent; prove explicit selection
  fails rather than walking upward.
- Exercise unavailable directories, files, links, special files, replaced
  ancestors, malformed configs, implementation mismatch, and `requires_cli`
  mismatch.
- Parameterize every repository-aware command family from an unrelated CWD.
- Prove repository-independent commands reject the option.
- Run the complete existing suite without the option to protect CWD behavior.
- Preserve query parser JSON intent, transaction parser JSON intent, recovery
  envelopes, and concise human errors.

### Adapter generation and routing

- Compare the generated catalog to every validated bundled skill's exact name
  and description.
- Detect added, removed, renamed, duplicated, reordered, or description-modified
  built-ins; require deterministic output across repeated builds.
- Prove built-in routing chooses a target-repository body, not the packaged
  body.
- Add a repository custom skill and prove its frontmatter extends routing.
- Change a target built-in description and prove target metadata wins and causes
  route re-evaluation.
- Reject malformed, oversized, linked, special, duplicate, mismatched, or
  concurrently replaced target skills.
- Assert authority loading order and the invariant that every generated
  repository command contains the same `-C <root>`.
- Assert vault content and tool output cannot switch repositories.

### Installer

- Test all seven target registry entries with isolated temporary home/config
  roots.
- For each command, assert only the selected agent destination changes.
- Cover new install, idempotent install, clean upgrade, owner drift, unmanaged
  collision, unknown files, unsafe parent topology, injected failures at every
  staging/promotion boundary, and exact recovery.
- Prove unknown/missing/multiple agent targets perform no writes.
- Build wheel and sdist artifacts, install them, move the source checkout away,
  and generate/install the same adapter from both.
- Scan artifacts and installed files for source-checkout and build-machine
  absolute paths.

### End-to-end acceptance

Use an ordinary business-project CWD and a separate temporary wiki root:

1. install an adapter for one explicit temporary agent home;
2. provide the exact wiki root to the adapter workflow;
3. validate and query the external wiki;
4. route to ingest and complete source closure, transaction begin, candidate
   write, validate, commit, and stale hot refresh;
5. run a retained recovery path with the same explicit root;
6. prove the business-project CWD and files remain unchanged; and
7. repeat with a missing root argument and with a wiki child directory, proving
   safe failure and no writes.

Run the documented full suite and README synchronization check after focused
tests.

## External Directory References

The initial target registry follows the current platform-owned skill locations:

- Codex: the platform `$CODEX_HOME/skills` contract;
- Claude Code: <https://code.claude.com/docs/en/slash-commands>;
- Cursor: <https://cursor.com/docs/skills>;
- Windsurf: <https://docs.windsurf.com/windsurf/cascade/skills>;
- OpenCode: <https://opencode.ai/docs/skills/>;
- Pi: <https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/skills.md>;
- Kiro: <https://kiro.dev/docs/skills/>.

These references select installation locations only. Repository runtime
authority continues to come exclusively from the validated target repository.

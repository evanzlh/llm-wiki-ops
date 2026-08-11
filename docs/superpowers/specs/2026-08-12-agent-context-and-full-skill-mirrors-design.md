# Agent Context Isolation and Full Skill Mirrors Design

**Date:** 2026-08-12
**Status:** Approved
**Target baseline:** `feat/portable-repo-mode` at `7596215`

## Problem

The repository currently mixes two different execution contexts:

1. the `obsidian-wiki` framework source tree, where a coding agent develops and tests
   the Python package; and
2. an initialized wiki repository, where an agent should execute the bundled wiki
   skills and follow the wiki runtime protocol.

At the framework root, `AGENTS.md`, its agent-specific aliases, the root `.skills/`
tree, per-agent skill discovery directories, and a Claude Stop hook currently expose
wiki-runtime behavior to agents that are only developing the framework. The source
tree does not contain a portable `.obsidian-wiki/config.toml`, so this injection is
both unnecessary and misleading.

Portable setup has the opposite problem. It keeps complete skills in `.skills/`, but
generates one-file adapters under each agent's discovery directory. Each adapter has
a generic description and asks the agent to open another `SKILL.md`. Skill selection
usually happens from discovery metadata before an agent reads that redirect, so the
adapter can prevent the correct skill from being selected. It also assumes that every
agent follows the indirection consistently.

The repository additionally carries a Claude-only automatic capture mechanism. Its
Stop hook inspects the just-finished transcript and may inject a quick-capture prompt.
No equivalent lifecycle integration exists for the other supported agents, and this
implicit behavior is outside the desired portable, agent-neutral model.

## Goals

- Make the framework source repository a development-only agent context.
- Store built-in skills as Python package resources outside every project-local skill
  discovery path.
- Make `.skills/` the only editable canonical skill tree in a portable wiki.
- Give every supported agent a complete ordinary-file mirror of every canonical
  portable skill, including accurate discovery frontmatter and all supporting files.
- Preserve a precise ownership boundary between framework-managed built-ins and
  collaborator-owned custom skills.
- Detect drift and fail closed rather than silently overwriting owner changes.
- Provide an explicit, recoverable command for synchronizing derived mirrors.
- Migrate unmodified legacy adapters safely during `repo upgrade-skills`.
- Remove automatic session-end capture while preserving all explicit capture and
  history-ingest workflows.
- Keep setup and upgrades platform-neutral and free of absolute repository paths.

## Non-goals

- Do not make per-agent skill directories independently editable.
- Do not use symlinks, hard links, junctions, or an agent-specific loader extension.
- Do not deduplicate mirrored file contents in Git.
- Do not auto-detect and merge edits made to generated mirrors.
- Do not automatically capture or ingest a conversation when an agent session ends.
- Do not remove manual `wiki-capture`, `wiki-capture --quick`, or any history-ingest
  skill.
- Do not turn the framework source checkout itself into a portable wiki.
- Do not change Personal-mode global skill installation in this workstream except for
  removing the automatic Claude Stop-hook installation path.

## Context Boundary

### Framework source repository

The framework source root contains only development instructions and product source:

```text
obsidian-wiki/
├── AGENTS.md                         # framework development instructions only
├── CLAUDE.md                         # ordinary pointer to AGENTS.md
├── GEMINI.md                         # ordinary pointer to AGENTS.md
├── .hermes.md                        # ordinary pointer to AGENTS.md
├── .agent/rules/...                  # development pointer only
├── .agent/workflows/...              # development pointer only
├── .cursor/rules/...                 # development pointer only
├── .windsurf/rules/...               # development pointer only
├── .kiro/steering/...                # development pointer only
├── .github/copilot-instructions.md   # development pointer only
└── obsidian_wiki/_data/skills/       # bundled product resources
```

The root `AGENTS.md` must state that this checkout is framework source, not a wiki
repository. It contains contributor commands, architecture boundaries, documentation
parity requirements, and test conventions. It must not tell a development agent to
resolve a vault, read owner wiki instructions, route ordinary development requests to
wiki skills, or apply write protocols. An explicitly invoked end-to-end fixture may
create and operate on a temporary wiki; that does not change the source-root context.

Agent-specific bootstrap files are ordinary, portable text files that direct the
agent to the development `AGENTS.md`. Where an agent format requires metadata, that
metadata describes the rule as framework development guidance. It must not be an
always-applied wiki runtime rule.

Remove all skill discovery trees from the framework root, including `.skills/` and
the six paths listed in `PROJECT_AGENT_DIRS`. The package resource tree is deliberately
under `obsidian_wiki/_data/skills/`, where coding agents do not discover it as a local
skill collection.

### Portable wiki repository

An initialized portable repository keeps runtime behavior:

```text
portable-wiki/
├── .obsidian-wiki/config.toml
├── .obsidian-wiki/managed-skills.json
├── .skills/                    # canonical built-ins and custom skills
├── .claude/skills/             # complete derived mirror
├── .cursor/skills/             # complete derived mirror
├── .windsurf/skills/           # complete derived mirror
├── .agents/skills/             # complete derived mirror
├── .pi/skills/                 # complete derived mirror
├── .kiro/skills/               # complete derived mirror
├── AGENTS.md                   # portable runtime protocol
├── agent-specific bootstraps
├── sources/
└── wiki/
```

Portable `AGENTS.md` continues to define configuration, ownership, Source ID,
transaction, Git, and path-containment rules. Each agent discovers complete skills
directly from its native directory; portable bootstraps no longer have to explain a
thin-adapter convention.

## Built-in Skill Packaging

`obsidian_wiki/_data/skills/` becomes the canonical built-in source in the framework
checkout and the installed Python distribution. There is no second checked-in root
`.skills/` copy. Package configuration includes the entire ordinary-file tree in both
wheel and source distributions.

All CLI code that needs bundled skills resolves this package resource, including when
the CLI was installed from a source clone with `uv`. Acceptance testing must install a
built artifact, remove or move the original clone, and prove that setup and skill
upgrade still work. No installed CLI path may depend on the source checkout remaining
present.

The bundled tree is validated before packaging and before setup: safe skill directory
names, an ordinary `SKILL.md`, valid required frontmatter, and no symbolic links, hard
links, or special files. Existing source-artifact ignore rules remain explicit and
must not silently discard a legitimate skill resource.

## Canonical and Derived Skill Trees

### Canonical ownership

`.skills/` is the portable repository's sole editable skill namespace. It contains:

- framework-managed built-ins copied from the installed package; and
- collaborator-owned custom skills added directly to the repository.

Every direct child skill is validated. The directory name must equal the frontmatter
`name`; `name` and `description` must be non-empty; and the entire subtree must contain
only ordinary directories and single-link regular files. Unicode is allowed in file
and directory names below a safe skill directory, and bytes and executable mode bits
are preserved exactly.

The user can add, change, or remove a custom skill in `.skills/`, review it in Git,
then explicitly synchronize mirrors. A local edit to a framework-managed canonical
skill is also treated as owner data and is never silently overwritten. Before a later
framework upgrade, that edit must be deliberately reconciled or preserved under a
custom skill identity.

### Complete mirrors

For each path in `PROJECT_AGENT_DIRS`, the `skills/` directory is an exact derived
mirror of `.skills/`. This includes every managed and custom skill and every file in
each skill tree: full frontmatter, Markdown body, references, scripts, templates, and
other resources.

Mirrors are ordinary copied files and directories. They contain no redirect adapters,
symlinks, hard links, absolute paths, or agent-specific rewrites. For each target,
skill names, relative paths, file bytes, file kinds, and meaningful mode bits match
the canonical snapshot. Mirror files are generated and must not be edited directly.

The duplication is intentional. The current bundle is small enough that transparent
discovery and predictable cross-platform behavior are more valuable than storage
deduplication.

## Managed Inventory

Upgrade the inventory to schema version 2. Its logical shape is:

```json
{
  "schema_version": 2,
  "implementation": "obsidian-wiki-portable",
  "skills_version": "<installed CLI version>",
  "managed_skills": ["llm-wiki", "wiki-ingest"],
  "managed_skill_digests": {
    "llm-wiki": "sha256:<canonical-tree-digest>",
    "wiki-ingest": "sha256:<canonical-tree-digest>"
  },
  "mirror_format": "full-copy-v1"
}
```

`managed_skills` is the framework ownership boundary, not the complete contents of
`.skills/`. Skills absent from this list are collaborator-owned. Tree digests record
the exact installed baseline for each managed canonical skill so a later upgrade can
distinguish an unchanged built-in from an owner-modified one. The digest algorithm
uses normalized relative paths, entry kinds, meaningful mode bits, and file bytes; it
does not incorporate machine-specific absolute paths or filesystem timestamps.

Setup writes the inventory only after canonical skills and every mirror have passed
validation. An upgrade writes it last, after all replacements succeed. Unknown schema
versions or mirror formats fail closed.

## Setup Behavior

Portable setup performs these steps:

1. Resolve built-in skills from `obsidian_wiki/_data/skills/`.
2. Validate the package resource tree before writing repository state.
3. Copy all built-ins into the new canonical `.skills/` tree.
4. Discover and validate every canonical skill. This normally means built-ins during
   first setup, but the implementation does not special-case the set.
5. Stage a complete copy for every supported agent directory.
6. Validate every staged mirror against the canonical snapshot.
7. Promote the portable configuration, runtime bootstraps, canonical skills, mirrors,
   and schema-v2 inventory through the existing recoverable setup transaction.
8. Run the same portable check used after later synchronization.

Setup creates all six supported mirrors by default. There is no selected-agent mode
and no adapter mode.

## Explicit Mirror Synchronization

Add:

```bash
obsidian-wiki repo sync-skills
obsidian-wiki repo sync-skills --apply
```

The default form is read-only. It validates `.skills/`, compares all target mirrors,
and prints a deterministic plan showing added, changed, removed, missing, and unsafe
entries per agent. Human output is concise; structured-output flags follow the common
CLI convention and expose issue codes and per-target changes.

`--apply` is explicit authorization to discard mirror-only drift and rebuild all six
mirrors from canonical `.skills/`. It never changes `.skills/` or the managed-skills
inventory. The implementation:

1. takes the portable skills lock and recovers any interrupted prior operation;
2. validates and snapshots `.skills/` once;
3. creates all six complete mirrors under a transaction staging directory;
4. validates staged trees byte-for-byte against the snapshot;
5. writes a journal containing targets, backups, and expected staged snapshots;
6. replaces each agent `skills/` directory with a filesystem-local rename;
7. validates the live result and marks the journal committed;
8. cleans backups and the transaction only after success.

A single filesystem operation cannot atomically replace six directories. The command
therefore provides transactional observable behavior through per-directory atomic
replacement, durable intent, rollback on ordinary failure, and deterministic recovery
on the next setup/check/sync/upgrade after interruption. It never reports success for
a partial mirror set.

If an agent `skills/` directory contains owner files not represented in `.skills/`,
the dry run reports them as drift. `--apply` may remove them only because it is the
explicit mirror-rebuild operation. Documentation tells owners to move valuable skills
to `.skills/` first. `check` and `upgrade-skills` never use this repair authority.

## Check and Drift Policy

`obsidian-wiki check` verifies:

- schema-v2 inventory syntax, implementation identity, version, managed names,
  digests, and mirror format;
- safe and complete canonical skill trees with valid discovery frontmatter;
- the recorded baseline digest and current state of every managed built-in;
- exactly the same complete skill set in `.skills/` and every agent mirror;
- exact tree content, entry kinds, relative paths, and meaningful file modes;
- absence of adapters, symlinks, hard links, special files, and extra mirror files;
- portable bootstrap integrity and the absence of generated automatic-capture state.

Mirror drift is an error. A modified managed canonical skill is separately reported
as a non-fatal upgrade-conflict warning with remediation that preserves owner data; it
is not mislabeled as mirror drift. It may still be explicitly mirrored by
`sync-skills --apply`, but a later `upgrade-skills` refuses to replace it. A custom
canonical skill is healthy whenever all mirrors match it.

Checks remain read-only except for recovering an already journaled interrupted
operation according to the repository's existing recovery contract. Human and JSON
output use stable, distinct issue codes for malformed canonical skills, managed
canonical divergence, missing mirrors, changed mirrors, extra mirror entries, unsafe
file types, and incomplete recovery.

## Upgrade and Legacy Migration

`obsidian-wiki repo upgrade-skills` upgrades only inventory-owned built-ins. Before
staging any write it requires:

- every previously managed canonical tree to match its recorded digest;
- every full-copy mirror to match the current canonical snapshot; or
- for a schema-v1 repository, every legacy adapter to match the exact adapter template
  and relative target expected for that CLI generation.

An altered managed canonical skill, altered legacy adapter, unexplained mirror file,
or mixed adapter/full-copy state fails closed. The error identifies the owner data and
instructs the user to preserve or reconcile it; upgrade does not provide a force flag.

For a trusted upgrade, the command:

1. stages the new package versions of all current managed built-ins;
2. removes managed built-ins retired by the package;
3. preserves every custom canonical skill byte-for-byte;
4. constructs every agent mirror from the resulting complete canonical set;
5. stages the schema-v2 inventory and new managed digests;
6. validates all artifacts;
7. uses the existing journal/backups/recovery mechanism to promote them; and
8. commits the new inventory last.

An exact schema-v1 adapter repository is therefore migrated atomically as part of
`upgrade-skills`; there is no separate adapter conversion command. Modified adapters
are never adopted as canonical content.

## Development Bootstrap Migration

The framework source change removes:

- root `.skills/`;
- `.claude/skills/`, `.cursor/skills/`, `.windsurf/skills/`, `.agents/skills/`,
  `.pi/skills/`, and `.kiro/skills/`;
- wiki-runtime content in root bootstrap files and always-applied rules.

It replaces the root instructions with development-only guidance and replaces each
supported agent bootstrap with an ordinary pointer appropriate to that agent's file
format. The move to `obsidian_wiki/_data/skills/` is a Git move where practical so
history remains reviewable.

Repository tests enforce this boundary: package resources may contain `SKILL.md`
files, but no framework-root path recognized as a local skill discovery directory may
contain wiki skills.

## Automatic Capture Removal

Remove the Claude Stop capture feature completely:

- `.claude/settings.json` and `.claude/hooks/wiki-stop-capture.sh` in framework source;
- the packaged hook asset;
- hook installation and upgrade code;
- the optional hook-install section in `wiki-setup`;
- setup, packaging, behavior, and recursion tests specific to the hook;
- human documentation for global or project hook installation; and
- Stop-hook language in `wiki-capture` and related skills.

After this change, no supported agent automatically reads its transcript or triggers
capture at session end. Users and agents can still explicitly invoke
`wiki-capture`, `wiki-capture --quick`, `wiki-history-ingest`, and every per-agent
history-ingest skill.

Release notes include manual removal guidance for users who previously opted into the
global Claude hook. The CLI does not infer authority to edit an existing external
`~/.claude/settings.json` while setting up or upgrading a repository.

## Error Handling and Portability

All canonical validation happens before staging. Malformed frontmatter, a name
mismatch, unsafe path component, symlink, hard link, special file, unreadable file, or
collision produces a non-zero result without repository changes.

All managed paths are resolved beneath the portable root with the existing
containment and single-link checks. Journals contain repository-relative paths only.
Generated files never persist an absolute clone location. Repository moves and CJK
paths must therefore work without regeneration except when an actual mirror is stale.

File comparisons do not depend on directory iteration order, modification time,
inode number, ownership, or platform-specific permission bits. The implementation
defines the small portable subset of meaningful mode bits—at minimum regular versus
executable files—and normalizes only those for snapshots and copies. File contents
are never newline-normalized.

## Documentation

Update the human-facing agent, installation, CLI, configuration, architecture,
skills, and contributing documentation where relevant. In particular, document:

- the source-repository versus portable-runtime context boundary;
- `.skills/` as the only editable canonical directory;
- complete per-agent mirrors as derived Git-tracked output;
- the dry-run and `--apply` forms of `repo sync-skills`;
- managed/custom ownership and upgrade conflict remediation;
- exact legacy-adapter migration rules; and
- explicit capture as the only capture behavior.

Remove all hook setup instructions. Keep `README.md` and `README_ZH.md` behaviorally
aligned and run the advisory translation-parity checker.

## Testing

### Source and distribution tests

- Assert that the framework root has no wiki skill discovery tree or Stop hook.
- Assert that every root bootstrap resolves only to development instructions.
- Build wheel and sdist and compare their complete built-in skill resources.
- Install from the wheel, move/delete the clone, and run setup and upgrade from an
  unrelated working directory.

### Setup and synchronization tests

- Use test-driven CLI/parser coverage for dry-run, `--apply`, human, and JSON output.
- Verify all six setup mirrors against `.skills/`, including frontmatter, nested
  references, scripts, executable modes, empty directories if supported, binary
  resources, and CJK filenames.
- Add, modify, and delete a custom canonical skill; verify dry-run plans and applied
  mirrors without inventory ownership changes.
- Modify, add, delete, symlink, and hard-link mirror entries; verify `check` errors,
  upgrade refusal, and explicit synchronization repair.
- Verify malformed canonical skills fail before writes.
- Inject failures before and during each promotion phase; verify rollback and next-run
  recovery, including no false successful partial state.

### Upgrade and migration tests

- Upgrade unchanged managed built-ins while preserving custom skills.
- Reject a managed canonical skill whose digest differs from the installed baseline.
- Migrate a complete set of exact schema-v1 adapters to schema-v2 full copies.
- Reject one modified, missing, extra, or mixed-format adapter without partial writes.
- Upgrade across added and retired managed built-ins and commit inventory last.
- Verify that setup and upgrade no longer create or modify Claude Stop-hook state.

### Integration acceptance

- Run the complete test suite and packaging checks.
- Initialize a fresh Git-tracked portable repository with the installed CLI.
- Demonstrate that each supported discovery directory contains the complete skills,
  with useful `name` and `description` visible without following an indirection.
- Move the repository and run `check`, sync dry-run, a transaction validation, and a
  manual capture or ingest flow without absolute-path leakage.
- Confirm no session-end event causes automatic capture.

## Acceptance Criteria

- Opening the framework source repository in a supported coding agent loads only
  framework development guidance and exposes no project-local wiki skills.
- The installed distribution owns its built-in skills independently of the source
  clone.
- A fresh portable setup gives all six supported agents complete discoverable skills,
  including accurate frontmatter and supporting resources.
- `.skills/` is the only canonical edit location and all mirrors can be deterministically
  checked and explicitly regenerated.
- Framework-managed and collaborator-owned skills remain distinguishable, and no
  check or upgrade silently overwrites unexplained owner changes.
- Exact legacy adapters migrate through `upgrade-skills`; altered adapters fail closed.
- Interrupted synchronization or upgrade is recoverable and never reported as a
  successful partial state.
- No generated portable file records an absolute clone path.
- Automatic Claude Stop capture and its install surface are gone, while explicit
  capture and history ingest continue to work.

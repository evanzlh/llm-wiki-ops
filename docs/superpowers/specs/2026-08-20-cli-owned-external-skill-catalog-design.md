# CLI-Owned External Skill Catalog Design

**Date:** 2026-08-20

**Status:** Approved for implementation planning

## Context

The optional global `llm-wiki-ops` Adapter currently embeds the installed CLI's
built-in skill names and descriptions. After external-repository preflight, it
then instructs the Agent to enumerate every direct repository skill, extract
`name` and `description` from each `SKILL.md` frontmatter block, merge that
repository catalog over the embedded snapshot, and rerun routing.

That protocol preserves repository-authored custom skills and repository-local
metadata, but it assigns deterministic filesystem discovery and frontmatter
parsing to the Agent. In practice an Agent may improvise a regular expression or
one-off parser. This is both more complicated and less reliable than the
framework's existing Python implementation. It also makes the Adapter's embedded
catalog nearly redundant because repository metadata always becomes authoritative
before an authority body is loaded.

The framework already loads and validates the canonical repository skill tree
during `llmwikiops check`. The validated `SkillCollection` contains the exact
frontmatter `name` and normalized complete `description` for every managed or
repository-authored direct skill. The CLI should expose that existing result
instead of asking each Agent to reconstruct it.

## Goals

- Make the selected repository's canonical skill tree the only routing catalog
  for an external operation.
- Parse and validate skill frontmatter deterministically in Python, never with
  Agent-authored regular expressions or ad hoc shell pipelines.
- Reuse the canonical skill snapshot already loaded by `check`; do not add a
  second repository scan solely to produce catalog JSON.
- Preserve repository-authored custom skills, repository-local descriptions,
  skill topology validation, immutable root binding, authority order, and current
  fail-closed preflight behavior.
- Remove the embedded built-in catalog and the catalog merge/reroute protocol from
  the global Adapter.
- Keep non-JSON `check` output and its exit-status behavior unchanged.

## Non-goals

- Return complete skill bodies or arbitrary frontmatter fields through the
  catalog.
- Install repository task skills globally.
- Change the repository skill format, managed-skills inventory, mirror format,
  transaction protocol, query protocol, or Adapter installation locations.
- Add a separate `skills catalog` command or make `info --json` inspect the skill
  tree.
- Change the supported quiescent local-repository threat model.

## Design

### One Python-owned catalog

`obsidian_wiki.skill_trees` will expose a small deterministic projection from a
validated `SkillCollection` to routing metadata. The JSON-compatible result is a
list sorted by canonical skill name. Every item has exactly these fields:

```json
{
  "name": "wiki-query",
  "description": "Use when querying repository knowledge."
}
```

The projection does not parse files. Parsing remains part of canonical skill-tree
discovery, which already:

- rejects unsafe directory names, links, hard-linked files, special files, and
  changed filesystem entries;
- requires one direct `SKILL.md` for every skill;
- parses supported frontmatter forms, including normalized multiline
  descriptions;
- requires nonempty `name` and `description` values;
- requires the frontmatter name to equal the direct directory name; and
- produces a sorted collection with unique directory identities.

The catalog projection validates its input type and returns fresh JSON-compatible
objects so callers cannot mutate the `SkillCollection`.

### `check --json` contract

`check_portable_repo` already calls canonical skill discovery while validating
managed skills and mirrors. That internal path will retain and return the
validated `SkillCollection` to the report assembler instead of discarding it.

Every `check --json` report gains a top-level `skill_catalog` field:

```json
{
  "status": "pass",
  "errors": 0,
  "warnings": 0,
  "issues": [],
  "skill_catalog": [
    {
      "name": "llm-wiki",
      "description": "..."
    },
    {
      "name": "wiki-query",
      "description": "..."
    }
  ]
}
```

When canonical skill discovery fails, `skill_catalog` is `null` and the existing
`canonical-skill-invalid` error keeps the report in `fail` status. If canonical
skills are valid but another repository check fails, the report may contain a
catalog, but the Adapter must stop on the failed command and must not use it.

`check --json` without `--strict` retains the existing exit semantics: `pass` and
`warn` exit zero; `fail` exits nonzero. `--strict` continues to make `warn`
nonzero. The Adapter invokes `check --json` without `--strict`, requires a zero
exit, requires `status` to be `pass` or `warn`, and requires `skill_catalog` to be
a nonempty list of exact `name`/`description` objects before routing.

Human-readable `check` output remains the current status and issue summary. It
does not print the catalog.

### Single-snapshot reuse

Canonical skill discovery must occur at most once inside one
`check_portable_repo` call. `_check_managed_skills` will return the successfully
loaded `SkillCollection | None` after using that same object for inventory and
mirror validation. `check_portable_repo` will project the returned collection
into `skill_catalog` when assembling the final report.

No new filesystem scan is added to `cmd_check`, and `info --json` remains a
lightweight repository-resolution and installation-information command.

### Simplified Adapter

The generated global Adapter retains:

- its trigger frontmatter and full-file bootstrap gate;
- one exact user-supplied repository root and immutable `-C <exact-root>` binding;
- supported local, user-controlled, quiescent repository requirements;
- command-construction safety;
- serialized preflight and existing recovery boundary;
- bounded authority and repository content reads;
- authority order, query limits, transaction review, recovery, hot refresh, Git
  restrictions, and stop conditions.

Its preflight becomes:

```text
llmwikiops -C <exact-root> info --json
llmwikiops -C <exact-root> check --json
```

After validating the exact root from `info`, the Agent validates the `check` JSON
and selects one task using only `skill_catalog`. It then loads authority bodies in
the existing order:

1. root `AGENTS.md`, if present;
2. the direct canonical `llm-wiki/SKILL.md` under the configured skills path;
3. configured vault `AGENTS.md`, if present; and
4. the direct selected task `SKILL.md`.

If the selected task is `llm-wiki`, it is read once. Delegation still loads a
delegated task body in a separate bounded read under the same root binding.

The Adapter removes:

- the embedded built-in catalog and its HTML catalog markers;
- installation-time capture and validation of packaged `SkillCollection` data;
- direct Agent enumeration of the repository skills directory;
- Agent-side frontmatter parsing and the 64 KiB routing-frontmatter protocol;
- embedded/repository catalog merging and route reevaluation; and
- correction rules for partial Agent-produced catalog output.

The Adapter treats catalog descriptions as routing metadata, not executable
instructions. Full repository skill bodies remain the authority for execution.

### Adapter generation and installation API

Because Adapter bytes no longer depend on packaged skill metadata,
`render_adapter_skill`, `build_desired_adapter`, and `install_adapter` no longer
accept a `SkillCollection`. `cmd_agent_install_adapter` no longer discovers the
packaged skill tree before installation. Adapter bytes remain deterministic for a
given template and CLI release, and the existing managed-record digest,
retention, upgrade, drift-preservation, and recovery mechanics remain unchanged.

This intentionally removes catalog parity tests from Adapter installation. Exact
repository catalog behavior moves to `skill_trees`, `portable_check`, and CLI
tests.

## Failure behavior

The external workflow stops before authority reads or task-directed mutation
when any of the following occurs:

- `info --json` fails, is empty or malformed, does not report `resolved`, or
  reports a different root;
- `check --json` fails, is empty or malformed, reports a status other than
  `pass` or `warn`, or lacks a valid nonempty `skill_catalog`;
- a catalog item has fields other than exact string `name` and `description`, an
  empty value, a duplicate name, or unsafe/noncanonical ordering;
- the required canonical `llm-wiki` entry is absent;
- routing is ambiguous or the selected direct skill body is unavailable; or
- repository evidence changes or unsupported concurrent mutation is detected.

The Agent does not repair, supplement, regex-parse, or merge a malformed catalog.
The only recovery remains the deterministic framework-managed maintenance that
`check` may complete before reporting its final state.

## Compatibility

Adding `skill_catalog` is an additive change to JSON `check` output. Existing
consumers that read the established report fields continue to work. Consumers
that require an exact top-level key set must update for the new field.

The Adapter's installed `SKILL.md` bytes change and therefore follow the existing
managed Adapter upgrade and retained-evidence path. No managed-record schema bump
is required because that schema already records the exact installed file digest
and CLI version.

Repository compatibility is unchanged: the selected repository's `requires_cli`
must accept the installed CLI, and `check` must validate its canonical skills and
mirrors before the catalog is usable.

## Documentation

Update the following surfaces consistently:

- `README.md` and `README_ZH.md`: describe the Adapter as consuming the catalog
  returned by deterministic preflight, with no embedded skill inventory.
- `docs/agents.md`: replace Agent-side enumeration and parsing with the exact
  `check --json` catalog contract and simplified authority sequence.
- `docs/architecture.md`: make Python-owned routing metadata explicit.
- `docs/cli.md`: document the additive `check --json` field.
- `docs/skills.md`: remove embedded catalog and merge/reroute behavior.
- affected historical design language only where a current normative statement
  would otherwise contradict the new architecture; historical plans remain
  historical records.

README headings, examples, links, and behavior remain synchronized through
`tools/check_readme_sync.py`.

## Testing

Tests will prove:

- catalog projection preserves exact normalized names and descriptions in stable
  order, including multiline descriptions and repository-authored custom skills;
- a successful `check --json` contains the catalog from the same canonical
  collection used for skill validation;
- invalid canonical skills produce `skill_catalog: null` and a failing report;
- unrelated repository validation failures cannot authorize Adapter routing even
  if a catalog is present;
- human `check` output remains unchanged;
- generated Adapter text contains `check --json` and the single-catalog routing
  contract, but contains no embedded catalog markers, direct skill enumeration,
  Agent frontmatter parsing, merge, or reroute protocol;
- Adapter installation no longer reads the packaged skill tree and retains all
  managed installation, upgrade, drift, retention, and recovery guarantees;
- human documentation matches the new protocol and English/Chinese README files
  remain synchronized; and
- the focused suites and full test suite pass without cache or bytecode artifacts.

## Resulting flow

```text
global Adapter
  -> info --json: bind and verify the exact repository
  -> check --json: validate repository and return one Python-owned skill_catalog
  -> Agent selects one task from that catalog
  -> Agent loads canonical and selected repository authority bodies
  -> Agent performs the requested operation under the immutable root binding
```

There is one routing source, one deterministic parser implementation, and no
Agent-authored frontmatter extraction.

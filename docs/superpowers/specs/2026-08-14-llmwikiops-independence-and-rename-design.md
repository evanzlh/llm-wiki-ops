# LLMWikiOps Independence and Rename Design

**Date:** 2026-08-14
**Status:** Approved
**Source baseline:** `feat/portable-repo-mode` at `a8436ac`
**Destination repository:** `git@github.com:evanzlh/llm-wiki-ops.git`

## Summary

Turn the independently maintained `obsidian-wiki` fork into **LLMWikiOps**, a
standalone open-source project hosted in a new GitHub repository outside the
upstream fork network. Preserve the complete Git history, upstream attribution,
MIT license, and fork-base record while making the current 349-commit product
line the new `main` branch.

The public product name, distribution metadata, documentation, primary CLI, and
repository identity change to LLMWikiOps. The established Python import package
`obsidian_wiki` and repository protocol directory `.obsidian-wiki/` remain stable
in this migration. The old `obsidian-wiki` executable remains as a compatibility
alias so existing initialized knowledge repositories can upgrade safely.

## Product Identity

The canonical identity is:

```text
Name:        LLMWikiOps
Repository:  https://github.com/evanzlh/llm-wiki-ops
Distribution: llm-wiki-ops
Primary CLI: llmwikiops
Description: A deterministic, repository-native implementation of the LLM Wiki pattern.
```

LLMWikiOps is personal-first and open source. Its roadmap serves the maintainer's
real knowledge workflow first; public use and contribution do not require parity
with another LLM Wiki implementation or with the former upstream project.

The name deliberately includes `LLMWiki` to identify the implemented product
pattern and `Ops` to identify the deterministic setup, validation, transaction,
recovery, skill deployment, and repository-maintenance layer supplied by the
framework.

## Independence Model

The new GitHub repository is created as an empty ordinary repository, not through
GitHub's fork action. Pushing the existing history into it preserves Git objects
and commit attribution without preserving GitHub fork-network metadata.

The historical relationship remains explicit in three places:

- the existing upstream commits and authors in Git history;
- the MIT `LICENSE`; and
- `docs/fork.md`, which records `Ar9av/obsidian-wiki` and fork base
  `5ef66b6bec8b26bab6594ac37fb4d8371469fbab`.

The project does not fetch, merge, rebase onto, or accept future changes from the
former upstream. It does not submit pull requests to the former upstream. The
`upstream` Git remote is removed after the destination repository is verified.

## Goals

- Publish the current independently developed product as LLMWikiOps.
- Make the 349 commits after the recorded fork base the canonical `main` line.
- Keep commit content, topology, authorship, attribution, and license history.
- Make all current human-facing documentation describe LLMWikiOps first.
- Install `llmwikiops` as the canonical executable.
- Keep `obsidian-wiki` as a functional compatibility executable.
- Point package metadata, issue links, clone commands, and maintenance identity to
  `evanzlh/llm-wiki-ops`.
- Keep README and README_ZH behavior and examples aligned.
- Push only the intentional canonical branch and new-project release tags.
- Leave the old fork recoverable until the new repository is verified.

## Non-goals

- Creating a root commit or discarding the upstream Git history.
- Squashing the independent development history.
- Renaming the `obsidian_wiki` Python import package.
- Renaming `.obsidian-wiki/`, its configuration schema, managed-skill state, or
  repository discovery protocol.
- Migrating initialized knowledge repositories to a new configuration directory.
- Changing CLI behavior beyond adding the new entry point and changing identity
  text, help examples, and documentation.
- Importing GitHub issues, pull requests, stars, releases, or other platform
  metadata from the old fork.
- Mirroring every local branch, remote-tracking branch, or upstream release tag.
- Claiming that LLMWikiOps is an official continuation of the former upstream.

## Naming and Compatibility Boundaries

### Rename now

The following surfaces become LLMWikiOps in this project:

- README titles, summaries, clone paths, installation examples, and navigation;
- current human documentation and packaged runtime-skill prose;
- Python distribution name `llm-wiki-ops`;
- primary executable `llmwikiops`;
- project URLs and issue URLs;
- implementation identity constants and `--version` output;
- contributor instructions and current command examples; and
- source-repository references embedded in tests or package metadata.

### Preserve now

The following names are stable compatibility or historical surfaces:

- Python imports beginning with `obsidian_wiki`;
- the package-resource directory `obsidian_wiki/_data/`;
- `.obsidian-wiki/config.toml` and `.obsidian-wiki/local/`;
- schema keys and environment names whose rename would require repository
  migration;
- the former upstream name in attribution and historical design records;
- historical commit messages and old, explicitly historical documentation; and
- the `obsidian-wiki` console-script alias.

Tests must distinguish an accidental stale public-brand reference from an
intentional compatibility, protocol, attribution, or historical reference. No
unreviewed global search-and-replace is allowed.

## CLI Contract

Both entry points invoke the same implementation:

```toml
[project.scripts]
llmwikiops = "obsidian_wiki.cli:main"
obsidian-wiki = "obsidian_wiki.cli:main"
```

All current documentation and newly rendered runtime templates use `llmwikiops`.
The compatibility command has identical behavior and emits no warning during this
migration. Removal or deprecation requires a later design because initialized
repositories and installed skill copies may contain the old executable name.

Python callers continue to import `obsidian_wiki`. This avoids a broad internal
move with no user benefit and allows the public rename to be reviewed separately
from a future Python API migration.

## Repository Protocol Contract

Initialized knowledge repositories continue to contain:

```text
.obsidian-wiki/
|-- config.toml
|-- managed-skills.json
`-- local/
```

This directory is a versioned on-disk protocol, not merely a brand string. Changing
it would require dual discovery, collision rules, transactional migration, rollback,
and compatibility tests. That work is excluded from the present rename.

Documentation may explain that `.obsidian-wiki/` is the retained repository protocol
directory, but examples must not imply that users should rename it manually.

## Documentation and Attribution

`README.md` and `README_ZH.md` present LLMWikiOps as the project and retain a concise
line identifying its origin and fork base. `docs/fork.md` becomes the complete
independence and attribution record. It must state that:

- LLMWikiOps is not affiliated with or maintained by the former upstream;
- future upstream changes are not tracked;
- the full history and MIT license are preserved; and
- current support, issues, and releases belong to the LLMWikiOps repository.

Current docs use the new repository URL and primary command. Historical specs may
retain old names when they describe the system as it existed at the time; they are
not rewritten merely for branding.

## Git Migration

The local feature line is already a strict descendant of the old `main`:

```text
origin/main at 5ef66b6
    `-- feat/portable-repo-mode: 349 commits
```

Migration therefore fast-forwards local `main`; it does not reset or rewrite it:

```bash
git switch main
git merge --ff-only feat/portable-repo-mode
```

Remote migration is staged for recovery:

```bash
git remote rename origin old-fork
git remote add origin git@github.com:evanzlh/llm-wiki-ops.git
git remote remove upstream
git push -u origin main
```

Only `main` is pushed initially. Do not use `git push --mirror`: local auxiliary
branches and former-upstream tags are not part of the new repository's supported
surface. After clone, CI, content, ancestry, and default-branch verification, remove
the temporary `old-fork` remote. The old GitHub fork can be archived with a pointer
to the new repository and deleted later at the maintainer's discretion.

## Branch and Release Policy

The standalone repository uses `main` as its protected, releasable branch. New work
uses short-lived `feat/*`, `fix/*`, and `docs/*` branches and merges through the new
repository's own review process.

Former-upstream tags are not pushed to the new repository. The first accepted
standalone release starts the LLMWikiOps release line at `v0.1.0`. Because versioning
comes from Git tags, release verification must include a clean clone from the new
repository before publishing that tag.

## Testing and Acceptance

Implementation follows regression-first tests for identity and compatibility.
Acceptance requires:

- package metadata names `llm-wiki-ops` and points to the new repository;
- both `llmwikiops --version` and `obsidian-wiki --version` work and identify
  `evanzlh/llm-wiki-ops`;
- installed wheels contain the unchanged `obsidian_wiki` package resources;
- `.obsidian-wiki/` setup, discovery, validation, transactions, and recovery still
  pass their focused and full suites;
- current README and runtime examples prefer `llmwikiops`;
- README synchronization validation passes;
- tests classify every retained old-name occurrence as intentional;
- the full test suite passes without bytecode or pytest cache writes;
- local `main` is a fast-forward of the former base and matches the intended feature
  tip after merge;
- the new remote initially contains only the intended `main` branch;
- a clean clone from the new remote installs and reports the correct identity; and
- the destination GitHub repository is not marked as a fork.

## Recovery

Before changing branches or remotes, create a fresh Git bundle under `/tmp` that
contains all local branches. Until remote verification completes:

- retain `old-fork` as a fetch/push URL for the former fork;
- retain the local feature branch and auxiliary branches;
- do not delete or rewrite the old GitHub fork; and
- do not create the first LLMWikiOps release tag.

If the new push or clone verification fails, restore the original remote names from
the recorded pre-migration state or recover local branches from the bundle. Remote
cleanup begins only after all acceptance checks pass.

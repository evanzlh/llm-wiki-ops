# LLMWikiOps Protocol Rename Design

**Date:** 2026-08-14
**Status:** Approved in conversation; pending written-spec review
**Source baseline:** `main` at `24cdfd8abeffb572fa03fb79845464c2f52c2729`
**Development branch:** `feat/llmwikiops-protocol-rename`

## Summary

Rename every current external repository-protocol identifier from the former
product identity to LLMWikiOps. The canonical repository state directory becomes
`.llmwikiops/`; managed agent filenames, managed markers, extension identifiers,
temporary names, and user-facing environment variables move to the same identity.

This is an intentional hard cutover. LLMWikiOps does not detect, read, migrate,
merge, alias, or delete the former protocol. A repository containing only the
former state directory is treated as uninitialized. Users who want to use the new
protocol must explicitly run `llmwikiops setup` and review the resulting files.

The Python import package `obsidian_wiki` and the historical attribution to
`Ar9av/obsidian-wiki` remain unchanged. They are not external repository-protocol
identifiers and renaming them would create a separate Python API and history
migration.

## Goals

- Make `.llmwikiops/` the only repository state and discovery directory.
- Make `llmwikiops` the identity of every current managed filename, marker,
  extension ID, temporary prefix, lock path, sidecar, and user/script environment
  variable.
- Centralize protocol identity constants so future code does not scatter raw
  names across implementation modules.
- Preserve deterministic setup, fail-closed validation, transaction safety,
  packaged resource behavior, and repository-relative portability.
- Leave former-protocol files untouched and require explicit user initialization.
- Keep human docs, packaged runtime guidance, tests, and build artifacts aligned.

## Non-goals

- Automatically migrating or copying an existing former-protocol directory.
- Supporting both old and new paths, markers, filenames, IDs, or environment
  variables.
- Detecting or reporting a special conflict when both state directories exist.
- Renaming the `obsidian_wiki` Python package or its package-resource directory.
- Rewriting the historical `Ar9av/obsidian-wiki` attribution, Git history, or old
  design records.
- Creating a release tag as part of this change.

## Canonical Protocol Identity

The canonical mappings are:

| Surface | Canonical value |
|---|---|
| Repository state directory | `.llmwikiops/` |
| Repository configuration | `.llmwikiops/config.toml` |
| Local state | `.llmwikiops/local/` |
| Managed-skill inventory | `.llmwikiops/managed-skills.json` |
| User configuration | `~/.llmwikiops/config` |
| Agent rule/workflow basename | `llmwikiops.md` |
| Cursor rule basename | `llmwikiops.mdc` |
| Managed block marker prefix | `llmwikiops:managed:` |
| Git attributes marker prefix | `llmwikiops:gitattributes:` |
| Portable bootstrap marker | `llmwikiops:portable-bootstrap` |
| Brain-capture picker ID | `llmwikiops-raw` |
| User/script environment prefix | `LLMWIKIOPS_` |

The same identity applies to protocol-specific lock files, transaction roots,
temporary filename prefixes, test sidecars, probe markers, and other current
machine-readable names. The implementation plan must inventory their exact current
values before editing and give each one a concrete new value.

## Architecture

### Central protocol identity

Create one small internal module whose sole responsibility is defining stable
LLMWikiOps protocol names. At minimum it owns the state-directory basename,
configuration path, local-state path, managed-inventory path, managed agent
basenames, marker prefixes, and public environment-variable names.

Implementation modules import these constants instead of independently spelling
the protocol identity. Package identity such as `IMPLEMENTATION_ID` remains in
`obsidian_wiki/__init__.py`; the protocol module must not become a second source of
distribution metadata.

### Repository discovery and configuration

Repository-aware commands walk ancestors looking only for
`.llmwikiops/config.toml`. Global configuration resolves only
`~/.llmwikiops/config`. Configuration defaults use `.llmwikiops/local`, and
validation rejects the former local-state value as non-canonical.

A repository containing only the former directory follows the ordinary
uninitialized-repository error path. The error may instruct the user to run
`llmwikiops setup`, but it must not mention or inspect the former directory.

### Setup and managed files

`llmwikiops setup` creates only the new directory, configuration, local-state
layout, inventory, agent rule/workflow filenames, and new managed markers. It
retains the existing checks for unsafe links, special files, path escape,
collisions, owner content, transactional writes, and rollback.

The former directory and former managed files are ordinary owner content. Setup
must not read, overwrite, rename, remove, or use them as migration input. If a new
target collides with unsafe or invalid content, setup fails closed under the
existing safety model.

### Transactions and local state

Locks, write-ahead logs, transaction workspaces, recovery copies, skill upgrade
state, exports, generated-skill review artifacts, and other local products move
under `.llmwikiops/local/`. Protocol-specific temporary and sidecar names also use
the new prefix. Recovery only recognizes transactions created by the new
protocol.

### Runtime resources and extension

Packaged bootstrap templates generate `llmwikiops.md` and `llmwikiops.mdc` files
with new markers. Agent discovery mappings, manifests, parity checks, and
frontmatter machine IDs use the new identity. The brain-capture extension uses
`llmwikiops-raw`; its behavior and storage semantics otherwise remain unchanged.

Public shell examples and runtime instructions use `LLMWIKIOPS_*` variables. The
former variables are unset from the product contract and receive no fallback.

## Hard-Cutover Behavior

The implementation must preserve these negative guarantees:

- no ancestor search for the former configuration path;
- no fallback to the former user configuration;
- no old-to-new file or directory copy;
- no dual read or dual write;
- no automatic marker conversion;
- no environment-variable alias;
- no warning that depends on inspecting former-protocol content; and
- no deletion or cleanup of former-protocol content.

When both old and new protocol files exist, only new files participate. This is
not treated as a migration conflict because the former files are outside the
LLMWikiOps protocol.

## Error Handling and Safety

- Missing `.llmwikiops/config.toml` produces the existing uninitialized-repository
  class of error and points to `llmwikiops setup`.
- Invalid new configuration fails closed before writes.
- Unsafe `.llmwikiops` links, special files, and out-of-repository targets are
  rejected by the same deterministic safety checks used today.
- Setup never overwrites owner-authored content merely because it resembles a
  former managed file.
- Transaction rollback and recovery operate entirely within the new protocol
  paths.
- Tests that create former-protocol fixtures must verify byte-for-byte that those
  fixtures remain untouched.

## Documentation

`README.md` and `README_ZH.md` remain one synchronized surface. Current docs and
packaged runtime skills show only `.llmwikiops`, new managed filenames, new
markers where exposed, and `LLMWIKIOPS_*` variables.

The migration note must state plainly that this is an incompatible protocol
rename: LLMWikiOps does not migrate existing repositories, and users must run
setup explicitly. It must not suggest manually moving local transaction state as
an endorsed workflow.

Historical specs and the upstream attribution may retain former names when they
describe past behavior. The new design and regression tests may mention former
identifiers only to define the hard-cutover boundary and negative cases.

## Testing Strategy

Implementation is regression-first:

1. Add failing contract tests for every canonical protocol mapping.
2. Add hard-cutover tests proving former paths, markers, IDs, and environment
   variables are ignored and remain unmodified.
3. Update repository discovery, configuration, setup, check, doctor, transaction,
   recovery, skill inventory, runtime-context, and packaged-resource tests.
4. Add fresh setup tests that assert the complete new tree and absence of former
   managed names.
5. Add tracked-file identity audits that allow only:
   - the `obsidian_wiki` Python package/import path;
   - `Ar9av/obsidian-wiki` historical attribution;
   - historical specs and plans; and
   - explicit negative-test fixtures and the hard-cutover design record.
6. Verify README synchronization, the full pytest suite, wheel/sdist contents, and
   an isolated `uv tool install` from a clean clone.

The audit must be semantic and path-aware rather than an unreviewed global
replacement. It must reject current former-protocol references while avoiding
false positives from the retained Python package and attribution.

## Git and Publication

Development occurs on `feat/llmwikiops-protocol-rename`. After implementation,
specification review, code-quality review, and fresh full-suite evidence, local
`main` is advanced with `--ff-only` and only `main` is pushed to
`git@github.com:evanzlh/llm-wiki-ops.git`.

No auxiliary branch or tag is pushed. A recovery bundle is refreshed before the
branch and remote publication step.

## Acceptance Criteria

- Fresh setup creates `.llmwikiops/config.toml`, `.llmwikiops/local/`, and only
  new managed filenames and markers.
- Repository discovery and global configuration use only new paths.
- Existing former-protocol fixtures remain byte-for-byte unchanged and do not
  affect discovery or setup.
- New and old state directories may coexist; only `.llmwikiops/` is authoritative.
- Transactions, recovery, skill management, exports, and generated artifacts use
  the new local-state tree.
- Current environment variables use only `LLMWIKIOPS_*`.
- The extension uses `llmwikiops-raw` and retains its existing behavior.
- Current human docs and packaged guidance contain no former external protocol
  identifiers outside explicit incompatibility explanations.
- The retained `obsidian_wiki` package and `Ar9av/obsidian-wiki` attribution still
  pass package and history tests.
- README synchronization, full pytest, build, and clean-clone isolated installation
  all pass.
- The new remote receives only the verified `main` branch and no tag.

## Recovery

Before publishing, create and verify a fresh bundle containing all local branches.
Because the protocol change is intentionally incompatible, rollback means using a
previous LLMWikiOps commit or restoring Git refs from the bundle; it does not mean
mutating repositories that still contain former-protocol state.

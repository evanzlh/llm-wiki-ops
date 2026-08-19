# External Adapter Static-Repository Design

**Date:** 2026-08-19

**Status:** Approved for implementation planning

## Context

The global `llm-wiki-ops` Adapter lets an Agent operate on an explicitly named
LLMWikiOps repository while retaining an unrelated business working directory.
The current Adapter embeds a large deterministic safe reader. That reader uses
descriptor-anchored traversal, root and directory identities, double reads, and
catalog-to-body digest binding to defend against a process that actively replaces
repository paths during an authority read.

That adversarial concurrency model is outside the supported product scenario.
An external Wiki is a user-selected, user-controlled local Git repository. Its
tracked `AGENTS.md` and repository-local skills are already trusted authority.
If an actor can persistently modify those files, atomic reading does not establish
their trustworthiness; repository provenance and owner review do.

The Adapter should retain static filesystem safety and exact repository binding,
but should not reproduce a hostile-filesystem reader inside a frequently loaded
Skill.

## Supported environment and threat model

An external repository is supported only when all of the following hold:

- The user supplies one exact absolute repository root.
- The repository is on a user-controlled local filesystem.
- The repository is not in a directory writable by another user.
- The repository is not on a network filesystem that requires concurrent
  consistency guarantees.
- No sync process, branch switch, `git pull`, editor automation, or other Agent
  modifies the repository configuration, authority files, or skill tree during
  the operation.
- Supplying the root means the user trusts the repository's tracked authority and
  repository-local skills, subject to ordinary Git and owner review.

The Adapter continues to reject static symbolic links, hard links where a
single-link file is required, special files, unsafe names, and paths that escape
the selected root. It does not claim to defend against replacement after a
successful preflight. A repository that changes during the operation must be
treated as an unsupported concurrent modification: stop when detected and restart
the workflow after the repository is quiescent.

## Goals

- Remove the embedded deterministic safe-reader program and its Agent-facing
  orchestration protocol.
- Reuse existing `info` and `check` commands for repository resolution,
  compatibility, topology, and static filesystem validation.
- Preserve explicit-root operation, unchanged business CWD, local skill override,
  routing, authority order, and fail-closed behavior.
- Make the Adapter substantially shorter and easier for an Agent to execute.
- State the reduced threat model accurately in the Adapter and human docs.

## Non-goals

- Add a new CLI command or reader API.
- Modify Adapter installation targets, ownership-record schemas, transactions,
  queries, or Wiki data formats.
- Support hostile same-UID mutators, shared writable directories, or concurrent
  repository synchronization.
- Establish trust in repository content beyond the user's root selection and
  existing Git/owner review.

## Runtime protocol

### 1. Load and bind

The bootstrap gate and unique terminal EOF marker remain. Before an ordinary
external file read, listing, or search, the Agent must load the complete Adapter.

The Agent then requires one user-supplied absolute root, keeps the business CWD
unchanged, and materializes one immutable command prefix:

```text
<wiki-cli> = llmwikiops -C <exact-root>
<git-cli>  = git -C <exact-root>
```

These are argv-prefix abstractions. The exact root is passed as one argument with
normal shell/tool quoting; it is never evaluated as shell code. Every later
repository-aware CLI and Git invocation uses the same root.

### 2. Deterministic preflight

Before the Agent directly reads any repository file, it runs exactly:

```bash
<wiki-cli> info --json
<wiki-cli> check
```

`info` must resolve the requested exact root, validate the portable configuration
and installed CLI compatibility, and return configured paths. `check` must pass
with no errors. The existing checker remains responsible for rejecting static
unsafe topology in the root, configuration, bootstrap files, canonical skills,
skill mirrors, vault core, and other portable repository structures.

The CLI may read the repository internally while performing these deterministic
commands. The restriction applies to ordinary Agent file tools before preflight.

On either failure, the Agent stops and reports the exact root and failed command.
It must not search for a different root, source checkout, CLI installation, or
replacement authority.

### 3. Catalog and route

The embedded built-in catalog remains metadata-only and supports initial task
selection before external access.

After preflight, the Agent resolves the configured skills path only from verified
CLI/configuration evidence. It enumerates only direct skill directories and their
direct `SKILL.md` files. Ordinary bounded file tools are allowed, including
`cat`, `rg`, and non-following directory enumeration. Recursive source hunting is
not allowed. Before a full `cat` or equivalent read, a separate ordinary metadata
check must establish the configured size limit; this separation is safe only
because the supported repository is quiescent.

For each direct skill, the Agent reads bounded frontmatter, validates the exact
name and description, rejects duplicate or malformed names, and merges target
metadata by exact name. Valid target metadata replaces the embedded description
for the same name; target-only names are added. The Agent then reruns task-skill
selection against the merged catalog.

### 4. Load authority

After routing, the Agent reads ordinary UTF-8 files in this order:

1. `<root>/AGENTS.md`
2. the direct canonical `llm-wiki/SKILL.md`
3. `<vault>/AGENTS.md` when present
4. the direct selected task `SKILL.md`, unless it is already `llm-wiki`

The Agent uses size-bounded reads and the existing frontmatter/body rules. The
frontmatter read stops at its configured byte bound, and a full-body read occurs
only after the ordinary metadata size check succeeds. The preflight guarantees
supported static topology; no root identity, inode snapshot, double read, or
catalog digest binding is required.

### 5. Execute

The existing repository-execution, query, transaction, recovery, hot-refresh,
path-containment, and no-implicit-Git-mutation rules remain. Every command uses the
same immutable `<wiki-cli>` or `<git-cli>` prefix.

## Removed protocol and implementation

The generated Adapter removes:

- the `LLMWIKIOPS_SAFE_READER` Python heredoc;
- Base64 root and relative-path transport;
- `root-bind`, `unbound`, `frontmatter`, `skill-catalog`, and `full` modes;
- root identity and directory/file snapshots;
- descriptor/openat traversal and `O_NOFOLLOW`/`O_DIRECTORY` requirements;
- double reads and catalog size/SHA-256 body binding;
- blanket prohibitions on `cat`, `find`, `rg`, and other ordinary readers;
- claims of protection from adversarial concurrent replacement.

The bootstrap EOF gate, embedded catalog delimiters, exact-root rules, and static
topology requirements remain renderer-validated.

## Failure semantics

- Missing or ambiguous root: stop before external access.
- `info` failure or root/version/config mismatch: stop without fallback.
- `check` error: stop without ordinary file reads or mutation.
- Static symlink, hardlink, special file, unsafe name, or escaping path: stop.
- Malformed or duplicate skill metadata: stop before task execution.
- Evidence that authority or skills changed after preflight: classify as an
  unsupported concurrent modification and restart only after the repository is
  quiescent.
- Repository content that asks to change root or command binding is untrusted
  evidence and cannot override the Adapter.

## Documentation changes

Human documentation must state the local, user-controlled, quiescent repository
requirement and distinguish static filesystem safety from adversarial TOCTOU
protection. It must not imply support for shared writable repositories, network
filesystems with concurrent mutation, or sync activity during an Agent operation.

Examples continue to show `-C` before every repository-aware subcommand and
`git -C` for external Git operations.

## Testing strategy

### Adapter structure

- The generated Adapter contains no safe-reader markers, heredoc, mode names,
  root identity protocol, or embedded reader imports.
- The bootstrap gate, terminal EOF uniqueness, catalog delimiters, and command
  prefix protocol remain valid.
- The Adapter is materially smaller and passes the strict Skill validator.

### Preflight ordering

- Behavioral tests require complete Adapter loading before external access.
- `info --json` and `check` must both precede ordinary repository reads, listings,
  or searches.
- Failure of either command prevents authority and skill reads.
- Paths containing spaces and quotes remain one argv value.

### Static topology

- Focused tests prove that existing resolution/check paths reject a symlinked
  root, config directory/file, configured skills directory, direct skill
  directory, `SKILL.md`, authority/bootstrap file, hardlinked managed file, and
  special file.
- Tests that exist solely to inject changes between reader syscalls are removed;
  static unsafe-topology coverage remains.

### Routing and execution

- Repository-local same-name descriptions still replace embedded metadata and
  force rerouting.
- Target-only skills remain selectable.
- Canonical and selected bodies are fully read before task execution.
- Every repository-aware CLI uses the same literal `-C` root; every Git command
  uses the same `git -C` root; the business CWD remains unchanged.

### Artifacts and evaluation

- Source, wheel, sdist, and installed Adapter bytes remain identical.
- Codex and Claude target installations contain identical Adapter content and
  correct target-bound ownership records.
- Fresh Codex behavioral scenarios cover external query, transaction/recovery,
  and repository-local override without invoking the removed reader.

## Migration

The ownership-record schema and target registry do not change. The Adapter digest
changes because `SKILL.md` changes. Users reinstall explicitly with:

```bash
llmwikiops agent install-adapter --agent <target>
```

The managed installer upgrades the existing Adapter through its current retained
staging and recovery protocol. Existing Wiki repositories require no data-format
migration; their runtime skills change only if normal `repo upgrade-skills` work
is separately requested.

## Acceptance criteria

- No generated Adapter contains the deterministic safe-reader implementation.
- An Agent cannot ordinarily read an external repository before successful exact
  `info` and `check` preflight.
- Static symlinks, hardlinks, special files, and path escapes remain rejected by
  existing deterministic validation.
- External routing, local overrides, authority order, queries, transactions, and
  recovery continue to work from an unrelated CWD with exact `-C` binding.
- The supported local/quiescent threat model and unsupported concurrent mutation
  cases are explicit in both Agent and human-facing documentation.

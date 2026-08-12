> **Superseded (2026-08-12):** Current behavior is defined by the
> [Portable-Only Repository Design](2026-08-12-portable-only-design.md).

# Portable Setup Installation Compatibility Design

## Status

Approved in conversation on 2026-08-10. This document records the agreed
security and compatibility boundaries before implementation.

## Problem

The documented source installation command, `uv tool install .`, can install
wheel files as hard links to uv's cache. Portable setup rejects every hard-linked
canonical skill source, so a supported installation can produce a CLI that
cannot run `setup --portable`, `repo upgrade-skills`, or migration apply.

Portable setup also rejects an otherwise empty Git repository because `.git`
makes the target non-empty. The resulting error does not explain that setup
accepts only a missing directory, a truly empty directory, or an existing
portable repository. This is awkward for a Git-native workflow and forced the
operator to move `.git` by hand.

## Goals

- Preserve the hard-link rejection for canonical skill inputs and managed
  portable files.
- Make the one supported uv source-install flow produce single-link bundled
  data on every supported platform.
- Give an actionable reinstall command if incompatible bundled data is found.
- Allow setup in a repository root whose only entry is an ordinary `.git`
  directory, without changing any Git metadata.
- Preserve the existing refusal to adopt arbitrary non-portable content.
- Keep setup free of global configuration, global skills, `.venv`, commits,
  remotes, and `git init` side effects.

## Non-goals

- Supporting editable, package-index, remote-URL, or vendored installation.
- Allowing portable setup to merge into an arbitrary non-empty project.
- Initializing Git, creating commits, or configuring a remote.
- Supporting a symlink or `.git` file as the `.git`-only bootstrap case in this
  change. Those layouts remain rejected rather than being followed outside the
  target root.
- Relaxing hard-link validation for portable repository files or arbitrary
  canonical source trees.

## Design

### 1. Canonical installation uses copy mode

The supported fresh-install command becomes:

```bash
uv tool install --link-mode copy .
```

The supported upgrade command becomes:

```bash
uv tool install --force --link-mode copy .
```

This remains a non-editable build from the current local clone. It only changes
how uv materializes wheel files from its cache, so the installed CLI remains
independent of the source directory.

The existing source-tree validation continues to reject symlinks, special
files, and regular files whose link count exceeds one. When it rejects a hard
link, the error includes the exact copy-mode reinstall command. This diagnostic
is appropriate for the production CLI, whose canonical source is its bundled
wheel data, and still identifies the violated invariant for lower-level callers.

All active installation surfaces must use the same commands: the English and
Simplified Chinese READMEs, installation/contributor/fork documentation, CLI
reinstall hints, and relevant bundled skills. Historical design and plan
documents are not rewritten.

### 2. Setup accepts a `.git`-only target

Portable setup classifies a target into four states:

1. missing — scaffold with the existing staged directory replacement;
2. empty directory — scaffold with the existing staged replacement and empty
   target restoration on failure;
3. `.git`-only directory — scaffold through a staged top-level merge that keeps
   `.git` in place;
4. non-empty directory — repair it only if it is already a valid portable
   repository; otherwise reject it without mutation.

The `.git`-only state requires exactly one root entry named `.git`, and that
entry must be a real directory rather than a symlink or special file. Its
contents are opaque owner-managed Git metadata: setup neither scans nor rewrites
them.

For the staged merge, setup first builds and validates the complete portable
tree in a sibling temporary directory. It then moves each generated top-level
entry into the target while recording successful moves. If any move raises,
the recorded entries are moved back in reverse order, leaving the original
target containing only its untouched `.git`. Temporary staging is removed on
both success and handled failure. No generated path may collide because target
classification happens before staging and is rechecked before commit.

This adds exception-atomic behavior for the new case without moving or copying
the Git directory. It does not broaden setup to `.git` plus owner files.

### 3. Errors and documentation

The non-portable-target error explains the accepted new-target states and says
that arbitrary existing repositories require an explicit migration or a new
empty target. Human documentation recommends setup before `git init`, while
also documenting that a target containing only an ordinary `.git` directory is
supported. It states explicitly that portable setup never initializes Git.

README command changes remain translation-aligned. Detailed target-state
behavior belongs in `docs/installation.md` and `docs/cli.md`; the README stays a
landing page.

## Testing

### Installation regression

Extend the isolated uv-tool integration test to execute the exact documented
copy-mode command, move the source clone, run `obsidian-wiki setup --portable`
from the installed executable, and run `obsidian-wiki doctor` inside the new
repository. Assert that bundled skills resolve inside the uv tool directory and
contain no multiply linked regular files.

Keep a unit test that supplies a deliberately hard-linked canonical source and
asserts rejection before target creation. It must also assert that the error
contains `uv tool install --force --link-mode copy .`.

### Git metadata regression

Add tests that:

- scaffold a target containing only `.git` and verify the Git tree snapshot,
  modes, and inodes are unchanged;
- inject a failure during the staged top-level merge and verify exact rollback
  to the original `.git`-only target with no staging residue;
- continue to reject `.git` plus an owner file without changes;
- reject symlinked or non-directory `.git` bootstrap entries;
- verify CLI output and `doctor`/`check` for the successful case.

### Documentation and policy regression

Update installation-policy tests to require the copy-mode commands on active
surfaces and reject the old bare commands where they would instruct users.
Run the README translation drift checker in addition to focused and full pytest
suites.

## Acceptance Criteria

- A clean clone installed with `uv tool install --link-mode copy .` can be moved
  or removed before the installed CLI scaffolds and validates a portable repo.
- The same CLI accepts an existing target whose only entry is an ordinary
  `.git` directory and preserves that directory exactly.
- A target with any unrelated root entry remains unchanged and fails clearly.
- Hard-linked arbitrary canonical sources remain rejected with actionable
  reinstall guidance.
- English and Simplified Chinese installation commands stay aligned.
- The complete test suite passes without changing personal/global wiki state.

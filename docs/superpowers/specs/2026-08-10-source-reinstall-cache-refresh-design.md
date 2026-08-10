# Source Reinstall Cache Refresh Design

## Status

Approved in conversation on 2026-08-10. This document amends the upgrade
command in the Portable Setup Installation Compatibility design after final
validation exposed uv's local-directory build cache behavior.

## Problem

`uv tool install --force --link-mode copy .` forces installation of the tool
entry point, but it does not refresh a cached build of the local source
directory. In a real upgrade from this repository, uv 0.8.13 reported a
successful reinstall while the installed CLI remained on an older Git-derived
version and older bundled skills.

The existing fresh-install command is not affected because it installs into a
new tool environment. The gap applies when an existing tool is rebuilt from a
clone at the same filesystem path.

## Decision

Keep the supported fresh-install command unchanged:

```bash
uv tool install --link-mode copy .
```

Change the supported upgrade and remediation command to:

```bash
uv tool install --force --reinstall --link-mode copy .
```

`--reinstall` is the narrow uv option that reinstalls packages regardless of
their current state and implies a cache refresh. `--upgrade` was rejected
because it also permits broader dependency upgrades. Manual cache deletion was
rejected because it is stateful, error-prone, and unsuitable as the documented
workflow.

The canonical `SOURCE_REINSTALL_COMMAND` remains the single source for CLI and
portable validation diagnostics. Active English, Simplified Chinese, install,
and contributor documentation must display the amended command. Historical
design and plan documents remain immutable; this amendment records the change.

## Regression Test

Extend the isolated uv integration test to prove refresh behavior rather than
only checking a clean install:

1. install the copied source into an isolated uv tool environment;
2. change a bundled skill in that temporary source repository and commit it;
3. run `SOURCE_REINSTALL_COMMAND` from the same source path;
4. assert the installed bundled skill contains the changed content;
5. move the source directory and retain the existing version, info, list,
   single-link, portable setup, doctor, check, and no-global-state assertions.

The test must fail with the old force-only command because uv can reuse the
first cached local-directory build. It must pass with `--reinstall`.

## Acceptance Criteria

- Reinstalling from an updated clone at the same path installs the new bundled
  content without manual cache cleanup.
- The installed bundle still contains only single-link regular skill files.
- The installed CLI remains usable after the source directory moves.
- All active upgrade guidance and diagnostics use the canonical refreshed
  reinstall command.
- Focused and full test suites pass, and the real global CLI reports the current
  source version after reinstall.

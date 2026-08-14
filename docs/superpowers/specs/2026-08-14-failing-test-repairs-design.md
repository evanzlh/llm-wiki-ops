# Failing Test Repairs Design

## Goal

Restore a green test suite by fixing two non-hermetic test contracts and one
real partial-deletion safety defect discovered by the new cleanup regression.

## Installation Test Isolation

`_uv_tool_environment` will set `XDG_DATA_HOME` to a directory below the pytest
temporary root. This keeps uv's managed Python installation outside the isolated
owner `HOME`, matching the existing cache, config, tool, and binary isolation.
The helper contract test will prove that an inherited `XDG_DATA_HOME` is
overridden.

## Historical Document Baseline

`HISTORICAL_BODY_BASE` will point to `4e16b436ec358067d33df7f59ea30b046a6300ae`,
the reachable parent of the commit that added superseded banners. The existing
test will continue comparing all fourteen current document bodies with their
pre-banner versions.

## Safe Cleanup Preflight

Before `_purge_bound_sync_directory` removes any entry, a new descriptor-relative
validator will inspect the complete bound subtree. It will reject special files,
avoid following symlinks, and apply the same child open/fstat/attachment identity
checks used by destructive cleanup.

The validation must cover the whole subtree before purge begins. Validating and
purging one sibling at a time would still allow an earlier sibling's evidence to
be deleted before a later sibling exposes an unsafe file. The purge retains its
current checks so changes occurring after preflight still fail closed without
following replaced directories.

## Testing

The three currently failing tests provide the initial RED state. Add a nested
cross-subtree regression that places evidence in an earlier subtree and a FIFO in
a later subtree, then proves the entire target remains unchanged. Run the focused
installation, historical-document, cleanup, swap, and symlink tests before the
full suite.

---
name: graph-colorize
description: >
  Use when the user wants Obsidian graph nodes colored by tag, category,
  visibility, or an explicit custom mapping.
---

# Graph Colorize

This workflow explicitly edits `.obsidian/graph.json`; it is an Obsidian
configuration change, not a knowledge transaction. Do not run `llmwikiops transaction begin`:
this edit has no knowledge candidates and does
not update manifest shards, `index.md`, or `log.md`.

## Authority and approval

1. Resolve the nearest ancestor `.obsidian-wiki/config.toml`. If absent, stop with
   `llmwikiops setup [DIR]`; invalid config fails closed.
2. Read repository `AGENTS.md`, `.skills/llm-wiki/SKILL.md`, then this skill. The
   canonical protocol wins on conflict. Never accept another vault path.
3. Require an existing configured-vault `.obsidian/` directory. Do not create it;
   ask the user to open the vault in Obsidian once if missing.
4. Show the proposed mode and replacement `colorGroups`, and obtain explicit user approval
   before this subjective configuration edit. Warn that Obsidian may
   overwrite config on close; ask the user to close it or reload immediately after.

## Modes

- `by-tag` (default): count tags in safe eligible Markdown, exclude visibility tags,
  and emit at most the ten most frequent groups as `tag:#<tag>`.
- `by-category`: emit existing non-empty `concepts`, `entities`, `skills`,
  `references`, `synthesis`, `projects`, and `journal` folders in that order, using
  the exact query `path:"<folder>"`.
- `by-visibility`: emit `visibility/pii`, `visibility/internal`, then
  `visibility/public`, using exact queries `tag:#visibility/pii`,
  `tag:#visibility/internal`, and `tag:#visibility/public`, so the most restrictive
  first match wins.
- `combined`: visibility groups first, then non-visibility tag groups.
- `custom`: honor the approved explicit query/color mapping after validating every
  query as a string and every color as a six-digit hexadecimal RGB value.
- `clear`: replace the array with `[]` after approval.

Use this stable, colorblind-friendly palette in order:
`#4E79A7`, `#F28E2B`, `#E15759`, `#76B7B2`, `#59A14F`, `#EDC948`,
`#B07AA1`, `#FF9DA7`, `#9C755F`, `#BAB0AC`. Convert a color to packed RGB with
`int(hex_without_hash, 16)` and store it as `{"a": 1, "rgb": <integer>}`.
For top tags, sort by count descending and then normalized tag ascending. Given the
same metadata and mode, group ordering and JSON values must be deterministic. Validate
the final `colorGroups` as an array of objects containing exactly a string `query`
and `color` object with numeric `a` and integer RGB in `0..16777215`.

## Safe backup and atomic edit

Use `.obsidian-wiki/local/obsidian-config-backups/<timestamp>/graph.json` for the
backup. `.obsidian-wiki/local/` is ignored local state. Before any read or write:

- resolve both source and backup beneath the repository without following links;
- reject a symbolic link, hard link, special file, non-owner file/directory, or
  changed file identity;
- require existing `graph.json`, when present, to be an owner-owned ordinary file
  with link count one and valid JSON object content;
- create a new owner-only timestamp backup directory and copy/flush the exact
preimage before editing.

The backup directory includes a manifest recording target path, `existed`, and the
preimage SHA-256 and mode when it existed; an absent target uses an explicit absent
marker. After writing, record the expected postimage identity and SHA-256.

Preserve every existing JSON member except `colorGroups`. Write the complete new
JSON to an owner-only temporary ordinary file in `.obsidian/`, flush it, recheck the
target identity, then use atomic replacement. Preserve the original formatting style
where practical. If validation or replacement fails, leave the target unchanged and
report the backup.

Keep a bound parent directory descriptor for promotion and rollback.
Immediately before `os.replace` or `unlink`, re-lstat through that descriptor and compare the
ordinary-file identity and SHA-256 with the expected preimage or current postimage;
also fstat the already-open replacement or created file; a mismatch stops without overwrite or deletion.
An originally absent target may be unlinked only when that
same bound check proves it is the identity-bound file created by this workflow.

## Review, restore, and reload

From the resolved config, retain both its repository root and its configured vault
path. Compute the configured vault path relative to the repository root, verify that
it is contained and has no `..` component, then append `.obsidian/graph.json`; call
the result `CONFIG_PATH`, for example
`<vault-relative>/.obsidian/graph.json`. Do not assume the vault directory is named
`wiki` and do not pass an absolute host path to Git.

From the repository root, run this argv-safe, path-scoped review and show it to the
user:

```bash
git --literal-pathspecs diff -- "$CONFIG_PATH"
```

`CONFIG_PATH` is one validated repository-relative path argument, not shell text or
a glob. The owner, not this workflow, decides whether to commit any tracked config
change; never commit, push, or publish it.

To undo, select an explicit backup and require the current target to match the
manifest's expected postimage identity and SHA-256; concurrent change stops restore.
If `existed` is true, atomically restore the validated preimage and mode. If false,
verify the created postimage and delete only that ordinary single-link file. After edit
or restore, tell the user to reload Obsidian with Cmd/Ctrl+R and verify the graph.
If the result is not accepted, restore rather than stacking unapproved changes.

Report mode, group count, target, backup path, diff status, and reload status. Do not
modify wiki pages, `log.md`, `index.md`, `hot.md`, or `.manifest.json`.

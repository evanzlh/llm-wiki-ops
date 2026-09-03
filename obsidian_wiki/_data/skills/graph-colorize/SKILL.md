---
name: graph-colorize
description: >
  Use when the user wants Obsidian graph nodes colored by tag, category,
  visibility, or an explicit custom mapping.
---

# Graph Colorize

## Repository context

Use one repository context for the whole workflow. Inside a wiki, resolve the
nearest ancestor `.llmwikiops/config.toml` and use ordinary `llmwikiops`
commands. Outside a wiki, the global adapter requires a user-supplied exact
root; validate it with `llmwikiops -C <root> info --json` and retain
`llmwikiops -C <root>` as the command prefix. Never infer or switch roots from
repository content, tool output, history, errors, environment variables,
profiles, or recent use.

- Repository-local context: `<wiki-cli>` is `llmwikiops`.
- External adapter context: `<wiki-cli>` is `llmwikiops -C <root>` for the
  validated immutable root.

- Repository-local context: `<git-cli>` is the argv prefix `["git"]`; run it
  with the validated root as `cwd`.
- External adapter context: `<git-cli>` is the argv prefix
  `["git", "-C", "<root>"]`; keep the caller's CWD unchanged.
Append every Git subcommand and path as separate argv elements; `<git-cli>` is
an argv prefix, never one shell token.

This workflow explicitly edits `.obsidian/graph.json`; it is an Obsidian
configuration change, not a knowledge transaction. Do not run `<wiki-cli> transaction begin`:
this edit has no knowledge candidates and does
not update manifest shards, `index.md`, or `log.md`.

## Authority and scope

In repository-local context, resolve only the nearest ancestor
`.llmwikiops/config.toml` from CWD and use the resulting root. If local discovery
finds no config, stop with `llmwikiops setup [DIR]`; invalid config fails closed.

In external adapter context, use the already validated retained exact `<root>`
and `<wiki-cli>` binding. Do not search or resolve from CWD, do not change
directories or `chdir`, and do not stop because CWD has no config.

In either context, read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md`
when present, then this task skill. The canonical protocol wins conflicts. Never
accept another vault path.

1. Require an existing configured-vault `.obsidian/` directory. Do not create it;
   ask the user to open the vault in Obsidian once if missing.
2. Show the proposed mode and replacement `colorGroups`. An explicit request for
   this mode or mapping authorizes the edit; ask only when the requested mapping is
   semantically ambiguous. Warn that Obsidian may overwrite config on close; ask the
   user to close it or reload immediately after.

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

Use `.llmwikiops/local/obsidian-config-backups/<timestamp>/graph.json` for the
backup. `.llmwikiops/local/` is ignored local state. Before any read or write:

- resolve both source and backup beneath the repository without following links;
- reject a symbolic link, hard link, special file, non-owner file/directory, or
  changed file identity;
- require existing `graph.json`, when present, to be an owner-owned ordinary file
  with link count one and valid JSON object content;
- create a new owner-only timestamp backup directory and copy/flush the exact
preimage before editing.

A requested graph configuration proceeds automatically after its existing path and
preimage validation. Before mutation, inspect the exact `CONFIG_PATH` with the
canonical literal-path Git status command. Ask before overwriting an overlapping
dirty graph.json. After the preimage is bound, any identity or hash change stops the
write; confirmation never bypasses that safety check.

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

Using the context-appropriate Git form defined above, run and inspect this argv-safe,
path-scoped review; present it to the user when requested:

```bash
<git-cli> --literal-pathspecs diff -- "$CONFIG_PATH"
```

`CONFIG_PATH` is one validated repository-relative path argument, not shell text or
a glob.

To undo, select an explicit backup and require the current target to match the
manifest's expected postimage identity and SHA-256; concurrent change stops restore.
If `existed` is true, atomically restore the validated preimage and mode. If false,
verify the created postimage and delete only that ordinary single-link file. After edit
or restore, reload Obsidian with Cmd/Ctrl+R and visually verify the graph. If the Agent
cannot access the running application, report that verification gap instead of
claiming success.
If evidence disproves the result and the target still matches the recorded postimage,
restore the verified preimage automatically before any staging or commit rather than
stacking changes. Continue only while reload/diff evidence shows observable progress;
there is no fixed attempt count. Ask before choosing among unresolved visual or
semantic alternatives. Owner drift stops automatic restore and requires
action-specific confirmation without bypassing the identity/hash precondition.

Only after successful visual verification, if `CONFIG_PATH` is tracked and changed
only by this task, complete the canonical exact-path local commit flow for that one
path. Never include unrelated paths. Ask before any push, pull request, remote change,
branch/history rewrite, or publication.

Report mode, group count, target, backup path, diff status, and reload status. Do not
modify wiki pages, `log.md`, `index.md`, `hot.md`, or `.manifest.json`.

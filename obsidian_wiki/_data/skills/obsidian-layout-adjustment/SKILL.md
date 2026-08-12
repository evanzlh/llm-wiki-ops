---
name: obsidian-layout-adjustment
description: >
  Use when changing or debugging Obsidian appearance, layout, active CSS snippets,
  panes, tabs, sidebars, note surfaces, properties, backlinks, graph, or icons.
---

# Obsidian Layout Adjustment

This workflow explicitly edits files under `.obsidian/`; those are Obsidian
configuration changes, not a knowledge transaction. Do not run `obsidian-wiki transaction begin`:
these edits have no knowledge candidates and do
not update manifest shards, `index.md`, or `log.md`.

## Authority and subjective approval

Resolve the nearest ancestor `.obsidian-wiki/config.toml`. If absent, stop with
`obsidian-wiki setup [DIR]`. Read repository `AGENTS.md`, then
`.skills/llm-wiki/SKILL.md`, then this skill; the canonical protocol wins on conflict.
Invalid config fails closed, and an invocation cannot select another vault.

Read `<vault>/.obsidian/appearance.json` and treat `enabledCssSnippets` as the active
styling source of truth. Require `.obsidian/` to exist. Explain the visible-object to
Obsidian-layer mapping, scoped files, and intended visual effect, then obtain explicit user approval
before a subjective edit. Also ask before switching themes, disabling
plugins/snippets, moving properties, or substantially changing typography/density.

Read `references/workflow-reference.md` when the object is ambiguous, the change did
nothing, the surface is still wrapped/not lifted/unreadable, or an accepted design is
being refactored.

## Operating loop

1. Inspect `appearance.json`, active snippets, and the precise current selector block.
2. Map the phrase to visible object, Obsidian layer, selector/settings surface, change
   type, and owning layer. Distinguish tab headers from the markdown view header.
3. Back up every file to be edited under
   `.obsidian-wiki/local/obsidian-config-backups/<timestamp>/`, preserving its
   repository-relative `.obsidian/` path.
4. Patch one owning stage, shell, header, wrapper, or child. Format CSS without
   changing selector order unless the approved change requires it.
5. Reload or focus Obsidian, screenshot the exact affected area, and compare it to
   the request. A valid stylesheet is not visual proof.
6. If evidence disproves the change, atomically restore the backup or change the
   ownership model. If one direction fails twice, restore before trying another.
7. Refactor only after the user accepts the visual result.

## Backup and edit safety

`.obsidian-wiki/local/` is ignored local state. Inspect every source, target, and
backup component without following links. Reject a symbolic link, hard link, special
file, non-owner path, escaping resolution, or identity change. Create a new
owner-only timestamp backup directory and flush ordinary single-link preimages before
editing.

Write each approved complete replacement through an owner-only temporary ordinary
file in the target directory, flush it, recheck the target, and use atomic rename.
Never alter app bundles, plugin source, installed theme source, or vault knowledge to
force a visual effect. To restore, validate the selected backup, back up the current
target, and atomically replace it; never copy through a link.

## Layer and change model

Translate natural language before editing. Typical mappings include:

| Visible object | Owning surface |
| --- | --- |
| tabs above a note / plus | workspace tab headers / new-tab control |
| arrows, book, dots | markdown view header |
| far-left icons | ribbon / side dock and active icon state |
| file/folder buttons | file explorer tree rows |
| note edge, shadow, lift | stage, pane shell, gutter, overflow |
| right sidebar / backlinks | right workspace split / backlink group wrapper |
| graph | graph plugin leaf/canvas |

For color, handle background, text/icon contrast, active, hover, and focus together.
For lift or rounding, inspect stage/shell/gutter/overflow relationships. “Nothing
changed” signals wrong ownership, clipping, coverage, override, or missed reload;
“still wrapped” usually signals both wrapper and child styling.

## Review and closeout

From the resolved config, retain its repository root and configured vault path. For
each edited file, compute the configured vault path relative to the repository root,
verify containment and reject `..`, then append that file's `.obsidian/` path. Call
the resulting single repository-relative path `CONFIG_PATH`; never assume the vault
directory is named `wiki` and never use an absolute host path as a Git pathspec.

From the repository root, review every edited file separately with argv-safe literal
pathspec handling:

```bash
git --literal-pathspecs diff -- "$CONFIG_PATH"
```

`CONFIG_PATH` is one validated argument, not shell text or a glob. Show each diff to
the user. The owner decides whether to commit tracked config; this workflow never
commits, pushes, or publishes. Reload Obsidian with Cmd/Ctrl+R after edit or restore
and screenshot-check the result.

Report changed files, backup paths, phrase-to-layer mapping, `git diff` status,
reload/screenshot result, and anything unverified. Do not mutate wiki pages,
`log.md`, `index.md`, `hot.md`, or `.manifest.json`.

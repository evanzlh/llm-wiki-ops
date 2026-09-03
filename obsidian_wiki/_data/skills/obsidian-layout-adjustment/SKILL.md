---
name: obsidian-layout-adjustment
description: >
  Use when changing or debugging Obsidian appearance, layout, active CSS snippets,
  panes, tabs, sidebars, note surfaces, properties, backlinks, graph, or icons.
---

# Obsidian Layout Adjustment

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

This workflow explicitly edits files under `.obsidian/`; those are Obsidian
configuration changes, not a knowledge transaction. Do not run `<wiki-cli> transaction begin`:
these edits have no knowledge candidates and do
not update manifest shards, `index.md`, or `log.md`.

## Authority and scope

In repository-local context, resolve only the nearest ancestor
`.llmwikiops/config.toml` from CWD and use the resulting root. If local discovery
finds no config, stop with `llmwikiops setup [DIR]`; invalid config fails closed.

In external adapter context, use the already validated retained exact `<root>`
and `<wiki-cli>` binding. Do not search or resolve from CWD, do not change
directories or `chdir`, and do not stop because CWD has no config.

In either context, read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md`
when present, then this task skill. The canonical protocol wins conflicts, and an
invocation cannot select another vault.

Read `<vault>/.obsidian/appearance.json` and treat `enabledCssSnippets` as the active
styling source of truth. Require `.obsidian/` to exist. Explain the visible-object to
Obsidian-layer mapping, scoped files, and intended visual effect. An explicit request
for that effect authorizes the scoped edit. Ask when the mapping remains ambiguous,
and before switching themes, disabling plugins/snippets, moving properties, or
substantially changing typography/density.

Read `references/workflow-reference.md` when the object is ambiguous, the change did
nothing, the surface is still wrapped/not lifted/unreadable, or an accepted design is
being refactored.

## Operating loop

1. Inspect `appearance.json`, active snippets, and the precise current selector block.
2. Map the phrase to visible object, Obsidian layer, selector/settings surface, change
   type, and owning layer. Distinguish tab headers from the markdown view header.
3. Back up every file to be edited under
   `.llmwikiops/local/obsidian-config-backups/<timestamp>/`, preserving its
   repository-relative `.obsidian/` path.
4. Patch one owning stage, shell, header, wrapper, or child. Format CSS without
   changing selector order unless the approved change requires it.
5. Reload or focus Obsidian, screenshot the exact affected area, and compare it to
   the request. A valid stylesheet is not visual proof.
6. If evidence disproves the change, atomically restore the verified preimage when
   no owner drift exists. Change the ownership model only when evidence supports it,
   and continue only while the reload/diff state shows observable progress.
7. Refactor only after screenshot evidence verifies the requested visual result; ask
   when acceptance depends on an unresolved subjective choice.

## Backup and edit safety

`.llmwikiops/local/` is ignored local state. Inspect every source, target, and
backup component without following links. Reject a symbolic link, hard link, special
file, non-owner path, escaping resolution, or identity change. Create a new
owner-only timestamp backup directory and flush ordinary single-link preimages before
editing. Store a backup manifest for every target with path, `existed`, preimage
SHA-256, and mode, using an explicit absent marker when the target did not exist.
Record the expected postimage identity and SHA-256 after each edit. Local backup
parents/directories use mode `0700` and backup files use `0600` where supported.

A requested layout change proceeds automatically after its existing path and
preimage validation. Before mutation, inspect every exact `CONFIG_PATH` with the
canonical literal-path Git status command and ask before overwriting an overlapping
dirty path. Ask before overwriting concurrently modified CSS. Any identity or hash
change after binding stops the write; confirmation never bypasses that precondition.

Write each approved complete replacement through an owner-only temporary ordinary
file in the target directory, flush it, recheck the target, and use atomic rename.
Never alter app bundles, plugin source, installed theme source, or vault knowledge to
force a visual effect. To restore, validate the selected backup, back up the current
target, and require the current identity and hash to equal the recorded postimage;
concurrent change stops restore. Atomically replace an originally existing target
with its preimage and mode. For an originally absent target, validate the postimage
then delete only that created ordinary single-link file. Never copy through a link.

Keep a bound parent directory descriptor for every promotion and rollback.
Immediately before `os.replace` or `unlink`, re-lstat through that descriptor and
compare identity and SHA-256 with the expected current preimage or postimage, and
fstat the open replacement or created file; a mismatch stops without overwrite or deletion.
For an originally absent target, unlink only the identity-bound file this
workflow created; a name match alone is insufficient.

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

Using the context-appropriate Git form above, review every edited file separately with argv-safe literal
pathspec handling:

```bash
<git-cli> --literal-pathspecs diff -- "$CONFIG_PATH"
```

`CONFIG_PATH` is one validated argument, not shell text or a glob. Inspect each diff
and present it to the user when requested. Reload Obsidian with Cmd/Ctrl+R after edit
or restore and screenshot-check the result. If the result is disproved, restore
automatically before any staging or commit only while the current target matches the
recorded postimage and no owner drift exists. Ask before choosing among unresolved
visual or semantic alternatives; owner drift requires action-specific confirmation
without bypassing the identity/hash precondition.

Only after successful screenshot verification, for tracked files changed only by this
task, complete the canonical exact-path local commit flow, validating and staging each
path separately and leaving unrelated paths untouched. Ask before any push, pull
request, remote change, branch/history rewrite, or publication.

Report changed files, backup paths, phrase-to-layer mapping, `<git-cli> diff` status,
reload/screenshot result, and anything unverified. Do not mutate wiki pages,
`log.md`, `index.md`, `hot.md`, or `.manifest.json`.

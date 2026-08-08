---
name: wiki-update
description: >
  Sync the current project's knowledge into the Obsidian wiki. Use this skill from any project
  when the user says "update wiki", "sync to wiki", "save this to my wiki", "update obsidian",
  or wants to distill what they've been working on into their knowledge base. This is the
  cross-project skill that lets you push knowledge from wherever you are into the vault. Accepts
  inline named-vault routing like "@work update wiki" via the shared Config Resolution Protocol.
---

# Wiki Update — Sync Any Project to Your Wiki

You are distilling knowledge from the current project into the user's Obsidian wiki. This skill works from any project directory, not just the obsidian-wiki repo.

## Before You Start

1. **Resolve config** — follow the canonical Config Resolution Protocol in
   `llm-wiki/SKILL.md`: explicit `@name`, nearest ancestor
   `.obsidian-wiki/config.toml`, then `.env`, personal global config, and setup
   guidance. This gives `OBSIDIAN_VAULT_PATH`, `OBSIDIAN_WIKI_REPO`,
   `OBSIDIAN_LINK_FORMAT` (`wikilink` or `markdown`), and optional QMD settings.
   In portable mode, keep computed absolute repository paths in memory and
   never write them into committed files. Works from any project directory.
2. **Select the manifest protocol from the resolved mode.** Personal mode reads
   the monolithic manifest v1 project entry and preserves its absolute
   `source_cwd` plus existing vault-relative source behavior. Portable mode
   uses manifest v2 through `obsidian-wiki cache-check` and `cache-update`;
   every authoritative input is named by a repository-relative Source ID.
   Never hand-edit a v2 marker or shard. In portable mode, manifest v2 schema 1
   requires exactly one configured source root even though the TOML field is a
   list.
3. Read `$OBSIDIAN_VAULT_PATH/index.md` to know what the wiki already contains.

When writing internal links in Steps 4–5, apply the link format from `llm-wiki/SKILL.md` (Link Format section) using the `OBSIDIAN_LINK_FORMAT` value.

## Step 1: Understand the Project

Figure out what this project is by scanning the current working directory:

- `README.md`, docs/, any markdown files
- Source structure (frameworks, languages, key abstractions)
- `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml` or whatever defines the project
- Git log (focus on commit messages that signal decisions, not "fix typo" stuff)
- Claude memory files if they exist (`.claude/` in the project)

Derive a clean project name from the directory name.

In Portable Repository mode, the scan may help you understand the working
tree, but only ordinary files below the configured `sources` root may become
durable provenance. A live URL or external filesystem path is not a durable
Source ID. First capture any necessary external material as a small, reviewable
ordinary Git snapshot below `sources`; Git LFS pointers are unsupported.

## Step 2: Compute the Delta

For personal manifest v1, check the project entry in `.manifest.json`:

- **First time?** Full scan. Everything is new.
- **Synced before?** Look at `last_commit_synced`. Before computing the delta, verify the stored SHA is still reachable:
  ```bash
  git merge-base --is-ancestor <last_commit_synced> HEAD
  ```
  - **Exit 0 (ancestor):** Safe. Run `git log <last_commit_synced>..HEAD --oneline` to see what changed.
  - **Exit 1 (not an ancestor — rebase or force-push occurred):** The stored SHA is no longer in this branch's history. Warn the user: *"Stored commit `<sha>` is no longer reachable — branch may have been rebased or force-pushed. Falling back to full scan."* Then treat as first-time sync: re-scan everything and update `last_commit_synced` to the current HEAD SHA at the end of Step 6.

If nothing meaningful changed since last sync, tell the user and stop.

For portable manifest v2, run `obsidian-wiki cache-check` on the authoritative
source files. Its `new`, `modified`, and `unchanged` lists use each file's
repository-relative Source ID. Compile only `new` and `modified` inputs; the
manifest hash, not a clone-specific absolute project path, defines the delta.

## Step 3: Decide What to Distill

This is the core question from Karpathy's pattern: **what would you want to know about this project if you came back in 3 months with zero context?**

Worth distilling:

- Architecture decisions and *why* they were made
- Patterns discovered while building (things you'd Google again otherwise)
- What tools, services, APIs the project depends on and how they're wired together
- Key abstractions, how they connect, what the mental model is
- Trade-offs that were evaluated, what was picked and why
- Things learned while building that aren't obvious from reading the code

Not worth distilling:

- File listings, boilerplate, config that's obvious
- Individual bug fixes with no broader lesson
- Dependency versions, lock file contents
- Implementation details the code already says clearly
- Routine changes anyone could read from the diff

The heuristic: **if reading the codebase answers the question, don't wiki it. If you'd have to re-derive the reasoning by reading git blame across 20 commits, wiki it.**

## Step 4: Distill into Wiki Pages

### Project-specific knowledge

Goes under `$VAULT/projects/<project-name>/`:

```
projects/<project-name>/
├── <project-name>.md          ← project overview (named after the project, NOT _project.md)
├── concepts/                  ← project-specific ideas, architectures
├── skills/                    ← project-specific how-tos, patterns
└── references/                ← project-specific source summaries
```

The overview page (`<project-name>.md`) should have:
- What the project is (one paragraph)
- Key concepts and how they connect
- Links to project-specific and global wiki pages

### Global knowledge

Things that aren't project-specific go in the global categories:

| What you found | Where it goes |
|---|---|
| A general concept learned | `concepts/` |
| A reusable pattern or technique | `skills/` |
| A tool/service/person | `entities/` |
| Cross-project analysis | `synthesis/` |

### Page format

Every page needs YAML frontmatter:

```markdown
---
title: >-
    Page Title
category: concepts
tags: [tag1, tag2]
sources: [projects/<project-name>]
summary: >-
    One or two sentences (≤200 chars) describing what this page covers.
provenance:
  extracted: 0.6
  inferred: 0.35
  ambiguous: 0.05
base_confidence: 0.59
lifecycle: draft
lifecycle_changed: TIMESTAMP_DATE
created: TIMESTAMP
updated: TIMESTAMP
---

Use folded scalar syntax (summary: >-) for title and summary to keep frontmatter parser-safe across punctuation (:, #, quotes) without escaping rules.
Keep the title and summary contents indented by two spaces under summary: >-.

# Page Title

- A fact the codebase or a doc actually states.
- A reason the design works this way. ^[inferred]

Use [[wikilinks]] to connect to other pages.
```

**Write a `summary:` frontmatter field** on every new/updated page (1–2 sentences, ≤200 chars), using `>-` folded style. For project sync, a good summary answers "what does this page tell me about the project I wouldn't guess from its title?" This field powers cheap retrieval by `wiki-query`.

**Apply provenance markers** per `llm-wiki` (Provenance Markers section). For project sync specifically:

- **Extracted** — anything visible in the code, config, or a doc/commit message: file structure, dependencies, function signatures, what a file does.
- **Inferred** — *why* a decision was made, design rationale, trade-offs, "the team chose X because Y" — unless a commit message, doc, or ADR states it explicitly.
- **Ambiguous** — when the code and docs disagree, or when there's clearly an in-progress migration with two patterns living side by side.

Compute the rough fractions and write the `provenance:` block on every new/updated page.

### Updating vs creating

- If a page already exists in the vault, **merge** new information into it. Don't create duplicates.
- If you're adding to an existing page, update the `updated` timestamp and add the new source.
- Check `index.md` to see what's already there before creating anything new.

## Step 5: Cross-link

After creating/updating pages:

- Add `[[wikilinks]]` from new pages to existing related pages
- Add `[[wikilinks]]` from existing pages back to the new ones where relevant
- Link the project overview to all project-specific pages and relevant global pages

## Step 6: Update Tracking

### Personal mode — manifest v1

Add or update this project's entry in the monolithic `.manifest.json`:

```json
{
  "projects": {
    "<project-name>": {
      "source_cwd": "/absolute/path/to/project",
      "last_synced": "TIMESTAMP",
      "last_commit_synced": "abc123f",
      "pages_in_vault": ["projects/<project-name>/<project-name>.md", "..."]
    }
  }
}
```

The absolute `source_cwd`, `last_commit_synced`, and project collection are
personal v1 behavior and remain valid there.

### Portable mode — manifest v2

After compiling each authoritative source, update its one shard with:

```bash
obsidian-wiki cache-update "$OBSIDIAN_VAULT_PATH" <source> --pages <page1> [page2 ...]
```

The CLI derives the repository-relative Source ID and writes below
`<vault>/.manifest/sources/`. Do not reconstruct a monolithic project collection,
manually edit JSON, or add model, agent, API, or generation-tool provenance
fields to the marker or shards.

### Update `index.md`

Add entries for any new pages created.

### Update `log.md`

Append:
```
- [TIMESTAMP] WIKI_UPDATE project=<project-name> pages_updated=X pages_created=Y source_cwd=/path/to/project
```

### Update `hot.md`

Read `$OBSIDIAN_VAULT_PATH/hot.md` (create from the template in `wiki-ingest` if missing). Rewrite **Recent Activity** with what was just synced — last 3 operations max. Update **Active Threads** if this project is an ongoing focus. Update **Key Takeaways** with the most important architectural insight or decision surfaced during this sync. Update `updated` timestamp.

Write conceptually: "Synced obsidian-wiki — added wiki-capture and wiki-research skills, core new capabilities are autonomous web research and conversation capture."

## Step 7: Refresh QMD Wiki Index (optional — requires `QMD_WIKI_COLLECTION`)

**GUARD: If `$QMD_WIKI_COLLECTION` is empty or unset, skip this step.** The markdown vault is the source of truth; QMD is only a search index.

Run this step only after pages, the mode-appropriate manifest tracking,
`index.md`, `log.md`, and `hot.md` have been written. If Step 2 found no
meaningful changes and the sync stopped early, do not refresh QMD.

This refresh currently requires the local QMD CLI. Use `$QMD_CLI` if set; otherwise use `qmd`. If the CLI is unavailable or returns an error, do not roll back the wiki update; report that the wiki was updated but QMD refresh was skipped or failed.

For CLI refresh:

```bash
${QMD_CLI:-qmd} update
```

If the output says new hashes need vectors, or if pages were created/updated and embeddings may be stale, run:

```bash
${QMD_CLI:-qmd} embed
```

Verify at least one created or materially updated page is visible in the wiki collection:

```bash
${QMD_CLI:-qmd} get "qmd://$QMD_WIKI_COLLECTION/projects/<project-name>/<page>.md" -l 5
```

If the exact `qmd://` path is uncertain, use:

```bash
${QMD_CLI:-qmd} ls "$QMD_WIKI_COLLECTION" | rg "<project-name>"
```

Record QMD refresh in the final report as one of:
- `QMD refreshed: update + embed + verified`
- `QMD skipped: QMD_WIKI_COLLECTION unset`
- `QMD skipped: qmd CLI unavailable`
- `QMD failed: <short error summary>`

## Tips

- **Be aggressive about merging.** If the project uses React Server Components, don't create a new page if `concepts/react-server-components.md` already exists. Update the existing one and add this project as a source.
- **Consult the tag taxonomy.** Read `$VAULT/_meta/taxonomy.md` if it exists, and use canonical tags.
- **Don't copy code.** Distill the *knowledge*, not the implementation. "This project uses a debounced search pattern with 300ms delay" is useful. Pasting the actual debounce function is not.
- **Project overview is the anchor.** The `<project-name>.md` file is what you'd read to get oriented. Make it good.

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
   Config resolution supplies concrete runtime values in agent memory; it does
   not export them into the parent shell. In Portable Repository mode, keep
   computed absolute paths in memory and never persist them in repository
   content or configuration.
2. **Read owner instructions** — after the vault resolves, read
   `<resolved-vault-path>/AGENTS.md` when it exists and apply its conventions.
3. **Select the terminal workflow.** Select one terminal workflow after the
   shared analysis and page-preparation steps: **Portable Repository
   completion** or **Personal mode completion**. Never mix their page-write or
   tracking operations. The Portable branch is this skill's local application
   of the canonical Portable Write Protocol in `llm-wiki/SKILL.md`. The parent
   agent owns mode resolution, the owner `AGENTS.md`, source closure, and
   terminal completion. Any delegated scan or analysis is read-only and
   returns findings to that parent; a worker must not write pages, start or
   finish a transaction, edit tracking files, run QMD, or publish Git state.
   The shared analysis and page-preparation steps are strictly read-only: they
   may propose source material and page content in memory, but they do not
   create source files or mutate the vault.
4. **Select read-side tracking by mode.** Personal mode reads the monolithic
   manifest v1 project entry and preserves its absolute `source_cwd` plus
   existing vault-relative source behavior. Portable mode inspects manifest v2
   only through CLI read commands and names every authoritative input by a
   repository-relative Source ID. Never hand-edit the portable marker, shards,
   or operation records. Portable manifest v2 schema 1 requires exactly one
   configured source root even though the TOML field is a list.
5. Read `<resolved-vault-path>/index.md` to understand existing wiki content.

Apply the resolved `OBSIDIAN_LINK_FORMAT` to every internal link in Steps 4–5,
using the Link Format section of `llm-wiki/SKILL.md`: `wikilink` means Obsidian
`[[wikilinks]]`, while `markdown` means standard Markdown links.

## Step 1: Understand the Project

Figure out what this project is by scanning the current project. In Portable
Repository mode, discover the repository root and keep it as the command CWD
for every CLI and Git command:

- `README.md`, docs/, any markdown files
- Source structure (frameworks, languages, key abstractions)
- `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml` or whatever defines the project
- Git log (focus on commit messages that signal decisions, not "fix typo" stuff)
- Claude memory files if they exist (`.claude/` in the project)

Derive a clean project name from the directory name.

In Portable Repository mode, the scan may help you understand the working
tree, but only ordinary files below the configured `sources` root may become
durable provenance. A live URL or external filesystem path is not a durable
Source ID. Record any need for a durable text source as an in-memory proposal;
do not create it during shared analysis. Git LFS pointers are unsupported.

## Step 2: Compute the Personal Delta or Gather Portable Inputs

For personal manifest v1, check the project entry in `.manifest.json`:

- **First time?** Full scan. Everything is new.
- **Synced before?** Look at `last_commit_synced`. Before computing the delta, verify the stored SHA is still reachable:
  ```bash
  git merge-base --is-ancestor <last_commit_synced> HEAD
  ```
  - **Exit 0 (ancestor):** Safe. Run `git log <last_commit_synced>..HEAD --oneline` to see what changed.
  - **Exit 1 (not an ancestor — rebase or force-push occurred):** The stored SHA is no longer in this branch's history. Warn the user: *"Stored commit `<sha>` is no longer reachable — branch may have been rebased or force-pushed. Falling back to full scan."* Then treat as first-time sync: re-scan everything and update `last_commit_synced` to the reviewed current HEAD during Personal mode completion.

If the Personal Git delta contains no meaningful change, tell the user and
stop without entering Personal mode completion.

For Portable Repository mode, do not run a cache command or take a no-change
exit during this shared step. Through Steps 2–5, carry the selected source
paths, pending source proposal, and prepared page/removal changes in memory
into Portable Repository completion. The Portable branch materializes any
proposed source before it computes the manifest-v2 delta.

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

## Step 4: Prepare Wiki Pages

Prepare the complete page contents in agent memory. Do not mutate the live
vault during this shared step. The selected terminal workflow decides whether
these pages are written as transaction candidates or directly to a Personal
vault.

### Project-specific knowledge

Uses these vault-relative paths under `projects/<project-name>/`:

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

Category is a semantic path contract:

- `projects/<project-name>/<project-name>.md` uses `category: projects`.
- `projects/<project-name>/concepts/` uses `category: concepts`.
- `projects/<project-name>/skills/` uses `category: skills`.
- `projects/<project-name>/references/` uses `category: references`.

The validator checks `category` against the page's semantic path, including
project overview and nested project-category paths.

### Global knowledge

Things that aren't project-specific go in the global categories:

| What you found | Where it goes |
|---|---|
| A general concept learned | `concepts/` |
| A reusable pattern or technique | `skills/` |
| A tool/service/person | `entities/` |
| A factual source summary | `references/` |
| Cross-project analysis | `synthesis/` |

Global pages use the category matching their top-level semantic directory:
`concepts`, `skills`, `entities`, `references`, or `synthesis`.

### Global `concepts/` page example

Every page needs YAML frontmatter. This example is specifically for a global
page below `concepts/`; use the path/category rules above for project pages and
other global categories:

```markdown
---
title: >-
    Page Title
category: concepts
tags: [tag1, tag2]
sources: [<authoritative-source-id>]
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

Use internal links in the resolved format to connect to other pages.
```

**Write a `summary:` frontmatter field** on every new/updated page (1–2 sentences, ≤200 chars), using `>-` folded style. For project sync, a good summary answers "what does this page tell me about the project I wouldn't guess from its title?" This field powers cheap retrieval by `wiki-query`.

Use actual authoritative source identities, never the illustrative placeholder.
Portable pages cite only repository-relative Source IDs that will belong to the
transaction. Personal pages retain the established concrete source-path
identity from manifest v1.

**Apply provenance markers** per `llm-wiki` (Provenance Markers section). For project sync specifically:

- **Extracted** — anything visible in the code, config, or a doc/commit message: file structure, dependencies, function signatures, what a file does.
- **Inferred** — *why* a decision was made, design rationale, trade-offs, "the team chose X because Y" — unless a commit message, doc, or ADR states it explicitly.
- **Ambiguous** — when the code and docs disagree, or when there's clearly an in-progress migration with two patterns living side by side.

Compute the rough fractions and write the `provenance:` block on every new/updated page.

### Updating vs creating

- If a page already exists in the vault, **prepare a merged replacement**.
  Don't create duplicates.
- Preserve its existing `created` value. Add the new source when it supports
  the page; the terminal workflow supplies the correct `updated` timestamp.
- When a repeat sync makes a page obsolete, prepare a reviewed deletion set
  and replacement pages that remove obsolete backlinks. Do not delete
  anything during shared preparation.
- Check `index.md` and the actual category path before preparing a new page.

## Step 5: Prepare Cross-links

Before selecting the terminal workflow:

- Add internal links in the resolved format from prepared pages to existing
  related pages.
- Prepare replacement pages for any existing pages that need back-links. In
  Portable mode, every such replacement expands source closure because the
  page's existing `sources` remain authoritative.
- Link the prepared project overview to all project-specific pages and relevant
  global pages.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode
and the parent has applied the owner `AGENTS.md`. Keep the repository root as
the command CWD throughout. The absolute `candidate_vault` is a runtime
destination only: keep it in agent memory, do not `cd` into it, and never
persist it in a page, manifest, operation record, skill, or configuration.

1. **Establish any missing source authority.** Before source closure, the
   parent may write a small, reviewable text source snapshot below the
   configured `sources` root when project evidence is external or outside that
   root. For an external-only first update, a pending proposal is work: the
   parent must materialize and review it before `cache-check`. Never take a
   no-change exit while a source proposal is pending. It is a source file, not
   a Git snapshot: do not commit or publish it, and do not ask a delegated
   analysis worker to create it. A failed source-snapshot creation or review
   stops before any transaction.
2. **Check the Portable delta after materialization.** After every selected
   source file exists, run from the repository-root CWD:

   `obsidian-wiki cache-check --configured <source1> [source2 ...] --json --pretty`.

   Inspect `missing` first. Treat its values as reported `missing` entries and
   report the exact returned values: an explicitly selected absent path may be
   absolute, while tracked missing entries are repository-relative Source IDs.
   Distinguish those shapes before choosing remediation. For an absolute
   selected path, correct the selection or materialize/restore that ordinary
   file. For a tracked Source ID, restore the corresponding source or complete
   a supported migration. If any entry is missing, do not start a transaction
   or mutate the live vault; apply only the stated source remediation, then
   rerun `cache-check --configured`. Never treat a missing-only result as no
   change.

   After `missing` is empty, take a no-change exit only when the result has no
   `new` or `modified` entries and there are no prepared page creations,
   updates, or removals requiring a transaction. Never bypass a pending source
   proposal. Otherwise continue; the manifest hash, not a clone-specific
   absolute path, defines the delta.
3. **Compute source closure before `transaction begin`.** A transaction's
   source set is immutable. Include every existing `sources` Source ID from
   each live page that will be updated or deleted, plus every new authoritative
   source used by the prepared project pages. Each source must now be an
   ordinary file below the configured repository `sources` root; never invent
   a Source ID. All candidate `sources` fields must be non-empty subsets of the
   transaction's repository-relative Source IDs.
   Preserve valid Unicode Source IDs and knowledge filenames exactly,
   including CJK paths such as `sources/项目/架构决策.md`; do not transliterate
   or Unicode-normalize them.
4. **Begin once with the complete closure** from the repository-root CWD:
   `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty`.
   Record the returned `transaction_id`, absolute `candidate_vault`,
   `started_at`, and canonical transaction Source IDs. If closure was
   incomplete, abort the active transaction and begin a new one; a Source ID
   cannot be added after begin.
5. **Apply transaction timestamps and write candidates.** For a new page, set
   `created = updated = started_at`. For an update, preserve the existing
   `created` and set `updated = started_at`. Write every new or replacement
   knowledge page only at its final vault-relative path below the returned
   `candidate_vault`, without changing CWD. Commit will not rewrite the
   reviewed candidate bytes.
6. **Declare every removal** with
   `obsidian-wiki transaction delete <id> <vault-relative-page.md>`. If a
   requested mutation cannot be represented by a candidate knowledge page or
   declared deletion, report it as unsupported and do not mutate the live
   vault.
7. **Validate before commit.** Run
   `obsidian-wiki transaction validate <id> --json --pretty`. Review every
   warning; warnings do not block commit. Fix every issue and rerun validation,
   because issues do block commit.
8. **Commit only a passing report** with
   `obsidian-wiki transaction commit <id> --json --pretty`.
9. **Use status-aware recovery.** On a JSON command failure, follow only the
   trusted `recovery.preferred_action` or a reported alternative whose
   prerequisites hold. Confirm the retained record with
   `obsidian-wiki transaction list --json`: its `recommended_action` must
   agree, and the chosen command must appear in `allowed_actions`.
   - An active transaction after validation or another preflight failure has
     not changed the live vault. Fix the candidate and validate again, or run
     `obsidian-wiki transaction abort <id> --json`; `retry`, `restore`, and
     `discard` are invalid while it is active.
   - After a mutation failure, inspect the retained status and workspace. A
     `promoting` record permits only its reported
     `obsidian-wiki transaction restore <id> --json`. For a `failed` record,
     prefer its reported `obsidian-wiki transaction retry <id> --json` after
     fixing the cause. Use restore or
     `obsidian-wiki transaction discard <id> --json` only when the action is
     listed in `allowed_actions` and its prerequisites hold. Follow the
     reported actions for `complete` and `restored` records too.
   - A configuration or begin failure with no trusted transaction ID, or an
     empty transaction list, has no retained recovery action. Fix the cause and
     begin anew. Never begin a replacement while the retained outcome is
     ambiguous.
10. **Refresh local hot context only after commit succeeds or recovery is fully
   resolved.** Resolution may be a successful retry, abort, restore, discard,
   or another allowed terminal recovery action whose prerequisites held. Do
   not run the hot flow while a transaction outcome is ambiguous. Once the
   gate is satisfied, run `obsidian-wiki hot status --json`. If it is stale,
   run `obsidian-wiki hot inputs --json --pretty`, use only those bounded
   inputs to write the semantic `hot.md` as the agent, and then run
   `obsidian-wiki hot mark-current --json`. This ignored local write happens
   outside the transaction; it is not a transaction candidate.
11. **Report and stop.** Report created, updated, and removed pages, validation
   warnings, recovery performed, and the local hot-cache result.

Portable quality checks:

- [ ] Source closure includes every retained source from updated/deleted pages and every new authoritative source before begin.
- [ ] Every candidate has valid frontmatter, a non-empty repository-relative `sources` subset, a concise `summary:`, and correct transaction timestamps.
- [ ] New and updated project pages retain working cross-links and no deletion breaks the prospective graph.
- [ ] Every validation issue is fixed and every warning is reviewed before commit.
- [ ] No live central file, manifest shard, operation page, or unsupported path was edited by the agent.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Hold the
concrete vault and QMD values in agent memory: config resolution does not export these values into the parent shell. For a new page, set `created` and
`updated` to the current ISO timestamp. For an update, preserve `created` and
set `updated` to the current ISO timestamp. Personal sources follow Personal
manifest v1 and cache rules; the Portable text-source snapshot step does not
apply here.

### Personal Git delta and direct page writes

Confirm that the Git delta analyzed in Step 2 still ends at the intended HEAD;
if HEAD changed during preparation, inspect the new commits before recording a
sync boundary. Write the prepared pages directly below
`<resolved-vault-path>` at their final vault-relative paths. Apply prepared
back-link replacements there too, so they remove obsolete backlinks before
deleting reviewed obsolete pages by exact path. Only after every deletion and
page write succeeds may Personal tracking begin; on any failure, stop without
manifest, central-file, cache, or QMD changes. Record as `last_commit_synced`
only the reviewed HEAD whose changes were compiled.

### Personal manifest v1 and cache

Add or update this project's entry in
`<resolved-vault-path>/.manifest.json`:

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
Personal manifest v1 behavior. Preserve all unrelated v1 entries, remove
deleted pages from `pages_in_vault`, and remove or update source-to-page
mappings so they match the surviving page set. For every authoritative
Personal source compiled into pages, record its content hash and current page
mapping with the concrete vault path:

```bash
obsidian-wiki cache-update <resolved-vault-path> <source> --pages <page1> [page2 ...] --json --pretty
```

If the CLI is unavailable, update only the Personal v1 equivalent after
computing the source SHA-256; never use that fallback against portable state.

### Personal central files

**`<resolved-vault-path>/index.md`** — add entries for new pages, refresh
summaries for updated pages, and remove their `index.md` entries when pages
were deleted.

**`<resolved-vault-path>/log.md`** — append:

```text
- [TIMESTAMP] WIKI_UPDATE project=<project-name> pages_updated=X pages_created=Y pages_removed=Z source_cwd=/absolute/path/to/project
```

Record deletion counts in `log.md`, including zero, so repeat syncs are
auditable.

**`<resolved-vault-path>/hot.md`** — create it from the `wiki-ingest` template
if missing. Rewrite **Recent Activity** with the last three operations at most,
update **Active Threads** when the project remains active, and put the most
important architectural insight in **Key Takeaways**. Update its timestamp.
Write the conceptual change, not a file list, and reflect the conceptual
removal in `hot.md` when pages or backlinks were deleted.

### Personal QMD refresh

Run Personal tracking and QMD only after deletions and writes succeed. If the
page-operation gate failed, do not run this section.

If the concrete resolved QMD wiki collection is empty or unset, skip this
step. Otherwise, run it only after page, manifest v1, cache, `index.md`,
`log.md`, and `hot.md` writes succeed. If Step 2 stopped for no meaningful
change, do not refresh QMD. Use the concrete resolved CLI path, or `qmd` when
no override was configured. A QMD failure does not roll back the completed
Personal wiki update; report it.

```bash
<resolved-qmd-cli> update
<resolved-qmd-cli> embed
<resolved-qmd-cli> get "qmd://<resolved-qmd-wiki-collection>/projects/<project-name>/<page>.md" -l 5
```

If the exact path is uncertain:

```bash
<resolved-qmd-cli> ls <resolved-qmd-wiki-collection> | rg "<project-name>"
```

Record QMD refresh in the final report as one of:

- `QMD refreshed: update + embed + verified`
- `QMD skipped: QMD_WIKI_COLLECTION unset`
- `QMD skipped: qmd CLI unavailable`
- `QMD failed: <short error summary>`

Personal quality checks:

- [ ] All prepared page and back-link writes succeeded before tracking changed.
- [ ] Reviewed obsolete pages and backlinks were removed before tracking, and every removal is absent from page mappings and the index.
- [ ] Manifest v1 preserves unrelated state and records the reviewed Git HEAD.
- [ ] Personal cache mappings, `index.md`, `log.md`, and `hot.md` reflect the same page set.
- [ ] QMD was refreshed and verified when configured, or its skip/failure was reported.
- [ ] The final report lists created, updated, and removed pages plus QMD status.

## Tips

- **Be aggressive about merging.** If the project uses React Server Components, don't create a new page if `concepts/react-server-components.md` already exists. Update the existing one and add this project as a source.
- **Consult the tag taxonomy.** Read `<resolved-vault-path>/_meta/taxonomy.md`
  if it exists, and use canonical tags.
- **Don't copy code.** Distill the *knowledge*, not the implementation. "This project uses a debounced search pattern with 300ms delay" is useful. Pasting the actual debounce function is not.
- **Project overview is the anchor.** The `<project-name>.md` file is what you'd read to get oriented. Make it good.

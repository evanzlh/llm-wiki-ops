---
name: tag-taxonomy
description: >
  Enforce consistent tagging across the Obsidian wiki using a controlled vocabulary.
  Use this skill when the user says "fix my tags", "normalize tags", "clean up tags",
  "tag audit", "what tags should I use", "tag taxonomy", or whenever you're creating or
  updating wiki pages and need to choose the right tags. Also trigger when the user asks
  about tag conventions, wants to add a new tag to the taxonomy, or says "my tags are a mess".
  Always consult this skill's taxonomy file before assigning tags to any wiki page.
---

# Tag Taxonomy — Controlled Vocabulary for Wiki Tags

You are enforcing consistent tagging across the wiki by normalizing tags to a controlled vocabulary.

## Before You Start

1. **Resolve config** — follow the Config Resolution Protocol in `llm-wiki/SKILL.md` (inline `@name` override → walk up CWD for `.env` → `~/.obsidian-wiki/config` → prompt setup). This gives `OBSIDIAN_VAULT_PATH`

   The parent agent resolves config and mode, reads the owner `AGENTS.md`, and
   keeps audit and page-preparation work read-only. Select one terminal workflow
   after the shared read-only analysis; workers never choose the mode or write.
   This routes every Portable write through the canonical Portable Write Protocol.
2. Read `$OBSIDIAN_VAULT_PATH/_meta/taxonomy.md` — this is the canonical tag list
3. Read `index.md` to understand the wiki's scope

## The Taxonomy File

The canonical tag vocabulary lives at `$OBSIDIAN_VAULT_PATH/_meta/taxonomy.md`. It defines:

- **Canonical tags** — the tags that should be used
- **Aliases** — common alternatives that should be mapped to the canonical form
- **Rules** — max 5 tags per page, lowercase/hyphenated, prefer broad over narrow
- **Migration guide** — specific renames for known inconsistencies

**Always read this file before tagging.** It's the source of truth.

## Reserved System Tags

`visibility/` is a reserved tag group with special rules. These tags are **not** domain or type tags and are managed separately from the taxonomy vocabulary:

| Tag | Purpose |
|---|---|
| `visibility/public` | Explicitly public — shown in all modes (same as no tag) |
| `visibility/internal` | Team-only — excluded in filtered query/export mode |
| `visibility/pii` | Sensitive data — excluded in filtered query/export mode |

**Rules for `visibility/` tags:**
- They do **not** count toward the 5-tag limit
- Only one `visibility/` tag per page
- Omit entirely when content is clearly public — no tag needed
- Never add `visibility/internal` just because content is technical; use it only for genuinely team-restricted knowledge
- When running a tag audit, report `visibility/` tag usage separately — do not flag them as unknown or non-canonical

When normalizing tags, leave `visibility/` tags untouched — they are not subject to alias mapping.

## Mode 1: Tag Audit

When the user wants to see the current state of tags:

### Step 1: Scan all pages

```
Glob: $VAULT_PATH/**/*.md (excluding _archives/, .obsidian/, _meta/)
Extract: tags field from YAML frontmatter
```

### Step 2: Build a tag frequency table

For each tag found, count how many pages use it. Flag:

- **Unknown tags** — not in the taxonomy's canonical list
- **Alias tags** — using an alias instead of the canonical form (e.g., `nextjs` instead of `react`)
- **Over-tagged pages** — pages with more than 5 tags
- **Untagged pages** — pages with no tags or empty tags field

### Step 3: Report

```markdown
## Tag Audit Report

### Summary

- Total unique tags: 47
- Canonical tags used: 32
- Non-canonical tags found: 15
- Pages over tag limit (5): 3
- Untagged pages: 2

### Non-Canonical Tags Found

| Current Tag | → Canonical | Pages Affected |
| ----------- | ----------- | -------------- |
| `nextjs`    | `react`     | 4              |
| `next-js`   | `react`     | 2              |
| `robotics`  | `ml`        | 1              |
| `windows98` | `retro`     | 3              |

### Unknown Tags (not in taxonomy)

| Tag          | Pages | Recommendation                   |
| ------------ | ----- | -------------------------------- |
| `flutter`    | 1     | Add to taxonomy under Frameworks |
| `kubernetes` | 2     | Add to taxonomy under DevOps     |

### Over-Tagged Pages

| Page                   | Tag Count | Tags                 |
| ---------------------- | --------- | -------------------- |
| `entities/jane-doe.md` | 8         | ai, ml, founder, ... |
```

## Portable Repository completion

If the selected intent was Mode 1 audit-only, return the audit report and stop:
no transaction, no normalization, and no central-file mutation.

Use this branch only after Portable Repository mode was resolved. Keep the
repository root as the command CWD; keep the absolute `candidate_vault` only in
memory and do not `cd` into it.

For Mode 2 normalization, if normalization produces no page changes, report no
changes and stop before source closure. In this no-op case, do not create an
empty transaction or operation journal, and do not refresh `hot.md`.

Before computing closure, fail closed if the requested logical operation
requires any `_meta/taxonomy.md` change. In that case the entire logical
operation is unsupported: stop before `transaction begin` or any page mutation,
and do not partially normalize pages while leaving the vocabulary unchanged.

1. Compute complete source closure before beginning: the set union of all
   existing `sources` Source IDs from every updated or deleted live page and all
   repository-relative Source IDs any candidate `sources` field will cite. This
   existing source closure applies to page tag changes; these are never compiled
   vault page paths. Preserve valid Unicode and CJK spellings exactly.
2. Run `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty`
   once with the closure; retain `id`, `started_at`, and `candidate_vault`.
3. Write candidate replacements or new knowledge pages at final vault-relative
   paths below `candidate_vault`. New pages use
   `created = updated = started_at`; updates preserve the existing `created` and
   set `updated = started_at`. `_meta/taxonomy.md` remains a live central file,
   not a transaction knowledge candidate.
4. Declare reviewed page removals with
   `obsidian-wiki transaction delete <id> <vault-relative-page.md>`.
5. Run `obsidian-wiki transaction validate <id> --json --pretty`. Review every
   warning and Fix every issue before continuing.
6. Run `obsidian-wiki transaction commit <id> --json --pretty` only after the
   report passes.
7. Use status-aware recovery on an unclear failure. Follow only
   `recovery.preferred_action`, `recommended_action`, and `allowed_actions` for
   `obsidian-wiki transaction abort <id> --json`, `retry <id> --json`,
   `restore <id> --json`, or `discard <id> --json`. With no trusted transaction
   ID, or while the outcome is ambiguous, stop and report.
8. Only after commit succeeds or recovery is fully resolved, run
   `obsidian-wiki hot status --json`. If stale, run
   `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to
   write the semantic `hot.md` as the agent, then run
   `obsidian-wiki hot mark-current --json`.
9. Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`,
   write `hot.md` as part of the transaction, refresh Personal QMD tracking,
   create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

If the selected intent was Mode 1 audit-only, return the audit report and stop:
no transaction, no normalization, and no central-file mutation.

Use this branch only when config resolution selected Personal mode. For Mode 2
normalization, if there are no page changes, report no changes and stop without
tracking or hot-cache writes. Otherwise continue with the normalization,
taxonomy, and tracking workflow below. Personal central files, QMD refresh, and
Git snapshot rules remain active.

## Mode 2: Tag Normalization

When the user wants to fix the tags:

### Step 1: Run audit (above)

### Step 2: Apply fixes

For each page with non-canonical tags:

1. Read the page
2. Replace alias tags with their canonical form from the taxonomy
3. If page has > 5 tags, suggest which to drop (keep the most specific/relevant ones)
4. Write the updated frontmatter

**Example:**

```yaml
# Before
tags: [nextjs, ai, ml-engineer, windows98, creative-coding, game, 8-bit, portfolio]

# After
tags: [react, ai, ml, retro, generative-art]
```

### Step 3: Handle unknowns

For tags that aren't in the taxonomy and aren't aliases:

- If the tag is used on 2+ pages, suggest adding it to the taxonomy
- If the tag is used on 1 page, suggest replacing it with the closest canonical tag
- Ask the user before making changes to unknown tags

### Step 4: Update taxonomy

If new canonical tags were agreed upon, append them to `_meta/taxonomy.md` in the correct section.

## Mode 3: Tagging a New Page

When you're creating a wiki page and need to choose tags:

1. Read `_meta/taxonomy.md`
2. Select up to 5 tags that best describe the page:
   - 1-2 **domain tags** (what subject area)
   - 1 **type tag** (what kind of thing)
   - 0-1 **project tags** (if project-specific)
   - 0-1 additional descriptive tags
3. Use only canonical tags — never aliases
4. If no existing tag fits, check if it's worth adding to the taxonomy

## Mode 4: Adding a New Tag

When the user wants to add a tag to the vocabulary:

1. Check if an existing tag already covers the concept (suggest it if so)
2. If genuinely new, determine which section it belongs in (Domain, Type, Project)
3. Add it to `_meta/taxonomy.md` with:
   - The canonical tag name
   - What it's used for
   - Any aliases to redirect

## After Any Tag Operation

Append to `log.md`:

```
- [TIMESTAMP] TAG_AUDIT tags_normalized=N unknown_tags=M pages_modified=P
```

Or for normalization:

```
- [TIMESTAMP] TAG_NORMALIZE tags_renamed=N pages_modified=M new_tags_added=P
```

**`hot.md`** — Read `$OBSIDIAN_VAULT_PATH/hot.md` (create from the template in `wiki-ingest` if missing). Update **Recent Activity** with a one-line summary — e.g. "Tag audit: normalized 14 tags across 28 pages; 2 new canonical tags added." Keep the last 3 operations. Update `updated` timestamp.

## QMD Refresh After Vault Writes

QMD is a search index, not the source of truth. If `$QMD_WIKI_COLLECTION` is empty or unset, skip this step. Run it only after this skill has written or rewritten vault markdown. If QMD refresh fails, do not roll back the vault changes; report the QMD status separately.

Use `$QMD_CLI` if set; otherwise use `qmd`.

```bash
${QMD_CLI:-qmd} update
```

If the output says vectors are needed or embeddings may be stale, run:

```bash
${QMD_CLI:-qmd} embed
```

Verify the collection with either:

```bash
${QMD_CLI:-qmd} ls "$QMD_WIKI_COLLECTION"
```

or, when a specific page path is known:

```bash
${QMD_CLI:-qmd} get "qmd://$QMD_WIKI_COLLECTION/<page>.md" -l 5
```

Record one of:
- `QMD refreshed: update + embed + verified`
- `QMD refreshed: update only + verified`
- `QMD skipped: QMD_WIKI_COLLECTION unset`
- `QMD skipped: qmd CLI unavailable`
- `QMD failed: <short error summary>`

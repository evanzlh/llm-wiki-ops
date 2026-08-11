# Ingesting URL Sources

Reference for the `wiki-ingest` skill when the source is a **web URL** rather than a local file.
Triggered by `/ingest-url <url>`, "add this URL", "ingest this link", "save this page", or a pasted
URL with "add this" / "save this to my wiki".

Where the page lands depends on both the resolved configuration mode and project
context. Portable Repository mode compiles a repository snapshot into supported
knowledge-page candidates; Personal mode retains the project-or-`misc/` affinity
workflow. Config resolution, the content trust boundary, and completion rules are
the same as the main `wiki-ingest/SKILL.md` — follow those; this file only covers
URL-specific mechanics.

## Step U0: Detect Current Project

Before fetching anything, determine whether the user is working inside a specific project.

**Detection order (first match wins):**

1. **Git remote name** — run `git remote get-url origin 2>/dev/null` from the current working directory. Strip the host, org, and `.git` suffix to get the repo name. Example: `https://github.com/acme/my-app.git` → `my-app`.
2. **Package metadata** — if no git remote, check `package.json` (`name` field), `pyproject.toml` (`[project] name`), `Cargo.toml` (`[package] name`), `go.mod` (module path last segment), in that order.
3. **Directory name** — if none of the above work, use the basename of the current working directory.
4. **No project context** — if the current directory IS the obsidian-wiki repo itself, or if detection produces a name that matches the wiki vault directory, treat it as "no project context". Portable mode targets global `references/`; Personal mode falls back to `misc/`.

**Normalise the project name:** lowercase, replace spaces and underscores with `-`, strip leading dots.

Once you have a candidate name, check whether
`<resolved-vault-path>/projects/<project-name>/` exists. Config resolution gives
the agent this concrete value in memory; it does not export a parent-shell
variable.

| Situation | Action |
|---|---|
| Project detected + folder **exists** | Prepare an existing-project reference |
| Project detected + folder **does not exist** | Prepare the mode-appropriate project pages |
| No project context | Portable: global `references/`; Personal: `misc/` |

## Step U0.5: Clean Extraction Preflight

Before fetching, check whether the `defuddle` CLI is available:

```bash
which defuddle
```

- **If available:** Use `defuddle <url>` (via Bash) to retrieve a clean, stripped-down markdown version of the page. This removes ads, navbars, cookie banners, and related-content sidebars — reducing token usage by ~40-60% on typical articles. Use the `defuddle` output as your content source for Step U4 instead of the raw WebFetch result.
- **If not available:** Fall back to `WebFetch` as normal. No action needed.

## Step U1: Fetch the URL

Use `WebFetch` to retrieve the content at the provided URL (or skip if `defuddle` was used in Step U0.5).

- If the page is paywalled, JS-rendered (blank body), or returns an error: prepare **stub content** with the title (inferred from the URL), the URL, `stub: true`, and `> [Stub] Page could not be fetched — enrich manually.` Do not write a page yet; continue into the selected mode flow.
- If the page fetches successfully: proceed to Step U2.

## Step U2: Check for Duplicate

Before preparing a new page, check whether this URL was already ingested:

- **Portable Repository mode:** search existing knowledge pages below
  `<resolved-vault-path>` for matching `origin_url` metadata, and search
  configured repository source roots for an existing snapshot with that
  origin. Do not parse or edit manifest shards.
- **Personal mode:** grep `.manifest.json` for the URL in `source_url`; in
  project mode search
  `<resolved-vault-path>/projects/<project-name>/`, otherwise search
  `<resolved-vault-path>/misc/`.

If found: report which page covers it and offer to re-ingest (update) if the user wants fresh content. Do not create a duplicate page.

## Portable Repository URL flow

Use this branch only after the parent has resolved Portable Repository mode and
read the owner `AGENTS.md`.

1. Derive a stable `web-<slug>` from the origin URL. Fetching and extraction
   are analysis only; if a helper performs them, it returns the fetched text,
   stub status, distilled page proposals, and source mapping to the parent. It
   never writes or completes an ingest independently.
2. The parent saves a small, reviewable Markdown snapshot strictly below a configured `sources` root.
   Include `origin_url` metadata and capture status
   in the snapshot, followed by the fetched text or stub evidence. Preserve
   Unicode exactly. This repository file—not the live URL—is authoritative.
3. Resolve the snapshot's repository-relative Source ID. A live URL must never appear in `sources`;
   retain it only as `origin_url` metadata on the snapshot
   and resulting knowledge page.
4. Prepare candidate knowledge pages only at supported Markdown paths. Use
   `projects/<project-name>/references/<slug>.md` when a project is known and
   `references/<slug>.md` otherwise. `misc/` is Personal-only. If a project
   overview must be created, it cites the same Source ID. `sources: []` is invalid
   in Portable mode. Do not create vault attachments or a non-Markdown candidate.
5. The parent combines these proposals with any other batch results, computes
   complete source closure, and uses the snapshot Source ID in one parent-owned transaction.
   It writes only below the returned `candidate_vault` and
   declares any removals through that transaction.
6. Return to the main skill's parent-owned Portable Repository completion for
   timestamps, validation and warning review, commit, recovery, hot freshness,
   reporting, and the stop boundary.

Do not write directly to the live vault, manifest, `index.md`, `log.md`, or `hot.md`.
Do not run Personal QMD refresh or the Personal URL steps below.

## Personal mode URL flow

The existing U3–U7 project/`misc/` write and tracking workflow is Personal-only.
Use the concrete resolved vault path held in agent memory; do not assume config
resolution exported a shell variable.

### Step U3: Determine Target Path and Generate Slug

Derive a slug from the URL:
1. Strip `https://`, `http://`, and trailing slashes
2. Take hostname + first 2 meaningful path segments
3. Lowercase everything; replace `/`, `.`, `?`, `=`, `&`, `#`, and spaces with `-`
4. Collapse consecutive `-` into one; trim leading/trailing `-`
5. Cap at 50 characters
6. Prepend `web-`

Examples:
- `https://martinfowler.com/articles/microservices.html` → `web-martinfowler-com-articles-microservices`
- `https://arxiv.org/abs/1706.03762` → `web-arxiv-org-abs-1706-03762`

#### Step U3a: Existing project

Target: `<resolved-vault-path>/projects/<project-name>/references/<slug>.md`

Create `references/` inside the project folder if it doesn't exist yet. This is a reference page, not a synthesis or concept page — it documents an external source that's relevant to the project.

#### Step U3b: New project

First, create the project skeleton:

```
projects/<project-name>/
├── <project-name>.md          ← project overview (stub — fill in what you know)
├── concepts/
├── references/
└── skills/
```

The project overview stub (`<project-name>.md`) frontmatter:
```yaml
---
title: "<Project Name>"
category: project
tags: []
sources: []
created: "<ISO-8601 timestamp>"
updated: "<ISO-8601 timestamp>"
summary: "Project wiki for <project-name>. Created automatically via URL ingest."
---
```

Then add the page to: `projects/<project-name>/references/<slug>.md`

Report to the user: "Created new project `<project-name>` in the vault."

#### Step U3c: No project context (misc fallback)

Target: `<resolved-vault-path>/misc/<slug>.md`

Create the `misc/` directory if it does not exist yet.

### Step U4: Extract Knowledge

From the fetched content, identify:
- **Title** — the page's actual title (from `<title>` or `# heading`)
- **Core concepts** — what is this page fundamentally about?
- **Key claims** — the 3-7 most important assertions or findings
- **Entities** mentioned — people, tools, libraries, organizations
- **Related topics** — what fields or ideas does this connect to?
- **Open questions** — what does the page raise but not answer?

Track provenance per claim:
- *Extracted* — page explicitly states this (no marker needed)
- *Inferred* — you're generalizing or connecting to external context → `^[inferred]`
- *Ambiguous* — page is vague or internally contradictory → `^[ambiguous]`

### Step U5: Write the Page

The frontmatter differs slightly between modes:

**Project mode** (`projects/<project-name>/references/<slug>.md`):
```yaml
---
title: "<page title>"
category: references
project: "<project-name>"
tags: [<2-4 domain tags from taxonomy>]
sources:
  - "<URL>"
source_url: "<URL>"
created: "<ISO-8601 timestamp>"
updated: "<ISO-8601 timestamp>"
summary: "<1-2 sentence description of what this page is about, ≤200 chars>"
stub: false
provenance:
  extracted: 0.X
  inferred: 0.X
  ambiguous: 0.X
base_confidence: <computed — see below>
lifecycle: draft
lifecycle_changed: "<ISO date today>"
---
```

**Misc mode** (`misc/<slug>.md`):
```yaml
---
title: "<page title>"
category: misc
tags: [<2-4 domain tags from taxonomy>]
sources:
  - "<URL>"
source_url: "<URL>"
created: "<ISO-8601 timestamp>"
updated: "<ISO-8601 timestamp>"
summary: "<1-2 sentence description of what this page is about, ≤200 chars>"
affinity: {}
promotion_status: misc
stub: false
provenance:
  extracted: 0.X
  inferred: 0.X
  ambiguous: 0.X
base_confidence: <computed — see below>
lifecycle: draft
lifecycle_changed: "<ISO date today>"
---
```

**Computing `base_confidence` for a URL source:**

Classify the URL's quality bucket using the host:
- `arxiv.org`, `doi.org`, conference sites → `paper` (1.0)
- `*.gov`, official vendor docs (e.g. `docs.python.org`, `developer.mozilla.org`) → `official` (0.9)
- Well-maintained third-party docs (e.g. `docs.docker.com`) → `documentation` (0.85)
- GitHub READMEs (`github.com`) → `repository` (0.75)
- Personal blogs, Medium, Substack, dev.to → `blog` (0.55)
- Stack Overflow, Hacker News, Reddit → `forum` (0.4)
- Anything else → `unknown` (0.4)

With 1 distinct source: `base_confidence = round(0.17 + 0.5 × quality_score, 2)`

Examples: `paper` → 0.67, `official` → 0.62, `documentation` → 0.60, `repository` → 0.55, `blog` → 0.45, `forum/unknown` → 0.37.

Then write the body (same for both modes):

- `## Overview` — 2–4 sentence summary of what the page covers
- `## Key Points` — bulleted list of main claims/findings, with provenance markers
- `## Concepts` — wikilinks to related concept pages (`[[concepts/...]]`); create minimal stubs for important ones that don't exist yet
- `## Entities` — wikilinks to entity pages (`[[entities/...]]`) for people, tools, orgs mentioned
- `## Open Questions` — questions the source raises (omit section if none)
- `## Related` — wikilinks to any existing wiki pages this connects to; in project mode, always include a link back to `[[projects/<project-name>/<project-name>]]`

Apply `visibility/internal` or `visibility/pii` tags if the content warrants them. When in doubt, omit.

**Minimum wikilinks:** every page must link to at least 2 existing pages. Search `index.md` before writing. If fewer than 2 related pages exist, create minimal stub pages for the most important concepts mentioned.

### Step U5b: Affinity scoring (misc mode only)

Skip this step entirely if in project mode.

After writing the page, scan every `[[wikilink]]` you placed. For each linked page:
1. Check if it lives under `projects/<project-name>/`
2. Check if it has a `project:` frontmatter field
3. If either is true, increment that project's affinity score

Also: scan the page body for exact mentions of project names listed in `index.md`. Each unlinked mention adds +1 to that project's score.

Write the result to the `affinity` frontmatter block. Leave `affinity: {}` if no project connections found.

If any project's score ≥ 3, surface it:

> ⚡ Strong affinity detected: this page has **3+ connections** to `<project-name>`. Run the `cross-linker` skill to recompute affinity and then consider promoting this page to `projects/<project-name>/references/`.

### Step U6: Update Project Overview (project mode only)

Skip this step if in misc mode.

Read the project overview at `projects/<project-name>/<project-name>.md`. If the overview is a stub or doesn't mention this reference yet, add the new page to a `## References` section:

```markdown
## References

- [[projects/<project-name>/references/<slug>]] — <one-line summary>
```

If a `## References` section already exists, append to it. Update the `updated` timestamp in frontmatter.

### Step U7: Update Manifest and Special Files

**`.manifest.json`** — add or update the entry:

```json
{
  "ingested_at": "TIMESTAMP",
  "source_url": "https://...",
  "source_type": "url",
  "stub": false,
  "project": "<project-name or null>",
  "promotion_status": "<project-name or misc>",
  "pages_created": ["projects/<project-name>/references/<slug>.md"],
  "pages_updated": ["projects/<project-name>/<project-name>.md"]
}
```

Update `stats.total_sources_ingested` and `stats.total_pages`.

**`index.md`** — add the new page under the appropriate section:
- Project mode: under `## Projects > <project-name>`
- Misc mode: under `## Misc` (create the section at the bottom if it doesn't exist)

**`log.md`** — append:

Project mode:
```
- [TIMESTAMP] INGEST_URL url="<url>" page="projects/<project-name>/references/<slug>.md" project="<project-name>" mode=project
```

Misc mode:
```
- [TIMESTAMP] INGEST_URL url="<url>" page="misc/<slug>.md" affinity={} promotion_status=misc mode=misc
```

**`hot.md`** — Update **Recent Activity** with what was just ingested — keep the last 3 operations. Update **Key Takeaways** if the page introduced a concept worth flagging. Update `updated` timestamp.

### Personal Quality Checklist (URL sources)

- [ ] Target path determined correctly based on project detection
- [ ] Page written with correct frontmatter for the mode (project vs. misc)
- [ ] `source_url` in frontmatter matches the ingested URL
- [ ] At least 2 wikilinks to existing pages
- [ ] `summary:` field is present and ≤200 chars
- [ ] Provenance markers applied; `provenance:` frontmatter block present
- [ ] In project mode: project overview updated with link to new reference
- [ ] In misc mode: `affinity` and `promotion_status` fields present
- [ ] `.manifest.json`, `index.md`, and `log.md` updated
- [ ] Stub pages reported to user if fetch failed
- [ ] QMD refresh per the main SKILL.md (skip if `QMD_WIKI_COLLECTION` unset)

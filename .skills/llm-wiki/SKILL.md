---
name: llm-wiki
description: >
  The foundational knowledge distillation pattern for building and maintaining an AI-powered Obsidian wiki.
  Based on Andrej Karpathy's LLM Wiki architecture. Use this skill whenever the user wants to understand the
  wiki pattern, set up a new knowledge base, or needs guidance on the three-layer architecture (raw sources →
  wiki → schema). Also use when discussing knowledge management strategy, wiki structure decisions, or how
  to organize distilled knowledge. This is the "theory" skill — other skills handle specific operations
  (ingesting, querying, linting).
---

# LLM Wiki — Knowledge Distillation Pattern

You are maintaining a persistent, compounding knowledge base. The wiki is not a chatbot — it is a **compiled artifact** where knowledge is distilled once and kept current, not re-derived on every query.

## Three-Layer Architecture

### Layer 1: Raw Sources (immutable)

The user's original documents — articles, papers, notes, PDFs, conversation logs, bookmarks, **and images** (screenshots, whiteboard photos, diagrams, slide captures). These are never modified by the system. They live wherever the user keeps them (configured via `OBSIDIAN_SOURCES_DIR` in `.env`). Images are first-class sources: the ingest skills read them via the Read tool's vision support and treat their interpreted content as inferred unless it's verbatim transcribed text. Image ingestion requires a vision-capable model — models without vision support should skip image sources and report which files were skipped.

Think of raw sources as the "source code" — authoritative but hard to query directly.

Don't confuse this with the in-vault `_raw/` staging folder, which is a different thing: a scratch inbox for quick captures and drafts awaiting promotion (see `wiki-capture` and `wiki-ingest`). Files there aren't Layer 1 sources, but `wiki-ingest` still moves rather than deletes them on promotion, since some have no other copy.

### Layer 2: The Wiki (LLM-maintained)

A collection of interconnected Obsidian-compatible markdown files organized by category. This is the compiled knowledge — synthesized, cross-referenced, and navigable. Each page has:

- YAML frontmatter (title, category, tags, sources, timestamps)
- Obsidian `[[wikilinks]]` connecting related concepts
- Clear provenance — every claim traces back to a source

The wiki lives at the path configured via `OBSIDIAN_VAULT_PATH` in `.env`.

### Layer 3: The Schema (this skill + config)

The rules governing how the wiki is structured — categories, conventions, page templates, and operational workflows. The schema tells the LLM *how* to maintain the wiki.

## Wiki Organization

The vault has two levels of structure: **categories** (what kind of knowledge) and **projects** (where the knowledge came from).

### Categories

Organize pages into these default categories (customizable in `.env`):

| Category | Purpose | Example |
|---|---|---|
| `concepts/` | Ideas, theories, mental models | `concepts/transformer-architecture.md` |
| `entities/` | People, orgs, tools, projects | `entities/andrej-karpathy.md` |
| `skills/` | How-to knowledge, procedures | `skills/fine-tuning-llms.md` |
| `references/` | Summaries of specific sources; academic papers use the Paper Deep-Dive Template (below) | `references/attention-is-all-you-need.md` |
| `synthesis/` | Cross-cutting analysis across sources | `synthesis/scaling-laws-debate.md` |
| `journal/` | Timestamped observations, session logs | `journal/2024-03-15.md` |

### Projects

Knowledge often belongs to a specific project. The `projects/` directory mirrors this:

```
$OBSIDIAN_VAULT_PATH/
├── projects/
│   ├── my-project/
│   │   ├── my-project.md      ← project overview (named after project)
│   │   ├── concepts/          ← project-scoped category pages
│   │   ├── skills/
│   │   └── ...
│   ├── another-project/
│   │   └── ...
│   └── side-project/
│       └── ...
├── concepts/                   ← global (cross-project) knowledge
├── entities/
├── skills/
└── ...
```

**When knowledge is project-specific** (a debugging technique that only applies to one codebase, a project-specific architecture decision), put it under `projects/<project-name>/<category>/`.

**When knowledge is general** (a concept like "React Server Components", a person like "Andrej Karpathy", a widely applicable skill), put it in the global category directory.

**Cross-referencing:** Project pages should `[[wikilink]]` to global pages and vice versa. A project's overview page should link to the key concept, skill, and entity pages relevant to that project — whether they live under the project or globally.

**Naming rule:** The project overview file must be named `<project-name>.md`, not `_project.md`. Obsidian's graph view uses the filename as the node label — `_project.md` makes every project appear as `_project` in the graph, making it unreadable. So `projects/my-project/my-project.md`, `projects/another-project/another-project.md`, etc.

Each project directory has an overview page structured like this:

```markdown
---
title: My Project
category: project
tags: [ai, web, backend]
source_path: ~/.claude/projects/-Users-name-Documents-projects-my-project
created: 2026-03-01T00:00:00Z
updated: 2026-04-06T00:00:00Z
---

# My Project

One-paragraph summary of what this project is.

## Key Concepts
- [[concepts/some-api]] — used for core functionality
- [[projects/my-project/concepts/main-architecture]] — project-specific architecture

## Related
- [[entities/some-service]] — deployment platform
```

## Special Files

Every wiki has these files at its root:

### `index.md`
A content-oriented catalog organized by category. Each entry has a one-line summary and tags. Rebuild this after every ingest operation. Format:

```markdown
# Wiki Index

## Concepts
- [[transformer-architecture]] — The dominant architecture for sequence modeling ( #ml #architecture)
- [[attention-mechanism]] — Core building block of transformers ( #ml #fundamentals)

## Entities
- [[andrej-karpathy]] — AI researcher, educator, former Tesla AI director ( #person #ml)
```
**Format rule**: Add a space after the opening `(` and tags.
❌ Don't: `description (#tag)` — breaks tag parsing
✅ Do: `description ( #tag)` — proper spacing and tag parsing

### `log.md`
Chronological append-only record tracking every operation. Each entry is parseable:

```markdown
## Log

- [2024-03-15T10:30:00Z] INGEST source="papers/attention.pdf" pages_updated=12 pages_created=3
- [2024-03-15T11:00:00Z] QUERY query="How do transformers handle long sequences?" result_pages=4
- [2024-03-16T09:00:00Z] LINT issues_found=2 orphans=1 contradictions=1
- [2024-03-17T10:00:00Z] ARCHIVE reason="rebuild" pages=87 destination="_archives/..."
- [2024-03-17T10:05:00Z] REBUILD archived_to="_archives/..." previous_pages=87
```

### `.manifest.json`
Tracks every source file that has been ingested — source identity, content hash,
and what wiki pages it produced. This is the backbone of the delta system. The
storage protocol depends on the resolved configuration mode; never treat the
personal and portable layouts as interchangeable. See the `wiki-status` skill
for reporting semantics.

The manifest enables:
- **Delta computation** — what's new or modified since last ingest
- **Append mode** — only process the delta, not everything
- **Audit** — which source produced which wiki page
- **Staleness detection** — source changed but wiki page hasn't been updated

### Personal mode — manifest v1

Personal vaults keep the established monolithic manifest v1 object in
`$OBSIDIAN_VAULT_PATH/.manifest.json`.

- For sources outside the vault, expand `~` and environment variables and use
  one canonical absolute key before comparing or writing. Never mix expanded
  and unexpanded aliases for the same file.
- Existing vault-relative keys remain valid for sources inside the vault, such
  as `_raw/articles/note.md`. Cache operations resolve those keys against the
  vault and must not convert them into duplicate absolute entries.
- To repair an older personal manifest containing colliding aliases, run
  `scripts/manifest.py normalize <vault>`; this command is for manifest v1
  only.
- Populate the v1 page-provenance fields with vault-relative paths so a changed
  source can revisit the pages it created or updated.

### Portable Repository mode — manifest v2

Portable repositories use a marker at `<vault>/.manifest.json` and one shard in
`<vault>/.manifest/sources/` per authoritative source. Each shard is keyed by a
stable repository-relative Source ID, for example
`sources/design/portable.md`; it never depends on a clone's filesystem
location.

The portable rules are strict:

- In portable mode, manifest v2 schema 1 requires exactly one configured
  source root, even though `[paths].sources` uses TOML list syntax. Multiple
  roots fail config validation; the list shape reserves future schema
  evolution.
- A repository-relative Source ID uses `/`, is normalized, contains no `.` or
  `..` segments, and identifies an ordinary file below the configured
  `sources` root. Absolute paths and backslashes are invalid.
- Status discovery ignores `.gitkeep` and files beneath hidden source path
  components (any relative component beginning with `.`). They are not
  authoritative tracked sources and do not receive shards.
- The shard path mirrors the portion below the source root:
  `sources/design/portable.md` maps to
  `<vault>/.manifest/sources/design/portable.md.json`. There is exactly one shard
  per source.
- Use `obsidian-wiki cache-check` before compiling and `obsidian-wiki
  cache-update` after compiling. These commands select manifest v2 from the
  resolved portable config. Agents must not hand-edit the marker or shards and
  must not reconstruct a monolithic source collection.
- A live URL or external filesystem path is not a durable Source ID. When that
  material must be authoritative, capture a small, reviewable snapshot below
  the configured source root and store it as an ordinary Git file. Git LFS
  pointers are unsupported because a pointer is not the source content.
- The marker and shards record source-to-page compilation state only. Do not
  add model, agent, API, or generation-tool provenance fields; those details do
  not identify the authoritative source and cause needless contributor churn.
- If a source was intentionally deleted, either restore it or remove the
  entire corresponding shard file with
  `git rm <vault>/.manifest/sources/<relative>.json`. This is whole-file Git
  deletion, never editing marker or shard JSON fields. There is no portable
  cache removal command.

## Page Template

When creating a new wiki page, use this structure:

```markdown
---
title: Page Title
category: concepts
tags: [ml, architecture]
aliases: [alternate name]
relationships:
  - target: "[[concepts/related-concept]]"
    type: extends
sources: [papers/attention.pdf]
summary: One or two sentences, ≤200 chars, so a reader (or another skill) can preview this page without opening it.
provenance:
  extracted: 0.72
  inferred: 0.25
  ambiguous: 0.03
base_confidence: 0.65
lifecycle: draft
lifecycle_changed: 2024-03-15
tier: supporting
created: 2024-03-15T10:30:00Z
updated: 2024-03-15T10:30:00Z
---

# Page Title

One-paragraph summary of what this page covers.

## Key Ideas

- The source's central claim, paraphrased directly.
- A generalization the source implies but doesn't state outright. ^[inferred]
- A figure two sources disagree on. ^[ambiguous]

Use [[wikilinks]] to connect to related pages.

## Open Questions

Things that are unresolved or need more sources.

## Sources

- [[references/attention-is-all-you-need]] — Original paper
```

## Paper Deep-Dive Template

The generic template suits most sources. **Academic papers are the exception.** For ML/AI/LLM/VLM (and similar) papers landing in `references/`, the substance lives in the architecture, the equations, and the results table — exactly what a terse "Key Ideas" list flattens away. For these, use the richer template below. This is the one place where *"compile, don't retrieve"* yields to a thorough, self-contained walkthrough a reader could study instead of the paper.

Obsidian renders the needed primitives natively, so no extra tooling is required: Mermaid fenced diagrams, `$$…$$` LaTeX (MathJax), markdown tables, and `![[image]]` / `![[paper.pdf#page=N]]` embeds.

Use this template only when the source is an academic paper (arXiv/conference) with load-bearing figures or equations. Everything else uses the generic Page Template above. Frontmatter, provenance markers, confidence, lifecycle, and `relationships:` are unchanged — only the body sections differ.

````markdown
---
# ...required frontmatter, same as the generic template; category: references...
---

# Paper Title

> [!tldr] One sentence: what's new, plus the headline result.

## Problem & Motivation

What's broken or missing that this paper addresses.

## Method / Architecture

Prose walkthrough. Embed the paper's real architecture figure as the primary
visual (see *Academic papers* in `wiki-ingest` for the PyMuPDF extraction recipe).
Fall back to a Mermaid flowchart only when no figure can be extracted.

![[attachments/<slug>-fig1.png]]
*Figure N (Author Year): one-line caption.*

## Key Equations

The 1–3 core equations as display math, not backtick code:

$$ \mathcal{L} = \mathbb{E}_{x}\!\left[-\log p_\theta(y \mid z)\right] $$

## Results

Headline numbers as a table, not a comma-separated blob — and embed a key
results/motivating figure (scaling plot, benchmark chart, capability collage)
when the paper has one:

| Method | Benchmark | Metric | Cost |
|---|---|---|---|
| Baseline | … | … | … |
| **This paper** | … | … | … |

![[attachments/<slug>-resultsN.png]]
*Figure N (Author Year): one-line caption.*

## Limitations

What the paper concedes or sidesteps. Mark reading-between-the-lines as ^[inferred].

## Related

Typed `[[wikilinks]]` to neighbouring work.

## Sources

- Clickable canonical link, e.g. <https://arxiv.org/abs/XXXX.XXXXX>
````

A Mermaid diagram reconstructed from the paper's prose is a synthesis, not a transcription — treat it as `^[inferred]` when the interpretation is non-trivial.

## Provenance Markers

Every claim on a wiki page has one of three provenance states. Mark them inline so the reader (and future ingest passes) can tell signal from synthesis.

These are framework defaults. A vault's `AGENTS.md` may add markers or workflow flags. Preserve owner extensions and treat orthogonal workflow flags separately from the extracted/inferred/ambiguous truth-state axis.

| State | Marker | Meaning |
|---|---|---|
| **Extracted** | *(no marker — default)* | A paraphrase of something a source actually says. |
| **Inferred** | `^[inferred]` suffix | An LLM-synthesized claim — a connection, generalization, or implication the source doesn't state directly. |
| **Ambiguous** | `^[ambiguous]` suffix | Sources disagree, or the source is unclear. |

Example:

```markdown
- Transformers parallelize across positions, unlike RNNs.
- This is why they scale better on modern hardware. ^[inferred]
- GPT-4 was trained on roughly 13T tokens. ^[ambiguous]
```

**Why this syntax:**
- `^[...]` is footnote-adjacent in Obsidian — renders cleanly and never collides with `[[wikilinks]]`.
- Inline (suffix) so a single bullet stays a single bullet.
- Default = extracted means existing pages without markers stay valid.

**Frontmatter summary:** Optionally surface the rough mix at the page level so the user can scan for speculation-heavy pages without reading them:

```yaml
provenance:
  extracted: 0.72   # rough fraction of sentences/bullets with no marker
  inferred: 0.25
  ambiguous: 0.03
```

These are best-effort numbers written by the ingest skill at create/update time. `wiki-lint` recomputes them and flags drift. The block is optional — pages without it are treated as fully extracted by convention.

## Typed Relationships

Plain `[[wikilinks]]` in page bodies carry no semantic weight — they indicate "related to" but not *how*. The optional `relationships:` frontmatter block adds typed, directional edges to the knowledge graph.

### The `relationships:` block

```yaml
relationships:
  - target: "[[Transformer Architecture]]"
    type: extends
  - target: "[[LSTM]]"
    type: contradicts
  - target: "[[Attention Mechanism]]"
    type: implements
```

Each entry has two required fields:
- `target` — a wikilink (using the same format as `OBSIDIAN_LINK_FORMAT`) to the related page
- `type` — one of the allowed semantic types below

### Allowed relationship types

The table below is the framework default allowlist. A vault's `AGENTS.md` may extend it; consumers must use the effective allowlist and preserve owner semantics without coercion.

| Type | Meaning | Example |
|---|---|---|
| `extends` | This page builds on or generalises the target | GPT extends Transformer Architecture |
| `implements` | This page is a concrete realisation of the target concept | BERT implements Masked Language Modelling |
| `contradicts` | This page's claims conflict with or refute the target | Evidence A contradicts Evidence B |
| `derived_from` | This page is based on or adapted from the target | Fine-tuning is derived from Transfer Learning |
| `uses` | This page depends on or relies on the target | RAG uses Vector Databases |
| `replaces` | This page supersedes or deprecates the target | GPT-4 replaces GPT-3 |
| `related_to` | Catch-all: related but no stronger directional type applies | Concept A is related to Concept B |

### Rules

- **Optional field** — omit the block entirely if no typed relationships are known. Untagged wikilinks remain valid and are treated as `related_to` by `wiki-export`.
- **Don't duplicate** — if `[[foo]]` already appears as an inline wikilink, the `relationships:` entry just enriches it with a type; it is not a second link.
- **Direction matters** — the page declaring the entry is the *source*; `target` is the destination. Only declare relationships from this page's perspective.
- **Don't fabricate** — only add a typed entry when the source material makes the relationship direction and type clear. When in doubt, use `related_to` or omit.

Skills that read `relationships:`: `wiki-export` (emits typed edges), `cross-linker` (writes typed entries when inferring links), `wiki-query` (surfaces type in answers and walks the typed-edge graph for multi-hop "how is X connected to Y" path queries — bounded BFS over the `relationships:` adjacency, frontmatter-only).

## Confidence and Lifecycle

Every page carries two orthogonal trust signals plus an optional supersession link.

The requiredness and lifecycle values below are framework defaults. A vault's `AGENTS.md` may extend lifecycle values or make trust fields optional. Validators must apply that effective owner schema while still validating any trust value that is present.

The deterministic lint/trust consumer accepts owner schema through `OBSIDIAN_ALLOWED_LIFECYCLES`, `OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES`, `OBSIDIAN_REQUIRED_TRUST_FIELDS`, and `OBSIDIAN_SCHEMA_SOURCE`. Resolution precedence is CLI > environment/config > these framework defaults (with lifecycle and relationship extensions additive). Explicit blank or whitespace-only values fail closed; omit the variable to select defaults. `wiki-lint/SKILL.md` owns the operational invocation contract.

### Required fields

```yaml
base_confidence: 0.65          # [0.0, 1.0] — time-independent quality estimate. Stored once, recomputed on content change.
lifecycle: draft               # draft | reviewed | verified | disputed | archived
lifecycle_changed: 2024-03-15  # ISO date of last state transition
# lifecycle_reason: "..."      # optional free-text — why the state changed; surfaced by wiki-query
# superseded_by: "[[new-page]]" # wikilink; only when lifecycle=archived
```

`lifecycle_reason` and `superseded_by` are optional. Never fabricate them.

### Confidence formula

The formula is a **manual base score**, not a deterministic URL classifier:

```
base_confidence = lineage_count_score * 0.5 + source_quality_score * 0.5

lineage_count_score  = min(independent_evidence_lineages / 3, 1.0)
source_quality_score = avg(reviewed quality score per independent lineage)
```

After calculating the raw score, assess whether the evidence covers the page's material claims. Partial coverage may justify keeping or lowering the score; unsupported material claims require source/claim repair before any confidence change. Avoid small score churn without meaningful epistemic change.

**Source-quality scores** (use the highest-matching bucket):

| Bucket | Score | Examples |
|---|---|---|
| `paper` | 1.0 | arXiv, conference proceedings |
| `official` | 0.9 | `*.gov`, vendor docs |
| `documentation` | 0.85 | well-maintained third-party docs |
| `book` | 0.8 | books, technical references |
| `repository` | 0.75 | content-addressed repository/code evidence |
| `blog` | 0.55 | personal blogs |
| `session_transcript` | 0.5 | conversation history or completed operation |
| `forum` | 0.4 | Stack Overflow, HN, Reddit, issue-grade reports |
| `unknown` | 0.4 | catch-all/current config |
| `llm_generated` | 0.3 | LLM synthesis or unvalidated memory seed |

**An independent evidence lineage** is an origin that can corroborate a claim independently. Canonical source IDs remain useful for identity, but identity alone does not prove independence. Collapse dependent evidence before counting:

- files, releases, and commits from one repository → one repository lineage;
- retry/review/fix tasks in one workstream → one task lineage;
- parent/child Kanban records → one task lineage;
- byte-identical memories across profiles → one memory lineage;
- a snapshot plus the mutable source it captures → one lineage;
- aliases or metadata references resolving to one origin → one lineage.

The deterministic `wiki-lint` path validates `_meta/trust-ledger.json`; it does not recompute confidence from source strings. New or materially changed pages are marked for manual review. Refresh the ledger only after explicit human approval.

**Per-skill defaults** (ingest skills compute this automatically):

| Skill | base_confidence | lifecycle |
|---|---|---|
| `wiki-ingest` (URL) | `0.17 + 0.5 × classify(url)` | `draft` |
| `wiki-ingest` (single doc) | per-source classifier | `draft` |
| `wiki-ingest` (multi-doc) | `min(N/3,1)×0.5 + avg_q×0.5` | `draft` |
| `wiki-research` | varies, often 0.85+ | `draft` |
| `wiki-capture` | 0.42 | `draft` |
| `*-history-ingest` | 0.42 | `draft` |
| `wiki-update` | 0.59 | `draft` |
| `wiki-synthesize` | `min(input_pages.base_confidence)` | `draft` |

### Lifecycle state machine

Five states. **`stale` is not a state** — it is a computed overlay: `is_stale = (today − updated) > 90 days`.

| State | Entered by | Notes |
|---|---|---|
| `draft` | Any ingest skill on first write | Default for all new pages |
| `reviewed` | Human edit only | |
| `verified` | Human edit only | Time alone never demotes verified pages |
| `disputed` | Manual edit only | Overrides every state except `archived` in display |
| `archived` | Manual edit, or ingest skill setting `superseded_by` | Terminal |

Only ingest skills set `draft`. All other transitions require a human editor. Update `lifecycle_changed` whenever the state changes.

## Importance Tiering

The `tier:` field controls which pages get updated on each ingest pass and their priority in retrieval. As wikis grow, re-reading every page on every ingest wastes tokens — tiering lets ingest and query skills focus effort where it matters most.

### Three tiers

| Tier | Meaning | Ingest behavior | Query priority |
|---|---|---|---|
| `core` | Load-bearing pages — many other pages depend on them (high incoming-link count or bridge position). Always worth updating. | Always update if the source is even marginally relevant | Surfaced first in index and full-read passes |
| `supporting` *(default)* | Standard wiki pages with moderate connectivity | Update when the source has clear new claims for this page | Standard priority |
| `peripheral` | Low-connectivity pages — rarely linked, narrowly scoped | Skip unless the source is *primarily* about this topic | Last resort; skipped when trimming to context budget |

### Assignment rules

- **New pages:** default to `tier: supporting`
- **Promote to `core`:** when a page accumulates ≥5 incoming wikilinks **or** is flagged as a bridge by `wiki-status` insights mode
- **Demote to `peripheral`:** when a page has ≤1 incoming link and hasn't been updated in 90+ days
- **Human override always wins** — edit `tier:` manually to lock a page at any level
- Existing pages without `tier:` are treated as `supporting` (backward compatible — no migration needed)

### Who manages tier

- `wiki-ingest` reads `tier:` to decide whether to update a page on the current pass
- `wiki-query` uses `tier:` to order candidates in the index pass and trim to context budget
- `wiki-status` insights mode computes graph metrics and **suggests** tier assignments — it never writes them automatically
- `wiki-lint` flags missing `tier:` on newly created pages (Phase 2 enforcement, same timeline as `base_confidence`)

## Retrieval Primitives

Reading the vault is the dominant cost of every read-side skill. Use the cheapest primitive that can answer the question and **escalate only when the cheaper one is insufficient**. Any skill that needs content from the vault should follow this table rather than jumping straight to full-page reads.

| Need | Primitive | Relative cost |
|---|---|---|
| Does a page exist? What's its title/category/tags? | Read `index.md`; `Grep` frontmatter blocks (scope with a pattern that targets `^---` blocks at file heads) | **Cheapest** |
| 1–2 sentence preview of a page | Read the `summary:` field in its frontmatter | **Cheap** |
| A specific claim or section inside a page | `Grep -A <n> -B <n> "<term>" <file>` — returns only the matching lines plus context | **Medium** |
| Whole-page content | `Read <file>` | **Expensive** — last resort |
| Relationships across pages | `Grep "\[\[.*?\]\]"` across the vault, or walk wikilinks from a known page | Case-by-case |

**Search command preference:** for shell/file searches, use ripgrep (`rg`, `rg --files`) when available; if not, fall back to `grep`/`find`. Capitalized `Grep`/`Glob` names in these skills are tool-generic primitives for agents that expose those tools.

**The rule:** escalate only when the cheaper primitive can't answer the question. If you can answer from `summary:` fields alone, don't read page bodies. If a grepped section with `-A 10 -B 2` gives you the claim, don't read the whole page. A 500-line page opened to read 15 lines is 485 lines of wasted tokens.

**Why this matters:** a 20-page vault lets you get away with full-vault scans. A 200-page vault does not. The primitives above are how the skills framework scales to large vaults without a database.

Skills that consume this table: `wiki-query`, `cross-linker`, `wiki-lint`, `wiki-status` (insights mode). Any new skill that reads the vault should cite this section rather than reinvent the pattern.

## QMD Index Freshness

QMD is an optional search index layered on top of the vault. The markdown vault is the source of truth. Any skill that writes wiki markdown should refresh QMD after the vault write completes, but only when `QMD_WIKI_COLLECTION` is configured and the local QMD transport is available. If QMD refresh fails, keep the vault changes and report the QMD status separately.

Use the cheapest verification path that proves the new content is visible: `qmd update`, `qmd embed` only if vectors are stale or missing, then a targeted `qmd get` or `qmd ls` check for one written page or the collection root. Read-only skills should not refresh QMD.

## Core Principles

1. **Compile, don't retrieve.** The wiki is pre-compiled knowledge. When you ingest a source, update every relevant page — don't just create a summary of the source.

2. **Compound over time.** Each ingest should make the wiki smarter, not just bigger. Merge new information into existing pages, resolve contradictions, strengthen cross-references.

3. **Provenance matters.** Every claim should trace to a source. When updating a page, note which source prompted the update.

4. **Mark inferences.** Default sentences are extracted. Mark synthesized claims with `^[inferred]` and contested claims with `^[ambiguous]`. A wiki that hides its guessing rots silently; one that marks it stays trustworthy.

5. **Human curates, LLM maintains.** The human decides what sources to add and what questions to ask. The LLM handles the bookkeeping — updating cross-references, maintaining consistency, noting contradictions.

6. **Obsidian is the IDE.** The user browses and explores the wiki in Obsidian. Everything must be valid Obsidian markdown with working wikilinks.

## Link Format

All internal links connecting wiki pages are controlled by `OBSIDIAN_LINK_FORMAT` from the resolved config (default: `wikilink`).

| Setting | Syntax | Example |
|---|---|---|
| `wikilink` *(default)* | `[[path/to/page]]` or `[[path/to/page\|display text]]` | `[[concepts/foo\|foo]]` |
| `markdown` | `[display text](relative/path.md)` | `[foo](../concepts/foo.md)` |

### Generating markdown-format links

When `OBSIDIAN_LINK_FORMAT=markdown`:
1. Compute the path from the **current file's directory** to the **target `.md` file** using `..` to climb up as needed.
2. Use the page title or a natural phrase as display text.
3. Always include the `.md` extension.

| Current file | Target | Relative link |
|---|---|---|
| `index.md` | `concepts/foo.md` | `[foo](concepts/foo.md)` |
| `concepts/foo.md` | `entities/bar.md` | `[bar](../entities/bar.md)` |
| `projects/my-project/my-project.md` | `concepts/foo.md` | `[foo](../../concepts/foo.md)` |
| `projects/my-project/concepts/arch.md` | `entities/bar.md` | `[bar](../../../entities/bar.md)` |

The `[[path\|display text]]` wikilink form maps to `[display text](relative/path.md)` in Markdown mode.

**Scope:** this setting affects only newly written or updated links. Existing vault content is never automatically migrated — users who want to convert old links can run the `cross-linker` or `wiki-lint` skill.

Every write skill reads `OBSIDIAN_LINK_FORMAT` from config before generating links and applies the correct format.

## Config Resolution Protocol

**All skills must resolve config using this algorithm — do not hard-code one
config location.** This keeps personal, named-vault, and Portable Repository
mode deterministic.

### Resolution order

0. **Inline vault override (`@name`) — explicit `@name`** — if the request
   contains an `@<name>` token (for example, `@work save this`), resolve `~/.obsidian-wiki/config.<name>` directly and use
   it for this invocation only. Missing or invalid named config fails closed;
   never change the persistent default and never fall back. Strip the routing
   token before treating the remaining request as content.
1. **nearest ancestor `.obsidian-wiki/config.toml`** — walk up from CWD. Its
   presence selects Portable Repository mode. Parse and validate it; an invalid
   marker is an error, not permission to try a lower-precedence source.
2. **nearest ancestor `.env` containing `OBSIDIAN_VAULT_PATH`** — walk up from
   CWD to `$HOME` and use the first matching file.
3. **`~/.obsidian-wiki/config`** — use the personal global config.
4. **setup guidance** — if no source exists, say: "No config found. Run
   `wiki-setup` to initialize your wiki."

For Portable Repository mode, resolve every `[paths]` value from the repository
root. Treat a file as authoritative source material only when it is below one
of the configured `sources` paths. Runtime tools may compute absolute paths in
memory, but skills must never synthesize an absolute `OBSIDIAN_WIKI_REPO` into
committed files. After resolving any mode, read `<vault>/AGENTS.md` when it
exists and apply its owner conventions for the session.

`@name` is a **per-invocation override** — it targets one vault for one request. `/wiki-switch <name>` is the **persistent default** — it re-points the active symlink for all future requests. Use `@name` to touch the other vault from anywhere without disturbing your default ("brain") vault.

Do not use a shell-only `.env` finder: it would skip the higher-precedence TOML
marker and can silently erase the distinction between an invalid config and a
missing one. Follow the ordered protocol above or use the installed CLI, whose
resolver implements it.

## Legacy-to-Portable Migration Protocol

Migration is a separate operator action, never an implicit `wiki-setup` branch
or a normal knowledge write. It accepts only a repository in which the legacy
vault and exactly one source root are already contained, distinct directories.
All legacy manifest sources and page-frontmatter sources must map to ordinary
files below that source root. The migration command must not copy or delete
external files. Before analysis, an operator may deliberately create a bounded,
reviewable repository snapshot and update legacy provenance to that snapshot;
never do this silently on the user's behalf.

Always analyze first. Paths are resolved against `--root`, not the current
working directory:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources
```

The no-flag form is strictly read-only. It prints mappings, page updates,
manifest shards, warnings, blockers, and—only for a blocker-free plan—the exact
apply command. JSON automation may add `--json --pretty`. Resolve all blockers,
review the mappings, then establish the supported rollback boundary before
apply: the vault's enclosing Git top level must equal `--root`, the legacy
baseline (including every intended source) must be committed, and the worktree
must be clean. Only then run:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources --apply
obsidian-wiki check
git diff
```

Blocker codes have these meanings:

| Code | Meaning |
|---|---|
| `outside-root` | The vault or source root is outside the migration repository. |
| `path-overlap` | The vault and source root contain one another. |
| `managed-path-overlap` | The vault or source root overlaps portable framework-owned paths. |
| `portable-artifact-conflict` | Existing manifest v2 artifacts cannot be safely adopted. |
| `manifest-missing` / `manifest-invalid` | The legacy v1 manifest is absent, unreadable, or malformed. |
| `unsafe-page` / `missing-page` | A manifest page path escapes policy, crosses a link, or is absent. |
| `page-frontmatter-invalid` | A knowledge page cannot be parsed as valid UTF-8 frontmatter. |
| `live-url-source` / `pseudo-source` | A source is not a durable repository file. |
| `external-source` | A source does not remain below the configured source root. |
| `unsafe-source` / `missing-source` | A source crosses a link, is absent, or is not an ordinary readable file. |
| `source-id-collision` | Different legacy records map incompatibly to one repository Source ID. |
| `unmapped-page-source` | Page frontmatter cites a source with no manifest mapping. |

For collaboration, preserve authoritative material as small, reviewable
Markdown or text files and commit those exact bytes with ordinary Git; external
material may be represented by a bounded text snapshot. The analyzer validates
ordinary files but does not verify Git-index membership or detect LFS pointer
signatures, so inspect `git status`/`git ls-files` before publishing. Binary
PDFs/images belong to Personal-mode ingest unless converted to a reviewable
text snapshot. Git LFS pointer files are metadata, not the referenced bytes,
and agents must not compile them as source contents.

Apply rechecks every analyzed preimage, builds candidates in local migration
state, installs portable config/skills/bootstrap/Git rules, rewrites page source
frontmatter, creates manifest v2 shards, replaces `index.md` and `log.md` with
stable built-in-query views, removes the existing legacy `hot.md`, and writes one immutable
migration operation last. A failure attempts to restore every original
preimage byte-for-byte and remove created files. If rollback is incomplete, the
CLI returns external `status: "error"`, explains that rollback was incomplete,
and records internal `rollback-failed` state while retaining its manifest and
snapshots; stop and use that evidence for manual diagnosis. A success reports a retained recovery
directory below `.obsidian-wiki/local/migrations/`; keep it until the Git diff
is accepted and committed, then it may be removed deliberately as local state.
The backup layout is internal evidence, not a supported post-success restore
interface. There is no migration restore subcommand—after a successful apply,
ordinary Git review/revert is the publication rollback boundary.

Migration never initializes Git, stages files, commits, pushes, or opens a pull
request. Portable Git facts use the worktree surrounding the configured vault
and require it to match the portable root; never create `wiki/.git`. When
portable config is resolved, `sync` and `sync-setup` refuse the Personal-mode
automatic workflow. Disable old Personal-mode cron/aliases, do not bypass mode
resolution with `--vault`, and let a human publish the reviewed branch. The
CLI cannot discover external schedulers or shell configuration, so the operator
must inspect the environment where that automation was installed.
The migration analyzer can run before Git is initialized, and `check` only
warns when no worktree exists. That tolerance supports read-only planning, not
apply: do not run `--apply` until the enclosing root and clean committed legacy
baseline exist. Absence from Git is not a CLI blocker, so this precondition is
the operator's responsibility.

## Portable Write Protocol

After config resolution, every skill that can write must branch on the resolved
mode **before any vault mutation**.

### Portable Repository mode

Use one CLI transaction for the complete logical write:

1. Start it with the actual one-or-more authoritative source paths:
   `obsidian-wiki transaction begin --source sources/a.md sources/b.md --json`.
   Record the returned `transaction_id` and `candidate_vault`.
2. Write new and updated knowledge pages only below `candidate_vault`, using
   their final vault-relative paths. If a requested mutation cannot be
   represented as candidate knowledge pages or declared deletions, stop and
   report that it is unsupported in portable mode; never fall through to a
   direct live-vault write.
3. Declare each removal with
   `obsidian-wiki transaction delete <id> concepts/obsolete.md`.
4. Review all candidate frontmatter, links, and deletion paths. Only then run
   `obsidian-wiki transaction commit <id> --json`.
5. If begin or commit fails, run
   `obsidian-wiki transaction list --json`, inspect the retained workspace and
   status, and deliberately choose one recovery action:
   `obsidian-wiki transaction retry <id> --json`,
   `obsidian-wiki transaction restore <id> --json`,
   `obsidian-wiki transaction abort <id> --json`, or
   `obsidian-wiki transaction discard <id> --json`. Never start a replacement
   transaction while the failed transaction's outcome is ambiguous.
6. Never hand-edit manifest shards, manifest markers, or operation pages. The
   transaction manager owns those files. In portable repositories,
   **index.md and log.md are stable** collaboration surfaces and ordinary writes
   do not update them.
7. Treat derived session context separately: **hot.md is local and ignored**.
   Run `obsidian-wiki hot status --json` before using it. When it reports
   `stale`, regenerate `hot.md` from current page summaries and recent operation
   entries (or proceed without it), then run
   `obsidian-wiki hot mark-current --json` after the replacement is complete.
8. Do not commit, push, or open a pull request. Transaction promotion updates
   the working tree only; Git publication always remains an explicit human
   action.

The transaction is also the safety boundary for destructive skills. Do not
create a pre-write Git snapshot or automatic Git commit in portable mode.
`WIKI_STAGED_WRITES` and `_staging/` do not replace transactions there.

### Personal mode

**Personal mode** retains the existing direct-write workflow and its central
manifest, `index.md`, `log.md`, and `hot.md` maintenance. Existing personal
pre-write snapshots and optional `_staging/` review behavior also remain in
force. Instructions elsewhere in a skill that directly edit those files or
create a Git snapshot are personal-mode instructions unless they explicitly say
otherwise.

### Vault-scoped state

Skills that write runtime state (e.g. `daily-update`) must scope that state to the resolved vault, not to a global path. Use:

```
VAULT_ID=$(echo "$OBSIDIAN_VAULT_PATH" | md5sum 2>/dev/null || md5 -q - <<< "$OBSIDIAN_VAULT_PATH" | cut -c1-8)
STATE_DIR="$HOME/.obsidian-wiki/state/$VAULT_ID"
```

### Standard "Before You Start" block

Every skill's setup section should read:

> **Resolve config** — follow the Config Resolution Protocol in
> `llm-wiki/SKILL.md`: explicit `@name`, nearest ancestor
> `.obsidian-wiki/config.toml`, nearest ancestor `.env` containing
> `OBSIDIAN_VAULT_PATH`, `~/.obsidian-wiki/config`, then setup guidance. This
> gives `OBSIDIAN_VAULT_PATH` and any tool-specific path overrides.

## Environment Variables

The wiki is configured through environment variables (see `.env.example`). The only required variable is the vault path — everything else has sensible defaults.

- `OBSIDIAN_VAULT_PATH` — Where the wiki lives **(required)**
- `OBSIDIAN_SOURCES_DIR` — Where raw source documents are
- `OBSIDIAN_CATEGORIES` — Comma-separated list of categories
- `WIKI_SKIP_PROJECTS` — Comma-separated substrings; any project dir whose name contains one is excluded from history ingest (scan + delta + manifest). See the "Project Scoping" step in the history-ingest skills.
- `CLAUDE_HISTORY_PATH` — Where to find Claude conversation data
- `CODEX_HISTORY_PATH` — Where to find Codex session data
- `HERMES_HOME` — Where to find Hermes agent data
- `OPENCLAW_HOME` — Where to find OpenClaw data
- `COPILOT_HISTORY_PATH` — Where to find Copilot session data
- `OBSIDIAN_LINK_FORMAT` — Internal link syntax: `wikilink` (default) or `markdown`
- `WIKI_TOKEN_WARN_THRESHOLD` — Emit a warning in `wiki-status` when the full-wiki token estimate exceeds this value (default: `100000`). Set to `0` to disable. See `wiki-status` for the token footprint report.
- `WIKI_STAGED_WRITES` — When `true`, all LLM-written pages go to `_staging/<category>/` for human review before promotion. See `wiki-setup` and `wiki-stage-commit` for details.

No API keys are needed — the agent running these skills already has LLM access built in.

## Modes of Operation

The wiki supports three ingest modes:

| Mode | When to use | What happens |
|---|---|---|
| **Append** | Small delta, incremental updates | Compute delta via manifest, ingest only new/modified sources |
| **Rebuild** | Major drift, fresh start needed | Archive current wiki to `_archives/`, clear, reprocess all sources |
| **Restore** | Need to go back | Bring back a previous archive |

Use `wiki-status` to see the delta and get a recommendation. Use `wiki-rebuild` for archive/rebuild/restore operations.

## Reference

For details on specific operations, see the companion skills:
- **wiki-status** — Audit what's ingested, compute delta, recommend append vs rebuild
- **wiki-rebuild** — Archive current wiki, rebuild from scratch, or restore from archive
- **wiki-ingest** — Distill source documents into wiki pages and raw text/chat/log data
- **claude-history-ingest** — Ingest Claude conversation history
- **codex-history-ingest** — Ingest Codex CLI session history
- **wiki-query** — Answer questions against the wiki
- **wiki-lint** — Audit and maintain wiki health
- **wiki-setup** — Initialize a new vault

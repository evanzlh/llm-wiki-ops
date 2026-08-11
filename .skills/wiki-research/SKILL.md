---
name: wiki-research
description: >
  Autonomously research a topic via multi-round web search, synthesize findings, and file structured
  results into the Obsidian wiki. Use this skill when the user says "/wiki-research [topic]",
  "research X", "find everything about Y", "do a deep dive on Z", "autonomous research on X",
  or wants comprehensive, web-sourced knowledge on a topic filed directly into their wiki.
---

# Wiki Research — Autonomous Multi-Round Research

You are running an autonomous research loop on a topic, synthesizing what you find, and filing the results into the Obsidian wiki as permanent knowledge.

## Before You Start

1. **Resolve mode before work** — the parent agent resolves config and mode with the Config Resolution Protocol in `llm-wiki/SKILL.md`, then reads the owner `AGENTS.md`. The Portable Write Protocol is the only allowed portable mutation path. Select exactly one terminal completion branch after research. Until then, shared preparation is read-only; network queries are read-only with respect to the vault and no worker may write a source or knowledge page.
2. Read `$OBSIDIAN_VAULT_PATH/index.md` to understand what's already in the wiki — don't re-research things the wiki covers well
3. Select the matching read-only hot-context gate:

   **Portable Repository hot context:** Run `obsidian-wiki hot status --json` first, then read the resolved `hot.md` only when it reports current. When stale, run `obsidian-wiki hot inputs --json --pretty` and use those bounded inputs as context, or skip hot context; do not write `hot.md` or mark it current during shared preparation.

   **Personal mode hot context:** Read `<resolved-vault-path>/hot.md` directly if it exists; config resolution supplies the concrete runtime path and does not export a shell variable.
4. Check the resolved vault's `references/research-config.md` if it exists — it may define source preferences, domains to skip, or confidence rules for this vault

When writing internal links in generated pages, apply the link format from `llm-wiki/SKILL.md` (Link Format section) using the `OBSIDIAN_LINK_FORMAT` value.

Confirm the research topic with the user if it's ambiguous. Then proceed.

## Research Configuration (optional)

If `references/research-config.md` exists in the vault, read it and apply any rules it defines:
- Source preferences (e.g., prefer academic sources, avoid certain domains)
- Domains to skip
- Confidence scoring adjustments
- Topic-specific constraints

If the file doesn't exist, proceed with defaults.

## Round 1 — Broad Survey

**Goal:** Get a wide map of the topic.

1. Decompose the topic into **3-5 distinct angles** (e.g., for "vector databases": what they are, when to use them, leading implementations, trade-offs, production gotchas)
2. For each angle, run **2-3 `WebSearch` queries** using varied phrasing
3. For the top 2-3 results per angle, use `WebFetch` (or `defuddle <url>` if available — cleaner extraction) to get content
4. From each fetched page, extract:
   - **Key claims** — what the source explicitly states
   - **Concepts** — ideas, terms, frameworks introduced
   - **Entities** — tools, people, organizations mentioned
   - **Contradictions** — places where sources disagree with each other

Track what's covered and what's missing as you go.

## Round 2 — Gap Fill

**Goal:** Close the holes left by Round 1.

Review what Round 1 produced:
- What questions did sources raise but not answer?
- Where do sources contradict each other?
- Which angles got thin coverage?

Run **up to 5 targeted searches** specifically addressing these gaps. Prefer primary sources, official documentation, and authoritative analyses over link aggregators.

Add findings to your working set. Update the contradiction list.

## Round 3 — Synthesis Check

**Goal:** Resolve contradictions; confirm depth is sufficient.

If major contradictions remain unresolved:
- Run one final targeted pass (2-3 searches) to find authoritative resolution
- If resolution is impossible, flag the contradiction explicitly in the synthesis page

If contradictions are minor or the topic feels well-covered after Round 2, skip additional searching and proceed to filing.

**Halt condition:** Stop when depth is achieved or 3 rounds are complete — do not loop indefinitely.

## Shared in-memory filing plan

Prepare one coherent plan for both completion branches. No source, candidate, or live-vault file is written in this phase.

- **reference candidates:** one per accepted major source, with `category: references`, URL/origin, summary, limitations, claim citations, `provenance`, a source-quality-derived `base_confidence`, and `lifecycle: draft`.
- **concept candidates:** one per substantive concept, with semantic frontmatter, cross-source claims, links to at least two supporting references when available, `provenance`, calibrated `base_confidence`, and `lifecycle: draft`.
- **entity candidates:** one per significant tool, organization, or person, linked to the concepts and references that establish it, with the same provenance/confidence/lifecycle fields.
- **master synthesis candidate:** `synthesis/Research: <Topic>.md`, with the sections `Overview`, `Key Findings`, `Core Concepts`, `Entities & Tools`, `Contradictions & Open Questions`, and `Sources Consulted`. Its frontmatter uses `category: synthesis`, research/domain tags, all accepted source IDs, a summary, `provenance`, multi-source `base_confidence`, and `lifecycle: draft`.

Apply the configured link format, merge with existing semantic owners instead of duplicating them, and plan reciprocal links among all four output classes. Keep timestamps unresolved until the selected completion branch supplies transaction `started_at` or the Personal runtime timestamp.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode. Keep the repository root as the command CWD. The network queries are read-only with respect to the vault; only the reviewed source-snapshot and transaction phases below may write.

1. The parent agent materializes each accepted web source as a small, reviewable UTF-8 Markdown source snapshot below a configured `sources` root. Record URL, retrieval time, content hash, excerpts, and page-level citations. Review the snapshot against the research results, preserve valid Unicode filenames and Source IDs, and never persist an absolute runtime path. If adequate evidence cannot be materialized and reviewed, stop before a transaction.
2. Compute complete authoritative source closure from all accepted snapshots plus the existing Source IDs of pages to replace/delete; use repository-relative IDs, never compiled vault pages. Run `obsidian-wiki transaction begin --source <source-id> [--source <source-id> ...] --json --pretty`, retain its absolute `candidate_vault` only in memory, and use `started_at`: new pages use `created = updated = started_at`; updates preserve the existing `created` and set `updated = started_at`.
3. Materialize the shared in-memory filing plan as candidate knowledge pages only below `candidate_vault`. New page `sources` contains non-empty relevant accepted snapshot Source IDs. Updated or merged page `sources` must preserve every existing Source ID that still supports retained content, add the new relevant accepted snapshot Source IDs, deduplicate them, and remain a non-empty subset of the frozen transaction source closure. Declare approved removals with `obsidian-wiki transaction delete <id> <vault-relative-page> --json --pretty`.
4. Whenever a candidate transaction is present, run `obsidian-wiki transaction validate <id> --json --pretty`. Review every warning, Fix every issue, and run `obsidian-wiki transaction commit <id> --json --pretty` only after validation passes.
5. On failure, use status-aware recovery: first read the failure response envelope's `recovery.preferred_action`; next run `obsidian-wiki transaction list --json --pretty`; cross-check the refreshed record's `recommended_action` and `allowed_actions`; only then execute exactly the reported recommended or preferred action whose prerequisites hold. Status mapping: for active or preflight failure, fix candidates, validate, then commit, or abort when that is the chosen allowed action; for promoting, restore; for failed, use only a reported allowed retry, restore, abort, or discard; for complete or restored, accept the reported terminal state and make no further mutation. If there is no trusted transaction ID or the outcome is ambiguous, stop and report rather than guessing.
6. Only after commit succeeds or recovery is fully resolved, run `obsidian-wiki hot status --json`. If stale, run `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to write the semantic `hot.md` as the agent, then run `obsidian-wiki hot mark-current --json`.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Materialize the shared in-memory filing plan directly in the live vault, then continue with manifest v1, central-file, QMD, and Personal Git behavior below. Do not fall through into Portable Repository completion.

## Filing — Write Wiki Pages

Organize all findings into wiki pages across four output areas:

### 1. sources/ — One page per major reference

For each significant source (typically 4-8 pages total):

```yaml
---
title: >-
  <Source title>
category: references
tags: [<2-4 domain tags>]
sources:
  - "<URL>"
source_url: "<URL>"
created: <ISO-8601 timestamp>
updated: <ISO-8601 timestamp>
summary: >-
  <1-2 sentences describing what this source covers, ≤200 chars>
provenance:
  extracted: 0.X
  inferred: 0.X
  ambiguous: 0.X
base_confidence: <0.17 + 0.5 × classify(url) for a single source>
lifecycle: draft
lifecycle_changed: <ISO date today>
---
```

Body: title, URL, what it covers, key claims (with provenance markers), limitations.

### 2. concepts/ — One page per substantive concept

For each significant concept surfaced across sources:

Standard concept frontmatter + body. Link concepts to each other and to source pages.

### 3. entities/ — Tools, organizations, people

For each significant entity encountered (tools, libraries, companies, key authors):

Standard entity frontmatter. Link back to concepts that use the entity and sources where it appears.

### 4. synthesis/Research: [Topic].md — Master synthesis

The primary output: a structured synthesis of everything found.

```yaml
---
title: >-
  Research: <Topic>
category: synthesis
tags: [<3-5 domain tags>, research]
sources: [<list of source URLs or page paths>]
created: <ISO-8601 timestamp>
updated: <ISO-8601 timestamp>
summary: >-
  Synthesis of <N>-round research on <topic>. Covers <core findings in ≤200 chars>.
provenance:
  extracted: 0.X
  inferred: 0.X
  ambiguous: 0.X
base_confidence: <min(N_unique_sources/3,1.0)×0.5 + avg_source_quality×0.5>
lifecycle: draft
lifecycle_changed: <ISO date today>
---

# Research: <Topic>

## Overview
<2-4 sentence executive summary of what the research found>

## Key Findings
<Bulleted list of the most important claims, each with a [[source page]] citation>

## Core Concepts
<Links to concept pages created, with one-line descriptions>

## Entities & Tools
<Links to entity pages, with one-line descriptions>

## Contradictions & Open Questions
<Where sources disagree or where the research hit limits>

## Sources Consulted
<Linked list of all source pages>
```

## Cross-linking

After filing all pages:
- Every concept page should link to at least 2 source pages
- Every source page should link to the concept pages it informed
- The synthesis page should link to all concept, entity, and source pages produced

Check `index.md` for existing pages on the same topics — merge into existing pages rather than creating duplicates.

## Update Tracking Files

**`.manifest.json`** — Add a `research` entry:
```json
{
  "type": "research",
  "topic": "<topic>",
  "researched_at": "TIMESTAMP",
  "rounds_completed": 3,
  "sources_fetched": N,
  "pages_created": ["..."],
  "pages_updated": ["..."]
}
```

**`index.md`** — Add all new pages under their respective sections.

**`log.md`** — Append:
```
- [TIMESTAMP] WIKI_RESEARCH topic="<topic>" rounds=N sources_fetched=N pages_created=M
```

**`hot.md`** — Update **Recent Activity** with the research topic and core finding. Update **Active Threads** if this is ongoing. Update `updated` timestamp.

## Quality Checklist

- [ ] 3 rounds completed (or halted at sufficient depth)
- [ ] Synthesis page exists at `synthesis/Research: [Topic].md`
- [ ] Source pages written for major references
- [ ] Concept and entity pages written for significant items
- [ ] Contradictions flagged in synthesis page
- [ ] All pages cross-linked
- [ ] `index.md`, `log.md`, `hot.md`, `.manifest.json` updated

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

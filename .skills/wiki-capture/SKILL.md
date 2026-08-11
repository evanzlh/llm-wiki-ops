---
name: wiki-capture
description: >
  Save the current conversation as a permanent, structured wiki note. Use this skill when the user
  says "save this", "/wiki-capture", "capture this", "file this conversation", "preserve this",
  "add this to my wiki", or wants to turn what was just discussed into lasting knowledge. The skill
  classifies the content, rewrites it as declarative knowledge (not a chat transcript), and places
  it in the correct vault category. Also supports a fast QUICK MODE (`/wiki-capture --quick`, "quick
  capture", "capture this finding", "save this bug fix", "save this gotcha", "drop this to raw", "quick
  save to wiki") that drops findings to the `_raw/` staging area in under 60 seconds with no manifest
  or index writes — used by the session-end Stop hook to auto-preserve findings. Accepts inline
  named-vault routing like "@research save this" via the shared Config Resolution Protocol.
---

# Wiki Capture — Conversation to Wiki Note

You are preserving knowledge from the current conversation as a permanent wiki note. The goal is to extract the *substance* — the knowledge itself — not a summary of what was said.

This skill has three modes:

- **Full mode (default)** — classify the content and write a finished, cross-linked wiki page directly into the right category. This is the rest of this document (Steps 1–7).
- **Quick mode (`--quick`)** — zero-friction staging: drop findings to `_raw/` in under 60 seconds with no manifest/index/log/QMD writes. Used for mid-session capture and by the session-end Stop hook. See below, then stop — do **not** run the full-mode steps.
- **Correction mode (`--correction`)** — capture one atomic correction as derived knowledge while leaving the immutable conversation/source untouched. Use the template below, then update only the derived consumers and tracking links.

**Portable Write Protocol branch:** The parent agent resolves config and mode with the Config Resolution Protocol in `llm-wiki/SKILL.md`, reads the owner `AGENTS.md`, and Select exactly one terminal completion branch. Until then, shared preparation is read-only; do not write `_raw/`, a knowledge page, or a source snapshot before selecting the branch.

## Shared read-only capture decision

Perform this in memory before selecting either completion branch:

1. **Select capture submode.** Choose Full, Quick, or Correction (`--correction`) from the request before applying any later rule.
2. **KEEP or SKIP.** SKIP purely conversational or inconclusive material with no reusable decision, verified finding, workaround, or non-obvious lesson. KEEP when the user explicitly requests capture or the conversation contains a durable decision, confirmed behavior, debugging conclusion, reusable pattern, or valuable synthesis. An automatic Stop-hook capture should err toward SKIP; an explicit user capture should err toward KEEP.
3. **Classify the kept content.** Choose the semantic category and matching final path: concept, entity, skill, reference, synthesis, journal, or project knowledge. Cluster related findings by topic and infer project context only from evidence in the conversation.
4. **Rewrite it as declarative knowledge.** Preserve the substance, evidence, reasoning, implications, and relationships without presenting it as a chat transcript. Prepare required frontmatter and links in memory; make no source or vault write here.

## Correction Mode (`--correction`) shared contract

For Correction submode, leave the immutable source untouched and Record exactly one atomic claim pair. `speaker_type` is semantic and independent of a serialized message `role`. Never include raw transcript excerpts.

```yaml
correction_id: <stable-id>
source_locator: <immutable file:line or channel/thread/timestamp>
source_text_sha256: <64 lowercase hex chars>
serialized_role: <source role, if present>
speaker_type: user | assistant | teammate | tool_result | slack_member
original_claim:
  subject: <exact entity or capability>
  assertion: <single atomic value>
corrected_claim:
  subject: <same exact entity or capability>
  assertion: <single atomic value or null>
authority_class: contract | decision | code | test | deploy | runtime | db | narrative
verification_state: verified | inferred | unverified | contradicted
asserted_at: <ISO-8601 timestamp>
effective_at: <ISO-8601 timestamp or null>
as_of: <ISO-8601 timestamp>
supersedes: [<original-claim-id>]
consumer_propagation:
  kw: open | not_applicable | complete
  ob: open | not_applicable | complete
  requirements: open | not_applicable | complete
  code: open | not_applicable | complete
  tests: open | not_applicable | complete
  ai_memory: open | not_applicable | complete
corrected_at: <ISO-8601 timestamp>
```

Before any derived write, compute `source_pre_sha256` directly from the authoritative source and require equality with `source_text_sha256`. After all derived consumers are prepared, recompute `source_post_sha256`; require `source_pre_sha256 == source_post_sha256 == source_text_sha256`. Track consumer propagation independently and mark a consumer complete only after verification. Keep secrets, source copies, and raw excerpts out of the correction record.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode. Keep the repository root as the command CWD and never write live `_raw/`.

1. Use the selected submode and completed shared read-only capture decision. For SKIP, report and stop; do not create a source snapshot, transaction, operation journal, or hot refresh.
2. For kept Full/Quick content, the parent writes a small, reviewable UTF-8 Markdown or plain-text snapshot below a configured `sources` root. Include origin, capture time, content hash, and the exact captured text; review and accept it before continuing. Preserve valid Unicode Source IDs and filenames and never persist an absolute runtime path. A quick/raw-only request is unsupported when the user will not authorize a source snapshot: report that boundary and stop before any write or transaction.
3. **Portable correction:** apply the shared atomic correction contract to the immutable source. Use its existing authoritative Source IDs and the source IDs already cited by affected live pages; this path does not create an ordinary conversation snapshot. If authority is insufficient or the source hash/locator cannot be verified, fail closed before `transaction begin`.
4. Compute complete authoritative source closure from the source IDs selected by the active submode and the existing Source IDs of every updated/deleted page. Run `obsidian-wiki transaction begin --source <source-id> [--source <source-id> ...] --json --pretty`. Keep the absolute `candidate_vault` only in memory. New candidates use `created = updated = started_at`; updates preserve the existing `created` and set `updated = started_at`.
5. Write the finished candidate only below `candidate_vault`. New page `sources` contains non-empty relevant accepted snapshot Source IDs for ordinary Full/Quick capture; a new Correction page instead cites its verified existing authoritative Source IDs. Updated or merged page `sources` must preserve every existing Source ID that still supports retained content, add the new relevant accepted snapshot Source IDs when present, deduplicate them, and remain a non-empty subset of the frozen transaction source closure. Correction candidates update every affected derived consumer independently and preserve the immutable source. Declare removals with `obsidian-wiki transaction delete <id> <vault-relative-page> --json --pretty`.
6. Whenever a candidate transaction is present, run `obsidian-wiki transaction validate <id> --json --pretty`. Review every warning, Fix every issue, and run `obsidian-wiki transaction commit <id> --json --pretty` only after validation passes.
7. On failure, use status-aware recovery: first read the failure response envelope's `recovery.preferred_action`; next run `obsidian-wiki transaction list --json --pretty`; cross-check the refreshed record's `recommended_action` and `allowed_actions`; only then execute exactly the reported recommended or preferred action whose prerequisites hold. Status mapping: for active or preflight failure, fix candidates, validate, then commit, or abort when that is the chosen allowed action; for promoting, restore; for failed, use only a reported allowed retry, restore, abort, or discard; for complete or restored, accept the reported terminal state and make no further mutation. If there is no trusted transaction ID or the outcome is ambiguous, stop and report rather than guessing.
8. Only after commit succeeds or recovery is fully resolved, run `obsidian-wiki hot status --json`. If stale, run `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to write the semantic `hot.md` as the agent, then run `obsidian-wiki hot mark-current --json`.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Apply the shared submode decision. Quick and Full retain direct `_raw/`, manifest v1, central-file, QMD, and Personal Git snapshot behavior. Personal Correction applies the shared atomic correction/hash/consumer-propagation contract, writes the derived correction directly, then updates manifest v1 and `log.md`; it never edits or copies the immutable source. Config resolution provides concrete runtime values; it does not export them into the parent shell. Do not fall through into Portable Repository completion.

## Quick Mode (`--quick`)

Trigger when invoked as `/wiki-capture --quick`, by "quick capture" / "capture this finding" / "save this bug fix" / "save this gotcha" / "drop this to raw" / "quick save to wiki", or automatically by the session-end Stop hook.

**Speed contract:** Inline only. No subagents. No QMD. No manifest/`index.md`/`log.md`/`hot.md` writes. Target: <60 seconds. Promotion to full wiki pages happens later via `/wiki-ingest`.

1. **Resolve config** (Config Resolution Protocol in `llm-wiki/SKILL.md`): get `OBSIDIAN_VAULT_PATH` and `OBSIDIAN_RAW_DIR` (default: `$OBSIDIAN_VAULT_PATH/_raw`).

   Ensure `$OBSIDIAN_RAW_DIR` exists; create it if not, then continue below.

   Capture does not independently reinterpret validator schema inputs. When `OBSIDIAN_ALLOWED_LIFECYCLES`, `OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES`, `OBSIDIAN_REQUIRED_TRUST_FIELDS`, or `OBSIDIAN_SCHEMA_SOURCE` is present, preserve it for the downstream lint/trust consumer: CLI values take precedence over environment/config values, which take precedence over framework defaults, and explicit blank or whitespace-only values fail closed. Omit a variable to use defaults.

2. **Write raw files** — for each kept topic cluster, write `$OBSIDIAN_RAW_DIR/<ISO-date>-<slug>.md`. Use a kebab-case slug (for example, `swift-actor-reentrancy`). Read `references/RAW-FORMAT.md` for the full frontmatter spec, finding-block body structure, and provenance/confidence calibration. Per-cluster fields that vary: `title`, `tags` (2–4 from taxonomy), `summary` (≤200 chars), `project` (inferred or `null`), `base_confidence` (0.6 discussed → 0.75 fix applied → 0.9 test confirmed), `provenance.extracted`/`provenance.inferred` (sum to 1.0), `lifecycle_changed` (today), `sources` (`"<project> session (<YYYY-MM-DD>)"`).

3. **Confirm** — list staged files and tell the user to run `/wiki-ingest` to promote them:
   ```
   Staged to _raw/:
     _raw/2026-05-27-swift-actor-reentrancy.md   — "Actor reentrancy causes deadlock in async forEach"
   Run /wiki-ingest to promote these to full wiki pages.
   ```
   Quick mode deliberately does **not** write the manifest, `index.md`, `log.md`, `hot.md`, or refresh QMD — promotion via `/wiki-ingest` handles all of that. **Stop here; do not run the full-mode steps below.**

---

## Correction Mode (`--correction`)

Use this mode when a user or stronger authority corrects a claim derived from an immutable conversation, tool result, or other raw source. Never edit or copy the raw source. Resolve config, read the vault `AGENTS.md`, and update an existing derived page when one owns the claim; otherwise create the smallest owner-compliant derived correction page.

Record exactly one atomic claim pair. `speaker_type` is semantic and must be assessed independently of a serialized message `role` (a tool result may be serialized as `role=user`). Do not include raw transcript excerpts.

```yaml
correction_id: <stable-id>
source_locator: <immutable file:line or channel/thread/timestamp>
source_text_sha256: <64 lowercase hex chars>
serialized_role: <source role, if present>
speaker_type: user | assistant | teammate | tool_result | slack_member
original_claim:
  subject: <exact entity or capability>
  assertion: <single atomic value>
corrected_claim:
  subject: <same exact entity or capability>
  assertion: <single atomic value or null>
authority_class: contract | decision | code | test | deploy | runtime | db | narrative
verification_state: verified | inferred | unverified | contradicted
asserted_at: <ISO-8601 timestamp>
effective_at: <ISO-8601 timestamp or null>
as_of: <ISO-8601 timestamp>
supersedes: [<original-claim-id>]
consumer_propagation:
  kw: open | not_applicable | complete
  ob: open | not_applicable | complete
  requirements: open | not_applicable | complete
  code: open | not_applicable | complete
  tests: open | not_applicable | complete
  ai_memory: open | not_applicable | complete
corrected_at: <ISO-8601 timestamp>
```

Before any derived write, compute `source_pre_sha256` directly from the immutable source and require it to equal `source_text_sha256`. After writing the correction and updating derived consumers, recompute `source_post_sha256` from the same locator. Abort and report an immutability violation unless `source_pre_sha256 == source_post_sha256 == source_text_sha256`. This verification is mandatory even when the correction write succeeds.

After writing the derived correction, link the immutable source to the created/updated page through `.manifest.json`, append only the correction ID and affected-page counts to `log.md`, and propagate the atomic correction to every consumer independently. Mark a consumer `complete` only after verifying that consumer; do not collapse mixed results into a single aggregate status. Keep secrets, raw excerpts, and source copies out of the correction record.

---

## Full Mode

## Before You Start

1. **Resolve config** — follow the Config Resolution Protocol in `llm-wiki/SKILL.md` (inline `@name` override → walk up CWD for `.env` → `~/.obsidian-wiki/config` → prompt setup). This gives `OBSIDIAN_VAULT_PATH` and `OBSIDIAN_LINK_FORMAT` (default: `wikilink`).

2. Read `$OBSIDIAN_VAULT_PATH/index.md` to understand existing wiki content (avoid duplicates)
3. Read `$OBSIDIAN_VAULT_PATH/hot.md` if it exists — it gives context on recent activity

When writing internal links in Step 5, apply the link format from `llm-wiki/SKILL.md` (Link Format section) using the `OBSIDIAN_LINK_FORMAT` value.

## Step 1: Identify What's Worth Preserving

Scan the conversation. Ask: what knowledge emerged here that would be valuable in 3 months with no memory of this chat?

Worth preserving:
- Decisions made and *why* they were made
- Analysis, frameworks, mental models developed
- Technical findings, patterns, or procedures
- Synthesized understanding of a topic
- Clear explanations of a concept that took effort to arrive at
- Key facts from an external source discussed in the conversation

Skip:
- Logistics, scheduling, pleasantries
- Exploratory back-and-forth where no conclusion was reached
- Content that's already in the wiki

If nothing material emerged, tell the user and stop.

## Step 2: Classify the Content Type

Assign one of five types — this determines the target folder and tone:

| Type | Description | Target folder |
|---|---|---|
| `synthesis` | Multi-step analysis or an answer to a specific question that required reasoning | `synthesis/` |
| `concept` | A definition, framework, or mental model (what a thing *is*) | `concepts/` |
| `source` | Summary of an external document, article, or resource discussed | `references/` |
| `decision` | A strategic, architectural, or design choice and its rationale | `synthesis/` |
| `session` | A complete discussion summary when the conversation spans multiple topics | `journal/` |

If the content clearly belongs to a specific project (detected from context or user mention), place it under `projects/<project-name>/<category>/` instead.

## Step 3: Rewrite as Declarative Knowledge

Do **not** write a summary of the conversation. Write the knowledge itself, in declarative present tense:

- Not: "The user asked about X and Claude explained that..."
- Yes: "X works by..."
- Not: "We decided to use Y because..."
- Yes: "Y is preferred over Z because [reason]. [^[inferred] if the rationale was implied, not stated explicitly]"

Apply provenance markers per `llm-wiki`:
- *Extracted* — explicitly stated in the conversation (no marker)
- *Inferred* — generalized or synthesized from the conversation → `^[inferred]`
- *Ambiguous* — disputed, uncertain, or contradictory → `^[ambiguous]`

## Step 4: Generate a Slug and Title

Derive a clear, descriptive title from the content. Slugify it:
- Lowercase, words separated by hyphens
- Max 50 characters
- Avoid dates in the slug (the frontmatter has `created`)

## Step 5: Write the Wiki Note

Create the file at the target path with required frontmatter:

```yaml
---
title: >-
  <Title>
category: <synthesis|concepts|references|journal|skills>
tags: [<2-5 domain tags from taxonomy>]
sources:
  - conversation:<ISO-date>
created: <ISO-8601 timestamp>
updated: <ISO-8601 timestamp>
summary: >-
  <1-2 sentences, ≤200 chars, answering "what knowledge does this page hold?">
provenance:
  extracted: 0.X
  inferred: 0.X
  ambiguous: 0.X
base_confidence: 0.42
lifecycle: draft
lifecycle_changed: <ISO date today>
---
```

Body structure by type:

**synthesis / decision:**
```markdown
# Title

## Context
<What prompted this — the problem or question being addressed>

## Finding / Decision
<The core knowledge or conclusion>

## Reasoning
<Why this is the case or why this choice was made>

## Implications
<What follows from this — what to watch for, next steps, trade-offs>

## Related
<[[wikilinks]] to connected pages>
```

**concept:**
```markdown
# Title

<Definition in one clear sentence.>

## What It Is
<Explanation of the concept>

## How It Works
<Mechanism or structure>

## When to Use
<Applicability, conditions, trade-offs>

## Related
<[[wikilinks]]>
```

**source:**
```markdown
# Title

> Source: <title or URL>

## What It Covers
<What the source is about>

## Key Points
<Bulleted claims with provenance markers>

## Open Questions
<What it raises but doesn't answer — omit if none>

## Related
<[[wikilinks]]>
```

**session:**
```markdown
# Title

*Session captured: <date>*

## Topics Covered
<Brief list>

## Key Takeaways
<The 3-5 most important things that emerged>

## Decisions Made
<Any explicit decisions, with rationale>

## Open Questions
<What remains unresolved>

## Related
<[[wikilinks]]>
```

Every note must link to at least 2 existing wiki pages. Search `index.md` before writing. If fewer than 2 related pages exist, create minimal stubs for the most important concepts referenced.

## Step 6: Update Tracking Files

**`index.md`** — Add the new page under its category section.

**`log.md`** — Append:
```
- [TIMESTAMP] CAPTURE type=<type> page="<path>" title="<title>"
```

**`hot.md`** — Update **Recent Activity** with what was just captured. Update **Key Takeaways** if the note introduced something worth flagging. Update `updated` timestamp.

## Step 7: Confirm to User

Report the saved path and title:
```
Saved to: projects/<name>/synthesis/<slug>.md
Title: <Title>
Type: synthesis
```

## Quality Checklist

- [ ] Content rewritten as declarative knowledge (not a chat transcript)
- [ ] Type classified correctly; target path is in the right folder
- [ ] Frontmatter complete with title, category, tags, sources, summary, provenance
- [ ] At least 2 wikilinks to existing pages
- [ ] `index.md`, `log.md`, and `hot.md` updated
- [ ] Confirmed save path to user

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

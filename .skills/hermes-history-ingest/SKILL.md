---
name: hermes-history-ingest
description: >
  Ingest Hermes agent history into the Obsidian wiki. Use this skill when the user wants to mine
  their past Hermes sessions for knowledge, import their ~/.hermes folder, extract insights from
  previous Hermes conversations, or says things like "process my Hermes history", "add my Hermes
  memories to the wiki", "ingest ~/.hermes", or "what have I worked on in Hermes". Also triggers
  when the user mentions Hermes memories, Hermes sessions, ~/.hermes/memories, or Hermes skill logs.
---

# Hermes History Ingest — Conversation & Memory Mining

You are extracting knowledge from the user's Hermes agent history and distilling it into the Obsidian wiki. Hermes stores both free-form memories and structured session transcripts — focus on durable knowledge, not operational telemetry.

This skill can be invoked directly or via the `wiki-history-ingest` router (`/wiki-history-ingest hermes`).

## Before You Start

1. **Resolve config and ownership** — follow the Config Resolution Protocol in
   `llm-wiki/SKILL.md`: explicit `@name`, nearest ancestor
   `.obsidian-wiki/config.toml`, nearest ancestor `.env` containing
   `OBSIDIAN_VAULT_PATH`, `~/.obsidian-wiki/config`, then setup guidance. The
   parent agent resolves config and mode, records the concrete vault and Hermes
   history paths, and reads the owner `AGENTS.md` at the resolved vault.
2. Select one terminal workflow after the shared analysis and page-preparation steps:
   **Portable Repository completion** or **Personal mode completion**. Never
   mix their writes or tracking. The Portable branch implements the canonical
   Portable Write Protocol locally. Shared discovery, memory/session extraction,
   clustering, and drafting are read-only. If work is divided, use
   analysis-only workers: they return inventories, evidence, and proposals but
   do not resolve mode, snapshot sources, begin transactions, or mutate files.
   The parent agent owns completion.
3. **Read mode-appropriate state.** Personal mode reads manifest v1 and
   `index.md` from the concrete vault. Portable Repository mode may inspect
   existing knowledge pages read-only, but Hermes memories/session caches are
   transient until the parent creates reviewed snapshots; never parse manifest
   v2 as a Personal source map. Personal append mode uses manifest v1.
   Portable append mode compares discovered agent/session identity and content hash against existing reviewed snapshots.

## Ingest Modes

### Append Mode (default)

Personal mode: check manifest v1 for each source file. Portable Repository mode: compare discovered agent/session identity and content hash against existing reviewed snapshots.
In either mode, only process:

- Files not in the manifest (new memory files, new session logs)
- Files whose modification time is newer than `ingested_at` in the manifest

Use this mode for regular syncs.

### Full Mode

Process everything regardless of manifest. Use after `wiki-rebuild` or if the user explicitly asks for a full re-ingest.

## Hermes Data Layout

Hermes stores all local artifacts under `~/.hermes/` (or `$HERMES_HOME` for non-default profiles).

```
~/.hermes/
├── memories/                          # Persistent agent memories (markdown or JSON)
│   └── *.md / *.json
├── skills/                            # Installed skills (read-only for ingest purposes)
│   └── <skill-name>/SKILL.md
├── sessions/                          # Session transcripts (if session logging is enabled)
│   └── YYYY-MM-DD/
│       └── <session-id>.jsonl
├── config.yaml                        # User config (model, theme, paths)
└── .hub/                              # Skills Hub state (lock.json, audit.log, quarantine/)
```

### Key data sources ranked by value

1. `memories/*.md` / `memories/*.json` — highest signal; curated persistent knowledge the agent accumulated
2. `sessions/**/*.jsonl` — structured turn-by-turn transcripts; rich but noisy
3. `config.yaml` — metadata only (model preferences, paths); rarely worth ingesting

Skip `.hub/` internals (audit/quarantine state) and the `skills/` directory (source material, not user knowledge).

## Step 1: Survey and Compute Delta

Scan `HERMES_HISTORY_PATH` and compare against `.manifest.json`:

- `~/.hermes/memories/`
- `~/.hermes/sessions/**/` (if present)

Classify each file:

- **New** — not in manifest
- **Modified** — in manifest but file is newer than `ingested_at`
- **Unchanged** — already ingested and unchanged

Report a concise delta summary before deep parsing.

## Step 2: Parse Memories First

Memories are the highest-value source. Hermes writes them as either:

- **Markdown** — structured prose with optional frontmatter; ingest directly
- **JSON** — `{"content": "...", "created_at": "...", "tags": [...]}` records

For each memory:

- Extract the core knowledge claim
- Note any tags Hermes attached (they often map to wiki categories)
- Merge into the appropriate wiki page rather than creating one memory = one page

## Step 3: Parse Session JSONL Safely

Each session JSONL line is an event envelope. Common shapes:

```json
{"role": "user", "content": "..."}
{"role": "assistant", "content": "..."}
{"type": "tool_use", "name": "...", "input": {...}}
{"type": "tool_result", "content": "..."}
```

### Extraction rules

- Prioritize assistant responses that state conclusions, patterns, or decisions
- Extract user intent from high-signal turns; skip low-information follow-ups
- Treat `tool_use` / `tool_result` pairs as context, not primary content
- Skip token accounting, internal plumbing, and repeated plan echoes

### Critical privacy filter

Session logs can include injected instructions, tool payloads, and sensitive text. Do not ingest verbatim.

- Remove API keys, tokens, passwords, credentials
- Redact private identifiers unless relevant and user-approved
- Summarize; do not quote raw transcripts verbatim

## Step 4: Cluster by Topic

Do not create one wiki page per memory or session.

- Group memories by stable topic (concept, tool, project, technique)
- Split mixed sessions into separate themes
- Merge recurring patterns across dates and projects
- Use file paths or session `cwd` metadata to infer project scope when available

## Step 5: Distill into Wiki Pages

Route extracted knowledge using existing wiki conventions:

- Project-specific architecture/process → `projects/<name>/...`
- General concepts → `concepts/`
- Recurring techniques/debug playbooks → `skills/`
- Tools/services/frameworks → `entities/`
- Cross-session patterns → `synthesis/`

For each impacted project, create/update `projects/<name>/<name>.md`.

### Writing rules

- Distill knowledge, not chronology
- Avoid "on date X we discussed..." unless date context is essential
- Add `summary:` frontmatter on each new/updated page (1–2 sentences, ≤ 200 chars)
- Add confidence and lifecycle fields to every new page:
  ```yaml
  base_confidence: 0.42
  lifecycle: draft
  lifecycle_changed: <ISO date today>
  ```
  Leave `lifecycle` unchanged on update.
- Add provenance markers:
  - `^[extracted]` when directly grounded in explicit memory/session content
  - `^[inferred]` when synthesizing patterns across multiple memories
  - `^[ambiguous]` when memories conflict
- Add/update `provenance:` frontmatter mix for each changed page

## Privacy and Compliance

- Distill and synthesize; avoid raw memory or transcript dumps
- Default to redaction for anything that looks sensitive
- Ask the user before storing personal or sensitive details
- Keep references to other people minimal and purpose-bound

## Reference

See `references/hermes-data-format.md` for field-level notes and extraction guidance.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode.
The external history cache and selected session files are transient analysis input,
never Portable Source IDs. Hermes memory files outside the repository remain
external even when already Markdown.

1. **Materialize source authority first.** The parent agent creates one small,
   reviewable UTF-8 Markdown or plain-text snapshot strictly below the configured
   `sources` root for every selected Hermes memory/session or coherent slice.
   Record agent identity, session identity (or stable memory identity), relevant excerpts,
   source timestamps, and a content hash. Redact secrets/private identifiers,
   replace local paths with repository-relative project labels, and include
   no machine-local absolute paths. Preserve valid Unicode in content, filenames,
   and Source IDs exactly. If an adequate snapshot cannot be created with safe,
   traceable evidence, stop or use Personal mode. Candidate pages cite only
   snapshot Source IDs, never `.hermes` paths, live URLs, or pseudo-sources.
2. **Compute full source closure before `transaction begin`.** Include every
   existing `sources` Source ID from pages updated or deleted plus every new
   snapshot Source ID. The set is immutable.
3. **Begin once.** Keep the repository root as the command CWD and run
   `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty`.
   Record `transaction_id`, runtime-only absolute `candidate_vault`,
   `started_at`, and Source IDs; do not `cd` into it or persist that path.
4. **Write candidates.** New pages use `created = updated = started_at`; updates
   preserve the existing `created` and set `updated = started_at`. Write only
   final vault-relative knowledge pages with a non-empty transaction-source subset.
5. **Declare removals** using
   `obsidian-wiki transaction delete <id> <vault-relative-page.md>`. Unsupported
   control/non-page mutations stop without a live-vault write.
6. **Validate and commit.** Run
   `obsidian-wiki transaction validate <id> --json --pretty`. Review every warning;
   warnings do not block commit. Fix every issue and rerun validation. Commit
   only a passing report with
   `obsidian-wiki transaction commit <id> --json --pretty`.
7. **Use status-aware recovery.** Follow only a trusted
   `recovery.preferred_action` or a reported alternative whose prerequisites
   hold. Confirm the retained record with
   `obsidian-wiki transaction list --json`; its `recommended_action` must agree
   and the command must be in `allowed_actions`. Fix/revalidate an active
   preflight failure or run `obsidian-wiki transaction abort <id> --json`. A
   `promoting` record permits only its reported
   `obsidian-wiki transaction restore <id> --json`. For a `failed` record,
   prefer `obsidian-wiki transaction retry <id> --json`; use
   `obsidian-wiki transaction restore <id> --json` or
   `obsidian-wiki transaction discard <id> --json` only when allowed and its
   prerequisites hold. A configuration/begin failure with no trusted transaction ID,
   or an empty list, has no recovery action. Never replace a transaction while
   its outcome is ambiguous.
8. **Refresh local hot context only after commit succeeds or recovery is fully resolved.**
   Run `obsidian-wiki hot status --json`; if stale, run
   `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to write
   the semantic `hot.md` as the agent, then run
   `obsidian-wiki hot mark-current --json`.
9. Report selected memories/sessions, snapshot Source IDs, page changes,
   warnings, recovery, and hot status.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Keep the
concrete vault, Hermes history, QMD CLI, and QMD collection values in agent
memory: config resolution does not export these values into the parent shell.
Write the prepared pages directly below `<resolved-vault-path>`, using current
ISO timestamps and preserving `created` on updates.

### Personal direct writes and Git safety

Apply any owner-required Personal Git snapshot to the concrete resolved vault
before direct writes. Write or merge prepared pages at their final paths below
`<resolved-vault-path>` and stop before tracking on failure.

### Personal manifest v1 and cache

For each memory/session file, update `<resolved-vault-path>/.manifest.json` as
manifest v1 with `ingested_at`, `size_bytes`, `modified_at`, `source_type`
(`hermes_memory` or `hermes_session`), project, and page lists. Preserve
canonical expanded absolute Personal source keys and unrelated entries. Retain:

```json
{
  "hermes": {
    "source_path": "<resolved-hermes-history-path>",
    "last_ingested": "TIMESTAMP",
    "memories_ingested": 42,
    "sessions_ingested": 7,
    "pages_created": 5,
    "pages_updated": 12
  }
}
```

Record each source mapping with concrete values:

```bash
obsidian-wiki cache-update <resolved-vault-path> <source> --pages <page1> [page2 ...] --json --pretty
```

### Personal central files

Update `<resolved-vault-path>/index.md`. Append to
`<resolved-vault-path>/log.md`:

```text
- [TIMESTAMP] HERMES_HISTORY_INGEST memories=N sessions=M pages_updated=X pages_created=Y mode=append|full
```

Read `<resolved-vault-path>/hot.md`, create the `wiki-ingest` template if
missing, summarize the conceptual Hermes ingest in **Recent Activity**, keep
three operations, and bump `updated`.

### Personal QMD refresh

When the concrete QMD collection is configured, refresh only after all
Personal writes. Failure does not roll back the vault.

```bash
<resolved-qmd-cli> update
<resolved-qmd-cli> embed
<resolved-qmd-cli> get "qmd://<resolved-qmd-wiki-collection>/<page>.md" -l 5
```

Use `embed` only for stale/missing vectors and report refreshed, skipped,
unavailable, or failed status.

Do not fall through into Portable Repository completion. Report the Personal
page, manifest v1, cache, central-file, Personal Git snapshot, and QMD results,
then stop.

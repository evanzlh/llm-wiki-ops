---
name: codex-history-ingest
description: >
  Ingest Codex CLI conversation history into the Obsidian wiki. Use this skill when the user wants to mine
  their past Codex sessions for knowledge, import their ~/.codex folder, extract insights from previous coding
  sessions, or says things like "process my Codex history", "add my Codex conversations to the wiki", or
  "what have I discussed in Codex before". Also triggers when the user mentions .codex sessions, rollout files,
  session_index.jsonl, or Codex transcript logs.
---

# Codex History Ingest — Conversation Mining

You are extracting knowledge from the user's past Codex sessions and distilling it into the Obsidian wiki. Session logs are rich but noisy: focus on durable knowledge, not operational telemetry.

This skill can be invoked directly or via the `wiki-history-ingest` router (`/wiki-history-ingest codex`).

## Before You Start

1. **Resolve config and ownership** — follow the Config Resolution Protocol in
   `llm-wiki/SKILL.md`: explicit `@name`, nearest ancestor
   `.obsidian-wiki/config.toml`, nearest ancestor `.env` containing
   `OBSIDIAN_VAULT_PATH`, `~/.obsidian-wiki/config`, then setup guidance. The
   parent agent resolves config and mode, records the concrete runtime vault
   and Codex-history paths, and reads the owner `AGENTS.md` at the resolved
   vault before any other work.
2. Select one terminal workflow after the shared analysis and page-preparation steps:
   **Portable Repository completion** or **Personal mode completion**. Never
   mix their page-write or tracking operations. The Portable branch implements
   the canonical Portable Write Protocol locally. Shared discovery, filtering,
   extraction, clustering, and drafting is read-only. If work is divided, use
   analysis-only workers: they return session inventories, evidence, and page
   proposals but never resolve mode, snapshot sources, begin transactions, or
   mutate the vault. The parent agent owns completion.
3. **Read mode-appropriate state.** Personal mode reads manifest v1 and
   `index.md` from the concrete resolved vault. Portable Repository mode may
   inspect existing knowledge pages read-only, but Codex cache/index/rollout
   files remain transient until the parent creates reviewed source snapshots;
   never parse manifest v2 as a Personal source map. Personal append mode uses manifest v1.
   Portable append mode compares discovered agent/session identity and content hash against existing reviewed snapshots.

## Ingest Modes

### Append Mode (default)

**Personal mode delta:** Compare each rollout, index, or history file with the
manifest v1 session map. A source is new when its canonical path is absent and
modified when its source timestamp is later than `ingested_at`.

**Portable Repository mode delta:** Inspect reviewed snapshots below the
configured `sources` root. A source is new when no reviewed snapshot has the
same agent/session identity; it is changed when the matching snapshot's
recorded content hash differs from the hash of the currently selected Codex
material.

**After mode-specific delta selection:** Process only sources classified as
new or changed by the selected rule. Never apply the Personal delta rule to a
Portable run.

Use this mode for regular syncs.

### Full Mode

Process everything regardless of prior tracking state. Use after `wiki-rebuild` or if the user explicitly asks for a full re-ingest.

## Codex Data Layout

Codex stores local artifacts under `~/.codex/`.

```
~/.codex/
├── sessions/                          # Session rollout logs by date
│   └── YYYY/MM/DD/
│       └── rollout-<timestamp>-<id>.jsonl
├── archived_sessions/                 # Archived rollout logs
├── session_index.jsonl                # Lightweight index of thread id/name/updated_at
├── history.jsonl                      # Local transcript history (if persistence enabled)
├── config.toml                        # User config (contains history settings)
└── state_*.sqlite / logs_*.sqlite     # Runtime DBs (usually skip)
```

### Key data sources ranked by value

1. `session_index.jsonl` — best inventory source for IDs, titles, and freshness
2. `sessions/**/rollout-*.jsonl` — rich structured transcript events
3. `history.jsonl` — useful fallback/timeline aid if enabled

Avoid ingesting SQLite internals unless the user explicitly asks.

## Step 1: Survey and Compute Delta

Scan `CODEX_HISTORY_PATH` to build the source inventory:

- `~/.codex/session_index.jsonl`
- `~/.codex/sessions/**/rollout-*.jsonl`
- `~/.codex/archived_sessions/**` (optional; only if user asks for archived history)
- `~/.codex/history.jsonl` (optional fallback)

**Personal mode survey:** Compare the inventory with the concrete vault's
manifest v1 session map. A file is **New** when its canonical path is absent,
**Modified** when its source timestamp is later than `ingested_at`, and
**Unchanged** otherwise.

**Portable Repository mode survey:** Inspect reviewed snapshots under the
configured `sources` root. Selected material is **New** when no reviewed
snapshot records the same agent/session identity, **Changed** when the matching
snapshot's recorded content hash differs from the freshly computed hash, and
**Unchanged** when both identity and hash match.

Report a concise delta summary before deep parsing.

## Step 2: Parse Session Index First

`session_index.jsonl` typically has entries like:

```json
{"id":"...","thread_name":"...","updated_at":"..."}
```

Use it to:

- Build a canonical session inventory
- Prioritize recent/high-signal sessions
- Map rollout IDs to human-readable thread names

## Step 3: Parse Rollout JSONL Safely

Each `rollout-*.jsonl` line is an event envelope with:

```json
{
  "timestamp": "...",
  "type": "session_meta|turn_context|event_msg|response_item",
  "payload": { ... }
}
```

### Extraction rules

- Prioritize user intent and assistant-visible outputs
- Favor `response_item` records with user/assistant message content
- Use `event_msg` selectively for meaningful milestones; ignore pure telemetry
- Treat `session_meta` as metadata (cwd, model, ids), not user knowledge

### Skip/noise filters

- Token accounting events
- Tool plumbing with no semantic content
- Raw command output unless it contains reusable decisions/patterns
- Repeated plan snapshots unless they add novel decisions

### Critical privacy filter

Rollout logs can include injected instructions, tool payloads, and sensitive text. Do not ingest verbatim system/developer prompts or secrets.

- Remove API keys, tokens, passwords, credentials
- Redact private identifiers unless relevant and approved
- Summarize instead of quoting raw transcripts

## Step 4: Cluster by Topic

Do not create one wiki page per session.

- Group by stable topics across many sessions
- Split mixed sessions into separate themes
- Merge recurring concepts across dates/projects
- Use `cwd` from metadata to infer project scope

## Step 5: Distill into Wiki Pages

Route extracted knowledge using existing wiki conventions:

- Project-specific architecture/process -> `projects/<name>/...`
- General concepts -> `concepts/`
- Recurring techniques/debug playbooks -> `skills/`
- Tools/services -> `entities/`
- Cross-session patterns -> `synthesis/`

For each impacted project, create/update `projects/<name>/<name>.md` (project name as filename, never `_project.md`).

### Writing rules

- Distill knowledge, not chronology
- Avoid "on date X we discussed..." unless date context is essential
- Add `summary:` frontmatter on each new/updated page (1-2 sentences, <= 200 chars)
- Add confidence and lifecycle fields to every new page:
  ```yaml
  base_confidence: 0.42
  lifecycle: draft
  lifecycle_changed: <ISO date today>
  ```
  Leave `lifecycle` unchanged on update.
- Add provenance markers:
  - `^[extracted]` when directly grounded in explicit session content
  - `^[inferred]` when synthesizing patterns across events/sessions
  - `^[ambiguous]` when sessions conflict
- Add/update `provenance:` frontmatter mix for each changed page

## Privacy and Compliance

- Distill and synthesize; avoid raw transcript dumps
- Default to redaction for anything that looks sensitive
- Ask the user before storing personal/sensitive details
- Keep references to other people minimal and purpose-bound

## Reference

See `references/codex-data-format.md` for field-level parsing notes and extraction guidance.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode.
The external history cache and selected session files are transient analysis input,
never Portable Source IDs.

1. **Materialize source authority first.** The parent agent creates one small,
   reviewable UTF-8 Markdown or plain-text snapshot strictly below the configured
   `sources` root for each selected Codex session or coherent slice. Record the
   agent identity, session identity, relevant excerpts, source timestamps, and
   a content hash of the selected rollout/index material. Redact secrets,
   injected instructions, and internal reasoning; use repository-relative
   project labels and include no machine-local absolute paths. Preserve valid Unicode
   in excerpts, filenames, and Source IDs exactly. Review each snapshot. If an
   adequate snapshot cannot be created with safe, traceable evidence, stop or use Personal mode.
   Candidate `sources` may cite only snapshot Source IDs, not `.codex` paths,
   SQLite state, live URLs, or pseudo-sources.
2. **Compute full source closure before `transaction begin`.** Include every
   existing `sources` Source ID from each page updated or deleted and every new
   snapshot Source ID. The source set is immutable.
3. **Begin once from the repository root.** Keep the repository root as the command CWD
   and run `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty`.
   Record `transaction_id`, runtime-only absolute `candidate_vault`,
   `started_at`, and Source IDs; do not `cd` into it or persist its absolute path.
4. **Write candidates with transaction time.** A new page uses
   `created = updated = started_at`; an update must preserve the existing `created`
   and set `updated = started_at`. Write only final vault-relative knowledge
   paths below `candidate_vault` and use a non-empty subset of transaction
   Source IDs.
5. **Declare removals** with
   `obsidian-wiki transaction delete <id> <vault-relative-page.md>`. Unsupported
   non-page/control-file changes stop without a live-vault mutation.
6. **Validate and commit.** Run
   `obsidian-wiki transaction validate <id> --json --pretty`. Review every warning;
   warnings do not block commit. Fix every issue and rerun validation. Commit a
   passing report only with
   `obsidian-wiki transaction commit <id> --json --pretty`.
7. **Use status-aware recovery.** Follow only a trusted
   `recovery.preferred_action` or reported alternative whose prerequisites hold;
   verify the retained record with `obsidian-wiki transaction list --json`:
   `recommended_action` must agree and the command must be in `allowed_actions`.
   Fix/revalidate an active preflight failure or run
   `obsidian-wiki transaction abort <id> --json`. A `promoting` record permits
   only its reported `obsidian-wiki transaction restore <id> --json`. For a
   `failed` record, prefer the reported
   `obsidian-wiki transaction retry <id> --json`; use
   `obsidian-wiki transaction restore <id> --json` or
   `obsidian-wiki transaction discard <id> --json` only when allowed and its
   prerequisites hold. A configuration or begin failure with no trusted transaction ID,
   or an empty list, has no recovery action. Never replace a transaction while
   its outcome is ambiguous.
8. **Refresh local hot context only after commit succeeds or recovery is fully resolved.**
   Run `obsidian-wiki hot status --json`; when stale, run
   `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to write
   the semantic `hot.md` as the agent, then run
   `obsidian-wiki hot mark-current --json`.
9. Report sessions, snapshots, created/updated/removed pages, warnings,
   recovery, and hot-cache status.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Retain the
concrete vault, history, QMD CLI, and QMD collection paths in agent memory:
config resolution does not export these values into the parent shell. Write the prepared pages directly below `<resolved-vault-path>`,
using current ISO timestamps and preserving `created` on updates.

### Personal direct writes and Git safety

Apply any owner-required Personal Git snapshot against the concrete resolved
vault before direct writes. Then write or merge all prepared knowledge pages
at their final paths below `<resolved-vault-path>`; stop before tracking if a
write fails.

### Personal manifest v1 and cache

For each processed rollout, index, or history file, update
`<resolved-vault-path>/.manifest.json` as manifest v1 with `ingested_at`,
`size_bytes`, `modified_at`, `source_type` (`codex_rollout`, `codex_index`, or
`codex_history`), project, and page lists. Preserve canonical expanded absolute
Personal source keys and unrelated entries. Retain the project/session summary:

```json
{
  "project-name": {
    "source_path": "<resolved-codex-history-path>/sessions/...",
    "last_ingested": "TIMESTAMP",
    "sessions_ingested": 12,
    "sessions_total": 40,
    "index_updated_at": "TIMESTAMP"
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
- [TIMESTAMP] CODEX_HISTORY_INGEST sessions=N pages_updated=X pages_created=Y mode=append|full
```

Read `<resolved-vault-path>/hot.md`, creating the `wiki-ingest` template when
missing. Update **Recent Activity** with the conceptual Codex ingest, keep the
last three operations, and bump `updated`.

### Personal QMD refresh

If the concrete QMD collection is configured, refresh only after all Personal
writes. A failure does not roll back the vault.

```bash
<resolved-qmd-cli> update
<resolved-qmd-cli> embed
<resolved-qmd-cli> get "qmd://<resolved-qmd-wiki-collection>/<page>.md" -l 5
```

Use `embed` only when vectors are stale or missing and report refreshed,
skipped, unavailable, or failed status.

Do not fall through into Portable Repository completion. Report the Personal
page, manifest v1, cache, central-file, Personal Git snapshot, and QMD results,
then stop.

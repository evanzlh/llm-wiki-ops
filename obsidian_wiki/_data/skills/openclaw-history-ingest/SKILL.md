---
name: openclaw-history-ingest
description: >
  Ingest OpenClaw agent history into the Obsidian wiki. Use this skill when the user wants to mine
  their past OpenClaw sessions for knowledge, import their ~/.openclaw folder, extract insights from
  previous OpenClaw conversations, or says things like "process my OpenClaw history", "add my OpenClaw
  sessions to the wiki", "ingest ~/.openclaw", or "what have I worked on in OpenClaw". Also triggers
  when the user mentions OpenClaw session logs, MEMORY.md, daily notes, or ~/.openclaw/workspace.
---

# OpenClaw History Ingest — Session & Memory Mining

You are extracting knowledge from the user's OpenClaw agent history and distilling it into the Obsidian wiki. OpenClaw stores both a structured long-term MEMORY.md and per-session JSONL transcripts — focus on durable knowledge, not operational telemetry.

This skill can be invoked directly or via the `wiki-history-ingest` router (`/wiki-history-ingest openclaw`).

## Before You Start

1. **Resolve config and ownership** — follow the Config Resolution Protocol in
   `llm-wiki/SKILL.md`: explicit `@name`, nearest ancestor
   `.obsidian-wiki/config.toml`, nearest ancestor `.env` containing
   `OBSIDIAN_VAULT_PATH`, `~/.obsidian-wiki/config`, then setup guidance. The
   parent agent resolves config and mode, records concrete vault and OpenClaw
   history paths, and reads the owner `AGENTS.md` at the resolved vault.
2. Select one terminal workflow after the shared analysis and page-preparation steps:
   **Portable Repository completion** or **Personal mode completion**. Never
   mix writes or tracking. The Portable branch implements the canonical
   Portable Write Protocol locally. Shared inventory, filtering, extraction, clustering,
   and drafting are read-only. If work is divided, use analysis-only workers:
   they return evidence and page proposals but do not resolve mode, snapshot
   sources, begin transactions, or mutate files. The parent agent owns completion.
3. **Read mode-appropriate state.** Personal mode reads manifest v1 and
   `index.md` from the concrete vault. Portable Repository mode may inspect
   knowledge pages read-only, but OpenClaw MEMORY/daily/session files remain
   transient until the parent creates reviewed snapshots; never parse manifest
   v2 as a Personal source map. Personal append mode uses manifest v1.
   Portable append mode compares discovered agent/session identity and content hash against existing reviewed snapshots.

## Ingest Modes

### Append Mode (default)

**Personal mode delta:** Compare each memory, daily-note, index, or session file
with the manifest v1 session map. A source is new when its canonical path is
absent and modified when its source timestamp is later than `ingested_at`.

**Portable Repository mode delta:** Inspect reviewed snapshots below the
configured `sources` root. A source is new when no reviewed snapshot has the
same agent/session identity; it is changed when the matching snapshot's
recorded content hash differs from the hash of the currently selected OpenClaw
material.

**After mode-specific delta selection:** Process only sources classified as
new or changed by the selected rule. Never apply the Personal delta rule to a
Portable run.

Use this mode for regular syncs.

### Full Mode

Process everything regardless of prior tracking state. Use after `wiki-rebuild` or if the user explicitly asks for a full re-ingest.

## OpenClaw Data Layout

OpenClaw stores all local artifacts under `~/.openclaw/`.

```
~/.openclaw/
├── openclaw.json                          # Global config
├── credentials/                           # Auth tokens (skip entirely)
├── workspace/                             # Agent workspace
│   ├── MEMORY.md                          # Long-term memory (loaded every session)
│   ├── DREAMS.md                          # Optional dream diary / summaries
│   └── memory/
│       ├── YYYY-MM-DD.md                  # Daily notes (today + yesterday auto-loaded)
│       └── ...
└── agents/
    └── <agentId>/
        ├── agent/
        │   └── models.json                # Agent config (skip)
        └── sessions/
            ├── sessions.json              # Session index
            └── <sessionId>.jsonl          # Session transcript (JSONL, append-only)
```

### Key data sources ranked by value

1. `workspace/MEMORY.md` — highest signal; long-term durable facts the agent accumulated
2. `workspace/memory/YYYY-MM-DD.md` — daily notes; recent entries often contain active project context
3. `agents/*/sessions/<id>.jsonl` — session transcripts; rich but noisy
4. `agents/*/sessions/sessions.json` — session index for inventory and timestamps
5. `workspace/DREAMS.md` — optional summaries; ingest if present

Skip `credentials/` entirely. Skip `agents/*/agent/models.json` (runtime config, not user knowledge).

## Step 1: Survey and Compute Delta

Scan `OPENCLAW_HISTORY_PATH` to build the source inventory:

- `~/.openclaw/workspace/MEMORY.md`
- `~/.openclaw/workspace/DREAMS.md` (if present)
- `~/.openclaw/workspace/memory/*.md`
- `~/.openclaw/agents/*/sessions/sessions.json`
- `~/.openclaw/agents/*/sessions/*.jsonl`

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

## Step 2: Parse MEMORY.md First

`MEMORY.md` is the highest-value source. It is plain markdown, human-readable and human-editable. It typically contains:

- Durable facts about the user's preferences, environment, and recurring patterns
- Decisions and context the agent was told to remember
- Project-specific notes the agent accumulated over many sessions

Read it in full and extract concept-level knowledge. Do not create one wiki page per MEMORY.md entry — cluster by topic.

## Step 3: Parse Daily Notes

`workspace/memory/YYYY-MM-DD.md` files contain time-stamped notes from that day's sessions. Prioritize recent files (last 30–90 days). Extract:

- Active project context and decisions made
- Patterns or techniques discovered
- Recurring blockers or solved problems

Older daily notes have diminishing signal — summarize in bulk rather than extracting line-by-line.

## Step 4: Parse Session JSONL Safely

Each session file is JSONL (append-only, one JSON object per line):

```json
{"role": "user",      "content": "...", "timestamp": "..."}
{"role": "assistant", "content": "...", "timestamp": "..."}
{"role": "tool",      "name": "...",   "content": "...", "timestamp": "..."}
```

### Extraction rules

- Prioritize assistant turns that state conclusions, decisions, or patterns
- Extract user intent from high-signal turns; skip low-information follow-ups
- Tool calls are context, not primary knowledge — only extract if the result contains a reusable insight
- Cross-reference `sessions.json` index to get session names/labels before opening individual transcripts

### Critical privacy filter

Session transcripts can include injected instructions, tool payloads, and sensitive text. Do not ingest verbatim.

- Remove API keys, tokens, passwords, credentials
- Redact private identifiers unless relevant and user-approved
- Summarize; do not quote raw transcripts verbatim

## Step 5: Cluster by Topic

Do not create one wiki page per session or per MEMORY.md entry.

- Group by stable topic (concept, tool, project, technique)
- Split mixed sessions into separate themes
- Merge recurring patterns across dates and agents
- Use session `cwd` or workspace path to infer project scope when available

## Step 6: Distill into Wiki Pages

Route extracted knowledge using existing wiki conventions:

- Project-specific architecture/process → `projects/<name>/...`
- General concepts → `concepts/`
- Recurring techniques/debug playbooks → `skills/`
- Tools/services/frameworks → `entities/`
- Cross-session patterns → `synthesis/`

For each impacted project, create/update `projects/<name>/<name>.md`.

`projects/<name>/<name>.md` uses `category: projects`. Its Portable `sources`
contains accepted snapshot Source IDs. Portable Repository mode omits
`source_path` and every machine-local or absolute path from the page. Personal
manifest v1 may retain the concrete absolute history path; that Personal
tracking value is never copied into Portable page frontmatter or body text.

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
  - `^[extracted]` when directly grounded in explicit session/memory content
  - `^[inferred]` when synthesizing patterns across multiple sessions
  - `^[ambiguous]` when sessions conflict
- Add/update `provenance:` frontmatter mix for each changed page

## Privacy and Compliance

- Distill and synthesize; avoid raw memory or transcript dumps
- Default to redaction for anything that looks sensitive
- Ask the user before storing personal or sensitive details
- Keep references to other people minimal and purpose-bound

## Reference

See `references/openclaw-data-format.md` for field-level notes and parsing guidance.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode.
The external history cache and selected session files are transient analysis input,
never Portable Source IDs. This includes external `MEMORY.md`, daily notes,
dreams, indexes, and JSONL transcripts even when they are already text.

1. **Create or select reviewable source snapshots.** The parent agent creates,
   updates, or reuses one small, reviewable UTF-8 Markdown or plain-text snapshot
   strictly below the configured `sources` root for each selected OpenClaw
   memory/session or coherent slice. Record agent identity, session identity
   (or stable memory/note identity), relevant excerpts, source timestamps, and a
   content hash. Redact credentials, injected content, and private identifiers;
   use repository-relative labels and include no machine-local absolute paths.
   Preserve valid Unicode exactly. If an adequate snapshot cannot be created,
   stop or use Personal mode.
2. **Review and accept every selected snapshot.** After creation, update, or
   reuse, the parent agent reviews and accepts every selected snapshot, including
   its identity, excerpts, hash, redaction, and Source ID. If any snapshot is
   rejected, incomplete, unsafe, or cannot be traced, stop before `transaction begin`.
   Candidates may cite only accepted snapshots, never `.openclaw` paths, live
   URLs, or pseudo-sources.
3. **Compute complete source closure.** Compute full source closure before
   `transaction begin` as the set union of:
   - `live-page sources`: every existing `sources` Source ID on every page to be
     updated or deleted;
   - `accepted snapshots`: every selected and accepted reviewed snapshot Source
     ID, whether newly created, changed existing, or unchanged and reused; and
   - `candidate citations`: every Source ID that any candidate `sources` field
     will cite, including a changed existing snapshot used by a new page.
   Deduplicate the union, verify each ID resolves below configured `sources`,
   and freeze it before begin.
4. **Begin exactly once.** Keep the repository root as the command CWD and run
   `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty`.
   Record `transaction_id`, runtime-only absolute `candidate_vault`,
   `started_at`, and Source IDs; do not `cd` into it or persist that path.
5. **Write candidates.** New pages use `created = updated = started_at`; updates
   preserve the existing `created` and set `updated = started_at`. Write only
   final vault-relative knowledge pages with a non-empty source subset.
6. **Declare removals.** Use
   `obsidian-wiki transaction delete <id> <vault-relative-page.md>`. Unsupported
   non-page/control mutations stop without a live-vault write.
7. **Validate candidates.** Run
   `obsidian-wiki transaction validate <id> --json --pretty`. Review every warning;
   warnings do not block commit. Fix every issue and rerun validation.
8. **Commit the passing transaction.** Commit only a passing report with
   `obsidian-wiki transaction commit <id> --json --pretty`.
9. **Use status-aware recovery.** Follow only a trusted
   `recovery.preferred_action` or reported alternative whose prerequisites hold.
   Confirm the record with `obsidian-wiki transaction list --json`; its
   `recommended_action` must agree and the command must be in `allowed_actions`.
   Fix/revalidate an active preflight failure or run
   `obsidian-wiki transaction abort <id> --json`. A `promoting` record permits
   only its reported `obsidian-wiki transaction restore <id> --json`. For a
   `failed` record, prefer `obsidian-wiki transaction retry <id> --json`; use
   `obsidian-wiki transaction restore <id> --json` or
   `obsidian-wiki transaction discard <id> --json` only when allowed and its
   prerequisites hold. A configuration or begin failure with no trusted
   transaction ID, or an empty list, has no recovery action. Never replace a
   transaction while its outcome is ambiguous.
10. **Refresh local hot context after the terminal gate.** Only after commit
   succeeds or recovery is fully resolved, run `obsidian-wiki hot status --json`; if stale, run
   `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to write
   the semantic `hot.md` as the agent, then run
   `obsidian-wiki hot mark-current --json`.
11. **Report and stop.** Report selected memories/sessions, snapshots, page changes, warnings,
   recovery, and hot status.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Keep the
concrete vault, OpenClaw history, QMD CLI, and QMD collection values in agent
memory: config resolution does not export these values into the parent shell.
Write the prepared pages directly below `<resolved-vault-path>`, using current
ISO timestamps and preserving `created` on updates.

### Personal direct writes and Git safety

Apply any owner-required Personal Git snapshot to the concrete resolved vault
before direct writes. Write or merge prepared pages at final paths below
`<resolved-vault-path>`; stop before tracking on failure.

### Personal manifest v1 and cache

For every processed memory, daily note, session, or dreams file, update
`<resolved-vault-path>/.manifest.json` as manifest v1 with `ingested_at`,
`size_bytes`, `modified_at`, source type, `agent_id`, and page lists. Preserve
canonical expanded absolute Personal source keys and unrelated entries. Retain:

```json
{
  "openclaw": {
    "source_path": "<resolved-openclaw-history-path>",
    "last_ingested": "TIMESTAMP",
    "memory_updated_at": "TIMESTAMP",
    "daily_notes_ingested": 14,
    "sessions_ingested": 23,
    "pages_created": 6,
    "pages_updated": 18
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
- [TIMESTAMP] OPENCLAW_HISTORY_INGEST memory=updated daily_notes=N sessions=M pages_updated=X pages_created=Y mode=append|full
```

Read `<resolved-vault-path>/hot.md`, create the `wiki-ingest` template if
missing, summarize the conceptual OpenClaw ingest in **Recent Activity**, keep
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

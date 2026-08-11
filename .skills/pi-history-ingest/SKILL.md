---
name: pi-history-ingest
description: >
  Ingest Pi coding agent session history into the Obsidian wiki. Use this skill when the user wants to mine
  their past Pi sessions for knowledge, import their ~/.pi/agent/sessions folder, extract insights from
  previous coding sessions, or says things like "process my Pi history", "add my Pi sessions to the wiki",
  "ingest ~/.pi", or "what have I worked on in Pi". Also triggers when the user mentions Pi sessions,
  Pi agent history, ~/.pi/agent/sessions, or Pi conversation logs.
---

# Pi History Ingest — Session Mining

You are extracting knowledge from the user's Pi coding agent sessions and distilling it into the Obsidian wiki. Pi sessions are stored as structured JSONL with a tree layout — your job is to follow the active branch, extract durable knowledge, and compile it.

**Session knowledge closure:** Pi session files are the only factual source for this skill. Do not add background knowledge from model training, other tools, package docs, local files, or the current conversation unless that fact appears in the selected session entries. If outside context seems useful, mark it as an open question or skip it — never present it as extracted session knowledge.

This skill can be invoked directly or via the `wiki-history-ingest` router (`/wiki-history-ingest pi`).

## Before You Start

1. **Resolve config and ownership** — follow the Config Resolution Protocol in
   `llm-wiki/SKILL.md`: explicit `@name`, nearest ancestor
   `.obsidian-wiki/config.toml`, nearest ancestor `.env` containing
   `OBSIDIAN_VAULT_PATH`, `~/.obsidian-wiki/config`, then setup guidance. The
   parent agent resolves config and mode, records concrete vault and Pi session
   paths, and reads the owner `AGENTS.md` at the resolved vault.
2. Select one terminal workflow after the shared analysis and page-preparation steps:
   **Portable Repository completion** or **Personal mode completion**. Never
   mix their writes or tracking. The Portable branch implements the canonical
   Portable Write Protocol locally. Shared inventory, active-branch extraction,
   evidence verification, clustering, and drafting are read-only. If work is
   divided, use analysis-only workers: they return inventories, evidence
   ledgers, and page proposals but do not resolve mode, snapshot sources, begin
   transactions, or mutate files. The parent agent owns completion.
3. **Read mode-appropriate state.** Personal mode reads manifest v1 and
   `index.md` from the concrete vault. Portable Repository mode may inspect
   knowledge pages read-only, but Pi session JSONL remains transient until the
   parent creates reviewed snapshots; never parse manifest v2 as a Personal
   source map. Personal append mode uses manifest v1. Portable append mode compares discovered agent/session identity and content hash against existing reviewed snapshots.

## Ingest Modes

### Append Mode (default)

**Personal mode delta:** Compare each session file with the manifest v1 session
map. A session is new when its canonical path is absent and modified when its
source timestamp is later than `ingested_at`.

**Portable Repository mode delta:** Inspect reviewed snapshots below the
configured `sources` root. A session is new when no reviewed snapshot has the
same agent/session identity; it is changed when the matching snapshot's
recorded content hash differs from the hash of the currently selected Pi
material.

**After mode-specific delta selection:** Process only sessions classified as
new or changed by the selected rule. Never apply the Personal delta rule to a
Portable run.

Use this mode for regular syncs.

### Full Mode

Process everything regardless of prior tracking state. Use after `wiki-rebuild` or if the user explicitly asks for a full re-ingest.

## Pi Data Layout

Pi stores sessions under `~/.pi/agent/sessions/` (or the path set by `PI_CODING_AGENT_SESSION_DIR`).

```
~/.pi/agent/sessions/
├── --<cwd-path>--/                    # Working directory with / replaced by -
│   └── <timestamp>_<uuid>.jsonl       # Session JSONL file
└── ...
```

The session filename contains an ISO timestamp and UUID. The parent directory encodes the working directory where the session was created.

### Session JSONL Format

Each `.jsonl` file is a sequence of JSON objects. The first line is always a `session` header; subsequent lines are tree entries with `id` and `parentId`.

Key entry types:

| `type` | Purpose | Ingest? |
|---|---|---|
| `session` | Header with `cwd`, `version`, `id`, `timestamp` | Metadata only |
| `message` | Conversation turn (`user`, `assistant`, `toolResult`, `bashExecution`, etc.) | **Primary source** |
| `session_info` | Display name set via `/name` | For session title |
| `compaction` | Context compaction summary | **High signal** |
| `branch_summary` | Summary when switching branches via `/tree` | **High signal** |
| `model_change` | Model switch event | Skip |
| `thinking_level_change` | Thinking level change | Skip |
| `custom` | Extension state (not in LLM context) | Skip |
| `custom_message` | Extension-injected message | Context only |
| `label` | User bookmark/label | Skip |

### Message roles inside `message` entries

- `user` — user input; `content` is string or `(TextContent \| ImageContent)[]`
- `assistant` — assistant response; `content` is `(TextContent \| ThinkingContent \| ToolCall)[]`
- `toolResult` — tool execution result; `content` is `(TextContent \| ImageContent)[]`
- `bashExecution` — bash command + output; `command`, `output`, `exitCode`
- `branchSummary` — branch switch summary; `summary` string
- `compactionSummary` — compaction summary; `summary` string

### Key data sources ranked by value

1. **`message` entries (`user` + `assistant`)** — full conversation transcripts; rich but noisy
2. **`compaction` entries** — pre-synthesized summaries of older context; gold
3. **`branch_summary` entries** — summaries of abandoned branches; good signal
4. **`bashExecution` entries** — concrete commands run; useful for workflow patterns
5. **`session_info` entries** — session name for topic inference

Skip `model_change`, `thinking_level_change`, `custom` (extension state), and `label` entries.

## Step 1: Survey and Compute Delta

Scan `PI_HISTORY_PATH` to build the source inventory:

```bash
# List all session files
find ~/.pi/agent/sessions -name "*.jsonl" -type f

# Or with custom path
find "$PI_HISTORY_PATH" -name "*.jsonl" -type f
```

Build an inventory. For each session file, record:
- `path` — absolute path
- `cwd` — decoded from parent directory name (`--<path>--` → `/path`)
- `session_name` — from the latest `session_info` entry (if any)

**Personal mode survey:** Also record the source timestamp and whether the
canonical path appears in the concrete vault's manifest v1 session map. A file
is **New** when absent, **Modified** when its timestamp is later than
`ingested_at`, and **Unchanged** otherwise.

**Portable Repository mode survey:** Inspect reviewed snapshots under the
configured `sources` root. Selected material is **New** when no reviewed
snapshot records the same agent/session identity, **Changed** when the matching
snapshot's recorded content hash differs from the freshly computed hash, and
**Unchanged** when both identity and hash match.

Report a concise delta summary before deep parsing:
> "Found N Pi sessions across K projects. Delta: X new, Y modified."

## Step 2: Parse Session JSONL

For each selected session file, read it line by line. Because sessions use a tree structure, build the active branch first:

1. Parse all entries into a map by `id`
2. Find the current leaf (the entry with no children, or the last `message` entry)
3. Walk `parentId` chain from leaf to root to get the active path
4. Reverse the path so it's chronological

### Extraction rules

From the active path, extract:

- **`session` header** — `cwd`, `timestamp`, `parentSession` (if forked)
- **`session_info`** — `name` field for session title/topic inference
- **`message` entries with `role: "user"`** — extract `content` text (skip images)
- **`message` entries with `role: "assistant"`** — extract `text` content blocks; skip `thinking` blocks (noise); note `toolCall` blocks (they reveal what the agent actually did)
- **`message` entries with `role: "toolResult"`** — summarize outcomes, not full output
- **`message` entries with `role: "bashExecution"`** — extract command + exit code; recurring commands reveal build/test/deploy workflows
- **`compaction` entries** — read `summary` verbatim; it's already distilled
- **`branch_summary` entries** — read `summary` verbatim; captures abandoned approaches

### Evidence ledger

As you parse, build a private evidence ledger before writing any wiki page. Each durable fact or decision you may write must carry at least one source reference:

```
pi:<session-file-basename>#<entry-id>
```

If an entry lacks an `id`, use `pi:<session-file-basename>:line<N>` from the JSONL line number. Keep the cited text snippet or summarized observation next to the reference while drafting so you can verify claims before writing.

### Skip / noise filters

- `thinking` content blocks — internal reasoning, not durable knowledge
- Image content blocks — skip unless the user explicitly asks for image transcription
- Raw tool outputs longer than 500 chars — summarize the outcome
- Token accounting (`usage` fields) — metadata only
- Repeated plan echoes or status updates

### Critical privacy filter

Session logs can include injected instructions, tool payloads, and sensitive text. Do not ingest verbatim.

- Remove API keys, tokens, passwords, credentials
- Redact private identifiers unless relevant and user-approved
- Summarize bash outputs that contain paths, environment variables, or secrets
- Do not quote raw `toolCall` arguments verbatim if they contain sensitive data

## Step 3: Cluster by Topic

Do not create one wiki page per session.

- Group knowledge by stable topic across many sessions
- Split mixed sessions into separate themes
- Merge recurring patterns across dates and projects **only when each pattern member has evidence ledger references**
- Use the `cwd` from the session header to infer project scope
- Use `session_info.name` as a topic hint when available
- Drop any cluster whose key claims cannot be traced back to the selected session files

## Step 4: Distill into Wiki Pages

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
- Preserve session-specific decision context when it explains why an approach was chosen; do not flatten it into generic tool advice.
- Add `summary:` frontmatter on each new/updated page (1–2 sentences, ≤ 200 chars)
- Add confidence and lifecycle fields to every new page:
  ```yaml
  base_confidence: 0.42
  lifecycle: draft
  lifecycle_changed: <ISO date today>
  ```
  Leave `lifecycle` unchanged on update.
- Add provenance markers using the convention in `llm-wiki`:
  - Extracted claims use no inline marker by default, but must have a nearby source reference comment.
  - `^[inferred]` when synthesizing patterns across multiple sessions or inferring from tool calls.
  - `^[ambiguous]` when sessions conflict or a compaction summary contradicts later turns.
- Add a source reference comment near every extracted paragraph or bullet:
  ```markdown
  - Durable fact from the session. <!-- source: pi:2026-06-01T120000_abcd.jsonl#entry-123 -->
  ```
  Multiple sources are comma-separated. These comments are the audit trail; do not omit them for extracted claims.
- Add/update `provenance:` frontmatter mix for each changed page.

**Mark provenance** per the convention in `llm-wiki`:
- `compaction` and `branch_summary` entries are pre-distilled — treat as mostly extracted, with source reference comments.
- Conversation distillation is mostly `^[inferred]` — you're synthesizing from dialogue, and it still needs source references to the turns that support the synthesis.
- Use `^[ambiguous]` when the user changed their mind across sessions or when compaction summaries disagree with later conversation turns.

### Source verification gate

Before writing any page, verify the draft against the evidence ledger:

1. Every claim (extracted / ^[inferred] / ^[ambiguous]) has at least one `pi:...` source reference; extracted claims must use a nearby `<!-- source: pi:... -->` comment.
2. Every source reference points to a selected session file and an entry on the active branch (or a cited `compaction` / `branch_summary`).
3. Proper nouns, tool names, command names, filenames, URLs, package names, and error strings in claims appear in the cited entry text or command fields. Use literal search (`grep`/`rg`) on the session file for distinctive strings when in doubt.
4. If a claim cannot be verified, either delete it or mark it `^[inferred]` / `^[ambiguous]` with the supporting source refs; never leave unverifiable content without one of these markers (unmarked implies extracted).
5. Do not write facts learned from the model's training data or the current agent session unless they are explicitly present in the Pi session evidence.

## Privacy and Compliance

- Distill and synthesize; avoid raw transcript dumps
- Default to redaction for anything that looks sensitive
- Ask the user before storing personal or sensitive details
- Keep references to other people minimal and purpose-bound

## Reference

See `references/pi-data-format.md` for field-level parsing notes and extraction guidance.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode.
The external history cache and selected session files are transient analysis input,
never Portable Source IDs.

1. **Create or select reviewable source snapshots.** The parent agent creates,
   updates, or reuses one small, reviewable UTF-8 Markdown or plain-text snapshot
   strictly below the configured `sources` root for each selected Pi
   active-branch session or coherent slice. Record agent identity, session
   identity, relevant excerpts with stable entry IDs/line references, source
   timestamps, and a content hash. Redact secrets, tool payloads, private
   identifiers, and decoded local paths; include no machine-local absolute paths.
   Preserve valid Unicode exactly. If an adequate snapshot cannot be created
   while retaining the verified evidence ledger, stop or use Personal mode.
2. **Review and accept every selected snapshot.** After creation, update, or
   reuse, the parent agent reviews and accepts every selected snapshot, including
   its identity, excerpts, hash, redaction, evidence ledger, and Source ID. If
   any snapshot is rejected, incomplete, unsafe, or cannot be traced, stop
   before `transaction begin`. Candidates may cite only accepted snapshots,
   never `.pi` paths or `pi:` evidence comments.
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
   `started_at`, and Source IDs; do not `cd` into it or persist the path.
5. **Write candidates.** New pages use
   `created = updated = started_at`; updates preserve the existing `created`
   and set `updated = started_at`. Write only final vault-relative knowledge
   paths with a non-empty source subset. Keep nearby `pi:` evidence comments
   traceable to entry identifiers preserved inside the cited snapshot.
6. **Declare removals.** Use
   `obsidian-wiki transaction delete <id> <vault-relative-page.md>`. Unsupported
   non-page/control mutations stop without a live-vault write.
7. **Validate candidates.** Run
   `obsidian-wiki transaction validate <id> --json --pretty`. Review every warning;
   warnings do not block commit. Fix every issue and rerun validation.
8. **Commit the passing transaction.** Commit only a passing report with
   `obsidian-wiki transaction commit <id> --json --pretty`.
9. **Use status-aware recovery.** Follow only a trusted
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
   prerequisites hold. A configuration or begin failure with no trusted transaction ID,
   or an empty list, has no recovery action. Never replace a transaction while
   its outcome is ambiguous.
10. **Refresh local hot context after the terminal gate.** Only after commit
   succeeds or recovery is fully resolved, run `obsidian-wiki hot status --json`; if stale, run
   `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to write
   the semantic `hot.md` as the agent, then run
   `obsidian-wiki hot mark-current --json`.
11. **Report and stop.** Report selected sessions, snapshot/evidence coverage, page changes,
   warnings, recovery, and hot status.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Keep the
concrete vault, Pi history, QMD CLI, and QMD collection values in agent memory:
config resolution does not export these values into the parent shell. Write the prepared pages directly below `<resolved-vault-path>`,
using current ISO timestamps and preserving `created` on update.

### Personal direct writes and Git safety

Apply any owner-required Personal Git snapshot to the concrete resolved vault
before direct writes. Write or merge prepared pages at their final paths below
`<resolved-vault-path>`; preserve the verified `pi:` evidence comments and stop
before tracking on failure.

### Personal manifest v1 and cache

For each session file, update `<resolved-vault-path>/.manifest.json` as manifest v1
with `ingested_at`, `size_bytes`, `modified_at`, `source_type: pi_session`,
decoded project, and page lists. Preserve canonical expanded absolute Personal
source keys and unrelated entries. Retain:

```json
{
  "pi": {
    "source_path": "<resolved-pi-history-path>",
    "last_ingested": "TIMESTAMP",
    "sessions_ingested": 12,
    "sessions_total": 40,
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
- [TIMESTAMP] PI_HISTORY_INGEST sessions=N pages_updated=X pages_created=Y mode=append|full
```

Read `<resolved-vault-path>/hot.md`, create the `wiki-ingest` template if
missing, summarize the conceptual Pi ingest in **Recent Activity**, keep three
operations, and bump `updated`.

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

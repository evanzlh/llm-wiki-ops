---
name: wiki-agent
description: >
  Query-driven targeted ingest from a specific AI agent's raw history. Use this skill when the user
  invokes /wiki-claude, /wiki-codex, /wiki-hermes, /wiki-openclaw, /wiki-copilot, /wiki-pi — with or without a
  search topic. Different from wiki-history-ingest (which bulk-ingests everything new): this skill finds
  sessions about a SPECIFIC TOPIC in a specific agent's history and ingests just those, then returns a
  synthesized answer immediately usable in the current session. Primary use case: you're working in
  agent A and want to pull in how you solved X in agent B's history. Cross-referencing, not archiving.
  Also trigger on: "what did I work on in codex about X", "search my claude sessions for Y",
  "pull in hermes knowledge about Z", "find that conversation where I did X in codex".
---

# Wiki Agent — Targeted Cross-Agent History Search + Ingest

You are doing a **query-driven targeted ingest** from one specific AI agent's raw conversation history. The user is typically working in a *different* agent right now and wants to pull in context from another agent's past sessions.

This is not bulk ingest. You find sessions about a specific topic, extract the relevant blobs, distill them into the wiki, and return a synthesized answer the user can act on immediately.

## Command Routing

Parse the invocation to determine the target agent and optional query:

| Command | Target | Example |
|---|---|---|
| `/wiki-claude [query]` | Claude Code history | `/wiki-claude "how did I set up auth middleware"` |
| `/wiki-codex [query]` | Codex CLI history | `/wiki-codex "rust ownership patterns"` |
| `/wiki-hermes [query]` | Hermes agent history | `/wiki-hermes "memory architecture"` |
| `/wiki-openclaw [query]` | OpenClaw history | `/wiki-openclaw "project planning approach"` |
| `/wiki-copilot [query]` | Copilot chat history | `/wiki-copilot "test strategy for API routes"` |
| `/wiki-pi [query]` | Pi agent history | `/wiki-pi "how did I refactor the auth module"` |

If no query is given, default to **recent sessions mode**: ingest the last 5 unprocessed sessions from that agent and return a summary of what was found. This is equivalent to a focused `wiki-history-ingest` for that agent only.

## Before You Start

1. **Resolve config and ownership** — follow the Config Resolution Protocol in
   `llm-wiki/SKILL.md`: explicit `@name`, nearest ancestor
   `.obsidian-wiki/config.toml`, nearest ancestor `.env` containing
   `OBSIDIAN_VAULT_PATH`, `~/.obsidian-wiki/config`, then setup guidance. The
   parent agent resolves config and mode, records the concrete vault and target
   agent history paths, and reads the owner `AGENTS.md` at the resolved vault.
2. Select one terminal workflow after the shared analysis and page-preparation steps:
   **Portable Repository completion** or **Personal mode completion**. Never
   mix their writes or tracking. The Portable branch implements the canonical
   Portable Write Protocol locally. Session inventory, scoring, targeted
   extraction, clustering, page preparation, and answer drafting are read-only.
   If work is divided, use analysis-only workers: they return ranked sessions,
   evidence, page proposals, and answer notes but do not resolve mode, create
   source snapshots, begin transactions, or mutate files. The parent agent owns completion.
3. **Read mode-appropriate state.** Personal mode reads manifest v1,
   `index.md`, and optional `hot.md` from the concrete resolved vault. Portable
   Repository mode may inspect existing knowledge pages and fresh local hot
   inputs read-only, but selected history files remain transient until the
   parent creates reviewed source snapshots; never parse manifest v2 as a
   Personal session map. Personal append mode uses manifest v1. Portable append mode compares discovered agent/session identity and content hash against existing reviewed snapshots.

---

## Step 1: Locate the Agent's History Root

| Agent | Default path | Config override |
|---|---|---|
| `claude` | `~/.claude` + `~/Library/Application Support/Claude/local-agent-mode-sessions/` | `CLAUDE_HISTORY_PATH` in `.env` |
| `codex` | `~/.codex` | `CODEX_HISTORY_PATH` in `.env` |
| `hermes` | `~/.hermes` | `HERMES_HOME` in env or `.env` |
| `openclaw` | `~/.openclaw` | `OPENCLAW_HOME` in `.env` |
| `copilot` | `~/.copilot` | `COPILOT_HISTORY_PATH` in `.env` |
| `pi` | `~/.pi/agent/sessions` | `PI_HISTORY_PATH` in `.env` |

If the history root doesn't exist, stop and tell the user: "No `<agent>` history found at `<path>`. Have you run `<agent>` on this machine? You can set a custom path with `<CONFIG_VAR>` in `.env`."

---

## Step 2: Build Session Inventory

Use the **cheapest index source** for each agent — don't open session files until you know which ones are relevant.

### Claude
```
Primary index:   ~/.claude/projects/  (directories = projects, files = sessions)
Session files:   ~/.claude/projects/*/*.jsonl
Desktop index:   find ~/Library/Application Support/Claude/local-agent-mode-sessions -name "local_*.json"
Signal fields:   sessionId, cwd, startedAt, title (in local_*.json)
```
Build a list of sessions: `{path, project_dir, modified_at, already_ingested}`.

### Codex
```
Primary index:   ~/.codex/session_index.jsonl
Session files:   ~/.codex/sessions/**/rollout-*.jsonl
Signal fields:   thread_id, name/title, updated_at (in session_index.jsonl)
```
Read `session_index.jsonl` as the inventory. Each line: `{thread_id, name, updated_at}`. Map thread IDs to rollout files by matching directory names.

### Hermes
```
Primary index:   ~/.hermes/memories/*.md  (fast to scan)
Session files:   ~/.hermes/sessions/**/*.jsonl
Signal fields:   file names, memory titles, first 3 lines of each memory
```
Scan memory filenames first (they're often titled by topic). Fall back to session listing.

### OpenClaw
```
Primary index:   ~/.openclaw/workspace/memory/MEMORY.md  (structured long-term memory)
Daily notes:     ~/.openclaw/workspace/memory/YYYY-MM-DD.md
Session index:   ~/.openclaw/agents/*/sessions/sessions.json
Session files:   ~/.openclaw/agents/*/sessions/*.jsonl
```
Read `MEMORY.md` sections first — it's the pre-compiled summary of everything. Daily notes give recency signal.

### Copilot
```
Primary index:   session filenames / directory listing
Session files:   varies by client (VS Code: ~/.copilot/sessions/*.jsonl or similar)
Signal fields:   session timestamps, file names
```

### Pi
```
Primary index:   ~/.pi/agent/sessions/--<cwd>--/ directories
Session files:   ~/.pi/agent/sessions/--<cwd>--/<timestamp>_<uuid>.jsonl
Signal fields:   cwd (decoded from dir name), session_info.name, timestamp in filename
```
Scan session directories first. Decode `--<cwd>--` to get the working directory. Read the first line (session header) and any `session_info` entries for the session name. No separate index file — the filesystem is the index.

---

## Step 3: Score Sessions Against the Query

If a query was given, score each session in the inventory without opening full session files:

1. **Name/title match** — does the session name or thread title contain the query terms? Score: +3
2. **CWD/project match** — does the working directory suggest the right project? Score: +2
3. **Recency** — apply exponential time decay with a 90-day half-life, as a multiplier on the match score rather than a bonus added to it:

   ```
   base  = name_match(3) + cwd_match(2)
   score = base * (0.35 + 0.65 * 0.5 ** (age_days / 90))
   ```

   The 0.35 floor is deliberate: an old session that matches the query exactly must still outrank a recent one that barely matches, or the skill can never answer "how did I first solve this?". This is the same decay `session-brain` uses, so the two skills rank consistently.
4. **Already ingested** — if this session was previously ingested and the wiki page already covers the query (check `hot.md` + `index.md`), flag as "covered" but still show in results

Select the **top 3–5 sessions** by score. If no query was given, select the 5 most recent unprocessed sessions.

---

## Step 4: Extract the Relevant Blob

Open each selected session file and extract only the content relevant to the query. **Do not read the full session if it's large — use targeted extraction.**

### Per-Agent Extraction Strategy

**Claude** (JSONL conversation):
- Each line: `{role, content, timestamp, ...}`
- Search with: `rg -i "<query terms>" <session.jsonl>` to find the relevant lines
- Extract: the surrounding conversation window (10 lines before + 20 lines after each hit)
- Special signal: tool calls (Read/Write/Bash/Edit) reveal what was actually done — extract these even without keyword matches if they're in the relevant window

**Codex** (rollout JSONL):
- Each line: `{type: "session_meta|turn_context|event_msg|response_item", ...}`
- Filter to `type: "event_msg"` (user turns) and `type: "response_item"` (model output)
- Search with: `rg -i "<query terms>" <rollout.jsonl>`
- Extract: matching turns + their parent context (the `turn_context` preceding the match)
- Skip: `session_meta` events (operational metadata, not knowledge)

**Hermes** (memory files + session JSONL):
- For memory files: read the full file (they're short — typically <500 words each)
- For session JSONL: `rg -i "<query terms>"` + surrounding window
- Memory files with title matches → read fully; others → grep only

**OpenClaw** (MEMORY.md + daily notes + session JSONL):
- `MEMORY.md`: grep for section headers containing query terms → extract that section
- Daily notes: grep most recent 30 days for query terms → extract matching paragraphs
- Session JSONL: same grep-window approach as Claude
- Prefer MEMORY.md/daily notes over session JSONL (they're pre-synthesized)

**Copilot** (session JSONL):
- Same grep-window approach as Claude
- Look for checkpoint files if available (pre-summarized)

**Pi** (structured JSONL with tree layout):
- Each line is a tree entry: `{type, id, parentId, timestamp, message?, ...}`
- Build the active branch: map entries by `id`, find leaf (last entry with no children), walk `parentId` to root
- Search with: `rg -i "<query terms>" <session.jsonl>` to find matching entries
- Extract: the matching entries + their ancestors on the active branch (follow parent chain)
- Special signal: `toolCall` blocks inside assistant messages reveal what was actually done — extract these even without keyword matches if they're in the relevant window
- Prefer `compaction` and `branch_summary` entries when available — they're pre-synthesized summaries
- Skip `thinking` content blocks (noise) and `model_change` / `thinking_level_change` entries

---

## Step 5: Distill Blobs into Wiki Pages

For each extracted blob, determine where it belongs in the wiki:

1. **Check if a wiki page already covers this** — grep `index.md` and page frontmatter for the topic. If yes, update the existing page rather than creating a new one.
2. **Determine category** using standard rules (from `llm-wiki/SKILL.md`):
   - Technique / how-to: `skills/<slug>.md` → `category: skills`
   - Abstract concept / pattern: `concepts/<slug>.md` → `category: concepts`
   - Tool / library / person: `entities/<slug>.md` → `category: entities`
   - Cross-cutting insight: `synthesis/<slug>.md` → `category: synthesis`
   The candidate path and `category` must use one matching pair. Never place a
   pipe-separated list or an unresolved category choice in frontmatter. The
   validator checks this semantic path/category match.
3. **Prepare the page** with required frontmatter. Source identity is selected
   by the terminal mode: Portable mode uses only accepted repository snapshot
   Source IDs; Personal mode retains agent-prefixed session sources so
   `memory-bridge` can find them. All other schema and trust fields apply in
   both modes.

### Valid Portable concept candidate example

This is one concrete, parseable candidate for
`concepts/auth-middleware.md`. For another semantic type, replace both path
and category with one pair above. Replace the example Source ID only with an
accepted snapshot Source ID from the selected transaction.

```markdown
---
title: Authentication Middleware Decisions
category: concepts
tags: [authentication, middleware]
sources:
  - sources/history/claude/session-7f3a/slice-auth-middleware.md
summary: The durable authentication middleware decisions recovered from one reviewed Claude session slice.
provenance:
  extracted: 0.70
  inferred: 0.25
  ambiguous: 0.05
base_confidence: 0.42
lifecycle: draft
lifecycle_changed: 2026-08-11
tier: supporting
created: 2026-08-11T12:00:00+00:00
updated: 2026-08-11T12:00:00+00:00
---

# Authentication Middleware Decisions

The application authenticates requests before route-specific authorization.
```

In Personal mode only, replace the repository snapshot item under `sources`
with the canonical `<agent>://<path/to/session>` source. Never use that
pseudo-source in Portable mode.
4. **Add cross-links** to related wiki pages found in `index.md`.

Distillation rules (same as all ingest skills):
- Extract durable knowledge, not operational telemetry
- One wiki page per concept, not one per session
- Merge into existing pages rather than duplicating
- Keep the signal: decisions made, patterns discovered, techniques that worked, bugs explained

---

## Step 6: Prepare the Synthesized Answer

Draft this answer from the selected evidence and existing wiki content, but do
not return it until the selected completion branch has finished successfully:

```
## From <agent> history: "<query>"

**Found in:** <N> sessions (<session names/titles>)

**Key insights:**
<Synthesized answer — 3–5 bullet points of the most useful knowledge>

**Wiki pages updated/created:**
- [[page-name]] — <what was added>
- [[page-name]] — <what was added>

**Sessions ingested:**
| Session | Date | Relevance |
|---------|------|-----------|
| <name>  | <date> | <one-line why it was selected> |

**Gaps:** <What the sessions didn't cover that might be relevant>
```

If a query was given but no relevant sessions were found, say so explicitly: "No sessions about '<query>' found in `<agent>` history. The most recent sessions covered: <list topics from last 3 sessions>."

---

## Cross-Agent Use Patterns

These are the primary use cases this skill is designed for:

**"I'm on Codex. What did I figure out about X in Claude?"**
→ `/wiki-claude "X"` — finds Claude sessions about X, ingests them, returns the answer

**"I solved a bug in Hermes last week. I need that context now in Claude Code."**
→ `/wiki-hermes "bug description"` — surfaces and ingests the Hermes session

**"What are all the approaches I've tried for X across all my tools?"**
→ Run `/wiki-claude "X"`, `/wiki-codex "X"`, `/wiki-hermes "X"` in sequence — each ingests its slice, the wiki accumulates the cross-agent picture, then `/memory-bridge diff` shows what each tool uniquely contributed

**No query — just "catch me up on recent Codex work"**
→ `/wiki-codex` — ingests last 5 Codex sessions and returns a summary

**"I'm on Claude Code. What did I figure out about X in Pi?"**
→ `/wiki-pi "X"` — finds Pi sessions about X, ingests them, returns the answer

**No query — just "catch me up on recent Pi work"**
→ `/wiki-pi` — ingests last 5 Pi sessions and returns a summary

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode.
The external history cache and selected session files are transient analysis input,
never Portable Source IDs. This targeted session slice still requires durable
repository evidence; the search result alone is not source authority.

### Stable targeted slice identity example

Identify each snapshot by the stable tuple `(agent, session, slice)`:

- `agent` is the lowercase routing key (`claude`, `codex`, `hermes`,
  `openclaw`, `copilot`, or `pi`).
- `session` is the source's stable native session ID. If none exists, use
  `s-<20 hex>` from SHA-256 of the agent key, a repository-relative project
  label, and the source-internal start timestamp. Never hash or record an
  absolute path.
- Normalize query intent by trimming it, collapsing Unicode whitespace to one
  space, lowercasing ASCII A–Z only, and preserving every other Unicode code
  point. Sort stable turn/event anchors. Set `slice` to `q-<20 hex>` from
  SHA-256 of UTF-8 `normalized_query + "\n" + "\n".join(sorted_anchors)`.
- Store the snapshot at
  `sources/history/<agent>/<session>/<slice>.md`. The same tuple reuses that
  Source ID and updates it only when its recorded content hash changes. A
  different query or anchor set produces a different slice and never overwrites
  or deletes earlier evidence. Record the tuple, normalized query, anchors,
  timestamps, and content hash in the snapshot so provenance is reproducible.

The two queries below target one session but retain two independent Source IDs:

```json
{
  "agent": "claude",
  "session": "550e8400-e29b-41d4-a716-446655440000",
  "slices": [
    {
      "query": "Authentication   Middleware",
      "normalized_query": "authentication middleware",
      "anchors": ["turn-0001", "turn-0008"],
      "slice": "q-858dce8e821e2d50a92c",
      "source_id": "sources/history/claude/550e8400-e29b-41d4-a716-446655440000/q-858dce8e821e2d50a92c.md"
    },
    {
      "query": "Retry Policy",
      "normalized_query": "retry policy",
      "anchors": ["turn-0014", "turn-0021"],
      "slice": "q-521461c4a1c0732741c5",
      "source_id": "sources/history/claude/550e8400-e29b-41d4-a716-446655440000/q-521461c4a1c0732741c5.md"
    }
  ]
}
```

1. **Create or select reviewable source snapshots.** For the selected 3–5 sessions
   (or up to five recent sessions without a query), the parent agent
   creates, updates, or reuses one small, reviewable UTF-8 Markdown or plain-text
   snapshot strictly below the configured `sources` root per coherent targeted
   session slice. Record agent identity, session identity, stable slice identity,
   query/relevance, relevant excerpts, source timestamps, and a content hash.
   Redact secrets, internal reasoning, and private machine context; use
   repository-relative labels and include no machine-local absolute paths.
   Preserve valid Unicode exactly. If an adequate snapshot cannot be created,
   stop or use Personal mode.
2. **Review and accept every selected snapshot.** After creation, update, or
   reuse, the parent agent reviews and accepts every selected snapshot, including
   its identity tuple, excerpts, hash, redaction, and Source ID. If any snapshot
   is rejected, incomplete, unsafe, or cannot be traced, stop before
   `transaction begin`. Each candidate `sources` cites only snapshot Source IDs
   that were accepted, never pseudo-sources, external cache paths, or live URLs.
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
5. **Write candidates.** New pages use `created = updated = started_at`; updates
   preserve the existing `created` and set `updated = started_at`. Write only
   final vault-relative knowledge paths with non-empty snapshot-source subsets.
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
11. **Report and stop.** Return the synthesized answer only after the selected completion branch finishes.
   Include selected session identities, snapshot Source IDs, created/updated/
   removed pages, validation warnings, recovery, hot status, and evidence gaps.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Keep the
concrete vault, target history root, QMD CLI, and QMD collection values in
agent memory: config resolution does not export these values into the parent shell.
Write the prepared pages directly below `<resolved-vault-path>`, retaining the
existing `<agent>://<path/to/session>` Personal provenance, using current ISO
timestamps, and preserving `created` on update.

### Personal direct writes and Git safety

Apply any owner-required Personal Git snapshot to the concrete resolved vault
before direct writes. Write or merge prepared pages at their final paths below
`<resolved-vault-path>`; stop before tracking if any write fails.

### Personal manifest v1 and cache

For each selected session file, update
`<resolved-vault-path>/.manifest.json` as manifest v1 while preserving
unrelated entries and canonical expanded Personal paths:

```json
{
  "<resolved-session-path>": {
    "ingested_at": "<now>",
    "source_type": "<agent>_conversation",
    "modified_at": "<file mtime>",
    "pages_created": [],
    "pages_updated": []
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
- [TIMESTAMP] WIKI-AGENT agent=<agent> query="<query>" sessions_searched=N sessions_ingested=M pages_created=X pages_updated=Y
```

Read `<resolved-vault-path>/hot.md`, create the `wiki-ingest` template if
missing, record a one-line conceptual summary, keep three operations, and bump
`updated`.

### Personal QMD refresh and answer

When the concrete QMD collection is configured, refresh only after all
Personal writes. Failure does not roll back the vault.

```bash
<resolved-qmd-cli> update
<resolved-qmd-cli> embed
<resolved-qmd-cli> get "qmd://<resolved-qmd-wiki-collection>/<page>.md" -l 5
```

Use `embed` only for stale/missing vectors. Then return the prepared synthesized
answer with selected sessions, page changes, QMD status, and gaps.

Do not fall through into Portable Repository completion. Report the Personal
page, manifest v1, cache, central-file, Personal Git snapshot, and QMD results,
then stop.

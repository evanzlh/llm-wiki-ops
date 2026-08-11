---
name: claude-history-ingest
description: >
  Ingest Claude Code conversation history into the Obsidian wiki. Use this skill when the user wants to mine
  their past Claude conversations for knowledge, import their ~/.claude folder, extract insights from
  previous coding sessions, or says things like "process my Claude history", "add my conversations to the wiki",
  "what have I discussed with Claude before". Also triggers when the user mentions their .claude folder,
  Claude projects, session data, past conversation logs, local-agent-mode sessions, or audit logs.
---

# Claude History Ingest — Conversation Mining

You are extracting knowledge from the user's past Claude Code conversations and distilling it into the Obsidian wiki. Conversations are rich but messy — your job is to find the signal and compile it.

This skill can be invoked directly or via the `wiki-history-ingest` router (`/wiki-history-ingest claude`).

## Before You Start

1. **Resolve config and ownership** — follow the Config Resolution Protocol in
   `llm-wiki/SKILL.md`: explicit `@name`, nearest ancestor
   `.obsidian-wiki/config.toml`, nearest ancestor `.env` containing
   `OBSIDIAN_VAULT_PATH`, `~/.obsidian-wiki/config`, then setup guidance. The
   parent agent resolves config and mode, records the concrete runtime vault
   and Claude-history paths, and reads the owner `AGENTS.md` at the resolved
   vault before any other work.
2. Select one terminal workflow after the shared analysis and page-preparation steps:
   **Portable Repository completion** or **Personal mode completion**. Never
   mix their page-write or tracking operations. The Portable branch implements
   the canonical Portable Write Protocol locally. All shared discovery,
   filtering, extraction, clustering, and drafting is strictly read-only. If
   batching is useful, use analysis-only workers: they may return session
   inventories, evidence, and page proposals, but may not resolve mode, create
   source snapshots, begin transactions, or write vault/tracking files. The
   parent agent owns completion.
3. **Read mode-appropriate state.** In Personal mode, read the concrete
   resolved vault's manifest v1 and `index.md` for delta and merge decisions.
   In Portable Repository mode, inspect existing knowledge pages read-only and
   treat Claude cache files as transient inputs until the parent materializes
   reviewed source snapshots in the Portable completion branch; never parse
   manifest v2 as a Personal source map. Personal append mode uses manifest v1.
   Portable append mode compares discovered agent/session identity and content hash against existing reviewed snapshots.
4. **Project Scoping** — read `WIKI_SKIP_PROJECTS` from config (comma-separated substrings). Exclude any project directory whose name contains one of them from **every** step below (scan, delta, sampling, manifest writes). If the user names extra projects to skip this run, add them. Apply the exclusion **once, uniformly** — don't hand-write `grep -v` filters into individual commands, which drifts between the scan and manifest steps.

## Ingest Modes

### Append Mode (default)

**Personal mode delta:** Compare each conversation JSONL or memory file with
the manifest v1 session map. A source is new when its canonical path is absent;
it is modified when its source timestamp is later than its `ingested_at` value.

**Portable Repository mode delta:** Inspect reviewed snapshots below the
configured `sources` root. A source is new when no reviewed snapshot has the
same agent/session identity; it is changed when the matching snapshot's
recorded content hash differs from the hash of the currently selected external
material.

**After mode-specific delta selection:** Process only sources classified as
new or changed by the selected rule. Never apply the Personal delta rule to a
Portable run.

This is usually what you want — the user ran a few new sessions and wants to capture the delta.

> **Personal mode only — Canonical paths when comparing.** Personal manifest
> v1 keys are absolute paths with `~` expanded (see `llm-wiki/SKILL.md` →
> `.manifest.json`). Before deciding a file is "new", expand its path the same
> way. The `scripts/manifest.py` helper does this for you with concrete values
> retained in agent memory:
>
> ```bash
> # New/modified sources, honoring WIKI_SKIP_PROJECTS + --skip, paths already canonical:
> python3 <resolved-wiki-repository-path>/scripts/manifest.py delta <resolved-vault-path> \
>   --scan "<resolved-claude-history-path>/projects/*/memory/*.md"
> # One-time repair if the manifest already mixes ~ and absolute keys:
> python3 <resolved-wiki-repository-path>/scripts/manifest.py normalize <resolved-vault-path> --dry-run
> ```
>
> The helper is optional and Personal-only. If it is unavailable, do the same
> expansion inline before every Personal manifest lookup and write. Never run
> it against Portable manifest v2.

### Pre-extraction (recommended — run before ingest)

Raw JSONL files are 80-90% noise: `tool_use` blocks, `thinking` blocks, `progress` events, and
`file-history-snapshot` entries dominate by byte count.  The `scripts/extract-jsonl.py` helper
strips all of that and writes compact signal-only JSON to `~/.claude/extracted/`, achieving
**50–200× file-size reduction** (e.g. 12 MB JSONL → 64 KB extracted).  This lets the skill read
5–10× more conversations per run within the same token budget.

**Personal mode:** run it as an optional pre-step before invoking this skill:

```bash
# First run — extract everything (skip excluded projects)
python3 <resolved-wiki-repository-path>/scripts/extract-jsonl.py --skip tsg,autom8

# Incremental — only sessions modified in the last day
python3 <resolved-wiki-repository-path>/scripts/extract-jsonl.py \
    --since "$(date -v-1d +%Y-%m-%d)" --skip tsg,autom8
```

Extracted files live at `~/.claude/extracted/<project-dir>/<session-id>.json` and contain:

```json
{
  "session_id": "uuid",
  "project": "-Users-name-myapp",
  "cwd": "/Users/name/myapp",
  "start_ts": "...",
  "end_ts": "...",
  "n_turns": 18,
  "n_user_words": 620,
  "turns": [
    {"role": "user",      "text": "..."},
    {"role": "assistant", "text": "..."}
  ]
}
```

**When Step 3 reads conversations, always prefer the extracted file over the raw JSONL.** (See Step 3.)

If `extract-jsonl.py` was not run first, fall back to raw JSONL — but note the coverage will be
shallower because each raw file costs far more tokens to read.

**Portable Repository mode:** extracted files under `~/.claude/extracted/`
remain transient analysis input, just like the raw history cache. Running the
helper is analysis-only: it does not create source authority or permit any
worker to write the repository or vault. Workers return selected excerpts and
identity metadata to the parent agent; the parent agent alone creates and
reviews the source snapshot, computes source closure, and owns the transaction.

### Conversation Sampling Heuristic

A history path can hold hundreds of conversation JSONLs — do not try to read them all. Per project:

- **If the project already has memory files** (`memory/*.md`), ingest those first (they are
  pre-distilled signal), then **also process conversations classified as new or
  changed by the selected mode's delta rule** — new conversations should still
  be captured even for memory-rich projects.
- **If the project has no memory files**, read only the **3 most recent** conversations (by mtime)
  to characterize it. Prefer pre-extracted files (see above) — they are cheap enough that you can
  read 5–10 in the same token budget as 1 raw JSONL.
- Always report what you sampled vs skipped (e.g. "agenttower: 7 memory files + 4 new conversations
  ingested, 14 unchanged conversations skipped"), so the coverage gap is visible rather than silent.

### Full Mode

Process everything regardless of prior tracking state. Use after a `wiki-rebuild` or if the user explicitly asks.

## Claude Code Data Layout

Claude Code stores data in two locations. Scan **both**.

### Source 1: `~/.claude/` (CLI sessions)

```
~/.claude/
├── projects/                          # Per-project directories
│   ├── -Users-name-project-a/         # Path-derived name (slashes → dashes)
│   │   ├── <session-uuid>.jsonl       # Conversation data (JSONL)
│   │   └── memory/                    # Structured memories
│   │       ├── MEMORY.md              # Memory index
│   │       ├── user_*.md              # User profile memories
│   │       ├── feedback_*.md          # Workflow feedback memories
│   │       └── project_*.md           # Project context memories
│   ├── -Users-name-project-b/
│   │   └── ...
├── sessions/                          # Session metadata (JSON)
│   └── <pid>.json                     # {pid, sessionId, cwd, startedAt, kind, entrypoint}
├── history.jsonl                      # Global session history
├── tasks/                             # Subagent task data
├── plans/                             # Saved plans
└── settings.json
```

### Source 2: `~/Library/Application Support/Claude/local-agent-mode-sessions/` (Desktop app agent sessions)

> **Pre-check first.** Many users are CLI-only and have no desktop sessions. Before walking the structure below, confirm it's non-empty:
> ```bash
> DESKTOP_SESSIONS="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"
> [ -d "$DESKTOP_SESSIONS" ] && find "$DESKTOP_SESSIONS" -name "audit.jsonl" | head -1
> ```
> If that prints nothing, skip this entire section (Source 2 + Step 3b) and don't narrate it.

The Claude desktop app stores local agent mode sessions here. The structure is deeply nested:

```
~/Library/Application Support/Claude/local-agent-mode-sessions/
└── <outer-uuid>/
    └── <inner-uuid>/
        ├── local_<session-uuid>.json          # Session metadata
        └── local_<session-uuid>/
            ├── audit.jsonl                    # Audit log — tool calls, file reads, commands run
            └── .claude/
                └── projects/
                    └── <path-encoded-name>/   # Same path-encoding as ~/.claude/projects/
                        └── <uuid>.jsonl       # Conversation transcript (same JSONL format as CLI)
```

**How to find all local-agent-mode sessions:**

```bash
# Find all session metadata files
find ~/Library/Application\ Support/Claude/local-agent-mode-sessions -name "local_*.json" -maxdepth 4

# Find all audit logs
find ~/Library/Application\ Support/Claude/local-agent-mode-sessions -name "audit.jsonl"

# Find all conversation transcripts
find ~/Library/Application\ Support/Claude/local-agent-mode-sessions -name "*.jsonl" -path "*/.claude/projects/*"
```

**Session metadata (`local_<uuid>.json`)** — JSON file with fields like `sessionId`, `cwd`, `startedAt`, `model`, `title`. Read this first to understand the session context before opening the transcript.

**Audit log (`audit.jsonl`)** — Each line is a JSON record of one agent action: tool calls (Read, Write, Bash, Edit), file accesses, shell commands executed, MCP calls. Useful for understanding *what the agent actually did* — often richer signal than the conversation text alone. Fields: `type`, `toolName`, `input`, `output`, `timestamp`, `sessionId`.

**Conversation transcript (`.claude/projects/.../<uuid>.jsonl`)** — Identical format to CLI conversation JSONL. Parse the same way as `~/.claude/projects/*/*.jsonl`.

### Key data sources ranked by value (both locations combined):

1. **Memory files** (`~/.claude/projects/*/memory/*.md`) — Pre-distilled, already wiki-friendly. Gold.
2. **Conversation JSONL** (both `~/.claude/projects/*/*.jsonl` and desktop app transcripts) — Full conversation transcripts. Rich but noisy.
3. **Audit logs** (`audit.jsonl` in desktop sessions) — Tool-call level record of what was done. Useful for extracting concrete actions, file patterns, and command patterns even when the conversation is sparse.
4. **Session metadata** (`sessions/*.json` and `local_*.json`) — Tells you which project, when, and what CWD.

## Step 1: Survey and Compute Delta

Scan both data locations to build one source inventory:

```bash
# --- Source 1: CLI sessions (~/.claude) ---
# Find all projects
Glob: ~/.claude/projects/*/

# Find memory files (highest value)
Glob: ~/.claude/projects/*/memory/*.md

# Find conversation JSONL files
Glob: ~/.claude/projects/*/*.jsonl

# --- Source 2: Desktop app local-agent-mode sessions ---
DESKTOP_SESSIONS="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"

# Session metadata
find "$DESKTOP_SESSIONS" -name "local_*.json" -maxdepth 4

# Audit logs
find "$DESKTOP_SESSIONS" -name "audit.jsonl"

# Conversation transcripts
find "$DESKTOP_SESSIONS" -name "*.jsonl" -path "*/.claude/projects/*"
```

**Personal mode survey:** Compare the unified inventory with the concrete
vault's manifest v1 session map. Classify a file as **New** when its canonical
path is absent, **Modified** when its source timestamp is later than
`ingested_at`, and **Unchanged** otherwise.

**Portable Repository mode survey:** Inspect reviewed snapshots under the
configured `sources` root. Classify selected material as **New** when no
reviewed snapshot records the same agent/session identity, **Changed** when a
matching snapshot's recorded content hash differs from the freshly computed
hash, and **Unchanged** when both identity and hash match.

Report the selected mode and its result: "Found X CLI projects, Y desktop
sessions. Memory files: A. Conversations: B. Audit logs: C. Delta: D new, E
changed."

## Step 2: Ingest Memory Files First

Memory files are already structured with YAML frontmatter:

```markdown
---
name: memory-name
description: one-line description
type: user|feedback|project|reference
---

Memory content here.
```

For each memory file:

- Read it and parse the frontmatter
- `user` type → feeds into an entity page about the user, or concept pages about their domain
- `feedback` type → feeds into skills pages (workflow patterns, what works, what doesn't)
- `project` type → feeds into entity pages for the project
- `reference` type → feeds into reference pages pointing to external resources

The `MEMORY.md` index file in each project is a quick summary — read it first to decide which individual memory files are worth reading in full.

## Step 3: Parse Conversation JSONL

**Always check for a pre-extracted file first** (see Pre-extraction section above).  For each
conversation `~/.claude/projects/<proj>/<uuid>.jsonl`, look for its counterpart at
`~/.claude/extracted/<proj>/<uuid>.json`.  If found, read that instead — it is already filtered to
user + assistant text turns and costs 50–200× fewer tokens than the raw JSONL.

```
# Resolution order for each session:
1. ~/.claude/extracted/<project>/<session-id>.json   ← prefer (compact, signal-only)
2. ~/.claude/projects/<project>/<session-id>.jsonl   ← fallback (raw, noisy)
```

**Reading a pre-extracted file:** it already contains only the turns you need.  Iterate
`turns[].{role, text}` directly.  The top-level fields (`cwd`, `start_ts`, `n_user_words`, etc.)
give you project context without any further parsing.

**Reading raw JSONL (fallback):** Each line is a JSON object:

```json
{
  "type": "user|assistant|progress|file-history-snapshot",
  "message": {
    "role": "user|assistant",
    "content": "text string"
  },
  "uuid": "...",
  "timestamp": "2026-03-15T10:30:00.000Z",
  "sessionId": "...",
  "cwd": "/path/to/project",
  "version": "2.1.59"
}
```

For assistant messages, `content` may be an array of content blocks:

```json
{
  "content": [
    {"type": "thinking", "text": "..."},
    {"type": "text", "text": "The actual response..."},
    {"type": "tool_use", "name": "Read", "input": {...}}
  ]
}
```

- Filter to `type: "user"` and `type: "assistant"` entries only
- For assistant entries, extract `text` blocks (skip `thinking` and `tool_use` — those are noise)
- The `cwd` field tells you which project this conversation belongs to
- Skip `type: "progress"` — internal agent progress updates
- Skip `type: "file-history-snapshot"` — file state tracking
- Skip subagent conversations (under `subagents/` subdirectories) — unless the user asks

## Step 3b: Parse Audit Logs (desktop sessions only)

For each `audit.jsonl` found under `local-agent-mode-sessions/`, read it line by line. Each line is a JSON record of one agent action:

```json
{
  "type": "tool_call",
  "toolName": "Bash",
  "input": {"command": "npm test"},
  "output": "...",
  "timestamp": "2026-04-10T14:22:00Z",
  "sessionId": "..."
}
```

**What to extract from audit logs:**

- **File access patterns** — which files does the agent repeatedly Read or Edit? These are the high-value files in the project. Note them as project references.
- **Shell commands** — recurring Bash commands reveal the project's build/test/deploy workflow. Distill these into a `skills/` page (e.g. "how this project is built and tested").
- **Tool call sequences** — if the agent always does Read → Edit → Bash in a particular order, that's a workflow pattern worth capturing.
- **Error patterns** — failed tool calls (non-zero exit codes, error outputs) reveal pain points, known rough edges, or recurring bugs.
- **MCP tool calls** — calls to MCP tools reveal which external services and APIs the project integrates with.

**Skip from audit logs:**

- Routine file reads with no pattern (e.g. reading config files once)
- Tool outputs that are just noise (long stack traces, verbose logs) — summarize the error class, not the full output
- Anything that looks like secrets, tokens, or credentials in command arguments or outputs

**Cross-reference with the conversation transcript:** The audit log tells you *what happened*; the conversation tells you *why*. When both are available for the same session, use them together — the audit log grounds the conversation in concrete actions.

Read the paired `local_<uuid>.json` session metadata before processing the audit log — it gives you `cwd`, `startedAt`, and `title` to contextualize the actions.

## Step 4: Cluster by Topic

Don't create one wiki page per conversation. Instead:

- Group extracted knowledge **by topic** across conversations
- A single conversation about "debugging auth + setting up CI" → two separate topics
- Three conversations across different days about "React performance" → one merged topic
- The project directory name gives you a natural first-level grouping

## Step 5: Distill into Wiki Pages

Each Claude project maps to a project directory in the vault. The project directory name from `~/.claude/projects/` encodes the original path — decode it to get a clean project name:

```
-Users/Documents/projects/my-Project   → myproject
-Users/Documents/projects/Another-app  → anotherapp
```

### Project-specific vs. global knowledge

| What you found                     | Where it goes               | Example                                             |
| ---------------------------------- | --------------------------- | --------------------------------------------------- |
| Project architecture decisions     | `projects/<name>/concepts/` | `projects/my-project/concepts/main-architecture.md` |
| Project-specific debugging         | `projects/<name>/skills/`   | `projects/my-project/skills/api-rate-limiting.md`   |
| General concept the user learned   | `concepts/` (global)        | `concepts/react-server-components.md`               |
| Recurring problem across projects  | `skills/` (global)          | `skills/debugging-hydration-errors.md`              |
| A tool/service used                | `entities/` (global)        | `entities/vercel-functions.md`                      |
| Patterns across many conversations | `synthesis/` (global)       | `synthesis/common-debugging-patterns.md`            |

For each project with content, create or update the project overview page at `projects/<name>/<name>.md` — **named after the project, not `_project.md`**. Obsidian's graph view uses the filename as the node label, so `_project.md` makes every project show up as `_project` in the graph. Naming it `<name>.md` gives each project a distinct, readable node name.

**Important:** Distill the _knowledge_, not the conversation. Don't write "In a conversation on March 15, the user asked about X." Write the knowledge itself, with the conversation as a source attribution.

**Write a `summary:` frontmatter field** on every new/updated page — 1–2 sentences, ≤200 chars, answering "what is this page about?" for a reader who hasn't opened it. `wiki-query`'s cheap retrieval path reads this field to avoid opening page bodies.

**Add confidence and lifecycle fields** to every new page's frontmatter:
```yaml
base_confidence: 0.42
lifecycle: draft
lifecycle_changed: <ISO date today>
```
On update, leave `lifecycle` and `lifecycle_changed` unchanged — only a human editor transitions lifecycle state.

**Mark provenance** per the convention in `llm-wiki` (Provenance Markers section):

- **Memory files** are mostly extracted — the user wrote them by hand and they're already distilled. Treat memory-derived claims as extracted unless you're stitching together claims from multiple memory files.
- **Conversation distillation** is mostly inferred. You're synthesizing a coherent claim from many turns of dialogue, often filling in implicit reasoning. Apply `^[inferred]` liberally to synthesized patterns, generalizations across sessions, and "what the user really meant" interpretations.
- Use `^[ambiguous]` when the user changed their mind across sessions or when assistant and user contradicted each other and the resolution is unclear.
- Write a `provenance:` frontmatter block on every new/updated page summarizing the rough mix.

## Privacy

- Distill and synthesize — don't copy raw conversation text verbatim
- Skip anything that looks like secrets, API keys, passwords, tokens
- If you encounter personal/sensitive content, ask the user before including it
- The user's conversations may reference other people — be thoughtful about what goes in the wiki

## Reference

See `references/claude-data-format.md` for more details on the data structures.

## Portable Repository completion

Use this branch only when config resolution selected Portable Repository mode.
The external history cache and selected session files are transient analysis input,
never Portable Source IDs.

1. **Materialize reviewable source authority before the transaction.** The
   parent agent creates one small, reviewable UTF-8 Markdown or plain-text snapshot
   strictly below the configured `sources` root for each selected Claude
   session or coherent session slice. Each snapshot records the agent identity,
   session identity, relevant excerpts, source timestamps, and a content hash
   of the selected external material. Redact secrets and internal reasoning;
   store repository-relative project labels instead of machine context, and
   include no machine-local absolute paths. Preserve valid Unicode in excerpts,
   filenames, and Source IDs exactly; do not transliterate or normalize it.
   Review every snapshot before using it. If an adequate snapshot cannot be created
   without binary/private material or lost provenance, stop or use Personal mode.
   Candidate page `sources` may cite only these snapshot Source IDs, never raw
   Claude cache paths, helper outputs, live URLs, or pseudo-sources.
2. **Compute full source closure before `transaction begin`.** Include every
   existing `sources` Source ID from each page that will be updated or deleted,
   plus every new snapshot Source ID. The set is immutable after begin.
3. **Begin once from the repository root.** Keep the repository root as the command CWD.
   Run
   `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty`.
   Record `transaction_id`, the absolute runtime-only `candidate_vault`,
   `started_at`, and canonical Source IDs; do not `cd` into it or persist the
   absolute path.
4. **Write reviewed candidates.** For a new page, set
   `created = updated = started_at`. For an update, preserve the existing `created`
   and set `updated = started_at`. Write only final vault-relative knowledge-page
   paths below `candidate_vault`, with a non-empty `sources` subset of the
   transaction Source IDs.
5. **Declare removals.** Use
   `obsidian-wiki transaction delete <id> <vault-relative-page.md>` for every
   reviewed obsolete page. If the requested change cannot be represented by
   candidate knowledge pages or declared deletions, report it as unsupported
   without mutating the live vault.
6. **Validate and commit.** Run
   `obsidian-wiki transaction validate <id> --json --pretty`. Review every warning;
   warnings do not block commit. Fix every issue and rerun validation because
   issues do block commit. Only after a passing report run
   `obsidian-wiki transaction commit <id> --json --pretty`.
7. **Use status-aware recovery.** On a JSON failure, follow only the trusted
   `recovery.preferred_action` or a listed alternative whose prerequisites hold.
   Confirm the retained record with `obsidian-wiki transaction list --json`:
   its `recommended_action` must agree and the chosen command must appear in
   `allowed_actions`. Fix and revalidate an active preflight failure or run
   `obsidian-wiki transaction abort <id> --json`; retry/restore/discard are
   invalid while active. A `promoting` record permits only its reported
   `obsidian-wiki transaction restore <id> --json`. For a `failed` record,
   prefer its reported `obsidian-wiki transaction retry <id> --json`; use
   `obsidian-wiki transaction restore <id> --json` or
   `obsidian-wiki transaction discard <id> --json` only when listed and its
   prerequisites hold. A configuration or begin failure with no trusted
   transaction ID, or an empty list, has no recovery action. Never begin a
   replacement while an outcome is ambiguous.
8. **Refresh local hot context only after commit succeeds or recovery is fully resolved.**
   Run `obsidian-wiki hot status --json`. If stale, run
   `obsidian-wiki hot inputs --json --pretty`, use only those bounded inputs to write
   the semantic `hot.md` as the agent, then run
   `obsidian-wiki hot mark-current --json`. This ignored local write is outside
   the transaction.
9. **Report and stop.** Report selected sessions, snapshots, created/updated/
   removed pages, validation warnings, recovery, and hot-cache status.

Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, write `hot.md` as part of the transaction, refresh Personal QMD tracking, create a Git snapshot, commit, or push.

Stop the portable workflow here. Do not continue into Personal mode completion.

## Personal mode completion

Use this branch only when config resolution selected Personal mode. Hold the
concrete resolved vault, history, repository-helper, QMD CLI, and QMD collection
values in agent memory: config resolution does not export these values into the parent shell.
Write the prepared pages directly below `<resolved-vault-path>` and use the
current ISO timestamp for `created`/`updated`, preserving `created` on update.

### Personal direct writes and Git safety

Apply any owner-required Personal Git snapshot rule against the concrete
resolved vault before direct writes; do not apply it in Portable mode. Then
write or merge all prepared project and global pages directly at their final
paths below `<resolved-vault-path>`. Stop before tracking if any page write
fails.

### Personal manifest v1 and cache

For each processed Claude conversation, memory, audit log, or desktop session,
update `<resolved-vault-path>/.manifest.json` as manifest v1 with
`ingested_at`, `size_bytes`, `modified_at`, source type, decoded project, and
created/updated page lists. Preserve canonical expanded absolute Personal
source keys and unrelated manifest entries. Retain the existing project summary:

```json
{
  "project-name": {
    "source_path": "<resolved-claude-history-path>/projects/<encoded-project>",
    "vault_path": "projects/project-name",
    "last_ingested": "TIMESTAMP",
    "conversations_ingested": 5,
    "conversations_total": 8,
    "memory_files_ingested": 3,
    "desktop_sessions_ingested": 2,
    "audit_logs_ingested": 2
  }
}
```

Record each source-to-page mapping with concrete values:

```bash
obsidian-wiki cache-update <resolved-vault-path> <source> --pages <page1> [page2 ...] --json --pretty
```

### Personal central files

Update `<resolved-vault-path>/index.md` for every created or changed page.
Append this existing entry to `<resolved-vault-path>/log.md`:

```text
- [TIMESTAMP] CLAUDE_HISTORY_INGEST projects=N conversations=M desktop_sessions=D audit_logs=A pages_updated=X pages_created=Y mode=append|full
```

Read `<resolved-vault-path>/hot.md`, creating it from the `wiki-ingest`
template if missing. Update **Recent Activity** with the conceptual ingest,
keep the last three operations, update **Active Threads** when useful, and bump
the frontmatter `updated` timestamp.

### Personal QMD refresh

QMD is a search index, not the source of truth. If the concrete resolved QMD
collection is empty or unset, skip it. Otherwise run after all Personal vault
and tracking writes; failure does not roll back the vault changes.

```bash
<resolved-qmd-cli> update
<resolved-qmd-cli> embed
<resolved-qmd-cli> get "qmd://<resolved-qmd-wiki-collection>/<page>.md" -l 5
```

Use `embed` only when vectors are stale or missing. Report refreshed,
skipped/unconfigured, unavailable, or failed status separately.

Do not fall through into Portable Repository completion. Report the Personal
page, manifest v1, central-file, cache, Personal Git snapshot, and QMD results,
then stop.

# Obsidian Wiki — Agent Context

A **skill-based framework** for building and maintaining an Obsidian knowledge
base. Agents execute Markdown skills directly; the installed Python CLI handles
deterministic setup, validation, and repository maintenance.

## README Translation Parity

`README.md` and `README_ZH.md` are one documentation surface. Keep headings, examples, links, and user-facing behavior aligned between the English and Simplified Chinese versions. The check is advisory and never blocks a PR: the `readme-translation-drift` CI job only reports drift. Run `python tools/check_readme_sync.py` to list commits that changed `README.md` without a later `README_ZH.md` update, along with the pending English diff — then translate and backfill those changes into `README_ZH.md`. Reviewers assess translation quality.

## Configuration

Resolve config using the Config Resolution Protocol in `llm-wiki/SKILL.md`, in
this exact order:

0. **Inline vault override (`@name`) — explicit `@name`** — resolve
   `~/.obsidian-wiki/config.<name>` directly for this invocation only. If it is
   missing or invalid, do **not** silently fall back to the default.
1. **nearest ancestor `.obsidian-wiki/config.toml`** — walk up from CWD. This
   tracked file selects Portable Repository mode and is authoritative; if it is
   invalid, fail instead of falling back.
2. **nearest ancestor `.env` containing `OBSIDIAN_VAULT_PATH`** — walk up from
   CWD to `$HOME`.
3. **`~/.obsidian-wiki/config`** — use the personal global config.
4. **setup guidance** — if none exists, tell the user to run `wiki-setup`.

In Portable Repository mode, resolve `[paths]` relative to the repository root.
Only files below the configured `sources` paths are authoritative source
material. Keep runtime absolute paths in memory: never synthesize or commit an
absolute `OBSIDIAN_WIKI_REPO`. After any mode resolves the vault, read
`<vault>/AGENTS.md` if it exists; its owner conventions override framework
defaults for the rest of the session.

The resolved config sets `OBSIDIAN_VAULT_PATH` (where the wiki lives). Personal
setup may also set `OBSIDIAN_WIKI_REPO` (installed CLI bundled-data root).
Portable resolution exposes its repository root only as a runtime value; do not
persist computed machine paths in portable repository files.

### Targeting a specific vault

You can maintain multiple vaults (each a `~/.obsidian-wiki/config.<name>` file managed by `wiki-switch`) and reach any of them from any directory:

- **`@name` (per-invocation override)** — prefix or mention `@<name>` anywhere in a request to route that one command to that vault, e.g. `@work save this` or `wiki-query @personal what do I know about X`. It overrides the CWD `.env` and the active symlink **for that invocation only** — it does **not** flip your default vault. If `config.<name>` doesn't exist, the skill reports it and lists available vaults; do **not** silently fall back to the default. The `@name` is stripped before the rest of the request is used as content.
- **`/wiki-switch <name>` (persistent default)** — re-points the active symlink so all future requests use that vault. This is your default "brain" vault; use `@name` to dip into the other one without switching.

**After reading config, always read `$OBSIDIAN_VAULT_PATH/AGENTS.md` if it exists.** It contains owner-specific conventions (domain vocabulary, ingest preferences, writing style, project scoping) that override framework defaults for all skills. Apply it for the duration of the session.

## Portable Write Protocol

When configuration resolves Portable Repository mode, every write skill must
select its Portable Repository completion branch before any vault mutation and
must not continue into Personal mode completion:

- Before begin, compute source closure: include every existing Source ID from
  pages to update or delete plus every new authoritative source. The
  transaction source set is immutable, and page `sources` must be a non-empty
  subset of it.
- Keep the repository root as command CWD. Use the returned absolute
  `candidate_vault` only in runtime memory, do not `cd` into it, and never
  persist it in repository content or configuration.
- Use transaction `started_at` for page timestamps: set both timestamps on a
  new page, and preserve `created` while updating `updated` on an existing page.
- Declare removals through the transaction. Validate every candidate, fix all
  issues, review warnings, and commit only a passing report. On failure, follow
  only the trusted preferred/recommended action and `allowed_actions` for the
  retained status; a no-ID failure has no recovery action.
- The transaction owns manifest shards and operation records; portable writes
  do not update stable `index.md`/`log.md`, Personal QMD tracking, or Git state.
- Use `cache-check --configured` instead of assuming a shell-exported vault.
- Preserve Unicode Source IDs and filenames exactly; do not transliterate or
  normalize them.
- Treat `hot.md` as ignored local derived state. Only after commit succeeds or
  recovery is resolved, check status, gather bounded inputs when stale, let the
  agent rewrite it, and then mark it current.

## Vault Structure

Knowledge category directories are shared across modes. Root tracking and
control paths have mode-specific ownership as noted below; Portable skills
write only transaction candidates at supported knowledge-page paths.

```
$OBSIDIAN_VAULT_PATH/
├── index.md                # Personal catalog; stable Portable query surface
├── log.md                  # Personal activity log; stable Portable query surface
├── hot.md                  # Personal maintained cache; Portable ignored local cache
├── .manifest.json          # Personal v1; Portable v2 marker with transaction-owned shards
├── _meta/                   # Personal-managed metadata; not Portable candidates
│   ├── taxonomy.md         # Controlled tag vocabulary
│   └── *.base              # Obsidian Bases dashboards
├── _insights.md            # Personal graph output; Portable uses a synthesis page
├── _raw/                   # Personal staging inbox; not a Portable candidate path
├── _readouts/              # Personal derived readouts; not knowledge pages
├── concepts/               # Abstract ideas, patterns, mental models
├── entities/               # Concrete things — people, tools, libraries, companies
├── skills/                 # How-to knowledge, techniques, procedures
├── references/             # Factual lookups — specs, APIs, configs
├── synthesis/              # Cross-cutting analysis connecting multiple concepts
├── journal/                # Time-bound entries — daily logs, session notes
└── projects/
    └── <project-name>.md   # One page per project synced via wiki-update
```

Every wiki page has required frontmatter: `title`, `category`, `tags`, `sources`, `created`, `updated`. Pages connect via internal links — `[[wikilinks]]` by default, or standard Markdown links when `OBSIDIAN_LINK_FORMAT=markdown` is set in config.

## Skill Routing

Skills live in `.skills/<name>/SKILL.md`. Match the user's intent to the right skill:

| User says something like… | Skill |
|---|---|
| "set up my wiki" / "initialize" | `wiki-setup` |
| "/wiki-history-ingest claude" / "/wiki-history-ingest codex" / "/wiki-history-ingest hermes" / "/wiki-history-ingest pi" | `wiki-history-ingest` |
| "ingest" / "add this to the wiki" / "process these docs" / "process this export" / "ingest this data" / logs, transcripts / "/ingest-url <url>" / "add this URL" / "ingest this link" / "save this page" | `wiki-ingest` |
| "import my Claude history" / "mine my conversations" | `claude-history-ingest` |
| "import my Codex history" / "mine my Codex sessions" | `codex-history-ingest` |
| "import my Hermes history" / "mine my Hermes memories" / "ingest ~/.hermes" | `hermes-history-ingest` |
| "import my OpenClaw history" / "mine my OpenClaw sessions" / "ingest ~/.openclaw" | `openclaw-history-ingest` |
| "import my Copilot history" / "mine my Copilot sessions" / "ingest ~/.copilot" | `copilot-history-ingest` |
| "import my Pi history" / "mine my Pi sessions" / "ingest ~/.pi" | `pi-history-ingest` |
| "what's the status" / "what's been ingested" / "show the delta" | `wiki-status` |
| "wiki insights" / "hubs" / "wiki structure" | `wiki-status` (insights mode) |
| "what do I know about X" / "find info on Y" / any question | `wiki-query` |
| "use my vault as context" / "context pack for X" / "bounded context" | `wiki-context-pack` |
| "narrate" / "briefing" / "explain this topic" / "/wiki-narrate" | `wiki-narrate` |
| "audit" / "lint" / "find broken links" / "wiki health" | `wiki-lint` |
| "dedup my wiki" / "find duplicate pages" / "merge duplicates" / "identity resolution" / "consolidate my wiki" | `wiki-dedup` |
| "rebuild" / "start over" / "archive" / "restore" | `wiki-rebuild` |
| "link my pages" / "cross-reference" / "connect my wiki" | `cross-linker` |
| "fix my tags" / "normalize tags" / "tag audit" | `tag-taxonomy` |
| "update wiki" / "sync to wiki" / "save this to my wiki" | `wiki-update` |
| `@work update wiki` / `wiki-query @personal ...` / `@research save this` | Any matching wiki skill + Config Resolution Protocol `@name` override |
| "export wiki" / "export graph" / "graphml" / "neo4j" / "export to OKF" / "OKF bundle" / "open knowledge format" | `wiki-export` |
| "import wiki" / "import from export" / "load graph.json" / "import vault" / "import OKF bundle" / "/wiki-import" | `wiki-import` |
| "color my graph" / "color code obsidian" / "color by tag/category/visibility" | `graph-colorize` |
| "save this" / "/wiki-capture" / "capture this" / "file this conversation" / "/wiki-capture --quick" / "quick capture" / "capture this finding" / "save this gotcha" / "drop to raw" | `wiki-capture` |
| "/wiki-research [topic]" / "research X" / "find everything about Y" | `wiki-research` |
| "create a dashboard" / "vault dashboard" / "show all X as a table" / "dynamic view" | `wiki-dashboard` |
| "synthesize my wiki" / "find connections" / "what concepts keep coming up together" / "/wiki-synthesize" | `wiki-synthesize` |
| "create a new skill" | `skill-creator` |
| "/vault-skill-factory" / "make a skill from my wiki" / "turn these pages into a skill" / "package my notes on X as a skill" / "build a domain-expert skill from my vault" | `vault-skill-factory` |
| "/wiki-claude [topic]" / "/wiki-codex [topic]" / "/wiki-hermes [topic]" / "/wiki-openclaw [topic]" / "/wiki-copilot [topic]" / "/wiki-pi [topic]" | `wiki-agent` |
| "/memory-bridge" / "browse codex memory" / "what did codex know about X" / "compare tool memories" / "cross-tool memory" | `memory-bridge` |
| "/session-brain" / "build my session map" / "cluster my claude sessions" / "rebuild the session graph" / "what topics have gone stale" | `session-brain` |
| "/wiki-sessions [topic]" / "which session did I do X in" / "find the session about X" / "when did I last work on X" / "have I done this before" | `session-search` |
| "/daily-update" / "morning sync" / "refresh the wiki index" / "set up the daily cron" / "install terminal notification" | `daily-update` |
| "/impl-validator" / "check this implementation" / "validate what you did" / "is this correct?" | `impl-validator` |
| "/wiki-switch NAME" / "switch to my work wiki" / "switch vault" / "change wiki" / "list my wikis" / "show my vaults" / "create a new vault config" | `wiki-switch` |
| "/wiki-digest" / "what did I learn this week" / "weekly digest" / "knowledge summary" / "what's new in my wiki" / "summarize my recent learning" / "monthly review" | `wiki-digest` |
| "/wiki-context-pack" / "make a context pack" / "context slice for X" / "pack the wiki for my agent" / "bounded context for Y" | `wiki-context-pack` |
| "/wiki-stage-commit" / "review staged pages" / "commit staged writes" / "promote staged pages" / "what's waiting in staging" | `wiki-stage-commit` |
| "restyle Obsidian" / "adjust the vault layout" / "CSS snippet" / "tune tabs/sidebars/graph panes" | `obsidian-layout-adjustment` |

### Session history: ingest vs. retrieve

Three skills read agent session caches, and they are not interchangeable:

- `wiki-history-ingest` (and its per-agent variants) **ingests** — distils sessions into permanent vault pages.
- `wiki-agent` **ingests a slice** — finds sessions about one topic in another agent's history and pulls them into the vault.
- `session-brain` / `session-search` **retrieve** — build a topic graph over the raw sessions and find or load one. They write a sidecar at `~/.claude/session-brain/` and never touch the vault.

If the user wants knowledge preserved, ingest. If they want to find the session where something happened, retrieve.

## Cross-Project Usage

The main use case: you're working in some other project and want to sync knowledge into your wiki, query it, or compile bounded context. Three portable skills handle this — `wiki-update`, `wiki-query`, and `wiki-context-pack`. They work from any directory.

### wiki-update (write to wiki)

1. Resolve config using the Config Resolution Protocol to get `OBSIDIAN_VAULT_PATH`.
2. Scan the current project: README, source structure, git log, and package metadata.
3. Distill what's worth remembering (architecture decisions, patterns, and trade-offs — not code listings).
4. Complete the write by mode. Portable Repository mode computes source
   closure and writes final vault-relative candidates through one transaction;
   Personal mode writes directly to `$VAULT/projects/<project-name>.md` and
   cross-links concept/entity pages as needed.
5. Track the result by mode. Portable transaction commit owns manifest v2
   shards and the immutable operation record; `index.md` and `log.md` stay
   stable, and the optional local hot flow runs only after commit. Personal
   mode updates manifest v1, `index.md`, `log.md`, and `hot.md` directly.

On repeat Personal-mode runs, `last_commit_synced` in manifest v1 limits the
delta via `git log <last_commit>..HEAD`. Portable mode follows `wiki-update`'s
Source ID and transaction delta rules instead.

### wiki-query (read from wiki)

1. Resolve config using the Config Resolution Protocol to get `OBSIDIAN_VAULT_PATH`
2. Scan titles, tags, and `summary:` frontmatter fields first (cheap pass)
3. Only open page bodies when the index pass can't answer
4. Return a synthesized answer with `[[wikilink]]` citations

### wiki-context-pack (read-only context)

1. Resolve the target vault and read its owner `AGENTS.md`
2. Rank existing notes without requiring schema migration
3. Compile summaries and selected excerpts within a hard token budget
4. Return a provenance-rich pack; never write it back to the vault

## Visibility Tags (optional)

Pages can carry a `visibility/` tag to mark their intended reach. **This is entirely optional** — untagged pages behave exactly as they always have (visible everywhere). The system stays single-vault, single source of truth.

| Tag | Meaning |
|---|---|
| *(no tag)* | Same as `visibility/public` — visible in all modes |
| `visibility/public` | Explicitly public — visible in all modes |
| `visibility/internal` | Team-only — excluded when querying in filtered mode |
| `visibility/pii` | Sensitive data — excluded when querying in filtered mode |

**Filtered mode** is opt-in, triggered by phrases like "public only", "user-facing answer", "no internal content", or "as a user would see it" in a query. Default mode shows everything.

`visibility/` tags are **system tags** — they don't count toward the 5-tag limit and are listed separately from domain/type tags in the taxonomy.

See `wiki-query` and `wiki-export` skills for how the filter is applied.

## Core Principles

- **Compile, don't retrieve.** The wiki is pre-compiled knowledge. Merge new
  knowledge into existing pages rather than appending duplicates; the resolved
  mode determines whether that page is a transaction candidate or a direct
  Personal write.
- **Track everything by mode.** Personal writes update manifest v1,
  `index.md`, `log.md`, and `hot.md`. Portable commit owns manifest v2 shards
  and the immutable operation record; agents do not live-edit those tracking
  files or stable `index.md`/`log.md`.
- **Connect with `[[wikilinks]]`.** Every page should link to related pages. This is what makes it a knowledge graph, not a folder of files.
- **Frontmatter is required.** Every wiki page needs: `title`, `category`, `tags`, `sources`, `created`, `updated`.
- **Single source of truth.** Visibility tags shape how content is surfaced — they don't duplicate or separate it.
- **Keep context warm.** Personal writes maintain the ~500-word `hot.md`
  semantic snapshot. Portable mode treats it as ignored local derived state and
  uses status → bounded inputs → agent write → mark-current only after
  commit succeeds or recovery is resolved.

## Architecture Reference

For the full pattern (three-layer architecture, page templates, project org), read `.skills/llm-wiki/SKILL.md`.

Human-facing documentation lives in `docs/` — `installation.md`, `agents.md`, `skills.md`, `cli.md`, `configuration.md`, `architecture.md`, `session-brain.md`, `contributing.md`. `README.md` is a landing page only; when you add a skill, CLI command, or config variable, update the matching `docs/` page rather than the README.

The vault format is structurally conformant with the [Open Knowledge Format (OKF) v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) — markdown files with YAML frontmatter, category subfolders, reserved `index.md`/`log.md`. `wiki-export` (OKF mode) and `wiki-import` are the bridge: they translate between our native frontmatter (`title`/`category`/`tags`/`sources`/`created`/`updated` + `summary`) and OKF (`type`/`title`/`description`/`resource`/`tags`/`timestamp`), making vaults exchangeable with any OKF tool. The OKF round-trip is lossless; the `graph.json` round-trip is not.

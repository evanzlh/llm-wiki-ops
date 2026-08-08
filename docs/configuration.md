# Configuration

## How config is resolved

Skills and portable-aware CLI commands resolve the vault in this order:

0. **Explicit `@name`** — resolve `~/.obsidian-wiki/config.<name>` for that
   request only.
1. **Nearest ancestor `.obsidian-wiki/config.toml`** — walking up from CWD;
   its presence selects Portable Repository mode.
2. **Nearest ancestor `.env` containing `OBSIDIAN_VAULT_PATH`** — walking up
   from CWD to `$HOME`.
3. **`~/.obsidian-wiki/config`** — the personal global config.
4. **Setup guidance** — if no source exists, run setup.

Every discovered source is authoritative. A missing named profile or invalid
portable, `.env`, or global config fails closed instead of falling through to a
different vault.

After resolving, skills also read `$OBSIDIAN_VAULT_PATH/AGENTS.md` if it exists. That's where you put owner-specific conventions — domain vocabulary, ingest preferences, writing style, project scoping — which override framework defaults for every skill.

Both `~/.obsidian-wiki/config` and `.env` use the same `KEY=value` format. Start from [`.env.example`](../.env.example).

The deterministic `query`, `context-pack`, `lint`, `trust-record`,
`trust-check`, `sync`, `sync-setup`, and `doctor` commands use this same
resolution. An explicit filesystem path uses no unrelated config. Schema
settings come only from the resolved source, so one vault's extensions cannot
leak into another vault.

## Portable Repository configuration

Portable repositories track this schema at `.obsidian-wiki/config.toml`:

```toml
schema_version = 1
implementation = "evanzlh/obsidian-wiki"
requires_cli = ">=2026.8,<2026.9"

[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"

[settings]
OBSIDIAN_LINK_FORMAT = "wikilink"
```

All `[paths]` values are repository-relative, use forward slashes, and must
remain inside the repository after resolving existing components. The vault
and source paths must not overlap. `implementation` prevents a repository for
another fork from being opened silently; `requires_cli` is a PEP 440 version
constraint checked before use. Machine-specific settings and absolute paths
are rejected.

Configured `sources` paths define the only authoritative source roots for
portable ingest. `local_state` is ignored runtime state and may be absent in a
fresh clone. The CLI exposes computed absolute values such as
`OBSIDIAN_WIKI_REPO` only at runtime; agents must not write them into committed
files. Portable setup does not create or consult an extra repository `.env`.

The tracked `.obsidian-wiki/managed-skills.json` is the ownership boundary for
framework-managed skill directories. After installing a newer CLI, refresh the
repository copy with:

```bash
obsidian-wiki repo upgrade-skills
```

The command preserves unlisted owner skills and text outside managed bootstrap
markers.

### Portable tracked and ignored state

Portable setup establishes this repository boundary:

| State | Git | Repository paths | Rule |
|---|---|---|---|
| Authoritative sources | Tracked | configured `sources/**` | The only durable ingest inputs and the origin of repository-relative Source IDs. |
| Compiled knowledge | Tracked | `<vault>/{concepts,entities,skills,references,synthesis,projects}/**`, plus `<vault>/journal/**` except `<vault>/journal/operations/**` | Agents promote these pages through transactions; humans review their Git diff. |
| Compilation ledger | Tracked | `<vault>/.manifest.json`, `<vault>/.manifest/sources/**` | The marker is fixed and the CLI owns affected shards. |
| Operation history | Tracked | `<vault>/journal/operations/YYYY/MM/<UTC>-<suffix>.md` | One immutable, merge-friendly page per completed transaction. |
| Stable query surfaces | Tracked | `<vault>/index.md`, `<vault>/log.md` | Portable setup creates them, but ordinary transactions never rewrite them. Built-in queries scan pages, shards, and operation entries instead. |
| Repository contract | Tracked | `.obsidian-wiki/config.toml`, `.obsidian-wiki/managed-skills.json`, `.skills/**`, agent bootstrap/adapters | Clone-independent configuration and agent behavior. |
| Semantic hot cache | Ignored | `<vault>/hot.md` | Local derived context; invalidate and rebuild it after authoritative state or branch changes. |
| Transaction and recovery state | Ignored | `.obsidian-wiki/local/**` | Lock, candidate pages, preimages, snapshots, metadata, and hot fingerprint; never publish it. |
| Obsidian UI state | Ignored | `<vault>/.obsidian/workspace.json`, `<vault>/.obsidian/workspace-mobile.json`, `<vault>/.trash/**` | Machine-local viewer state, not knowledge. |

An edited or dirty source worktree is expected during compilation. Transaction
promotion does not require a globally clean repository: it checks preimages
only for affected output pages and manifest shards. If one of those targets
changed after `transaction begin`, promotion fails and retains recovery state;
unrelated source or output edits do not block it.

Do not override the ignored paths with `git add -f`. Portable changes stop in
the working tree. Review `git diff`, run `obsidian-wiki check`, and use the
repository's normal branch and pull-request workflow; the portable transaction
and hot-state commands never commit or push those changes.

## Manifest mode selected by configuration

Personal `.env` and global configurations keep manifest v1: one monolithic
`$OBSIDIAN_VAULT_PATH/.manifest.json`. External source keys use expanded
absolute paths, while existing vault-relative keys remain supported for
in-vault sources.

The presence of a valid `.obsidian-wiki/config.toml` selects manifest v2. Its
`[paths].sources` entries are the authority boundary: every durable
repository-relative Source ID must use `/`, be normalized, and name an ordinary
file below the source root. Although the TOML value is a list, manifest v2
schema 1 requires exactly one configured source root; multiple entries fail
closed. The portable vault contains a fixed `<vault>/.manifest.json` marker and
one entry below `<vault>/.manifest/sources/` per source.

Status scans ignore `.gitkeep` and any file with hidden source path components
(relative components beginning with `.`). These placeholders and hidden files
are not authoritative tracked sources.

Use `obsidian-wiki cache-check` and `cache-update` for v2 state. Do not run the
legacy `scripts/manifest.py` commands, manually edit shards, or replace the
marker with a monolithic source map. A live URL or external filesystem path is
not a durable Source ID; store necessary external material as a small,
reviewable snapshot below `sources` using ordinary Git. Git LFS pointers are
unsupported. Portable manifest entries do not record model, agent, API, or
generation-tool provenance.

See [Architecture → Manifest protocols](architecture.md#manifest-protocols) for
the marker and shard JSON shapes, and [CLI Reference → Portable repository
validation](cli.md#portable-repository-validation) for the deterministic CI
gate.

## Core

| Variable | What it does | Default |
|---|---|---|
| `OBSIDIAN_VAULT_PATH` | **Required.** Absolute path to your vault | — |
| `OBSIDIAN_WIKI_REPO` | Installed CLI bundled-data root (set by personal setup; used for skill/asset lookups) | *auto* |
| `OBSIDIAN_SOURCES_DIR` | Comma-separated source directories to ingest documents from | *(empty)* |
| `OBSIDIAN_CATEGORIES` | Wiki page categories (directories created in the vault) | `concepts,entities,skills,references,synthesis,journal` |
| `OBSIDIAN_MAX_PAGES_PER_INGEST` | Max pages created or updated per ingest | `15` |
| `OBSIDIAN_LINK_FORMAT` | `wikilink` → `[[concepts/foo]]`, or `markdown` → `` [text](path.md) ``. Affects future writes only — existing content is never migrated | `wikilink` |
| `OBSIDIAN_RAW_DIR` | Staging directory inside the vault for unprocessed drafts | `_raw` |
| `LINT_SCHEDULE` | Health-check frequency: `daily` \| `weekly` \| `manual` | `weekly` |

Local git repo clones work in `OBSIDIAN_SOURCES_DIR` (public or private, any host). Clone locally, then add the path. Repo directories are auto-detected via a `.git` folder and enumerated with `git ls-files`, so whatever the repo's own `.gitignore` excludes — `node_modules`, build output, venvs, secrets — is skipped automatically rather than relying on a hardcoded skip-list.

## History ingest

| Variable | What it does | Default |
|---|---|---|
| `CLAUDE_HISTORY_PATH` | Where to find Claude data | *auto-discovers from `~/.claude`* |
| `CODEX_HISTORY_PATH` | Where to find Codex data | `~/.codex` |
| `HERMES_HISTORY_PATH` | Where to find Hermes data | `~/.hermes` |
| `OPENCLAW_HISTORY_PATH` | Where to find OpenClaw data | `~/.openclaw` |
| `COPILOT_HISTORY_PATH` | Where to find Copilot CLI data | `~/.copilot/session-state` |
| `PI_HISTORY_PATH` | Where to find Pi sessions | `~/.pi/agent/sessions` |
| `WIKI_SKIP_PROJECTS` | Comma-separated substrings; project dirs matching any are skipped during scan, delta, and manifest steps. e.g. `archived,scratch,sandbox` | *(empty)* |
| `WIKI_SESSION_BRAIN_DIR` | Where the session-brain sidecar is written | `~/.claude/session-brain` |

## Staged writes & trust

`WIKI_STAGED_WRITES` and `_staging/` are Personal-mode features. Portable
configuration does not accept this setting; Portable writes use ignored local
transactions and `candidate_vault` review instead.

| Variable | What it does | Default |
|---|---|---|
| `WIKI_STAGED_WRITES` | Personal mode only: when `true`, LLM-written pages land in `_staging/` for human review instead of the live vault. Promote them with `/wiki-stage-commit` | *(unset — direct writes)* |
| `OBSIDIAN_TRUST_STRICT` | When `1`, `obsidian-wiki lint` treats missing trust fields, ledger errors, stale reviews, and score mismatches as failures rather than warnings. Same as `lint --strict-trust` | *(unset)* |
| `OBSIDIAN_ALLOWED_LIFECYCLES` | Comma-separated lifecycle extensions for this resolved vault | *(framework defaults only)* |
| `OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES` | Comma-separated relationship-type extensions for this resolved vault | *(framework defaults only)* |
| `OBSIDIAN_REQUIRED_TRUST_FIELDS` | Comma-separated effective required trust fields. Allowed values: `base_confidence`, `lifecycle`, `lifecycle_changed`, `updated`; unknown values fail closed | `base_confidence,lifecycle` for lint; also `updated` for standalone trust commands |
| `OBSIDIAN_SCHEMA_SOURCE` | Owner authority locator emitted in machine reports | `config:<resolved-config-path>` when overrides exist |

Schema resolution precedence is CLI flags > resolved environment/config values > framework defaults. Lifecycle and relationship-type extension lists are additive to framework defaults; CLI required-field values replace environment requiredness. CLI-only schema overrides use a `cli:<context>` source label rather than claiming a config-file provenance. If CLI and resolved config both contribute overrides, reports use `cli+config:<resolved-config-path>` unless `--schema-source` or `OBSIDIAN_SCHEMA_SOURCE` supplies the owner authority explicitly.

The four schema variables are `OBSIDIAN_ALLOWED_LIFECYCLES`, `OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES`, `OBSIDIAN_REQUIRED_TRUST_FIELDS`, and `OBSIDIAN_SCHEMA_SOURCE`. When any is present, its value and every comma-separated entry must be non-empty after trimming whitespace. Empty values, repeated commas, and trailing commas fail closed with exit 1; remove the variable entirely to use framework defaults. The distributable `.env.example` documents safe commented examples for all four.

Staged pages aren't visible in Obsidian's graph until promoted. `wiki-status` lists pending staged writes first when this mode is on — the work is done, it just needs your eyes. Personal setup creates `_staging/` even when the mode is off; Portable setup deliberately does not.

## Vault Skill Factory

`vault-skill-factory` turns mature curated pages into portable Agent Skills. Generated skills land in a **review directory** — never auto-installed, never written into `.skills/`.

| Variable | What it does | Default |
|---|---|---|
| `SKILL_FACTORY_OUTPUT_DIR` | Where generated skills are written | `<vault>/_generated-skills` |
| `SKILL_FACTORY_MATURITY` | Which lifecycle states count as mature enough to harvest (pages with `tier: core` also qualify) | `reviewed,verified` |

## PageIndex (optional, long PDFs)

For long PDFs — books, reports — PageIndex builds a table-of-contents tree (section titles, summaries, page ranges) before ingest, so the agent reads only the relevant sections. Without it, `wiki-ingest` reads PDFs directly.

Install: clone [PageIndex](https://github.com/VectifyAI/PageIndex), create a venv, and put an LLM key in its `.env` (LiteLLM). See `.skills/wiki-ingest/references/pageindex.md`.

| Variable | What it does | Default |
|---|---|---|
| `PAGEINDEX_REPO` | Path to the PageIndex repo — setting this enables the long-PDF branch | *(empty — disabled)* |
| `PAGEINDEX_MODEL` | LiteLLM model id PageIndex uses | `openai/glm-4.6` |
| `PAGEINDEX_MIN_PAGES` | Only preprocess PDFs with at least this many pages | `30` |
| `PAGEINDEX_WORKSPACE` | Cache dir for `*_structure.json` | `<PAGEINDEX_REPO>/results` |

## QMD semantic search (optional)

By default, `wiki-ingest` and `wiki-query` use Grep/Glob — fully functional, no extra setup. If your vault grows large or you want concept-level matches across your sources, plug in [QMD](https://github.com/tobi/qmd), either through MCP or by letting the agent call the local `qmd` CLI.

| Variable | What it does | Default |
|---|---|---|
| `QMD_WIKI_COLLECTION` | Collection indexing your compiled wiki pages — used by `wiki-query` | *(empty — disabled)* |
| `QMD_PAPERS_COLLECTION` | Collection indexing your raw source documents — used by `wiki-ingest` | *(empty — disabled)* |
| `QMD_TRANSPORT` | `mcp` (agent-configured MCP server) or `cli` (local `qmd` binary) | `mcp` |
| `QMD_CLI_SEARCH_MODE` | `quality` (rerank, best relevance), `balanced` (`--no-rerank`), or `fast` (semantic only) | `quality` |
| `QMD_CLI` | Override the `qmd` binary path if it isn't on `PATH` | `qmd` |

**Setup:**

```bash
qmd collection add /path/to/vault --name my-wiki
qmd collection add /path/to/sources --name papers
```

```env
QMD_WIKI_COLLECTION=my-wiki
QMD_PAPERS_COLLECTION=papers
QMD_TRANSPORT=mcp
QMD_CLI_SEARCH_MODE=quality
```

> **The two collections must stay disjoint.** `wiki-query` treats them as separate layers — compiled knowledge vs. raw staging — and cites them separately. Since `OBSIDIAN_VAULT_PATH` contains `_raw/`, a plain `qmd collection add <vault>` merges the two layers and makes superseded drafts retrievable and citable as though they were compiled pages.
>
> QMD has no `--ignore` flag, so scope the collection by editing `~/.config/qmd/index.yml`:
>
> ```yaml
> collections:
>   my-wiki:
>     path: /path/to/vault
>     pattern: "**/*.md"
>     ignore:
>       - "_raw/**"
>       - "log.md"
> ```
>
> Then run `qmd update`.

**What changes when it's on:**

- `wiki-query` runs a semantic pass (lex+vec) against your wiki collection before falling back to Grep — finds conceptually related pages even when the exact terms don't match.
- `wiki-ingest` queries your papers collection before writing a new page — surfaces related sources, spots contradictions, and decides whether to create a new page or merge into an existing one.

Both degrade gracefully: with the collection names unset, they skip the QMD step silently and use Grep.

## `_raw/` staging directory

In Personal mode, `_raw/` is a staging area inside your vault for unprocessed captures — rough notes, clipboard pastes, quick voice-memo transcripts. Drop files there and the next `wiki-ingest` run promotes them to proper wiki pages and removes the originals, so nothing is processed twice.

The fastest way to feed it during a live coding session:

```text
/wiki-capture --quick
```

It scans the current conversation, extracts bugs and gotchas, and writes structured draft files in under 60 seconds — no subagents, no manifest writes.

To promote everything waiting there:

```text
/wiki-ingest promote my raw pages
```

The directory is created automatically by `wiki-setup`. The path is configurable via `OBSIDIAN_RAW_DIR`.

Portable setup reserves `wiki/_raw/`, but it is neither an authoritative source
root nor a valid transaction candidate category. Portable quick capture
therefore fails closed instead of writing it directly: preserve the material
under a configured `sources` path, then run `wiki-ingest` so the resulting
knowledge page is promoted through a transaction.

### Browser capture extension

This repo includes a zero-build Chrome extension at [`extensions/brain-capture/`](../extensions/brain-capture/) for saving web pages and selected text straight into `_raw/`.

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select `extensions/brain-capture`

To find your configured `_raw` folder from a clone of this repo:

```bash
awk -F= '/^OBSIDIAN_VAULT_PATH=/{print $2 "/_raw"; exit}' "$(git rev-parse --show-toplevel)/.env"
```

## Syncing your vault to GitHub

Your vault is a directory of plain markdown files — push it to a private GitHub repo and you get version history, backup, and cross-device sync for free. The source-installed CLI offers this during personal setup and exposes the same implementation later through `obsidian-wiki sync-setup`.

This section applies to Personal-mode vault repositories. A Portable
Repository already has an enclosing Git workflow: use local transactions for
knowledge writes, inspect `git diff`, and let a human commit/push a branch and
open the pull request. Do not use the Personal-mode `sync` or `sync-setup`
commands for a Portable Repository; transaction and hot-state commands never
commit or push.

**What setup does:**

1. `git init` your vault if it isn't already a repo
2. Creates a `.gitignore` excluding Obsidian workspace/cache files
3. Sets the remote you supply — the vault's own `git remote`, not a config file, is the source of truth for whether sync is configured, so it can't drift
4. Optionally adds a `wiki-sync` shell alias
5. Optionally installs an hourly cron job

**Run a sync at any time:**

```bash
wiki-sync            # alias added by setup
obsidian-wiki sync   # or call it directly
```

Each run stages all changes, commits as `sync 2026-07-30 14:00`, and pushes.

**Configure it later, or by hand:**

```bash
obsidian-wiki sync-setup https://github.com/you/my-wiki.git
# or:
cd /path/to/your/vault
git init
git remote add origin https://github.com/you/my-wiki.git
```

**Hourly auto-sync via cron:**

```
0 * * * * obsidian-wiki sync --vault /path/to/your/vault >> ~/.obsidian-wiki/sync.log 2>&1
```

> Keep the repo **private** if your vault contains personal notes. Nothing is sent to any third-party service — your vault lives on your machines and in your GitHub account only.

## Visibility tags (optional)

Pages can carry a `visibility/` tag marking their intended reach. This is **entirely optional** — untagged pages behave exactly as they always have. The system stays single-vault, single source of truth.

| Tag | Meaning |
|---|---|
| *(none)* | Same as `visibility/public` — visible in all modes |
| `visibility/public` | Explicitly public |
| `visibility/internal` | Team-only — excluded in filtered mode |
| `visibility/pii` | Sensitive — excluded in filtered mode |

**Filtered mode** is opt-in, triggered by phrases like "public only", "user-facing answer", "no internal content", or "as a user would see it" in a query. Default mode shows everything.

`visibility/` tags are **system tags** — they don't count toward the 5-tag limit and are listed separately from domain/type tags in the taxonomy.

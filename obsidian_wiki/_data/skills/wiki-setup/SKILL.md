---
name: wiki-setup
description: >
  Initialize a new Obsidian wiki vault with the correct structure, special files, and configuration.
  Use this skill when the user wants to set up a new wiki from scratch, initialize the vault structure,
  create the .env file, or says things like "set up my wiki", "initialize obsidian", "create a new vault",
  "get started with the wiki". Also use when the user needs to reconfigure their existing vault or
  fix a broken setup.
---

# Obsidian Setup — Vault Initialization

You are setting up a new Obsidian wiki vault (or repairing an existing one).

## Choose the setup mode

Use the canonical Config Resolution Protocol in `llm-wiki/SKILL.md` when
repairing an existing setup: explicit `@name`, nearest ancestor
`.obsidian-wiki/config.toml`, then `.env`, personal global config, and setup
guidance. Do not replace a discovered portable repository with personal
configuration.

### Portable Repository mode

For a Git-native, clone-ready, multi-contributor knowledge repository, use the
installed CLI rather than hand-writing `.env`:

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
```

Portable setup writes repository-relative `.obsidian-wiki/config.toml`, the
vault, canonical tracked skills, regular Markdown agent adapters, and bootstrap
files inside the target repository. It **does not write `~/.obsidian-wiki/config`**
or global agent skill directories. Do not add a
repository `.venv`, vendor the CLI, or commit an absolute
`OBSIDIAN_WIKI_REPO`; each contributor installs the CLI separately from a
framework clone:

```bash
uv tool install --link-mode copy .
```

Portable setup accepts a missing target, an empty target, or a target containing
only an ordinary `.git` directory. It preserves an existing `.git` directory
and rejects arbitrary non-portable content; legacy layouts need explicit migration
with `obsidian-wiki repo migrate`.

Setup does not run `git init`, commit, or configure a remote. For a new
repository, run setup first, then `git init`; a Git-only target remains
compatible and keeps its existing Git metadata.

#### Migrate an existing co-located repository

Migration is explicit and dry-run first; never run portable setup over a legacy
vault. The repository root, legacy vault, and single source root must already
exist in one repository, with the vault and source tree separate. Every
manifest source and every page-frontmatter source must map to an ordinary file
below that source root. First run:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources
```

The analyzer changes nothing. Resolve every reported blocker, then run the
exact apply command printed by the dry-run. Before `--apply`, require an
enclosing Git worktree whose top level equals `--root`, commit an intentional
legacy baseline, and confirm the worktree is clean; this is the supported
post-success rollback point:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources --apply
obsidian-wiki check
git diff
```

Apply converts manifest v1 to repository-relative manifest v2 shards, rewrites
page source frontmatter, replaces `index.md` and `log.md` with stable portable
views, removes the existing legacy `hot.md`, installs repository-local skills and portable
Git rules, and writes one migration operation page. It never copies external
sources, initializes Git, commits, or pushes. On a write failure it attempts to
restore preimages byte-for-byte. If rollback is incomplete, stop: the command
reports that state and retains its snapshots for manual diagnosis. After
success it reports the retained local recovery directory below
`.obsidian-wiki/local/migrations/`; this is internal recovery/audit data, not a
supported restore interface. Review the ordinary Git diff before a human
commits on a branch.

Live URLs, pseudo-sources, external or missing files, unsafe paths, invalid
manifest/frontmatter, unmapped page sources, and conflicting Source ID mappings
are blockers. Before migration, an operator may deliberately capture required
external material as small, reviewable Markdown or text snapshots below
`sources`; the migration command itself never moves or copies it. The analyzer
checks ordinary files, not Git-index membership or LFS signatures. Confirm the
sources are tracked before publishing. Git LFS pointer files are not source
content and must not be compiled. See the migration reference in
`llm-wiki/SKILL.md` and the human CLI documentation for the complete blocker
table.

Portable validation recognizes the Git worktree surrounding `wiki/`; it never
creates `wiki/.git`. `obsidian-wiki sync` and `sync-setup` refuse when portable
config is resolved. Remove any old Personal-mode cron or alias, do not bypass
portable resolution with an explicit `--vault`, and publish only with the
repository's human-controlled Git workflow.

### Personal mode

For one person's machine-wide vault and globally discoverable agent skills,
continue with the `.env`/global-config workflow below or run
`obsidian-wiki setup --vault /absolute/path/to/vault`.

The numbered setup steps and optional GitHub-sync and QMD sections below
are Personal-mode instructions unless they explicitly say otherwise.

## Step 1: Create .env

If `.env` doesn't exist, create it from `.env.example`. Ask the user for:

1. **Where should the vault live?** → `OBSIDIAN_VAULT_PATH`
   - Default: `~/Documents/obsidian-wiki-vault`
   - Must be an absolute path (after expansion)

2. **Where are your source documents?** → `OBSIDIAN_SOURCES_DIR`
   - Can be multiple paths, comma-separated
   - Default: `~/Documents`
   - Local git repo clones (public or private, any host) can be listed here too — clone
     the repo locally first, then add its path. See "Ingesting Git Repositories" in
     `wiki-ingest/SKILL.md` for how repo sources are handled.

3. **Want to import Claude history?** → `CLAUDE_HISTORY_PATH`
   - Default: auto-discovers from `~/.claude`
   - Set explicitly if Claude data is elsewhere

4. **Have QMD installed?** → `QMD_WIKI_COLLECTION` / `QMD_PAPERS_COLLECTION` / `QMD_TRANSPORT`
   - Optional. Enables semantic search in `wiki-query` and source discovery in `wiki-ingest`.
   - Default to `QMD_TRANSPORT=mcp` unless the user wants the agent to call the local `qmd` CLI directly.
   - If using CLI mode, set `QMD_CLI_SEARCH_MODE=quality` by default; suggest `balanced` if reranking is too slow.
   - If unsure, skip for now — both skills fall back to `Grep` automatically.
   - Install instructions: see `.env.example` (QMD section).
   - **If `QMD_WIKI_COLLECTION` is set, verify the collection excludes `_raw/`.** The wiki
     collection and papers collection must stay disjoint — `wiki-query` cites them as
     separate layers (compiled knowledge vs. raw staging), and `OBSIDIAN_VAULT_PATH` contains
     `_raw/`, so a plain `qmd collection add <vault>` silently merges the two.
     Read `~/.config/qmd/index.yml`, find the entry for `$QMD_WIKI_COLLECTION`, and check its
     `ignore` list includes `_raw/**` (and ideally `log.md`, which has no semantic value). If
     the collection doesn't exist yet, create it (`qmd collection add "$OBSIDIAN_VAULT_PATH"
     --name <collection-name>`), then add the `ignore` block to `index.yml` by hand — `qmd`
     has no `--ignore` flag and refuses a second `collection add` on a path that already has
     one, so editing the YAML is the only way to scope it. Run `qmd update` after editing.
     If the collection already exists without the `ignore` block, tell the user their
     wiki collection is indexing `_raw/` (including `_raw/_archived/` drafts left behind by
     `wiki-ingest`) and offer to add the `ignore` block and re-run `qmd update`.

5. **Token budget warning threshold?** → `WIKI_TOKEN_WARN_THRESHOLD`
   - Default: `100000` (warn when full-wiki read would cost > 100K tokens)
   - Set to `0` to disable the warning entirely
   - `wiki-status` shows a token footprint table and emits this warning automatically

6. **Enable staged writes?** → `WIKI_STAGED_WRITES`
   - Default: unset / `false` (pages written directly to their final location)
   - Set to `true` for team wikis, high-stakes domains, or any vault where the human wants final say on every LLM-written page
   - When enabled: all new/updated pages land in `_staging/` first; run `/wiki-stage-commit` to review and promote them
   - `wiki-status` shows a "Staged writes pending" count when files are waiting

## Step 2: Create Vault Directory Structure

```bash
mkdir -p "$OBSIDIAN_VAULT_PATH"/{concepts,entities,skills,references,synthesis,journal,projects,_archives,_raw,_staging,.obsidian}
```

- `.obsidian/` — Obsidian's own config. Creates vault recognition.
- `projects/` — Per-project knowledge (populated during ingest).
- `_archives/` — Stores wiki snapshots for rebuild/restore operations.
- `_raw/` — Staging area for unprocessed drafts. Drop rough notes here; `wiki-ingest` will promote them to proper wiki pages and move the originals into `_raw/_archived/` (created on first use).
- `_staging/` — Review queue for LLM-written pages when `WIKI_STAGED_WRITES=true`. Pages here are not visible in Obsidian's graph until promoted via `/wiki-stage-commit`.

## Step 3: Create Special Files

### index.md

```markdown
---
title: Wiki Index
---

# Wiki Index

*This index is automatically maintained. Last updated: TIMESTAMP*

## Concepts

*No pages yet. Use `wiki-ingest` to add your first source.*

## Entities

## Skills

## References

## Synthesis

## Journal
```

### log.md

```markdown
---
title: Wiki Log
---

# Wiki Log

- [TIMESTAMP] INIT vault_path="OBSIDIAN_VAULT_PATH" categories=concepts,entities,skills,references,synthesis,journal
```

### hot.md

```markdown
---
title: Hot Cache
updated: TIMESTAMP
---

# Hot Cache

*A ~500-word semantic snapshot of recent activity. Updated after every major write operation.*

## Recent Activity

- [TIMESTAMP] INIT — vault created at OBSIDIAN_VAULT_PATH

## Active Threads

*None yet — start ingesting sources to populate.*

## Key Takeaways

*None yet.*

## Flagged Contradictions

*None yet.*
```

### .manifest.json

Create an empty manifest so ingest skills have a tracking file to append to and
`obsidian-wiki doctor` reports the vault as complete (it treats `.manifest.json`
as a required core file):

```bash
printf '{}\n' > "$OBSIDIAN_VAULT_PATH/.manifest.json"
```

## Step 4: Create .obsidian Configuration

Create minimal Obsidian config for a good out-of-box experience:

### .obsidian/app.json
```json
{
  "strictLineBreaks": false,
  "showFrontmatter": false,
  "defaultViewMode": "preview",
  "livePreview": true
}
```

### .obsidian/appearance.json
```json
{
  "baseFontSize": 16
}
```

## Step 5: Recommend Obsidian Plugins

Tell the user about these recommended community plugins (they install manually):

1. **Dataview** — Query page metadata, create dynamic tables. Essential for a wiki.
2. **Graph Analysis** — Enhanced graph view for exploring connections.
3. **Templater** — If they want to create pages manually using templates.
4. **Obsidian Git** — Auto-backup the vault to a git repo.

## Step 6: Verify Setup

Run a quick sanity check:
- [ ] Vault directory exists with: `concepts/`, `entities/`, `skills/`, `references/`, `synthesis/`, `journal/`, `projects/`, `_archives/`, `_raw/`
- [ ] `index.md` exists at vault root
- [ ] `log.md` exists at vault root
- [ ] `hot.md` exists at vault root
- [ ] `.manifest.json` exists at vault root (empty `{}` is fine)
- [ ] `.env` has `OBSIDIAN_VAULT_PATH` set
- [ ] `.obsidian/` directory exists
- [ ] `_staging/` directory exists (required even when `WIKI_STAGED_WRITES` is not set — created on setup for future use)
- [ ] Source directories (if configured) exist and are readable

Report the results and tell the user they can now:
1. Open the vault in Obsidian (File → Open Vault → select the directory)
2. Run `wiki-status` to see what's available to ingest
3. Run `wiki-ingest` to add their first sources
4. Run `claude-history-ingest` to mine their Claude conversations
5. Run `codex-history-ingest` to mine their Codex sessions (if they use Codex)
6. Run `wiki-status` again anytime to check the delta

## Optional: Configure GitHub Sync

Ask the user: **"Want to sync your vault to a private GitHub repo?"**

The vault is plain markdown, so pushing it to git gets you version history, backup, and
cross-device sync for free. This is opt-in — skip it if the user declines or has no repo ready.

If yes:

1. Ask for the repo URL (e.g. `https://github.com/you/my-wiki.git`). Recommend it be **private**
   if the vault holds personal notes.
2. Verify that `command -v obsidian-wiki` succeeds, then run the installed CLI,
   which handles `git init`, a default `.gitignore`, and wiring the `origin` remote:
   ```bash
   obsidian-wiki sync-setup "<repo-url>" --vault "$OBSIDIAN_VAULT_PATH"
   ```
   If the installed executable is unavailable, stop and direct the user to the
   supported clone-and-`uv tool install --link-mode copy .` flow. Do not execute source from a
   checkout as a fallback.
3. Tell the user they can run `obsidian-wiki sync` any time afterward to commit and push pending
   vault changes (stages everything, commits with a timestamp, pushes). There's no config file to
   check for sync status — the vault's own `git remote` is the source of truth.

## Optional: Refresh QMD After Setup

If `QMD_WIKI_COLLECTION` is configured and the local QMD CLI is available, run `qmd update` after the initial vault files exist so the fresh vault is immediately queryable. No embedding pass is usually needed at setup time because the vault starts empty, so a plain update is enough unless you have already populated pages. Before running it, confirm the `_raw/` exclusion described in Step 1.4 is in place — otherwise this update indexes the (currently empty) staging directory into the wiki collection too, and every future draft dropped there joins it silently.

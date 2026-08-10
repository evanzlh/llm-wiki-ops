# Architecture

The wiki is the artifact. The agent is the maintainer. Obsidian is the viewer.

No scripts run the semantic knowledge pipeline — the skills are Markdown files
that tell an AI agent *how* to operate on the vault. The installed CLI handles
deterministic setup, config resolution, validation, and transactional
repository maintenance.

## The four stages

Every time you feed the brain, it runs through these:

### 1. Ingest

The agent reads your source material directly — markdown, PDFs (with page ranges), JSONL conversation exports, plain text logs, chat exports, meeting transcripts, and images (screenshots, whiteboard photos, diagrams; vision-capable model required). No preprocessing step, no pipeline to run. The agent reads the file the same way it reads code.

### 2. Pull information

From the raw source, the agent pulls out concepts, entities, claims, relationships, and open questions. A conversation about debugging a React hook yields a "stale closure" pattern. A research paper yields the key idea and its caveats. A work log yields decisions and their rationale. Noise gets dropped, signal gets kept.

Each page also gets a 1–2 sentence `summary:` in its frontmatter at write time — later queries use this to preview pages without opening them.

### 3. Merge

New knowledge merges against what's already there. If a concept page exists, the agent updates it: merging new information, noting contradictions, strengthening cross-references. If it's genuinely new, a page gets created. Nothing is duplicated. Sources are tracked in frontmatter so every claim stays attributable.

### 4. Schema

The schema isn't fixed upfront. It emerges from your sources and evolves as you add more. The agent maintains coherence: categories stay consistent, wikilinks point to real pages, the index reflects what's actually there. When you add a new domain, the schema expands without breaking what exists.

A manifest tracks every source that's been ingested — identity, content hash,
and which pages it produced. Personal vaults use a monolithic v1 ledger;
portable repositories use a v2 marker plus source shards. On the next run, the
agent computes the delta and only processes what's new or changed.

## The loop

1. Agent resolves the vault path (`@name` → nearest
   `.obsidian-wiki/config.toml` → nearest matching `.env` → global config)
2. Agent reads the mode-appropriate manifest state to know what's already been done
3. Agent reads the relevant skill for instructions
4. Agent uses its built-in tools to do the work
5. Personal mode updates the monolithic manifest, `index.md`, `log.md`, and
   `hot.md`; Portable Repository mode promotes candidate pages through a local
   transaction, updates only affected manifest shards, and records one
   immutable operation page
6. Output is standard Obsidian-compatible markdown with frontmatter and `[[wikilinks]]`

## Portable repository layer

Portable Repository mode keeps sources, compiled vault, canonical skills, and
agent discovery in one Git repository:

```text
knowledge-repo/
├── .obsidian-wiki/config.toml
├── .obsidian-wiki/managed-skills.json
├── .gitattributes
├── sources/
├── wiki/
├── .skills/
├── AGENTS.md
└── .claude/skills/, .cursor/skills/, ...
```

TOML paths resolve from the repository root. Canonical skills are tracked once
under `.skills/`; per-agent adapters are regular Markdown files with
repository-relative references, not symlinks. The knowledge repository does
not contain `.venv` or a CLI runtime—each contributor installs the CLI from a
framework clone with `uv tool install --link-mode copy .`. `obsidian-wiki repo upgrade-skills`
refreshes only inventory-owned framework content and preserves owner files.
Linux and macOS are the first-release CLI support boundary, but no OS-specific
absolute path is committed.

CLI compatibility and tracked framework assets use this two-step portable CLI upgrade protocol.
On a branch, a maintainer first changes the tracked `requires_cli` setting to a
reviewed PEP 440 constraint that accepts the installed version; only then does
the maintainer refresh managed assets, validate the repository, and review the
result:

```bash
git switch -c upgrade-portable-cli
# Edit .obsidian-wiki/config.toml so requires_cli accepts the installed version.
obsidian-wiki repo upgrade-skills
obsidian-wiki check
git diff
```

Every collaborator must install a CLI version that satisfies the updated
repository contract. `repo upgrade-skills` does not bypass compatibility checks
and does not automatically rewrite `requires_cli`, so the version-contract
change and asset refresh remain distinct Git changes. Validate and review both,
then commit them together through the repository workflow.

The tracked `.gitattributes` managed block disables clone-specific text
conversion while retaining text diff/merge behavior for common knowledge and
configuration formats. Consequently a contributor's `core.autocrlf` setting
does not change authoritative source bytes or manifest hashes after clone.

### Authoritative, collaborative, and local state

Portable repositories use this tracked/ignored boundary:

| State | Git | Repository paths | Rule |
|---|---|---|---|
| Authoritative sources | Tracked | configured `sources/**` | The only durable ingest inputs and the origin of repository-relative Source IDs. |
| Compiled knowledge | Tracked | `<vault>/{concepts,entities,skills,references,synthesis,projects}/**`, plus `<vault>/journal/**` except `<vault>/journal/operations/**` | Agents promote these pages through transactions; humans review their Git diff. |
| Compilation ledger | Tracked | `<vault>/.manifest.json`, `<vault>/.manifest/sources/**` | The marker is fixed and the CLI owns affected shards. |
| Operation history | Tracked | `<vault>/journal/operations/YYYY/MM/<UTC>-<suffix>.md` | One immutable, merge-friendly page per completed transaction. |
| Stable query surfaces | Tracked | `<vault>/index.md`, `<vault>/log.md` | Portable setup creates them, but ordinary transactions never rewrite them. Built-in queries scan pages, shards, and operation entries instead. |
| Repository contract | Tracked | `.obsidian-wiki/config.toml`, `.obsidian-wiki/managed-skills.json`, `.gitattributes`, `.skills/**`, agent bootstrap/adapters | Clone-independent configuration, byte-stability rules, and agent behavior. |
| Semantic hot cache | Ignored | `<vault>/hot.md` | Local derived context; invalidate and rebuild it after authoritative state or branch changes. |
| Transaction and recovery state | Ignored | `.obsidian-wiki/local/**` | Lock, candidate pages, preimages, snapshots, metadata, and hot fingerprint; never publish it. |
| Obsidian UI state | Ignored | `<vault>/.obsidian/workspace.json`, `<vault>/.obsidian/workspace-mobile.json`, `<vault>/.trash/**` | Machine-local viewer state, not knowledge. |

### Portable write lifecycle

An agent begins one transaction with one or more actual authoritative source
paths. The CLI resolves them to Source IDs, acquires the repository write lock,
records preimages, and returns a local `candidate_vault`. The agent writes only
final vault-relative knowledge paths there and declares removals separately.

On commit, the CLI validates candidate paths, required frontmatter, and
transaction-scoped `sources`; verifies that affected live output targets still
match their begin-time preimages; snapshots those targets; promotes pages and
deletions; rebuilds affected manifest relationships; and writes the operation
page last. A dirty source worktree is normal and allowed. Unrelated edits are
also allowed: only preimage drift in an affected output page or manifest shard
blocks promotion.

Completed operations use this collision-resistant path:

```text
<vault>/journal/operations/YYYY/MM/YYYYMMDDTHHMMSSZ-<random-hex>.md
```

The operation frontmatter records the transaction ID, completion time, and
Source IDs; its body lists created, updated, and removed pages. Operation pages
are immutable and are not supplied by the agent.

Transactions are retained for deliberate recovery. `retry` re-runs a failed
promotion only while targets still match its preimages. An interrupted process
may leave status `promoting`; its only recovery action is `restore`. `restore`
replays the recorded original/absent state and, for a completed transaction,
first requires all affected files to match its postimages so it cannot
overwrite later work. `abort` abandons active or failed staged work; `discard`
removes retained failed/completed/restored recovery state only after the
outcome is understood. None of these operations invokes Git.

`hot.md` has a separate local freshness protocol. Its sidecar fingerprints
knowledge pages, manifest state, operation entries, and the current Git branch
or detached HEAD. `obsidian-wiki hot status --json` removes only stale
`hot.md`; an agent may then rebuild the semantic snapshot from current pages
and recent operations and finish with `obsidian-wiki hot mark-current --json`.

The transaction CLI changes the working tree but never commits or pushes
portable changes. Git diff and pull-request review are the content boundary;
branch publication and merging are explicit human actions.

### Legacy migration lifecycle

Legacy-to-portable conversion is not a setup side effect. A read-only analyzer
first proves that the vault and one non-overlapping source root are contained
in the target repository and that every legacy provenance edge maps to an
ordinary source file. Blockers stop the operation before any mutation.

Apply then builds all candidates in ignored local migration state, rechecks
the analyzed preimages, and promotes portable config, skills, bootstrap files,
byte-stability attributes, rewritten page frontmatter, manifest v2 shards, and
one immutable migration operation. The existing legacy `hot.md` is removed;
`index.md` and `log.md` become stable built-in-query surfaces. A mid-apply
failure attempts to restore all original files byte-for-byte and remove newly
created files. An incomplete rollback is reported and retains its manifest and
snapshots for manual diagnosis. Successful recovery data remains below
`.obsidian-wiki/local/migrations/` until the operator has accepted and
committed the ordinary Git diff; it is internal audit/recovery state, not a
supported post-success restore interface.

Git discovery begins at the vault and recognizes the enclosing worktree. Its
top level must equal the portable root; portable tooling never initializes
`<vault>/.git`. Migration, transactions, validation, and hot-state maintenance
never stage, commit, push, or call a hosting-provider API. `sync` and
`sync-setup` fail when portable config is resolved, leaving publication to a
human-controlled branch and pull request.

Migration analysis can run before Git initialization, and validation warns
rather than mutates when no worktree exists. Apply has a stricter operator
precondition: the enclosing top level must equal the portable root and a clean,
complete legacy baseline must already be committed. That baseline is the
supported post-success rollback point. Git membership is not a CLI blocker, so
the operator must verify it before apply.

## Manifest protocols

### Personal mode: manifest v1

Personal vaults retain the monolithic `.manifest.json`. Sources outside the
vault use expanded absolute keys; existing vault-relative keys remain valid for
sources inside the vault. This preserves the established personal workflow and
its maintenance tools.

### Portable Repository mode: manifest v2

Portable repositories commit this fixed marker at `<vault>/.manifest.json`.
Although `[paths].sources` uses list syntax, manifest v2 schema 1 requires
exactly one configured source root; multiple entries fail validation.

```json
{
  "schema_version": 2,
  "storage": "sharded",
  "entries": ".manifest/sources"
}
```

Each authoritative source has one entry below `<vault>/.manifest/sources/`. For
example, `sources/design/portable.md` maps to
`<vault>/.manifest/sources/design/portable.md.json`:

```json
{
  "source_id": "sources/design/portable.md",
  "content_hash": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "pages": ["concepts/portable-repository.md"],
  "compiled_at": "2026-08-07T07:30:00Z"
}
```

The repository-relative Source ID is clone-independent. It must use forward
slashes (`/`), be normalized without `.` or `..`, and name an ordinary file
below a configured `sources` root. A live URL or external filesystem path is
not a durable Source ID. Capture external material as a small, reviewable
snapshot below `sources` using ordinary Git storage. Git LFS pointers are
unsupported because a pointer is not authoritative content.

Markdown and plain text are the primary collaboration formats; small
reviewable JSON, YAML, or TOML snapshots are also suitable when their bytes are
committed directly. Binary PDFs/images remain Personal-mode inputs unless
converted to reviewable text. Migration and validation do not recognize a
dedicated untracked-source or LFS-pointer blocker, so the operator must inspect
Git status and source bytes. Large opaque binaries and Git LFS pointers are not
compiled as authoritative source contents.

Agents use `obsidian-wiki cache-check` and `cache-update`; they do not hand-edit
the marker or shards. The v2 schema deliberately omits model, agent, API, and
generation-tool provenance: it records authoritative source compilation, not
which tool happened to perform it.

Portable validation gives source drift stable issue names:

The source scan ignores `.gitkeep` and hidden source path components (any
relative component beginning with `.`). They are not authoritative tracked
sources, so they do not produce shards or `source-new` errors.

- `source-new`: a source exists without a shard.
- `source-stale`: a source hash differs from its shard.
- `source-orphaned`: a shard names a source that no longer exists.

All three are errors and PR blockers. Run `obsidian-wiki check` locally and in CI
before merging.

For `source-orphaned`, restore a mistakenly deleted source. If deletion was
intentional, remove the entire corresponding shard file with
`git rm <vault>/.manifest/sources/<relative>.json`. This is whole-file Git
deletion, never editing marker or shard JSON fields.

## Vault structure

```
$OBSIDIAN_VAULT_PATH/
├── index.md                # Personal: maintained index; Portable: stable query surface
├── log.md                  # Personal: activity log; Portable: stable query surface
├── hot.md                  # Personal: maintained cache; Portable: ignored local cache
├── .manifest.json          # Personal v1 ledger or portable v2 marker
├── _meta/
│   ├── taxonomy.md         # Controlled tag vocabulary
│   └── *.base              # Obsidian Bases dashboard definitions
├── _insights.md            # Personal-only graph analysis output
├── _raw/                   # Personal-only capture staging; reserved in Portable mode
├── _staging/               # Personal-only review queue when WIKI_STAGED_WRITES=true
├── _archives/              # Personal-only timestamped rebuild/restore snapshots
├── _readouts/              # Personal-only saved wiki-narrate readouts
├── concepts/               # Abstract ideas, patterns, mental models
├── entities/               # Concrete things — people, tools, libraries, companies
├── skills/                 # How-to knowledge, techniques, procedures
├── references/             # Factual lookups — specs, APIs, configs
├── synthesis/              # Cross-cutting analysis connecting multiple concepts
├── journal/                # Time-bound entries — daily logs, session notes
└── projects/
    └── <project-name>.md   # One page per project, synced via wiki-update
```

Knowledge that's project-specific goes under `projects/`. Knowledge that's general goes in the global category directories. Both are cross-referenced with `[[wikilinks]]`.

Every page carries required frontmatter: `title`, `category`, `tags`, `sources`, `created`, `updated`.

Portable validation deliberately accepts a restricted frontmatter grammar:
top-level scalar values, scalar lists, the framework `provenance` mapping
(`extracted`, `inferred`, `ambiguous`), and the framework `relationships` list
of mappings (`target`, `type`). These are the only supported nested shapes.
Arbitrary nested YAML, flow mappings, tags, anchors, and aliases fail closed;
the CLI does not load general YAML objects from knowledge pages.

In Personal mode, write skills maintain `hot.md` as before. In Portable
Repository mode it is ignored, freshness-checked local derived state; tracked
operation entries and page summaries are the durable replacement for its
collaboration role.

## Core principles

- **Compile, don't retrieve.** The wiki is pre-compiled knowledge. Update existing pages — don't append or duplicate.
- **Track durable state.** Personal mode maintains its central files. Portable
  mode tracks sources, knowledge pages, manifest shards, and immutable
  operations while keeping transactions and `hot.md` local.
- **Connect with `[[wikilinks]]`.** This is what makes it a knowledge graph rather than a folder of files.
- **Frontmatter is required.** Every page, every time.
- **Single source of truth.** Visibility tags shape how content surfaces — they never duplicate or separate it.

## What we added on top of Karpathy's pattern

The [original gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) is the seed: compile knowledge once into interconnected markdown and keep it current, instead of asking an LLM the same questions repeatedly or running RAG every time. Here's what got built around it.

- **Delta tracking.** A manifest tracks every source file ingested. Come back later and it computes the delta, processing only what's new or changed. You're not re-ingesting your document library every time.

- **Project-based organization.** Knowledge is filed under projects when project-specific, globally when not. Both cross-referenced. Ten codebases, ten spaces in the vault.

- **Archive and rebuild.** Personal mode can archive the whole vault, rebuild,
  or restore a previous `_archives/` snapshot. Portable mode does not write
  `_archives/`; it rebuilds representable knowledge through transaction-backed
  candidate replacements and declared deletions, retaining local recovery
  snapshots until the transaction is discarded.

- **Multi-agent ingest.** Documents, PDFs, Claude Code history, Codex sessions, Hermes memories, OpenClaw `MEMORY.md`, Pi sessions, Copilot CLI history, Windsurf data, ChatGPT exports, Slack logs, meeting transcripts, raw text. Dedicated skills for each agent, plus a catch-all for arbitrary exports.

- **Cross-agent targeted search.** `/wiki-codex "rust ownership"` from inside Claude Code finds your Codex sessions on that topic, extracts the relevant blobs, distills them into pages, and returns a synthesized answer. Topic-first, not session-first. Each agent has its own extraction strategy. Pair with `/memory-bridge diff` to see what each tool uniquely contributed.

- **Audit and lint.** Orphaned pages, broken wikilinks, stale content, contradictions, missing frontmatter — plus a dashboard of what's ingested vs. pending.

- **Identity resolution.** `wiki-dedup` finds pages covering the same concept under different names ("RSC" vs. "React Server Components") and merges them.

- **Automated cross-linking.** After ingest, the cross-linker scans for unlinked mentions and weaves them into the graph.

- **Tag taxonomy.** A controlled vocabulary in `_meta/taxonomy.md`, with a skill that audits and normalizes tags vault-wide.

- **Provenance tracking.** Every claim is tagged: extracted (default), `^[inferred]` (LLM synthesis), or `^[ambiguous]` (sources disagree). A `provenance:` block in frontmatter summarizes the mix per page, and `wiki-lint` flags pages drifting into mostly speculation. You can always tell what your wiki knows from what it guessed.

- **Trust ledger.** `obsidian-wiki trust-record` / `trust-check` record and validate human-approved confidence reviews against material fingerprints, so CI can gate on "a person actually checked this."

- **Multimodal sources.** Screenshots, whiteboard photos, slide captures, and diagrams ingest like text — visible text transcribed verbatim, interpreted content tagged as inferred.

- **Wiki insights.** `wiki-status` can analyze the shape of the vault itself: top hubs, bridge pages (nodes whose removal would partition the graph), tag cluster cohesion, scored surprising connections, a graph delta since last run, and questions the structure is uniquely positioned to answer. Personal mode writes `_insights.md`; Portable mode promotes `synthesis/wiki-insights.md` through a transaction.

- **Graph export and import.** `wiki-export` turns the wikilink graph into `graph.json`, `graph.graphml` (Gephi/yEd), `cypher.txt` (Neo4j), a self-contained interactive `graph.html`, or an OKF bundle. `wiki-import` reads any of it back.

- **Tiered retrieval.** `wiki-query` reads titles, tags, and summaries first, opening page bodies only when the cheap pass can't answer. Say "quick answer" to force index-only mode. Query cost stays roughly flat from 20 pages to 2000.

- **Session brain.** A topic graph over your raw agent session history, so you can find the session where something happened. See [Session Brain](session-brain.md).

- **Staged writes.** In Personal mode, set `WIKI_STAGED_WRITES=true` and LLM-written pages queue in `_staging/` for review. Portable mode uses ignored transaction candidates instead.

## Open Knowledge Format

The vault format is structurally conformant with [OKF v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) — markdown with YAML frontmatter, category subfolders, reserved `index.md`/`log.md`.

`wiki-export` (OKF mode) and `wiki-import` are the bridge: they translate between native frontmatter (`title`/`category`/`tags`/`sources`/`created`/`updated` + `summary`) and OKF (`type`/`title`/`description`/`resource`/`tags`/`timestamp`), making vaults exchangeable with any OKF tool.

The OKF round-trip is lossless. The `graph.json` round-trip is not — it carries structure, not page bodies.

## Repo layout

The source-built wheel is the bundled-data carrier for installed operation. It contains the canonical skills, bootstrap files, and hook assets, so the CLI built by `uv tool install --link-mode copy .` does not execute the source checkout or require the clone to remain in place.

```
obsidian-wiki/
├── .skills/                             # ← Canonical skill definitions (source of truth)
│   └── <skill-name>/SKILL.md            #   39 skills — see docs/skills.md
│
├── obsidian_wiki/                       # Python package — CLI, setup, sync, session brain
├── extensions/brain-capture/            # Zero-build Chrome capture extension
├── tools/check_readme_sync.py           # Translation drift reporter
│
├── CLAUDE.md                            # Bootstrap → Claude Code / Kilocode (→ AGENTS.md)
├── GEMINI.md                            # Bootstrap → Gemini CLI (→ AGENTS.md)
├── AGENTS.md                            # Bootstrap → Codex, OpenCode, Aider, Droid, Trae, Hermes, OpenClaw
├── .hermes.md                           # Bootstrap → Hermes (symlink → AGENTS.md)
├── .cursor/rules/obsidian-wiki.mdc      # Always-on → Cursor (alwaysApply: true)
├── .windsurf/rules/obsidian-wiki.md     # Always-on → Windsurf
├── .kiro/steering/obsidian-wiki.md      # Always-on → Kiro (inclusion: always)
├── .agent/rules/obsidian-wiki.md        # Always-on → Google Antigravity
├── .agent/workflows/obsidian-wiki.md    # Slash-command registry → Antigravity
├── .github/copilot-instructions.md      # Always-on → GitHub Copilot (VS Code Chat)
│
├── .claude/skills/   → symlinks to .skills/*   (created by setup)
├── .cursor/skills/   → symlinks to .skills/*
├── .windsurf/skills/ → symlinks to .skills/*
├── .agents/skills/   → symlinks to .skills/*
├── .pi/skills/       → symlinks to .skills/*
├── .kiro/skills/     → symlinks to .skills/*
│
├── .env.example                         # Configuration template
└── docs/                                # You are here
```

The two supported setup modes are described in [Installation](installation.md): personal mode connects bundled skills to agent-wide discovery paths, while Portable Repository mode writes tracked repository-local integrations.

For the full pattern — three-layer architecture, page templates, project organization — read [`.skills/llm-wiki/SKILL.md`](../.skills/llm-wiki/SKILL.md).

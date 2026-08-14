# Architecture

LLMWikiOps treats the knowledge base as a reproducible repository artifact. Python owns deterministic setup, validation, containment, transactions, and maintenance; tracked skills tell agents how to interpret sources and compose knowledge.

## One repository layout

```text
team-knowledge/
├── .obsidian-wiki/
│   ├── config.toml
│   └── local/                 # ignored transactions and recovery state
├── .skills/                   # canonical tracked skills
├── .claude/skills/            # complete derived mirrors
├── .cursor/skills/
├── .windsurf/skills/
├── .agents/skills/
├── .pi/skills/
├── .kiro/skills/
├── sources/                   # exactly one configured source root
└── wiki/
    ├── .manifest.json         # tracked manifest v2 marker
    ├── .manifest/sources/     # tracked manifest v2 shards
    ├── concepts/ entities/ skills/ references/ synthesis/ journal/ projects/
    ├── index.md          # tracked control view
    ├── log.md            # tracked authoritative operation log
    └── hot.md            # tracked derived semantic view
```

Bootstrap instructions at the repository root direct supported agents to the canonical skill tree. Mirrors contain ordinary files so clones do not depend on links or machine-global installation.

## Authority and data flow

The nearest ancestor configuration selects the repository. The repository-root `sources/` directory is the tracked authority: owners review and track source snapshots before beginning a transaction. The agent reads the canonical protocol and a task skill, then opens a transaction for the exact sources. Candidate pages are composed in ignored local transaction workspaces, not in the live vault.

Stable repository-relative Source IDs identify inputs across clones. The Source ID retains the repository-relative configured source-root prefix. When mapping it to a shard, `ShardedManifest.entry_path` first removes that prefix, then appends `.json` below `wiki/.manifest/sources/`. For example, `sources/design/architecture.md` maps to `wiki/.manifest/sources/design/architecture.md.json`. This keeps concurrent updates merge-friendly.

## Transaction review and recovery

The write lifecycle is:

1. `transaction begin` records source identities, time, candidate workspace, snapshots, and deletions.
2. The agent writes complete candidate pages only inside the returned workspace.
3. `transaction validate` checks the prospective vault, including candidate-to-candidate and candidate-to-live links.
4. Human or agent transaction review inspects the candidate diff and validation report.
5. `transaction commit` revalidates, creates recovery material, promotes reviewed candidate pages, upserts affected manifest shards, and appends one canonical operation block to `wiki/log.md` last. Its JSON result returns `log_path`. It never changes tracked source snapshots.
6. The owner reviews `git diff` and handles Git publication outside the CLI.

Failed promotion retains status-aware recovery state. `transaction retry`, `restore`, `discard`, and `abort` make each next action explicit. Owner changes are never silently overwritten.

## Control, log, and derived views

`wiki/index.md` is the tracked control view. `wiki/log.md` is the tracked authoritative operation log, parsed as canonical blocks rather than as graph pages. `wiki/hot.md` is a tracked derived semantic recent-activity view. `hot status` detects staleness without modifying or deleting the file; `hot inputs` supplies bounded deterministic input, and `hot mark-current` records the regenerated view's fingerprint. Owners resolve ordinary Git conflicts in both tracked views.

## Manifest v2

Manifest v2 is sharded and has exactly one configured source root. The tracked `wiki/.manifest.json` marker selects sharded storage at `wiki/.manifest/sources/`. A transaction commit owns every affected shard but never changes the authoritative source snapshot; never edit manifest shards directly. `check` compares tracked sources, shards, generated pages, skills, mirrors, and bootstrap assets without invoking a model.

## Boundaries

The supported architecture is this single repository workflow. The CLI does not publish Git changes. A Dashboard is outside the current package and has no stub; any future Dashboard is a separate product decision.

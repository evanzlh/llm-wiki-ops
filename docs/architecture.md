# Architecture

LLMWikiOps treats the knowledge base as a reproducible repository artifact. Python owns deterministic setup, validation, containment, transactions, and maintenance; tracked skills tell agents how to interpret sources and compose knowledge.

## One repository layout

```text
team-knowledge/
├── .llmwikiops/
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

Inside a wiki, repository-aware commands use nearest-ancestor CWD discovery. Outside a wiki, use an explicitly installed global adapter and mandatory `-C` / `--repo` on every repository-aware command.

## Optional external router

The global `llm-wiki-ops` Adapter is an optional global router, not a second repository mode. It contains generated built-in routing descriptions but no task bodies, contains no selected wiki path, and never installs the repository task skill tree globally. Each external request binds one exact root through the CLI; the runtime does not persist the explicit host path in tracked content or in the Adapter. Supplying that root trusts its tracked repository authority, subject to Git and owner review. After validation, the target repository's bootstrap, canonical protocol, skill metadata, and skill bodies remain authoritative.

External Adapter authority reads support only a user-controlled local, quiescent repository. The owner guarantees the stable environment: concurrent mutation, shared-writable repositories, network-sync activity, and network filesystems requiring concurrent consistency are unsupported. The CLI mechanically performs static validation of the selected root, configuration, version, and topology. This model does not claim adversarial TOCTOU protection between validation and later direct Agent reads; a detected or suspected change requires stopping and restarting from preflight.

Adapter installation uses a retention boundary instead of pathname deletion. A verified replaced tree or interrupted-installation artifact is atomically moved out of the active namespace into the target Agent configuration root's `.llmwikiops-retained/` directory and reverified there. This avoids claiming an inode-conditional delete guarantee that POSIX does not provide. The installer does not automatically unlink, recursively remove, or garbage-collect retained evidence, so repeated upgrades can consume disk space until an owner explicitly reviews and manually removes unneeded evidence.

## Authority and data flow

Without an explicit root, the nearest ancestor configuration selects the repository. With `-C` / `--repo`, the exact selected root must directly contain `.llmwikiops/config.toml`; the runtime does not perform ancestor fallback. The repository-root `sources/` directory is the tracked authority: owners review and track source snapshots before beginning a transaction. The agent reads the canonical protocol and a task skill, then opens a transaction for the exact sources. Candidate pages are composed in ignored local transaction workspaces, not in the live vault.

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

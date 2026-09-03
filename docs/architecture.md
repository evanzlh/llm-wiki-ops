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

The global `llm-wiki-ops` Adapter contains no built-in skill catalog or task
bodies. After exact-root resolution, `check --json` projects routing metadata
from the same Python-validated canonical `SkillCollection`; that repository
catalog is the only routing source for the external operation. The Adapter contains no selected wiki path and never installs the repository task skill tree globally. Each external request binds one exact root through the CLI; the runtime does not persist the explicit host path in tracked content or in the Adapter. Supplying that root trusts its tracked repository authority, subject to validated Git state and Agent review. After validation, the target repository's bootstrap, canonical protocol, skill metadata, and skill bodies remain authoritative.

External Adapter authority reads support only a user-controlled local, quiescent repository. The owner guarantees the stable environment: concurrent mutation, shared-writable repositories, network-sync activity, and network filesystems requiring concurrent consistency are unsupported. The CLI mechanically performs static validation of the selected root, configuration, version, and topology. This model does not claim adversarial TOCTOU protection between validation and later direct Agent reads; a detected or suspected change requires stopping and restarting from preflight.

Adapter installation uses a retention boundary instead of pathname deletion. A verified replaced tree or interrupted-installation artifact is atomically moved out of the active namespace into the target Agent configuration root's `.llmwikiops-retained/` directory and reverified there. This avoids claiming an inode-conditional delete guarantee that POSIX does not provide. The installer does not automatically unlink, recursively remove, or garbage-collect retained evidence, so repeated upgrades can consume disk space. An Agent may delete it only after user confirmation; no cleanup or uninstall CLI is added.

## Authority and data flow

Without an explicit root, the nearest ancestor configuration selects the repository. With `-C` / `--repo`, the exact selected root must directly contain `.llmwikiops/config.toml`; the runtime does not perform ancestor fallback. The repository-root `sources/` directory is tracked authority: the Agent reviews, validates, and locally commits exact Source paths before beginning a transaction. The Agent reads the canonical protocol and a task skill, then opens a transaction for those exact sources. Candidate pages are composed in ignored local transaction workspaces, not in the live vault.

Ordinary task-scoped work completes automatically: an Agent may inspect, update, validate, and locally commit exact task-owned paths. Failed safety conditions trigger validate and recover steps without bypass, continuing only while structured state shows progress. Ask before external publication, destructive or work-losing actions, owner-overlapping changes, authority-expanding actions, or semantic decisions. This task-scoped authority never permits unrelated mutation.

Stable repository-relative Source IDs identify inputs across clones. The Source ID retains the repository-relative configured source-root prefix. When mapping it to a shard, `ShardedManifest.entry_path` first removes that prefix, then appends `.json` below `wiki/.manifest/sources/`. For example, `sources/design/architecture.md` maps to `wiki/.manifest/sources/design/architecture.md.json`. This keeps concurrent updates merge-friendly.

## Transaction review and recovery

The write lifecycle is:

1. `transaction begin` records source identities, time, candidate workspace, snapshots, and deletions.
2. The agent writes complete candidate pages only inside the returned workspace.
3. `transaction validate` checks the prospective vault, including candidate-to-candidate and candidate-to-live links.
4. Human or agent transaction review inspects the candidate diff and validation report.
5. `transaction commit` revalidates and atomically owns the live mutation: it creates recovery material, promotes reviewed candidate pages, upserts affected manifest shards, and appends one canonical operation block to `wiki/log.md` last. Its JSON result returns `log_path`. It never changes tracked source snapshots.
6. The Agent runs the final checks, validates and stages exact task paths, inspects the staged diff, and makes the path-limited local commit.

Failed promotion retains status-aware recovery state. `transaction retry`, `restore`, `discard`, and `abort` make each next action explicit. The Agent reloads structured state after each safe action and continues only while it makes observable progress; an unchanged action and input are not repeated. Owner changes are never silently overwritten.

## Control, log, and derived views

`wiki/index.md` is the tracked control view. `wiki/log.md` is the tracked authoritative operation log, parsed as canonical blocks rather than as graph pages. `wiki/hot.md` is a tracked derived semantic recent-activity view. `hot status` detects staleness without modifying or deleting the file; `hot inputs` supplies bounded deterministic input, and `hot mark-current` records the regenerated view's fingerprint. Owner-overlapping Git conflicts in either tracked view require confirmation before resolution.

## Manifest v2

Manifest v2 is sharded and has exactly one configured source root. The tracked `wiki/.manifest.json` marker selects sharded storage at `wiki/.manifest/sources/`. A transaction commit owns every affected shard but never changes the authoritative source snapshot; never edit manifest shards directly. `check` compares tracked sources, shards, generated pages, skills, mirrors, and bootstrap assets without invoking a model.

## Boundaries

The supported architecture is this single repository workflow. A local commit is not Git publication; push, pull-request, remote, and history decisions remain outside the CLI and require confirmation. A Dashboard is outside the current package and has no stub; any future Dashboard is a separate product decision.

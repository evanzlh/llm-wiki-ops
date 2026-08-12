# Architecture

obsidian-wiki treats the knowledge base as a reproducible repository artifact. Python owns deterministic setup, validation, containment, transactions, and maintenance; tracked skills tell agents how to interpret sources and compose knowledge.

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
    ├── .manifest/sources/     # tracked manifest v2 shards
    ├── .ops/                  # immutable operation records
    ├── _sources/              # tracked source snapshots
    ├── index.md               # stable
    ├── log.md                 # stable
    └── hot.md                 # ignored derived view
```

Bootstrap instructions at the repository root direct supported agents to the canonical skill tree. Mirrors contain ordinary files so clones do not depend on links or machine-global installation.

## Authority and data flow

The nearest ancestor configuration selects the repository. Authoritative source bytes enter through `sources/`; the agent reads the canonical protocol and a task skill, then opens a transaction for the exact sources. Candidate pages are composed in ignored local transaction workspaces, not in the live vault.

Tracked source snapshots preserve the bytes used for compilation. Stable repository-relative Source IDs identify inputs across clones. Each source has one manifest v2 shard at `wiki/.manifest/sources/<source-id>.json`, which keeps concurrent updates merge-friendly.

## Transaction review and recovery

The write lifecycle is:

1. `transaction begin` records source identities, time, candidate workspace, snapshots, and deletions.
2. The agent writes complete candidate pages only inside the returned workspace.
3. `transaction validate` checks the prospective vault, including candidate-to-candidate and candidate-to-live links.
4. Human or agent transaction review inspects the candidate diff and validation report.
5. `transaction commit` revalidates, creates recovery material, promotes the reviewed bytes, owns affected manifest shards, and appends an immutable operation page.
6. The owner reviews `git diff` and handles Git publication outside the CLI.

Failed promotion retains status-aware recovery state. `transaction retry`, `restore`, `discard`, and `abort` make each next action explicit. Owner changes are never silently overwritten.

## Stable and derived views

Ordinary knowledge writes keep stable `wiki/index.md` and `wiki/log.md`; consumers derive navigation from page metadata and operation records. Ignored `wiki/hot.md` is a semantic recent-activity view. `hot status` detects staleness, `hot inputs` supplies bounded deterministic input, and `hot mark-current` records the regenerated view's fingerprint.

## Manifest v2

Manifest v2 is sharded and has exactly one configured source root. A transaction commit owns every affected shard and its source snapshot; never edit manifest shards directly. `check` compares tracked sources, snapshots, shards, generated pages, skills, mirrors, and bootstrap assets without invoking a model.

## Boundaries

The supported architecture is this single repository workflow. The CLI does not publish Git changes. A Dashboard is outside the current package and has no stub; any future Dashboard is a separate product decision.

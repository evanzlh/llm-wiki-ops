# CLI Reference

The `obsidian-wiki` Python package ships a CLI for setup, inspection, and the deterministic parts of the workflow — the things that don't need an LLM. Everything else is a [skill](skills.md) your agent runs. Install it with the supported [local-clone source build](installation.md#install-from-a-clone).

```bash
obsidian-wiki --help
obsidian-wiki --version
```

Running `obsidian-wiki` with no subcommand defaults to `setup`.

## Setup & inspection

| Command | What it does |
|---|---|
| `setup` | Install skills into your agents and write `~/.obsidian-wiki/config` |
| `setup --portable DIR` | Create or validate a clone-ready portable knowledge repository |
| `info` | Show install paths, version, and resolved config |
| `list` | List the bundled skills |
| `doctor` | Health-check config, vault shape, bootstrap assets, and installed skills |
| `check` | Read-only deterministic validation for the current portable repository |
| `repo upgrade-skills` | Transactionally refresh tracked framework-managed skills and adapters |
| `repo migrate` | Analyze or explicitly apply a legacy-to-portable repository migration |

```bash
obsidian-wiki setup --vault ~/brain
obsidian-wiki setup --portable ./team-knowledge
obsidian-wiki setup --project .        # also install project-local skills + bootstrap files
obsidian-wiki setup --project-only     # skip the global install (use with --project)
obsidian-wiki setup --copy             # copy skill files instead of symlinking
obsidian-wiki setup --remote https://github.com/you/my-wiki.git   # configure sync non-interactively

obsidian-wiki doctor --json --pretty
obsidian-wiki doctor --vault /other/vault --project .
obsidian-wiki doctor --strict          # exit non-zero on warnings too

cd ./team-knowledge
obsidian-wiki check
```

For framework CLI upgrades, follow the
[compatibility and managed-skills protocol](#upgrade-portable-cli-compatibility-and-managed-skills)
below; the quick reference intentionally does not skip its `requires_cli` step.

Portable setup writes only inside `DIR`: repository-relative TOML, vault
scaffolding, canonical skills, regular Markdown adapters, and bootstrap files.
It does not write global config or global agent directories. The repository
does not contain `.venv` or a vendored CLI; contributors install the CLI with
`uv tool install --link-mode copy .` from their own framework clone. Linux and macOS are the
first-release CLI support boundary.

Portable setup accepts a missing or empty target, or one containing only an ordinary `.git` directory; it preserves that directory and rejects arbitrary non-portable content. It does not run `git init`, commit, or configure a remote: for a new repository, run setup first and then `git init`; use `repo migrate` for legacy layouts.

Human-output commands other than `setup`, `info`, and `doctor` may warn when
the global Personal installation has gone stale (the package upgraded but
skills were not re-linked). Always-structured cache commands and `hot inputs`
do not mix this unrelated global setup warning into their output. Re-run
`obsidian-wiki setup` to repair a stale Personal installation.

### Upgrade portable CLI compatibility and managed skills

After installing a newer framework CLI, use this two-step portable CLI upgrade protocol
on a branch. First, deliberately edit the tracked `requires_cli` setting in
`.obsidian-wiki/config.toml` to a reviewed PEP 440 constraint that accepts the
installed version. Then refresh managed files, run deterministic validation,
and review the working-tree diff:

```bash
git switch -c upgrade-portable-cli
# Edit .obsidian-wiki/config.toml so requires_cli accepts the installed version.
obsidian-wiki repo upgrade-skills
obsidian-wiki check
git diff
```

Every collaborator must install a CLI version that satisfies the updated
repository constraint. `repo upgrade-skills` validates compatibility before
writing; it does not bypass an incompatible `requires_cli` constraint and does
not automatically rewrite `requires_cli`. The command also does not commit or
push, so review and commit both the constraint and managed-file changes before
publishing them through the normal pull-request workflow.

### Inspect runtime context

`info` is a read-only, per-invocation view of the runtime context and the CLI
installation. It never changes the default vault, a profile symlink, or any
configuration file. Select a vault or profile only for this invocation with
`info --vault PATH|@name`:

```bash
obsidian-wiki info
obsidian-wiki info --vault /other/vault
obsidian-wiki info --vault @research
obsidian-wiki info --json --pretty
```

Human output has two sections, `Runtime context` and `CLI installation`.
Machine output is one JSON document with top-level `runtime`, `installation`,
and `warnings` fields; `--pretty` only formats that document. Runtime status is
`resolved`, `unconfigured`, or `error`. An unconfigured machine has exit 0 and
setup guidance, so `info` remains available before setup. An invalid selected
configuration produces `runtime.status: "error"` and exit 1. JSON writes no
human text to stderr; human warnings are written to stderr.

### Portable-context override warnings

`doctor`, `lint`, `trust-record`, `trust-check`, `query`, and `context-pack`
honor an explicit vault or `@profile` selection even when their CWD discovers a
Portable Repository. Their JSON reports add a `context_warnings` array with
the informational `portable-context-overridden` warning; it is additive to the
command's normal report. This includes selecting the same vault explicitly:
the selection still bypasses portable semantics. Human output writes one
warning and its hint to stderr. The warning neither changes the command's
business or strict exit status nor contributes to numeric `doctor` warnings.

`info` presents the same condition in its top-level `warnings` array rather
than a command report. Omit the explicit selection when you intend to retain
the portable repository semantics.

## Legacy-to-portable migration

Migration is intentionally separate from `setup`. It accepts only a repository
that already contains a legacy vault and one separate source root. Both paths
must remain below `--root`; every manifest and page-frontmatter source must map
to an ordinary file below `--sources`. It never imports external files.

Run the analyzer first:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources
obsidian-wiki repo migrate --root . --vault wiki --sources sources --json --pretty
```

Without `--apply`, the command is read-only. Human output lists `Mappings`,
`Page updates`, `Manifest shards`, `Warnings`, and `Blockers`, then prints the
exact apply command only when the plan has no blocker. JSON uses
`status: "ready"` or `status: "blocked"`; a blocked plan exits `1` and has no
apply command.
Paths are resolved against `--root`, not the shell's current directory. An
already-portable repository returns success without rewriting it.

Blockers are stable operator-facing categories:

| Code | Meaning and action |
|---|---|
| `outside-root` | The vault or source root is outside the migration repository; co-locate it first. |
| `path-overlap` | The vault and source root contain one another; make them separate trees. |
| `managed-path-overlap` | A configured tree overlaps portable-owned config, skill, adapter, or ignore paths. |
| `portable-artifact-conflict` | Existing manifest v2 artifacts are non-empty or unsafe; reconcile them before migration. |
| `manifest-missing` | The legacy manifest is absent or not an ordinary file. |
| `manifest-invalid` | The legacy manifest cannot be parsed or has invalid source entries. |
| `unsafe-page` | A manifest page path is unsafe, escapes policy, or crosses a symbolic link. |
| `missing-page` | A page named by the legacy manifest is absent or not an ordinary file. |
| `page-frontmatter-invalid` | A knowledge page is unreadable UTF-8 or has invalid frontmatter. |
| `live-url-source` | A live URL must be captured as a bounded repository snapshot. |
| `pseudo-source` | A synthetic source token cannot become a portable Source ID. |
| `external-source` | A source is outside the configured source root; move or snapshot it first. |
| `unsafe-source` | A source crosses a symbolic link or another unsafe path boundary. |
| `missing-source` | A source is absent, unreadable, or not an ordinary file. |
| `source-id-collision` | Different legacy records map incompatibly to one repository-relative Source ID. |
| `unmapped-page-source` | Page frontmatter cites a source with no legacy manifest mapping. |

Warnings do not enable implicit apply; review them together with mappings.
Resolve every blocker and rerun dry-run. Before apply, require an enclosing Git
worktree whose top level equals `--root`, commit the complete legacy baseline
including intended sources, and confirm the worktree is clean. That baseline
commit is the supported post-success rollback point. Then use only the printed
explicit command:

```bash
obsidian-wiki repo migrate --root . --vault wiki --sources sources --apply
obsidian-wiki check
git diff
```

Apply rechecks all analyzed preimages and refuses drift. It installs portable
config, managed skills/adapters/bootstrap, `.gitignore`, and byte-stable
`.gitattributes`; rewrites page provenance; replaces manifest v1 with the v2
marker and one shard per source; replaces `index.md` and `log.md` with stable
built-in-query views; removes the existing legacy `hot.md`; and writes
one immutable migration operation last. It never initializes Git, stages,
commits, pushes, or opens a pull request.

Before changing the worktree, apply stores original bytes and absence records
below `.obsidian-wiki/local/migrations/<id>/snapshots`. Any failure attempts an
automatic byte-for-byte rollback and removes created files. A successful apply
prints `changed_files`, `removed_files`, and its retained `backup_dir`; keep
that ignored local directory until the ordinary Git diff is accepted and
committed. If rollback itself is incomplete, JSON reports `status: "error"`,
the error states that rollback failed, and the migration manifest/snapshots are
retained for manual diagnosis. The backup layout is internal evidence, not a
supported restore interface. There is no migration restore command after
success—use normal Git review/revert as the publication rollback boundary.

Portable Git discovery recognizes the worktree surrounding `<vault>/`; its top
level must match the portable root. These commands never create
`<vault>/.git`. When portable config is resolved, `sync` and `sync-setup`
refuse Personal-mode auto-stage/commit/push. Remove old Personal-mode cron or
aliases and do not bypass portable resolution with an explicit `--vault`.
There is no portable command that enumerates external schedulers or shell
configuration; inspect the system where that automation was installed.
The analyzer may run before Git exists, while `check` warns rather than
initializes a missing worktree. This is read-only planning tolerance: do not
run `--apply` until the enclosing root and clean committed baseline exist.
Untracked source state is not a dedicated migration blocker, so verify it
explicitly.

Collaborative sources are Markdown, text, and small reviewable text/structured
snapshots such as JSON, YAML, or TOML whose exact bytes are committed with
ordinary Git. The analyzer checks ordinary files but does not enforce Git-index
membership or detect LFS signatures; inspect `git status`, `git ls-files`, and
source bytes before publishing. Binary PDFs/images remain Personal-mode inputs
unless converted to reviewable text. Git LFS pointers are not dereferenced and
agents must not compile them as source contents.

## Portable transactions and local hot state

These commands resolve the nearest Portable Repository config from the current
directory. They fail outside portable mode and never invoke `git add`,
`commit`, `push`, or pull-request APIs.

| Command | What it does |
|---|---|
| `transaction begin --source PATH [PATH ...]` | Lock the repository, resolve actual authoritative sources, record preimages, and return a local `candidate_vault`. |
| `transaction list` | List active and retained recovery transactions, including workspace and status. |
| `transaction delete ID PAGE` | Declare removal of one safe vault-relative knowledge page. |
| `transaction validate ID` | Read-only preflight of candidate metadata and the complete prospective knowledge graph. |
| `transaction commit ID` | Validate and promote a reviewed active transaction. |
| `transaction retry ID` | Retry a retained failed transaction after rechecking preimages. |
| `transaction restore ID` | Restore recorded originals for interrupted `promoting`, failed, or completed work without overwriting later postimage drift. |
| `transaction abort ID` | Abandon active or failed staged work and release its lock. |
| `transaction discard ID` | Remove retained failed, completed, or restored recovery state after review. |
| `hot status` | Report freshness and invalidate only stale local `hot.md`. |
| `hot inputs` | Return bounded deterministic inputs for an agent-written `hot.md`; never write semantic prose. |
| `hot mark-current` | Record an agent-written `hot.md` against the current authoritative fingerprint. |

A normal write looks like this:

```bash
obsidian-wiki transaction begin --source sources/design.md --json --pretty
# From the repository-root CWD, write final vault-relative pages below the
# returned absolute runtime candidate_vault. Do not cd into that directory.
obsidian-wiki transaction delete <id> concepts/obsolete.md --json
obsidian-wiki transaction validate <id> --json --pretty
obsidian-wiki transaction commit <id> --json --pretty
```

`begin --source` accepts one or more repository-relative paths or filesystem
paths that resolve to ordinary files below the configured source root. The
JSON result includes `transaction_id`, `candidate_vault`, `snapshots`,
`source_ids`, and `status`. Candidate pages must use a supported knowledge
category, required frontmatter, and a non-empty `sources` list drawn only from
that transaction. Reserved central files and `journal/operations/**` cannot be
agent candidates.

Keep the repository root as the command working directory throughout this
flow. `candidate_vault` is an absolute runtime destination, not a durable
Source ID or a value to write into tracked files. Use the `started_at` returned
by `begin` for page timestamps: a new page uses
`created = updated = started_at`; an update preserves `created` and sets
`updated = started_at`.

`transaction validate ID` is read-only. Its JSON report contains
`transaction_id`, `status`, `candidate_pages`, `deletions`, `issues`, and
`warnings`. Each finding has `code`, `path`, and `message`; `target` is present
when the finding refers to a link, source, or expected category.
Exit status is `0` for `pass` and `1` for `fail`; CLI/configuration failures
retain the transaction error envelope described below. Content validation
uses this in-memory overlay:

```text
prospective vault = (live knowledge pages - declared deletions) + candidate replacements
```

The overlay is keyed by vault-relative path, so a candidate replaces the
same-path live page rather than creating a duplicate copy.

Candidate checks cover supported output paths, strict UTF-8, restricted YAML
frontmatter, required non-empty scalar fields, list-shaped `tags` and
`sources`, duplicate-free sources, ISO dates or timezone-aware timestamps,
and directory/category agreement. Each candidate may cite a non-empty subset
of the transaction's `source_ids`; it may not cite a foreign Source ID. Graph
checks resolve both wikilinks and local Markdown `.md` links across candidates
and unchanged live pages, and report broken or ambiguous identities—including
links broken by a declared deletion. `commit` enforces the same preflight
before any recovery snapshot or live-vault promotion; `retry` rechecks content
before resuming a retained failure. A failing report cannot be bypassed by
skipping the explicit validate command.

After preflight, commit checks only affected output preimages. A dirty source
worktree and unrelated edits are allowed. If an affected page or manifest shard drifted,
commit fails without silently overwriting it and retains the workspace. Inspect
it with `transaction list --json`, then deliberately `retry`, `restore`,
`abort`, or `discard` according to its status. A completed transaction remains
available for restore until discarded.

Transaction JSON failures use this exact envelope shape (values shown are an
example):

```json
{
  "status": "error",
  "error": {
    "code": "transaction-error",
    "message": "..."
  },
  "recovery": {
    "transaction_id": "tx-1",
    "transaction_status": "active",
    "inspect_command": "obsidian-wiki transaction list --json",
    "preferred_action": {
      "command": "obsidian-wiki transaction commit tx-1",
      "reason": "commit after fixing the original cause and reviewing the candidate",
      "requires": [
        "the original failure cause is removed",
        "the candidate vault has been reviewed"
      ]
    },
    "alternatives": [
      {
        "command": "obsidian-wiki transaction abort tx-1",
        "reason": "abandon the active staged work",
        "requires": ["the candidate is no longer needed"]
      }
    ]
  }
}
```

The top-level `recovery` object always has the five keys shown above.
`error.code` is `config-error`, `manifest-error`, or `transaction-error`, but
the code does not determine whether recovery actions are trusted. Recovery
trust comes only from a validated retained record reloaded after the failure.
Configuration, `begin`, `list`, and other no-ID failures are inspection-only,
as are corrupt or unreadable records: `transaction_id`, `transaction_status`,
and `preferred_action` are null, `alternatives` is empty, and `inspect_command`
remains `obsidian-wiki transaction list --json`. A `manifest-error` during
`commit` or `retry` can still include mutating guidance when its named retained
record reloads and validates.

For human transaction failures, stdout is empty. Within the transaction
failure block, stderr is ordered as the error, an optional trusted transaction
status, the inspect command, the preferred action and its requirements, then
alternatives and their requirements. An ambient stale-install warning and hint
may precede that block. Displayed untrusted control characters are escaped,
and the CLI does not invent commands for an untrusted record.

`obsidian-wiki transaction list --json` remains a top-level array. Its record
additions have this shape:

```json
[
  {
    "transaction_id": "tx-1",
    "status": "active",
    "recommended_action": {
      "command": "obsidian-wiki transaction commit tx-1",
      "reason": "commit after fixing the original cause and reviewing the candidate",
      "requires": [
        "the original failure cause is removed",
        "the candidate vault has been reviewed"
      ]
    },
    "allowed_actions": [
      {
        "command": "obsidian-wiki transaction commit tx-1",
        "reason": "commit after fixing the original cause and reviewing the candidate",
        "requires": [
          "the original failure cause is removed",
          "the candidate vault has been reviewed"
        ]
      },
      {
        "command": "obsidian-wiki transaction abort tx-1",
        "reason": "abandon the active staged work",
        "requires": ["the candidate is no longer needed"]
      }
    ]
  }
]
```

`recommended_action` is an action object or null; `allowed_actions` is an
array of action objects. Failure envelopes call the corresponding fields
`recovery.preferred_action` and `recovery.alternatives`. For a trusted record,
`allowed_actions` contains the preferred/recommended action followed by every
alternative. Human `transaction list` output includes the recommended command
too.

Use the status/action matrix rather than guessing after a failure:

| Status | Preferred recovery | Alternative recovery |
|---|---|---|
| `active` | `commit` after fixing the cause and reviewing the candidate. | `abort`. |
| `promoting` | `restore` only. | None. |
| `failed` | `retry` after fixing the cause and rechecking preimages. | `restore`, `abort`, or `discard`. |
| `complete` | `discard` after accepting the result. | `restore` while affected files match recorded postimages. |
| `restored` | `discard` after reviewing the restore. | A second, idempotent `restore`. |

Every action includes its `reason` and `requires` prerequisites. Guidance never
executes recovery, prompts, or invokes Git: after checking the prerequisites,
the user explicitly runs the chosen command. No action is automatically
executed.

Transaction JSON parse errors are structured with the same envelope. Use full
option names for transaction commands: their parsers do not accept long-option
abbreviations such as `--j` for `--json`.

Every successful commit writes one immutable operation page last:

```text
<vault>/journal/operations/YYYY/MM/YYYYMMDDTHHMMSSZ-<random-hex>.md
```

It records Source IDs and the created, updated, and removed page paths.
Ordinary portable transactions do not rewrite `<vault>/index.md`, `<vault>/log.md`,
or `<vault>/hot.md`. Built-in status/query paths inspect pages, manifest shards,
and operation entries directly, so `index.md` and `log.md` remain stable merge
surfaces.

Before a skill uses local semantic context, it runs:

```bash
obsidian-wiki hot status --json
# If stale, collect bounded deterministic inputs (defaults shown):
obsidian-wiki hot inputs --pages 50 --operations 10 --json --pretty
# The agent semantically rewrites ignored <vault>/hot.md from those inputs.
obsidian-wiki hot mark-current --json
```

The fingerprint covers authoritative knowledge, manifest state, operations,
and the current branch or detached HEAD. `hot status` deletes stale `hot.md`
only; it never invents semantic content. Git diff and pull-request review are
the portable content boundary, and publishing remains an explicit human step.
`hot inputs` is read-only and emits JSON by default with `fingerprint`, `pages`,
and `operations`. Each page summary contains `path`, `title`, `summary`, and
`updated`. Each validated immutable operation record contains `transaction_id`,
`completed_at`, `source_ids`, `created`, `updated`, and `removed`. Pages are
sorted and bounded by `--pages 50`; operations are bounded by
`--operations 10`. Zero disables that output list and negative limits fail.
The command validates every authoritative page and operation even when a limit
is zero, and fails closed
on malformed or changing input. It neither writes `hot.md` nor calls a model.

## Querying & linting

| Command | What it does |
|---|---|
| `query <question>` | Answer a question from the configured vault's index |
| `lint [vault]` | Find missing frontmatter, broken links, duplicates, and orphans |

```bash
obsidian-wiki query "what do I know about MCP security?"
obsidian-wiki query "rate limiting" --top 12 --max-read 5 --json

obsidian-wiki lint                     # uses the configured vault
obsidian-wiki lint /path/to/vault --strict
obsidian-wiki lint @research --json    # uses ~/.obsidian-wiki/config.research only
obsidian-wiki lint --strict-trust      # fail on trust-ledger problems, not just warn
obsidian-wiki lint --allow-lifecycle active --allow-relationship-type synthesizes \
  --required-trust-field updated --schema-source /path/to/vault/AGENTS.md
```

Lint resolves its vault and schema together: explicit path (no config
inheritance), positional `@name`, nearest ancestor
`.obsidian-wiki/config.toml`, nearest CWD `.env`, then global config. CLI schema
flags extend/replace that resolved vault's settings and are recorded in the
JSON `schema` block.

## Context packs

`wiki-context-pack` compiles a task-scoped snapshot from existing Markdown.
Notes do not need to be moved into wiki-generated folders or migrated to the
full frontmatter schema. The command is read-only.

```bash
obsidian-wiki context-pack "authentication architecture" --budget 8000
obsidian-wiki context-pack --recent --budget 4000
obsidian-wiki context-pack "release notes" --budget 8000 --public-only
```

Omitting `--budget` uses the default of 8000 estimated tokens.

The output includes source paths, summaries, selected excerpts, and a hard
estimated-token ceiling. Vault excerpts are explicitly marked as untrusted
reference data: downstream agents may use their facts but must not execute
instructions embedded in notes. Use `--metadata-only` for the smallest pack,
or `--json` for tool-to-tool integration.

| Flag | Effect |
|---|---|
| `--budget N` | Maximum estimated output tokens, 256–100000 (default 8000) |
| `--recent` | Select recently updated notes — the only way to omit the topic |
| `--public-only` | Exclude `visibility/internal` and `visibility/pii` notes |
| `--metadata-only` | Titles, provenance, and summaries with no body excerpts |
| `--json` | Structured output for tool-to-tool integration |
| `--vault PATH` | Override `OBSIDIAN_VAULT_PATH` |

`context` is an accepted alias for `context-pack`.

## Session brain

Builds a topic graph over your agent session history. Output is a **sidecar** at `~/.claude/session-brain/` — the vault is never written to. Full detail in [Session Brain](session-brain.md).

| Command | What it does |
|---|---|
| `sessions-build` | Build (or incrementally update) the topic graph |
| `sessions-query <topic>` | Find the sessions most relevant to a topic |
| `sessions-show <id>` | Show one session's node and its nearest neighbours |
| `sessions-clusters` | List the discovered topic clusters |
| `sessions-name --from FILE` | Assign durable names to clusters, surviving rebuilds |

```bash
obsidian-wiki sessions-build                       # ~3s cold, under a second incrementally
obsidian-wiki sessions-build --full --verbose      # ignore caches, re-read everything
obsidian-wiki sessions-build --since 2026-01-01 --skip archived,scratch
obsidian-wiki sessions-build --k 12 --min-sim 0.12 --mutual --half-life 60

obsidian-wiki sessions-query "prismor telemetry"
obsidian-wiki sessions-query "auth bug" --project my-app --cluster 3 --json

obsidian-wiki sessions-show 01935a40 --neighbors 12
obsidian-wiki sessions-clusters --unnamed
obsidian-wiki sessions-name --from names.json      # or - for stdin
```

`sessions-name` takes a JSON array of `{"id": N, "name": "...", "summary": "..."}`. The `/session-brain` skill generates this for you.

## Vault syncing

| Command | What it does |
|---|---|
| `sync` | Stage, commit, and push pending vault changes |
| `sync-setup <remote>` | Configure GitHub sync (git init, `.gitignore`, remote) |

```bash
obsidian-wiki sync
obsidian-wiki sync-setup https://github.com/you/my-wiki.git
```

See [Configuration → Syncing your vault to GitHub](configuration.md#syncing-your-vault-to-github).

These sync commands are for Personal-mode vault repositories. Portable
repositories use the transaction commands above and a human-controlled branch
and pull-request workflow. From a Portable Repository CWD, `sync` and
`sync-setup` refuse before any mutation even with an explicit `--vault`; it
cannot bypass that branch-and-pull-request boundary. Outside portable context,
Personal-mode `sync` and `sync-setup` retain their existing auto-stage,
commit, push, and setup behavior. Transaction and hot-state commands never
commit or push.

## Trust ledger

Records and validates human-approved confidence reviews, so you can gate on "a person actually checked these pages" in CI.

| Command | What it does |
|---|---|
| `trust-record` | Record explicitly approved manual confidence reviews |
| `trust-check` | Validate confidence values and material fingerprints against the ledger |

```bash
obsidian-wiki trust-record --all --reviewed-at 2026-07-30T10:00:00+00:00 --approved
obsidian-wiki trust-record --page concepts/rate-limiting.md --reviewed-at <ISO> --approved
obsidian-wiki trust-check --strict
obsidian-wiki trust-record @research --all --reviewed-at <ISO> --approved --allow-lifecycle active
obsidian-wiki trust-check @research --allow-lifecycle active --schema-source /vault/AGENTS.md
```

`--reviewed-at` needs a timezone. `--approved` is required and mandatory — it's your assertion that a human approved every confidence value being recorded. `trust-check --strict` is the CI/scheduled gate. `trust-record` and `trust-check` resolve the same vault-scoped schema as lint; pass the same lifecycle and required-field overrides to record and check. If the owner schema does not require `base_confidence`, pages without it are reported as `not_applicable`, excluded by `trust-record --all`, and any obsolete ledger entry is warned by `trust-check` then removed by `trust-record --page` or a rebuild. Both JSON and human-readable record output list excluded pages and removed obsolete entries; human output also emits a stderr warning when removal occurs. Required-field config accepts only `base_confidence`, `lifecycle`, `lifecycle_changed`, and `updated`; typos fail closed. Lifecycle, relationship-type, and required-field override values are stripped and empty or whitespace-only entries are rejected rather than added to an allowlist. Without an explicit `--schema-source`, CLI overrides on an explicit vault are labeled `cli:explicit-vault`; combined CLI and config overrides use `cli+config:<resolved-config-path>`.

## Lower-level commands

Available for automation, scripting, and debugging. Skills call some of these internally.

| Command | What it does |
|---|---|
| `graph-query <vault> <question>` | Answer from the wikilink index without reading page bodies |
| `graph-analyse <vault>` | God nodes, communities, surprising connections |
| `batch-plan <vault> <source_dir>` | Split a source directory into parallel-ingest batches, skipping unchanged files |
| `cache-check <vault> <sources...>` | Legacy explicit-vault form: which sources are new / modified / unchanged in the mode-appropriate manifest |
| `cache-check --configured <sources...>` | Resolve the vault and mode from CWD configuration; every positional path is a source |
| `cache-update <vault> <source>` | Low-level compatibility interface: record a source hash and pages in personal v1 or a portable v2 shard |
| `cache-hash <path>` | Compute a file or directory hash (no manifest I/O) |
| `ast-extract <path>` | Extract classes, functions, and imports from code — no LLM, no API calls |

```bash
obsidian-wiki graph-query /path/to/vault "transformer architecture" --pretty
obsidian-wiki graph-analyse /path/to/vault --top 30 --pretty
obsidian-wiki batch-plan /path/to/vault ~/research --max-mb 4 --max-files 30
obsidian-wiki cache-check --configured sources/design.md --json --pretty
obsidian-wiki cache-check /resolved/vault /resolved/source.md --json --pretty
obsidian-wiki cache-update /resolved/vault /resolved/source.md --json --pretty
obsidian-wiki cache-hash /resolved/source.md --json --pretty
obsidian-wiki ast-extract ./src --pretty
```

`cache-update` is a low-level compatibility interface for explicit manifest
maintenance and legacy automation; the positional example documents its real
CLI form. It is not a Portable transaction completion step: ordinary Portable
write skills use `transaction commit`, which derives and owns the affected
shards.

Cache output is JSON by default. `cache-check`, `cache-update`, and
`cache-hash` also accept explicit `--json` idempotently, and `--pretty` only
adds indentation. Without `--configured`, `cache-check` preserves the legacy
`VAULT SOURCE...` interpretation; with it, the normal CWD config protocol
resolves the vault and every path is a source. When a legacy explicit-vault
`cache-check` overrides a Portable context discovered from CWD, its relevant
warning appears in `context_warnings`; because cache commands are always
structured, structured JSON stdout remains parseable and stderr stays empty.
Unrelated global Personal setup warnings are suppressed.

Most other commands accept `--json` and/or `--pretty` for machine-readable output.

## Portable repository validation

From any directory inside a portable repository, run:

```bash
obsidian-wiki check
obsidian-wiki check --json --pretty
```

The command is read-only and does not invoke an LLM. Human output lists every
issue; `--json` emits `status`, `errors`, `warnings`, and `issues`, while
`--pretty` only formats that JSON.

Exit behavior is suitable for any CI platform:

- `exit 0` when the report has no errors (warnings, if any, remain visible).
- `exit 1` when validation finds an error, config resolution fails, or the
  command is run outside Portable Repository mode.

Portable manifest drift uses three stable error codes: `source-new` means an
authoritative source has no shard, `source-stale` means its content hash no
longer matches, and `source-orphaned` means a shard has no source file. All
three are PR blockers.

For `source-new` or `source-stale`, compile or recompile the source through a
transaction. Begin with the complete authoritative source closure, write and
review candidate pages from the repository-root CWD, and use this completion
sequence:

```bash
obsidian-wiki transaction begin --source <source> --json --pretty
# Compile or recompile reviewed candidate pages below the returned candidate_vault.
obsidian-wiki transaction validate <id> --json --pretty
obsidian-wiki transaction commit <id> --json --pretty
obsidian-wiki check
```

Commit updates the affected shards. Do not follow transaction completion with
`cache-update`. After commit, rerun `obsidian-wiki check` and review the
Git diff before merging.

For `source-orphaned`, restore a source deleted by mistake. If deletion was
intentional, remove the entire corresponding shard file with
`git rm <vault>/.manifest/sources/<relative>.json`. This is whole-file Git
deletion, never editing marker or shard JSON fields; there is no CLI removal
subcommand.

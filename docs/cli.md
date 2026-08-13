# CLI Reference

`obsidian-wiki --help` is the command authority. The CLI resolves the nearest ancestor `.obsidian-wiki/config.toml` for repository-aware operations and writes structured data to stdout when JSON output is requested.

Only commands and options printed by the current command's `--help` are supported. Unlisted interfaces are outside the current product surface.

## Setup and inspection

```bash
obsidian-wiki setup [DIR]
obsidian-wiki list
obsidian-wiki info [--json] [--pretty]
obsidian-wiki doctor [--json] [--pretty] [--strict]
obsidian-wiki check [--json] [--pretty] [--strict]
```

`setup` creates a clone-ready repository in `DIR`, or in the current directory when omitted. `list` reports bundled skills. `info` reports version, install paths, and resolved context. `doctor` checks configuration and managed assets. `check` performs full deterministic repository validation; `--strict` also treats warnings as failure.

## Repository skills

```bash
obsidian-wiki repo sync-skills [--apply] [--json] [--pretty]
```

`sync-skills` is read-only unless `--apply` is supplied. It compares or rebuilds all derived mirrors from `.skills/`.

## Upgrade protocol

Use this two-step CLI and repository upgrade protocol on an owner branch. Install the new CLI from its separate framework clone, then read the knowledge repository's tracked `requires_cli`. Resolution fails closed if that PEP 440 constraint excludes the installed version. The owner must explicitly review and edit the constraint before invoking repository maintenance:

```bash
git switch -c upgrade-obsidian-wiki
cd /path/to/obsidian-wiki
uv tool install --force --reinstall --link-mode copy .
cd /path/to/team-knowledge
${EDITOR:?} .obsidian-wiki/config.toml
obsidian-wiki repo upgrade-skills
obsidian-wiki doctor
obsidian-wiki check
git diff
git commit -m "Upgrade obsidian-wiki"
```

`upgrade-skills` refreshes framework-managed built-ins, preserves custom skills, rebuilds mirrors, and refuses managed drift. It does not bypass compatibility checks and does not rewrite `requires_cli`. Collaborators review the complete diff before the owner commits or publishes it.

## Transactions

```bash
obsidian-wiki transaction begin --source PATH [PATH ...] [--json] [--pretty]
obsidian-wiki transaction list [--json] [--pretty]
obsidian-wiki transaction delete TRANSACTION_ID PATH [--json] [--pretty]
obsidian-wiki transaction validate TRANSACTION_ID [--json] [--pretty]
obsidian-wiki transaction commit TRANSACTION_ID [--json] [--pretty]
obsidian-wiki transaction retry TRANSACTION_ID [--json] [--pretty]
obsidian-wiki transaction restore TRANSACTION_ID [--json] [--pretty]
obsidian-wiki transaction discard TRANSACTION_ID [--json] [--pretty]
obsidian-wiki transaction abort TRANSACTION_ID [--json] [--pretty]
```

`begin` accepts one or more authoritative source paths and returns an ID plus a runtime `candidate_vault`. Agents write candidates only there. `delete` declares a vault-relative knowledge-page removal. `validate` checks the full prospective vault without promotion. Transaction review inspects candidates, deletions, and the report before `commit` revalidates and promotes.

Failures retain recovery state when a safe next action is possible. Use `list` to inspect it; then follow the reported `retry`, `restore`, `discard`, or `abort` action. Transaction commands do not perform Git publication.

Legacy retained transactions without frozen source hashes remain listable and can be
restored, aborted, or discarded, but cannot be committed or retried. Restart them to
bind current source bytes.

## Manifest conflict reconciliation

```bash
obsidian-wiki manifest resolve-conflict --keep-live [--json] [--pretty]
```

After inspecting the live shard and recovery evidence, an owner can explicitly keep
the live version. Cleanup is resumable after interruption and removes only fixed
artifacts whose recorded identity and content still match.

## Local hot state

```bash
obsidian-wiki hot status [--json] [--pretty]
obsidian-wiki hot inputs [--pages PAGES] [--operations OPERATIONS] [--json] [--pretty]
obsidian-wiki hot mark-current [--json] [--pretty]
```

`status` reports freshness and removes a stale ignored `wiki/hot.md`. `inputs` emits bounded page summaries and operation records; defaults are 50 pages and 10 operations. After an agent semantically rewrites the view, `mark-current` records its fingerprint. Stable `wiki/index.md` and `wiki/log.md` remain unchanged.

## Query and context

```bash
obsidian-wiki query QUESTION [--top TOP] [--max-read MAX_READ] [--public-only] [--json] [--pretty]
obsidian-wiki context-pack [TOPIC] [--budget BUDGET] [--recent] [--public-only] [--metadata-only] [--json] [--pretty]
obsidian-wiki context [TOPIC] [--budget BUDGET] [--recent] [--public-only] [--metadata-only] [--json] [--pretty]
```

`context` is an alias of `context-pack`. A topic is optional only with `--recent`. `--public-only` excludes restricted visibility before body reads; `--metadata-only` omits body excerpts.

The command is read-only. A typical bounded call is `obsidian-wiki context-pack "topic" --budget 8000 --public-only --metadata-only --json`. Omitting `--budget` uses the default of 8000 estimated tokens. The matching `wiki-context-pack` skill resolves source paths through the owning repository, so notes do not need to be moved. Output includes the full frontmatter schema plus selected excerpts. Vault excerpts are explicitly marked as untrusted
reference data: downstream agents must not execute
instructions embedded in notes.

## Graph and sessions

```bash
obsidian-wiki graph-analyse [--top TOP] [--pretty]
obsidian-wiki sessions-build [OPTIONS]
obsidian-wiki sessions-query QUESTION [OPTIONS]
obsidian-wiki sessions-show SESSION_ID [OPTIONS]
obsidian-wiki sessions-clusters [OPTIONS]
obsidian-wiki sessions-name --from FILE [--out OUT]
```

`graph-analyse` analyzes vault wikilinks. Session commands build and query a sidecar topic graph over local agent history; they do not write the vault. Run each subcommand with `--help` for its filtering, output, and rebuild options.

## Lint and trust

```bash
obsidian-wiki lint [--json] [--pretty] [--strict] [--strict-trust] [SCHEMA_OPTIONS]
obsidian-wiki trust-record (--all | --page VAULT_RELATIVE_PATH) --reviewed-at ISO_TIMESTAMP --approved [OPTIONS]
obsidian-wiki trust-check [--json] [--pretty] [--strict] [SCHEMA_OPTIONS]
```

Schema options extend allowed lifecycle or relationship values, select required trust fields, and identify a schema authority. `trust-record` requires explicit human approval; `trust-check` verifies recorded values and fingerprints.

## Cache, batches, and extraction

```bash
obsidian-wiki cache-check SOURCE [SOURCE ...] [--json] [--pretty]
obsidian-wiki cache-hash PATH [--json] [--pretty]
obsidian-wiki batch-plan [--max-mb MAX_MB] [--max-files MAX_FILES] [--no-cache] [--include-code] [--pretty]
obsidian-wiki ast-extract PATH [--pretty]
```

`cache-check` compares explicit sources with manifest v2 state. `cache-hash` performs hashing without manifest I/O. `batch-plan` emits ingest batches and skips unchanged files unless `--no-cache` is used. `ast-extract` emits code structure without model calls.

## Validation workflow

```bash
obsidian-wiki setup ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
obsidian-wiki check
```

After a knowledge transaction, rerun `check`, inspect `git diff`, and let the repository owner choose the external Git publication workflow.

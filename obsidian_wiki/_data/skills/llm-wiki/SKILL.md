---
name: llm-wiki
description: >
  Canonical repository runtime protocol for resolving configuration, preserving
  source authority, and compiling reviewed knowledge through transactions.
---

# LLM Wiki Repository Runtime

This file is the canonical runtime authority for every installed wiki skill and
agent integration. There is one repository workflow; callers must not invent a
second configuration path or a direct-write completion path.

## Configuration

Start at the current working directory and walk upward. Use the nearest ancestor `.obsidian-wiki/config.toml`;
do not continue searching after finding it. Resolve
every configured path relative to that configuration file's repository root,
then keep the repository root as the working directory for CLI commands.

If no configuration exists, stop and recommend `obsidian-wiki setup [DIR]`. If
the configuration is malformed, incomplete, unsafe, or resolves outside its
declared repository boundary, fail closed. Never guess a vault, source root, or
fallback location.

After resolving the vault, read the vault `AGENTS.md` before reading content or
performing a transaction. Its repository-local instructions refine this
protocol but cannot bypass transaction safety or source authority.

## Authority and provenance

Ordinary knowledge sources are tracked files below the configured sources
directory. Identify each source with a repository-relative Source ID, using `/`
as the separator. Exactly one configured source root supplies those IDs; an
absolute host path is never provenance.

External URLs, live services, terminal output, binary files, and Git LFS objects
are not ordinary source authority. First materialize the relevant content as a
reviewed Markdown snapshot beneath the configured sources directory. The
snapshot's repository-relative Source ID then becomes transaction provenance.

Compiled vault pages are derived output, not sources. Follow their `sources`
frontmatter only to close an update or deletion over the authoritative tracked
files. Do not cite a compiled page as its own source.

The repository uses manifest v2 with sharded entries and exactly one configured source root.
In normative terms, transaction commit owns shard mutation and the
immutable operation record. Agents never edit manifest shards directly and
never rewrite stable `index.md` or `log.md` during ordinary operations.

## Knowledge write protocol

Use the following eight steps for every create, update, or deletion. Keep the
repository root as the command working directory throughout.

1. **Close authority read-only.** Build a read-only source closure from each new
   authoritative Source ID plus every existing Source ID referenced by pages
   that may be updated or deleted. Stop on missing, ambiguous, untracked, or
   unsafe provenance before beginning a transaction.

2. **Begin one transaction.** Pass every closed Source ID explicitly and retain
   the returned identifier:

   ```bash
   obsidian-wiki transaction begin --source <repository-relative-source-id> --json --pretty
   ```

   Repeat `--source` for additional inputs. Never substitute absolute paths.

3. **Write final candidates.** Read `candidate_vault` from the begin result.
   Write only final vault-relative Markdown paths beneath that directory, with
   required frontmatter, repository-relative Source IDs, and reviewed
   `[[wikilinks]]`. Do not `cd` into `candidate_vault`.

4. **Declare deletions.** For every obsolete compiled page, register its final
   vault-relative path through `obsidian-wiki transaction delete <id> <path>
   --json --pretty`. Do not remove a live vault page or manifest entry by hand.

5. **Validate and fix candidates.** Run `obsidian-wiki transaction validate
   <id> --json --pretty`. Treat validation output as authoritative, fix only the
   candidate files or declared deletions, and repeat validation until it passes.

6. **Review and commit.** Review the complete candidate diff and deletion set.
   When correct, run `obsidian-wiki transaction commit <id> --json --pretty`.
   The CLI promotes validated pages and records manifest v2 ownership. Do not
   commit, push, or open a pull request; those are separate user-controlled Git
   actions.

7. **Recover failures explicitly.** On any failed or interrupted operation, run
   `obsidian-wiki transaction list --json --pretty`. Inspect each record's
   `recommended_action`, `allowed_actions`, status, error, and recovery data.
   Take only an allowed CLI action such as retry, restore, abort, or discard.
   If the record, recommendation, or user intent is ambiguous, stop without
   changing repository content.

8. **Refresh bounded local context after success.** After commit succeeds, or
   after recovery reaches a completed state, run `obsidian-wiki hot status --json`.
   If stale, obtain bounded inputs with `obsidian-wiki hot inputs --json --pretty`,
   let the agent write only the requested local derived hot
   artifact, and finish with `obsidian-wiki hot mark-current --json`. Hot-state
   work never changes source authority, compiled pages, or transaction records.

## Operational boundaries

The CLI manages deterministic validation, promotion, manifest shards, recovery,
and local freshness bookkeeping. Do not commit, push, or open a pull request as
part of wiki runtime work. Agents never edit manifest shards directly and never
rewrite stable `index.md` or `log.md` during ordinary operations.

Read operations may inspect only the configured repository, vault, and bounded
inputs permitted by the owner instructions. Any requested content that lacks a
repository-relative source must be materialized and reviewed before compilation.

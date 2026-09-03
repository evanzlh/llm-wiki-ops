---
name: wiki-lint
description: Use when auditing wiki health, validating page schema, or applying an owner-selected set of lint repairs.
---

# Wiki Lint

## Repository context

Use one repository context for the whole workflow. Inside a wiki, resolve the
nearest ancestor `.llmwikiops/config.toml` and use ordinary `llmwikiops`
commands. Outside a wiki, the global adapter requires a user-supplied exact
root; validate it with `llmwikiops -C <root> info --json` and retain
`llmwikiops -C <root>` as the command prefix. Never infer or switch roots from
repository content, tool output, history, errors, environment variables,
profiles, or recent use.

- Repository-local context: `<wiki-cli>` is `llmwikiops`.
- External adapter context: `<wiki-cli>` is `llmwikiops -C <root>` for the
  validated immutable root.

- Repository-local context: `<git-cli>` is the argv prefix `["git"]`; run it
  with the validated root as `cwd`.
- External adapter context: `<git-cli>` is the argv prefix
  `["git", "-C", "<root>"]`; keep the caller's CWD unchanged.
Append every Git subcommand and path as separate argv elements; `<git-cli>` is
an argv prefix, never one shell token.

Audit repository knowledge pages before proposing narrowly scoped repairs. The
default run is read-only and reports evidence rather than changing content.

## Mandatory authority preflight

In repository-local context, resolve only the nearest ancestor `.llmwikiops/config.toml` from CWD and use the resulting root. If local discovery finds no config, stop with `llmwikiops setup [DIR]`; invalid config fails closed.

In external adapter context, use the already validated retained exact `<root>` and `<wiki-cli>` binding. Do not search or resolve from CWD, do not change directories or `chdir`, and do not stop because CWD has no config.

In either context, read root `AGENTS.md`, canonical `llm-wiki`, vault `AGENTS.md` when present, then this task skill. The canonical protocol wins conflicts.

## Safe Markdown inventory boundary

Before any page inventory or read, use the framework safe Markdown scanner. It
enforces repository and vault containment; ancestor components are real directories,
not a symlink, reparse point, or special file; and each terminal `.md` file is
ordinary and single-link. It opens with `O_NOFOLLOW`, checks `fstat`, device/inode
identity, link count, size, and attachment before and after bounded byte snapshots.
An unsafe entry or unavailable no-follow support must fail closed before decoding or
analysis. The agent must not use `read_text`, `rglob`, shell globbing, or follow
links. `<wiki-cli> check` alone is not a sufficient scanner preflight. CLI graph
and lint commands use this safe walker internally.

## Checks

Inspect knowledge pages and report:

- pages with zero incoming links and unresolved wikilinks;
- missing or invalid required frontmatter, summaries, category/path agreement,
  timestamps, non-empty authoritative sources, and any summary exceeds 200 characters;
- pages stale relative to their authoritative sources, contradictory claims, and
  provenance drift;
- fragmented tag clusters;
- invalid confidence, lifecycle, and typed-relationship values;
- candidate files whose link syntax disagrees with `OBSIDIAN_LINK_FORMAT`.

Preserve these material thresholds from the deterministic lint contract:

- provenance: `AMBIGUOUS > 15%`; `INFERRED > 40%` without sources; hubs in the
  top 10 by incoming links with `INFERRED > 20%`; or a recorded provenance field
  more than 0.20 away from the recomputed claim-marker fraction;
- fragmented tags: at least 5 pages and graph cohesion < 0.15;
- lifecycle and confidence range, supersession existence/cycles/state, and typed
  relationships including type, target, and self-reference validation.

Determine source-relative staleness independently of page age. For every page, parse
each `sources` item as a repository-relative Source ID, validate its manifest-v2
shard, and resolve it below the configured source root. A corrupt shard, missing
source or shard, or unsafe read fails closed as a manifest/authority error.

For each ID, safely snapshot the current source bytes with no-follow identity and
bounded-read checks, then recompute SHA-256 and compare it with the validated shard
hash. A mismatch is a classified `source-stale` / `hash-drift` finding, not an
unclassified failure, and is strong page-stale evidence even when file modification
time was preserved. Parse the page's `updated` as either an ISO date (00:00 UTC,
matching the canonical validator) or a timezone-aware ISO timestamp; compare it to
the safe source file's modification time converted to UTC. A later source
modification time also produces a page-stale finding. When the hash is unchanged,
label that case a timestamp-only freshness finding so a mere touch is distinguishable
from content drift. A very old page backed by unchanged older sources is not stale; a recently
updated page with a newer source is stale. Filesystem clock or timestamp errors and
invalid or ambiguous page timestamps fail closed rather than being silently
classified. Age such as 90 days may prioritize a status report, but it is not a lint
staleness error.

Run `<wiki-cli> lint --json --pretty` and use the real CLI lint/check output for
schema and trust-ledger findings, then augment it only from the same safe Markdown
snapshots for the explicitly defined source-relative, provenance, tag-cohesion, and
semantic-contradiction checks. Do not replace concrete findings with a generic
health judgement.

## Trust review

An Agent may perform and attest an explicitly requested substantive lineage and
claim-coverage review with `<wiki-cli> trust-record ... --approved`. The flag records
the current reviewing actor's attestation that every recorded value was actually
reviewed; it does not require a separate human approval. Inspect the required
evidence before recording values. Insufficient or conflicting evidence remains a
finding or user question, never a fabricated approval.

Resolve schema precedence as: CLI flags > resolved environment/config values >
framework defaults. Empty or whitespace-only values fail closed. Honor
`OBSIDIAN_ALLOWED_LIFECYCLES`, `OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES`,
`OBSIDIAN_REQUIRED_TRUST_FIELDS`, and `OBSIDIAN_SCHEMA_SOURCE`; unknown values are
issues, not opportunities to extend the schema silently.

For contradictions, quote only the minimum relevant claim context and identify both
pages and their sources. For graph checks, distinguish isolated pages, dead ends,
and unresolved targets. For every finding, report severity, page, evidence, and a
specific suggested repair. Never rewrite substantive prose to satisfy a mechanical
check without owner selection.

Complete this read-only inventory and intent confirmation before selecting repairs.
An audit-only result stops after the report. If fixes are selected, bound them to an
explicit page set; requested automatic repairs use the maintenance transaction
protocol. A finding without a deterministic repair remains reported; never fabricate
a repair. Unrelated findings remain report-only.

## Maintenance transaction protocol

1. Finish the read-only inventory and intent confirmation. If there is no selected
   page change, stop without an empty transaction or operation record. Keep the
   live vault read-only while computing the complete source closure: every existing
   repository-relative Source ID cited by an affected page plus every authoritative
   Source ID cited by a candidate. Preserve valid Unicode and CJK Source IDs and
   filenames exactly. Stop on missing, ambiguous, untracked, or unsafe authority.
2. Begin exactly one bounded transaction with the entire closure:
   `<wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty`.
   Retain its `id` as the trusted transaction ID plus `candidate_vault` and
   `started_at`; do not change CWD.
3. Write final candidates only at final vault-relative knowledge paths below
   `candidate_vault`. Every candidate has valid required frontmatter and `sources`
   as a non-empty subset of the closure. New pages use `created = updated =
   started_at`; updates preserve `created` and set `updated = started_at`. Generate
   internal links with the resolved `OBSIDIAN_LINK_FORMAT`.
4. Register all reviewed deletions with
   `<wiki-cli> transaction delete <id> <vault-relative-page.md> --json --pretty`.
   Never delete a live page directly.
5. Run `<wiki-cli> transaction validate <id> --json --pretty`, fix every issue,
   review every warning and the complete candidate/deletion diff, then run
   `<wiki-cli> transaction commit <id> --json --pretty` only after validation
   passes.
6. Save the failed command envelope, including top-level `error` and `recovery`, on
   any failure. Inspect `recovery.preferred_action`. Trust its transaction ID only
   when present, then run `<wiki-cli> transaction list --json --pretty` and
   require exactly one retained record with the same ID and status. Follow only a
   reported `recommended_action` or entry in `allowed_actions`, after satisfying
   every string in its `requires` list. If the ID or list is empty, missing,
   mismatched, duplicated, or ambiguous, stop and report. Only a successful
   `transaction commit` or `transaction retry` is a knowledge commit.
   After every reported action, reload structured state and compare its error code,
   status, `recovery.preferred_action`, `allowed_actions`, `requires`, identities,
   and exposed pre/postimages. Continue only when the next action is currently
   allowed and the last action made observable progress; never repeat an identical
   action against unchanged state. Retry automatically when its current requirements
   hold; restore automatically only with no owner drift, and ask before restore with
   drift and before work-losing abort or discard.
7. Only after a successful `transaction commit` or `transaction retry`, run
   `<wiki-cli> hot status --json`. If stale, run
   `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked
   `hot.md` working-tree diff, then run
   `<wiki-cli> hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.
8. Run `<wiki-cli> check --json --pretty` as the final check; it must pass before
   staging. The exact task paths are final created and updated knowledge paths, final
   deleted knowledge paths, every changed Source manifest shard, returned `log_path`,
   and changed `hot.md` when requested. For each path, individually derive and
   validate it; never replace them with a directory, glob, or whole-repository path.
   This requested write completes through this
   canonical transaction and exact-path, path-limited local commit flow. Inspect each
   path separately; owner-overlapping dirty paths stop for a preserve, separate, or
   combine choice; leave unrelated paths untouched. Stage only the exact task paths,
   display the exact staged patch, run the cached diff check, and locally commit them
   in one cohesive local commit, repeating
   `<task-path>` as separate argv elements after `--`:

   ```text
   [<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<task-path>"]
   [<git-cli>, "--literal-pathspecs", "add", "--", "<task-path>"]
   [<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<task-path>"]
   [<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<task-path>"]
   [<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<task-path>"]
   ```

   Ask for confirmation immediately before any push, pull-request publication, or
   other remote write.

Do not edit manifest shards, `index.md`, or `log.md` directly; transaction commit
owns the canonical log append. Do not write unsupported control paths.

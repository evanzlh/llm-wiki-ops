---
name: wiki-lint
description: Use when auditing wiki health, validating page schema, or applying an owner-selected set of lint repairs.
---

# Wiki Lint

Audit repository knowledge pages before proposing narrowly scoped repairs. The
default run is read-only and reports evidence rather than changing content.

## Checks

Inspect knowledge pages and report:

- pages with no incoming links and links whose target cannot be resolved;
- missing or invalid required frontmatter, summaries, category/path agreement,
  timestamps, and non-empty authoritative sources;
- stale high-impact pages, contradictory claims, provenance drift, and synthesis
  gaps;
- fragmented tag clusters, visibility inconsistencies, and promotion candidates;
- invalid confidence, lifecycle, and typed-relationship values;
- candidate files whose link syntax disagrees with `OBSIDIAN_LINK_FORMAT`.

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
explicit page set; unrelated findings remain report-only.

## Mandatory authority preflight

Locate the nearest ancestor `.obsidian-wiki/config.toml`, resolve its repository
root, and keep that repository root as the command working directory. If resolution
fails, stop and recommend `obsidian-wiki setup [DIR]`; do not guess paths. Before
inventory, read authority in this order: root `AGENTS.md`, canonical `llm-wiki`,
vault `AGENTS.md` when present, then this task skill. The canonical protocol wins
if instructions conflict.

## Maintenance transaction protocol

1. Finish the read-only inventory and intent confirmation. If there is no selected
   page change, stop without an empty transaction or operation record. Keep the
   live vault read-only while computing the complete source closure: every existing
   repository-relative Source ID cited by an affected page plus every authoritative
   Source ID cited by a candidate. Preserve valid Unicode and CJK Source IDs and
   filenames exactly. Stop on missing, ambiguous, untracked, or unsafe authority.
2. Begin exactly one bounded transaction with the entire closure:
   `obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty`.
   Retain its `id` as the trusted transaction ID plus `candidate_vault` and
   `started_at`; do not change CWD.
3. Write final candidates only at final vault-relative knowledge paths below
   `candidate_vault`. Every candidate has valid required frontmatter and `sources`
   as a non-empty subset of the closure. New pages use `created = updated =
   started_at`; updates preserve `created` and set `updated = started_at`. Generate
   internal links with the resolved `OBSIDIAN_LINK_FORMAT`.
4. Register all reviewed deletions with
   `obsidian-wiki transaction delete <id> <vault-relative-page.md> --json --pretty`.
   Never delete a live page directly.
5. Run `obsidian-wiki transaction validate <id> --json --pretty`, fix every issue,
   review every warning and the complete candidate/deletion diff, then run
   `obsidian-wiki transaction commit <id> --json --pretty` only after validation
   passes.
6. Save the failed command envelope, including top-level `error` and `recovery`, on
   any failure. Inspect `recovery.preferred_action`. Trust its transaction ID only
   when present, then run `obsidian-wiki transaction list --json --pretty` and
   require exactly one retained record with the same ID and status. Follow only a
   reported `recommended_action` or entry in `allowed_actions`, after satisfying
   every string in its `requires` list. If the ID or list is empty, missing,
   mismatched, duplicated, or ambiguous, stop and report. Only a successful
   `transaction commit` or `transaction retry` is a knowledge commit.
7. Only after a successful `transaction commit` or `transaction retry`, run
   `obsidian-wiki hot status --json`. If stale, run
   `obsidian-wiki hot inputs --json --pretty`, write only the requested bounded hot
   candidate as a local derived artifact, then run
   `obsidian-wiki hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.

Do not edit manifest shards, operation records, stable `index.md`, or stable
`log.md`; do not run Git publication commands or write unsupported control paths.

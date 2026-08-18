---
name: wiki-rebuild
description: Use when rebuilding an explicit set of knowledge pages from declared repository sources or replacing drifted derived pages.
---

# Wiki Rebuild

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

A rebuild is a transaction-backed page rebuild, not a repository reset. It may
create, replace, or delete only an explicit page set derived from declared sources.
Inventory and intent confirmation are mandatory because rebuilds can replace
substantial reviewed content.

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

## Scope and planning

List every requested final vault-relative page, its authoritative Source IDs, its
disposition (`create`, `replace`, or `delete`), and affected inbound links. Read the
current page before replacing or deleting it. Preserve valid content, `created`,
Unicode filenames, and source-backed distinctions. A deletion must be individually
reviewed and accompanied by candidate backlink repairs when necessary.

Split large work into bounded transactions, each with a complete, independently
reviewable page set and source closure. Finish one transaction before planning the
next; never open overlapping workspaces. The phrase bounded transactions means each
batch is small enough for full candidate and deletion review, not an excuse for an
unbounded bulk mutation.

Plan batches sequentially. Batch 1 closes against the current live state. Each later
batch recomputes inventory and source closure from the current live state including
the previous successful batch. Permit no forward references to pages planned for a
later batch and no later backlink repairs: every committed batch must validate as a
self-contained graph. Order creations and replacements before any dependent removal,
and schedule deletions last after their backlink repairs are already in the same
batch. If any batch fails, stop all subsequent batches. The result is explicit:
previous successful commits remain retained; never roll them back implicitly.
Report partial completion, the remaining page set, and the failed transaction's
recovery state before returning.

Repository history restoration belongs to the owner through external Git history.
This skill does not restore historical states or clear repository content. Ask the
owner to select the historical revision and complete that operation outside this
runtime, then inventory the resulting current state before any derived-page rebuild.

Report scope, sources, preserved material, proposed replacements, and reviewed
deletions. Complete this read-only inventory and intent confirmation before the
first batch. If the explicit page set is empty, stop.

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
7. Only after a successful `transaction commit` or `transaction retry`, run
   `<wiki-cli> hot status --json`. If stale, run
   `<wiki-cli> hot inputs --json --pretty`, write only the requested tracked
   `hot.md` working-tree diff, then run
   `<wiki-cli> hot mark-current --json`. Do not refresh after abort, restore, or
   discard, and must not mark stale inputs current directly.

Do not edit manifest shards, `index.md`, or `log.md` directly; transaction commit
owns the canonical log append. Do not run Git publication commands or write unsupported control paths.
Do not commit, push, or open a pull request.

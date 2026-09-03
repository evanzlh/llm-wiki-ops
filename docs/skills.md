# Skills Reference

LLMWikiOps packages 36 skills. `setup` copies them into the repository's canonical `.skills/` tree and builds complete agent mirrors. Managed built-ins are upgraded with `llmwikiops repo upgrade-skills`; repository-authored custom skills are preserved.

Inside a wiki, repository-aware commands use nearest-ancestor CWD discovery. Outside a wiki, use an explicitly installed global adapter and mandatory `-C` / `--repo` on every repository-aware command.

The optional global Adapter is only a router. It contains no selected wiki path,
built-in skill catalog, or task bodies, and never installs the repository task
skill tree globally. After exact-root resolution, `check --json` returns one
Python-validated catalog containing managed and repository-authored custom skills.
The Agent selects from that repository-authoritative metadata and then reads the
complete selected body under the same immutable repository binding.

Updating this global router retains verified prior Adapter artifacts in the Agent configuration root's `.llmwikiops-retained/` evidence area. It is not routine cleanup: an Agent deletes retained evidence only after user confirmation, and no cleanup or uninstall CLI exists.

Ordinary task-scoped work completes automatically: an Agent may inspect, update, validate, and locally commit exact task-owned paths. Failed safety conditions trigger validate and recover steps without bypass, continuing only while structured state shows progress. Ask before external publication, destructive or work-losing actions, owner-overlapping changes, authority-expanding actions, or semantic decisions.

## Canonical protocol and review

- `llm-wiki` defines repository resolution, Source IDs, page schema, manifest v2, and the transaction-only write contract.
- `wiki-transaction-review` reviews candidate pages, deletions, validation reports, and prospective changes before the only completion step, `transaction commit`.

Every writing skill must invoke the canonical protocol, begin a transaction for exact source paths, write only inside `candidate_vault`, validate, review, and commit. Recovery follows current structured status and continues only while safe actions make progress. Direct mutation of the live vault or manifest shards is outside skill authority.

## Complete bundled inventory

History ingestion:

- `claude-history-ingest`
- `codex-history-ingest`
- `copilot-history-ingest`
- `hermes-history-ingest`
- `openclaw-history-ingest`
- `pi-history-ingest`
- `wiki-history-ingest`

Capture, ingestion, and synthesis:

- `daily-update`
- `wiki-capture`
- `wiki-import`
- `wiki-ingest`
- `wiki-research`
- `wiki-synthesize`
- `wiki-update`

Reading and navigation:

- `cross-linker`
- `graph-colorize`
- `session-brain`
- `session-search`
- `wiki-agent`
- `wiki-context-pack`
- `wiki-digest`
- `wiki-export`
- `wiki-narrate`
- `wiki-query`
- `wiki-status`

Quality and maintenance:

- `impl-validator`
- `obsidian-layout-adjustment`
- `tag-taxonomy`
- `wiki-dedup`
- `wiki-lint`
- `wiki-rebuild`
- `wiki-transaction-review`

Setup and extension:

- `llm-wiki`
- `skill-creator`
- `vault-skill-factory`
- `wiki-setup`

## Authoring and upgrades

Framework skill sources live at `obsidian_wiki/_data/skills/<name>/SKILL.md`. In a generated knowledge repository, custom skills belong in `.skills/<name>/SKILL.md`. Inspect mirror drift with `llmwikiops repo sync-skills --json --pretty`, then apply the reviewed `plan_token` with `llmwikiops repo sync-skills --apply --expected-plan TOKEN --json --pretty`. Run `doctor` and `check`, and review the tracked diff.

A managed upgrade refuses drift in built-ins and unknown historical content rather than overwriting owner work. The package currently has no Dashboard skill or compatibility placeholder; that capability remains a separate follow-up design.

## Local export and factory boundaries

`wiki-export` writes review artifacts only to ignored `.llmwikiops/local/exports/<timestamp>/`. Public export is metadata-first: it reads bounded frontmatter, excludes restricted visibility before any body read, and never discloses excluded identities. It can emit JSON, GraphML, Cypher, HTML, and an explicitly requested OKF bundle. It never changes knowledge, starts a transaction, or performs Git publication.

`vault-skill-factory` writes a review artifact first to ignored `.llmwikiops/local/generated-skills/<name>/`. It selects a confirmed mature cluster, preserves uncertainty and provenance, and validates the generated artifact with the repository-managed validator when safe. When installation is part of the request, request-scoped installation copies the reviewed artifact to the exact `.skills/<name>/` target, applies mirrors with `repo sync-skills --apply --expected-plan TOKEN`, reruns validation, and finishes with an exact-path local commit. Existing targets, ambiguous clusters, external dependencies, and owner-overlapping paths ask first; human review is optional unless requested or a semantic choice remains.

A local commit is not Git publication; ask before pushing, opening or merging a pull request, changing a remote, or rewriting history.

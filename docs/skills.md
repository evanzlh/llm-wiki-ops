# Skills Reference

LLMWikiOps packages 36 skills. `setup` copies them into the repository's canonical `.skills/` tree and builds complete agent mirrors. Managed built-ins are upgraded with `llmwikiops repo upgrade-skills`; repository-authored custom skills are preserved.

Inside a wiki, repository-aware commands use nearest-ancestor CWD discovery. Outside a wiki, use an explicitly installed global adapter and mandatory `-C` / `--repo` on every repository-aware command.

The optional global Adapter is only a router. It embeds the installed CLI's built-in names and descriptions, contains no selected wiki path or task bodies, and never installs the repository task skill tree globally. After the exact target repository is validated, direct `.skills/*/SKILL.md` frontmatter is loaded again: custom names extend the catalog, while repository-local skill metadata and body take precedence for a matching built-in name. If metadata differs, re-evaluate the route, then read the complete selected body from that repository under the same immutable repository binding.

Updating this global router retains verified prior Adapter artifacts in the Agent configuration root's `.llmwikiops-retained/` evidence area. Runtime skills have no authority to delete or garbage-collect that area; cleanup is a separate user-confirmed manual filesystem action.

## Canonical protocol and review

- `llm-wiki` defines repository resolution, Source IDs, page schema, manifest v2, and the transaction-only write contract.
- `wiki-transaction-review` reviews candidate pages, deletions, validation reports, and prospective changes before the only completion step, `transaction commit`.

Every writing skill must invoke the canonical protocol, begin a transaction for exact source paths, write only inside `candidate_vault`, validate, review, and commit once. Recovery follows the transaction status. Direct mutation of the live vault or manifest shards is outside skill authority.

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

Framework skill sources live at `obsidian_wiki/_data/skills/<name>/SKILL.md`. In a generated knowledge repository, custom skills belong in `.skills/<name>/SKILL.md`. Rebuild mirrors, run `doctor` and `check`, and review the tracked diff.

A managed upgrade refuses drift in built-ins and unknown historical content rather than overwriting owner work. The package currently has no Dashboard skill or compatibility placeholder; that capability remains a separate follow-up design.

## Local export and factory boundaries

`wiki-export` writes review artifacts only to ignored `.llmwikiops/local/exports/<timestamp>/`. Public export is metadata-first: it reads bounded frontmatter, excludes restricted visibility before any body read, and never discloses excluded identities. It can emit JSON, GraphML, Cypher, HTML, and an explicitly requested OKF bundle. It never changes knowledge, starts a transaction, or performs Git publication.

`vault-skill-factory` writes a review artifact only to ignored `.llmwikiops/local/generated-skills/<name>/`. It selects a confirmed mature cluster, preserves uncertainty and provenance, validates the generated artifact with the repository-managed validator when safe, and never installs the result. It never writes `.skills/` or any agent discovery directory; human review and a separate owner-controlled installation are required.

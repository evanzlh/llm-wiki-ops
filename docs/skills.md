# Skills Reference

The framework packages 36 skills. `setup` copies them into the repository's canonical `.skills/` tree and builds complete agent mirrors. Managed built-ins are upgraded with `obsidian-wiki repo upgrade-skills`; repository-authored custom skills are preserved.

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

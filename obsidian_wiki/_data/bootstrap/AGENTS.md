# LLMWikiOps — Repository Authority

The Config Resolution Protocol is repository-only: resolve the nearest ancestor
`.llmwikiops/config.toml`, keep its repository root as the working directory,
and read this file before wiki work. If config is absent, use
`llmwikiops setup [DIR]`; invalid config fails closed.

First load the `llm-wiki` skill as the canonical protocol, then load the
applicable task skill. If a task skill conflicts with the canonical protocol,
the canonical protocol takes precedence. All knowledge writes use CLI
transactions; direct vault, manifest, index, or log mutation is outside agent
authority. The sole exception to the direct live-vault mutation ban is a tracked
`wiki/hot.md` semantic refresh after a successful `transaction commit` or
`transaction retry`. Freshness status is read-only, may run at any time, and must
not remove the tracked file. Agents must not edit `wiki/log.md` directly;
transaction commit owns that authoritative file and appends its canonical operation
block last.

Treat owner changes as authoritative. The CLI does not perform Git publication;
commits, pushes, and pull requests require a separate owner decision.
Owners resolve ordinary Git conflicts in `log.md` and `hot.md`.

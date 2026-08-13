# Obsidian Wiki — Repository Authority

The Config Resolution Protocol is repository-only: resolve the nearest ancestor
`.obsidian-wiki/config.toml`, keep its repository root as the working directory,
and read this file before wiki work. If config is absent, use
`obsidian-wiki setup [DIR]`; invalid config fails closed.

`.skills/` is the canonical skill tree. First load
`.skills/llm-wiki/SKILL.md` as the canonical protocol, then load the applicable
`.skills/<task>/SKILL.md`. If a task skill conflicts with the canonical
protocol, the canonical protocol takes precedence. All knowledge writes use CLI
transactions; direct vault, manifest, index, or log mutation is outside agent
authority. Transaction commit owns the tracked authoritative `wiki/log.md` and
appends its canonical operation block last. The tracked `wiki/hot.md` is a derived
semantic view; freshness status is read-only and must not remove it.

Treat owner changes as authoritative. The CLI does not perform Git publication;
commits, pushes, and pull requests require a separate owner decision.
Owners resolve ordinary Git conflicts in `log.md` and `hot.md`.

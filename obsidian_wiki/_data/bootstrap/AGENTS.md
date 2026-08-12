# Obsidian Wiki — Repository Authority

The Config Resolution Protocol is repository-only: resolve the nearest ancestor
`.obsidian-wiki/config.toml`, keep its repository root as the working directory,
and read this file before wiki work. If config is absent, use
`obsidian-wiki setup [DIR]`; invalid config fails closed.

`.skills/` is the canonical skill tree. Read `.skills/llm-wiki/SKILL.md` for the
single source-authority and transaction protocol, then read the task-specific
skill. All knowledge writes use CLI transactions; direct vault, manifest, index,
or log mutation is outside agent authority.

Treat owner changes as authoritative. The CLI does not perform Git publication;
commits, pushes, and pull requests require a separate owner decision.

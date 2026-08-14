---
inclusion: always
---

# LLMWikiOps Repository Rules

Resolve the nearest ancestor `.obsidian-wiki/config.toml` and read repository
`AGENTS.md`. First load `.skills/llm-wiki/SKILL.md` as the canonical transaction
protocol, then load the applicable `.skills/<task>/SKILL.md`. The canonical
protocol takes precedence over conflicts. Missing or invalid configuration
stops wiki work.

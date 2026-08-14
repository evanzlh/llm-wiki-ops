---
inclusion: always
---

# LLMWikiOps Repository Rules

Resolve the nearest ancestor `.llmwikiops/config.toml` and read repository
`AGENTS.md`. First load the `llm-wiki` skill as the canonical transaction
protocol, then load the applicable task skill. The canonical protocol takes
precedence over conflicts. Missing or invalid configuration stops wiki work.

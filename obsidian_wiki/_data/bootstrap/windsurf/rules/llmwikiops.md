---
name: "LLMWikiOps"
activation: "always-on"
---

# LLMWikiOps Repository Rules

Resolve the nearest ancestor `.llmwikiops/config.toml`, then read repository
`AGENTS.md`. First load the `llm-wiki` skill as the canonical transaction
protocol, then load the applicable task skill. The canonical protocol takes
precedence over conflicts. Stop when configuration is missing or invalid.

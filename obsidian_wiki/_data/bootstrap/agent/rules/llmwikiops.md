---
alwaysApply: true
description: Delegate LLMWikiOps work to the repository runtime authority.
---

# LLMWikiOps Repository Rules

Resolve the nearest ancestor `.llmwikiops/config.toml`, then read repository
`AGENTS.md`. First load the `llm-wiki` skill as the canonical protocol, then load
the applicable task skill. The canonical protocol takes precedence over
conflicts. Invalid or missing configuration stops wiki work; all knowledge
writes use a transaction.

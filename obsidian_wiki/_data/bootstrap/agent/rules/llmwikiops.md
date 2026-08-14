---
alwaysApply: true
description: Delegate LLMWikiOps work to the repository runtime authority.
---

# LLMWikiOps Repository Rules

Resolve the nearest ancestor `.obsidian-wiki/config.toml`, then read repository
`AGENTS.md`. First load `.skills/llm-wiki/SKILL.md` as the canonical protocol,
then load the applicable `.skills/<task>/SKILL.md`. The canonical protocol takes
precedence over conflicts. Invalid or missing configuration stops wiki work;
all knowledge writes use a transaction.

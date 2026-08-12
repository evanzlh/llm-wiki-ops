---
name: "Obsidian Wiki"
activation: "always-on"
---

# Obsidian Wiki Repository Rules

Resolve the nearest ancestor `.obsidian-wiki/config.toml`, then read repository
`AGENTS.md`. First load `.skills/llm-wiki/SKILL.md` as the canonical transaction
protocol, then load the applicable `.skills/<task>/SKILL.md`. The canonical
protocol takes precedence over conflicts. Stop when configuration is missing or
invalid.

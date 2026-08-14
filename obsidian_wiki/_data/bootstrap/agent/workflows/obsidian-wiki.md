---
name: obsidian-wiki
description: Delegate wiki workflows to repository-owned skills.
commands:
  - name: wiki-query
    skill: .skills/wiki-query/SKILL.md
  - name: wiki-update
    skill: .skills/wiki-update/SKILL.md
  - name: wiki-ingest
    skill: .skills/wiki-ingest/SKILL.md
  - name: wiki-status
    skill: .skills/wiki-status/SKILL.md
---

# LLMWikiOps Workflow Registry

Resolve `.obsidian-wiki/config.toml` and read `AGENTS.md`. First load the
canonical transaction protocol in `.skills/llm-wiki/SKILL.md`, then load the
command's `.skills/<task>/SKILL.md`. The canonical protocol takes precedence
over conflicts.

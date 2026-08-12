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

# Obsidian Wiki Workflow Registry

Resolve `.obsidian-wiki/config.toml` and read `AGENTS.md` first. Every command
loads its task skill from `.skills/`, which delegates all writes to the canonical
transaction protocol in `.skills/llm-wiki/SKILL.md`.

---
name: llmwikiops
description: Delegate wiki workflows to repository-owned skills.
commands:
  - name: wiki-query
    skill: wiki-query
  - name: wiki-update
    skill: wiki-update
  - name: wiki-ingest
    skill: wiki-ingest
  - name: wiki-status
    skill: wiki-status
---

# LLMWikiOps Workflow Registry

Resolve `.llmwikiops/config.toml` and read `AGENTS.md`. First load the
`llm-wiki` skill as the canonical transaction protocol, then load the command's
task skill. The canonical protocol takes precedence over conflicts.

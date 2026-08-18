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

Use one repository context for the whole workflow. Resolve the nearest ancestor
`.llmwikiops/config.toml` locally, or validate a user-supplied exact external
root with `llmwikiops -C <root> info --json`.

Once validated, keep `<root>` immutable for the whole workflow.

Read authority in this exact order:

1. `<root>/AGENTS.md`
2. `<root>/.skills/llm-wiki/SKILL.md`
3. `<vault>/AGENTS.md` when present
4. `<root>/.skills/<selected-task>/SKILL.md`

Target repository metadata overrides the adapter's generated snapshot and forces route reevaluation.
Load the `llm-wiki` skill as canonical, then the selected task skill.
The canonical protocol takes precedence over task-skill conflicts. All
knowledge writes use transactions.

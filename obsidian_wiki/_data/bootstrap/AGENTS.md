# LLMWikiOps — Repository Authority

The Config Resolution Protocol establishes one immutable repository binding.

Use one repository context for the whole workflow. Inside a wiki, resolve the
nearest ancestor `.llmwikiops/config.toml`. Outside a wiki, require a
user-supplied exact root, validate it with `llmwikiops -C <root> info --json`,
and retain that immutable binding. Never infer or switch roots.

Once validated, keep `<root>` immutable for the whole workflow.

Read authority in this exact order:

1. `<root>/AGENTS.md`
2. `<root>/.skills/llm-wiki/SKILL.md`
3. `<vault>/AGENTS.md` when present
4. `<root>/.skills/<selected-task>/SKILL.md`

Target repository metadata overrides the adapter's generated snapshot and forces route reevaluation.
Load the `llm-wiki` skill as canonical, then the selected task skill.
The canonical protocol takes precedence over task-skill conflicts. All knowledge
writes use CLI transactions; direct vault, manifest, index, or log mutation is
outside agent authority. The sole exception to the direct live-vault mutation ban is a tracked
`wiki/hot.md` semantic refresh after a successful `transaction commit` or
`transaction retry`. Freshness status is read-only, may run at any time, and must
not remove the tracked file. Agents must not edit `wiki/log.md` directly;
transaction commit owns that authoritative file and appends its canonical operation
block last.

Treat owner changes as authoritative. The CLI does not perform Git publication;
commits, pushes, and pull requests require a separate owner decision.
Owners resolve ordinary Git conflicts in `log.md` and `hot.md`.

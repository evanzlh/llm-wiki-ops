# Bootstrap Skill Name Routing Design

## Goal

Make every generated agent bootstrap refer to runtime skills by skill name rather
than by the canonical `.skills/<name>/SKILL.md` storage path. Each supported agent
can then resolve the skill through its own managed discovery directory.

## Scope

Update all seven resources under `obsidian_wiki/_data/bootstrap/`:

- the repository `AGENTS.md` template;
- Agent rules and workflow registry;
- Cursor rules;
- GitHub Copilot instructions;
- Kiro steering;
- Windsurf rules.

The bootstrap instructions will require agents to load the `llm-wiki` skill
first, followed by the applicable task skill. The `llm-wiki` protocol continues
to take precedence over conflicting task-skill instructions. Agent workflow
registry entries will likewise identify `wiki-query`, `wiki-update`,
`wiki-ingest`, and `wiki-status` by name.

This change does not alter the canonical `.skills/` storage tree, managed mirror
creation, synchronization, upgrade ownership, or runtime skill contents.

## Behavior

Bootstrap files must not direct an agent to load a skill from `.skills/` or name
a `SKILL.md` path. They may still direct the agent to read repository
`AGENTS.md` and resolve `.llmwikiops/config.toml`, because those are repository
authority and configuration files rather than skills.

Setup continues to install the same managed bootstrap files. Only their managed
content changes, so existing owner-authored content outside managed blocks keeps
the current preservation behavior.

## Testing

Regression tests will verify both package resources and the output of
`setup_portable_repo`:

- every bootstrap uses the `llm-wiki` name before the task-skill reference;
- no bootstrap contains a `.skills/` skill-loading path;
- workflow registry `skill:` values are bare skill names;
- setup still preserves owner content and managed-block markers.

Focused setup and bootstrap contract tests will run first, followed by the full
test suite and README synchronization check.

# Agent Compatibility

Knowledge repositories carry their own agent instructions and skills. No user-global wiki installation is required.

## Discovery layout

`.skills/` is the canonical tracked tree. Setup creates complete ordinary-file mirrors for Claude (`.claude/skills/`), Cursor (`.cursor/skills/`), Windsurf (`.windsurf/skills/`), Codex-compatible agents (`.agents/skills/`), Pi (`.pi/skills/`), and Kiro (`.kiro/skills/`). Root bootstrap files direct each agent to the same protocol.

Edit only `.skills/`. Use `obsidian-wiki repo sync-skills` to inspect drift and `obsidian-wiki repo sync-skills --apply` to rebuild all mirrors.

## Authority protocol

Before wiki work, an agent must:

1. Resolve the nearest ancestor `.obsidian-wiki/config.toml` and keep its repository root as the command working directory.
2. Read the repository bootstrap instructions.
3. Read `.skills/llm-wiki/SKILL.md` as the canonical protocol.
4. Read the one or more task skills needed for the request.
5. Treat owner edits and tracked source bytes as authoritative.

The canonical protocol wins if a task skill conflicts with it.

## Reads

Use `query`, `context-pack`, graph tools, or direct reads explicitly permitted by the selected skill. Public-only options filter restricted visibility before body reads. Repository-relative identities belong in tracked content; resolved absolute paths are runtime values only.

## Writes

All knowledge writes use CLI transactions:

```bash
obsidian-wiki transaction begin --source sources/example.md --json --pretty
obsidian-wiki transaction validate <transaction-id> --json --pretty
obsidian-wiki transaction commit <transaction-id> --json --pretty
obsidian-wiki check
```

Write complete candidate pages beneath the returned `candidate_vault`. Do not write the live vault, manifest shards, stable index/log files, or operation records directly. Transaction review happens after validation and before commit. If a command retains recovery state, follow its reported `retry`, `restore`, `discard`, or `abort` action instead of starting an unrelated write.

For the local recent-activity view, use `hot status`, collect `hot inputs` when stale, rewrite ignored `wiki/hot.md` semantically, then run `hot mark-current`.

## Git boundary

Transaction commands stop at a reviewable working-tree diff. They do not commit, push, modify remotes, or open pull requests. Git publication requires an external owner decision.

## Dashboard boundary

No Dashboard or placeholder skill is installed. Adding one later requires a separate design; agents must not infer an extension point from its absence.

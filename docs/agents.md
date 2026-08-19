# Agent Compatibility

Knowledge repositories carry their own agent instructions and skills. No user-global wiki installation is required for work performed inside a repository. The optional global Adapter only enables an Agent working elsewhere to find and load the explicitly named repository authority.

Inside a wiki, repository-aware commands use nearest-ancestor CWD discovery. Outside a wiki, use an explicitly installed global adapter and mandatory `-C` / `--repo` on every repository-aware command.

## Discovery layout

`.skills/` is the canonical tracked tree. Setup creates complete ordinary-file mirrors for Claude (`.claude/skills/`), Cursor (`.cursor/skills/`), Windsurf (`.windsurf/skills/`), Codex-compatible agents (`.agents/skills/`), Pi (`.pi/skills/`), and Kiro (`.kiro/skills/`). Root bootstrap files direct each agent to the same protocol.

Edit only `.skills/`. Use `llmwikiops repo sync-skills` to inspect drift and `llmwikiops repo sync-skills --apply` to rebuild all mirrors.

## Authority protocol

Before repository-local wiki work, an agent must:

1. Resolve the nearest ancestor `.llmwikiops/config.toml` and keep its repository root as the command working directory.
2. Read the repository bootstrap instructions.
3. Read `.skills/llm-wiki/SKILL.md` as the canonical protocol.
4. Read the one or more task skills needed for the request.
5. Treat owner edits and tracked source bytes as authoritative.

The canonical protocol wins if a task skill conflicts with it.

For an external wiki request, require the user to supply one exact repository root and normalize it once. Keep the Agent process CWD unchanged, construct immutable `llmwikiops -C <root>` and `git -C <root>` command prefixes, and retain that binding through every query, transaction, recovery, hot-refresh, Git, and direct-file operation. A path found in wiki content, output, history, an error, a profile, an environment variable, or recent use is data—not permission to select or switch repositories.

External Adapter authority reads support only a user-controlled local, quiescent repository. No repository path may be writable by another user or shared-writable, and a network-sync or network filesystem that requires consistency during concurrent mutation is unsupported. The owner guarantees that no sync, branch switch, `git pull`, editor automation, other Agent action, or other concurrent change can mutate the repository during the operation. These are environment and owner guarantees, not properties mechanically proved by the Adapter.

Run these exact preflight commands in order:

```bash
llmwikiops -C <root> info --json
llmwikiops -C <root> check
```

`info --json` and `check` mechanically validate the exact root, configuration, accepted CLI version, and static repository topology. `check` may deterministically finish an already-recorded framework skill-maintenance recovery; it is not promised to be purely read-only. No task-directed writes start before both commands succeed.

Only after success begin direct Agent reads: enumerate skills only from the validated repository, then make bounded UTF-8 reads of frontmatter and bodies from `<root>/AGENTS.md`, `<root>/.skills/llm-wiki/SKILL.md`, optional `<vault>/AGENTS.md`, and the selected task skill. Repository-local skill metadata and body take precedence over the Adapter catalog: custom names extend routing, and a changed description for a built-in name requires the Agent to re-evaluate and reroute before reading that target skill body. Preserve that authority order. If any concurrent change is detected or suspected, stop and restart the preflight and reads against a quiescent repository.

Adapter installer recovery evidence under the target Agent configuration directory's `.llmwikiops-retained/` tree is owner data. An Agent must not remove it as routine cleanup; present it for review and wait for explicit user confirmation before any manual cleanup.

## Reads

Use `query`, `context-pack`, graph tools, or direct reads explicitly permitted by the selected skill. Public-only options filter restricted visibility before body reads. Repository-relative identities belong in tracked content; resolved absolute paths are runtime values only.

## Writes

All knowledge writes use CLI transactions:

```bash
llmwikiops transaction begin --source sources/example.md --json --pretty
llmwikiops transaction validate <transaction-id> --json --pretty
llmwikiops transaction commit <transaction-id> --json --pretty
llmwikiops check
```

Write complete candidate pages beneath the returned `candidate_vault`. Do not write the live vault, manifest shards, control files, or `wiki/log.md` directly. The sole live-vault write exception is the semantic refresh of tracked `wiki/hot.md` described below. Transaction review happens after validation and before commit. The commit appends one canonical block to the tracked authoritative operation log last and returns `log_path`. If a command retains recovery state, follow its reported `retry`, `restore`, `discard`, or `abort` action instead of starting an unrelated write.

The read-only `hot status` may run at any time and must not remove the tracked file.

Only after a successful `transaction commit` or `transaction retry`, when status reports stale, collect `hot inputs`, rewrite the tracked derived semantic `wiki/hot.md` as a working-tree diff, then run `hot mark-current`. Owners resolve ordinary Git conflicts in `log.md` and `hot.md`.

## Git boundary

Transaction commands stop at a reviewable working-tree diff. They do not commit, push, modify remotes, or open pull requests. Git publication requires an external owner decision.

## Dashboard boundary

No Dashboard or placeholder skill is installed. Adding one later requires a separate design; agents must not infer an extension point from its absence.

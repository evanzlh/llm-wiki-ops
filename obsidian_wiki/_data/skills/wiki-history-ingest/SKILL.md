---
name: wiki-history-ingest
description: Use when selecting the supported tool-specific skill for coding-agent history input.
---

# History Ingest Router

This skill is a thin route to one retained tool-specific skill. It does not parse sessions, does not create snapshots, does not begin transactions, and does not mutate repository, vault, or tracking state.

## Routes

- Claude paths, Claude Code JSONL, Desktop local-agent sessions -> `claude-history-ingest`
- Codex rollout or session-index artifacts -> `codex-history-ingest`
- Copilot CLI, VS Code chat, or `session-store.db` -> `copilot-history-ingest`
- Hermes memory/session artifacts -> `hermes-history-ingest`
- OpenClaw memory/session artifacts -> `openclaw-history-ingest`
- Pi agent session JSONL -> `pi-history-ingest`

If the user names one tool, route directly. If a supplied input unambiguously matches one route, state the selection and invoke that skill. Otherwise ask which supported tool produced the history. Unknown tools are NEEDS_CONTEXT; do not improvise a parser or a generic write path.

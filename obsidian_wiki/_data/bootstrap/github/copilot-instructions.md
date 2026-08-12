# Obsidian Wiki Repository Instructions

Resolve the nearest ancestor `.obsidian-wiki/config.toml`, keep the repository
root as the working directory, and read `AGENTS.md`. Load task instructions from
`.skills/`, with `.skills/llm-wiki/SKILL.md` as the canonical source-authority
and transaction protocol. Fail closed on missing or invalid configuration; do
not write vault or tracking files outside a CLI transaction.

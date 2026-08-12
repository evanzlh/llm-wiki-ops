# Obsidian Wiki Repository Instructions

Resolve the nearest ancestor `.obsidian-wiki/config.toml`, keep the repository
root as the working directory, and read `AGENTS.md`. First load
`.skills/llm-wiki/SKILL.md` as the canonical transaction protocol, then load the
applicable `.skills/<task>/SKILL.md`. The canonical protocol takes precedence
over conflicts. Fail closed on missing or invalid configuration.

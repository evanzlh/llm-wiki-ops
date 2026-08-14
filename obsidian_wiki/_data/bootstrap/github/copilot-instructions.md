# LLMWikiOps Repository Instructions

Resolve the nearest ancestor `.llmwikiops/config.toml`, keep the repository
root as the working directory, and read `AGENTS.md`. First load
the `llm-wiki` skill as the canonical transaction protocol, then load the
applicable task skill. The canonical protocol takes precedence over conflicts.
Fail closed on missing or invalid configuration.

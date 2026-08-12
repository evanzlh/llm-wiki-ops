# Agent Compatibility

Works with **any AI coding agent that can read files**. First install the CLI through the supported [source-clone flow](installation.md#install-from-a-clone). Then use `obsidian-wiki setup` for personal mode or `obsidian-wiki setup --portable <path>` to create a portable repository.

Each agent has its own convention for discovering skills. Personal setup connects the CLI's bundled skills to agent-wide locations. Portable setup writes tracked repository-local skills and bootstrap files so collaborators get the same instructions from the knowledge repository.

In a portable repository, `.skills/` is the only editable canonical skill tree.
The directories `.claude/skills/`, `.cursor/skills/`, `.windsurf/skills/`,
`.agents/skills/`, `.pi/skills/`, and `.kiro/skills/` are complete derived
ordinary-file mirrors, not symlinks or abbreviated forwarding files. You must
never edit an agent mirror directly; inspect drift with `obsidian-wiki repo sync-skills`
and rebuild all mirrors explicitly with `--apply`. The Config Resolution
Protocol in `AGENTS.md` gives tracked
`.obsidian-wiki/config.toml` precedence over personal `.env` and global config.

## Matrix

| Agent | Bootstrap | Skills Directory | Slash Commands |
|---|---|---|---|
| **[Claude Code](https://claude.ai/code)** | `CLAUDE.md` | `.claude/skills/` + `~/.claude/skills/` | ✅ `/wiki-ingest`, `/wiki-status`, etc. |
| **[Cursor](https://cursor.com)** | `.cursor/rules/obsidian-wiki.mdc` | `.cursor/skills/` | ✅ `/wiki-ingest`, `/wiki-status`, etc. |
| **[Windsurf](https://windsurf.com)** | `.windsurf/rules/obsidian-wiki.md` | `.windsurf/skills/` | ✅ via Cascade |
| **[Codex (OpenAI)](https://openai.com/codex)** | `AGENTS.md` | `~/.codex/skills/` | `$wiki-ingest` (Codex uses `$`) |
| **[Gemini CLI](https://github.com/google-gemini/gemini-cli)** | `GEMINI.md` | `~/.gemini/skills/` | ✅ `/wiki-ingest`, `/wiki-query`, etc. |
| **[Google Antigravity](https://antigravity.google)** | `.agent/rules/` + `.agent/workflows/` | `.agents/skills/` | ✅ via workflows registry |
| **[Kiro IDE/CLI](https://kiro.dev)** | `.kiro/steering/obsidian-wiki.md` | `.kiro/skills/` + `~/.kiro/skills/` | ✅ `/wiki-ingest`, `/wiki-status`, etc. |
| **[Hermes](https://hermes-agent.nousresearch.com)** | `.hermes.md` | `~/.hermes/skills/` | ✅ `/wiki-history-ingest hermes`, etc. |
| **[OpenClaw](https://openclaw.ai)** | `AGENTS.md` | `~/.openclaw/skills/` + `~/.agents/skills/` | ✅ `/wiki-ingest`, `/wiki-history-ingest openclaw`, etc. |
| **[OpenCode](https://opencode.ai)** | `AGENTS.md` | `~/.agents/skills/` | ✅ `/wiki-ingest`, `/wiki-query`, etc. |
| **[Aider](https://aider.chat)** | `AGENTS.md` | `~/.agents/skills/` | Describe intent in chat |
| **[Factory Droid](https://factory.ai)** | `AGENTS.md` | `~/.agents/skills/` | ✅ `/wiki-ingest`, `/wiki-query`, etc. |
| **[Trae](https://trae.ai)** / **Trae CN** | `AGENTS.md` | `~/.trae/skills/` / `~/.trae-cn/skills/` | ✅ via Agent tool |
| **GitHub Copilot (VS Code)** | `.github/copilot-instructions.md` | — | Describe intent in chat |
| **GitHub Copilot (CLI)** | — | `~/.copilot/skills/` | ✅ `/wiki-ingest`, `/wiki-query`, etc. |
| **[Kilocode](https://kilo.ai/)** | `AGENTS.md` / `CLAUDE.md` | `.agents/skills/` + `.claude/skills/` | ✅ `/wiki-ingest`, `/wiki-status`, etc. |
| **[Pi](https://pi.dev)** | `AGENTS.md` | `.pi/skills/` + `~/.pi/agent/skills/` | ✅ `/wiki-ingest`, `/wiki-history-ingest pi`, etc. |

> Slash commands work in Claude Code, Cursor, Windsurf, and most CLI agents. Everywhere else, just describe what you want — the agent matches your intent against the skill descriptions.

Named-vault routing (`@work update wiki`) works in every agent above, because `@name` is documented in the shared skills and bootstrap context that all of them load.

## Setup modes

Use the installed CLI for both supported modes:

```bash
obsidian-wiki setup --vault ~/brain
obsidian-wiki setup --portable ./team-knowledge
```

The first command configures personal, agent-wide discovery on the current machine. The second creates a clone-ready repository without writing global config or global agent directories. The agent-specific notes below describe what the CLI wires.

Each collaborator installs the CLI independently from a framework clone with
`uv tool install --link-mode copy .`; the knowledge repository itself does not contain `.venv`
or a CLI runtime. Linux and macOS are the first-release CLI support boundary,
while committed skill mirrors and configuration use platform-neutral relative paths.

Portable setup installs no automatic conversation capture. In particular,
automatic Stop capture is not installed; use `/wiki-capture --quick` when you
explicitly want to preserve a small finding. If an older release added a global
Claude hook, remove the old global Claude Stop Hook entry manually from your
user-level Claude settings. Also remove its referenced legacy script if you no
longer use it. Do not copy a Hook configuration into the knowledge repository.

## Portable agent write protocol

Repository-local skills resolve `.obsidian-wiki/config.toml` before personal
configuration and branch immediately on Portable Repository mode. Write skills
provide explicit adjacent Portable Repository completion and Personal mode
completion branches; the agent follows exactly one branch and stops at its
boundary.

Keep the repository root as the command working directory. A write agent starts
one CLI transaction with the complete authoritative source closure, records the
returned `candidate_vault` as a runtime-only absolute path; do not `cd` into
it. It writes final vault-relative pages below that destination, declares
deletions through the CLI, and uses the transaction `started_at`
deterministically: a new page sets `created = updated = started_at`; an updated
page must preserve `created` and set `updated = started_at`.

The agent must validate before commit, review `candidate_pages`, `deletions`,
`issues`, and `warnings`, and promote only a passing report. It never edits live
knowledge pages, manifest shards, or operation pages by hand, and it never
persists the runtime absolute candidate path in a page or repository config.

An edited source worktree is expected and does not block the agent. Promotion
checks begin-time preimages only for affected live output pages and manifest
shards. If one drifted, the transaction is retained: the agent reports it and
uses status-aware recovery—an explicit retry, restore, abort, or discard action
instead of creating a replacement transaction with an ambiguous outcome.

Portable `index.md` and `log.md` are stable collaboration surfaces. Agents do
not update them for ordinary writes; built-in query/status behavior reads pages,
shards, and immutable `journal/operations/**` entries. `hot.md` is ignored local
derived state: agents run `obsidian-wiki hot status --json`, rebuild it only
when stale, and finish with `obsidian-wiki hot mark-current --json`.
The full hot freshness gate is `hot status` → `hot inputs` → semantic rewrite
→ `hot mark-current`; the CLI gathers and fingerprints deterministic inputs
while the agent remains responsible for the prose.

Agents stop after updating the working tree. They do not commit, push, or open
a pull request for portable knowledge changes. Humans review the Git diff and
use the repository's branch/PR policy as the content approval boundary.

<details>
<summary><b>Claude Code</b></summary>

Skills are auto-discovered from `.claude/skills/`. Personal setup connects the installed bundled skills there; portable repositories track the complete `.claude/skills/` mirror. The `CLAUDE.md` file at the repo root is automatically loaded as project context.

```bash
cd /path/to/obsidian-wiki && claude "set up my wiki"
```
</details>

<details>
<summary><b>Cursor</b></summary>

Skills are auto-discovered from `.cursor/skills/`. The `.cursor/rules/obsidian-wiki.mdc` file provides always-on context. Personal or portable CLI setup creates the matching integration. Then type `/wiki-setup` in the chat.
</details>

<details>
<summary><b>Windsurf</b></summary>

Cascade reads rules from `.windsurf/rules/` and skills from `.windsurf/skills/`. Personal or portable CLI setup creates the matching integration. Then tell Cascade: "set up my wiki".
</details>

<details>
<summary><b>Codex</b></summary>

Reads `AGENTS.md` for project context. Personal CLI setup connects the installed bundled skills at `~/.codex/skills/`; portable repositories supply tracked local instructions.

```bash
cd /path/to/obsidian-wiki && codex "set up my wiki"
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

Reads `GEMINI.md` and discovers global skills from `~/.gemini/skills/`. Personal CLI setup connects the installed bundled skills; portable repositories supply tracked local instructions.

```bash
cd /path/to/obsidian-wiki && gemini "set up my wiki"
```
</details>

<details>
<summary><b>Google Antigravity</b></summary>

Always-on via `.agent/rules/` + `.agent/workflows/`. CLI setup supplies both files and the `.agents/skills/` integration. Personal mode also supports the legacy `~/.gemini/antigravity/skills/` path.
</details>

<details>
<summary><b>Kiro IDE/CLI</b></summary>

Always-on via `.kiro/steering/*.md` with `inclusion: always`. CLI setup supplies the appropriate repository-local or personal skill integration. Invoke with `/wiki-ingest`, `/wiki-query`, etc.
</details>

<details>
<summary><b>OpenCode / Aider / Factory Droid / Trae</b></summary>

All read `AGENTS.md` at the repo root. Personal CLI setup uses `~/.agents/skills/` as the shared discovery path and also connects Trae's dedicated skill locations. Portable repositories keep the corresponding instructions local.
</details>

<details>
<summary><b>Hermes</b></summary>

Reads `.hermes.md` first, then falls back to `AGENTS.md`. Skills are discovered from `~/.hermes/skills/` in personal mode or the tracked repository integration in portable mode.

```bash
cd /path/to/obsidian-wiki && hermes "set up my wiki"
# Mine Hermes history into the wiki:
/wiki-history-ingest hermes
```
</details>

<details>
<summary><b>OpenClaw</b></summary>

Reads `AGENTS.md` (priority 10). Discovers skills from `~/.openclaw/skills/` and `~/.agents/skills/`. Skills auto-register as slash commands.

```bash
cd /path/to/obsidian-wiki && openclaw "set up my wiki"
# Mine OpenClaw history:
/wiki-history-ingest openclaw
```
</details>

<details>
<summary><b>GitHub Copilot</b></summary>

**VS Code Chat:** reads `.github/copilot-instructions.md`. Say "set up my wiki" in Copilot Chat.

**CLI:** discovers skills from `~/.copilot/skills/`; personal CLI setup connects the bundled skills there, while portable repositories carry their own integration.
</details>

<details>
<summary><b>Pi</b></summary>

Reads `AGENTS.md` (walking up from cwd). Discovers skills from `.pi/skills/`, `.agents/skills/`, and `~/.pi/agent/skills/`; CLI setup populates the locations appropriate to personal or portable mode.

```bash
cd /path/to/obsidian-wiki && pi "set up my wiki"
# Mine Pi session history:
/wiki-history-ingest pi
```
</details>

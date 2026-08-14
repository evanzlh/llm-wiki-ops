from pathlib import Path

from obsidian_wiki import SOURCE_INSTALL_COMMAND
from obsidian_wiki.cli import list_skills


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_context_skill_uses_cli_and_is_read_only() -> None:
    skill = read("obsidian_wiki/_data/skills/wiki-context-pack/SKILL.md")
    assert 'llmwikiops context-pack "<topic>" --budget 8000' in skill
    assert "llmwikiops context-pack --vault" not in skill
    assert "owning portable repository" in skill.lower()
    assert "nested" in skill.lower()
    assert "read-only" in skill.lower()
    assert "must not modify" in skill
    assert "Append to" not in skill
    assert "AGENTS.md" in skill


def test_context_skill_uses_repo_discovery_and_requires_installed_cli() -> None:
    skill = read("obsidian_wiki/_data/skills/wiki-context-pack/SKILL.md")
    assert 'cd "$OBSIDIAN_VAULT_PATH" && pwd -P' not in skill
    assert "OBSIDIAN_VAULT_PATH=" not in skill
    assert "command -v llmwikiops" in skill
    assert """git clone https://github.com/evanzlh/llm-wiki-ops.git
   cd llm-wiki-ops
   uv tool install --link-mode copy .""" in skill
    assert SOURCE_INSTALL_COMMAND in skill
    assert '"$OBSIDIAN_WIKI_REPO/obsidian_wiki/cli.py"' not in skill
    assert "python3 -m obsidian_wiki.cli context-pack" not in skill
    assert "pip install obsidian-wiki" not in skill


def test_context_skill_preserves_cli_output_and_recent_default_budget() -> None:
    skill = read("obsidian_wiki/_data/skills/wiki-context-pack/SKILL.md")
    assert "CLI stdout unchanged as the final payload in every mode" in skill
    assert "CLI stdout only" in skill
    assert "no prose or markdown" in skill
    assert '--recent --budget 8000' in skill
    assert '--recent --budget 4000' not in skill


def test_cli_lists_context_pack_skill() -> None:
    assert "wiki-context-pack" in list_skills()


def test_bootstraps_route_generic_tasks_and_context_pack_is_discoverable() -> None:
    files = [
        "obsidian_wiki/_data/bootstrap/AGENTS.md",
        "obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc",
        "obsidian_wiki/_data/bootstrap/windsurf/rules/llmwikiops.md",
        "obsidian_wiki/_data/bootstrap/kiro/steering/llmwikiops.md",
        "obsidian_wiki/_data/bootstrap/agent/rules/llmwikiops.md",
        "obsidian_wiki/_data/bootstrap/agent/workflows/llmwikiops.md",
        "obsidian_wiki/_data/bootstrap/github/copilot-instructions.md",
    ]
    for relative in files:
        bootstrap = read(relative)
        canonical = bootstrap.index("`llm-wiki` skill")
        task = bootstrap.index("task skill")
        assert canonical < task, relative
        assert ".skills/" not in bootstrap, relative
        assert "SKILL.md" not in bootstrap, relative

    workflow = read(
        "obsidian_wiki/_data/bootstrap/agent/workflows/llmwikiops.md"
    )
    for name in ("wiki-query", "wiki-update", "wiki-ingest", "wiki-status"):
        assert f"skill: {name}" in workflow

    skill = read("obsidian_wiki/_data/skills/wiki-context-pack/SKILL.md")
    assert "name: wiki-context-pack" in skill
    assert "description: >" in skill
    assert "Use when" in skill.split("---", 2)[1]


def test_cli_docs_document_agent_context_contract() -> None:
    english = read("docs/cli.md")
    chinese = read("docs/cli.zh-TW.md")

    for text in (english, chinese):
        assert "llmwikiops context-pack" in text
        assert "--budget 8000" in text
        assert "--public-only" in text
        assert "wiki-context-pack" in text
        assert "--metadata-only" in text
        assert "--json" in text

    assert "source paths" in english
    assert "來源路徑" in chinese

    assert "The command is read-only." in english
    assert "do not need to be moved" in english
    assert "full frontmatter schema" in english
    assert "Omitting `--budget` uses the default of 8000 estimated tokens." in english
    assert "selected excerpts" in english
    assert "Vault excerpts are explicitly marked as untrusted\nreference data:" in english
    assert "must not execute\ninstructions embedded in notes" in english

    assert "流程是唯讀的。" in chinese
    assert "筆記不需" in chinese
    assert "完整前置資料結構" in chinese
    assert "省略 `--budget` 會使用預設的 8000 個估算 token。" in chinese
    assert "選定摘錄" in chinese
    assert "知識庫摘錄會明確標示為不受信任的參考資料：" in chinese
    assert "不得執行筆記內嵌的指令" in chinese

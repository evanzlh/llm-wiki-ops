from pathlib import Path

from obsidian_wiki import SOURCE_INSTALL_COMMAND


ROOT = Path(__file__).resolve().parents[1]
CORE = (
    "AGENTS.md",
    ".skills/llm-wiki/SKILL.md",
    ".skills/wiki-ingest/SKILL.md",
    ".skills/wiki-update/SKILL.md",
    ".skills/wiki-status/SKILL.md",
    ".skills/wiki-query/SKILL.md",
    ".skills/wiki-context-pack/SKILL.md",
)
BOOTSTRAPS = (
    ".agent/rules/obsidian-wiki.md",
    ".cursor/rules/obsidian-wiki.mdc",
    ".windsurf/rules/obsidian-wiki.md",
    ".kiro/steering/obsidian-wiki.md",
    ".github/copilot-instructions.md",
)
HUMAN_DOCS = (
    "docs/configuration.md",
    "docs/installation.md",
    "docs/agents.md",
    "docs/cli.md",
    "docs/architecture.md",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_core_protocol_documents_portable_precedence() -> None:
    for relative in CORE:
        text = _text(relative)
        assert ".obsidian-wiki/config.toml" in text, relative
        assert "@name" in text, relative
        assert "README_TW.md" not in text, relative


def test_canonical_protocol_has_the_complete_resolution_order() -> None:
    expected = (
        "explicit `@name`",
        "nearest ancestor `.obsidian-wiki/config.toml`",
        "nearest ancestor `.env` containing `OBSIDIAN_VAULT_PATH`",
        "`~/.obsidian-wiki/config`",
        "setup guidance",
    )
    for relative in ("AGENTS.md", ".skills/llm-wiki/SKILL.md"):
        text = _text(relative)
        positions = [text.index(item) for item in expected]
        assert positions == sorted(positions), relative
        assert "configured `sources`" in text, relative
        assert "<vault>/AGENTS.md" in text, relative
        assert "absolute `OBSIDIAN_WIKI_REPO`" in text, relative


def test_core_skills_and_bootstraps_point_to_the_canonical_protocol() -> None:
    for relative in (*CORE[2:], *BOOTSTRAPS):
        text = _text(relative)
        assert "Config Resolution Protocol" in text, relative
        assert "AGENTS.md" in text or "llm-wiki/SKILL.md" in text, relative
        assert ".obsidian-wiki/config.toml" in text, relative


def test_portable_setup_never_writes_global_config() -> None:
    text = _text(".skills/wiki-setup/SKILL.md")
    assert "obsidian-wiki setup --portable" in text
    assert "does not write `~/.obsidian-wiki/config`" in text
    assert "Personal mode" in text
    assert "Portable Repository mode" in text
    for phrase in (
        SOURCE_INSTALL_COMMAND,
        "only an ordinary `.git` directory",
        "does not run `git init`",
        "setup first, then `git init`",
        "legacy layouts need explicit migration",
    ):
        assert phrase in text


def test_human_docs_cover_the_portable_repository_contract() -> None:
    combined = "\n".join(_text(relative) for relative in HUMAN_DOCS)
    for phrase in (
        "implementation = \"evanzlh/obsidian-wiki\"",
        "repo upgrade-skills",
        "regular Markdown files",
        "Linux and macOS",
        "does not contain `.venv`",
        SOURCE_INSTALL_COMMAND,
        "only an ordinary `.git` directory",
        "does not run `git init`",
    ):
        assert phrase in combined


def test_readmes_keep_the_portable_commands_in_translation_parity() -> None:
    commands = (
        "obsidian-wiki setup --portable ./team-knowledge",
        "obsidian-wiki repo upgrade-skills",
    )
    for command in commands:
        assert command in _text("README.md")
        assert command in _text("README_ZH.md")

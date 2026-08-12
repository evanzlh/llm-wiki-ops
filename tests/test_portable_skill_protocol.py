from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "obsidian_wiki/_data/skills/llm-wiki/SKILL.md"
SETUP = "obsidian_wiki/_data/skills/wiki-setup/SKILL.md"
BOOTSTRAPS = (
    "obsidian_wiki/_data/bootstrap/AGENTS.md",
    "obsidian_wiki/_data/bootstrap/agent/rules/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/agent/workflows/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/cursor/rules/obsidian-wiki.mdc",
    "obsidian_wiki/_data/bootstrap/github/copilot-instructions.md",
    "obsidian_wiki/_data/bootstrap/kiro/steering/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/windsurf/rules/obsidian-wiki.md",
)
FORBIDDEN_RUNTIME_TERMS = (
    "Personal mode",
    "Portable Repository mode",
    "@name",
    "~/.obsidian-wiki/config",
    "WIKI_STAGED_WRITES",
    "cache-update",
    "QMD_",
)


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_canonical_runtime_has_no_mode_or_legacy_branches() -> None:
    canonical = text(CANONICAL)
    for forbidden in FORBIDDEN_RUNTIME_TERMS:
        assert forbidden not in canonical


def test_bootstraps_delegate_to_repository_authority() -> None:
    for relative in BOOTSTRAPS:
        bootstrap = text(relative)
        for required in ("config.toml", "transaction"):
            assert required in bootstrap, f"{relative}: missing {required!r}"
        assert ".skills/" in bootstrap or "AGENTS.md" in bootstrap, relative
        for forbidden in FORBIDDEN_RUNTIME_TERMS:
            assert forbidden not in bootstrap, f"{relative}: contains {forbidden!r}"


def test_setup_is_repository_only_and_describes_managed_assets() -> None:
    setup = text(SETUP)
    for required in (
        "obsidian-wiki setup [DIR]",
        "clone",
        "doctor",
        "check",
        "`.skills/`",
        "managed mirrors",
        "upgrade",
        "Git",
    ):
        assert required in setup
    for forbidden in (
        "global install",
        "prompt publication",
        *FORBIDDEN_RUNTIME_TERMS,
    ):
        assert forbidden not in setup

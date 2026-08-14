"""Contract tests for owner-safe transactional maintenance skills."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATHS = (
    "obsidian_wiki/_data/skills/cross-linker/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-dedup/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-lint/SKILL.md",
)


def _skill_texts() -> list[tuple[str, str]]:
    return [
        (path, (ROOT / path).read_text(encoding="utf-8"))
        for path in SKILL_PATHS
    ]


def test_maintenance_resolves_nearest_repository_and_closes_sources_before_write() -> None:
    for path, text in _skill_texts():
        assert "nearest ancestor `.obsidian-wiki/config.toml`" in text, path
        assert "complete source closure" in text, path
        assert "live vault read-only" in text, path


def test_maintenance_uses_cli_transaction_validation_and_commit() -> None:
    for path, text in _skill_texts():
        assert "llmwikiops transaction begin --source" in text, path
        assert "llmwikiops transaction validate <id>" in text, path
        assert "llmwikiops transaction commit <id>" in text, path


def test_maintenance_uses_trusted_recovery_and_leaves_git_to_owner() -> None:
    for path, text in _skill_texts():
        assert "trusted transaction ID" in text, path
        assert "recovery.preferred_action" in text, path
        assert "allowed_actions" in text, path
        assert "Do not commit, push, or open a pull request" in text, path
        for forbidden in ("git add", "git commit", "reset --hard", "clean -fd"):
            assert forbidden not in text, (path, forbidden)

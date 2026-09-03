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
        assert "nearest ancestor `.llmwikiops/config.toml`" in text, path
        assert "complete source closure" in text, path
        assert "live vault read-only" in text, path


def test_maintenance_uses_cli_transaction_validation_and_commit() -> None:
    for path, text in _skill_texts():
        assert "Repository-local context: `<wiki-cli>` is `llmwikiops`" in text, path
        assert "External adapter context: `<wiki-cli>` is `llmwikiops -C <root>`" in text, path
        assert "<wiki-cli> transaction begin --source" in text, path
        assert "<wiki-cli> transaction validate <id>" in text, path
        assert "<wiki-cli> transaction commit <id>" in text, path


def test_maintenance_uses_trusted_recovery_and_scoped_local_commits() -> None:
    for path, text in _skill_texts():
        flat = " ".join(text.split())
        assert "trusted transaction ID" in flat, path
        assert "recovery.preferred_action" in flat, path
        assert "allowed_actions" in flat, path
        assert "display the exact staged patch" in flat, path
        assert "locally commit" in flat, path
        assert "leave unrelated paths untouched" in flat, path
        assert "owner-overlapping dirty paths" in flat, path
        assert "confirmation immediately before any push" in flat, path

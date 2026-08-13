from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "obsidian_wiki/_data/skills/llm-wiki/SKILL.md"
PATTERN = ROOT / "obsidian_wiki/_data/skills/llm-wiki/references/karpathy-pattern.md"


def test_manifest_v2_is_sharded_and_has_one_source_root() -> None:
    for path in (CANONICAL, PATTERN, ROOT / "docs/architecture.md"):
        text = path.read_text(encoding="utf-8")
        for required in (
            "manifest v2",
            "sharded",
            "exactly one configured source root",
            "transaction commit",
        ):
            assert required in text, f"{path}: missing {required!r}"


def test_transaction_owns_manifest_mutation() -> None:
    for path in (CANONICAL, PATTERN, ROOT / "docs/configuration.md"):
        text = path.read_text(encoding="utf-8")
        assert "transaction commit" in text
        assert "owns" in text
        assert "never edit manifest shards directly" in text


def test_docs_define_tracked_and_derived_manifest_state() -> None:
    text = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    assert "wiki/.manifest/sources/" in text
    assert "tracked source snapshots" in text
    assert "ignored local transaction workspaces" in text
    assert "log.md            # tracked authoritative operation log" in text
    assert "hot.md            # tracked derived semantic view" in text

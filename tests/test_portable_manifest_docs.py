from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "obsidian_wiki/_data/skills/llm-wiki/SKILL.md"
PATTERN = ROOT / "obsidian_wiki/_data/skills/llm-wiki/references/karpathy-pattern.md"


def test_manifest_v2_is_sharded_and_has_one_source_root() -> None:
    for path in (CANONICAL, PATTERN):
        text = path.read_text(encoding="utf-8")
        for required in (
            "manifest v2",
            "sharded entries",
            "exactly one configured source root",
            "transaction commit",
        ):
            assert required in text, f"{path}: missing {required!r}"


def test_transaction_owns_manifest_mutation() -> None:
    for path in (CANONICAL, PATTERN):
        text = path.read_text(encoding="utf-8")
        assert "transaction commit owns" in text
        assert "never edit manifest shards directly" in text

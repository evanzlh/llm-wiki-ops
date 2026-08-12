import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "obsidian_wiki/_data/skills/llm-wiki/SKILL.md"


def test_canonical_protocol_has_required_top_level_sections() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    headings = re.findall(r"(?m)^## (.+)$", text)
    assert headings[:3] == [
        "Configuration",
        "Authority and provenance",
        "Knowledge write protocol",
    ]


def test_canonical_protocol_defines_configuration_and_authority() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    for required in (
        "nearest ancestor `.obsidian-wiki/config.toml`",
        "obsidian-wiki setup [DIR]",
        "fail closed",
        "repository root",
        "vault `AGENTS.md`",
        "repository-relative Source ID",
        "reviewed Markdown snapshot",
    ):
        assert required in text


def test_canonical_protocol_is_one_eight_step_transaction() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    protocol = text.split("## Knowledge write protocol", 1)[1]
    steps = re.findall(r"(?m)^(\d+)\. \*\*(.+?)\*\*", protocol)
    assert [int(number) for number, _ in steps] == list(range(1, 9))
    for required in (
        "read-only source closure",
        "transaction begin --source",
        "--json --pretty",
        "candidate_vault",
        "transaction delete",
        "transaction validate",
        "transaction commit",
        "transaction list --json --pretty",
        "recommended_action",
        "allowed_actions",
        "hot status --json",
        "hot inputs --json --pretty",
        "hot mark-current",
    ):
        assert required in protocol


def test_cli_ownership_and_git_boundary_are_explicit() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    for required in (
        "Do not commit, push, or open a pull request",
        "never edit manifest shards directly",
        "never rewrite stable `index.md` or `log.md`",
    ):
        assert required in text

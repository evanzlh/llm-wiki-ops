from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DOCS = (
    ".skills/llm-wiki/SKILL.md",
    ".skills/wiki-ingest/SKILL.md",
    ".skills/wiki-update/SKILL.md",
    ".skills/wiki-status/SKILL.md",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_core_skills_distinguish_personal_and_portable_manifests() -> None:
    concepts = ("manifest v1", "manifest v2", "repository-relative Source ID")
    for relative in SKILL_DOCS:
        text = _text(relative)
        for concept in concepts:
            assert concept in text, f"{relative}: missing {concept!r}"


def test_llm_wiki_portable_protocol_never_requires_clone_specific_paths() -> None:
    text = _text(".skills/llm-wiki/SKILL.md")
    start = text.index("### Portable Repository mode — manifest v2")
    portable_section = text[start : text.find("\n## ", start)]

    assert "canonical absolute paths" not in portable_section
    assert "wiki/.manifest/sources/" in portable_section
    assert "cache-check" in portable_section
    assert "cache-update" in portable_section


def test_human_docs_show_the_manifest_marker_and_shard_shapes() -> None:
    architecture = _text("docs/architecture.md")
    for marker_field in (
        '"schema_version": 2',
        '"storage": "sharded"',
        '"entries": ".manifest/sources"',
    ):
        assert marker_field in architecture
    for shard_field in (
        '"source_id": "sources/design/portable.md"',
        '"content_hash": "sha256:',
        '"pages": ["concepts/portable-repository.md"]',
        '"compiled_at": "2026-08-07T07:30:00Z"',
    ):
        assert shard_field in architecture


def test_human_docs_define_portable_source_id_and_snapshot_rules() -> None:
    docs = "\n".join(
        _text(relative)
        for relative in ("docs/architecture.md", "docs/configuration.md")
    )
    for phrase in (
        "repository-relative Source ID",
        "forward slashes (`/`)",
        "configured `sources`",
        "live URL",
        "external filesystem path",
        "ordinary Git",
        "Git LFS pointers are unsupported",
    ):
        assert phrase in docs


def test_status_docs_define_portable_pr_blockers() -> None:
    status_skill = _text(".skills/wiki-status/SKILL.md")
    human_docs = "\n".join(
        _text(relative)
        for relative in ("docs/architecture.md", "docs/cli.md")
    )
    for text in (status_skill, human_docs):
        for issue in ("source-new", "source-stale", "source-orphaned"):
            assert issue in text
        assert "PR blocker" in text
        assert "obsidian-wiki check" in text


def test_cli_docs_define_check_output_and_exit_behavior() -> None:
    cli = _text("docs/cli.md")
    assert "read-only" in cli
    assert "exit 0" in cli
    assert "exit 1" in cli
    assert "--json" in cli
    assert "--pretty" in cli


def test_readmes_have_one_aligned_portable_check_example() -> None:
    english = _text("README.md")
    chinese = _text("README_ZH.md")
    command = "obsidian-wiki check"

    assert english.count(command) == 1
    assert chinese.count(command) == 1
    assert command in english.split("## Start a portable team wiki", 1)[1]
    assert command in chinese.split("## 创建便携式团队知识库", 1)[1]
    assert "README_TW.md" not in english + chinese

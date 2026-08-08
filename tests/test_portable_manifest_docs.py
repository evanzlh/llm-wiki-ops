from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DOCS = (
    ".skills/llm-wiki/SKILL.md",
    ".skills/wiki-ingest/SKILL.md",
    ".skills/wiki-update/SKILL.md",
    ".skills/wiki-status/SKILL.md",
)
HUMAN_MANIFEST_DOCS = (
    "docs/architecture.md",
    "docs/configuration.md",
    "docs/cli.md",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _collapsed(relative: str) -> str:
    return " ".join(_text(relative).split())


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
    assert "<vault>/.manifest/sources/" in portable_section
    assert "cache-check" in portable_section
    assert "cache-update" in portable_section


def test_portable_protocol_uses_the_resolved_vault_placeholder() -> None:
    for relative in (*SKILL_DOCS, *HUMAN_MANIFEST_DOCS):
        text = _text(relative)
        assert "wiki/.manifest" not in text, relative
        assert "<vault>/.manifest" in text, relative


def test_manifest_v2_schema_one_has_exactly_one_source_root() -> None:
    rule = "manifest v2 schema 1 requires exactly one configured source root"
    for relative in (*SKILL_DOCS, "docs/architecture.md", "docs/configuration.md"):
        assert rule in _collapsed(relative), relative


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
    docs = " ".join(
        _collapsed(relative)
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


def test_orphaned_source_reconciliation_deletes_the_whole_shard_file() -> None:
    command = "git rm <vault>/.manifest/sources/<relative>.json"
    for relative in (
        ".skills/wiki-status/SKILL.md",
        "docs/architecture.md",
        "docs/cli.md",
    ):
        text = _collapsed(relative)
        assert command in text, relative
        assert "entire corresponding shard file" in text, relative
        assert "never editing marker or shard JSON fields" in text, relative

    combined = "\n".join(
        _text(relative) for relative in (*SKILL_DOCS, *HUMAN_MANIFEST_DOCS)
    )
    assert "cache-remove" not in combined


def test_portable_status_ignores_placeholder_and_hidden_source_paths() -> None:
    for relative in (".skills/wiki-status/SKILL.md", "docs/architecture.md"):
        text = _collapsed(relative)
        assert ".gitkeep" in text, relative
        assert "hidden source path components" in text, relative
        assert "not authoritative tracked sources" in text, relative


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

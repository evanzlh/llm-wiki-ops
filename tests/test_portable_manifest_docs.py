from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DOCS = (
    "obsidian_wiki/_data/skills/llm-wiki/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-ingest/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-update/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-status/SKILL.md",
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


def _h2_headings(text: str) -> list[tuple[str, int, int]]:
    headings: list[tuple[str, int, int]] = []
    fence: tuple[str, int] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        fence_match = re.match(
            r"^[ \t]{0,3}(?P<marker>`{3,}|~{3,})(?P<tail>.*)$", content
        )
        if fence_match is not None:
            marker = fence_match.group("marker")
            tail = fence_match.group("tail")
            if fence is None:
                fence = (marker[0], len(marker))
            elif (
                marker[0] == fence[0]
                and len(marker) >= fence[1]
                and not tail.strip()
            ):
                fence = None
            offset += len(line)
            continue

        if fence is None:
            heading_match = re.match(
                r"^##[ \t]+(?P<title>[^\r\n]+?)[ \t]*$", content
            )
            if heading_match is not None:
                headings.append(
                    (heading_match.group("title"), offset, offset + len(content))
                )
        offset += len(line)
    return headings


def _h2_section(text: str, heading: str, *, relative: str) -> str:
    headings = _h2_headings(text)
    matches = [match for match in headings if match[0] == heading]
    assert len(matches) == 1, (
        f"{relative}: expected exactly one H2 {heading!r}, found {len(matches)}"
    )
    match = matches[0]
    position = headings.index(match)
    following = headings[position + 1 :]
    end = following[0][1] if following else len(text)
    return text[match[2] : end]


def test_core_skills_distinguish_personal_and_portable_manifests() -> None:
    concepts = ("manifest v1", "manifest v2", "repository-relative Source ID")
    for relative in SKILL_DOCS:
        text = _text(relative)
        for concept in concepts:
            assert concept in text, f"{relative}: missing {concept!r}"


def test_llm_wiki_portable_protocol_never_requires_clone_specific_paths() -> None:
    text = _text("obsidian_wiki/_data/skills/llm-wiki/SKILL.md")
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
    status_skill = _text("obsidian_wiki/_data/skills/wiki-status/SKILL.md")
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
        "obsidian_wiki/_data/skills/wiki-status/SKILL.md",
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
    for relative in ("obsidian_wiki/_data/skills/wiki-status/SKILL.md", "docs/architecture.md"):
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


def test_readmes_have_aligned_portable_check_examples() -> None:
    english = _text("README.md")
    chinese = _text("README_ZH.md")
    command = "obsidian-wiki check"

    english_portable = _h2_section(
        english, "Start a portable team wiki", relative="README.md"
    )
    chinese_portable = _h2_section(
        chinese, "创建便携式团队知识库", relative="README_ZH.md"
    )
    english_commands = tuple(
        line for line in english_portable.splitlines() if command in line
    )
    chinese_commands = tuple(
        line for line in chinese_portable.splitlines() if command in line
    )

    assert english_commands, "README.md portable section has no check command"
    assert english_commands == chinese_commands
    assert "README_TW.md" not in english + chinese

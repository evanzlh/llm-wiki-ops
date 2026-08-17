"""Tests for the GraphRAG query index module."""
import json
from pathlib import Path
from typing import Any, cast

import pytest

import obsidian_wiki.graphrag as graphrag
from obsidian_wiki.frontmatter import FrontmatterError
from obsidian_wiki.graphrag import (
    build_index,
    find_path,
    query,
    rank_candidates,
)
from obsidian_wiki.query_language import QuerySpec


def test_module_docs_use_current_portable_query_command() -> None:
    assert graphrag.__doc__ is not None
    assert "llmwikiops query 'find \"注意力机制\"'" in graphrag.__doc__
    assert 'llmwikiops query --mode find --term "注意力机制"' in graphrag.__doc__
    assert "graph-query" not in graphrag.__doc__
    assert 'query "<question>"' not in graphrag.__doc__


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


def _page(vault: Path, name: str, *, title: str = "", summary: str = "",
          tags: list[str] | None = None, links: list[str] | None = None,
          tier: str = "supporting", category: str = "concepts",
          lifecycle: str = "reviewed", updated: str = "2026-08-13") -> Path:
    lines = ["---", f"title: {title or name}"]
    if summary:
        lines.append(f"summary: {summary}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines.append(f"tier: {tier}")
    lines.append(f"category: {category}")
    lines.append(f"lifecycle: {lifecycle}")
    lines.append(f"updated: {updated}")
    lines.append("---")
    lines.append(f"# {title or name}")
    for lnk in (links or []):
        lines.append(f"[[{lnk}]]")
    p = vault / f"{name}.md"
    p.write_text("\n".join(lines) + "\n")
    return p


@pytest.fixture
def simple_vault(vault):
    _page(vault, "transformer", title="Transformer Architecture",
          summary="Self-attention mechanism for sequence modelling.",
          tags=["deep-learning", "nlp"], tier="core", links=["attention", "embedding"])
    _page(vault, "attention", title="Attention Mechanism",
          summary="Computes weighted sums over value vectors.",
          tags=["deep-learning"], links=["transformer"])
    _page(vault, "embedding", title="Word Embedding",
          summary="Dense vector representation of tokens.",
          tags=["nlp"])
    _page(vault, "python", title="Python",
          summary="General-purpose programming language.",
          tags=["programming"])
    return vault


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_includes_journal_operations_as_knowledge(self, vault):
        journal = vault / "journal"
        journal.mkdir()
        _page(journal, "daily", title="Daily")
        operations = journal / "operations"
        operations.mkdir()
        _page(operations, "entry", title="Operation Entry", links=["daily"])

        index = build_index(vault)

        assert set(index) == {"journal/daily", "journal/operations/entry"}
        assert index["journal/operations/entry"]["out_links"] == ["journal/daily"]

    def test_rejects_symlinked_journal_operations(self, vault, tmp_path):
        journal = vault / "journal"
        journal.mkdir()
        operations = journal / "operations"
        external = tmp_path / "external-operations"
        external.mkdir()
        _page(external, "secret", title="Secret")
        try:
            operations.symlink_to(external, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks are unavailable: {exc}")

        with pytest.raises(RuntimeError, match="symlink"):
            build_index(vault)

    def test_excludes_exact_root_views_but_keeps_nested_names(self, vault):
        concepts = vault / "concepts"
        concepts.mkdir()
        _page(concepts, "topic", title="Topic", links=["log"])
        _page(concepts, "log", title="Nested Log", links=[])
        for name in ("index", "log", "hot"):
            _page(vault, name, title=f"Root {name.title()}", links=["topic"])

        index = build_index(vault)

        assert set(index) == {"concepts/topic", "concepts/log"}
        assert index["concepts/log"]["title"] == "Nested Log"
        assert index["concepts/topic"]["out_links"] == ["concepts/log"]
        assert index["concepts/topic"]["in_links"] == []

    def test_skips_root_views_before_frontmatter_parsing(self, vault):
        for name in ("index.md", "log.md", "hot.md"):
            (vault / name).write_text("---\ntags: [one]\ntags: [two]\n---\n", encoding="utf-8")
        _page(vault, "topic", title="Topic")

        assert set(build_index(vault)) == {"topic"}

    def test_returns_slugs(self, simple_vault):
        idx = build_index(simple_vault)
        assert "transformer" in idx
        assert "attention" in idx

    def test_reads_title(self, simple_vault):
        idx = build_index(simple_vault)
        assert idx["transformer"]["title"] == "Transformer Architecture"

    def test_reads_summary(self, simple_vault):
        idx = build_index(simple_vault)
        assert "Self-attention" in idx["transformer"]["summary"]

    def test_reads_tags(self, simple_vault):
        idx = build_index(simple_vault)
        assert "deep-learning" in idx["transformer"]["tags"]

    def test_reads_tier(self, simple_vault):
        idx = build_index(simple_vault)
        assert idx["transformer"]["tier"] == "core"

    def test_preserves_distinct_nested_page_identities_and_paths(self, vault):
        concepts = vault / "concepts"
        projects = vault / "projects"
        concepts.mkdir()
        projects.mkdir()
        _page(concepts, "agent", title="Concept Agent")
        _page(projects, "agent", title="Project Agent")

        index = build_index(vault)

        assert set(index) == {"concepts/agent", "projects/agent"}
        assert index["concepts/agent"]["path"] == "concepts/agent.md"
        assert index["projects/agent"]["path"] == "projects/agent.md"
        assert index["concepts/agent"]["ambiguous_links"] == []

    def test_resolves_qualified_links_and_records_ambiguous_aliases(self, vault):
        concepts = vault / "concepts"
        projects = vault / "projects"
        concepts.mkdir()
        projects.mkdir()
        _page(concepts, "agent", title="Concept Agent")
        _page(projects, "agent", title="Project Agent")
        _page(vault, "source", links=["concepts/agent", "agent"])

        index = build_index(vault)

        assert index["source"]["out_links"] == ["concepts/agent"]
        assert index["concepts/agent"]["in_links"] == ["source"]
        assert index["source"]["ambiguous_links"] == [
            {"target": "agent", "candidates": ["concepts/agent", "projects/agent"]}
        ]

    def test_resolves_qualified_markdown_links_with_fragments_and_queries(self, vault):
        concepts = vault / "concepts"
        concepts.mkdir()
        _page(concepts, "agent", title="Agent")
        source = _page(vault, "source")
        source.write_text(
            source.read_text(encoding="utf-8")
            + "[Exact](concepts/agent.md)\n"
            + "[Fragment](concepts/agent.md#details)\n"
            + "[Query](concepts/agent.md?view=compact#details)\n",
            encoding="utf-8",
        )

        index = build_index(vault)

        assert index["source"]["out_links"] == ["concepts/agent"] * 3
        assert index["source"]["ambiguous_links"] == []

    def test_ignores_non_markdown_and_external_markdown_destinations(self, vault):
        _page(vault, "foo.mdx", title="Foo MDX")
        _page(vault, "agent", title="Agent")
        source = _page(vault, "source")
        source.write_text(
            source.read_text(encoding="utf-8")
            + "[MDX](foo.mdx)\n"
            + "[HTTP](http://example.invalid/agent.md)\n"
            + "[HTTPS](https://example.invalid/agent.md)\n"
            + "[Mail](mailto:agent.md)\n"
            + "[File](file:agent.md)\n"
            + "[Protocol](//example.invalid/agent.md)\n"
            + "[HTML](agent.md.html)\n",
            encoding="utf-8",
        )

        index = build_index(vault)

        assert index["source"]["out_links"] == []
        assert index["source"]["ambiguous_links"] == []

    def test_resolves_local_markdown_paths_relative_to_nested_source(self, vault):
        concepts = vault / "concepts"
        nested = vault / "nested"
        concepts.mkdir()
        nested.mkdir()
        _page(concepts, "agent", title="Agent")
        _page(nested, "local", title="Local")
        source = _page(nested, "source")
        source.write_text(
            source.read_text(encoding="utf-8")
            + "[Parent](../concepts/agent.md#details)\n"
            + "[Local](./local.md)\n"
            + "[Root](/concepts/agent.md)\n"
            + "[Angle](<../concepts/agent.md>)\n"
            + '[Title](../concepts/agent.md "Agent title")\n'
            + "[Escape](../../outside.md)\n",
            encoding="utf-8",
        )

        index = build_index(vault)

        assert index["nested/source"]["out_links"] == [
            "concepts/agent",
            "nested/local",
            "concepts/agent",
            "concepts/agent",
            "concepts/agent",
        ]

    def test_markdown_destinations_do_not_fall_back_to_title_aliases(self, vault):
        _page(vault, "alias", title="missing/target")
        source = _page(vault, "source")
        source.write_text(
            source.read_text(encoding="utf-8")
            + "[Broken](missing/target.md)\n",
            encoding="utf-8",
        )

        index = build_index(vault)

        assert index["source"]["out_links"] == []
        assert index["source"]["ambiguous_links"] == []
        assert index["alias"]["in_links"] == []

    def test_markdown_destination_preserves_page_ids_ending_in_md(self, vault):
        _page(vault, "foo.md", title="Double Markdown Suffix")
        source = _page(vault, "source")
        source.write_text(
            source.read_text(encoding="utf-8") + "[Double](foo.md.md)\n",
            encoding="utf-8",
        )

        index = build_index(vault)

        assert index["source"]["out_links"] == ["foo.md"]
        assert index["foo.md"]["in_links"] == ["source"]

    def test_resolves_slash_title_aliases_after_exact_page_ids(self, vault):
        concepts = vault / "concepts"
        concepts.mkdir()
        _page(concepts, "agent", title="Qualified Agent")
        _page(vault, "alias-page", title="concepts/agent")
        _page(vault, "reference", title="AC/DC")
        _page(vault, "source", links=["AC/DC", "concepts/agent"])

        index = build_index(vault)

        assert index["source"]["out_links"] == ["reference", "concepts/agent"]
        assert index["concepts/agent"]["in_links"] == ["source"]
        assert index["alias-page"]["in_links"] == []

    def test_keeps_root_and_nested_basename_links_ambiguous(self, vault):
        concepts = vault / "concepts"
        concepts.mkdir()
        _page(vault, "agent", title="Root Agent")
        _page(concepts, "agent", title="Concept Agent")
        _page(vault, "source", links=["agent"])

        index = build_index(vault)

        assert index["source"]["out_links"] == []
        assert index["source"]["ambiguous_links"] == [
            {"target": "agent", "candidates": ["agent", "concepts/agent"]}
        ]

    def test_duplicate_normalized_identity_fails_closed(self, vault):
        _page(vault, "Agent", title="First Agent")
        duplicate = _page(vault, "Ａgent", title="Second Agent")
        duplicate.write_text(
            duplicate.read_text(encoding="utf-8") + "PRIVATE-BODY-SENTINEL\n",
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="duplicate normalized query page identity") as raised:
            build_index(vault)

        assert "PRIVATE-BODY-SENTINEL" not in str(raised.value)

    def test_public_only_filters_metadata_before_body_read(self, vault, monkeypatch):
        _page(vault, "public", summary="Public summary", tags=["public"], links=[])
        blocked = _page(
            vault,
            "private",
            summary="Private metadata",
            tags=["visibility/internal"],
            links=["public"],
        )
        blocked.write_text(
            blocked.read_text(encoding="utf-8") + "PRIVATE-BODY-SENTINEL\n",
            encoding="utf-8",
        )
        reads: list[str] = []
        original = graphrag.read_markdown_snapshot

        def observed(snapshot):
            reads.append(snapshot.relative)
            return original(snapshot)

        monkeypatch.setattr(graphrag, "read_markdown_snapshot", observed)

        index = build_index(vault, public_only=True)

        assert set(index) == {"public"}
        assert "private.md" not in reads
        assert "PRIVATE-BODY-SENTINEL" not in repr(index)

    @pytest.mark.parametrize(
        "tag_line",
        [
            "  - visibility/internal # restricted",
            '  - "visibility/internal" # restricted',
        ],
    )
    def test_public_only_parses_commented_and_quoted_block_tags_before_body_read(
        self, vault, monkeypatch, tag_line
    ):
        (vault / "private.md").write_text(
            "---\r\ntitle: Private\r\ntags:\r\n"
            f"{tag_line}\r\n---\r\nPRIVATE-BODY-SENTINEL\r\n",
            encoding="utf-8",
        )
        reads: list[str] = []
        original = graphrag.read_markdown_snapshot

        def observed(snapshot):
            reads.append(snapshot.relative)
            return original(snapshot)

        monkeypatch.setattr(graphrag, "read_markdown_snapshot", observed)

        assert build_index(vault, public_only=True) == {}
        assert reads == []

    def test_public_only_parses_cr_only_private_metadata_before_body_read(
        self, vault, monkeypatch
    ):
        (vault / "private.md").write_bytes(
            b"---\rtitle: Private\rtags:\r"
            b"  - visibility/internal # restricted\r---\r"
            b"PRIVATE-BODY-SENTINEL\r"
        )
        reads: list[str] = []
        monkeypatch.setattr(
            graphrag,
            "read_markdown_snapshot",
            lambda snapshot: reads.append(snapshot.relative),
        )

        assert build_index(vault, public_only=True) == {}
        assert reads == []

    def test_invalid_public_metadata_fails_closed_before_body_read(
        self, vault, monkeypatch
    ):
        (vault / "private.md").write_text(
            "---\ntags: [public]\ntags: [visibility/internal]\n---\n"
            "PRIVATE-BODY-SENTINEL\n",
            encoding="utf-8",
        )
        reads: list[str] = []
        monkeypatch.setattr(
            graphrag,
            "read_markdown_snapshot",
            lambda snapshot: reads.append(snapshot.relative),
        )

        assert build_index(vault, public_only=True) == {}
        assert reads == []
        with pytest.raises(FrontmatterError, match="duplicate"):
            build_index(vault)

    def test_shared_parser_preserves_legacy_index_keys_and_adds_trust_metadata(
        self, simple_vault
    ):
        entry = build_index(simple_vault)["transformer"]
        legacy = {
            "title": "Transformer Architecture",
            "tags": ["deep-learning", "nlp"],
            "summary": "Self-attention mechanism for sequence modelling.",
            "category": "concepts",
            "tier": "core",
            "path": "transformer.md",
            "out_links": ["attention", "embedding"],
            "in_links": ["attention"],
        }

        assert {key: entry[key] for key in legacy} == legacy
        assert entry["visibility"] == []
        assert entry["lifecycle"] == "reviewed"
        assert entry["updated"] == "2026-08-13"

    def test_out_links(self, simple_vault):
        idx = build_index(simple_vault)
        assert "attention" in idx["transformer"]["out_links"]

    def test_in_links_reverse(self, simple_vault):
        idx = build_index(simple_vault)
        assert "transformer" in idx["attention"]["in_links"]

    def test_empty_vault(self, vault):
        idx = build_index(vault)
        assert idx == {}

    @pytest.mark.parametrize("name", ["_archives", "_raw", "_readouts", "_staging"])
    def test_does_not_hide_personal_artifact_names(self, vault, name):
        directory = vault / name
        directory.mkdir()
        _page(directory, "draft", title="Draft")
        idx = build_index(vault)
        assert f"{name}/draft" in idx

    def test_rejects_external_symlink_without_leaking_content(self, vault, tmp_path):
        raw = vault / "_raw"
        raw.mkdir()
        secret = tmp_path / "secret.md"
        secret.write_text("# SECRET-MARKER\n", encoding="utf-8")
        (raw / "leak.md").symlink_to(secret)

        with pytest.raises(RuntimeError, match="symlink") as raised:
            build_index(vault)

        assert "SECRET-MARKER" not in str(raised.value)

    def test_ignores_unrelated_non_markdown_symlink(self, vault, tmp_path):
        _page(vault, "kept", title="Kept")
        secret = tmp_path / "secret.json"
        secret.write_text("SECRET-MARKER\n", encoding="utf-8")
        (vault / "unrelated.json").symlink_to(secret)

        index = build_index(vault)

        assert "kept" in index
        assert "SECRET-MARKER" not in str(index)

    def test_reads_folded_block_scalar_summary(self, vault):
        # Regression for #156: `summary: >-` puts the real text on the next
        # indented line(s), not on the `summary:` line itself.
        (vault / "folded.md").write_text(
            "---\n"
            "title: >-\n"
            "  Folded Title\n"
            "summary: >-\n"
            "  Some text that wraps\n"
            "  onto a second line.\n"
            "category: concepts\n"
            "---\n"
            "# Folded\n"
        )
        idx = build_index(vault)
        assert idx["folded"]["title"] == "Folded Title"
        assert idx["folded"]["summary"] == "Some text that wraps onto a second line."

    def test_reads_literal_block_scalar_summary(self, vault):
        (vault / "literal.md").write_text(
            "---\n"
            "title: Literal\n"
            "summary: |-\n"
            "  Literal block text.\n"
            "category: concepts\n"
            "---\n"
            "# Literal\n"
        )
        idx = build_index(vault)
        assert idx["literal"]["summary"] == "Literal block text."


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------

class TestRankCandidates:
    def test_exact_title_match_scores_highest(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, "transformer")
        assert result[0]["slug"] == "transformer"
        assert result[0]["match_kind"] == "exact"

    def test_tag_match_included(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, "nlp")
        slugs = [r["slug"] for r in result]
        assert "transformer" in slugs or "embedding" in slugs

    def test_no_match_returns_empty(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, "zzznomatch")
        assert result == []

    def test_core_tier_boosted(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, "deep-learning")
        # transformer is tier:core; attention is tier:supporting — transformer should score higher
        transformer_score = next((r["score"] for r in result if r["slug"] == "transformer"), 0)
        attention_score = next((r["score"] for r in result if r["slug"] == "attention"), 0)
        assert transformer_score > attention_score

    def test_respects_top_n(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, "deep-learning", top_n=1)
        assert len(result) <= 1

    @pytest.mark.parametrize("top_n", [0, -1, 1.5, True, "1"])
    def test_rejects_invalid_optional_top_n(self, simple_vault, top_n):
        with pytest.raises(graphrag.QueryExecutionError) as raised:
            rank_candidates(
                build_index(simple_vault),
                "deep-learning",
                top_n=top_n,
            )

        assert raised.value.code == "invalid_query_arguments"

    def test_lexical_kind_precedes_degree_and_tier(self, vault):
        _page(vault, "exact", title="Topic", tier="peripheral")
        _page(vault, "substring", title="Topic Overview", tier="core")
        for index in range(20):
            _page(vault, f"link-{index}", links=["substring"])

        result = rank_candidates(build_index(vault), "Topic")

        assert result[0]["slug"] == "exact"
        assert result[0]["match_kind"] == "exact"
        assert result[1]["match_kind"] == "title"


# ---------------------------------------------------------------------------
# find_path
# ---------------------------------------------------------------------------

class TestFindPath:
    def test_direct_link(self, simple_vault):
        idx = build_index(simple_vault)
        path = find_path(idx, "transformer", "attention")
        assert path is not None
        assert "transformer" in path
        assert "attention" in path

    def test_same_node(self, simple_vault):
        idx = build_index(simple_vault)
        path = find_path(idx, "transformer", "transformer")
        assert path == ["transformer"]

    def test_unknown_node_returns_none(self, simple_vault):
        idx = build_index(simple_vault)
        path = find_path(idx, "transformer", "zzznone")
        assert path is None

    def test_multi_hop(self, vault):
        _page(vault, "a", links=["b"])
        _page(vault, "b", links=["c"])
        _page(vault, "c", links=[])
        idx = build_index(vault)
        path = find_path(idx, "a", "c")
        assert path is not None
        assert len(path) == 3

    def test_no_path_returns_none(self, vault):
        _page(vault, "x", links=[])
        _page(vault, "y", links=[])
        idx = build_index(vault)
        path = find_path(idx, "x", "y")
        assert path is None


# ---------------------------------------------------------------------------
# query (integration)
# ---------------------------------------------------------------------------

class TestQuery:
    def test_returns_required_keys(self, simple_vault):
        result = query(simple_vault, QuerySpec(mode="find", term="transformer"))
        assert set(result) >= {
            "grammar_version",
            "mode",
            "status",
            "candidates",
            "path",
            "god_nodes_relevant",
            "should_read",
            "should_read_metadata",
            "index_only",
            "stats",
        }
        assert result["grammar_version"] == "query-language/v1"
        assert result["mode"] == "find"
        assert result["status"] == "ok"
        assert result["stats"] == {
            "indexed_pages": 4,
            "query_operands": {"term": "transformer"},
        }
        assert "answer_type" not in result
        assert "query_terms" not in result["stats"]

    def test_chinese_phrase_matches_without_query_tokenization(self, vault):
        _page(
            vault,
            "attention",
            title="注意力机制",
            summary="用于序列建模的加权聚合方法",
            tags=["深度学习"],
        )

        result = query(vault, QuerySpec(mode="find", term="注意力机制"))

        assert result["status"] == "ok"
        assert result["candidates"][0]["page"] == "attention.md"

    def test_find_does_not_split_or_expand_a_phrase(self, simple_vault):
        result = query(
            simple_vault,
            QuerySpec(mode="find", term="attention unknown"),
        )

        assert result["status"] == "no_matches"
        assert result["candidates"] == []

    def test_list_reports_total_and_truncation(self, vault):
        for index in range(3):
            _page(vault, f"page-{index}", summary="共享主题摘要", tags=["共享主题"])

        result = query(
            vault,
            QuerySpec(mode="list", term="共享主题"),
            top_n=2,
        )

        assert result["status"] == "ok"
        assert result["total_matches"] == 3
        assert result["truncated"] is True
        assert len(result["candidates"]) == 2
        assert result["index_only"] is False
        assert len(result["should_read"]) <= 3

    def test_mixed_language_path_query(self, vault):
        _page(vault, "attention", title="注意力机制", links=["embedding"])
        _page(vault, "embedding", title="Word Embedding")

        result = query(
            vault,
            QuerySpec(mode="path", source="注意力机制", target="Word Embedding"),
        )

        assert result["status"] == "ok"
        assert result["path"] == ["attention.md", "embedding.md"]
        assert result["index_only"] is False
        assert result["should_read"] == ["attention.md", "embedding.md"]

    def test_path_distinguishes_no_match_from_no_path(self, vault):
        _page(vault, "left", title="Left")
        _page(vault, "right", title="Right")

        missing = query(
            vault,
            QuerySpec(mode="path", source="Missing", target="Right"),
        )
        disconnected = query(
            vault,
            QuerySpec(mode="path", source="Left", target="Right"),
        )

        assert missing["status"] == "no_matches"
        assert missing["unresolved_operands"] == ["source"]
        assert disconnected["status"] == "no_path"

    def test_path_rejects_ambiguous_endpoint_alias(self, vault):
        for folder in ("concepts", "projects"):
            directory = vault / folder
            directory.mkdir()
            _page(directory, "agent", title=f"{folder} agent")
        _page(vault, "target", title="Target", links=["concepts/agent"])

        with pytest.raises(graphrag.QueryExecutionError) as raised:
            query(
                vault,
                QuerySpec(mode="path", source="agent", target="Target"),
            )

        assert raised.value.code == "ambiguous_operand"
        assert raised.value.details["operand"] == "source"
        assert raised.value.details["candidates"] == [
            "concepts/agent.md",
            "projects/agent.md",
        ]

        chosen = query(
            vault,
            QuerySpec(
                mode="path",
                source=raised.value.details["candidates"][0],
                target="Target",
            ),
        )

        assert chosen["status"] == "ok"
        assert chosen["path"] == ["concepts/agent.md", "target.md"]

    def test_path_rejects_ambiguous_best_substring_kind(self, vault):
        _page(vault, "agent-alpha", title="Agent Alpha", tier="peripheral")
        _page(vault, "agent-beta", title="Agent Beta", tier="core")
        _page(vault, "target", title="Target", links=["agent-beta"])

        with pytest.raises(graphrag.QueryExecutionError) as raised:
            query(
                vault,
                QuerySpec(mode="path", source="Agent", target="Target"),
            )

        assert raised.value.code == "ambiguous_operand"
        assert raised.value.details == {
            "operand": "source",
            "candidates": ["agent-alpha.md", "agent-beta.md"],
        }

    def test_index_only_requires_exact_title_or_identity_match(self, simple_vault):
        exact = query(
            simple_vault,
            QuerySpec(mode="find", term="Transformer Architecture"),
        )
        partial = query(
            simple_vault,
            QuerySpec(mode="find", term="Architecture"),
        )

        assert exact["index_only"] is True
        assert exact["should_read"] == []
        assert partial["index_only"] is False
        assert partial["should_read"] == ["transformer.md"]

    def test_index_only_requires_one_exact_lexical_match(self, vault):
        for folder, tier in (("concepts", "core"), ("projects", "supporting")):
            directory = vault / folder
            directory.mkdir()
            _page(
                directory,
                "agent",
                title=f"{folder.title()} Agent",
                summary=f"Summary for {folder}",
                tier=tier,
            )

        result = query(
            vault,
            QuerySpec(mode="find", term="agent"),
            top_n=1,
        )

        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["match_kind"] == "exact"
        assert result["index_only"] is False
        assert result["should_read"] == ["concepts/agent.md"]

    @pytest.mark.parametrize(
        "spec",
        [
            "find",
            QuerySpec(mode=cast(Any, "unknown"), term="topic"),
            QuerySpec(mode="find"),
            QuerySpec(mode="find", term="topic", source="extra"),
            QuerySpec(mode="find", term="   "),
            QuerySpec(mode="list", term=cast(Any, 123)),
            QuerySpec(mode="path", source="left"),
            QuerySpec(mode="path", source="left", target="right", term="extra"),
            QuerySpec(mode="path", source="", target="right"),
            QuerySpec(mode="path", source="left", target=cast(Any, 123)),
        ],
    )
    def test_rejects_malformed_query_specs(self, vault, spec):
        with pytest.raises(graphrag.QueryExecutionError) as raised:
            query(vault, spec)

        assert raised.value.code == "invalid_query_arguments"

    @pytest.mark.parametrize(
        "options",
        [
            {"top_n": 0},
            {"top_n": -1},
            {"top_n": 1.5},
            {"top_n": True},
            {"max_should_read": -1},
            {"max_should_read": 1.5},
            {"max_should_read": True},
            {"max_should_read": "1"},
        ],
    )
    def test_rejects_invalid_query_numeric_bounds(self, vault, options):
        with pytest.raises(graphrag.QueryExecutionError) as raised:
            query(vault, QuerySpec(mode="find", term="topic"), **options)

        assert raised.value.code == "invalid_query_arguments"

    def test_zero_max_should_read_is_valid(self, simple_vault):
        result = query(
            simple_vault,
            QuerySpec(mode="find", term="Architecture"),
            max_should_read=0,
        )

        assert result["status"] == "ok"
        assert result["index_only"] is False
        assert result["should_read"] == []
        assert result["should_read_metadata"] == []

    @pytest.mark.parametrize(
        ("spec", "mode"),
        [
            (QuerySpec(mode="find", term="anything"), "find"),
            (QuerySpec(mode="list", term="anything"), "list"),
            (QuerySpec(mode="path", source="left", target="right"), "path"),
        ],
    )
    def test_empty_vault_returns_mode_specific_no_matches(self, vault, spec, mode):
        result = query(vault, spec)

        assert result["grammar_version"] == "query-language/v1"
        assert result["mode"] == mode
        assert result["status"] == "no_matches"
        assert result["candidates"] == []
        assert result["index_only"] is False

    def test_json_serialisable(self, simple_vault):
        result = query(simple_vault, QuerySpec(mode="find", term="deep-learning"))
        json.dumps(result)

    def test_public_result_has_trust_metadata_without_private_identity(self, vault):
        _page(
            vault,
            "public",
            summary="Launch summary",
            tags=["visibility/public"],
            lifecycle="verified",
            updated="2026-08-12",
        )
        _page(
            vault,
            "secret-roadmap",
            summary="SECRET-METADATA-SENTINEL",
            tags=["visibility/pii"],
        )

        result = query(
            vault,
            QuerySpec(mode="find", term="launch"),
            public_only=True,
        )

        assert result["stats"]["indexed_pages"] == 1
        assert "secret-roadmap" not in json.dumps(result)
        candidate = result["candidates"][0]
        assert candidate["visibility"] == ["visibility/public"]
        assert candidate["lifecycle"] == "verified"
        assert candidate["updated"] == "2026-08-12"
        trust = result["should_read_metadata"][0]
        assert trust == {
            "page": "public.md",
            "visibility": ["visibility/public"],
            "lifecycle": "verified",
            "updated": "2026-08-12",
        }

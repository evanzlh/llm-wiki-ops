"""Tests for the GraphRAG query index module."""
import json
from pathlib import Path

import pytest

import obsidian_wiki.graphrag as graphrag
from obsidian_wiki.frontmatter import FrontmatterError
from obsidian_wiki.graphrag import (
    build_index,
    classify_query,
    find_path,
    query,
    rank_candidates,
)


def test_module_docs_use_current_portable_query_command() -> None:
    assert graphrag.__doc__ is not None
    assert 'llmwikiops query "<question>"' in graphrag.__doc__
    assert "graph-query" not in graphrag.__doc__


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
        result = rank_candidates(idx, ["transformer"])
        assert result[0]["slug"] == "transformer"

    def test_tag_match_included(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, ["nlp"])
        slugs = [r["slug"] for r in result]
        assert "transformer" in slugs or "embedding" in slugs

    def test_no_match_returns_empty(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, ["zzznomatch"])
        assert result == []

    def test_core_tier_boosted(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, ["deep-learning"])
        # transformer is tier:core; attention is tier:supporting — transformer should score higher
        transformer_score = next((r["score"] for r in result if r["slug"] == "transformer"), 0)
        attention_score = next((r["score"] for r in result if r["slug"] == "attention"), 0)
        assert transformer_score > attention_score

    def test_respects_top_n(self, simple_vault):
        idx = build_index(simple_vault)
        result = rank_candidates(idx, ["deep-learning"], top_n=1)
        assert len(result) <= 1


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
# classify_query
# ---------------------------------------------------------------------------

class TestClassifyQuery:
    def test_direct_query(self):
        qt, terms = classify_query("What is a transformer?")
        assert qt == "direct"
        assert any("transformer" in t.lower() for t in terms)

    def test_path_query(self):
        qt, terms = classify_query("How is transformer connected to embedding?")
        assert qt == "path"
        assert len(terms) == 2

    def test_gap_query(self):
        qt, _ = classify_query("What do I not know about reinforcement learning?")
        assert qt == "gap"

    def test_list_query(self):
        qt, _ = classify_query("List all pages about deep learning")
        assert qt == "list"

    def test_stop_words_filtered(self):
        _, terms = classify_query("What is the difference?")
        assert "the" not in terms
        assert "is" not in terms


# ---------------------------------------------------------------------------
# query (integration)
# ---------------------------------------------------------------------------

class TestQuery:
    def test_returns_required_keys(self, simple_vault):
        result = query(simple_vault, "What is a transformer?")
        assert set(result.keys()) >= {"answer_type", "candidates", "path",
                                       "god_nodes_relevant", "should_read", "index_only"}

    def test_finds_exact_match(self, simple_vault):
        result = query(simple_vault, "transformer architecture")
        pages = [c["page"] for c in result["candidates"]]
        assert any("transformer" in p for p in pages)

    def test_path_query_populated(self, simple_vault):
        result = query(simple_vault, "How is transformer connected to embedding?")
        assert result["answer_type"] == "path"

    def test_index_only_on_exact_with_summary(self, simple_vault):
        result = query(simple_vault, "Transformer Architecture")
        # Title exact match + summary → index_only should be True
        assert result["index_only"] is True

    def test_should_read_empty_when_index_only(self, simple_vault):
        result = query(simple_vault, "Transformer Architecture")
        if result["index_only"]:
            assert result["should_read"] == []

    def test_empty_vault(self, vault):
        result = query(vault, "anything")
        assert result["candidates"] == []
        assert result["index_only"] is True

    def test_json_serialisable(self, simple_vault):
        result = query(simple_vault, "deep learning")
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

        result = query(vault, "launch", public_only=True)

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

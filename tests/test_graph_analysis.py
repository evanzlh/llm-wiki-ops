"""Tests for vault graph analysis: community detection, god nodes, surprising connections."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.graph_analysis import (
    analyse_vault,
    dead_ends,
    detect_communities_greedy,
    god_nodes,
    isolated,
    parse_vault_graph,
    surprising_connections,
)


# ---------------------------------------------------------------------------
# Fixtures — synthetic vault
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (tmp_path / ".obsidian-wiki").mkdir()
    (tmp_path / "sources").mkdir()
    (tmp_path / ".skills").mkdir()
    (tmp_path / ".obsidian-wiki/config.toml").write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "vault"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    return v


def _page(vault: Path, name: str, links: list[str], tags: list[str] | None = None) -> Path:
    """Write a minimal wiki page with wikilinks."""
    lines = ["---", f"title: {name}"]
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines += ["---", f"# {name}", ""]
    lines += [f"[[{lnk}]]" for lnk in links]
    p = vault / f"{name}.md"
    p.write_text("\n".join(lines) + "\n")
    return p


@pytest.fixture
def simple_vault(vault):
    """
    A → B → C
    A → C
    D (isolated)
    E → F (dead-end cluster)
    """
    _page(vault, "a", ["b", "c"], tags=["concepts"])
    _page(vault, "b", ["c"], tags=["concepts"])
    _page(vault, "c", [], tags=["references"])
    _page(vault, "d", [], tags=["entities"])
    _page(vault, "e", ["f"])
    _page(vault, "f", [])
    return vault


# ---------------------------------------------------------------------------
# parse_vault_graph
# ---------------------------------------------------------------------------

class TestParseVaultGraph:
    def test_includes_journal_operations_as_knowledge(self, vault):
        journal = vault / "journal"
        journal.mkdir()
        _page(journal, "daily", [])
        operations = journal / "operations"
        operations.mkdir()
        _page(operations, "entry", ["daily"], tags=["operation"])

        outgoing, tags = parse_vault_graph(vault)

        assert outgoing == {"daily": [], "entry": ["daily"]}
        assert tags == {"entry": ["operation"]}

    def test_rejects_symlinked_journal_operations(self, vault, tmp_path):
        journal = vault / "journal"
        journal.mkdir()
        operations = journal / "operations"
        external = tmp_path / "external-operations"
        external.mkdir()
        _page(external, "secret", [])
        try:
            operations.symlink_to(external, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks are unavailable: {exc}")

        with pytest.raises(RuntimeError, match="symlink"):
            parse_vault_graph(vault)

    def test_excludes_exact_root_views_but_keeps_nested_names(self, vault):
        concepts = vault / "concepts"
        concepts.mkdir()
        _page(concepts, "topic", ["log"], tags=["knowledge"])
        _page(concepts, "log", [], tags=["nested"])
        for name in ("index", "log", "hot"):
            _page(vault, name, ["topic"], tags=[f"root-{name}"])

        outgoing, tags = parse_vault_graph(vault)

        assert outgoing == {"log": [], "topic": ["log"]}
        assert tags == {"log": ["nested"], "topic": ["knowledge"]}

    def test_reads_wikilinks(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        assert "b" in outgoing["a"]
        assert "c" in outgoing["a"]

    def test_all_pages_present_as_keys(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        assert set(outgoing.keys()) == {"a", "b", "c", "d", "e", "f"}

    def test_reads_tags(self, simple_vault):
        _, tags = parse_vault_graph(simple_vault)
        assert "concepts" in tags.get("a", [])

    def test_empty_vault(self, vault):
        outgoing, tags = parse_vault_graph(vault)
        assert outgoing == {}

    def test_self_links_ignored(self, vault):
        _page(vault, "selfref", ["selfref"])
        outgoing, _ = parse_vault_graph(vault)
        assert "selfref" not in outgoing.get("selfref", [])

    def test_links_to_nonexistent_pages_excluded(self, vault):
        _page(vault, "orphan", ["doesnotexist"])
        outgoing, _ = parse_vault_graph(vault)
        assert outgoing["orphan"] == []

    @pytest.mark.parametrize("name", ["_archives", "_raw", "_readouts", "_staging"])
    def test_does_not_hide_personal_artifact_names(self, vault, name):
        directory = vault / name
        directory.mkdir()
        _page(directory, "legacy", [])

        outgoing, _ = parse_vault_graph(vault)

        assert "legacy" in outgoing

    def test_rejects_external_symlink_without_leaking_content(self, vault, tmp_path):
        raw = vault / "_raw"
        raw.mkdir()
        secret = tmp_path / "secret.md"
        secret.write_text("# SECRET-MARKER\n", encoding="utf-8")
        (raw / "leak.md").symlink_to(secret)

        with pytest.raises(RuntimeError, match="symlink") as raised:
            parse_vault_graph(vault)

        assert "SECRET-MARKER" not in str(raised.value)

    def test_ignores_unrelated_non_markdown_symlink(self, vault, tmp_path):
        _page(vault, "kept", [])
        secret = tmp_path / "image.png"
        secret.write_text("SECRET-MARKER\n", encoding="utf-8")
        (vault / "image.png").symlink_to(secret)

        outgoing, tags = parse_vault_graph(vault)

        assert "kept" in outgoing
        assert "SECRET-MARKER" not in str((outgoing, tags))


# ---------------------------------------------------------------------------
# god_nodes
# ---------------------------------------------------------------------------

class TestGodNodes:
    def test_c_is_top_hub(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        result = god_nodes(outgoing, top_n=3)
        # c has 2 in-links (from a and b), so should be in top 3
        top_pages = {r["page"] for r in result}
        assert "c" in top_pages

    def test_degree_sum(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        result = god_nodes(outgoing)
        for node in result:
            assert node["degree"] == node["in_degree"] + node["out_degree"]

    def test_respects_top_n(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        result = god_nodes(outgoing, top_n=2)
        assert len(result) <= 2


# ---------------------------------------------------------------------------
# dead_ends / isolated
# ---------------------------------------------------------------------------

class TestDeadEndsIsolated:
    def test_dead_ends(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        de = dead_ends(outgoing)
        assert "c" in de
        assert "f" in de
        assert "a" not in de

    def test_isolated(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        iso = isolated(outgoing)
        assert "d" in iso
        assert "a" not in iso
        assert "c" not in iso  # c has incoming links so not isolated


# ---------------------------------------------------------------------------
# Community detection
# ---------------------------------------------------------------------------

class TestCommunityDetection:
    def test_returns_list_of_sets(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        comms = detect_communities_greedy(outgoing)
        assert isinstance(comms, list)
        for c in comms:
            assert isinstance(c, set)

    def test_all_nodes_assigned(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        comms = detect_communities_greedy(outgoing)
        all_nodes = set(outgoing.keys())
        assigned = set()
        for c in comms:
            assigned |= c
        assert assigned == all_nodes

    def test_no_overlap(self, simple_vault):
        outgoing, _ = parse_vault_graph(simple_vault)
        comms = detect_communities_greedy(outgoing)
        seen = set()
        for c in comms:
            assert c.isdisjoint(seen), "Node appears in two communities"
            seen |= c

    def test_connected_cluster_grouped(self, vault):
        # a-b-c tightly connected, x-y-z separate — should land in different communities
        _page(vault, "a", ["b", "c"])
        _page(vault, "b", ["a", "c"])
        _page(vault, "c", ["a", "b"])
        _page(vault, "x", ["y", "z"])
        _page(vault, "y", ["x", "z"])
        _page(vault, "z", ["x", "y"])
        outgoing, _ = parse_vault_graph(vault)
        comms = detect_communities_greedy(outgoing)
        # At least 2 communities
        assert len(comms) >= 2

    def test_empty_graph(self, vault):
        outgoing, _ = parse_vault_graph(vault)
        comms = detect_communities_greedy(outgoing)
        assert comms == []


# ---------------------------------------------------------------------------
# Surprising connections
# ---------------------------------------------------------------------------

class TestSurprisingConnections:
    def test_cross_community_edge_found(self, vault):
        # Build two tight clusters with one bridge
        _page(vault, "a", ["b", "c", "x"])
        _page(vault, "b", ["a", "c"])
        _page(vault, "c", ["a", "b"])
        _page(vault, "x", ["y", "z"])
        _page(vault, "y", ["x", "z"])
        _page(vault, "z", ["x", "y"])
        outgoing, _ = parse_vault_graph(vault)
        comms = detect_communities_greedy(outgoing)
        sc = surprising_connections(outgoing, comms)
        sources = {s["source"] for s in sc}
        targets = {s["target"] for s in sc}
        assert sources | targets  # at least one cross-community edge found

    def test_no_intra_community_edges(self, vault):
        _page(vault, "a", ["b"])
        _page(vault, "b", ["a"])
        outgoing, _ = parse_vault_graph(vault)
        comms = [{"a", "b"}]  # one community
        sc = surprising_connections(outgoing, comms)
        assert sc == []

    def test_scores_positive(self, vault):
        _page(vault, "a", ["b", "x"])
        _page(vault, "b", ["a"])
        _page(vault, "x", ["y"])
        _page(vault, "y", ["x"])
        outgoing, _ = parse_vault_graph(vault)
        comms = detect_communities_greedy(outgoing)
        sc = surprising_connections(outgoing, comms)
        for item in sc:
            assert item["score"] > 0


# ---------------------------------------------------------------------------
# analyse_vault (integration)
# ---------------------------------------------------------------------------

class TestAnalyseVault:
    def test_root_views_do_not_affect_graph_statistics_or_metrics(self, vault):
        concepts = vault / "concepts"
        concepts.mkdir()
        _page(concepts, "topic", [])
        for name in ("index", "log", "hot"):
            _page(vault, name, ["topic"])

        result = analyse_vault(vault)

        assert result["stats"] == {"pages": 1, "edges": 0, "communities": 1}
        assert all(item["page"] not in {"index", "log", "hot"} for item in result["god_nodes"])
        assert not {"index", "log", "hot"}.intersection(result["dead_ends"])
        assert not {"index", "log", "hot"}.intersection(result["isolated"])

    def test_returns_all_keys(self, simple_vault):
        result = analyse_vault(simple_vault)
        assert set(result.keys()) == {
            "god_nodes", "communities", "surprising_connections",
            "dead_ends", "isolated", "stats",
        }

    def test_stats_correct(self, simple_vault):
        result = analyse_vault(simple_vault)
        assert result["stats"]["pages"] == 6
        assert result["stats"]["edges"] > 0

    def test_empty_vault(self, vault):
        result = analyse_vault(vault)
        assert result["stats"]["pages"] == 0

    def test_json_serialisable(self, simple_vault):
        result = analyse_vault(simple_vault)
        json.dumps(result)  # must not raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestGraphAnalyseCLI:
    def test_outputs_json(self, simple_vault):
        nested = simple_vault.parent / "work/nested"
        nested.mkdir(parents=True)
        proc = subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", "graph-analyse"],
            capture_output=True, text=True,
            cwd=nested,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert "god_nodes" in data

    def test_pretty_flag(self, simple_vault):
        proc = subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", "graph-analyse", "--pretty"],
            capture_output=True, text=True,
            cwd=simple_vault,
        )
        assert proc.returncode == 0
        assert "\n  " in proc.stdout

    def test_top_flag(self, simple_vault):
        proc = subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", "graph-analyse", "--top", "3"],
            capture_output=True, text=True,
            cwd=simple_vault,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert len(data["god_nodes"]) <= 3

    def test_missing_vault_exits_nonzero(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", "graph-analyse"],
            capture_output=True, text=True,
            cwd=tmp_path,
        )
        assert proc.returncode != 0

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

from obsidian_wiki import FORK_BASE_COMMIT, IMPLEMENTATION_ID, UPSTREAM_URL

ROOT = Path(__file__).resolve().parents[1]


def test_llmwikiops_identity_constants_are_stable() -> None:
    assert IMPLEMENTATION_ID == "evanzlh/llm-wiki-ops"
    assert UPSTREAM_URL == "https://github.com/Ar9av/obsidian-wiki"
    assert FORK_BASE_COMMIT == "5ef66b6bec8b26bab6594ac37fb4d8371469fbab"


def test_version_output_identifies_llmwikiops() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.startswith("llmwikiops ")
    assert "evanzlh/llm-wiki-ops" in result.stdout


def test_package_metadata_preserves_upstream_and_points_users_to_fork() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = tomllib.loads(text)["project"]
    assert project["name"] == "llm-wiki-ops"
    assert (
        project["description"]
        == "LLM-oriented operational framework for durable Markdown knowledge bases"
    )
    assert 'authors = [{ name = "Ar9av" }]' in text
    assert 'maintainers = [{ name = "evanzlh" }]' in text
    assert project["urls"] == {
        "Homepage": "https://github.com/evanzlh/llm-wiki-ops",
        "Repository": "https://github.com/evanzlh/llm-wiki-ops",
        "Issues": "https://github.com/evanzlh/llm-wiki-ops/issues",
        "Changelog": "https://github.com/evanzlh/llm-wiki-ops/releases",
        "Upstream": "https://github.com/Ar9av/obsidian-wiki",
    }
    assert project["scripts"] == {"llmwikiops": "obsidian_wiki.cli:main"}

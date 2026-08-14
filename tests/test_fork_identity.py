from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

from obsidian_wiki import FORK_BASE_COMMIT, IMPLEMENTATION_ID, UPSTREAM_URL

ROOT = Path(__file__).resolve().parents[1]
CURRENT_PRODUCT_IDENTITY = re.compile(
    r"(?<![.\w-])obsidian(?:-|\s+)wiki(?![\w-])", re.IGNORECASE
)


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


def test_only_llmwikiops_cli_and_protocol_names_remain_supported() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'llmwikiops = "obsidian_wiki.cli:main"' in pyproject
    assert 'obsidian-wiki = "obsidian_wiki.cli:main"' not in pyproject
    assert (ROOT / "obsidian_wiki").is_dir()
    assert ".obsidian-wiki/config.toml" in (
        ROOT / "docs/configuration.md"
    ).read_text(encoding="utf-8")


def test_current_product_prose_uses_llmwikiops_identity() -> None:
    """Stable filenames may retain the legacy name; their prose may not."""
    for relative in (
        ".gitignore",
        ".cursor/rules/obsidian-wiki.mdc",
        "obsidian_wiki/session_index.py",
        "obsidian_wiki/lint.py",
    ):
        contents = (ROOT / relative).read_text(encoding="utf-8")
        assert not CURRENT_PRODUCT_IDENTITY.search(contents), relative


def test_gitignore_setup_hint_uses_the_supported_cli_syntax() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "llmwikiops setup [DIR]" in gitignore
    assert "--portable" not in gitignore
    assert "--project" not in gitignore


def test_session_index_describes_its_stdlib_scope_without_misstating_dependencies() -> None:
    contents = (ROOT / "obsidian_wiki/session_index.py").read_text(encoding="utf-8")

    assert "`dependencies = []`" not in contents
    assert "Package dependencies are declared in `pyproject.toml`" in contents

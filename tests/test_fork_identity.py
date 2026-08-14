from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

import pytest

from obsidian_wiki import FORK_BASE_COMMIT, IMPLEMENTATION_ID, UPSTREAM_URL

ROOT = Path(__file__).resolve().parents[1]
LEGACY_IDENTITY = re.compile(
    r"obsidian_wiki|obsidian-wiki|obsidian\s+wiki|evanzlh/obsidian-wiki",
    re.IGNORECASE,
)
MANAGED_BOOTSTRAP_FILENAMES = (
    "agent/rules/obsidian-wiki.md",
    "agent/workflows/obsidian-wiki.md",
    "cursor/rules/obsidian-wiki.mdc",
    "kiro/steering/obsidian-wiki.md",
    "windsurf/rules/obsidian-wiki.md",
)


def _line_at(contents: str, offset: int) -> str:
    start = contents.rfind("\n", 0, offset) + 1
    end = contents.find("\n", offset)
    return contents[start:] if end == -1 else contents[start:end]


def _is_allowed_legacy_identity(
    relative: Path, contents: str, match: re.Match[str]
) -> bool:
    value = match.group().casefold()
    line = _line_at(contents, match.start()).strip()
    if value == "obsidian_wiki":
        return True
    if value == "evanzlh/obsidian-wiki":
        return False
    if match.start() and contents[match.start() - 1] == ".":
        next_character = contents[match.end() : match.end() + 1]
        if next_character in {"/", "'", '"'}:
            return True
        if relative == Path("obsidian_wiki/portable_manifest.py"):
            return line == '_SIDECAR = ".obsidian-wiki-manifest-mutation"'
        return relative == Path("obsidian_wiki/portable.py") and (
            "prefix=f" in line or "tempfile.mkdtemp(prefix=" in line
        )
    if contents[match.start() - len("Ar9av/") : match.start()] == "Ar9av/":
        return True
    if any(f'".{name}"' in line for name in MANAGED_BOOTSTRAP_FILENAMES):
        return True
    if relative == Path("extensions/brain-capture/popup.js"):
        return line == 'id: "obsidian-wiki-raw",'
    if relative == Path("obsidian_wiki/portable_manifest.py"):
        return line == 'marker = b"obsidian-wiki manifest capability probe\\n"'
    if relative != Path("obsidian_wiki/portable.py"):
        return False
    if line == '_LEGACY_BOOTSTRAP_HEADING = "# Obsidian Wiki Agent Instructions\\n\\n"':
        return True
    if any(
        marker in line
        for marker in (
            "obsidian-wiki:managed:",
            "obsidian-wiki:gitattributes:",
            "obsidian-wiki:portable-bootstrap",
        )
    ):
        return True
    return line.startswith('raise ValueError("malformed obsidian-wiki ')


def _current_source_paths() -> tuple[Path, ...]:
    paths = {
        *ROOT.glob("*.toml"),
        ROOT / ".gitignore",
        ROOT / "AGENTS.md",
        *(ROOT / "obsidian_wiki").rglob("*.py"),
        *(ROOT / "tools").rglob("*.py"),
        *(ROOT / "scripts").rglob("*.py"),
        *(ROOT / ".cursor").rglob("*.mdc"),
        *(ROOT / "extensions").rglob("*.js"),
        *(ROOT / "extensions").rglob("*.json"),
        *(ROOT / "extensions").rglob("*.html"),
    }
    return tuple(sorted(path for path in paths if path.is_file()))


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
    """Current source/config may retain only explicit compatibility identities."""
    disallowed = []
    for path in _current_source_paths():
        contents = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        for match in LEGACY_IDENTITY.finditer(contents):
            if not _is_allowed_legacy_identity(relative, contents, match):
                line = contents.count("\n", 0, match.start()) + 1
                snippet = _line_at(contents, match.start()).strip()
                disallowed.append(f"{relative}:{line}: {snippet}")
    assert not disallowed, disallowed


@pytest.mark.parametrize(
    ("relative", "contents", "allowed"),
    (
        (Path("obsidian_wiki/module.py"), "from obsidian_wiki import cli", True),
        (Path("obsidian_wiki/config.py"), 'root / ".obsidian-wiki/config.toml"', True),
        (Path("fixture.py"), 'root / ".obsidian-wiki-brand"', False),
        (Path("pyproject.toml"), "https://github.com/Ar9av/obsidian-wiki", True),
        (
            Path("obsidian_wiki/portable.py"),
            '_LEGACY_BOOTSTRAP_HEADING = "# Obsidian Wiki Agent Instructions\\n\\n"',
            True,
        ),
        (Path("obsidian_wiki/portable.py"), '".cursor/rules/obsidian-wiki.mdc"', True),
        (Path("extensions/brain-capture/popup.js"), 'id: "obsidian-wiki-raw",', True),
        (Path("fixture.py"), 'run "obsidian-wiki check"', False),
        (Path("fixture.py"), "# Obsidian Wiki Agent Instructions", False),
        (Path("fixture.py"), "https://github.com/evanzlh/obsidian-wiki", False),
    ),
)
def test_current_source_identity_detector_classifies_contexts(
    relative: Path, contents: str, allowed: bool
) -> None:
    matches = list(LEGACY_IDENTITY.finditer(contents))

    assert matches, contents
    assert all(
        _is_allowed_legacy_identity(relative, contents, match) for match in matches
    ) is allowed


def test_gitignore_setup_hint_uses_the_supported_cli_syntax() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "llmwikiops setup [DIR]" in gitignore
    assert "--portable" not in gitignore
    assert "--project" not in gitignore
    assert (
        "source checkout does not track portable repository-local agent mirrors"
        in gitignore
    )
    assert "symlinks" not in gitignore
    assert "adapters" not in gitignore


def test_session_index_describes_its_stdlib_scope_without_misstating_dependencies() -> None:
    contents = (ROOT / "obsidian_wiki/session_index.py").read_text(encoding="utf-8")

    assert "`dependencies = []`" not in contents
    assert "Package dependencies are declared in `pyproject.toml`" in contents

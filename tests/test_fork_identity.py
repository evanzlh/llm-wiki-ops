from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

import pytest

from obsidian_wiki import FORK_BASE_COMMIT, IMPLEMENTATION_ID, UPSTREAM_URL

ROOT = Path(__file__).resolve().parents[1]
FORMER_EXTERNAL_PROTOCOL = re.compile(
    r"(?i)(?:\.obsidian-wiki|"
    r"(?<![A-Za-z0-9_])obsidian-wiki(?![A-Za-z0-9_])|"
    r"(?-i:OBSIDIAN_WIKI_[A-Z0-9_]+)|obsidian\s+wiki)"
)
UPSTREAM_ATTRIBUTION = "https://github.com/Ar9av/obsidian-wiki"

def disallowed_protocol_matches(path: Path, text: str) -> list[str]:
    """Return former external protocol names outside the exact upstream URL."""
    violations: list[str] = []
    for match in FORMER_EXTERNAL_PROTOCOL.finditer(text):
        start = match.start()
        upstream_start = text.rfind(UPSTREAM_ATTRIBUTION)
        upstream_end = upstream_start + len(UPSTREAM_ATTRIBUTION)
        inside_exact_upstream = (
            upstream_start >= 0
            and upstream_start <= start < upstream_end
            and text[upstream_end : upstream_end + 1]
            in {"", " ", "\n", "\t", "'", '"', ")", "]", ">", ","}
        )
        if not inside_exact_upstream:
            line = text.count("\n", 0, start) + 1
            violations.append(f"{path}:{line}: {match.group()}")
    return violations


def is_specialized_surface(path: Path) -> bool:
    """Keep historical records, tests, and packaged prose under dedicated guards."""
    return (
        path.parts[:1] == ("tests",)
        or path.parts[:1] == ("docs",)
        or path.parts[:3] == ("obsidian_wiki", "_data", "skills")
        or path.parts[:3] == ("obsidian_wiki", "_data", "bootstrap")
    )


def _is_current_source_config_path(relative: Path) -> bool:
    if is_specialized_surface(relative):
        return False
    if relative in {
        Path(".gitignore"),
        Path("AGENTS.md"),
        Path("CLAUDE.md"),
        Path("GEMINI.md"),
        Path(".hermes.md"),
        Path(".github/copilot-instructions.md"),
    }:
        return True
    if relative.parent == Path(".") and relative.suffix == ".toml":
        return True
    if relative.parts[:1] in {("obsidian_wiki",), ("tools",), ("scripts",)}:
        return relative.suffix == ".py"
    if relative.parts[:1] == ("extensions",):
        return relative.suffix in {".html", ".js", ".json"}
    if relative.parts[:1] == (".cursor",):
        return relative.suffix == ".mdc"
    return relative.parts[:2] in {
        (".agent", "rules"),
        (".agent", "workflows"),
        (".windsurf", "rules"),
        (".kiro", "steering"),
    }


def _select_current_source_paths(tracked_relatives: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(
        sorted(
            ROOT / relative
            for relative in tracked_relatives
            if _is_current_source_config_path(relative)
        )
    )


def _current_source_paths() -> tuple[Path, ...]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    relatives = (Path(value) for value in tracked.split("\0") if value)
    return _select_current_source_paths(relatives)


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
    assert ".llmwikiops/config.toml" in (
        ROOT / "docs/configuration.md"
    ).read_text(encoding="utf-8")


def test_current_product_source_and_config_have_no_former_protocol() -> None:
    """The tracked production/config surface has no compatibility aliases."""
    disallowed: list[str] = []
    for path in _current_source_paths():
        contents = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        disallowed.extend(disallowed_protocol_matches(relative, contents))
    assert not disallowed, disallowed


def test_current_source_surface_covers_tracked_development_configs() -> None:
    relative_paths = {
        path.relative_to(ROOT) for path in _current_source_paths()
    }

    assert {
        Path("CLAUDE.md"),
        Path("GEMINI.md"),
        Path(".hermes.md"),
        Path(".agent/rules/llmwikiops.md"),
        Path(".agent/workflows/llmwikiops.md"),
        Path(".windsurf/rules/llmwikiops.md"),
        Path(".kiro/steering/llmwikiops.md"),
        Path(".github/copilot-instructions.md"),
        Path(".cursor/rules/llmwikiops.mdc"),
        Path("obsidian_wiki/portable.py"),
        Path("tools/check_readme_sync.py"),
        Path("pyproject.toml"),
        Path(".gitignore"),
        Path("extensions/brain-capture/popup.js"),
    } <= relative_paths
    assert not any(path.parts[:2] == ("docs", "superpowers") for path in relative_paths)
    assert not any(path.parts[:1] == ("docs",) for path in relative_paths)
    assert Path("docs/configuration.md") not in relative_paths
    assert Path("obsidian_wiki/_data/skills/llm-wiki/SKILL.md") not in relative_paths


def test_current_source_surface_uses_only_git_listed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed = Path("obsidian_wiki/portable.py")
    ambient = Path("obsidian_wiki/_identity_audit_scratch.py")
    calls = []

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=f"{listed}\0".encode("utf-8"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    selected = _current_source_paths()
    assert selected == (ROOT / listed,)
    assert ROOT / ambient not in selected
    assert calls == [
        ((["git", "ls-files", "-z"],), {"cwd": ROOT, "check": True, "capture_output": True}),
    ]


@pytest.mark.parametrize(
    ("path", "specialized"),
    (
        (Path("tests/test_protocol_identity.py"), True),
        (Path("docs/superpowers/specs/historical.md"), True),
        (Path("obsidian_wiki/_data/skills/llm-wiki/SKILL.md"), True),
        (Path("obsidian_wiki/_data/bootstrap/AGENTS.md"), True),
        (Path("obsidian_wiki/portable.py"), False),
        (Path("extensions/brain-capture/popup.js"), False),
    ),
)
def test_specialized_surfaces_are_excluded_by_exact_path_category(
    path: Path, specialized: bool
) -> None:
    assert is_specialized_surface(path) is specialized


@pytest.mark.parametrize(
    ("contents", "violations"),
    (
        ("from obsidian_wiki import cli", []),
        ("https://github.com/Ar9av/obsidian-wiki", []),
        ('root / ".obsidian-wiki/config.toml"', [".obsidian-wiki"]),
        ("<!-- obsidian-wiki:managed:start -->", ["obsidian-wiki"]),
        ('id: "obsidian-wiki-raw",', ["obsidian-wiki"]),
        ('_SIDECAR = ".obsidian-wiki-manifest-mutation"', [".obsidian-wiki"]),
        ('marker = b"obsidian-wiki manifest capability probe\\n"', ["obsidian-wiki"]),
        ("OBSIDIAN_WIKI_REPO=/tmp/repository", ["OBSIDIAN_WIKI_REPO"]),
        ("# Obsidian Wiki Agent Instructions", ["Obsidian Wiki"]),
        ("https://github.com/evanzlh/obsidian-wiki", ["obsidian-wiki"]),
        ("https://github.com/Ar9av/obsidian-wiki.evil", ["obsidian-wiki"]),
        ("ObSiDiAn-WiKi setup", ["ObSiDiAn-WiKi"]),
    ),
)
def test_former_protocol_detector_rejects_all_external_protocol_variants(
    contents: str, violations: list[str]
) -> None:
    found = disallowed_protocol_matches(Path("fixture.txt"), contents)
    assert [entry.rsplit(": ", 1)[1] for entry in found] == violations


def test_former_protocol_managed_assets_are_absent_from_source_tree() -> None:
    former = (
        ".agent/rules/obsidian-wiki.md",
        ".agent/workflows/obsidian-wiki.md",
        ".cursor/rules/obsidian-wiki.mdc",
        ".windsurf/rules/obsidian-wiki.md",
        ".kiro/steering/obsidian-wiki.md",
        "obsidian_wiki/_data/bootstrap/agent/rules/obsidian-wiki.md",
        "obsidian_wiki/_data/bootstrap/agent/workflows/obsidian-wiki.md",
        "obsidian_wiki/_data/bootstrap/cursor/rules/obsidian-wiki.mdc",
        "obsidian_wiki/_data/bootstrap/windsurf/rules/obsidian-wiki.md",
        "obsidian_wiki/_data/bootstrap/kiro/steering/obsidian-wiki.md",
    )
    assert not [relative for relative in former if (ROOT / relative).exists()]


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

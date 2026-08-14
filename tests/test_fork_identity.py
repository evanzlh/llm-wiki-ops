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
    r"(?i:OBSIDIAN_WIKI_[A-Z0-9_]+)|obsidian\s+wiki)"
)
UPSTREAM_ATTRIBUTION = "https://github.com/Ar9av/obsidian-wiki"

TRACKED_CATEGORIES = frozenset(
    {"production", "docs", "tests", "package-resource", "other"}
)
HISTORICAL_DOCUMENTS = frozenset(
    {
        Path("docs/superpowers/plans/2026-08-07-fork-identity-and-source-install.md"),
        Path("docs/superpowers/plans/2026-08-07-portable-config-and-setup.md"),
        Path("docs/superpowers/plans/2026-08-07-portable-migration-and-e2e.md"),
        Path("docs/superpowers/plans/2026-08-07-portable-transactions-and-derived-state.md"),
        Path("docs/superpowers/plans/2026-08-07-sharded-manifest-and-check.md"),
        Path("docs/superpowers/plans/2026-08-10-cli-runtime-context-and-recovery-guidance.md"),
        Path("docs/superpowers/plans/2026-08-10-portable-setup-installation-compatibility.md"),
        Path("docs/superpowers/plans/2026-08-10-source-reinstall-cache-refresh.md"),
        Path("docs/superpowers/plans/2026-08-11-portable-agent-preflight-cli.md"),
        Path("docs/superpowers/plans/2026-08-11-portable-agent-skill-docs.md"),
        Path("docs/superpowers/plans/2026-08-12-agent-context-and-full-skill-mirrors.md"),
        Path("docs/superpowers/plans/2026-08-12-portable-only.md"),
        Path("docs/superpowers/plans/2026-08-13-single-operation-log-and-tracked-hot.md"),
        Path("docs/superpowers/plans/2026-08-14-llmwikiops-independence-and-rename.md"),
        Path("docs/superpowers/plans/2026-08-14-llmwikiops-protocol-rename.md"),
        Path("docs/superpowers/specs/2026-08-07-portable-repo-mode-design.md"),
        Path("docs/superpowers/specs/2026-08-10-cli-runtime-context-and-recovery-guidance-design.md"),
        Path("docs/superpowers/specs/2026-08-10-portable-setup-installation-compatibility-design.md"),
        Path("docs/superpowers/specs/2026-08-11-portable-agent-ergonomics-design.md"),
        Path("docs/superpowers/specs/2026-08-12-agent-context-and-full-skill-mirrors-design.md"),
        Path("docs/superpowers/specs/2026-08-12-portable-only-design.md"),
        Path("docs/superpowers/specs/2026-08-13-single-operation-log-and-tracked-hot-design.md"),
        Path("docs/superpowers/specs/2026-08-14-llmwikiops-independence-and-rename-design.md"),
        Path("docs/superpowers/specs/2026-08-14-llmwikiops-protocol-rename-design.md"),
    }
)
TEST_PROTOCOL_GUARDS = frozenset(
    {
        Path("tests/test_asset_artifact_parity.py"),
        Path("tests/test_context_pack_docs.py"),
        Path("tests/test_doctor.py"),
        Path("tests/test_fork_identity.py"),
        Path("tests/test_installation_policy.py"),
        Path("tests/test_portable_config.py"),
        Path("tests/test_portable_human_docs.py"),
        Path("tests/test_portable_only_contract.py"),
        Path("tests/test_portable_setup.py"),
        Path("tests/test_portable_skill_protocol.py"),
        Path("tests/test_portable_write_protocol.py"),
        Path("tests/test_protocol_identity.py"),
        Path("tests/test_query_cli.py"),
        Path("tests/test_scripts_packaging.py"),
        Path("tests/test_transaction.py"),
    }
)

def disallowed_protocol_matches(path: Path, text: str) -> list[str]:
    """Return former external protocol names outside the exact upstream URL."""
    violations: list[str] = []
    for match in FORMER_EXTERNAL_PROTOCOL.finditer(text):
        start = match.start()
        attribution_starts = [
            index
            for index in range(len(text))
            if text.startswith(UPSTREAM_ATTRIBUTION, index)
        ]
        inside_exact_upstream = any(
            attribution_start <= start < attribution_start + len(UPSTREAM_ATTRIBUTION)
            and text[
                attribution_start + len(UPSTREAM_ATTRIBUTION) : attribution_start
                + len(UPSTREAM_ATTRIBUTION)
                + 1
            ]
            in {"", " ", "\n", "\t", "'", '"', ")", "]", ">", ","}
            for attribution_start in attribution_starts
        )
        if match.group() == "obsidian_wiki_cwd":
            continue
        if not inside_exact_upstream:
            line = text.count("\n", 0, start) + 1
            violations.append(f"{path}:{line}: {match.group()}")
    return violations


def _tracked_manifest() -> frozenset[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    return frozenset(Path(value) for value in tracked.split("\0") if value)


def classify_tracked_path(relative: Path) -> str:
    if relative.parts[:1] == ("tests",):
        return "tests"
    if relative.parts[:1] == ("docs",) or relative.name in {"README.md", "README_ZH.md"}:
        return "docs"
    if relative.parts[:2] == ("obsidian_wiki", "_data"):
        return "package-resource"
    if relative in {
        Path(".gitattributes"),
        Path(".gitignore"),
        Path("AGENTS.md"),
        Path("CLAUDE.md"),
        Path("GEMINI.md"),
        Path(".hermes.md"),
        Path(".github/copilot-instructions.md"),
    }:
        return "production"
    if relative.parent == Path(".") and relative.suffix == ".toml":
        return "production"
    if relative.name == "uv.lock":
        return "production"
    if relative.parts[:1] in {("obsidian_wiki",), ("tools",), ("scripts",)}:
        return "production" if relative.suffix == ".py" else "other"
    if relative.parts[:1] == ("extensions",):
        return "production" if relative.suffix in {".css", ".html", ".js", ".json"} else "other"
    if relative.parts[:1] == (".github",):
        return "production"
    if relative.parts[:1] == (".cursor",):
        return "production" if relative.suffix == ".mdc" else "other"
    if relative.parts[:2] in {
        (".agent", "rules"),
        (".agent", "workflows"),
        (".windsurf", "rules"),
        (".kiro", "steering"),
    }:
        return "production"
    return "other"


def _select_current_source_paths(tracked_relatives: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(
        sorted(
            ROOT / relative
            for relative in tracked_relatives
            if classify_tracked_path(relative) == "production"
        )
    )


def _current_source_paths() -> tuple[Path, ...]:
    return _select_current_source_paths(_tracked_manifest())


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
        Path("extensions/brain-capture/popup.css"),
        Path(".gitattributes"),
        Path(".github/workflows/readme-sync.yml"),
        Path("uv.lock"),
    } <= relative_paths
    assert Path("docs/configuration.md") not in relative_paths
    assert Path("obsidian_wiki/_data/legacy-skill-digests-v1.json") not in relative_paths


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


def test_tracked_manifest_has_one_exhaustive_path_category() -> None:
    manifest = _tracked_manifest()
    partitions = {
        category: {path for path in manifest if classify_tracked_path(path) == category}
        for category in TRACKED_CATEGORIES
    }

    assert manifest
    assert set().union(*partitions.values()) == manifest
    assert sum(len(paths) for paths in partitions.values()) == len(manifest)
    assert {
        "production": Path(".gitattributes"),
        "docs": Path("README.md"),
        "tests": Path("tests/test_fork_identity.py"),
        "package-resource": Path("obsidian_wiki/_data/legacy-skill-digests-v1.json"),
        "other": Path("LICENSE"),
    }.items() <= {
        (category, path)
        for category, paths in partitions.items()
        for path in paths
    }


def test_tracked_docs_tests_and_resources_have_dedicated_protocol_guards() -> None:
    manifest = _tracked_manifest()
    docs = {path for path in manifest if classify_tracked_path(path) == "docs"}
    tests = {path for path in manifest if classify_tracked_path(path) == "tests"}
    resources = {
        path for path in manifest if classify_tracked_path(path) == "package-resource"
    }

    assert HISTORICAL_DOCUMENTS <= docs
    assert TEST_PROTOCOL_GUARDS <= tests
    assert Path("obsidian_wiki/_data/legacy-skill-digests-v1.json") in resources

    for relative in docs - HISTORICAL_DOCUMENTS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        if relative == Path("docs/fork.md"):
            assert "https://github.com/Ar9av/obsidian-wiki" in text
            assert "https://github.com/evanzlh/obsidian-wiki" not in text
            continue
        if relative == Path("README.md"):
            assert "https://github.com/Ar9av/obsidian-wiki" in text
            assert "The former `.obsidian-wiki/` state is not detected" in text
            continue
        if relative == Path("README_ZH.md"):
            assert "https://github.com/Ar9av/obsidian-wiki" in text
            assert "旧的 `.obsidian-wiki/` 状态不会检测、读取、迁移或删除" in text
            continue
        violations = disallowed_protocol_matches(relative, text)
        assert not violations, violations
    for relative in resources:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert not disallowed_protocol_matches(relative, text), relative
    test_references = {
        relative
        for relative in tests
        if FORMER_EXTERNAL_PROTOCOL.search((ROOT / relative).read_text(encoding="utf-8"))
    }
    assert test_references <= TEST_PROTOCOL_GUARDS


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
        ("Obsidian_Wiki_REPO=/tmp/repository", ["Obsidian_Wiki_REPO"]),
        ("# Obsidian Wiki Agent Instructions", ["Obsidian Wiki"]),
        ("https://github.com/evanzlh/obsidian-wiki", ["obsidian-wiki"]),
        ("https://github.com/Ar9av/obsidian-wiki.evil", ["obsidian-wiki"]),
        ("ObSiDiAn-WiKi setup", ["ObSiDiAn-WiKi"]),
        (
            "https://github.com/Ar9av/obsidian-wiki\n"
            "https://github.com/Ar9av/obsidian-wiki",
            [],
        ),
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
    assert not set(former) & _tracked_manifest()


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

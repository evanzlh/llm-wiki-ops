from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import cli
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable import (
    MANAGED_END,
    MANAGED_START,
    MANIFEST_MARKER,
    PORTABLE_ROOT_IGNORE,
    PORTABLE_VAULT_DIRS,
    compatible_cli_spec,
    ensure_portable_gitignore,
    merge_managed_block,
    setup_portable_repo,
)


INDEX_BYTES = b'''---
title: Wiki Index
---

# Wiki Index

```query
path:"concepts" OR path:"entities" OR path:"skills" OR path:"references" OR path:"synthesis" OR path:"projects"
```
'''

LOG_BYTES = b'''---
title: Wiki Operation Log
---

# Wiki Operation Log

```query
path:"journal/operations"
```
'''

WIKI_INGEST_ADAPTER = '''---
name: wiki-ingest
description: Portable adapter for the repository-canonical wiki-ingest skill.
---

# Portable skill adapter

Read and follow `../../../.skills/wiki-ingest/SKILL.md` from this repository. Resolve that path from this adapter file, never from the process working directory.
'''


def run_cli(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


@pytest.fixture
def tiny_skills(tmp_path: Path) -> Path:
    source = tmp_path / "canonical-skills"
    for name in ("wiki-ingest", "wiki-query"):
        skill = source / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    nested_git = source / "wiki-ingest" / ".git"
    nested_git.mkdir()
    (nested_git / "config").write_text("must not be copied\n", encoding="utf-8")
    return source


def test_setup_portable_creates_repo_without_global_side_effects(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    target = work / "knowledge"
    work.mkdir()

    result = run_cli(home, work, "setup", "--portable", str(target))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert str(target.resolve()) in result.stdout
    assert f"Open {target.resolve() / 'wiki'} in Obsidian" in result.stdout
    assert not (home / ".obsidian-wiki").exists()
    assert IMPLEMENTATION_ID in (target / ".obsidian-wiki/config.toml").read_text()
    assert (target / "sources").is_dir()
    assert (target / "wiki/concepts").is_dir()
    assert json.loads((target / "wiki/.manifest.json").read_text()) == MANIFEST_MARKER
    assert not (target / "wiki/hot.md").exists()
    assert not (target / ".venv").exists()
    assert not (target / "obsidian_wiki").exists()
    assert not (target / ".git").exists()
    assert "wiki/hot.md" in (target / ".gitignore").read_text().splitlines()
    assert (target / ".skills/wiki-ingest/SKILL.md").read_bytes() == (
        cli.skills_dir() / "wiki-ingest/SKILL.md"
    ).read_bytes()
    agents = (target / "AGENTS.md").read_text()
    assert MANAGED_START in agents and MANAGED_END in agents
    assert "## Team conventions" in agents
    assert "README Translation Parity" not in agents


def test_portable_adapters_are_regular_relative_files_and_survive_repo_move(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    target = tmp_path / "knowledge"
    result = run_cli(home, tmp_path, "setup", "--portable", str(target))
    assert result.returncode == 0, result.stderr
    adapter = target / ".claude/skills/wiki-ingest/SKILL.md"

    assert adapter.is_file()
    assert not adapter.is_symlink()
    assert adapter.read_text(encoding="utf-8") == WIKI_INGEST_ADAPTER
    assert "../../../.skills/wiki-ingest/SKILL.md" in adapter.read_text()

    renamed = tmp_path / "renamed-repository"
    target.rename(renamed)
    moved_adapter = renamed / ".claude/skills/wiki-ingest/SKILL.md"
    referenced = moved_adapter.parent / "../../../.skills/wiki-ingest/SKILL.md"
    assert referenced.resolve().read_bytes() == (
        cli.skills_dir() / "wiki-ingest/SKILL.md"
    ).read_bytes()


def test_setup_portable_rejects_legacy_setup_flags(tmp_path: Path) -> None:
    target = tmp_path / "portable"
    result = run_cli(
        tmp_path / "home",
        tmp_path,
        "setup",
        "--portable",
        str(target),
        "--vault",
        str(tmp_path / "vault"),
    )

    assert result.returncode != 0
    assert "cannot be combined" in result.stderr
    assert not target.exists()


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2026.8.3", ">=2026.8,<2026.9"),
        ("2026.8", ">=2026.8,<2026.9"),
        ("2026.8.3.dev4", "==2026.8.3.dev4"),
        ("2026.8rc1", "==2026.8rc1"),
        ("2026.8.3+wheel.7", "==2026.8.3"),
    ],
)
def test_compatible_cli_spec(version: str, expected: str) -> None:
    assert compatible_cli_spec(version) == expected


def test_compatible_cli_spec_rejects_invalid_version() -> None:
    with pytest.raises(ValueError, match="invalid CLI version"):
        compatible_cli_spec("not a version")


def test_portable_config_is_relative_minimal_and_loadable(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    config_path = root / ".obsidian-wiki/config.toml"
    text = config_path.read_text(encoding="utf-8")

    assert 'schema_version = 1' in text
    assert f'implementation = "{IMPLEMENTATION_ID}"' in text
    assert 'requires_cli = ">=2026.8,<2026.9"' in text
    assert '[paths]' in text
    assert 'vault = "wiki"' in text
    assert 'sources = ["sources"]' in text
    assert 'skills = ".skills"' in text
    assert 'local_state = ".obsidian-wiki/local"' in text
    assert "OBSIDIAN_WIKI_REPO" not in text
    assert "history" not in text.lower()
    assert "api" not in text.lower()
    assert "schema_source" not in text

    loaded = load_portable_config(
        config_path,
        installed_version="2026.8.3",
        implementation=IMPLEMENTATION_ID,
    )
    assert loaded.vault == (root / "wiki").resolve()
    assert loaded.sources == ((root / "sources").resolve(),)
    assert loaded.skills == (root / ".skills").resolve()
    assert loaded.local_state == (root / ".obsidian-wiki/local").resolve()
    assert loaded.settings == {
        "OBSIDIAN_CATEGORIES": "concepts,entities,skills,references,synthesis,journal,projects",
        "OBSIDIAN_MAX_PAGES_PER_INGEST": "15",
        "OBSIDIAN_LINK_FORMAT": "wikilink",
        "OBSIDIAN_RAW_DIR": "_raw",
        "OBSIDIAN_TRUST_STRICT": "false",
    }


def test_stable_vault_files_are_exact_and_second_setup_preserves_them_and_owner_text(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    index = root / "wiki/index.md"
    log = root / "wiki/log.md"
    assert index.read_bytes() == INDEX_BYTES
    assert log.read_bytes() == LOG_BYTES
    assert not index.read_bytes().endswith(b"\n\n")
    assert not log.read_bytes().endswith(b"\n\n")

    fixed_time = 1_700_000_000_000_000_000
    os.utime(index, ns=(fixed_time, fixed_time))
    os.utime(log, ns=(fixed_time, fixed_time))
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nOwner terminology: use garden.\n")

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert index.stat().st_mtime_ns == fixed_time
    assert log.stat().st_mtime_ns == fixed_time
    assert index.read_bytes() == INDEX_BYTES
    assert log.read_bytes() == LOG_BYTES
    assert "Owner terminology: use garden." in agents.read_text()
    assert agents.read_text().count(MANAGED_START) == 1
    assert agents.read_text().count(MANAGED_END) == 1


def test_vault_layout_manifest_and_obsidian_json_contract(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8", source_skills=tiny_skills)
    vault = root / "wiki"

    for relative in (*PORTABLE_VAULT_DIRS, ".manifest/sources"):
        assert (vault / relative).is_dir(), relative
    assert (root / "sources").is_dir()
    assert not (vault / "_staging").exists()
    assert not (vault / "hot.md").exists()
    assert json.loads((vault / ".manifest.json").read_text()) == {
        "schema_version": 2,
        "storage": "sharded",
        "entries": ".manifest/sources",
    }
    for relative in (".obsidian/app.json", ".obsidian/appearance.json"):
        parsed = json.loads((vault / relative).read_text())
        assert isinstance(parsed, dict) and parsed


def test_gitignore_preserves_owner_entries_and_adds_portable_state_idempotently(
    tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    ignore = root / ".gitignore"
    ignore.write_text("*.pyc\nowner-cache/\n", encoding="utf-8")

    ensure_portable_gitignore(root, "notes/brain")
    first = ignore.read_text(encoding="utf-8")
    ensure_portable_gitignore(root, "notes/brain")

    assert ignore.read_text(encoding="utf-8") == first
    assert first.startswith("*.pyc\nowner-cache/\n")
    assert first.splitlines() == [
        "*.pyc",
        "owner-cache/",
        *PORTABLE_ROOT_IGNORE,
        "notes/brain/hot.md",
        "notes/brain/.obsidian/workspace.json",
        "notes/brain/.obsidian/workspace-mobile.json",
        "notes/brain/.trash/",
    ]
    assert str(root.resolve()) not in first


def test_canonical_skills_and_all_agent_adapters_are_copies_with_relative_targets(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert (root / ".skills/wiki-ingest").is_dir()
    assert not any(path.is_symlink() for path in (root / ".skills").rglob("*"))
    for agent_relative, _label in cli.PROJECT_AGENT_DIRS:
        for skill_name in ("wiki-ingest", "wiki-query"):
            adapter = root / agent_relative / skill_name / "SKILL.md"
            assert adapter.is_file()
            assert not adapter.is_symlink()
            expected_target = root / ".skills" / skill_name / "SKILL.md"
            relative = os.path.relpath(expected_target, adapter.parent).replace(os.sep, "/")
            assert f"`{relative}`" in adapter.read_text(encoding="utf-8")
            assert str(root) not in adapter.read_text(encoding="utf-8")


def test_bootstrap_files_are_ordinary_markdown_with_correct_agents_reference(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    references = {
        "CLAUDE.md": "AGENTS.md",
        "GEMINI.md": "AGENTS.md",
        ".hermes.md": "AGENTS.md",
        ".agent/rules/obsidian-wiki.md": "../../AGENTS.md",
        ".agent/workflows/obsidian-wiki.md": "../../AGENTS.md",
        ".cursor/rules/obsidian-wiki.mdc": "../../AGENTS.md",
        ".windsurf/rules/obsidian-wiki.md": "../../AGENTS.md",
        ".kiro/steering/obsidian-wiki.md": "../../AGENTS.md",
        ".github/copilot-instructions.md": "../AGENTS.md",
    }

    for relative, reference in references.items():
        path = root / relative
        assert path.is_file(), relative
        assert not path.is_symlink(), relative
        assert f"`{reference}`" in path.read_text(encoding="utf-8")


def test_root_agents_is_portable_dedicated_and_preserves_team_conventions(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    text = (root / "AGENTS.md").read_text(encoding="utf-8")

    assert ".obsidian-wiki/config.toml" in text
    assert ".skills/<name>/SKILL.md" in text
    assert "wiki/AGENTS.md" in text
    assert "transaction-only" in text
    assert "commit, push, or open a pull request" in text
    assert "## Team conventions" in text
    assert "terminology" in text and "writing style" in text
    assert "README Translation Parity" not in text


def test_merge_managed_block_only_replaces_managed_region() -> None:
    existing = f"Owner preface\n\n{MANAGED_START}\nold\n{MANAGED_END}\n\nOwner footer\n"
    merged = merge_managed_block(existing, "new instructions")

    assert merged == (
        f"Owner preface\n\n{MANAGED_START}\nnew instructions\n{MANAGED_END}"
        "\n\nOwner footer\n"
    )
    inserted = merge_managed_block("Owner-only text\n", "managed")
    assert inserted.startswith(f"{MANAGED_START}\nmanaged\n{MANAGED_END}\n\n")
    assert inserted.endswith("Owner-only text\n")


@pytest.mark.parametrize(
    "malformed",
    [
        f"{MANAGED_START}\nmissing end\n",
        f"missing start\n{MANAGED_END}\n",
        f"{MANAGED_END}\nreversed\n{MANAGED_START}\n",
        f"{MANAGED_START}\na\n{MANAGED_START}\nb\n{MANAGED_END}\n",
        f"{MANAGED_START}\na\n{MANAGED_END}\nb\n{MANAGED_END}\n",
    ],
)
def test_merge_managed_block_rejects_malformed_markers(malformed: str) -> None:
    with pytest.raises(ValueError, match="managed markers"):
        merge_managed_block(malformed, "replacement")


@pytest.mark.parametrize(
    "legacy_args",
    [
        ["--vault", "vault"],
        ["--project", "project"],
        ["--project-only"],
        ["--copy"],
        ["--remote", "https://example.test/wiki.git"],
    ],
)
def test_setup_portable_rejects_every_legacy_setup_flag(
    legacy_args: list[str],
    tmp_path: Path,
) -> None:
    target = tmp_path / "portable"
    result = run_cli(
        tmp_path / "home",
        tmp_path,
        "setup",
        "--portable",
        str(target),
        *legacy_args,
    )
    assert result.returncode != 0
    assert "cannot be combined" in result.stderr
    assert not target.exists()


def test_setup_portable_without_directory_defaults_to_current_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "cwd-target"
    target.mkdir()

    result = run_cli(tmp_path / "home", target, "setup", "--portable")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert str(target.resolve()) in result.stdout
    assert (target / ".obsidian-wiki/config.toml").is_file()


def test_generated_portable_files_do_not_embed_source_or_home_paths(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    forbidden = (str(tiny_skills.resolve()), str(Path.home().resolve()))

    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert all(value not in text for value in forbidden), path
    assert not any(path.name == ".git" for path in root.rglob("*"))
    assert not (root / "obsidian_wiki").exists()
    assert not (root / ".venv").exists()

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import cli
from obsidian_wiki import portable
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
    upgrade_portable_skills,
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


def snapshot_tree(root: Path) -> tuple[tuple[str, str, bytes | str], ...]:
    """Capture types and content without following directory symlinks."""
    if not root.exists() and not root.is_symlink():
        return ()
    entries: list[tuple[str, str, bytes | str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            entries.append((relative, "dir", b""))
        else:
            entries.append((relative, "file", path.read_bytes()))
    return tuple(entries)


def make_skill_source(root: Path, name: str = "wiki-ingest") -> Path:
    source = root / "source-skills"
    skill = source / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
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


def test_setup_rejects_symlinked_config_tree_without_external_writes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside-config"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched\n", encoding="utf-8")
    (root / ".obsidian-wiki").symlink_to(outside, target_is_directory=True)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="symlink"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"
    assert not (outside / "config.toml").exists()


def test_rerun_rejects_symlinked_agent_skill_without_changing_target(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    adapter_dir = root / ".claude/skills/wiki-ingest"
    shutil.rmtree(adapter_dir)
    outside = tmp_path / "outside-skill"
    outside.mkdir()
    external_skill = outside / "SKILL.md"
    external_skill.write_text("external owner skill\n", encoding="utf-8")
    adapter_dir.symlink_to(outside, target_is_directory=True)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="symlink"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before
    assert external_skill.read_text(encoding="utf-8") == "external owner skill\n"


@pytest.mark.parametrize("managed_relative", ["AGENTS.md", ".github", "wiki/concepts"])
def test_rerun_rejects_symlinked_managed_parent_file_or_descendant(
    managed_relative: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    managed = root / managed_relative
    outside = tmp_path / f"outside-{managed.name}"
    if managed.is_dir():
        shutil.rmtree(managed)
        outside.mkdir()
        (outside / "sentinel").write_text("outside\n", encoding="utf-8")
        managed.symlink_to(outside, target_is_directory=True)
    else:
        managed.unlink()
        outside.write_text("outside\n", encoding="utf-8")
        managed.symlink_to(outside)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="symlink"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before
    if outside.is_file():
        assert outside.read_text(encoding="utf-8") == "outside\n"
    else:
        assert (outside / "sentinel").read_text(encoding="utf-8") == "outside\n"


def test_direct_config_writer_rejects_symlinked_managed_parent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".obsidian-wiki").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        portable.write_portable_config(root, version="2026.8.3")

    assert list(outside.iterdir()) == []


def test_rerun_rejects_managed_bootstrap_parent_file_before_any_write(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    shutil.rmtree(root / ".github")
    (root / ".github").write_text("owner collision\n", encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="parent"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


@pytest.mark.parametrize("link_location", ["top-level", "nested", "skill-file"])
def test_source_skill_symlinks_are_rejected_without_materializing_external_content(
    link_location: str, tmp_path: Path
) -> None:
    source = make_skill_source(tmp_path)
    secret = tmp_path / "external-secret"
    secret.write_text("do not copy me\n", encoding="utf-8")
    if link_location == "top-level":
        (source / "linked-skill").symlink_to(source / "wiki-ingest", target_is_directory=True)
    elif link_location == "nested":
        (source / "wiki-ingest/secret-link").symlink_to(secret)
    else:
        skill_file = source / "wiki-ingest/SKILL.md"
        skill_file.unlink()
        skill_file.symlink_to(secret)
    target = tmp_path / "repo"

    with pytest.raises(ValueError, match="symlink"):
        setup_portable_repo(target, version="2026.8.3", source_skills=source)

    assert not target.exists()
    assert secret.read_text(encoding="utf-8") == "do not copy me\n"


def test_only_real_skill_directories_are_copied(tmp_path: Path) -> None:
    source = make_skill_source(tmp_path)
    (source / "top-level.txt").write_text("not a skill\n", encoding="utf-8")
    (source / "not-a-skill").mkdir()
    (source / "not-a-skill/note.md").write_text("ordinary\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git/SKILL.md").write_text("# fake cache skill\n", encoding="utf-8")
    root = tmp_path / "repo"

    setup_portable_repo(root, version="2026.8.3", source_skills=source)

    assert {entry.name for entry in (root / ".skills").iterdir()} == {"wiki-ingest"}
    assert not (root / ".claude/skills/top-level.txt").exists()
    assert not (root / ".claude/skills/not-a-skill").exists()


def test_source_copy_excludes_vcs_environment_and_cache_artifacts(tmp_path: Path) -> None:
    source = make_skill_source(tmp_path)
    skill = source / "wiki-ingest"
    excluded_dirs = (
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".AppleDouble",
        ".LSOverride",
        ".Spotlight-V100",
        ".Trashes",
    )
    for name in excluded_dirs:
        directory = skill / "nested" / name
        directory.mkdir(parents=True)
        (directory / "payload").write_text("excluded\n", encoding="utf-8")
    excluded_files = (
        "bytecode.pyc",
        "optimized.pyo",
        ".env",
        ".env.local",
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
        "Icon\r",
        "._resource",
    )
    for name in excluded_files:
        path = skill / "nested" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("excluded\n", encoding="utf-8")
    (skill / "nested/keep.md").write_text("keep\n", encoding="utf-8")
    root = tmp_path / "repo"

    setup_portable_repo(root, version="2026.8.3", source_skills=source)

    copied = root / ".skills/wiki-ingest"
    assert (copied / "nested/keep.md").read_text(encoding="utf-8") == "keep\n"
    assert not any(path.name in excluded_dirs for path in copied.rglob("*"))
    assert not any(
        path.name in excluded_files
        for path in copied.rglob("*")
    )


def test_setup_rejects_unrelated_nonempty_target_without_changes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "owner-project"
    root.mkdir()
    (root / "README.md").write_text("owner repository\n", encoding="utf-8")
    (root / "random.bin").write_bytes(b"\x00\x01owner")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="not a portable"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


def test_existing_portable_rerun_preserves_owner_config_and_unmanaged_files(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    config = root / ".obsidian-wiki/config.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + 'OBSIDIAN_ALLOWED_LIFECYCLES = "draft,reviewed"\n',
        encoding="utf-8",
    )
    config_bytes = config.read_bytes()
    agents = root / "AGENTS.md"
    agents.write_text(
        "Owner preface.\n\n" + agents.read_text(encoding="utf-8") + "\nOwner footer.\n",
        encoding="utf-8",
    )
    owner_files = {
        "CLAUDE.md": "owner Claude rules\n",
        ".cursor/rules/obsidian-wiki.mdc": "owner Cursor rules\n",
        ".claude/skills/wiki-ingest/SKILL.md": "owner adapter\n",
    }
    for relative, content in owner_files.items():
        (root / relative).write_text(content, encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git/config").write_text("owner git metadata\n", encoding="utf-8")

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert config.read_bytes() == config_bytes
    assert "Owner preface." in agents.read_text(encoding="utf-8")
    assert "Owner footer." in agents.read_text(encoding="utf-8")
    for relative, content in owner_files.items():
        assert (root / relative).read_text(encoding="utf-8") == content
    assert (root / ".git/config").read_text(encoding="utf-8") == "owner git metadata\n"


def test_generated_bootstrap_uses_managed_block_and_preserves_owner_text_on_rerun(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    alias = root / "CLAUDE.md"
    initial = alias.read_text(encoding="utf-8")
    assert MANAGED_START in initial and MANAGED_END in initial
    alias.write_text(initial + "\nOwner alias convention.\n", encoding="utf-8")

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    rerun = alias.read_text(encoding="utf-8")
    assert rerun.count(MANAGED_START) == 1
    assert rerun.count(MANAGED_END) == 1
    assert "Owner alias convention." in rerun


def test_setup_portable_rerun_preserves_appended_team_policy(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    first = run_cli(tmp_path / "home", tmp_path, "setup", "--portable", str(root))
    assert first.returncode == 0, first.stderr
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8") + "\n## Team policy\nUse our glossary.\n",
        encoding="utf-8",
    )

    second = run_cli(tmp_path / "home", tmp_path, "setup", "--portable", str(root))

    assert second.returncode == 0, second.stderr
    assert "## Team policy\nUse our glossary." in agents.read_text(encoding="utf-8")


def test_repo_upgrade_skills_repairs_adapter_and_preserves_team_sentence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    setup = run_cli(home, tmp_path, "setup", "--portable", str(root))
    assert setup.returncode == 0, setup.stderr
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8") + "\nTeam-owned sentence.\n",
        encoding="utf-8",
    )
    adapter = root / ".claude/skills/wiki-ingest/SKILL.md"
    adapter.unlink()

    result = run_cli(home, root, "repo", "upgrade-skills")

    assert result.returncode == 0, result.stderr
    assert "Team-owned sentence." in agents.read_text(encoding="utf-8")
    assert adapter.read_text(encoding="utf-8") == WIKI_INGEST_ADAPTER


def test_initial_setup_writes_exact_managed_skills_inventory(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    inventory = root / ".obsidian-wiki/managed-skills.json"
    payload = {
        "implementation": IMPLEMENTATION_ID,
        "skills": ["wiki-ingest", "wiki-query"],
        "skills_version": "2026.8.3",
    }
    assert inventory.read_bytes() == (
        json.dumps(payload, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def test_setup_rerun_preserves_inventory_config_owner_and_managed_skill_bytes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    config = root / ".obsidian-wiki/config.toml"
    canonical = root / ".skills/wiki-ingest/SKILL.md"
    inventory_bytes = inventory.read_bytes()
    config.write_text(config.read_text() + 'OBSIDIAN_ALLOWED_LIFECYCLES = "draft"\n')
    config_bytes = config.read_bytes()
    canonical.write_text("owner-edited managed skill\n", encoding="utf-8")
    owner_skill = root / ".skills/team-skill"
    owner_skill.mkdir()
    (owner_skill / "notes.txt").write_text("owner\n", encoding="utf-8")
    owner_adapter = root / ".claude/skills/team-skill"
    owner_adapter.mkdir()
    (owner_adapter / "notes.txt").write_text("owner adapter\n", encoding="utf-8")
    owner_bootstrap = root / ".cursor/rules/team.mdc"
    owner_bootstrap.write_text("owner bootstrap\n", encoding="utf-8")
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nOwner ending.\n", encoding="utf-8")
    bundled_new = tiny_skills / "wiki-new"
    bundled_new.mkdir()
    (bundled_new / "SKILL.md").write_text("# new\n", encoding="utf-8")
    before_owner_skill = snapshot_tree(owner_skill)
    before_owner_adapter = snapshot_tree(owner_adapter)

    setup_portable_repo(root, version="2026.8.4", source_skills=tiny_skills)

    assert inventory.read_bytes() == inventory_bytes
    assert config.read_bytes() == config_bytes
    assert canonical.read_text(encoding="utf-8") == "owner-edited managed skill\n"
    assert not (root / ".skills/wiki-new").exists()
    assert snapshot_tree(owner_skill) == before_owner_skill
    assert snapshot_tree(owner_adapter) == before_owner_adapter
    assert owner_bootstrap.read_text(encoding="utf-8") == "owner bootstrap\n"
    assert "Owner ending." in agents.read_text(encoding="utf-8")


def test_setup_migrates_pristine_pre_inventory_repo_only_when_skills_are_exact(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    inventory.unlink()

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert json.loads(inventory.read_text()) == {
        "implementation": IMPLEMENTATION_ID,
        "skills": ["wiki-ingest", "wiki-query"],
        "skills_version": "2026.8.3",
    }

    inventory.unlink()
    (root / ".skills/wiki-ingest/SKILL.md").write_text("changed\n", encoding="utf-8")
    before = snapshot_tree(root)
    with pytest.raises(ValueError, match="inventory|migration|upgrade"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    assert snapshot_tree(root) == before


def test_upgrade_replaces_adds_removes_and_rebuilds_managed_skills(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    v2 = tmp_path / "v2"
    for name, body in (("wiki-ingest", "# ingest v2\n"), ("wiki-new", "# new v2\n")):
        skill = v2 / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(body, encoding="utf-8")
    untouched_paths = [
        root / ".obsidian-wiki/config.toml",
        root / "wiki/index.md",
        root / "wiki/log.md",
        root / "wiki/.manifest.json",
        root / ".gitignore",
    ]
    untouched = {path: path.read_bytes() for path in untouched_paths}
    git_config = root / ".git/config"
    git_config.parent.mkdir()
    git_config.write_text("owner git data\n", encoding="utf-8")

    names = upgrade_portable_skills(root, version="2026.8.4", source_skills=v2)

    assert names == ("wiki-ingest", "wiki-new")
    assert (root / ".skills/wiki-ingest/SKILL.md").read_text() == "# ingest v2\n"
    assert (root / ".skills/wiki-new/SKILL.md").read_text() == "# new v2\n"
    assert not (root / ".skills/wiki-query").exists()
    for agent_relative, _label in cli.PROJECT_AGENT_DIRS:
        assert not (root / agent_relative / "wiki-query").exists()
        for name in names:
            adapter = root / agent_relative / name
            assert snapshot_tree(adapter) == (
                (
                    "SKILL.md",
                    "file",
                    portable._adapter_text(
                        name,
                        os.path.relpath(
                            root / ".skills" / name / "SKILL.md",
                            adapter,
                        ).replace(os.sep, "/"),
                    ).encode(),
                ),
            )
    assert json.loads((root / ".obsidian-wiki/managed-skills.json").read_text()) == {
        "implementation": IMPLEMENTATION_ID,
        "skills": ["wiki-ingest", "wiki-new"],
        "skills_version": "2026.8.4",
    }
    assert {path: path.read_bytes() for path in untouched_paths} == untouched
    assert git_config.read_text(encoding="utf-8") == "owner git data\n"


def test_upgrade_preserves_unlisted_owner_skill_directories(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    owner_paths = [root / ".skills/team-owned"] + [
        root / agent_relative / "team-owned"
        for agent_relative, _label in cli.PROJECT_AGENT_DIRS
    ]
    for path in owner_paths:
        path.mkdir(parents=True)
        (path / "OWNER.txt").write_text(f"owner:{path}\n", encoding="utf-8")
    before = {path: snapshot_tree(path) for path in owner_paths}

    upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert {path: snapshot_tree(path) for path in owner_paths} == before


@pytest.mark.parametrize("collision_location", ["canonical", "adapter"])
def test_upgrade_new_bundled_skill_owner_collision_fails_closed(
    collision_location: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    new_skill = tiny_skills / "team-owned"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text("# bundled\n", encoding="utf-8")
    collision = (
        root / ".skills/team-owned"
        if collision_location == "canonical"
        else root / ".claude/skills/team-owned"
    )
    collision.mkdir(parents=True)
    (collision / "OWNER.txt").write_text("owner\n", encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="owner|collision|unlisted"):
        upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


def test_upgrade_refreshes_only_bootstrap_managed_regions(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    agents = root / "AGENTS.md"
    agents.write_text(
        (
            "Owner preface\n\n"
            + agents.read_text().replace("transaction-only writes", "stale managed rule")
            + "\nOwner footer\n"
        )
    )
    claude = root / "CLAUDE.md"
    claude.write_text(
        "Owner before\n"
        + claude.read_text().replace(
            "Read and follow `AGENTS.md`", "Stale managed bootstrap"
        )
        + "Owner after\n"
    )
    unknown = root / ".agent/rules/team.md"
    unknown.write_text("owner file\n", encoding="utf-8")

    upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert agents.read_text().startswith("Owner preface\n")
    assert agents.read_text().endswith("Owner footer\n")
    assert claude.read_text().startswith("Owner before\n")
    assert claude.read_text().endswith("Owner after\n")
    assert unknown.read_text() == "owner file\n"
    assert "stale managed rule" not in agents.read_text()
    assert "transaction-only writes" in agents.read_text()
    assert "Stale managed bootstrap" not in claude.read_text()
    assert "Read and follow `AGENTS.md`" in claude.read_text()
    assert agents.read_text().count(MANAGED_START) == 1
    assert claude.read_text().count(MANAGED_START) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"implementation": "other/wiki", "skills_version": "1", "skills": []},
        {
            "implementation": IMPLEMENTATION_ID,
            "skills_version": "1",
            "skills": [],
            "extra": True,
        },
        {"implementation": IMPLEMENTATION_ID, "skills_version": 1, "skills": []},
        {"implementation": IMPLEMENTATION_ID, "skills_version": "1", "skills": "x"},
        {
            "implementation": IMPLEMENTATION_ID,
            "skills_version": "1",
            "skills": ["wiki-query", "wiki-ingest"],
        },
        {
            "implementation": IMPLEMENTATION_ID,
            "skills_version": "1",
            "skills": ["wiki-ingest", "wiki-ingest"],
        },
        {
            "implementation": IMPLEMENTATION_ID,
            "skills_version": "1",
            "skills": ["../wiki-ingest"],
        },
    ],
)
def test_upgrade_invalid_inventory_json_schema_fails_before_writes(
    payload: object, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    inventory.write_text(json.dumps(payload), encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="managed-skills.json"):
        upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


def test_upgrade_invalid_inventory_encoding_and_file_kinds_fail_before_writes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    inventory.write_text("{invalid", encoding="utf-8")
    before = snapshot_tree(root)
    with pytest.raises(ValueError, match="managed-skills.json"):
        upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)
    assert snapshot_tree(root) == before

    inventory.unlink()
    external = tmp_path / "external-inventory"
    external.write_text("{}\n", encoding="utf-8")
    inventory.symlink_to(external)
    before = snapshot_tree(root)
    with pytest.raises(ValueError, match="managed-skills.json|symlink"):
        upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)
    assert snapshot_tree(root) == before

    inventory.unlink()
    valid = json.dumps(
        {
            "implementation": IMPLEMENTATION_ID,
            "skills": ["wiki-ingest", "wiki-query"],
            "skills_version": "2026.8.3",
        }
    )
    external.write_text(valid, encoding="utf-8")
    os.link(external, inventory)
    before = snapshot_tree(root)
    with pytest.raises(ValueError, match="hard link|multiple links|managed-skills.json"):
        upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("inventory_kind", ["missing", "directory"])
def test_upgrade_missing_or_nonregular_inventory_fails_before_writes(
    inventory_kind: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    inventory.unlink()
    if inventory_kind == "directory":
        inventory.mkdir()
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="managed skills inventory|managed-skills.json"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


def test_repo_upgrade_cli_requires_portable_context_and_supports_nested_cwd(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    failed = run_cli(home, outside, "repo", "upgrade-skills")
    assert failed.returncode != 0
    assert "portable" in failed.stderr.lower() or "configured" in failed.stderr.lower()
    assert not (home / ".obsidian-wiki").exists()

    root = tmp_path / "repo"
    setup = run_cli(home, tmp_path, "setup", "--portable", str(root))
    assert setup.returncode == 0, setup.stderr
    nested = root / "wiki/concepts/deep"
    nested.mkdir(parents=True)
    upgraded = run_cli(home, nested, "repo", "upgrade-skills")
    assert upgraded.returncode == 0, upgraded.stderr
    assert str(root.resolve()) in upgraded.stdout
    assert "skills" in upgraded.stdout
    assert not (home / ".obsidian-wiki").exists()


def test_repo_parser_requires_nested_subcommand_and_rejects_root_argument(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    no_subcommand = run_cli(home, tmp_path, "repo")
    arbitrary_root = run_cli(home, tmp_path, "repo", "upgrade-skills", str(tmp_path))

    assert no_subcommand.returncode != 0
    assert "required" in no_subcommand.stderr.lower()
    assert arbitrary_root.returncode != 0
    assert "unrecognized arguments" in arbitrary_root.stderr.lower()


@pytest.mark.parametrize("unsafe", ["source-symlink", "managed-symlink"])
def test_upgrade_safety_preflight_leaves_everything_unchanged(
    unsafe: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    external = tmp_path / "outside"
    external.mkdir()
    (external / "sentinel").write_text("outside\n", encoding="utf-8")
    if unsafe == "source-symlink":
        (tiny_skills / "wiki-ingest/external").symlink_to(external)
    else:
        managed = root / ".claude/skills/wiki-ingest"
        shutil.rmtree(managed)
        managed.symlink_to(external, target_is_directory=True)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="symlink"):
        upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert snapshot_tree(root) == before
    assert (external / "sentinel").read_text() == "outside\n"


def test_upgrade_rolls_back_when_staged_directory_swap_fails(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text("# upgraded\n", encoding="utf-8")
    before = snapshot_tree(root)
    original_replace = Path.replace

    def fail_staged_canonical(source: Path, target: Path) -> Path:
        if (
            ".skills-upgrade-" in str(source)
            and source.parent.name == "canonical"
            and source.name == "wiki-ingest"
        ):
            raise OSError("simulated staged swap failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_staged_canonical)

    with pytest.raises(OSError, match="simulated staged swap"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


def test_upgrade_rolls_back_all_swaps_when_inventory_commit_fails(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text("# upgraded\n", encoding="utf-8")
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nOwner sentence.\n", encoding="utf-8")
    before = snapshot_tree(root)

    def fail_inventory(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated inventory commit failure")

    monkeypatch.setattr(portable, "_write_managed_skills_inventory", fail_inventory)

    with pytest.raises(OSError, match="simulated inventory commit"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


@pytest.mark.parametrize(
    ("old", "new", "error_match"),
    [
        ('implementation = "evanzlh/obsidian-wiki"', 'implementation = "other/wiki"', "implementation"),
        ('requires_cli = ">=2026.8,<2026.9"', 'requires_cli = ">=2099"', "requires CLI"),
        ('vault = "wiki"', 'vault = "notes"', "canonical portable paths"),
        ("schema_version = 1", "schema_version = [", "invalid portable configuration"),
    ],
)
def test_invalid_existing_portable_config_fails_before_any_write(
    old: str,
    new: str,
    error_match: str,
    tmp_path: Path,
    tiny_skills: Path,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    config = root / ".obsidian-wiki/config.toml"
    config.write_text(config.read_text().replace(old, new), encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match=error_match):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


@pytest.mark.parametrize("broken", ["index", "log", "manifest", "directory"])
def test_invalid_existing_portable_artifact_fails_before_any_write(
    broken: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    if broken == "index":
        (root / "wiki/index.md").write_text("wrong\n", encoding="utf-8")
    elif broken == "log":
        (root / "wiki/log.md").write_text("wrong\n", encoding="utf-8")
    elif broken == "manifest":
        (root / "wiki/.manifest.json").write_text("{}\n", encoding="utf-8")
    else:
        shutil.rmtree(root / "wiki/concepts")
        (root / "wiki/concepts").write_text("collision\n", encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="portable"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


def test_malformed_agents_markers_fail_before_any_rerun_write(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text().replace(MANAGED_END, ""), encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="managed markers"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


@pytest.mark.parametrize("preexisting_empty", [False, True])
def test_new_target_failure_is_atomic(
    preexisting_empty: bool,
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    if preexisting_empty:
        root.mkdir()

    def fail_bootstrap(_root: Path) -> None:
        raise RuntimeError("simulated late bootstrap failure")

    monkeypatch.setattr(portable, "install_portable_bootstrap", fail_bootstrap)
    with pytest.raises(RuntimeError, match="simulated late"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    if preexisting_empty:
        assert root.is_dir()
        assert list(root.iterdir()) == []
    else:
        assert not root.exists()
    assert not any(path.name.startswith(".repo.obsidian-wiki-") for path in tmp_path.iterdir())


def test_cli_reports_malformed_portable_target_without_traceback(tmp_path: Path) -> None:
    root = tmp_path / "owner-project"
    root.mkdir()
    (root / "README.md").write_text("owner\n", encoding="utf-8")
    before = snapshot_tree(root)

    result = run_cli(tmp_path / "home", tmp_path, "setup", "--portable", str(root))

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "not a portable" in result.stderr
    assert "Traceback" not in result.stderr
    assert snapshot_tree(root) == before


def test_compatible_cli_spec_with_epoch_uses_exact_public_version() -> None:
    assert compatible_cli_spec("1!2026.8") == "==1!2026.8"


def test_gitignore_uses_lf_and_escapes_literal_vault_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(os, "linesep", "\r\n")

    ensure_portable_gitignore(root, "#team notes/[draft]*?!")

    data = (root / ".gitignore").read_bytes()
    assert b"\r" not in data
    assert b"\\#team\\ notes/\\[draft\\]\\*\\?\\!/hot.md\n" in data


def test_rerun_replaces_hard_linked_agents_without_mutating_external_inode(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    agents = root / "AGENTS.md"
    agents.unlink()
    external = tmp_path / "external-agents.md"
    external.write_text("External owner instructions.\n", encoding="utf-8")
    external_bytes = external.read_bytes()
    os.link(external, agents)
    assert agents.stat().st_ino == external.stat().st_ino

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert external.read_bytes() == external_bytes
    assert agents.stat().st_ino != external.stat().st_ino
    assert MANAGED_START in agents.read_text(encoding="utf-8")
    assert "External owner instructions." in agents.read_text(encoding="utf-8")


def test_rerun_replaces_hard_linked_gitignore_without_mutating_external_inode(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    gitignore = root / ".gitignore"
    gitignore.unlink()
    external = tmp_path / "external-ignore"
    external.write_text("owner-cache/\n", encoding="utf-8")
    external_bytes = external.read_bytes()
    os.link(external, gitignore)
    assert gitignore.stat().st_ino == external.stat().st_ino

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert external.read_bytes() == external_bytes
    assert gitignore.stat().st_ino != external.stat().st_ino
    lines = gitignore.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "owner-cache/"
    assert "wiki/hot.md" in lines


@pytest.mark.parametrize("hard_link_location", ["nested", "skill-file"])
def test_source_skill_hard_links_are_rejected_before_target_creation(
    hard_link_location: str, tmp_path: Path
) -> None:
    source = make_skill_source(tmp_path)
    external = tmp_path / "external-source"
    external.write_text("external source bytes\n", encoding="utf-8")
    external_bytes = external.read_bytes()
    if hard_link_location == "nested":
        os.link(external, source / "wiki-ingest/external.txt")
    else:
        skill_file = source / "wiki-ingest/SKILL.md"
        skill_file.unlink()
        os.link(external, skill_file)
    target = tmp_path / "repo"

    with pytest.raises(ValueError, match="hard link|multiple links"):
        setup_portable_repo(target, version="2026.8.3", source_skills=source)

    assert external.read_bytes() == external_bytes
    assert not target.exists()
    assert not any(path.name.startswith(".repo.obsidian-wiki-") for path in tmp_path.iterdir())


def test_source_skill_fifo_is_rejected_before_target_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_skill_source(tmp_path)
    os.mkfifo(source / "wiki-ingest/input.pipe")
    target = tmp_path / "repo"

    def fail_if_copy_reached(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("copy reached before source validation")

    monkeypatch.setattr(portable.shutil, "copytree", fail_if_copy_reached)

    with pytest.raises(ValueError, match="ordinary|regular"):
        setup_portable_repo(target, version="2026.8.3", source_skills=source)

    assert not target.exists()
    assert not any(path.name.startswith(".repo.obsidian-wiki-") for path in tmp_path.iterdir())

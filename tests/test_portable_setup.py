from __future__ import annotations

import json
import os
import shutil
import stat
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


def write_prepared_skill_upgrade_journal(
    root: Path, source: Path, *, version: str
) -> tuple[Path, dict[str, object]]:
    """Create the canonical pre-swap journal state used by recovery tests."""
    transaction = root / ".obsidian-wiki/local/skill-upgrades/txn-prepared-test"
    old_names = tuple(
        json.loads(
            (root / ".obsidian-wiki/managed-skills.json").read_text(encoding="utf-8")
        )["skills"]
    )
    current_names = tuple(
        sorted(
            path.name
            for path in source.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        )
    )
    staged_root = transaction / "staged"
    records: list[dict[str, object]] = []
    for name in sorted(set(old_names) | set(current_names)):
        staged_canonical = staged_root / "canonical" / name
        if name in current_names:
            shutil.copytree(
                source / name,
                staged_canonical,
                ignore=portable._ignore_source_artifacts,
            )
            canonical_target = root / ".skills" / name
            os.chmod(
                staged_canonical,
                (
                    stat.S_IMODE(canonical_target.stat().st_mode)
                    if canonical_target.exists()
                    else 0o755
                ),
            )
        records.append(
            {
                "backup": "",
                "had_target": name in old_names,
                "staged": (
                    staged_canonical.relative_to(root).as_posix()
                    if name in current_names
                    else None
                ),
                "target": f".skills/{name}",
            }
        )
        for agent_index, (agent_relative, _label) in enumerate(
            cli.PROJECT_AGENT_DIRS
        ):
            staged_adapter = staged_root / "adapters" / str(agent_index) / name
            if name in current_names:
                skill_file = staged_adapter / "SKILL.md"
                skill_file.parent.mkdir(parents=True)
                target_relative = f"{agent_relative}/{name}/SKILL.md"
                canonical_relative = f".skills/{name}/SKILL.md"
                skill_file.write_text(
                    portable._adapter_text(
                        name,
                        os.path.relpath(
                            canonical_relative,
                            str(Path(target_relative).parent),
                        ).replace(os.sep, "/"),
                    ),
                    encoding="utf-8",
                )
                target_skill = root / target_relative
                os.chmod(
                    skill_file,
                    stat.S_IMODE(target_skill.stat().st_mode)
                    if target_skill.exists()
                    else 0o644,
                )
                adapter_target = root / agent_relative / name
                os.chmod(
                    staged_adapter,
                    (
                        stat.S_IMODE(adapter_target.stat().st_mode)
                        if adapter_target.exists()
                        else 0o755
                    ),
                )
            records.append(
                {
                    "backup": "",
                    "had_target": name in old_names,
                    "staged": (
                        staged_adapter.relative_to(root).as_posix()
                        if name in current_names
                        else None
                    ),
                    "target": f"{agent_relative}/{name}",
                }
            )

    for bootstrap_index, (target, text) in enumerate(
        portable._portable_bootstrap_plans(root)
    ):
        staged_bootstrap = staged_root / "bootstrap" / str(bootstrap_index)
        portable._stage_text_for_replacement(
            root, transaction, staged_bootstrap, target, text
        )
        records.append(
            {
                "backup": "",
                "had_target": target.exists(),
                "install": "",
                "staged": staged_bootstrap.relative_to(root).as_posix(),
                "target": target.relative_to(root).as_posix(),
            }
        )

    staged_inventory = staged_root / "inventory/managed-skills.json"
    staged_inventory.parent.mkdir(parents=True)
    staged_inventory.write_text(
        json.dumps(
            {
                "implementation": IMPLEMENTATION_ID,
                "skills": list(current_names),
                "skills_version": version,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(
        staged_inventory,
        stat.S_IMODE(
            (root / ".obsidian-wiki/managed-skills.json").stat().st_mode
        ),
    )
    records.append(
        {
            "backup": "",
            "had_target": True,
            "install": "",
            "staged": staged_inventory.relative_to(root).as_posix(),
            "target": ".obsidian-wiki/managed-skills.json",
        }
    )
    payload: dict[str, object] = {
        "created_parents": [],
        "implementation": IMPLEMENTATION_ID,
        "replacements": records,
        "schema_version": 3,
        "status": "prepared",
    }
    rewrite_prepared_skill_upgrade_journal(root, transaction, payload)
    return transaction, payload


def rewrite_prepared_skill_upgrade_journal(
    root: Path, transaction: Path, payload: dict[str, object]
) -> None:
    records = payload["replacements"]
    assert isinstance(records, list)
    transaction_relative = transaction.relative_to(root).as_posix()
    transaction.mkdir(parents=True, exist_ok=True)
    install_root = transaction / "install"
    if install_root.exists():
        shutil.rmtree(install_root)
    install_root.mkdir()
    for index, record in enumerate(records):
        assert isinstance(record, dict)
        record["backup"] = f"{transaction_relative}/backups/{index}"
        staged_raw = record.get("staged")
        if staged_raw is None:
            record["install"] = None
            continue
        assert isinstance(staged_raw, str)
        staged = root / staged_raw
        install = install_root / str(index)
        if staged.is_dir():
            shutil.copytree(staged, install, copy_function=shutil.copy2)
        else:
            shutil.copy2(staged, install)
        record["install"] = install.relative_to(root).as_posix()
    (transaction / "journal.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


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
    (source / ".git").mkdir()
    (source / ".git/SKILL.md").write_text("# fake cache skill\n", encoding="utf-8")
    root = tmp_path / "repo"

    setup_portable_repo(root, version="2026.8.3", source_skills=source)

    assert {entry.name for entry in (root / ".skills").iterdir()} == {"wiki-ingest"}
    assert not (root / ".claude/skills/top-level.txt").exists()


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


@pytest.mark.parametrize("inventory_missing", [False, True])
def test_existing_setup_uses_portable_upgrade_lock_before_inventory_work(
    inventory_missing: bool, tmp_path: Path, tiny_skills: Path
) -> None:
    fcntl = pytest.importorskip("fcntl")
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    if inventory_missing:
        (root / ".obsidian-wiki/managed-skills.json").unlink()
    lock = root / ".obsidian-wiki/local/portable-skills.lock"
    descriptor = os.open(lock, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = snapshot_tree(root)
    try:
        with pytest.raises(ValueError, match="locked|another.*upgrade"):
            setup_portable_repo(
                root, version="2026.8.3", source_skills=tiny_skills
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert snapshot_tree(root) == before


def test_existing_setup_recovers_pending_upgrade_before_inventory_read(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction, _payload = write_prepared_skill_upgrade_journal(
        root, tiny_skills, version="2026.8.4"
    )
    original_reader = portable._read_managed_skills_inventory

    def assert_recovered_before_inventory(
        repository: Path,
    ) -> tuple[str, tuple[str, ...]]:
        assert not transaction.exists()
        return original_reader(repository)

    monkeypatch.setattr(
        portable, "_read_managed_skills_inventory", assert_recovered_before_inventory
    )

    result = setup_portable_repo(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert result == root
    assert not transaction.exists()


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


def test_upgrade_preserves_existing_skill_root_modes_and_defaults_new_roots_to_0755(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    existing = [root / ".skills/wiki-ingest"] + [
        root / agent_relative / "wiki-ingest"
        for agent_relative, _label in cli.PROJECT_AGENT_DIRS
    ]
    for path in existing:
        os.chmod(path, 0o700)
    new_skill = tiny_skills / "wiki-new"
    new_skill.mkdir(mode=0o700)
    os.chmod(new_skill, 0o700)
    (new_skill / "SKILL.md").write_text("# wiki-new\n", encoding="utf-8")

    upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in existing)
    new_roots = [root / ".skills/wiki-new"] + [
        root / agent_relative / "wiki-new"
        for agent_relative, _label in cli.PROJECT_AGENT_DIRS
    ]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o755 for path in new_roots)


def test_upgrade_never_copies_transaction_artifacts_directly_to_final_targets(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    new_skill = tiny_skills / "wiki-new"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text("# wiki-new\n", encoding="utf-8")
    original_copy2 = shutil.copy2
    original_copytree = shutil.copytree

    def is_transaction_artifact(path: str | os.PathLike[str]) -> bool:
        candidate = Path(path)
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return False
        return (
            len(relative.parts) >= 4
            and relative.parts[:3]
            == (".obsidian-wiki", "local", "skill-upgrades")
            and relative.parts[3].startswith("txn-")
        )

    def is_final_target(path: str | os.PathLike[str]) -> bool:
        candidate = Path(path)
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        return not is_transaction_artifact(candidate)

    def reject_direct_file_copy(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
        *,
        follow_symlinks: bool = True,
    ) -> str:
        if is_transaction_artifact(source) and is_final_target(target):
            raise AssertionError("transaction file copied directly to final target")
        return original_copy2(source, target, follow_symlinks=follow_symlinks)

    def reject_direct_tree_copy(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
        *args: object,
        **kwargs: object,
    ) -> str:
        if is_transaction_artifact(source) and is_final_target(target):
            raise AssertionError("transaction tree copied directly to final target")
        return original_copytree(source, target, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", reject_direct_file_copy)
    monkeypatch.setattr(shutil, "copytree", reject_direct_tree_copy)

    upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert (root / ".skills/wiki-new/SKILL.md").read_text(encoding="utf-8") == (
        "# wiki-new\n"
    )


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
            source.parent.name == "install"
            and Path(target) == root / ".skills/wiki-ingest"
        ):
            raise OSError("simulated staged swap failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_staged_canonical)

    with pytest.raises(OSError, match="simulated staged swap"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


@pytest.mark.parametrize("preexisting_empty_parent", [False, True])
def test_upgrade_rolls_back_all_swaps_when_inventory_commit_fails(
    preexisting_empty_parent: bool,
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text("# upgraded\n", encoding="utf-8")
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nOwner sentence.\n", encoding="utf-8")
    shutil.rmtree(root / ".github")
    if preexisting_empty_parent:
        (root / ".github").mkdir()
    before = snapshot_tree(root)

    original_replace = Path.replace

    def fail_inventory(source: Path, target: Path) -> Path:
        if (
            source.parent.name == "install"
            and Path(target) == root / ".obsidian-wiki/managed-skills.json"
        ):
            raise OSError("simulated inventory commit failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_inventory)

    with pytest.raises(OSError, match="simulated inventory commit"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


def test_upgrade_rollback_preserves_concurrently_populated_created_parent(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    shutil.rmtree(root / ".github")
    original_replace = Path.replace

    def populate_parent_then_fail(source: Path, target: Path) -> Path:
        if (
            source.parent.name == "install"
            and Path(target) == root / ".obsidian-wiki/managed-skills.json"
        ):
            (root / ".github/OWNER.txt").write_text(
                "concurrent owner data\n", encoding="utf-8"
            )
            raise OSError("simulated inventory commit failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", populate_parent_then_fail)

    with pytest.raises(OSError, match="rollback|parent|incomplete"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert (root / ".github/OWNER.txt").read_text(encoding="utf-8") == (
        "concurrent owner data\n"
    )
    assert list(
        (root / ".obsidian-wiki/local/skill-upgrades").glob("*/journal.json")
    )


def test_failed_forward_and_rollback_preserve_journal_for_next_recovery(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text("# ingest v2\n", encoding="utf-8")
    (tiny_skills / "wiki-query/SKILL.md").write_text("# query v2\n", encoding="utf-8")
    old_inventory = (root / ".obsidian-wiki/managed-skills.json").read_bytes()
    original_replace = Path.replace
    failures = {"forward": False, "restore": False, "restore_attempts": 0}

    def fail_forward_and_one_restore(source: Path, target: Path) -> Path:
        source_text = str(source)
        if (
            not failures["forward"]
            and source.parent.name == "install"
            and Path(target) == root / ".skills/wiki-query"
        ):
            failures["forward"] = True
            raise OSError("simulated forward swap failure")
        if (
            failures["forward"] and "/backups/" in source_text
        ):
            failures["restore_attempts"] += 1
            if (
                not failures["restore"]
                and Path(target) == root / ".skills/wiki-ingest"
            ):
                failures["restore"] = True
                raise OSError("simulated rollback restore failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_forward_and_one_restore)

    with pytest.raises(OSError, match="rollback|restore"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    journals = list(
        (root / ".obsidian-wiki/local/skill-upgrades").glob("*/journal.json")
    )
    assert len(journals) == 1
    assert list(journals[0].parent.glob("backups/*"))
    assert failures["restore_attempts"] > 1
    assert (root / ".obsidian-wiki/managed-skills.json").read_bytes() == old_inventory

    monkeypatch.setattr(Path, "replace", original_replace)
    original_reader = portable._read_managed_skills_inventory

    def assert_recovered_before_inventory(
        repository: Path,
    ) -> tuple[str, tuple[str, ...]]:
        assert not list(
            (repository / ".obsidian-wiki/local/skill-upgrades").glob(
                "*/journal.json"
            )
        )
        return original_reader(repository)

    monkeypatch.setattr(
        portable, "_read_managed_skills_inventory", assert_recovered_before_inventory
    )
    names = upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert names == ("wiki-ingest", "wiki-query")
    assert (root / ".skills/wiki-ingest/SKILL.md").read_text() == "# ingest v2\n"
    assert (root / ".skills/wiki-query/SKILL.md").read_text() == "# query v2\n"
    assert not list(
        (root / ".obsidian-wiki/local/skill-upgrades").glob("*/journal.json")
    )
    assert json.loads((root / ".obsidian-wiki/managed-skills.json").read_text())[
        "skills_version"
    ] == "2026.8.4"


def test_upgrade_fails_fast_while_repository_lock_is_held_without_writes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    fcntl = pytest.importorskip("fcntl")
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    lock = root / ".obsidian-wiki/local/portable-skills.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = snapshot_tree(root)
    try:
        with pytest.raises(ValueError, match="locked|another.*upgrade"):
            upgrade_portable_skills(
                root, version="2026.8.4", source_skills=tiny_skills
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert snapshot_tree(root) == before
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


@pytest.mark.parametrize("bundle_kind", ["empty", "missing-skill-file"])
def test_upgrade_rejects_empty_or_malformed_bundle_without_changes(
    bundle_kind: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    source = tmp_path / "bad-bundle"
    source.mkdir()
    if bundle_kind == "missing-skill-file":
        (source / "wiki-malformed").mkdir()
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="empty|SKILL.md|malformed"):
        upgrade_portable_skills(root, version="2026.8.4", source_skills=source)

    assert snapshot_tree(root) == before


def test_upgrade_preserves_existing_bootstrap_file_mode(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    agents = root / "AGENTS.md"
    inventory = root / ".obsidian-wiki/managed-skills.json"
    adapter = root / ".claude/skills/wiki-ingest/SKILL.md"
    os.chmod(agents, 0o600)
    os.chmod(inventory, 0o640)
    os.chmod(adapter, 0o600)
    agents.write_text(
        agents.read_text().replace("transaction-only writes", "stale managed rule"),
        encoding="utf-8",
    )

    upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert stat.S_IMODE(agents.stat().st_mode) == 0o600
    assert stat.S_IMODE(inventory.stat().st_mode) == 0o640
    assert stat.S_IMODE(adapter.stat().st_mode) == 0o600
    assert "transaction-only writes" in agents.read_text(encoding="utf-8")


def test_pre_inventory_migration_late_failure_has_no_partial_writes(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (root / ".obsidian-wiki/managed-skills.json").unlink()
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text().replace("transaction-only writes", "stale managed rule"),
        encoding="utf-8",
    )
    gitignore = root / ".gitignore"
    gitignore.write_text(
        gitignore.read_text().replace("wiki/hot.md\n", ""), encoding="utf-8"
    )
    before = snapshot_tree(root)

    def fail_inventory(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated migration inventory failure")

    monkeypatch.setattr(portable, "_write_managed_skills_inventory", fail_inventory)

    with pytest.raises(OSError, match="migration inventory"):
        setup_portable_repo(root, version="2026.8.4", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


def test_upgrade_ignores_unrelated_owner_symlinks_and_preserves_them(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    outside = tmp_path / "owner-outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("owner data\n", encoding="utf-8")
    owner_links = [
        root / "wiki/concepts/owner-link",
        root / "sources/owner-link",
        root / ".skills/team-owned/owner-link",
        root / ".claude/skills/team-owned/owner-link",
        root / ".cursor/rules/team-owned-link",
    ]
    for link in owner_links:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside, target_is_directory=True)

    upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert all(link.is_symlink() and link.resolve() == outside for link in owner_links)
    assert sentinel.read_text(encoding="utf-8") == "owner data\n"


@pytest.mark.parametrize("unsafe_field", ["target", "backup", "staged", "install"])
def test_upgrade_recovery_rejects_unsafe_journal_paths_without_external_writes(
    unsafe_field: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction = root / ".obsidian-wiki/local/skill-upgrades/txn-malicious"
    transaction.mkdir(parents=True)
    transaction_relative = transaction.relative_to(root).as_posix()
    record: dict[str, object] = {
        "backup": f"{transaction_relative}/backups/0",
        "had_target": True,
        "install": f"{transaction_relative}/install/0",
        "staged": f"{transaction_relative}/staged/inventory/managed-skills.json",
        "target": ".obsidian-wiki/managed-skills.json",
    }
    record[unsafe_field] = "../outside"
    journal = transaction / "journal.json"
    journal.write_text(
        json.dumps(
            {
                "created_parents": [],
                "implementation": IMPLEMENTATION_ID,
                "replacements": [record],
                "schema_version": 3,
                "status": "prepared",
            }
        ),
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.write_text("owner data\n", encoding="utf-8")

    with pytest.raises(ValueError, match="journal|unsafe|repository-relative"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert outside.read_text(encoding="utf-8") == "owner data\n"
    assert journal.is_file()


@pytest.mark.parametrize("remnant_kind", ["empty", "partial-staging", "internal-link"])
def test_upgrade_recovery_removes_safe_journalless_transaction_remnants(
    remnant_kind: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction = root / ".obsidian-wiki/local/skill-upgrades/txn-remnant"
    transaction.mkdir(parents=True)
    outside = tmp_path / "owner-outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("owner data\n", encoding="utf-8")
    if remnant_kind == "partial-staging":
        staged = transaction / "staged/canonical/wiki-ingest"
        staged.mkdir(parents=True)
        (staged / "SKILL.md").write_text("partial\n", encoding="utf-8")
    elif remnant_kind == "internal-link":
        (transaction / "staged").mkdir()
        (transaction / "staged/owner-link").symlink_to(
            outside, target_is_directory=True
        )

    names = upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert names == ("wiki-ingest", "wiki-query")
    assert not transaction.exists()
    assert sentinel.read_text(encoding="utf-8") == "owner data\n"


@pytest.mark.parametrize(
    "created_parents",
    [
        ["."],
        ["../outside"],
        ["wiki"],
        [".claude", ".claude"],
        [".claude/skills", ".claude"],
    ],
)
def test_upgrade_recovery_rejects_malformed_created_parent_journal_without_writes(
    created_parents: list[str], tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction, payload = write_prepared_skill_upgrade_journal(
        root, tiny_skills, version="2026.8.4"
    )
    payload["created_parents"] = created_parents
    rewrite_prepared_skill_upgrade_journal(root, transaction, payload)
    before = snapshot_tree(root)

    with pytest.raises(
        ValueError,
        match="created parent|journal|repository-relative|ancestor|canonically",
    ):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


@pytest.mark.parametrize("target_kind", ["canonical", "adapter"])
@pytest.mark.parametrize("had_target", [True, False])
def test_upgrade_recovery_cannot_claim_unlisted_owner_skill(
    target_kind: str, had_target: bool, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    target_relative = (
        ".skills/team-owned"
        if target_kind == "canonical"
        else ".claude/skills/team-owned"
    )
    owner = root / target_relative
    owner.mkdir(parents=True)
    (owner / "OWNER.txt").write_text("owner bytes\n", encoding="utf-8")

    transaction, payload = write_prepared_skill_upgrade_journal(
        root, tiny_skills, version="2026.8.4"
    )
    staged_owner = (
        transaction / "staged/canonical/team-owned"
        if target_kind == "canonical"
        else transaction / "staged/adapters/0/team-owned"
    )
    staged_owner.mkdir(parents=True)
    (staged_owner / "OWNER.txt").write_text("owner bytes\n", encoding="utf-8")
    records = payload["replacements"]
    assert isinstance(records, list)
    inventory_record = records.pop()
    owner_record: dict[str, object] = {
        "backup": "",
        "had_target": had_target,
        "staged": (
            None if had_target else staged_owner.relative_to(root).as_posix()
        ),
        "target": target_relative,
    }
    records.extend((owner_record, inventory_record))
    rewrite_prepared_skill_upgrade_journal(root, transaction, payload)
    attacker_backup = root / str(owner_record["backup"])
    if had_target:
        attacker_backup.mkdir(parents=True)
        (attacker_backup / "ATTACKER.txt").write_text(
            "attacker bytes\n", encoding="utf-8"
        )
    before = snapshot_tree(root)

    with pytest.raises(
        (ValueError, OSError),
        match="inventory|owner|unlisted|managed|recovery|absent",
    ):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before
    assert (owner / "OWNER.txt").read_text(encoding="utf-8") == "owner bytes\n"
    assert not (owner / "ATTACKER.txt").exists()


@pytest.mark.parametrize(
    "corruption", ["missing-adapter", "extra-canonical", "wrong-staged-layout"]
)
def test_upgrade_recovery_rejects_noncanonical_skill_record_plan_without_writes(
    corruption: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    new_skill = tiny_skills / "wiki-new"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text("# wiki-new\n", encoding="utf-8")
    transaction, payload = write_prepared_skill_upgrade_journal(
        root, tiny_skills, version="2026.8.4"
    )
    records = payload["replacements"]
    assert isinstance(records, list)
    inventory_record = records.pop()

    if corruption == "missing-adapter":
        records[:] = [
            record
            for record in records
            if not (
                isinstance(record, dict)
                and record["target"] == ".claude/skills/wiki-new"
            )
        ]
    elif corruption == "extra-canonical":
        staged_extra = transaction / "staged/canonical/team-owned"
        staged_extra.mkdir(parents=True)
        (staged_extra / "SKILL.md").write_text("# team-owned\n", encoding="utf-8")
        records.append(
            {
                "backup": "",
                "had_target": False,
                "staged": staged_extra.relative_to(root).as_posix(),
                "target": ".skills/team-owned",
            }
        )
    else:
        record = next(
            record
            for record in records
            if isinstance(record, dict) and record["target"] == ".skills/wiki-new"
        )
        original_staged = root / str(record["staged"])
        forged_staged = transaction / "staged/forged/wiki-new"
        forged_staged.parent.mkdir(parents=True)
        original_staged.replace(forged_staged)
        record["staged"] = forged_staged.relative_to(root).as_posix()

    records.append(inventory_record)
    rewrite_prepared_skill_upgrade_journal(root, transaction, payload)
    before = snapshot_tree(root)

    with pytest.raises(
        (ValueError, OSError),
        match="plan|record|missing|unexpected|staged|recovery",
    ):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


@pytest.mark.parametrize("target_kind", ["canonical", "adapter"])
@pytest.mark.parametrize("tampering", ["content", "mode"])
def test_upgrade_recovery_rejects_new_target_matching_staged_but_not_source(
    target_kind: str,
    tampering: str,
    tmp_path: Path,
    tiny_skills: Path,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    new_skill = tiny_skills / "wiki-new"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text("# wiki-new\n", encoding="utf-8")
    transaction, _payload = write_prepared_skill_upgrade_journal(
        root, tiny_skills, version="2026.8.4"
    )
    if target_kind == "canonical":
        staged = transaction / "staged/canonical/wiki-new"
        target = root / ".skills/wiki-new"
    else:
        staged = transaction / "staged/adapters/0/wiki-new"
        target = root / ".claude/skills/wiki-new"
    shutil.copytree(staged, target, copy_function=shutil.copy2)
    staged_skill = staged / "SKILL.md"
    target_skill = target / "SKILL.md"
    if tampering == "content":
        staged_skill.write_text("# attacker-controlled\n", encoding="utf-8")
        target_skill.write_text("# attacker-controlled\n", encoding="utf-8")
    else:
        os.chmod(staged_skill, 0o600)
        os.chmod(target_skill, 0o600)
    before = snapshot_tree(root)
    before_modes = (
        stat.S_IMODE(staged_skill.stat().st_mode),
        stat.S_IMODE(target_skill.stat().st_mode),
    )

    with pytest.raises(
        (ValueError, OSError), match="source|trusted|content|mode|recovery"
    ):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before
    assert (
        stat.S_IMODE(staged_skill.stat().st_mode),
        stat.S_IMODE(target_skill.stat().st_mode),
    ) == before_modes


def test_recovery_removes_proven_new_skill_targets_created_before_inventory_commit(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    new_skill = tiny_skills / "wiki-new"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text("# wiki-new\n", encoding="utf-8")
    original_apply = portable._apply_journaled_upgrade

    def crash_before_inventory(
        repository: Path,
        _transaction: Path,
        _payload: dict[str, object],
        records: list[tuple[Path, Path, Path | None, bool]],
    ) -> None:
        for index, (target, backup, staged, had_target) in enumerate(records):
            if target == repository / ".obsidian-wiki/managed-skills.json":
                raise OSError("simulated process crash before inventory commit")
            if had_target:
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup)
            if staged is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                (_transaction / "install" / str(index)).replace(target)

    monkeypatch.setattr(portable, "_apply_journaled_upgrade", crash_before_inventory)
    with pytest.raises(OSError, match="before inventory commit"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert (root / ".skills/wiki-new/SKILL.md").is_file()
    for agent_relative, _label in cli.PROJECT_AGENT_DIRS:
        assert (root / agent_relative / "wiki-new/SKILL.md").is_file()
    assert list(
        (root / ".obsidian-wiki/local/skill-upgrades").glob("*/journal.json")
    )

    monkeypatch.setattr(portable, "_apply_journaled_upgrade", original_apply)
    names = upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert names == ("wiki-ingest", "wiki-new", "wiki-query")
    assert (root / ".skills/wiki-new/SKILL.md").read_text() == "# wiki-new\n"
    for agent_relative, _label in cli.PROJECT_AGENT_DIRS:
        assert (root / agent_relative / "wiki-new/SKILL.md").is_file()
    assert not list(
        (root / ".obsidian-wiki/local/skill-upgrades").glob("*/journal.json")
    )
    assert json.loads((root / ".obsidian-wiki/managed-skills.json").read_text())[
        "skills"
    ] == ["wiki-ingest", "wiki-new", "wiki-query"]


def test_next_invocation_recovery_removes_created_bootstrap_parents_before_upgrade(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    shutil.rmtree(root / ".github")
    original_apply = portable._apply_journaled_upgrade

    def crash_before_inventory(
        repository: Path,
        _transaction: Path,
        _payload: dict[str, object],
        records: list[tuple[Path, Path, Path | None, bool]],
    ) -> None:
        for index, (target, backup, staged, had_target) in enumerate(records):
            if target == repository / ".obsidian-wiki/managed-skills.json":
                raise OSError("simulated process crash before inventory commit")
            if had_target:
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup)
            if staged is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                (_transaction / "install" / str(index)).replace(target)

    monkeypatch.setattr(portable, "_apply_journaled_upgrade", crash_before_inventory)
    with pytest.raises(OSError, match="before inventory commit"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert (root / ".github/copilot-instructions.md").is_file()
    monkeypatch.setattr(portable, "_apply_journaled_upgrade", original_apply)
    original_preflight = portable._preflight_upgrade_paths

    def assert_parent_recovered_before_upgrade(
        repository: Path,
        *,
        previous_names: tuple[str, ...],
        current_names: tuple[str, ...],
    ) -> list[tuple[Path, str]]:
        assert not (repository / ".github").exists()
        return original_preflight(
            repository,
            previous_names=previous_names,
            current_names=current_names,
        )

    monkeypatch.setattr(
        portable, "_preflight_upgrade_paths", assert_parent_recovered_before_upgrade
    )
    names = upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert names == ("wiki-ingest", "wiki-query")
    assert (root / ".github/copilot-instructions.md").is_file()


@pytest.mark.parametrize("tampering", ["owner-target", "arbitrary-backup"])
def test_bootstrap_recovery_rejects_untrusted_or_owner_diverged_state(
    tampering: str,
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    original_apply = portable._apply_journaled_upgrade

    def crash_before_inventory(
        repository: Path,
        _transaction: Path,
        _payload: dict[str, object],
        records: list[tuple[Path, Path, Path | None, bool]],
    ) -> None:
        for index, (target, backup, staged, had_target) in enumerate(records):
            if target == repository / ".obsidian-wiki/managed-skills.json":
                raise OSError("simulated process crash before inventory commit")
            if had_target:
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup)
            if staged is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                (_transaction / "install" / str(index)).replace(target)

    monkeypatch.setattr(portable, "_apply_journaled_upgrade", crash_before_inventory)
    with pytest.raises(OSError, match="before inventory commit"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )
    monkeypatch.setattr(portable, "_apply_journaled_upgrade", original_apply)
    journal = next(
        (root / ".obsidian-wiki/local/skill-upgrades").glob("*/journal.json")
    )
    payload = json.loads(journal.read_text(encoding="utf-8"))

    if tampering == "owner-target":
        target = root / "CLAUDE.md"
        target.write_text(
            target.read_text(encoding="utf-8") + "\nOwner post-crash edit.\n",
            encoding="utf-8",
        )
    else:
        agents_record = next(
            record
            for record in payload["replacements"]
            if record["target"] == "AGENTS.md"
        )
        target = root / str(agents_record["backup"])
        target.write_text("# arbitrary attacker backup\n", encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(
        (ValueError, OSError), match="bootstrap|owner|diverged|rollback|trusted"
    ):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    if tampering == "owner-target":
        assert "Owner post-crash edit." in target.read_text(encoding="utf-8")
        assert any(journal.parent.glob("backups/*"))
    else:
        assert snapshot_tree(root) == before
    assert journal.is_file()


def test_upgrade_recovery_requires_complete_fixed_bootstrap_record_set(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction, payload = write_prepared_skill_upgrade_journal(
        root, tiny_skills, version="2026.8.4"
    )
    records = payload["replacements"]
    assert isinstance(records, list)
    records[:] = [
        record
        for record in records
        if not (
            isinstance(record, dict)
            and record["target"] == ".github/copilot-instructions.md"
        )
    ]
    rewrite_prepared_skill_upgrade_journal(root, transaction, payload)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="bootstrap|record|missing|plan"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before
    assert (transaction / "journal.json").is_file()


def test_upgrade_recovery_rejects_install_candidate_diverging_from_proof(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction, payload = write_prepared_skill_upgrade_journal(
        root, tiny_skills, version="2026.8.4"
    )
    records = payload["replacements"]
    assert isinstance(records, list)
    canonical_record = next(
        record
        for record in records
        if isinstance(record, dict) and record["target"] == ".skills/wiki-ingest"
    )
    install = root / str(canonical_record["install"])
    (install / "SKILL.md").write_text("# candidate tampering\n", encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="install candidate|proof|trusted"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before
    assert (transaction / "journal.json").is_file()


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


@pytest.mark.parametrize("operation", ["setup", "upgrade"])
@pytest.mark.parametrize(
    "managed_location",
    [
        ".skills/wiki-ingest/nested/owner.md",
        ".skills/wiki-ingest/SKILL.md",
        ".claude/skills/wiki-ingest/SKILL.md",
        "AGENTS.md",
        "CLAUDE.md",
        ".obsidian-wiki/config.toml",
        ".obsidian-wiki/managed-skills.json",
    ],
)
def test_existing_portable_rejects_hardlinked_managed_targets_before_mutation(
    operation: str,
    managed_location: str,
    tmp_path: Path,
    tiny_skills: Path,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    target = root / managed_location
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_bytes() if target.exists() else b"owner nested bytes\n"
    if target.exists():
        target.unlink()
    external = tmp_path / "external-managed-file"
    external.write_bytes(original)
    external_bytes = external.read_bytes()
    os.link(external, target)
    assert target.stat().st_ino == external.stat().st_ino
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="hard link|multiple links"):
        if operation == "setup":
            setup_portable_repo(
                root, version="2026.8.4", source_skills=tiny_skills
            )
        else:
            upgrade_portable_skills(
                root, version="2026.8.4", source_skills=tiny_skills
            )

    assert external.read_bytes() == external_bytes
    assert target.stat().st_ino == external.stat().st_ino
    assert snapshot_tree(root) == before
    assert not (root / ".obsidian-wiki/local/skill-upgrades").exists()


@pytest.mark.parametrize("operation", ["setup", "upgrade"])
@pytest.mark.parametrize(
    "owner_location",
    [
        ".skills/team-owned/OWNER.md",
        ".claude/skills/team-owned/OWNER.md",
        "wiki/concepts/owner.md",
        "sources/owner.md",
    ],
)
def test_existing_portable_allows_hardlinks_in_unlisted_owner_locations(
    operation: str,
    owner_location: str,
    tmp_path: Path,
    tiny_skills: Path,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    target = root / owner_location
    target.parent.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external-owner-file"
    external.write_text("owner hardlinked bytes\n", encoding="utf-8")
    os.link(external, target)
    inode = external.stat().st_ino

    if operation == "setup":
        setup_portable_repo(root, version="2026.8.4", source_skills=tiny_skills)
    else:
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert target.stat().st_ino == inode
    assert external.stat().st_ino == inode
    assert target.read_text(encoding="utf-8") == "owner hardlinked bytes\n"


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

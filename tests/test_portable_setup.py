from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import (
    IMPLEMENTATION_ID,
    SOURCE_REINSTALL_COMMAND,
    cli,
    portable,
    skill_trees,
)
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable import (
    MANAGED_END,
    MANAGED_START,
    MANIFEST_MARKER,
    PORTABLE_ROOT_IGNORE,
    PORTABLE_VAULT_DIRS,
    compatible_cli_spec,
    ensure_portable_gitattributes,
    ensure_portable_gitignore,
    merge_managed_block,
    plan_portable_skill_sync,
    render_portable_gitattributes,
    setup_portable_repo,
)
from obsidian_wiki.portable import (
    upgrade_portable_skills as _upgrade_portable_skills,
)
from obsidian_wiki.skill_inventory import (
    LegacyManagedSkillsInventory,
    ManagedSkillsInventory,
    read_inventory,
)
from obsidian_wiki.skill_trees import discover_skill_collection

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

SYNTHETIC_LEGACY_BASELINES: dict[Path, dict[str, str]] = {}


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


def skill_markdown(name: str, description: str | None = None) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description or 'Use the ' + name + ' workflow.'}\n"
        "---\n\n"
        f"# {name}\n\n"
        "Follow the complete bundled workflow.\n"
    )


@pytest.fixture
def tiny_skills(tmp_path: Path) -> Path:
    source = tmp_path / "canonical-skills"
    for name in ("wiki-ingest", "wiki-query"):
        skill = source / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(skill_markdown(name), encoding="utf-8")
    reference = source / "wiki-ingest/references/深入阅读.md"
    reference.parent.mkdir()
    reference.write_text("# 深入阅读\n", encoding="utf-8")
    script = source / "wiki-ingest/scripts/run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    (source / "wiki-ingest/assets").mkdir()
    (source / "wiki-ingest/assets/blob.bin").write_bytes(b"\x00\xff\x10wiki\n")
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
    (skill / "SKILL.md").write_text(skill_markdown(name), encoding="utf-8")
    return source


def assert_all_agent_mirrors_match(root: Path) -> None:
    canonical = discover_skill_collection(root / ".skills")
    for agent_relative, _label in portable.PROJECT_AGENT_DIRS:
        mirror_root = root / agent_relative
        mirror = discover_skill_collection(mirror_root)
        assert mirror == canonical
        assert not any(path.is_symlink() for path in mirror_root.rglob("*"))


def make_legacy_adapter_repo(root: Path) -> None:
    """Convert a fresh v2 setup into the exact schema-v1 upgrade fixture."""
    inventory = read_inventory(root, allow_legacy=True)
    if isinstance(inventory, LegacyManagedSkillsInventory):
        if root not in SYNTHETIC_LEGACY_BASELINES:
            canonical = discover_skill_collection(root / ".skills").by_name()
            SYNTHETIC_LEGACY_BASELINES[root] = {
                name: canonical[name].digest for name in inventory.managed_skills
            }
        return
    assert isinstance(inventory, ManagedSkillsInventory)
    SYNTHETIC_LEGACY_BASELINES[root] = dict(inventory.managed_skill_digests)
    for agent_relative, _label in portable.PROJECT_AGENT_DIRS:
        agent_root = root / agent_relative
        root_modes = {
            name: stat.S_IMODE((agent_root / name).stat().st_mode)
            for name in inventory.managed_skills
        }
        skill_file_modes = {
            name: stat.S_IMODE((agent_root / name / "SKILL.md").stat().st_mode)
            for name in inventory.managed_skills
        }
        for name in inventory.managed_skills:
            shutil.rmtree(agent_root / name)
            skill_file = agent_root / name / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            canonical = root / ".skills" / name / "SKILL.md"
            relative = os.path.relpath(canonical, skill_file.parent).replace(
                os.sep, "/"
            )
            skill_file.write_text(
                portable._legacy_adapter_text(name, relative), encoding="utf-8"
            )
            os.chmod(skill_file.parent, root_modes[name])
            os.chmod(skill_file, skill_file_modes[name])
    (root / ".obsidian-wiki/managed-skills.json").write_text(
        portable.render_managed_skills_inventory(
            inventory.skills_version, inventory.managed_skills
        ),
        encoding="utf-8",
    )


def upgrade_portable_skills(
    root: Path, *, version: str, source_skills: Path
) -> tuple[str, ...]:
    """Exercise recognized legacy migration from an explicit synthetic fixture."""
    try:
        inventory = read_inventory(root, allow_legacy=True)
    except ValueError:
        inventory = None
    if inventory is None:
        return _upgrade_portable_skills(
            root, version=version, source_skills=source_skills
        )
    if isinstance(inventory, ManagedSkillsInventory):
        make_legacy_adapter_repo(root)
    legacy = read_inventory(root, allow_legacy=True)
    assert isinstance(legacy, LegacyManagedSkillsInventory)
    synthetic_baseline = SYNTHETIC_LEGACY_BASELINES[root]
    original_loader = portable._load_legacy_skill_digest_catalog
    portable._load_legacy_skill_digest_catalog = lambda: (synthetic_baseline,)
    try:
        return _upgrade_portable_skills(
            root, version=version, source_skills=source_skills
        )
    finally:
        portable._load_legacy_skill_digest_catalog = original_loader


def write_prepared_skill_upgrade_journal(
    root: Path, source: Path, *, version: str
) -> tuple[Path, dict[str, object]]:
    """Create the canonical pre-swap journal state used by recovery tests."""
    make_legacy_adapter_repo(root)
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
                    portable._legacy_adapter_text(
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

    result = run_cli(home, work, "setup", str(target))

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
    assert "* -text" in (target / ".gitattributes").read_text().splitlines()
    assert (target / ".skills/wiki-ingest/SKILL.md").read_bytes() == (
        cli.skills_dir() / "wiki-ingest/SKILL.md"
    ).read_bytes()
    agents = (target / "AGENTS.md").read_text()
    assert MANAGED_START in agents and MANAGED_END in agents
    assert "## Team conventions" in agents
    assert "README Translation Parity" not in agents


def test_portable_complete_mirrors_are_ordinary_and_survive_repo_move(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    target = tmp_path / "knowledge"
    result = run_cli(home, tmp_path, "setup", str(target))
    assert result.returncode == 0, result.stderr
    mirror = target / ".claude/skills/wiki-ingest/SKILL.md"
    canonical = target / ".skills/wiki-ingest/SKILL.md"

    assert mirror.is_file()
    assert not mirror.is_symlink()
    assert mirror.read_bytes() == canonical.read_bytes()
    assert "Portable adapter" not in mirror.read_text(encoding="utf-8")
    assert "../../../.skills/" not in mirror.read_text(encoding="utf-8")

    renamed = tmp_path / "renamed-repository"
    target.rename(renamed)
    assert (renamed / ".claude/skills/wiki-ingest/SKILL.md").read_bytes() == (
        renamed / ".skills/wiki-ingest/SKILL.md"
    ).read_bytes()


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
    for unsupported in ("_archives", "_raw", "_readouts", "_staging"):
        assert not (vault / unsupported).exists()
    assert "OBSIDIAN_RAW_DIR" not in (
        root / ".obsidian-wiki/config.toml"
    ).read_text(encoding="utf-8")
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


def test_gitattributes_preserve_owner_rules_and_disable_byte_conversion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    attributes = root / ".gitattributes"
    owner = "*.png binary\n# owner rule\n"
    attributes.write_text(owner, encoding="utf-8")

    ensure_portable_gitattributes(root)
    first = attributes.read_text(encoding="utf-8")
    ensure_portable_gitattributes(root)

    assert attributes.read_text(encoding="utf-8") == first
    assert first == render_portable_gitattributes(owner)
    assert first.startswith(owner.rstrip("\n"))
    assert "\n* -text\n" in first
    assert first.rstrip().endswith(portable.GITATTRIBUTES_END)


def test_setup_writes_complete_mirrors_and_v2_inventory(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    canonical = discover_skill_collection(root / ".skills")
    assert_all_agent_mirrors_match(root)
    inventory = read_inventory(root)
    assert isinstance(inventory, ManagedSkillsInventory)
    assert inventory.managed_skills == canonical.names
    assert inventory.managed_skill_digests == {
        skill.name: skill.digest for skill in canonical.skills
    }
    assert (root / ".skills/wiki-ingest/references/深入阅读.md").is_file()
    assert (
        root / ".skills/wiki-ingest/assets/blob.bin"
    ).read_bytes() == b"\x00\xff\x10wiki\n"
    assert (root / ".skills/wiki-ingest/scripts/run.sh").stat().st_mode & 0o111


def test_skill_sync_plan_reports_all_agent_additions_without_writing(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    custom = root / ".skills/team-note"
    custom.mkdir()
    (custom / "SKILL.md").write_text(
        skill_markdown("team-note", "Use for team notes."), encoding="utf-8"
    )
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)

    assert report.status == "drift"
    assert report.canonical_skills == ("team-note", "wiki-ingest", "wiki-query")
    assert tuple(target.path for target in report.targets) == tuple(
        relative for relative, _label in portable.PROJECT_AGENT_DIRS
    )
    assert all(target.added == ("team-note",) for target in report.targets)
    assert all(
        not target.changed and not target.removed and not target.unsafe
        for target in report.targets
    )
    assert report.as_dict() == {
        "status": "drift",
        "canonical_skills": ["team-note", "wiki-ingest", "wiki-query"],
        "targets": [
            {
                "path": relative,
                "added": ["team-note"],
                "changed": [],
                "removed": [],
                "unsafe": [],
            }
            for relative, _label in portable.PROJECT_AGENT_DIRS
        ],
        "warnings": [],
    }
    assert snapshot_tree(root) == before


def test_clean_skill_sync_plan_is_stably_ordered_and_preserves_full_trees(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)

    assert report.status == "clean"
    assert tuple(target.path for target in report.targets) == tuple(
        relative for relative, _label in portable.PROJECT_AGENT_DIRS
    )
    assert all(
        not target.added
        and not target.changed
        and not target.removed
        and not target.unsafe
        for target in report.targets
    )
    assert snapshot_tree(root) == before


def test_skill_sync_plan_reports_a_missing_target_as_folded_additions(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    shutil.rmtree(root / ".cursor/skills")
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)
    change = next(item for item in report.targets if item.path == ".cursor/skills")

    assert change.added == ("wiki-ingest", "wiki-query")
    assert not change.changed and not change.removed and not change.unsafe
    assert snapshot_tree(root) == before


def test_skill_sync_apply_recreates_a_missing_agent_parent(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    shutil.rmtree(root / ".cursor")

    report = portable.sync_portable_skill_mirrors(root, apply=True)

    assert report.status == "applied"
    assert_all_agent_mirrors_match(root)


def test_skill_sync_staging_failure_rolls_back_a_created_agent_parent(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    shutil.rmtree(root / ".cursor")
    original_stage = portable._stage_complete_agent_mirrors

    def fail_after_staging(*args: object, **kwargs: object):
        original_stage(*args, **kwargs)
        raise OSError("simulated staging failure")

    monkeypatch.setattr(portable, "_stage_complete_agent_mirrors", fail_after_staging)

    with pytest.raises(OSError, match="staging failure"):
        portable.sync_portable_skill_mirrors(root, apply=True)

    assert not (root / ".cursor").exists()
    assert not (root / portable.SYNC_OPERATION.transactions_relative).exists()


def test_skill_sync_plan_classifies_changed_removed_and_ordinary_invalid_extra(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    changed = root / ".claude/skills/wiki-ingest/SKILL.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "\nMirror drift.\n")
    (root / ".cursor/skills/wiki-query/SKILL.md").unlink()
    extra = root / ".windsurf/skills/owner-extra"
    extra.mkdir()
    (extra / "SKILL.md").write_text("not frontmatter\n", encoding="utf-8")
    (extra / "data.bin").write_bytes(b"\x00\xff")
    binary = root / ".agents/skills/wiki-ingest/assets/blob.bin"
    binary.write_bytes(binary.read_bytes() + b"changed")
    executable = root / ".pi/skills/wiki-ingest/scripts/run.sh"
    executable.chmod(0o644)
    (root / ".kiro/skills/wiki-ingest/references/深入阅读.md").unlink()
    (root / ".skills/wiki-ingest/empty/nested").mkdir(parents=True)
    for agent_relative, _label in portable.PROJECT_AGENT_DIRS:
        (root / agent_relative / "wiki-ingest/empty/nested").mkdir(parents=True)
    (root / ".kiro/skills/wiki-ingest/empty/nested").rmdir()
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)
    by_path = {target.path: target for target in report.targets}

    assert by_path[".claude/skills"].changed == ("wiki-ingest/SKILL.md",)
    assert by_path[".cursor/skills"].added == ("wiki-query/SKILL.md",)
    assert by_path[".windsurf/skills"].removed == ("owner-extra",)
    assert by_path[".agents/skills"].changed == (
        "wiki-ingest/assets/blob.bin",
    )
    assert by_path[".pi/skills"].changed == ("wiki-ingest/scripts/run.sh",)
    assert by_path[".kiro/skills"].added == (
        "wiki-ingest/empty/nested",
        "wiki-ingest/references/深入阅读.md",
    )
    assert all(not target.unsafe for target in report.targets)
    assert snapshot_tree(root) == before


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_skill_sync_plan_reports_unsafe_entries_without_following_them(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("outside\n", encoding="utf-8")
    link = root / ".pi/skills/wiki-ingest/outside-link"
    link.symlink_to(outside, target_is_directory=True)
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)
    by_path = {target.path: target for target in report.targets}

    assert report.status == "drift"
    assert by_path[".pi/skills"].unsafe == ("wiki-ingest/outside-link",)
    assert sentinel.read_text(encoding="utf-8") == "outside\n"
    assert snapshot_tree(root) == before


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_skill_sync_plan_reports_all_safe_and_unsafe_drift_in_one_target(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    target = root / ".claude/skills"
    changed = target / "wiki-ingest/SKILL.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "\nchanged\n")
    extra = target / "owner-extra"
    extra.mkdir()
    (extra / "SKILL.md").write_text("ordinary extra\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "wiki-ingest/unsafe-link").symlink_to(
        outside, target_is_directory=True
    )
    hardlink_source = outside / "hardlink-source"
    hardlink_source.write_bytes(b"outside\n")
    hardlink = target / "wiki-query/SKILL.md"
    hardlink.unlink()
    os.link(hardlink_source, hardlink)
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)
    change = next(item for item in report.targets if item.path == ".claude/skills")

    assert change.added == ()
    assert change.changed == ("wiki-ingest/SKILL.md",)
    assert change.removed == ("owner-extra",)
    assert change.unsafe == (
        "wiki-ingest/unsafe-link",
        "wiki-query/SKILL.md",
    )
    assert hardlink_source.read_bytes() == b"outside\n"
    assert snapshot_tree(root) == before


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_skill_sync_plan_rejects_an_ancestor_symlink_without_following_it(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("outside\n", encoding="utf-8")
    shutil.rmtree(root / ".kiro/skills")
    (root / ".kiro/skills").symlink_to(outside, target_is_directory=True)
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)
    by_path = {target.path: target for target in report.targets}

    assert by_path[".kiro/skills"].unsafe == (".",)
    assert sentinel.read_text(encoding="utf-8") == "outside\n"
    assert snapshot_tree(root) == before


def test_skill_sync_plan_root_level_unsafe_suppresses_all_safe_drift(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    target = root / ".kiro/skills"
    shutil.rmtree(target)
    target.write_text("not a directory\n", encoding="utf-8")
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)
    change = next(item for item in report.targets if item.path == ".kiro/skills")

    assert change.unsafe == (".",)
    assert not change.added and not change.changed and not change.removed
    assert snapshot_tree(root) == before


def test_skill_sync_plan_root_unsafe_collapses_redundant_unsafe_descendants(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    original = portable.snapshot_ordinary_tree_with_unsafe

    def root_and_child_unsafe(path: Path, *, anchor: Path):
        if path == root / ".kiro/skills":
            return (), (
                skill_trees.UnsafeSkillEntry(".", "changed"),
                skill_trees.UnsafeSkillEntry(
                    "wiki-ingest/unsafe-child", "symlink"
                ),
            )
        return original(path, anchor=anchor)

    monkeypatch.setattr(
        portable, "snapshot_ordinary_tree_with_unsafe", root_and_child_unsafe
    )
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)
    change = next(item for item in report.targets if item.path == ".kiro/skills")

    assert change.unsafe == (".",)
    assert not change.added and not change.changed and not change.removed
    assert snapshot_tree(root) == before


def test_skill_sync_plan_warns_for_managed_canonical_digest_divergence(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    canonical = root / ".skills/wiki-query/SKILL.md"
    canonical.write_text(
        skill_markdown("wiki-query", "Owner-modified query workflow."),
        encoding="utf-8",
    )
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)

    assert report.status == "drift"
    assert report.warnings == (
        {
            "code": "managed-canonical-modified",
            "path": ".skills/wiki-query",
            "message": (
                "managed canonical skill differs from the installed inventory digest"
            ),
        },
    )
    assert all(target.changed == ("wiki-query/SKILL.md",) for target in report.targets)
    with pytest.raises(TypeError):
        report.warnings[0]["code"] = "mutated"
    assert snapshot_tree(root) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory-swap race")
def test_skill_sync_plan_binds_mirror_scan_against_swap_and_restore(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    target = root / ".kiro/skills"
    changed_relative = "wiki-query/SKILL.md"
    changed = target / changed_relative
    changed.write_text(changed.read_text(encoding="utf-8") + "\nreal drift\n")
    backup = tmp_path / "kiro-skills-backup"
    target_metadata = target.lstat()
    target_identity = (target_metadata.st_dev, target_metadata.st_ino)
    original_listdir = skill_trees.os.listdir
    swapped = False
    before = snapshot_tree(root)

    def swap_while_descriptor_is_bound(descriptor: int):
        nonlocal swapped
        if not isinstance(descriptor, int):
            return original_listdir(descriptor)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) == target_identity and not swapped:
            swapped = True
            target.rename(backup)
            target.symlink_to(root / ".skills", target_is_directory=True)
            try:
                return original_listdir(descriptor)
            finally:
                target.unlink()
                backup.rename(target)
        return original_listdir(descriptor)

    monkeypatch.setattr(skill_trees.os, "listdir", swap_while_descriptor_is_bound)
    try:
        report = plan_portable_skill_sync(root)
    finally:
        if target.is_symlink():
            target.unlink()
            backup.rename(target)

    change = next(item for item in report.targets if item.path == ".kiro/skills")
    assert swapped
    assert change.changed == (changed_relative,) or change.unsafe == (".",)
    assert snapshot_tree(root) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory-swap race")
def test_skill_sync_plan_binds_canonical_discovery_against_swap_and_restore(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    canonical_skill = root / ".skills/wiki-query"
    canonical_file = canonical_skill / "SKILL.md"
    canonical_file.write_text(
        skill_markdown("wiki-query", "Owner-modified query workflow."),
        encoding="utf-8",
    )
    external = tmp_path / "external-wiki-query"
    shutil.copytree(root / ".claude/skills/wiki-query", external)
    backup = tmp_path / "canonical-wiki-query-backup"
    target_metadata = canonical_skill.lstat()
    target_identity = (target_metadata.st_dev, target_metadata.st_ino)
    original_listdir = skill_trees.os.listdir
    swapped = False
    before = snapshot_tree(root)

    def swap_while_descriptor_is_bound(descriptor: int):
        nonlocal swapped
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) == target_identity and not swapped:
            swapped = True
            canonical_skill.rename(backup)
            canonical_skill.symlink_to(external, target_is_directory=True)
            try:
                return original_listdir(descriptor)
            finally:
                canonical_skill.unlink()
                backup.rename(canonical_skill)
        return original_listdir(descriptor)

    monkeypatch.setattr(skill_trees.os, "listdir", swap_while_descriptor_is_bound)
    try:
        with pytest.raises(ValueError, match="canonical|skill|changed|unsafe"):
            plan_portable_skill_sync(root)
    finally:
        if canonical_skill.is_symlink():
            canonical_skill.unlink()
            backup.rename(canonical_skill)

    assert swapped
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("inventory_kind", ["legacy", "invalid"])
def test_skill_sync_plan_rejects_legacy_or_invalid_inventory_without_writing(
    inventory_kind: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory_path = root / ".obsidian-wiki/managed-skills.json"
    if inventory_kind == "legacy":
        inventory_path.write_text(
            json.dumps(
                {
                    "implementation": IMPLEMENTATION_ID,
                    "skills": ["wiki-ingest", "wiki-query"],
                    "skills_version": "2026.8.3",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    else:
        inventory_path.write_text("{not-json\n", encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="inventory|upgrade-skills"):
        plan_portable_skill_sync(root)

    assert snapshot_tree(root) == before


def test_skill_sync_plan_rejects_invalid_canonical_skill_without_writing(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (root / ".skills/wiki-query/SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: invalid\n---\n", encoding="utf-8"
    )
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="canonical skill") as captured:
        plan_portable_skill_sync(root)

    assert str(root) not in str(captured.value)
    assert snapshot_tree(root) == before


def _add_custom_canonical_skill(root: Path, name: str = "team-note") -> None:
    skill = root / ".skills" / name
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        skill_markdown(name, f"Use the {name} workflow."), encoding="utf-8"
    )


def _setup_cli_portable_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    setup_portable_repo(
        root, version=cli.__version__, source_skills=cli.skills_dir()
    )
    return root


def test_sync_skills_dry_run_apply_and_clean_preserve_authoritative_state(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    owner_only = root / ".claude/skills/owner-only/OWNER.md"
    owner_only.parent.mkdir()
    owner_only.write_text("owner mirror bytes\n", encoding="utf-8")
    canonical_before = snapshot_tree(root / ".skills")
    inventory = root / ".obsidian-wiki/managed-skills.json"
    inventory_before = inventory.read_bytes()

    dry = portable.sync_portable_skill_mirrors(root, apply=False)

    assert dry.status == "drift"
    assert owner_only.is_file()
    assert snapshot_tree(root / ".skills") == canonical_before
    assert inventory.read_bytes() == inventory_before

    applied = portable.sync_portable_skill_mirrors(root, apply=True)

    assert applied.status == "applied"
    assert_all_agent_mirrors_match(root)
    assert not owner_only.exists()
    assert snapshot_tree(root / ".skills") == canonical_before
    assert inventory.read_bytes() == inventory_before
    assert portable.sync_portable_skill_mirrors(root, apply=False).status == "clean"


def test_sync_skills_preserves_existing_mirror_root_mode(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    target = root / ".claude/skills"
    os.chmod(target, 0o700)
    _add_custom_canonical_skill(root)

    report = portable.sync_portable_skill_mirrors(root, apply=True)

    assert report.status == "applied"
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_repo_sync_skills_json_dry_run_and_apply(tmp_path: Path) -> None:
    root = _setup_cli_portable_repo(tmp_path)
    _add_custom_canonical_skill(root)

    dry = run_cli(
        tmp_path / "home", root, "repo", "sync-skills", "--json", "--pretty"
    )

    assert dry.returncode == 1
    assert dry.stderr == ""
    assert json.loads(dry.stdout)["status"] == "drift"
    assert not (root / ".claude/skills/team-note").exists()

    applied = run_cli(
        tmp_path / "home", root, "repo", "sync-skills", "--apply", "--json"
    )

    assert applied.returncode == 0, applied.stderr
    assert applied.stderr == ""
    assert json.loads(applied.stdout)["status"] == "applied"
    assert_all_agent_mirrors_match(root)

    clean = run_cli(tmp_path / "home", root, "repo", "sync-skills", "--json")
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout)["status"] == "clean"


def test_repo_sync_skills_human_output_describes_rebuilt_derived_roots(
    tmp_path: Path,
) -> None:
    root = _setup_cli_portable_repo(tmp_path)
    _add_custom_canonical_skill(root)

    result = run_cli(tmp_path / "home", root, "repo", "sync-skills", "--apply")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "six derived" in result.stdout.lower()
    assert ".skills/" in result.stdout
    assert "inventory" in result.stdout.lower()
    assert "unchanged" in result.stdout.lower()


def test_repo_sync_skills_human_dry_run_and_error_streams(tmp_path: Path) -> None:
    root = _setup_cli_portable_repo(tmp_path)
    _add_custom_canonical_skill(root)

    drift = run_cli(tmp_path / "home", root, "repo", "sync-skills")

    assert drift.returncode == 1
    assert "drift" in drift.stdout.lower()
    assert "--apply" in drift.stdout
    assert drift.stderr == ""

    outside = tmp_path / "outside-human"
    outside.mkdir()
    invalid = run_cli(tmp_path / "home", outside, "repo", "sync-skills")

    assert invalid.returncode == 1
    assert invalid.stdout == ""
    assert invalid.stderr.startswith("error:")


def test_repo_sync_skills_human_success_keeps_canonical_warning(
    tmp_path: Path,
) -> None:
    root = _setup_cli_portable_repo(tmp_path)
    canonical = root / ".skills/wiki-query/SKILL.md"
    canonical.write_text(
        skill_markdown("wiki-query", "Owner-modified query workflow."),
        encoding="utf-8",
    )

    result = run_cli(tmp_path / "home", root, "repo", "sync-skills", "--apply")

    assert result.returncode == 0, result.stderr
    assert "rebuilt" in result.stdout.lower()
    assert "warning:" in result.stderr
    assert "managed canonical skill differs" in result.stderr


def test_repo_sync_skills_json_errors_do_not_mix_stderr_or_partial_reports(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    invalid_context = run_cli(
        tmp_path / "home", outside, "repo", "sync-skills", "--json"
    )

    assert invalid_context.returncode == 1
    assert invalid_context.stderr == ""
    assert json.loads(invalid_context.stdout)["status"] == "error"

    root = _setup_cli_portable_repo(tmp_path)
    (root / ".skills/wiki-query/SKILL.md").write_text(
        "---\nname: [broken\n---\n", encoding="utf-8"
    )
    malformed = run_cli(
        tmp_path / "home", root, "repo", "sync-skills", "--json"
    )

    assert malformed.returncode == 1
    assert malformed.stderr == ""
    error = json.loads(malformed.stdout)
    assert error["status"] == "error"
    assert "canonical" in error["error"].lower()
    assert str(root) not in malformed.stdout


def test_sync_skills_fails_fast_while_repository_lock_is_held_without_writes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    fcntl = pytest.importorskip("fcntl")
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    lock = root / ".obsidian-wiki/local/portable-skills.lock"
    descriptor = os.open(lock, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = snapshot_tree(root)
    try:
        with pytest.raises(ValueError, match="locked|another"):
            portable.sync_portable_skill_mirrors(root, apply=True)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert snapshot_tree(root) == before


def test_portable_setup_lock_creation_does_not_require_posix_dirfd_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(portable, "_sync_dirfd_supported", lambda: False)

    lock = portable._ensure_portable_lock_file(root)

    assert lock == root / ".obsidian-wiki/local/portable-skills.lock"
    assert lock.is_file()


@pytest.mark.parametrize("swapped_targets", range(1, len(portable.PROJECT_AGENT_DIRS) + 1))
def test_sync_recovery_after_every_target_swap_never_accepts_partial_mirrors(
    swapped_targets: int,
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    original_apply = portable._apply_journaled_replacements

    def interrupt_after_target_swaps(
        repository: Path,
        transaction: Path,
        operation: portable.ReplacementOperation,
        payload: dict[str, object],
        records: list[tuple[Path, Path, Path | None, bool]],
        **_kwargs: object,
    ) -> None:
        assert operation == portable.SYNC_OPERATION
        for index, (target, backup, staged, had_target) in enumerate(
            records[:swapped_targets]
        ):
            assert had_target and staged is not None
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.replace(backup)
            (transaction / "install" / str(index)).replace(target)
        raise OSError("simulated process interruption")

    monkeypatch.setattr(
        portable, "_apply_journaled_replacements", interrupt_after_target_swaps
    )
    with pytest.raises(OSError, match="interruption"):
        portable.sync_portable_skill_mirrors(root, apply=True)

    journals = list(
        (root / portable.SYNC_OPERATION.transactions_relative).glob("*/journal.json")
    )
    assert len(journals) == 1
    assert json.loads(journals[0].read_text(encoding="utf-8"))["status"] == "prepared"
    if swapped_targets < len(portable.PROJECT_AGENT_DIRS):
        assert plan_portable_skill_sync(root).status == "drift"

    monkeypatch.setattr(portable, "_apply_journaled_replacements", original_apply)
    recovered = portable.sync_portable_skill_mirrors(root, apply=True)

    assert recovered.status == "applied"
    assert_all_agent_mirrors_match(root)
    assert not journals[0].exists()


def test_sync_rollback_failure_preserves_evidence_for_next_recovery(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    original_rename = portable._rename_sync_path
    failures = {"forward": False, "restore": False}

    def fail_forward_and_restore(
        repository: Path, source: Path, target: Path, **kwargs: object
    ) -> None:
        if (
            not failures["forward"]
            and source.parent.name == "install"
            and target == root / ".cursor/skills"
        ):
            failures["forward"] = True
            raise OSError("simulated sync forward failure")
        if (
            failures["forward"]
            and not failures["restore"]
            and source.parent.name == "backups"
            and target == root / ".claude/skills"
        ):
            failures["restore"] = True
            raise OSError("simulated sync restore failure")
        original_rename(repository, source, target, **kwargs)

    monkeypatch.setattr(portable, "_rename_sync_path", fail_forward_and_restore)
    with pytest.raises(OSError, match="rollback|evidence|preserved"):
        portable.sync_portable_skill_mirrors(root, apply=True)

    journals = list(
        (root / portable.SYNC_OPERATION.transactions_relative).glob("*/journal.json")
    )
    assert len(journals) == 1
    assert any((journals[0].parent / "backups").iterdir())

    monkeypatch.setattr(portable, "_rename_sync_path", original_rename)
    recovered = portable.sync_portable_skill_mirrors(root, apply=True)
    assert recovered.status == "applied"
    assert_all_agent_mirrors_match(root)
    assert not journals[0].exists()


def write_prepared_skill_sync_journal(
    root: Path,
) -> tuple[
    Path,
    dict[str, object],
    list[tuple[Path, Path, Path | None, bool]],
]:
    canonical = skill_trees.discover_anchored_skill_collection(
        root / ".skills", anchor=root
    )
    transaction = portable._create_replacement_transaction(
        root, portable.SYNC_OPERATION
    )
    replacements = portable._stage_complete_agent_mirrors(
        root, transaction, canonical
    )
    payload, records = portable._prepare_replacement_journal(
        root,
        transaction,
        portable.SYNC_OPERATION,
        replacements,
    )
    return transaction, payload, records


def test_bound_sync_install_materializes_snapshot_without_rereading_staged_root(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction = portable._create_replacement_transaction(
        root, portable.SYNC_OPERATION
    )
    staged = transaction / "staged/mirror"
    skill_trees.materialize_skill_collection(
        discover_skill_collection(root / ".skills"), staged
    )
    install = transaction / "install/0"

    def reject_path_reread(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("bound sync install reread staged root")

    monkeypatch.setattr(portable.shutil, "copytree", reject_path_reread)

    portable._copy_staged_replacement(root, staged, install, bound=True)

    assert skill_trees.discover_anchored_skill_collection(
        install, anchor=root
    ) == skill_trees.discover_anchored_skill_collection(
        root / ".skills", anchor=root
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-swap race")
def test_bound_sync_install_parent_swap_never_writes_external_tree(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction = portable._create_replacement_transaction(
        root, portable.SYNC_OPERATION
    )
    staged = transaction / "staged/mirror"
    skill_trees.materialize_skill_collection(
        discover_skill_collection(root / ".skills"), staged
    )
    install_parent = transaction / "install"
    install_parent.mkdir()
    original_parent = transaction / "install-original"
    outside = tmp_path / "outside-install"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("outside owner bytes\n", encoding="utf-8")
    before = snapshot_tree(outside)
    original_state = portable._bound_replacement_state
    swapped = False

    def swap_parent_after_source_snapshot(
        repository: Path, path: Path, *, label: str
    ):
        nonlocal swapped
        state = original_state(repository, path, label=label)
        if path == staged and label == "staged copy source" and not swapped:
            swapped = True
            install_parent.rename(original_parent)
            install_parent.symlink_to(outside, target_is_directory=True)
        return state

    monkeypatch.setattr(
        portable, "_bound_replacement_state", swap_parent_after_source_snapshot
    )
    try:
        with pytest.raises(ValueError, match="unsafe|changed|bound|directory"):
            portable._copy_staged_replacement(
                root, staged, install_parent / "0", bound=True
            )
    finally:
        if install_parent.is_symlink():
            install_parent.unlink()
            original_parent.rename(install_parent)

    assert swapped
    assert snapshot_tree(outside) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-swap race")
def test_bound_sync_install_ordinary_parent_swap_never_writes_external_tree(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction = portable._create_replacement_transaction(
        root, portable.SYNC_OPERATION
    )
    staged = transaction / "staged/mirror"
    skill_trees.materialize_skill_collection(
        discover_skill_collection(root / ".skills"), staged
    )
    install_parent = transaction / "install"
    install_parent.mkdir()
    original_parent = transaction / "install-original"
    outside = tmp_path / "outside-install"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("outside owner bytes\n", encoding="utf-8")
    before = snapshot_tree(outside)
    original_state = portable._bound_replacement_state
    swapped = False

    def swap_parent_after_source_snapshot(
        repository: Path, path: Path, *, label: str
    ):
        nonlocal swapped
        state = original_state(repository, path, label=label)
        if path == staged and label == "staged copy source" and not swapped:
            swapped = True
            install_parent.rename(original_parent)
            outside.rename(install_parent)
        return state

    monkeypatch.setattr(
        portable, "_bound_replacement_state", swap_parent_after_source_snapshot
    )
    try:
        with pytest.raises(ValueError, match="changed|identity|bound|directory"):
            portable._copy_staged_replacement(
                root, staged, install_parent / "0", bound=True
            )
    finally:
        if install_parent.exists():
            install_parent.rename(outside)
        if original_parent.exists():
            original_parent.rename(install_parent)

    assert swapped
    assert snapshot_tree(outside) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-swap race")
def test_bound_sync_live_rename_parent_swap_never_mutates_external_tree(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction = portable._create_replacement_transaction(
        root, portable.SYNC_OPERATION
    )
    backup_parent = transaction / "backups"
    portable._ensure_sync_directory(root, backup_parent, label="test backup")
    source = root / ".claude/skills"
    backup = backup_parent / "0"
    detached = root / ".claude-detached"
    outside = tmp_path / "outside-claude"
    outside_skills = outside / "skills"
    outside_skills.mkdir(parents=True)
    (outside_skills / "sentinel").write_text(
        "outside owner bytes\n", encoding="utf-8"
    )
    before = snapshot_tree(outside)
    original_rename = portable.os.rename
    swapped = False

    def detach_parent_during_bound_rename(
        source_name: str,
        target_name: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        if (
            source_name == "skills"
            and target_name == "0"
            and src_dir_fd is not None
            and dst_dir_fd is not None
            and not swapped
        ):
            swapped = True
            original_rename(root / ".claude", detached)
            (root / ".claude").symlink_to(outside, target_is_directory=True)
        original_rename(
            source_name,
            target_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(portable.os, "rename", detach_parent_during_bound_rename)
    try:
        with pytest.raises(ValueError, match="changed|detached"):
            portable._rename_sync_path(root, source, backup)
    finally:
        monkeypatch.setattr(portable.os, "rename", original_rename)
        if (root / ".claude").is_symlink():
            (root / ".claude").unlink()
        if backup.exists() and not (detached / "skills").exists():
            original_rename(backup, detached / "skills")
        if detached.exists():
            original_rename(detached, root / ".claude")

    assert swapped
    assert snapshot_tree(outside) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-swap race")
def test_bound_sync_live_ordinary_parent_swap_never_mutates_external_tree(
    tmp_path: Path,
    tiny_skills: Path,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction = portable._create_replacement_transaction(
        root, portable.SYNC_OPERATION
    )
    backup_parent = transaction / "backups"
    portable._ensure_sync_directory(root, backup_parent, label="test backup")
    source = root / ".claude/skills"
    backup = backup_parent / "0"
    source_parent_identity = portable._sync_directory_identity(
        root, source.parent, label="test source parent"
    )
    detached = root / ".claude-detached"
    outside = tmp_path / "outside-claude"
    outside_skills = outside / "skills"
    outside_skills.mkdir(parents=True)
    (outside_skills / "sentinel").write_text(
        "outside owner bytes\n", encoding="utf-8"
    )
    before = snapshot_tree(outside)

    (root / ".claude").rename(detached)
    outside.rename(root / ".claude")
    try:
        with pytest.raises(ValueError, match="changed|identity|bound|directory"):
            portable._rename_sync_path(
                root,
                source,
                backup,
                expected_source_parent_identity=source_parent_identity,
            )
    finally:
        if (root / ".claude").exists():
            (root / ".claude").rename(outside)
        if detached.exists():
            detached.rename(root / ".claude")

    assert snapshot_tree(outside) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-swap race")
def test_sync_apply_binds_live_parent_before_ordinary_swap(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    detached = root / ".claude-detached"
    outside = tmp_path / "outside-claude"
    outside_skills = outside / "skills"
    outside_skills.mkdir(parents=True)
    (outside_skills / "sentinel").write_text(
        "outside owner bytes\n", encoding="utf-8"
    )
    before = snapshot_tree(outside)
    original_rename = portable._rename_sync_path
    swapped = False

    def swap_before_first_live_rename(
        repository: Path, source: Path, target: Path, **kwargs: object
    ) -> None:
        nonlocal swapped
        if source == root / ".claude/skills" and not swapped:
            swapped = True
            (root / ".claude").rename(detached)
            outside.rename(root / ".claude")
            try:
                original_rename(repository, source, target, **kwargs)
            finally:
                (root / ".claude").rename(outside)
                detached.rename(root / ".claude")
            return
        original_rename(repository, source, target, **kwargs)

    monkeypatch.setattr(portable, "_rename_sync_path", swap_before_first_live_rename)

    with pytest.raises(ValueError, match="changed|identity|bound|directory"):
        portable.sync_portable_skill_mirrors(root, apply=True)

    assert swapped
    assert snapshot_tree(outside) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-swap race")
def test_sync_apply_binds_live_parent_before_staging(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    detached = root / ".claude-detached"
    outside = tmp_path / "outside-claude"
    outside_skills = outside / "skills"
    outside_skills.mkdir(parents=True)
    (outside_skills / "sentinel").write_text(
        "outside owner bytes\n", encoding="utf-8"
    )
    before = snapshot_tree(outside)
    original_stage = portable._stage_complete_agent_mirrors
    swapped = False

    def swap_before_staging(
        repository: Path,
        transaction: Path,
        canonical: skill_trees.SkillCollection,
        **kwargs: object,
    ):
        nonlocal swapped
        swapped = True
        (root / ".claude").rename(detached)
        outside.rename(root / ".claude")
        try:
            return original_stage(
                repository, transaction, canonical, **kwargs
            )
        finally:
            (root / ".claude").rename(outside)
            detached.rename(root / ".claude")

    monkeypatch.setattr(portable, "_stage_complete_agent_mirrors", swap_before_staging)

    with pytest.raises(ValueError, match="changed|identity|bound|directory"):
        portable.sync_portable_skill_mirrors(root, apply=True)

    assert swapped
    assert snapshot_tree(outside) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor-relative rename")
def test_bound_sync_rename_never_overwrites_concurrent_destination(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction = portable._create_replacement_transaction(
        root, portable.SYNC_OPERATION
    )
    backup_parent = transaction / "backups"
    portable._ensure_sync_directory(root, backup_parent, label="test backup")
    source = root / ".claude/skills"
    target = backup_parent / "0"
    original_rename = portable.os.rename
    inserted = False

    def insert_owner_destination_before_rename(
        source_name: str,
        target_name: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal inserted
        assert src_dir_fd is not None and dst_dir_fd is not None
        if source_name == "skills" and target_name == "0" and not inserted:
            inserted = True
            os.rmdir(target_name, dir_fd=dst_dir_fd)
            os.mkdir(target_name, dir_fd=dst_dir_fd)
            owner_dir_fd = os.open(
                target_name,
                portable._inventory_directory_flags(),
                dir_fd=dst_dir_fd,
            )
            try:
                owner_fd = os.open(
                    "owner.txt",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=owner_dir_fd,
                )
                try:
                    os.write(owner_fd, b"owner bytes\n")
                finally:
                    os.close(owner_fd)
            finally:
                os.close(owner_dir_fd)
        original_rename(
            source_name,
            target_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(portable.os, "rename", insert_owner_destination_before_rename)

    with pytest.raises(OSError):
        portable._rename_sync_path(root, source, target)

    assert inserted
    assert source.is_dir()
    assert (target / "owner.txt").read_text(encoding="utf-8") == "owner bytes\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor-relative lock")
def test_portable_skills_lock_parent_swap_never_touches_external_tree(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    local = root / ".obsidian-wiki/local"
    detached = root / ".obsidian-wiki/local-detached"
    outside = tmp_path / "outside-local"
    outside.mkdir()
    (outside / "sentinel").write_text("owner bytes\n", encoding="utf-8")
    before = snapshot_tree(outside)
    original_open = portable.os.open
    swapped = False

    def swap_before_lock_open(path: object, flags: int, *args, **kwargs) -> int:
        nonlocal swapped
        if (
            not swapped
            and (path == root / portable._PORTABLE_SKILLS_LOCK or path == "portable-skills.lock")
        ):
            swapped = True
            local.rename(detached)
            local.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(portable.os, "open", swap_before_lock_open)
    try:
        with (
            pytest.raises(ValueError, match="changed|detached|unsafe"),
            portable._portable_skills_lock(root),
        ):
            pass
    finally:
        monkeypatch.setattr(portable.os, "open", original_open)
        if local.is_symlink():
            local.unlink()
        if detached.exists():
            detached.rename(local)

    assert swapped
    assert snapshot_tree(outside) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX transaction identity binding")
def test_sync_recovery_rejects_transaction_swap_after_authorization(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    transaction, _payload, _records = write_prepared_skill_sync_journal(root)
    detached = transaction.with_name(f"{transaction.name}-detached")
    original_authorize = portable._authorize_sync_recovery
    swapped = False

    def swap_after_authorization(*args, **kwargs):
        nonlocal swapped
        result = original_authorize(*args, **kwargs)
        transaction.rename(detached)
        shutil.copytree(detached, transaction)
        (transaction / "replacement-marker").write_text(
            "replacement transaction\n", encoding="utf-8"
        )
        swapped = True
        return result

    monkeypatch.setattr(portable, "_authorize_sync_recovery", swap_after_authorization)

    with pytest.raises(ValueError, match="transaction.*changed|identity"):
        portable.sync_portable_skill_mirrors(root, apply=False)

    assert swapped
    assert (transaction / "replacement-marker").is_file()
    assert (detached / "journal.json").is_file()


@pytest.mark.skipif(os.name != "posix", reason="POSIX transaction identity binding")
def test_sync_rollback_revalidates_transaction_before_live_removal(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    transaction, _payload, records = write_prepared_skill_sync_journal(root)
    target, backup, _staged, had_target = records[0]
    assert had_target
    portable._rename_sync_path(root, target, backup)
    portable._rename_sync_path(root, transaction / "install/0", target)
    transaction_identity = portable._sync_directory_identity(
        root, transaction, label="test transaction"
    )
    detached = transaction.with_name(f"{transaction.name}-detached")
    original_state = portable._bound_replacement_state
    live_before = snapshot_tree(target)
    swapped = False

    def swap_after_staged_proof(
        repository: Path, path: Path, *, label: str
    ):
        nonlocal swapped
        result = original_state(repository, path, label=label)
        if label == "rollback staged proof" and not swapped:
            transaction.rename(detached)
            shutil.copytree(detached, transaction)
            swapped = True
        return result

    monkeypatch.setattr(portable, "_bound_replacement_state", swap_after_staged_proof)

    errors = portable._rollback_upgrade_records(
        root,
        records,
        operation=portable.SYNC_OPERATION,
        removable_new_targets={target},
        sync_transaction_binding=(transaction, transaction_identity),
    )

    assert swapped
    assert errors
    assert snapshot_tree(target) == live_before


def rewrite_sync_journal(transaction: Path, payload: dict[str, object]) -> None:
    (transaction / "journal.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize("status", ["prepared", "committed"])
def test_sync_recovers_each_journal_status(
    status: str,
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    _add_custom_canonical_skill(root)
    transaction, _payload, _records = write_prepared_skill_sync_journal(root)
    if status == "committed":
        original_cleanup = portable._cleanup_replacement_transaction

        def fail_committed_cleanup(
            repository: Path,
            candidate: Path,
            operation: portable.ReplacementOperation,
            **_kwargs: object,
        ) -> None:
            assert operation == portable.SYNC_OPERATION
            raise OSError("simulated committed cleanup interruption")

        monkeypatch.setattr(
            portable, "_cleanup_replacement_transaction", fail_committed_cleanup
        )
        with pytest.raises(OSError, match="cleanup interruption"):
            payload, records, _parents = portable._load_replacement_journal(
                root, transaction, portable.SYNC_OPERATION
            )
            portable._apply_journaled_replacements(
                root, transaction, portable.SYNC_OPERATION, payload, records
            )
        monkeypatch.setattr(
            portable, "_cleanup_replacement_transaction", original_cleanup
        )
        assert json.loads(
            (transaction / "journal.json").read_text(encoding="utf-8")
        )["status"] == "committed"

    report = portable.sync_portable_skill_mirrors(root, apply=True)

    assert report.status == ("applied" if status == "prepared" else "clean")
    assert_all_agent_mirrors_match(root)
    assert not transaction.exists()


@pytest.mark.parametrize(
    "corruption",
    [
        "schema3-sync",
        "operation-mismatch",
        "canonical-target",
        "missing-root",
        "duplicate-root",
        "extra-root",
    ],
)
def test_sync_recovery_rejects_unauthorized_schema_operation_or_record_plan(
    corruption: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction, payload, _records = write_prepared_skill_sync_journal(root)
    raw_records = payload["replacements"]
    assert isinstance(raw_records, list)
    if corruption == "schema3-sync":
        payload["schema_version"] = 3
        payload.pop("operation")
    elif corruption == "operation-mismatch":
        payload["operation"] = "upgrade"
    elif corruption == "canonical-target":
        assert isinstance(raw_records[0], dict)
        raw_records[0]["target"] = ".skills"
    elif corruption == "missing-root":
        raw_records.pop()
    elif corruption == "duplicate-root":
        assert isinstance(raw_records[1], dict)
        assert isinstance(raw_records[0], dict)
        raw_records[1]["target"] = raw_records[0]["target"]
    else:
        assert isinstance(raw_records[-1], dict)
        raw_records[-1]["target"] = "AGENTS.md"
    rewrite_sync_journal(transaction, payload)
    before = snapshot_tree(root)

    with pytest.raises(
        ValueError, match="schema-3|operation|target|record|six|duplicate"
    ):
        portable.sync_portable_skill_mirrors(root, apply=False)

    assert snapshot_tree(root) == before
    assert (transaction / "journal.json").is_file()


@pytest.mark.parametrize("tampering", ["staged", "install", "canonical"])
def test_sync_recovery_rejects_proof_or_canonical_tampering_without_writes(
    tampering: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction, _payload, _records = write_prepared_skill_sync_journal(root)
    if tampering == "staged":
        target = transaction / "staged/mirrors/0/wiki-ingest/SKILL.md"
    elif tampering == "install":
        target = transaction / "install/0/wiki-ingest/SKILL.md"
    else:
        target = root / ".skills/wiki-ingest/SKILL.md"
    target.write_text(target.read_text(encoding="utf-8") + "\ntampered\n")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="canonical|proof|candidate|differs"):
        portable.sync_portable_skill_mirrors(root, apply=False)

    assert snapshot_tree(root) == before
    assert (transaction / "journal.json").is_file()


def test_schema4_sync_journal_is_bound_to_sync_transaction_directory(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction, _payload, _records = write_prepared_skill_sync_journal(root)
    upgrades = root / portable.UPGRADE_OPERATION.transactions_relative
    upgrades.mkdir(parents=True)
    moved = upgrades / transaction.name
    transaction.replace(moved)
    try:
        (root / portable.SYNC_OPERATION.transactions_relative).rmdir()
    except OSError:
        pass
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="operation|identity|journal"):
        portable.sync_portable_skill_mirrors(root, apply=False)

    assert snapshot_tree(root) == before
    assert (moved / "journal.json").is_file()


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory-swap race")
@pytest.mark.parametrize("proof_kind", ["install", "live"])
def test_sync_recovery_binds_proof_and_live_postimage_against_swap_restore(
    proof_kind: str,
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    transaction, payload, records = write_prepared_skill_sync_journal(root)
    if proof_kind == "live":
        for index, (target, backup, staged, had_target) in enumerate(records):
            assert had_target and staged is not None
            target.replace(backup)
            (transaction / "install" / str(index)).replace(target)
        payload["status"] = "committed"
        rewrite_sync_journal(transaction, payload)
        proof = records[0][0]
    else:
        proof = transaction / "install/0"
    replacement = tmp_path / f"{proof_kind}-proof-backup"
    observed = proof.lstat()
    identity = (observed.st_dev, observed.st_ino)
    original_listdir = skill_trees.os.listdir
    swapped = False

    def swap_while_descriptor_is_bound(descriptor: int):
        nonlocal swapped
        if not isinstance(descriptor, int):
            return original_listdir(descriptor)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) == identity and not swapped:
            swapped = True
            proof.rename(replacement)
            proof.symlink_to(root / ".skills", target_is_directory=True)
            try:
                return original_listdir(descriptor)
            finally:
                proof.unlink()
                replacement.rename(proof)
        return original_listdir(descriptor)

    monkeypatch.setattr(skill_trees.os, "listdir", swap_while_descriptor_is_bound)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="changed|unsafe|proof|postimage"):
        portable.sync_portable_skill_mirrors(root, apply=False)

    assert swapped
    assert snapshot_tree(root) == before
    assert (transaction / "journal.json").is_file()


def test_cli_check_recovers_pending_sync_but_direct_checker_is_read_only(
    tmp_path: Path,
) -> None:
    from obsidian_wiki.portable_check import check_portable_repo

    root = _setup_cli_portable_repo(tmp_path)
    _add_custom_canonical_skill(root)
    transaction, _payload, records = write_prepared_skill_sync_journal(root)
    target, backup, _staged, _had_target = records[0]
    backup.parent.mkdir(parents=True, exist_ok=True)
    target.replace(backup)
    (transaction / "install/0").replace(target)
    config = load_portable_config(
        root / ".obsidian-wiki/config.toml",
        installed_version=cli.__version__,
        implementation=IMPLEMENTATION_ID,
    )
    before = snapshot_tree(root)

    direct = check_portable_repo(config)

    assert direct["status"] == "fail"
    assert snapshot_tree(root) == before
    assert (transaction / "journal.json").is_file()

    checked = run_cli(tmp_path / "home", root, "check", "--json")

    assert checked.returncode == 1
    assert json.loads(checked.stdout)["status"] == "fail"
    assert not transaction.exists()


def test_initial_setup_snapshots_bundled_source_once(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    original = portable.discover_skill_collection
    source_calls: list[bool] = []

    def record_discovery(path: Path, *, ignore_source_artifacts: bool = False):
        if path == tiny_skills:
            source_calls.append(ignore_source_artifacts)
        return original(path, ignore_source_artifacts=ignore_source_artifacts)

    monkeypatch.setattr(portable, "discover_skill_collection", record_discovery)

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert source_calls == [True]


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
        ["--portable"],
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
        str(target),
        *legacy_args,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert not target.exists()


def test_setup_portable_without_directory_defaults_to_current_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "cwd-target"
    target.mkdir()

    result = run_cli(tmp_path / "home", target, "setup")

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
            content = path.read_bytes()
            assert all(value.encode("utf-8") not in content for value in forbidden), (
                path
            )
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


def test_nonignored_top_level_skill_source_file_is_rejected(tmp_path: Path) -> None:
    source = make_skill_source(tmp_path)
    (source / "top-level.txt").write_text("not a skill\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git/SKILL.md").write_text("# fake cache skill\n", encoding="utf-8")
    root = tmp_path / "repo"

    with pytest.raises(ValueError, match="each skill must be an ordinary directory"):
        setup_portable_repo(root, version="2026.8.3", source_skills=source)

    assert not root.exists()


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


def test_setup_scaffolds_git_only_target_without_mutating_git_metadata(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    git_dir = root / ".git"
    objects = git_dir / "objects"
    objects.mkdir(parents=True)
    config = git_dir / "config"
    config.write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    git_dir.chmod(0o750)
    config.chmod(0o640)
    before_tree = snapshot_tree(git_dir)
    before_metadata = {
        path: (path.lstat().st_ino, stat.S_IMODE(path.lstat().st_mode))
        for path in (git_dir, config)
    }

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert (root / ".obsidian-wiki/config.toml").is_file()
    assert (root / "wiki").is_dir()
    assert snapshot_tree(git_dir) == before_tree
    assert {
        path: (path.lstat().st_ino, stat.S_IMODE(path.lstat().st_mode))
        for path in (git_dir, config)
    } == before_metadata


def test_setup_rejects_git_plus_owner_content_without_changes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "README.md").write_text("owner content\n", encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="not a portable") as exc_info:
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert "missing/empty" in str(exc_info.value)
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("git_kind", ["file", "symlink"])
def test_setup_rejects_non_directory_git_only_target(
    git_kind: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git_entry = root / ".git"
    if git_kind == "file":
        git_entry.write_text("gitdir: ../worktree.git\n", encoding="utf-8")
    else:
        external = tmp_path / "external-git"
        external.mkdir()
        git_entry.symlink_to(external, target_is_directory=True)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match=r"\.git.*ordinary directory"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


def test_git_only_target_merge_failure_restores_exact_original(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    git_dir = root / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text("owner git metadata\n", encoding="utf-8")
    before = snapshot_tree(root)
    original_replace = Path.replace
    staged_moves = 0
    failure_raised = False

    def fail_third_staged_move(self: Path, target: Path) -> Path:
        nonlocal staged_moves, failure_raised
        target = Path(target)
        if (
            self.parent.parent == root.parent
            and self.parent.name.startswith(f".{root.name}.obsidian-wiki-")
            and target.parent == root
        ):
            staged_moves += 1
            if staged_moves == 3 and not failure_raised:
                failure_raised = True
                raise OSError("simulated staged merge failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_third_staged_move)

    with pytest.raises(OSError, match="simulated staged merge failure"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert failure_raised
    assert snapshot_tree(root) == before
    assert not list(root.parent.glob(f".{root.name}.obsidian-wiki-*"))


def test_git_only_target_staging_cleanup_failure_restores_exact_original(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    git_dir = root / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text("owner git metadata\n", encoding="utf-8")
    before = snapshot_tree(root)
    original_rmdir = Path.rmdir
    failure_raised = False

    def fail_staging_cleanup_once(self: Path) -> None:
        nonlocal failure_raised
        if (
            not failure_raised
            and self.parent == root.parent
            and self.name.startswith(f".{root.name}.obsidian-wiki-")
            and not any(self.iterdir())
        ):
            failure_raised = True
            raise OSError("simulated staging cleanup failure")
        original_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", fail_staging_cleanup_once)

    with pytest.raises(OSError, match="simulated staging cleanup failure"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert failure_raised
    assert snapshot_tree(root) == before
    assert not list(root.parent.glob(f".{root.name}.obsidian-wiki-*"))


def test_setup_cleanup_failure_preserves_original_error_and_staging_evidence(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    original_rmtree = shutil.rmtree

    def fail_preflight(*args: object, **kwargs: object) -> None:
        raise ValueError("simulated setup preflight failure")

    def fail_staging_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        candidate = Path(path)
        if (
            candidate.parent == root.parent
            and candidate.name.startswith(f".{root.name}.obsidian-wiki-")
        ):
            raise OSError("simulated staging evidence cleanup failure")
        original_rmtree(candidate, *args, **kwargs)

    monkeypatch.setattr(portable, "_preflight_existing_portable", fail_preflight)
    monkeypatch.setattr(shutil, "rmtree", fail_staging_cleanup)

    with pytest.raises(OSError, match="cleanup is incomplete") as exc_info:
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert str(exc_info.value.__cause__) == "simulated setup preflight failure"
    assert "simulated staging evidence cleanup failure" in str(exc_info.value)
    staging = list(root.parent.glob(f".{root.name}.obsidian-wiki-*"))
    assert len(staging) == 1
    assert snapshot_tree(staging[0])
    assert not root.exists()


def test_setup_cli_scaffolds_git_only_target_and_validators_pass(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "repo"
    git_env = os.environ.copy()
    git_env["HOME"] = str(home)
    initialized = subprocess.run(
        ["git", "init", "--quiet", str(root)],
        env=git_env,
        text=True,
        capture_output=True,
    )
    assert initialized.returncode == 0, initialized.stderr
    assert {path.name for path in root.iterdir()} == {".git"}

    setup = run_cli(home, tmp_path, "setup", str(root))
    doctor = run_cli(home, root, "doctor")
    check = run_cli(home, root, "check")

    assert setup.returncode == 0, setup.stderr
    assert doctor.returncode == 0, doctor.stderr
    assert check.returncode == 0, check.stderr
    assert (root / ".git").is_dir()
    assert not (home / ".obsidian-wiki").exists()


def test_existing_portable_rerun_rejects_mirror_drift_without_writing(
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

    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="sync-skills --apply"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before
    assert config.read_bytes() == config_bytes


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
    first = run_cli(tmp_path / "home", tmp_path, "setup", str(root))
    assert first.returncode == 0, first.stderr
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8") + "\n## Team policy\nUse our glossary.\n",
        encoding="utf-8",
    )

    second = run_cli(tmp_path / "home", tmp_path, "setup", str(root))

    assert second.returncode == 0, second.stderr
    assert "## Team policy\nUse our glossary." in agents.read_text(encoding="utf-8")


def test_repo_upgrade_skills_refuses_mirror_drift_and_preserves_team_sentence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    setup = run_cli(home, tmp_path, "setup", str(root))
    assert setup.returncode == 0, setup.stderr
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8") + "\nTeam-owned sentence.\n",
        encoding="utf-8",
    )
    mirror = root / ".claude/skills/wiki-ingest/SKILL.md"
    mirror.write_text(mirror.read_text() + "\nMirror drift.\n", encoding="utf-8")
    before = snapshot_tree(root)

    result = run_cli(home, root, "repo", "upgrade-skills")

    assert result.returncode == 1
    assert "sync-skills --apply" in result.stderr
    assert snapshot_tree(root) == before
    assert "Team-owned sentence." in agents.read_text(encoding="utf-8")


def test_initial_setup_writes_exact_managed_skills_inventory(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"

    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    inventory_path = root / ".obsidian-wiki/managed-skills.json"
    inventory = read_inventory(root)
    canonical = discover_skill_collection(root / ".skills")
    assert isinstance(inventory, ManagedSkillsInventory)
    assert inventory.schema_version == 2
    assert inventory.mirror_format == "full-copy-v1"
    assert inventory.managed_skills == canonical.names
    assert inventory.managed_skill_digests == {
        skill.name: skill.digest for skill in canonical.skills
    }
    assert inventory_path.read_text(encoding="utf-8").endswith("\n")


def test_v2_upgrade_preserves_custom_canonical_and_rebuilds_full_mirrors(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    custom = root / ".skills/team-workflow"
    custom.mkdir()
    (custom / "SKILL.md").write_text(
        skill_markdown("team-workflow", "Team-owned workflow."), encoding="utf-8"
    )
    (custom / "references").mkdir()
    (custom / "references/团队约定.md").write_text(
        "# 团队约定\n", encoding="utf-8"
    )
    portable.sync_portable_skill_mirrors(root, apply=True)
    custom_before = snapshot_tree(custom)

    (tiny_skills / "wiki-ingest/SKILL.md").write_text(
        skill_markdown("wiki-ingest", "Upgraded managed workflow."),
        encoding="utf-8",
    )
    new_skill = tiny_skills / "wiki-new"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text(skill_markdown("wiki-new"), encoding="utf-8")

    names = _upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert names == ("wiki-ingest", "wiki-new", "wiki-query")
    assert snapshot_tree(custom) == custom_before
    assert_all_agent_mirrors_match(root)
    inventory = read_inventory(root)
    assert isinstance(inventory, ManagedSkillsInventory)
    assert inventory.skills_version == "2026.8.4"
    assert inventory.managed_skills == names
    managed = discover_skill_collection(tiny_skills, ignore_source_artifacts=True)
    assert dict(inventory.managed_skill_digests) == {
        skill.name: skill.digest for skill in managed.skills
    }


def test_v2_upgrade_refuses_modified_managed_canonical_before_staging(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    canonical = root / ".skills/wiki-ingest/SKILL.md"
    canonical.write_text(canonical.read_text() + "\nOwner modification.\n")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="managed canonical.*digest|modified"):
        _upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


def test_v2_upgrade_refuses_mirror_drift_with_sync_remediation(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    mirror = root / ".claude/skills/wiki-ingest/SKILL.md"
    mirror.write_text(mirror.read_text() + "\nMirror-only edit.\n")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="repo sync-skills --apply"):
        _upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


def test_legacy_adapter_upgrade_requires_known_canonical_baseline(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    make_legacy_adapter_repo(root)
    canonical = root / ".skills/wiki-ingest/SKILL.md"
    canonical.write_text(canonical.read_text() + "\nOwner modification.\n")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="legacy canonical.*not recognized"):
        _upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


@pytest.mark.parametrize("corruption", ["missing", "modified", "extra", "mixed"])
def test_legacy_adapter_migration_requires_an_exact_uniform_adapter_set(
    corruption: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    make_legacy_adapter_repo(root)
    adapter = root / ".claude/skills/wiki-ingest"
    if corruption == "missing":
        (adapter / "SKILL.md").unlink()
    elif corruption == "modified":
        skill_file = adapter / "SKILL.md"
        skill_file.write_text(skill_file.read_text() + "\nOwner edit.\n")
    elif corruption == "extra":
        (adapter / "OWNER.txt").write_text("owner bytes\n", encoding="utf-8")
    else:
        shutil.rmtree(adapter)
        shutil.copytree(root / ".skills/wiki-ingest", adapter)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="adapter|SKILL.md|legacy"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


def test_legacy_custom_skill_must_be_absent_everywhere_or_mirrored_everywhere(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    custom = root / ".skills/team-workflow"
    custom.mkdir()
    (custom / "SKILL.md").write_text(
        skill_markdown("team-workflow", "Team-owned workflow."), encoding="utf-8"
    )
    make_legacy_adapter_repo(root)
    shutil.copytree(custom, root / ".claude/skills/team-workflow")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="absent everywhere|across all agents"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert snapshot_tree(root) == before


def test_recognized_legacy_migration_emits_structured_warning(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    make_legacy_adapter_repo(root)
    warnings: list[dict[str, str]] = []
    original_loader = portable._load_legacy_skill_digest_catalog
    portable._load_legacy_skill_digest_catalog = lambda: (
        SYNTHETIC_LEGACY_BASELINES[root],
    )
    try:
        names = _upgrade_portable_skills(
            root,
            version="2026.8.4",
            source_skills=tiny_skills,
            warning_sink=warnings,
        )
    finally:
        portable._load_legacy_skill_digest_catalog = original_loader

    assert names == ("wiki-ingest", "wiki-query")
    assert warnings == [
        {
            "code": "legacy-adapters-migrated",
            "message": (
                "recognized legacy adapters were migrated to complete agent skill "
                "mirrors"
            ),
        }
    ]


def test_v2_committed_upgrade_recovers_cleanup_after_inventory_swap(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text(
        skill_markdown("wiki-ingest", "Upgraded after committed recovery."),
        encoding="utf-8",
    )
    original_cleanup = portable._cleanup_replacement_transaction
    interrupted = False

    def interrupt_committed_cleanup(
        repository: Path,
        transaction: Path,
        operation: portable.ReplacementOperation = portable.UPGRADE_OPERATION,
        **kwargs: object,
    ) -> None:
        nonlocal interrupted
        journal = transaction / "journal.json"
        if (
            operation == portable.UPGRADE_OPERATION
            and journal.is_file()
            and json.loads(journal.read_text(encoding="utf-8"))["status"]
            == "committed"
            and not interrupted
        ):
            interrupted = True
            raise OSError("simulated committed upgrade cleanup interruption")
        original_cleanup(repository, transaction, operation, **kwargs)

    monkeypatch.setattr(
        portable, "_cleanup_replacement_transaction", interrupt_committed_cleanup
    )
    with pytest.raises(OSError, match="committed upgrade cleanup"):
        _upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    journals = list(
        (root / portable.UPGRADE_OPERATION.transactions_relative).glob(
            "*/journal.json"
        )
    )
    assert interrupted and len(journals) == 1
    assert json.loads(journals[0].read_text(encoding="utf-8"))["status"] == "committed"
    inventory = read_inventory(root)
    assert isinstance(inventory, ManagedSkillsInventory)
    assert inventory.skills_version == "2026.8.4"

    monkeypatch.setattr(
        portable, "_cleanup_replacement_transaction", original_cleanup
    )
    names = _upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert names == ("wiki-ingest", "wiki-query")
    assert not journals[0].exists()
    assert_all_agent_mirrors_match(root)


def test_setup_v2_rerun_rejects_canonical_and_custom_skill_drift_without_writes(
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
    canonical.write_text(
        skill_markdown("wiki-ingest", "Owner-edited managed workflow."),
        encoding="utf-8",
    )
    owner_skill = root / ".skills/team-skill"
    owner_skill.mkdir()
    (owner_skill / "SKILL.md").write_text(
        skill_markdown("team-skill", "Team-owned workflow."), encoding="utf-8"
    )
    owner_adapter = root / ".claude/skills/team-skill"
    owner_adapter.mkdir()
    (owner_adapter / "notes.txt").write_text("owner adapter\n", encoding="utf-8")
    owner_bootstrap = root / ".cursor/rules/team.mdc"
    owner_bootstrap.write_text("owner bootstrap\n", encoding="utf-8")
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nOwner ending.\n", encoding="utf-8")
    bundled_new = tiny_skills / "wiki-new"
    bundled_new.mkdir()
    (bundled_new / "SKILL.md").write_text(skill_markdown("wiki-new"), encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="sync-skills --apply"):
        setup_portable_repo(root, version="2026.8.4", source_skills=tiny_skills)

    assert snapshot_tree(root) == before
    assert inventory.read_bytes() == inventory_bytes
    assert config.read_bytes() == config_bytes
    assert not (root / ".skills/wiki-new").exists()


def test_setup_v2_rerun_rejects_inventory_ownership_not_in_canonical(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["managed_skills"].append("zzghost")
    payload["managed_skill_digests"]["zzghost"] = "sha256:" + "0" * 64
    inventory.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="missing.*zzghost|zzghost.*missing"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


def test_upgrade_parent_swap_never_mutates_external_agent_tree(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text(
        skill_markdown("wiki-ingest", "Upgraded workflow."), encoding="utf-8"
    )
    external = tmp_path / "external-claude"
    shutil.copytree(root / ".claude", external)
    external_before = snapshot_tree(external)
    original_apply = portable._apply_journaled_upgrade
    original_replace = Path.replace
    swapped = False
    external_mutation_attempted = False

    def observe_replace(source: Path, target: Path) -> Path:
        nonlocal external_mutation_attempted
        if source == root / ".claude/skills" and source.parent.is_symlink():
            external_mutation_attempted = True
        return original_replace(source, target)

    def swap_parent_then_apply(
        repository: Path,
        transaction: Path,
        payload: dict[str, object],
        records: list[tuple[Path, Path, Path | None, bool]],
        **kwargs: object,
    ) -> None:
        nonlocal swapped
        parent = root / ".claude"
        held = root / ".claude-held"
        parent.rename(held)
        try:
            parent.symlink_to(external, target_is_directory=True)
        except OSError:
            held.rename(parent)
            pytest.skip("symlinks are unavailable")
        swapped = True
        try:
            original_apply(repository, transaction, payload, records, **kwargs)
        finally:
            if parent.is_symlink():
                parent.unlink()
            if held.exists():
                held.rename(parent)

    monkeypatch.setattr(portable, "_apply_journaled_upgrade", swap_parent_then_apply)
    monkeypatch.setattr(Path, "replace", observe_replace)

    with pytest.raises((OSError, ValueError), match="unsafe|changed|symlink|parent"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert swapped
    assert not external_mutation_attempted
    assert snapshot_tree(external) == external_before


def test_upgrade_refuses_managed_canonical_edit_after_staging(
    tmp_path: Path,
    tiny_skills: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text(
        skill_markdown("wiki-ingest", "Upgraded workflow."), encoding="utf-8"
    )
    target = root / ".skills/wiki-ingest/SKILL.md"
    owner_edit = skill_markdown("wiki-ingest", "Concurrent owner edit.")
    original_apply = portable._apply_journaled_upgrade
    edited = False

    def edit_then_apply(
        repository: Path,
        transaction: Path,
        payload: dict[str, object],
        records: list[tuple[Path, Path, Path | None, bool]],
        **kwargs: object,
    ) -> None:
        nonlocal edited
        target.write_text(owner_edit, encoding="utf-8")
        edited = True
        original_apply(repository, transaction, payload, records, **kwargs)

    monkeypatch.setattr(portable, "_apply_journaled_upgrade", edit_then_apply)

    with pytest.raises((OSError, ValueError), match="changed|diverged|preimage"):
        upgrade_portable_skills(
            root, version="2026.8.4", source_skills=tiny_skills
        )

    assert edited
    assert target.read_text(encoding="utf-8") == owner_edit


def test_setup_without_inventory_requires_explicit_skill_upgrade_without_writes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    inventory.unlink()

    before = snapshot_tree(root)
    with pytest.raises(ValueError, match="repo upgrade-skills"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    assert snapshot_tree(root) == before


def test_setup_legacy_inventory_requires_explicit_upgrade_without_writes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    inventory.write_text(
        json.dumps(
            {
                "implementation": IMPLEMENTATION_ID,
                "skills": ["wiki-ingest", "wiki-query"],
                "skills_version": "2026.8.3",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="repo upgrade-skills"):
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

    with pytest.raises(ValueError, match="repo upgrade-skills"):
        setup_portable_repo(root, version="2026.8.4", source_skills=tiny_skills)

    assert not transaction.exists()


def test_upgrade_replaces_adds_removes_and_rebuilds_managed_skills(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    v2 = tmp_path / "v2"
    rendered = {
        name: skill_markdown(name, f"Use the upgraded {name} workflow.")
        for name in ("wiki-ingest", "wiki-new")
    }
    for name, body in rendered.items():
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
    assert (root / ".skills/wiki-ingest/SKILL.md").read_text() == rendered[
        "wiki-ingest"
    ]
    assert (root / ".skills/wiki-new/SKILL.md").read_text() == rendered["wiki-new"]
    assert not (root / ".skills/wiki-query").exists()
    for agent_relative, _label in cli.PROJECT_AGENT_DIRS:
        assert not (root / agent_relative / "wiki-query").exists()
        for name in names:
            assert snapshot_tree(root / agent_relative / name) == snapshot_tree(
                root / ".skills" / name
            )
    inventory = read_inventory(root)
    assert isinstance(inventory, ManagedSkillsInventory)
    assert inventory.skills_version == "2026.8.4"
    assert inventory.managed_skills == names
    assert {path: path.read_bytes() for path in untouched_paths} == untouched
    assert git_config.read_text(encoding="utf-8") == "owner git data\n"


def test_upgrade_preserves_existing_skill_root_modes_and_defaults_new_roots_to_0755(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    existing_canonical = root / ".skills/wiki-ingest"
    mirror_roots = [
        root / agent_relative
        for agent_relative, _label in cli.PROJECT_AGENT_DIRS
    ]
    os.chmod(existing_canonical, 0o700)
    for path in mirror_roots:
        os.chmod(path, 0o700)
    new_skill = tiny_skills / "wiki-new"
    new_skill.mkdir(mode=0o700)
    os.chmod(new_skill, 0o700)
    (new_skill / "SKILL.md").write_text(skill_markdown("wiki-new"), encoding="utf-8")

    upgrade_portable_skills(
        root, version="2026.8.4", source_skills=tiny_skills
    )

    assert stat.S_IMODE(existing_canonical.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in mirror_roots)
    assert stat.S_IMODE((root / ".skills/wiki-new").stat().st_mode) == 0o755
    assert all(
        stat.S_IMODE((path / "wiki-new").stat().st_mode) == 0o755
        for path in mirror_roots
    )


def test_upgrade_never_copies_transaction_artifacts_directly_to_final_targets(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    new_skill = tiny_skills / "wiki-new"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text(skill_markdown("wiki-new"), encoding="utf-8")
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

    assert (root / ".skills/wiki-new/SKILL.md").read_text(
        encoding="utf-8"
    ) == skill_markdown("wiki-new")


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
        (path / "SKILL.md").write_text(
            skill_markdown("team-owned", "Team-owned workflow."), encoding="utf-8"
        )
    before = {path: snapshot_tree(path) for path in owner_paths}

    upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert {path: snapshot_tree(path) for path in owner_paths} == before


@pytest.mark.parametrize("collision_location", ["canonical", "adapter"])
def test_upgrade_new_bundled_skill_owner_collision_fails_closed(
    collision_location: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    make_legacy_adapter_repo(root)
    new_skill = tiny_skills / "team-owned"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text(skill_markdown("team-owned"), encoding="utf-8")
    collision = (
        root / ".skills/team-owned"
        if collision_location == "canonical"
        else root / ".claude/skills/team-owned"
    )
    collision.mkdir(parents=True)
    (collision / "SKILL.md").write_text(
        skill_markdown("team-owned", "Owner collision."), encoding="utf-8"
    )
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
    setup = run_cli(home, tmp_path, "setup", str(root))
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
    make_legacy_adapter_repo(root)
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

    with pytest.raises(ValueError, match="symlink|symbolic link"):
        upgrade_portable_skills(root, version="2026.8.4", source_skills=tiny_skills)

    assert snapshot_tree(root) == before
    assert (external / "sentinel").read_text() == "outside\n"


def test_upgrade_rolls_back_when_staged_directory_swap_fails(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    make_legacy_adapter_repo(root)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text(
        skill_markdown("wiki-ingest", "Use the upgraded ingest workflow."),
        encoding="utf-8",
    )
    before = snapshot_tree(root)
    original_rename = portable._rename_sync_path

    def fail_staged_canonical(
        repository: Path, source: Path, target: Path, **kwargs: object
    ) -> None:
        if (
            source.parent.name == "install"
            and Path(target) == root / ".skills/wiki-ingest"
        ):
            raise OSError("simulated staged swap failure")
        original_rename(repository, source, target, **kwargs)

    monkeypatch.setattr(portable, "_rename_sync_path", fail_staged_canonical)

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
    make_legacy_adapter_repo(root)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text(
        skill_markdown("wiki-ingest", "Use the upgraded ingest workflow."),
        encoding="utf-8",
    )
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nOwner sentence.\n", encoding="utf-8")
    shutil.rmtree(root / ".github")
    if preexisting_empty_parent:
        (root / ".github").mkdir()
    before = snapshot_tree(root)

    original_rename = portable._rename_sync_path

    def fail_inventory(
        repository: Path, source: Path, target: Path, **kwargs: object
    ) -> None:
        if (
            source.parent.name == "install"
            and Path(target) == root / ".obsidian-wiki/managed-skills.json"
        ):
            raise OSError("simulated inventory commit failure")
        original_rename(repository, source, target, **kwargs)

    monkeypatch.setattr(portable, "_rename_sync_path", fail_inventory)

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
    original_rename = portable._rename_sync_path

    def populate_parent_then_fail(
        repository: Path, source: Path, target: Path, **kwargs: object
    ) -> None:
        if (
            source.parent.name == "install"
            and Path(target) == root / ".obsidian-wiki/managed-skills.json"
        ):
            (root / ".github/OWNER.txt").write_text(
                "concurrent owner data\n", encoding="utf-8"
            )
            raise OSError("simulated inventory commit failure")
        original_rename(repository, source, target, **kwargs)

    monkeypatch.setattr(portable, "_rename_sync_path", populate_parent_then_fail)

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
    make_legacy_adapter_repo(root)
    (tiny_skills / "wiki-ingest/SKILL.md").write_text(
        skill_markdown("wiki-ingest", "Use ingest v2."), encoding="utf-8"
    )
    (tiny_skills / "wiki-query/SKILL.md").write_text(
        skill_markdown("wiki-query", "Use query v2."), encoding="utf-8"
    )
    old_inventory = (root / ".obsidian-wiki/managed-skills.json").read_bytes()
    original_rename = portable._rename_sync_path
    failures = {"forward": False, "restore": False, "restore_attempts": 0}

    def fail_forward_and_one_restore(
        repository: Path, source: Path, target: Path, **kwargs: object
    ) -> None:
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
        original_rename(repository, source, target, **kwargs)

    monkeypatch.setattr(
        portable, "_rename_sync_path", fail_forward_and_one_restore
    )

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

    monkeypatch.setattr(portable, "_rename_sync_path", original_rename)
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
    assert (root / ".skills/wiki-ingest/SKILL.md").read_text() == skill_markdown(
        "wiki-ingest", "Use ingest v2."
    )
    assert (root / ".skills/wiki-query/SKILL.md").read_text() == skill_markdown(
        "wiki-query", "Use query v2."
    )
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
    make_legacy_adapter_repo(root)
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
    make_legacy_adapter_repo(root)
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
    assert stat.S_IMODE(adapter.stat().st_mode) == 0o644
    assert "transaction-only writes" in agents.read_text(encoding="utf-8")


def test_pre_inventory_repository_fails_without_partial_writes(
    tmp_path: Path, tiny_skills: Path
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

    with pytest.raises(ValueError, match="repo upgrade-skills"):
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
    (new_skill / "SKILL.md").write_text(skill_markdown("wiki-new"), encoding="utf-8")
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
    (new_skill / "SKILL.md").write_text(skill_markdown("wiki-new"), encoding="utf-8")
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
    (new_skill / "SKILL.md").write_text(skill_markdown("wiki-new"), encoding="utf-8")
    original_apply = portable._apply_journaled_upgrade

    def crash_before_inventory(
        repository: Path,
        _transaction: Path,
        _payload: dict[str, object],
        records: list[tuple[Path, Path, Path | None, bool]],
        **_kwargs: object,
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
    assert (root / ".skills/wiki-new/SKILL.md").read_text() == skill_markdown(
        "wiki-new"
    )
    for agent_relative, _label in cli.PROJECT_AGENT_DIRS:
        assert (root / agent_relative / "wiki-new/SKILL.md").is_file()
    assert not list(
        (root / ".obsidian-wiki/local/skill-upgrades").glob("*/journal.json")
    )
    inventory = read_inventory(root)
    assert isinstance(inventory, ManagedSkillsInventory)
    assert inventory.managed_skills == ("wiki-ingest", "wiki-new", "wiki-query")


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
        **_kwargs: object,
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
        **_kwargs: object,
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

    result = run_cli(tmp_path / "home", tmp_path, "setup", str(root))

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
    if operation == "upgrade":
        make_legacy_adapter_repo(root)
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

    with pytest.raises(
        ValueError, match="hard link|multiple links|multiply-linked|single-link"
    ):
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


@pytest.mark.parametrize(
    "owner_location",
    [
        ".skills/team-owned/OWNER.md",
        ".claude/skills/team-owned/OWNER.md",
    ],
)
def test_existing_setup_rejects_hardlinks_in_custom_skill_trees(
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
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="multiply-linked|sync-skills"):
        setup_portable_repo(root, version="2026.8.4", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


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

    with pytest.raises(
        ValueError, match="hard link|multiple links|multiply-linked"
    ) as exc_info:
        setup_portable_repo(target, version="2026.8.3", source_skills=source)

    assert SOURCE_REINSTALL_COMMAND in str(exc_info.value)

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

    with pytest.raises(ValueError, match="ordinary|regular|special"):
        setup_portable_repo(target, version="2026.8.3", source_skills=source)

    assert not target.exists()
    assert not any(path.name.startswith(".repo.obsidian-wiki-") for path in tmp_path.iterdir())

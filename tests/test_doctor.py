"""Tests for the doctor CLI command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki.cli import list_skills, skills_dir
from obsidian_wiki.portable import setup_portable_repo


def _run(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def _write_config(home: Path, vault: Path, *, version: str | None = None) -> None:
    config_dir = home / ".obsidian-wiki"
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = [f'OBSIDIAN_VAULT_PATH="{vault}"']
    if version is not None:
        lines.append(f'OBSIDIAN_WIKI_VERSION="{version}"')
    (config_dir / "config").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_vault(vault: Path, *, manifest: str = '{"sources": {}}') -> None:
    vault.mkdir(parents=True, exist_ok=True)
    for name in ("index.md", "log.md", "hot.md"):
        (vault / name).write_text(f"# {name}\n", encoding="utf-8")
    (vault / ".manifest.json").write_text(manifest, encoding="utf-8")


def _install_all_skills(home: Path) -> None:
    target = home / ".claude" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    for name in list_skills():
        skill_dir = target / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")


def _make_portable_repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "knowledge"
    setup_portable_repo(root, version=__version__, source_skills=skills_dir())
    hot = root / "wiki/hot.md"
    if hot.exists():
        hot.unlink()
    nested = root / "work/nested"
    nested.mkdir(parents=True)
    return root, nested


def test_doctor_json_clean_install(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _make_vault(vault)
    _write_config(home, vault)
    _install_all_skills(home)

    proc = _run(home, "doctor", "--json")

    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["status"] == "pass"
    assert any(check["name"] == "manifest-json" and check["status"] == "pass" for check in data["checks"])


def test_doctor_warns_without_agent_installs_but_exits_zero(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _make_vault(vault)
    _write_config(home, vault)

    proc = _run(home, "doctor", "--json")

    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["status"] == "warn"
    assert any(check["name"] == "agent-installs" and check["status"] == "warn" for check in data["checks"])


def test_doctor_fails_on_invalid_manifest(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _make_vault(vault, manifest="{not json")
    _write_config(home, vault)
    _install_all_skills(home)

    proc = _run(home, "doctor", "--json")

    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["status"] == "fail"
    assert any(check["name"] == "manifest-json" and check["status"] == "fail" for check in data["checks"])


def test_doctor_strict_turns_warnings_into_nonzero_exit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _make_vault(vault)
    _write_config(home, vault, version="0.0.0")

    proc = _run(home, "doctor", "--json", "--strict")

    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert any(check["name"] == "setup-version" and check["status"] == "warn" for check in data["checks"])


def test_doctor_portable_mode_ignores_global_config_and_agent_installs(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    global_vault = tmp_path / "global-vault"
    _make_vault(global_vault)
    _write_config(home, global_vault)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "pass"
    checks = {check["name"]: check for check in report["checks"]}
    for name in (
        "portable-config",
        "implementation",
        "portable-paths",
        "project-skills",
    ):
        assert checks[name]["status"] == "pass"
    assert "global-config" not in checks
    assert "agent-installs" not in checks
    assert str(root / "wiki") in json.dumps(report)
    assert str(global_vault) not in json.dumps(report)
    assert not (root / "wiki/hot.md").exists()


def test_doctor_portable_mode_does_not_require_global_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _root, nested = _make_portable_repo(tmp_path)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "pass"
    assert all(
        check["name"] not in {"global-config", "agent-installs"}
        for check in report["checks"]
    )


def test_doctor_wrong_portable_implementation_fails_without_global_fallback(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    config = root / ".obsidian-wiki/config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            IMPLEMENTATION_ID, "Ar9av/obsidian-wiki"
        ),
        encoding="utf-8",
    )
    global_vault = tmp_path / "global-vault"
    _make_vault(global_vault)
    _write_config(home, global_vault)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["implementation"]["status"] == "fail"
    assert str(global_vault) not in json.dumps(report)
    assert "global-config" not in checks


def test_doctor_dangling_portable_config_symlink_fails_without_global_fallback(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    config = root / ".obsidian-wiki/config.toml"
    config.unlink()
    config.symlink_to(tmp_path / "missing-config.toml")
    global_vault = tmp_path / "global-vault"
    _make_vault(global_vault)
    _write_config(home, global_vault)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["portable-config"]["status"] == "fail"
    assert "symlink" in json.dumps(report).lower()
    assert str(global_vault) not in json.dumps(report)


@pytest.mark.parametrize("entry_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize(
    "relative",
    [
        ".obsidian-wiki/config.toml",
        "CLAUDE.md",
        "wiki/index.md",
        ".skills/wiki-ingest/SKILL.md",
        ".claude/skills/wiki-ingest/SKILL.md",
    ],
)
def test_doctor_portable_mode_rejects_unsafe_managed_paths_without_fallback(
    entry_kind: str,
    relative: str,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    target = root / relative
    content = target.read_bytes()
    target.unlink()
    external = tmp_path / "external-managed-file"
    external.write_bytes(content)
    if entry_kind == "symlink":
        target.symlink_to(external)
    else:
        os.link(external, target)
    global_vault = tmp_path / "global-vault"
    _make_vault(global_vault)
    _write_config(home, global_vault)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["status"] == "fail"
    assert any(check["status"] == "fail" for check in report["checks"])
    details = json.dumps(report).lower()
    assert "symlink" in details if entry_kind == "symlink" else "hard link" in details
    assert str(global_vault) not in json.dumps(report)

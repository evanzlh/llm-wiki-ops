from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID


ROOT = Path(__file__).resolve().parents[1]


def write_portable(root: Path, body: str | None = None) -> Path:
    config = root / ".obsidian-wiki" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        body
        or f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"

[paths]
vault = "wiki"
sources = ["sources", "imports"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    return config


def write_legacy(path: Path, vault: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'OBSIDIAN_VAULT_PATH="{vault}"\n',
        encoding="utf-8",
    )
    return path


def run_info(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", "info", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


def test_info_json_reports_portable_runtime_and_global_installation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    config = write_portable(project)
    global_vault = tmp_path / "global-vault"
    write_legacy(home / ".obsidian-wiki" / "config", global_vault)

    result = run_info(home, project, "--json", "--pretty")

    assert result.returncode == 0
    assert result.stderr == ""
    data = payload(result)
    runtime = data["runtime"]
    assert runtime == {
        "status": "resolved",
        "mode": "portable",
        "source": str(config.resolve()),
        "vault": str((project / "wiki").resolve()),
        "portable": {
            "root": str(project.resolve()),
            "sources": [
                str((project / "sources").resolve()),
                str((project / "imports").resolve()),
            ],
            "skills": str((project / ".skills").resolve()),
            "local_state": str((project / ".obsidian-wiki" / "local").resolve()),
        },
    }
    assert data["installation"]["global_default"]["vault"] == str(global_vault)
    assert data["warnings"] == []


def test_info_json_explicit_vault_warns_when_portable_context_is_overridden(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    write_portable(project)
    write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-vault")
    vault = (project / "wiki").resolve()

    result = run_info(home, project, "--vault", str(vault), "--json")

    assert result.returncode == 0
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["mode"] == "explicit"
    assert [warning["code"] for warning in data["warnings"]] == [
        "portable-context-overridden"
    ]


def test_info_json_without_config_is_available_with_setup_guidance(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = run_info(home, cwd, "--json")

    assert result.returncode == 0
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["status"] == "unconfigured"
    assert "obsidian-wiki setup" in data["runtime"]["guidance"]
    assert data["installation"]["version"]


def test_info_json_invalid_authoritative_portable_is_one_error_document(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    write_portable(project, "this is not valid TOML = [")

    result = run_info(home, project, "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["status"] == "error"
    assert "invalid portable configuration" in data["runtime"]["error"]
    assert data["installation"]["skills"]


def test_info_human_output_separates_runtime_installation_and_warning(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    write_portable(project)
    vault = (project / "wiki").resolve()

    result = run_info(home, project, "--vault", str(vault))

    assert result.returncode == 0
    assert "Runtime context" in result.stdout
    assert "CLI installation" in result.stdout
    assert "skills root:" in result.stdout
    assert result.stderr.count("portable context") == 1


def test_info_human_output_uses_indented_section_layout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    config = write_portable(project)

    result = run_info(home, project)

    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[:10] == [
        "Runtime context",
        "  status: resolved",
        "  mode: portable",
        f"  source: {config.resolve()}",
        f"  vault: {(project / 'wiki').resolve()}",
        f"  repository: {project.resolve()}",
        f"  source: {(project / 'sources').resolve()}",
        f"  source: {(project / 'imports').resolve()}",
        f"  skills: {(project / '.skills').resolve()}",
        f"  local state: {(project / '.obsidian-wiki' / 'local').resolve()}",
    ]
    assert lines[10] == ""
    assert lines[11] == "CLI installation"
    assert all(line.startswith("  ") for line in lines[12:19])
    assert lines[14].startswith("  skills root: ")
    assert lines[18] == "  agent installs:"
    assert lines[19:]
    assert all(line.startswith("    ") for line in lines[19:])


@pytest.mark.parametrize(
    ("name", "setup", "arg", "expected_mode"),
    [
        ("named", "named", "@work", "named"),
        ("env", "env", None, "env"),
        ("global", "global", None, "global"),
        ("explicit", "none", "explicit-wiki", "explicit"),
    ],
)
def test_info_json_uses_each_resolution_source(
    tmp_path: Path, name: str, setup: str, arg: str | None, expected_mode: str
) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project" / "nested"
    cwd.mkdir(parents=True)
    if setup == "named":
        write_legacy(home / ".obsidian-wiki" / "config.work", tmp_path / "named-wiki")
    elif setup == "env":
        write_legacy(cwd.parent / ".env", tmp_path / "env-wiki")
    elif setup == "global":
        write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-wiki")

    result = run_info(home, cwd, *( ["--vault", arg] if arg else [] ), "--json")

    assert result.returncode == 0, (name, result.stderr)
    assert result.stderr == ""
    assert payload(result)["runtime"]["mode"] == expected_mode


def test_info_explicit_does_not_mutate_config_files_or_symlink_targets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    default = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "default")
    named_target = write_legacy(home / "saved-work-config", tmp_path / "work")
    named = home / ".obsidian-wiki" / "config.work"
    named.symlink_to(named_target)
    before = {
        "default": default.read_bytes(),
        "named_target": named_target.read_bytes(),
        "named_link": os.readlink(named),
    }

    result = run_info(home, cwd, "--vault", "@work", "--json")

    assert result.returncode == 0
    assert result.stderr == ""
    assert default.read_bytes() == before["default"]
    assert named_target.read_bytes() == before["named_target"]
    assert os.readlink(named) == before["named_link"]


def test_info_json_has_no_human_rendering_glyphs_or_headings(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = run_info(tmp_path / "home", cwd, "--json")

    assert result.returncode == 0
    for token in ("⚠️", "✅", "Runtime context", "CLI installation"):
        assert token not in result.stdout

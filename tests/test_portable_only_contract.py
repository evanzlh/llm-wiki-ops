from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import cli


ROOT = Path(__file__).resolve().parents[1]


def run_cli(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )


def test_setup_directory_creates_portable_repository(tmp_path: Path) -> None:
    target = tmp_path / "knowledge"

    result = run_cli(tmp_path / "home", tmp_path, "setup", str(target))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert f"Repository scaffolded at {target.absolute()}" in result.stdout
    assert f"Open {target.absolute() / 'wiki'} in Obsidian" in result.stdout
    assert (target / ".obsidian-wiki/config.toml").is_file()


def test_setup_defaults_to_current_directory(tmp_path: Path) -> None:
    target = tmp_path / "knowledge"
    target.mkdir()

    result = run_cli(tmp_path / "home", target, "setup")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert f"Repository scaffolded at {target.absolute()}" in result.stdout
    assert (target / ".obsidian-wiki/config.toml").is_file()


def test_bare_cli_prints_help_without_writing_repository_state(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()

    result = run_cli(tmp_path / "home", work)

    assert result.returncode == 0, result.stderr
    assert "usage: obsidian-wiki" in result.stdout
    assert "setup" in result.stdout
    assert result.stderr == ""
    assert not (work / ".obsidian-wiki").exists()


def test_cli_has_no_global_agent_installation_surface() -> None:
    assert not hasattr(cli, "GLOBAL_AGENT_DIRS")
    assert not hasattr(cli, "_agent_install_payload")


@pytest.mark.parametrize(
    ("command", "legacy_label"),
    [("info", "agent installs"), ("doctor", "agent-installs")],
)
def test_inspection_commands_do_not_report_global_agent_installations(
    command: str, legacy_label: str, tmp_path: Path
) -> None:
    result = run_cli(tmp_path / "home", tmp_path, command)

    assert legacy_label not in result.stdout.lower()


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
def test_setup_rejects_removed_arguments(
    legacy_args: list[str], tmp_path: Path
) -> None:
    result = run_cli(tmp_path / "home", tmp_path, "setup", *legacy_args)

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert not (tmp_path / ".obsidian-wiki").exists()

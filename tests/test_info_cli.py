from __future__ import annotations

import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

import obsidian_wiki.cli as cli
from obsidian_wiki import IMPLEMENTATION_ID


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
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    return config


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink behavior")
@pytest.mark.parametrize(
    "command",
    (
        ("repo", "sync-skills", "--json"),
        ("hot", "status", "--json"),
        ("hot", "mark-current", "--json"),
        ("hot", "inputs", "--json"),
        ("cache-check", "sources/example.md", "--json"),
        ("check", "--json"),
        ("doctor", "--json"),
        ("lint", "--json"),
        (
            "trust-record",
            "--all",
            "--reviewed-at",
            "2026-08-13T00:00:00Z",
            "--approved",
            "--json",
        ),
        ("trust-check", "--json"),
        ("query", "question", "--json"),
        ("context-pack", "topic", "--json"),
        ("transaction", "list", "--json"),
    ),
)
def test_repository_json_commands_structure_config_resolution_errors(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    write_portable(repository)
    (repository / "wiki").symlink_to("wiki")

    result = subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *command],
        cwd=repository,
        env={**os.environ, "HOME": str(home)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1, (command, result.stdout, result.stderr)
    assert result.stderr == "", command
    document = json.loads(result.stdout)
    assert document["status"] in {"error", "fail"}, (command, document)
    rendered = json.dumps(document)
    assert "paths.vault" in rendered, (command, document)
    assert "Traceback" not in result.stdout


def test_info_json_reports_repository_local_runtime(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    config = write_portable(repository)
    wiki = repository / "wiki"
    wiki.mkdir()

    result = run_info(home, wiki, "--json", "--pretty")

    assert result.returncode == 0
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"] == {
        "status": "resolved",
        "config": str(config),
        "root": str(repository),
        "vault": str(wiki),
        "sources": [str(repository / "sources")],
        "skills": str(repository / ".skills"),
        "local_state": str(repository / ".obsidian-wiki/local"),
    }
    assert "global_config" not in data["installation"]
    assert "agents" not in data["installation"]


def test_info_json_without_config_is_available_with_exact_setup_guidance(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = run_info(home, cwd, "--json")

    assert result.returncode == 0
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["status"] == "unconfigured"
    assert data["runtime"]["guidance"] == "run: obsidian-wiki setup [DIR]"
    assert data["installation"]["version"]
    assert data["installation"]["skills"]
    assert "global_config" not in data["installation"]
    assert "agents" not in data["installation"]


def test_info_json_invalid_portable_is_one_error_document(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    write_portable(repository, "this is not valid TOML = [")

    result = run_info(home, repository, "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["status"] == "error"
    assert "invalid portable configuration" in data["runtime"]["error"]
    assert data["installation"]["skills"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX link safety contract")
def test_info_json_rejects_symlinked_repository_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    real = write_portable(tmp_path / "real")
    linked = repository / ".obsidian-wiki" / "config.toml"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(real)

    result = run_info(home, repository, "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["status"] == "error"
    assert "symlinks are not allowed" in data["runtime"]["error"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink behavior")
def test_info_json_structures_configured_path_symlink_loop(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    write_portable(repository)
    (repository / "wiki").symlink_to("wiki")

    result = run_info(home, repository, "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["status"] == "error"
    assert "paths.vault cannot be resolved safely" in data["runtime"]["error"]
    assert "Traceback" not in result.stdout


def test_info_human_output_has_repository_local_sections(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    config = write_portable(repository)

    result = run_info(home, repository)

    assert result.returncode == 0
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines[:8] == [
        "Runtime context",
        "  status: resolved",
        f"  config: {config}",
        f"  repository: {repository}",
        f"  vault: {repository / 'wiki'}",
        f"  source: {repository / 'sources'}",
        f"  skills: {repository / '.skills'}",
        f"  local state: {repository / '.obsidian-wiki/local'}",
    ]
    assert "CLI installation" in lines
    assert "agent installs:" not in result.stdout


def test_info_json_has_no_human_rendering_glyphs_or_headings(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = run_info(tmp_path / "home", cwd, "--json")

    assert result.returncode == 0
    assert "Runtime context" not in result.stdout
    assert "CLI installation" not in result.stdout
    assert "✓" not in result.stdout
    assert "⚠" not in result.stdout


def test_info_json_structures_unavailable_current_directory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def cwd_failure() -> Path:
        raise PermissionError("cwd denied")

    monkeypatch.setattr(Path, "cwd", cwd_failure)

    returncode = cli.cmd_info(Namespace(json=True, pretty=False, vault=None))

    captured = capsys.readouterr()
    assert returncode == 1
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data["runtime"] == {
        "status": "error",
        "error": "current working directory is unavailable: cwd denied",
    }

"""Tests for the Portable repository context-pack CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from obsidian_wiki import IMPLEMENTATION_ID


def run_cli(
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


def make_repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "knowledge"
    vault = root / "wiki"
    nested = root / "work/nested"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / ".skills").mkdir()
    vault.mkdir()
    nested.mkdir(parents=True)
    (root / ".obsidian-wiki/config.toml").write_text(
        f'''schema_version = 1
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
    (vault / "auth.md").write_text(
        "# Authentication\n\nUse short-lived access tokens.\n", encoding="utf-8"
    )
    return root, vault, nested


def test_context_pack_uses_portable_vault_from_nested_directory(tmp_path: Path) -> None:
    _root, _vault, nested = make_repository(tmp_path)

    proc = run_cli(
        tmp_path / "home",
        "context-pack",
        "authentication",
        "--budget",
        "512",
        cwd=nested,
    )

    assert proc.returncode == 0, proc.stderr
    assert "# Agent Context: authentication" in proc.stdout
    assert "auth.md" in proc.stdout


def test_context_alias_uses_portable_vault_and_emits_json(tmp_path: Path) -> None:
    _root, _vault, nested = make_repository(tmp_path)

    proc = run_cli(
        tmp_path / "home", "context", "authentication", "--json", "--pretty", cwd=nested
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["schema_version"] == 1
    assert data["pages"][0]["path"] == "auth.md"
    assert "context_warnings" not in data


def test_recent_mode_does_not_require_topic(tmp_path: Path) -> None:
    _root, _vault, nested = make_repository(tmp_path)

    proc = run_cli(tmp_path / "home", "context-pack", "--recent", cwd=nested)

    assert proc.returncode == 0, proc.stderr
    assert "# Agent Context: Recent Activity" in proc.stdout


def test_topic_is_required_without_recent(tmp_path: Path) -> None:
    _root, _vault, nested = make_repository(tmp_path)

    proc = run_cli(tmp_path / "home", "context-pack", cwd=nested)

    assert proc.returncode == 1
    assert "topic is required" in proc.stderr


def test_context_pack_requires_portable_repository(tmp_path: Path) -> None:
    proc = run_cli(tmp_path / "home", "context-pack", "authentication", cwd=tmp_path)

    assert proc.returncode == 1
    assert "repository not configured" in proc.stderr

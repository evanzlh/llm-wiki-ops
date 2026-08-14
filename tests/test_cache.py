"""Tests for the portable content-hash cache surface."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.cache import (
    check_sources,
    compute_hash,
    hash_file,
    sha256_dir,
    sha256_file,
)
from obsidian_wiki.config import load_portable_config


@pytest.fixture
def src_file(tmp_path: Path) -> Path:
    path = tmp_path / "doc.md"
    path.write_text("# Hello\nSome content.", encoding="utf-8")
    return path


@pytest.fixture
def src_dir(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    (path / "a.py").write_text("x = 1", encoding="utf-8")
    (path / "b.py").write_text("y = 2", encoding="utf-8")
    return path


@pytest.fixture
def portable_repo(tmp_path: Path):
    root = tmp_path / "portable"
    (root / ".llmwikiops").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "wiki").mkdir()
    (root / ".skills").mkdir()
    config_path = root / ".llmwikiops" / "config.toml"
    config_path.write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".llmwikiops/local"
''',
        encoding="utf-8",
    )
    (root / "wiki" / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
    )
    config = load_portable_config(
        config_path,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    return root, config


class TestHashing:
    def test_sha256_file_deterministic(self, src_file):
        assert sha256_file(src_file) == sha256_file(src_file)

    def test_sha256_file_changes_on_edit(self, src_file):
        before = sha256_file(src_file)
        src_file.write_text("changed", encoding="utf-8")
        assert sha256_file(src_file) != before

    def test_sha256_dir_deterministic(self, src_dir):
        assert sha256_dir(src_dir) == sha256_dir(src_dir)

    def test_compute_hash_dispatches_and_hash_file_aliases(self, src_file, src_dir):
        assert hash_file(src_file) == sha256_file(src_file)
        assert len(compute_hash(src_dir)) == 64


def test_check_sources_reports_new_portable_source(portable_repo):
    root, config = portable_repo
    source = root / "sources" / "note.md"
    source.write_text("note", encoding="utf-8")

    assert check_sources(config, [source]) == {
        "new": ["sources/note.md"],
        "modified": [],
        "unchanged": [],
        "missing": [],
    }


def _run(cwd: Path, home: Path, *args: str):
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("cwd_name", [".", "wiki"])
def test_cache_check_resolves_repository_from_nested_cwd(
    portable_repo, tmp_path, cwd_name
):
    root, _config = portable_repo
    (root / "sources" / "note.md").write_text("note", encoding="utf-8")
    cwd = root if cwd_name == "." else root / cwd_name

    proc = _run(cwd, tmp_path / "home", "cache-check", "sources/note.md", "--json")

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["new"] == ["sources/note.md"]
    assert proc.stderr == ""


def test_cache_check_does_not_follow_terminal_source_symlink(portable_repo, tmp_path):
    root, _config = portable_repo
    target = root / "sources" / "ordinary.md"
    target.write_text("source", encoding="utf-8")
    alias = root / "sources" / "alias.md"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")

    proc = _run(root, tmp_path / "home", "cache-check", "sources/alias.md", "--json")

    assert proc.returncode == 1
    assert proc.stderr == ""
    assert "source must be a single-link ordinary file" in json.loads(proc.stdout)[
        "error"
    ]["message"]


@pytest.mark.parametrize("source", ["../outside-missing.md", "{absolute}"])
def test_cache_check_rejects_missing_paths_outside_repository(
    portable_repo, tmp_path, source
):
    root, _config = portable_repo
    if source == "{absolute}":
        source = str(tmp_path / "absolute-outside-missing.md")

    proc = _run(root, tmp_path / "home", "cache-check", source, "--json")

    assert proc.returncode == 1
    assert proc.stderr == ""
    assert "outside the repository root" in json.loads(proc.stdout)["error"][
        "message"
    ]
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("cache-update", "wiki", "source"),
        ("cache-check", "--configured", "source"),
    ],
)
def test_removed_cache_cli_forms_are_argparse_errors(tmp_path, args):
    proc = _run(tmp_path, tmp_path / "home", *args)
    assert proc.returncode == 2


def test_cache_hash_remains_standalone(src_file, tmp_path):
    proc = _run(tmp_path, tmp_path / "home", "cache-hash", str(src_file))
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["sha256"] == sha256_file(src_file)

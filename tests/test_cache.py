"""Tests for the content-hash cache module."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.cache import (
    check_sources,
    compute_hash,
    hash_file,
    sha256_file,
    sha256_dir,
    update_source,
    _load_manifest,
    _manifest_path,
)
from obsidian_wiki.config import load_portable_config


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def src_file(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# Hello\nSome content.", encoding="utf-8")
    return f


@pytest.fixture
def src_dir(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    (d / "a.py").write_text("x = 1")
    (d / "b.py").write_text("y = 2")
    return d


@pytest.fixture
def portable_repo(tmp_path):
    root = tmp_path / "portable"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "wiki").mkdir()
    (root / ".skills").mkdir()
    config_path = root / ".obsidian-wiki" / "config.toml"
    config_path.write_text(
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


# ---------------------------------------------------------------------------
# Hash functions
# ---------------------------------------------------------------------------

class TestHashing:
    def test_sha256_file_deterministic(self, src_file):
        assert sha256_file(src_file) == sha256_file(src_file)

    def test_sha256_file_changes_on_edit(self, src_file):
        h1 = sha256_file(src_file)
        src_file.write_text("# Different content")
        h2 = sha256_file(src_file)
        assert h1 != h2

    def test_sha256_dir_deterministic(self, src_dir):
        assert sha256_dir(src_dir) == sha256_dir(src_dir)

    def test_sha256_dir_changes_on_edit(self, src_dir):
        h1 = sha256_dir(src_dir)
        (src_dir / "a.py").write_text("x = 999")
        h2 = sha256_dir(src_dir)
        assert h1 != h2

    def test_compute_hash_dispatches(self, src_file, src_dir):
        assert len(compute_hash(src_file)) == 64  # hex SHA-256
        assert len(compute_hash(src_dir)) == 64

    def test_hash_file_alias(self, src_file):
        assert hash_file(src_file) == sha256_file(src_file)


# ---------------------------------------------------------------------------
# check_sources
# ---------------------------------------------------------------------------

class TestCheckSources:
    def test_portable_new_source_uses_source_id(self, portable_repo):
        root, config = portable_repo
        source = root / "sources" / "a.md"
        source.write_text("a", encoding="utf-8")

        result = check_sources(config.vault, [source], portable=config)

        assert result["new"] == ["sources/a.md"]

    def test_portable_selected_deleted_source_is_missing_once(self, portable_repo):
        root, config = portable_repo
        source = root / "sources" / "a.md"
        source.write_text("a", encoding="utf-8")
        update_source(config.vault, source, portable=config)
        source.unlink()

        result = check_sources(config.vault, [source], portable=config)

        assert result["missing"] == [str(source)]

    def test_new_source(self, vault, src_file):
        result = check_sources(vault, [src_file])
        assert str(src_file) in result["new"]
        assert result["modified"] == []
        assert result["unchanged"] == []

    def test_unchanged_after_update(self, vault, src_file):
        update_source(vault, src_file)
        result = check_sources(vault, [src_file])
        assert str(src_file) in result["unchanged"]
        assert result["new"] == []
        assert result["modified"] == []

    def test_modified_after_content_change(self, vault, src_file):
        update_source(vault, src_file)
        src_file.write_text("# Changed content")
        result = check_sources(vault, [src_file])
        assert str(src_file) in result["modified"]

    def test_missing_path(self, vault, tmp_path):
        ghost = tmp_path / "ghost.md"
        result = check_sources(vault, [ghost])
        assert str(ghost) in result["missing"]

    def test_empty_source_list(self, vault):
        result = check_sources(vault, [])
        assert result == {"new": [], "modified": [], "unchanged": [], "missing": []}

    def test_multiple_sources(self, vault, src_file, src_dir):
        update_source(vault, src_file)
        result = check_sources(vault, [src_file, src_dir])
        assert str(src_file) in result["unchanged"]
        assert str(src_dir) in result["new"]

    def test_timestamp_irrelevant(self, vault, src_file):
        # Touch the file (change mtime) without changing content — still unchanged
        update_source(vault, src_file)
        src_file.touch()
        result = check_sources(vault, [src_file])
        assert str(src_file) in result["unchanged"]

    def _write_relative_manifest(self, vault, rel_key, content_hash):
        """Write a manifest whose source key is stored vault-relative."""
        _manifest_path(vault).write_text(
            json.dumps(
                {"sources": {rel_key: {"content_hash": content_hash, "last_ingested": "2026-07-14"}}}
            ),
            encoding="utf-8",
        )

    def test_relative_manifest_key_unchanged_for_abs_path(self, vault):
        # Manifest stores a vault-relative key; caller passes the absolute path.
        src = vault / "_raw" / "articles" / "foo.md"
        src.parent.mkdir(parents=True)
        src.write_text("body", encoding="utf-8")
        self._write_relative_manifest(vault, "_raw/articles/foo.md", sha256_file(src))
        result = check_sources(vault, [src])
        assert str(src) in result["unchanged"]
        assert result["new"] == []
        assert result["missing"] == []

    def test_relative_manifest_key_not_falsely_missing(self, vault):
        # A relative key whose file exists under the vault must not be flagged missing,
        # even when CWD != vault root.
        src = vault / "_raw" / "articles" / "foo.md"
        src.parent.mkdir(parents=True)
        src.write_text("body", encoding="utf-8")
        self._write_relative_manifest(vault, "_raw/articles/foo.md", sha256_file(src))
        result = check_sources(vault, [])
        assert "_raw/articles/foo.md" not in result["missing"]

    def test_relative_manifest_key_modified(self, vault):
        src = vault / "_raw" / "articles" / "foo.md"
        src.parent.mkdir(parents=True)
        src.write_text("body", encoding="utf-8")
        self._write_relative_manifest(vault, "_raw/articles/foo.md", "stale-hash")
        result = check_sources(vault, [src])
        assert str(src) in result["modified"]

    def test_relative_manifest_key_genuinely_missing(self, vault):
        # A relative key with no file on disk is still reported missing.
        self._write_relative_manifest(vault, "_raw/articles/gone.md", "abc")
        result = check_sources(vault, [])
        assert "_raw/articles/gone.md" in result["missing"]


# ---------------------------------------------------------------------------
# update_source / manifest
# ---------------------------------------------------------------------------

class TestUpdateSource:
    def test_portable_update_returns_bare_hash_and_marks_unchanged(self, portable_repo):
        root, config = portable_repo
        source = root / "sources" / "a.md"
        source.write_text("a", encoding="utf-8")

        content_hash = update_source(
            config.vault,
            source,
            ["concepts/a.md"],
            portable=config,
        )

        assert content_hash == compute_hash(source)
        assert check_sources(config.vault, [source], portable=config)["unchanged"] == [
            "sources/a.md"
        ]
        shard = config.vault / ".manifest" / "sources" / "a.md.json"
        assert json.loads(shard.read_text(encoding="utf-8"))["pages"] == [
            "concepts/a.md"
        ]

    def test_writes_manifest(self, vault, src_file):
        update_source(vault, src_file)
        assert _manifest_path(vault).exists()

    def test_records_correct_hash(self, vault, src_file):
        h = update_source(vault, src_file)
        assert h == sha256_file(src_file)
        sources = _load_manifest(vault)
        assert sources[str(src_file)]["content_hash"] == h

    def test_records_pages_produced(self, vault, src_file):
        update_source(vault, src_file, pages_produced=["concepts/foo.md", "entities/bar.md"])
        sources = _load_manifest(vault)
        assert sources[str(src_file)]["pages_produced"] == ["concepts/foo.md", "entities/bar.md"]

    def test_records_last_ingested_timestamp(self, vault, src_file):
        update_source(vault, src_file)
        sources = _load_manifest(vault)
        assert "last_ingested" in sources[str(src_file)]

    def test_update_overwrites_old_hash(self, vault, src_file):
        update_source(vault, src_file)
        src_file.write_text("new content")
        h2 = update_source(vault, src_file)
        sources = _load_manifest(vault)
        assert sources[str(src_file)]["content_hash"] == h2

    def test_preserves_other_manifest_entries(self, vault, src_file, src_dir):
        update_source(vault, src_file)
        update_source(vault, src_dir)
        sources = _load_manifest(vault)
        assert str(src_file) in sources
        assert str(src_dir) in sources


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCacheCLI:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", *args],
            capture_output=True, text=True,
        )

    def _run_from(self, cwd: Path, home: Path, *args: str):
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)
        return subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
        )

    def test_cache_hash_file(self, src_file):
        proc = self._run("cache-hash", str(src_file))
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["sha256"] == sha256_file(src_file)

    def test_cache_hash_missing_exits_nonzero(self, tmp_path):
        proc = self._run("cache-hash", str(tmp_path / "nope.md"))
        assert proc.returncode != 0

    def test_cache_check_new(self, vault, src_file):
        proc = self._run("cache-check", str(vault), str(src_file))
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert str(src_file) in data["new"]

    def test_cache_check_pretty(self, vault, src_file):
        proc = self._run("cache-check", "--pretty", str(vault), str(src_file))
        assert proc.returncode == 0
        assert "\n  " in proc.stdout

    def test_cache_update_then_check_unchanged(self, vault, src_file):
        self._run("cache-update", str(vault), str(src_file))
        proc = self._run("cache-check", str(vault), str(src_file))
        data = json.loads(proc.stdout)
        assert str(src_file) in data["unchanged"]

    def test_cache_update_with_pages(self, vault, src_file):
        proc = self._run("cache-update", str(vault), str(src_file),
                         "--pages", "concepts/foo.md", "entities/bar.md")
        assert proc.returncode == 0
        sources = _load_manifest(vault)
        assert sources[str(src_file)]["pages_produced"] == ["concepts/foo.md", "entities/bar.md"]

    def test_portable_context_is_resolved_from_cwd(
        self, portable_repo, monkeypatch, tmp_path
    ):
        from obsidian_wiki.cli import _portable_for_vault

        root, config = portable_repo
        monkeypatch.chdir(config.sources[0])
        assert _portable_for_vault(config.vault) == config

        other_vault = root / "other-vault"
        other_vault.mkdir()
        assert _portable_for_vault(other_vault) is None

        monkeypatch.chdir(tmp_path)
        assert _portable_for_vault(config.vault) is None

    def test_invalid_cwd_portable_config_blocks_cache_update(self, portable_repo):
        root, config = portable_repo
        source = root / "sources" / "a.md"
        source.write_text("a", encoding="utf-8")
        config.path.write_text(
            config.path.read_text(encoding="utf-8").replace(
                IMPLEMENTATION_ID, "wrong/implementation"
            ),
            encoding="utf-8",
        )
        marker = (config.vault / ".manifest.json").read_text(encoding="utf-8")
        home = root / "home"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "obsidian_wiki.cli",
                "cache-update",
                str(config.vault),
                str(source),
            ],
            capture_output=True,
            text=True,
            cwd=config.sources[0],
            env=env,
        )

        assert proc.returncode == 1
        assert "implementation" in proc.stderr
        assert (config.vault / ".manifest.json").read_text(encoding="utf-8") == marker
        assert not (config.vault / ".manifest").exists()

    @pytest.mark.parametrize("command", ["cache-check", "cache-update"])
    def test_v2_cache_commands_require_portable_cwd_without_mutation(
        self, portable_repo, tmp_path, command
    ):
        root, config = portable_repo
        source = root / "sources" / "a.md"
        source.write_text("a", encoding="utf-8")
        update_source(config.vault, source, portable=config)
        before = {
            path.relative_to(config.vault).as_posix(): path.read_bytes()
            for path in sorted(config.vault.rglob("*"))
            if path.is_file()
            and (
                path == config.vault / ".manifest.json"
                or config.vault / ".manifest" in path.parents
            )
        }
        outside = tmp_path / "outside"
        outside.mkdir()

        proc = self._run_from(
            outside,
            tmp_path / "home",
            command,
            str(config.vault),
            str(source),
        )

        assert proc.returncode == 1
        assert proc.stderr.strip().endswith(
            "error: manifest v2 is portable-only; "
            "run this command inside the portable repository"
        )
        assert "Traceback" not in proc.stderr
        assert {
            path.relative_to(config.vault).as_posix(): path.read_bytes()
            for path in sorted(config.vault.rglob("*"))
            if path.is_file()
            and (
                path == config.vault / ".manifest.json"
                or config.vault / ".manifest" in path.parents
            )
        } == before

    @pytest.mark.parametrize("command", ["cache-check", "cache-update"])
    def test_portable_manifest_errors_exit_concisely(
        self, portable_repo, tmp_path, command
    ):
        root, config = portable_repo
        source = root / "sources" / "a.md"
        source.write_text("a", encoding="utf-8")
        (config.vault / ".manifest.json").write_text("{}\n", encoding="utf-8")

        proc = self._run_from(
            config.sources[0],
            tmp_path / "home",
            command,
            str(config.vault),
            str(source),
        )

        assert proc.returncode == 1
        assert "invalid manifest v2 marker" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_cache_update_without_portable_config_keeps_v1(self, tmp_path):
        cwd = tmp_path / "work"
        home = tmp_path / "home"
        vault = tmp_path / "vault"
        source = tmp_path / "source.md"
        cwd.mkdir()
        home.mkdir()
        vault.mkdir()
        source.write_text("source", encoding="utf-8")
        env = os.environ.copy()
        env["HOME"] = str(home)

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "obsidian_wiki.cli",
                "cache-update",
                str(vault),
                str(source),
            ],
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
        )

        assert proc.returncode == 0, proc.stderr
        assert str(source) in json.loads((vault / ".manifest.json").read_text())["sources"]

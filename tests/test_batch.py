"""Tests for the batch planning module."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.batch import (
    _classify,
    _make_batches,
    discover_sources,
    plan_batches,
)
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable_manifest import ManifestError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def source_dir(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
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


def _write(path: Path, content: str = "x", size: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if size is not None:
        path.write_bytes(b"x" * size)
    else:
        path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------

class TestClassify:
    def test_markdown(self, tmp_path):
        assert _classify(tmp_path / "foo.md") == "text"

    def test_pdf(self, tmp_path):
        assert _classify(tmp_path / "paper.pdf") == "pdf"

    def test_image(self, tmp_path):
        assert _classify(tmp_path / "shot.png") == "image"

    def test_python_code(self, tmp_path):
        assert _classify(tmp_path / "main.py") == "code"

    def test_skip_binary(self, tmp_path):
        assert _classify(tmp_path / "lib.so") == "skip"

    def test_skip_lockfile(self, tmp_path):
        assert _classify(tmp_path / "poetry.lock") == "skip"


# ---------------------------------------------------------------------------
# discover_sources
# ---------------------------------------------------------------------------

class TestDiscoverSources:
    def test_finds_markdown(self, source_dir, vault):
        _write(source_dir / "a.md")
        _write(source_dir / "b.md")
        files = discover_sources(source_dir, vault=vault)
        kinds = [f["kind"] for f in files]
        assert kinds.count("text") == 2

    def test_excludes_code_by_default(self, source_dir, vault):
        _write(source_dir / "main.py")
        _write(source_dir / "notes.md")
        files = discover_sources(source_dir, vault=vault)
        assert all(f["kind"] != "code" for f in files)

    def test_includes_code_when_flag_set(self, source_dir, vault):
        _write(source_dir / "main.py")
        files = discover_sources(source_dir, vault=vault, include_code=True)
        assert any(f["kind"] == "code" for f in files)

    def test_skips_binary(self, source_dir, vault):
        _write(source_dir / "lib.so")
        files = discover_sources(source_dir, vault=vault)
        assert files == []

    def test_skips_hidden_dirs(self, source_dir, vault):
        _write(source_dir / ".git" / "config", "gitconfig")
        _write(source_dir / "doc.md")
        files = discover_sources(source_dir, vault=vault)
        paths = [f["path"] for f in files]
        assert not any(".git" in p for p in paths)

    def test_skips_node_modules(self, source_dir, vault):
        _write(source_dir / "node_modules" / "foo.md")
        _write(source_dir / "readme.md")
        files = discover_sources(source_dir, vault=vault)
        assert len(files) == 1

    def test_returns_size(self, source_dir, vault):
        p = _write(source_dir / "doc.md", size=512)
        files = discover_sources(source_dir, vault=vault)
        assert files[0]["size_bytes"] == 512


# ---------------------------------------------------------------------------
# discover_sources — git repo awareness
# ---------------------------------------------------------------------------

def _git(repo_dir: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo_dir), *args], check=True,
                    capture_output=True)


@pytest.fixture
def git_repo(source_dir):
    _git(source_dir, "init", "-q")
    _git(source_dir, "config", "user.email", "test@example.com")
    _git(source_dir, "config", "user.name", "Test")
    return source_dir


class TestDiscoverSourcesGitAware:
    def test_respects_gitignore_for_custom_dirs(self, git_repo, vault):
        # A dir name that isn't in the hardcoded SKIP_DIRS list, but is
        # ignored by this repo's own .gitignore.
        _write(git_repo / ".gitignore", "vendor_stuff/\n")
        _write(git_repo / "vendor_stuff" / "generated.md")
        _write(git_repo / "readme.md")
        files = discover_sources(git_repo, vault=vault)
        paths = [f["path"] for f in files]
        assert not any("vendor_stuff" in p for p in paths)
        assert any("readme.md" in p for p in paths)

    def test_includes_untracked_uncommitted_files(self, git_repo, vault):
        _write(git_repo / "draft.md")
        files = discover_sources(git_repo, vault=vault)
        assert any("draft.md" in f["path"] for f in files)

    def test_still_skips_dot_git_dir(self, git_repo, vault):
        _write(git_repo / "readme.md")
        files = discover_sources(git_repo, vault=vault)
        paths = [f["path"] for f in files]
        assert not any(".git" in p for p in paths)

    def test_non_git_dir_falls_back_to_walk(self, source_dir, vault):
        _write(source_dir / "doc.md")
        files = discover_sources(source_dir, vault=vault)
        assert len(files) == 1


# ---------------------------------------------------------------------------
# _make_batches
# ---------------------------------------------------------------------------

class TestMakeBatches:
    def _files(self, sizes: list[int]) -> list[dict]:
        return [{"path": f"f{i}.md", "kind": "text", "size_bytes": s}
                for i, s in enumerate(sizes)]

    def test_single_batch_small(self):
        files = self._files([100, 200, 300])
        batches = _make_batches(files, max_batch_bytes=10_000, max_batch_files=20)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_splits_on_byte_limit(self):
        files = self._files([600_000, 600_000, 600_000])  # each 0.6 MB, limit 1 MB
        batches = _make_batches(files, max_batch_bytes=1_000_000, max_batch_files=20)
        assert len(batches) >= 2

    def test_splits_on_file_count(self):
        files = self._files([10] * 25)
        batches = _make_batches(files, max_batch_bytes=10_000_000, max_batch_files=10)
        assert len(batches) == 3  # 10 + 10 + 5

    def test_empty_input(self):
        assert _make_batches([], max_batch_bytes=1_000_000, max_batch_files=20) == []

    def test_all_files_present_in_batches(self):
        files = self._files([100] * 47)
        batches = _make_batches(files, max_batch_bytes=10_000_000, max_batch_files=10)
        total = sum(len(b) for b in batches)
        assert total == 47


# ---------------------------------------------------------------------------
# plan_batches
# ---------------------------------------------------------------------------

class TestPlanBatches:
    def test_portable_manifest_skips_unchanged_source(self, portable_repo):
        root, config = portable_repo
        source = root / "sources" / "a.md"
        source.write_text("a", encoding="utf-8")
        from obsidian_wiki.cache import update_source

        update_source(config.vault, source, pages_produced=[], portable=config)

        result = plan_batches(config.sources[0], config.vault, portable=config)

        assert result["stats"]["skipped_unchanged"] == 1
        assert result["stats"]["to_ingest"] == 0

    def test_portable_corrupted_manifest_error_propagates(self, portable_repo):
        root, config = portable_repo
        (root / "sources" / "a.md").write_text("a", encoding="utf-8")
        (config.vault / ".manifest.json").write_text("{}\n", encoding="utf-8")

        with pytest.raises(ManifestError, match="invalid manifest v2 marker"):
            plan_batches(config.sources[0], config.vault, portable=config)

    def test_portable_out_of_config_source_error_propagates(self, portable_repo):
        root, config = portable_repo
        outside = root / "outside"
        outside.mkdir()
        (outside / "a.md").write_text("a", encoding="utf-8")

        with pytest.raises(ManifestError, match="outside the configured source root"):
            plan_batches(outside, config.vault, portable=config)

    def test_legacy_cache_error_keeps_permissive_fallback(
        self, source_dir, vault, monkeypatch
    ):
        source = _write(source_dir / "a.md")
        import obsidian_wiki.cache as cache

        def fail_cache_check(*args, **kwargs):
            raise RuntimeError("legacy cache unavailable")

        monkeypatch.setattr(cache, "check_sources", fail_cache_check)

        result = plan_batches(source_dir, vault)

        assert result["stats"]["skipped_unchanged"] == 0
        assert result["batches"][0]["files"] == [str(source)]

    def test_returns_required_keys(self, source_dir, vault):
        _write(source_dir / "a.md")
        result = plan_batches(source_dir, vault)
        assert "batches" in result
        assert "stats" in result
        assert "merge_hint" in result

    def test_empty_dir_gives_zero_batches(self, source_dir, vault):
        result = plan_batches(source_dir, vault)
        assert result["stats"]["batch_count"] == 0

    def test_single_file_single_batch(self, source_dir, vault):
        _write(source_dir / "doc.md")
        result = plan_batches(source_dir, vault)
        assert result["stats"]["batch_count"] == 1
        assert len(result["batches"][0]["files"]) == 1

    def test_batch_kinds_tallied(self, source_dir, vault):
        _write(source_dir / "a.md")
        _write(source_dir / "b.md")
        result = plan_batches(source_dir, vault)
        kinds = result["batches"][0]["kinds"]
        assert kinds.get("text", 0) == 2

    def test_skips_unchanged_after_cache_update(self, source_dir, vault):
        f = _write(source_dir / "doc.md", "some content")
        # Mark it as ingested
        from obsidian_wiki.cache import update_source
        update_source(vault, f)
        result = plan_batches(source_dir, vault)
        assert result["stats"]["skipped_unchanged"] == 1
        assert result["stats"]["to_ingest"] == 0

    def test_no_cache_flag_includes_unchanged(self, source_dir, vault):
        f = _write(source_dir / "doc.md", "some content")
        from obsidian_wiki.cache import update_source
        update_source(vault, f)
        result = plan_batches(source_dir, vault, skip_unchanged=False)
        assert result["stats"]["to_ingest"] == 1

    def test_batch_total_bytes_correct(self, source_dir, vault):
        _write(source_dir / "a.md", size=1000)
        _write(source_dir / "b.md", size=2000)
        result = plan_batches(source_dir, vault, skip_unchanged=False)
        total = result["batches"][0]["total_bytes"]
        assert total == 3000

    def test_json_serialisable(self, source_dir, vault):
        _write(source_dir / "doc.md")
        result = plan_batches(source_dir, vault)
        json.dumps(result)  # must not raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestBatchPlanCLI:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", *args],
            capture_output=True, text=True,
        )

    def test_outputs_json(self, source_dir, vault):
        (source_dir / "doc.md").write_text("content")
        proc = self._run("batch-plan", str(vault), str(source_dir))
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert "batches" in data

    def test_pretty_flag(self, source_dir, vault):
        (source_dir / "doc.md").write_text("content")
        proc = self._run("batch-plan", str(vault), str(source_dir), "--pretty")
        assert proc.returncode == 0
        assert "\n  " in proc.stdout

    def test_missing_source_dir_exits_nonzero(self, vault, tmp_path):
        proc = self._run("batch-plan", str(vault), str(tmp_path / "nope"))
        assert proc.returncode != 0

    def test_max_files_respected(self, source_dir, vault):
        for i in range(5):
            (source_dir / f"doc{i}.md").write_text(f"content {i}")
        proc = self._run("batch-plan", str(vault), str(source_dir), "--max-files", "2")
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        # 5 files with max 2 per batch → 3 batches
        assert data["stats"]["batch_count"] == 3

    def test_invalid_cwd_portable_config_blocks_batch_plan(self, portable_repo):
        root, config = portable_repo
        source = root / "sources" / "a.md"
        source.write_text("a", encoding="utf-8")
        config.path.write_text(
            config.path.read_text(encoding="utf-8").replace(
                IMPLEMENTATION_ID, "wrong/implementation"
            ),
            encoding="utf-8",
        )
        home = root / "home"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "obsidian_wiki.cli",
                "batch-plan",
                str(config.vault),
                str(config.sources[0]),
            ],
            capture_output=True,
            text=True,
            cwd=config.sources[0],
            env=env,
        )

        assert proc.returncode == 1
        assert "implementation" in proc.stderr

    def _run_portable(self, root, config, source_dir):
        home = root / "home"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "obsidian_wiki.cli",
                "batch-plan",
                str(config.vault),
                str(source_dir),
            ],
            capture_output=True,
            text=True,
            cwd=config.sources[0],
            env=env,
        )

    def test_corrupted_portable_manifest_exits_concisely(self, portable_repo):
        root, config = portable_repo
        (root / "sources" / "a.md").write_text("a", encoding="utf-8")
        (config.vault / ".manifest.json").write_text("{}\n", encoding="utf-8")

        proc = self._run_portable(root, config, config.sources[0])

        assert proc.returncode == 1
        assert "invalid manifest v2 marker" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_out_of_config_portable_source_exits_concisely(self, portable_repo):
        root, config = portable_repo
        outside = root / "outside"
        outside.mkdir()
        (outside / "a.md").write_text("a", encoding="utf-8")

        proc = self._run_portable(root, config, outside)

        assert proc.returncode == 1
        assert "outside the configured source root" in proc.stderr
        assert "Traceback" not in proc.stderr

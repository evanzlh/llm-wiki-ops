"""Tests for portable batch planning."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.batch import _classify, _make_batches, discover_sources, plan_batches
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable_manifest import ManifestError, ShardedManifest


@pytest.fixture
def portable_repo(tmp_path: Path):
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
    if size is None:
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(b"x" * size)
    return path


@pytest.mark.parametrize(
    ("name", "kind"),
    [("a.md", "text"), ("a.pdf", "pdf"), ("a.png", "image"), ("a.py", "code")],
)
def test_classify_supported_files(tmp_path, name, kind):
    assert _classify(tmp_path / name) == kind


def test_discover_sources_filters_code_and_binary(portable_repo):
    _root, config = portable_repo
    _write(config.sources[0] / "note.md")
    _write(config.sources[0] / "tool.py")
    _write(config.sources[0] / "binary.so")

    assert [Path(item["path"]).name for item in discover_sources(config.sources[0])] == [
        "note.md"
    ]


def test_discover_sources_includes_code_when_requested(portable_repo):
    _root, config = portable_repo
    _write(config.sources[0] / "tool.py")

    files = discover_sources(config.sources[0], include_code=True)
    assert [Path(item["path"]).name for item in files] == ["tool.py"]


def test_discover_sources_skips_hidden_directories(portable_repo):
    _root, config = portable_repo
    _write(config.sources[0] / ".hidden" / "secret.md")
    _write(config.sources[0] / "visible.md")

    assert [Path(item["path"]).name for item in discover_sources(config.sources[0])] == [
        "visible.md"
    ]


@pytest.mark.parametrize("name", ["_archives", "_raw", "_readouts", "_staging"])
def test_discover_sources_does_not_hide_personal_artifact_names(
    portable_repo, name: str
) -> None:
    _root, config = portable_repo
    source = _write(config.sources[0] / name / "note.md")

    assert [item["path"] for item in discover_sources(config.sources[0])] == [
        str(source)
    ]


def test_discover_sources_reports_size(portable_repo):
    _root, config = portable_repo
    _write(config.sources[0] / "note.md", size=512)

    assert discover_sources(config.sources[0])[0]["size_bytes"] == 512


def test_discover_sources_respects_gitignore(portable_repo):
    _root, config = portable_repo
    subprocess.run(
        ["git", "-C", str(config.sources[0]), "init", "-q"],
        check=True,
        capture_output=True,
    )
    _write(config.sources[0] / ".gitignore", "ignored/\n")
    _write(config.sources[0] / "ignored" / "generated.md")
    _write(config.sources[0] / "visible.md")

    paths = [item["path"] for item in discover_sources(config.sources[0])]
    assert not any("ignored" in path for path in paths)
    assert any("visible.md" in path for path in paths)


def test_make_batches_respects_file_limit():
    files = [
        {"path": f"f{i}.md", "kind": "text", "size_bytes": 10}
        for i in range(5)
    ]
    batches = _make_batches(files, max_batch_bytes=1_000, max_batch_files=2)
    assert [len(batch) for batch in batches] == [2, 2, 1]


def test_make_batches_respects_byte_limit():
    files = [
        {"path": f"f{i}.md", "kind": "text", "size_bytes": 600_000}
        for i in range(3)
    ]
    batches = _make_batches(
        files,
        max_batch_bytes=1_000_000,
        max_batch_files=20,
    )
    assert len(batches) == 3


def test_make_batches_handles_empty_input():
    assert _make_batches([], max_batch_bytes=1_000, max_batch_files=2) == []


def test_plan_batches_uses_config_and_reports_source_dir(portable_repo):
    root, config = portable_repo
    source = _write(root / "sources" / "note.md", "note")

    result = plan_batches(config.sources[0], config, skip_unchanged=False)

    assert result["source_dir"] == str(root / "sources")
    assert result["batches"][0]["files"] == [str(source)]


def test_plan_batches_assigns_reviewed_completion_to_parent(portable_repo):
    _root, config = portable_repo

    result = plan_batches(config.sources[0], config, skip_unchanged=False)

    assert result["merge_hint"] == (
        "Dispatch each batch for analysis and let the parent wiki-ingest workflow "
        "own reviewed transaction completion."
    )


def test_plan_batches_skips_unchanged_shard(portable_repo):
    root, config = portable_repo
    source = _write(root / "sources" / "note.md", "note")
    ShardedManifest(config).upsert(source, pages=[])

    result = plan_batches(config.sources[0], config)

    assert result["stats"]["skipped_unchanged"] == 1
    assert result["stats"]["to_ingest"] == 0


def test_plan_batches_propagates_manifest_errors(portable_repo):
    root, config = portable_repo
    _write(root / "sources" / "note.md")
    (config.vault / ".manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ManifestError, match="invalid manifest v2 marker"):
        plan_batches(config.sources[0], config)


def test_plan_batches_propagates_malformed_shard(portable_repo):
    root, config = portable_repo
    _write(root / "sources" / "note.md")
    shard = root / "wiki" / ".manifest" / "sources" / "note.md.json"
    _write(shard, "{}\n")

    with pytest.raises(ManifestError, match="invalid fields|source_id"):
        plan_batches(config.sources[0], config)


def test_plan_batches_validates_files_even_without_cache(portable_repo):
    root, config = portable_repo
    external = _write(root / "external.md")
    try:
        (config.sources[0] / "linked.md").symlink_to(external)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(ManifestError, match="outside the configured source root"):
        plan_batches(config.sources[0], config, skip_unchanged=False)


def test_plan_batches_no_cache_rejects_internal_terminal_symlink(portable_repo):
    root, config = portable_repo
    target = _write(root / "sources" / "target.md")
    alias = root / "sources" / "alias.md"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(ManifestError, match="single-link ordinary file"):
        plan_batches(config.sources[0], config, skip_unchanged=False)


def test_plan_batches_no_cache_rejects_hardlinked_source(portable_repo, tmp_path):
    root, config = portable_repo
    external = _write(tmp_path / "external.md")
    source = root / "sources" / "linked.md"
    try:
        os.link(external, source)
    except OSError:
        pytest.skip("hard links are unavailable")

    with pytest.raises(ManifestError, match="single-link ordinary file"):
        plan_batches(config.sources[0], config, skip_unchanged=False)


def test_plan_batches_no_cache_rejects_fifo_source(portable_repo):
    root, config = portable_repo
    source = root / "sources" / "pipe.md"
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable")
    try:
        os.mkfifo(source)
    except OSError:
        pytest.skip("FIFOs are unavailable")

    with pytest.raises(ManifestError, match="single-link ordinary file"):
        plan_batches(config.sources[0], config, skip_unchanged=False)


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


def test_batch_plan_uses_first_configured_source_without_positionals(
    portable_repo, tmp_path
):
    root, _config = portable_repo
    _write(root / "sources" / "note.md")

    proc = _run(root, tmp_path / "home", "batch-plan", "--no-cache")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["source_dir"] == str(root / "sources")
    assert payload["stats"]["total_files"] == 1


def test_batch_plan_rejects_positional_paths(portable_repo, tmp_path):
    root, config = portable_repo
    proc = _run(
        root,
        tmp_path / "home",
        "batch-plan",
        str(config.vault),
        str(config.sources[0]),
    )
    assert proc.returncode == 2


def test_batch_plan_no_cache_reports_internal_symlink_concisely(
    portable_repo, tmp_path
):
    root, _config = portable_repo
    target = _write(root / "sources" / "target.md")
    alias = root / "sources" / "alias.md"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")

    proc = _run(root, tmp_path / "home", "batch-plan", "--no-cache")

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "single-link ordinary file" in proc.stderr
    assert "Traceback" not in proc.stderr

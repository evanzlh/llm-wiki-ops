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


def test_make_batches_respects_file_limit():
    files = [
        {"path": f"f{i}.md", "kind": "text", "size_bytes": 10}
        for i in range(5)
    ]
    batches = _make_batches(files, max_batch_bytes=1_000, max_batch_files=2)
    assert [len(batch) for batch in batches] == [2, 2, 1]


def test_plan_batches_uses_config_and_reports_source_dir(portable_repo):
    root, config = portable_repo
    source = _write(root / "sources" / "note.md", "note")

    result = plan_batches(config.sources[0], config, skip_unchanged=False)

    assert result["source_dir"] == str(root / "sources")
    assert result["batches"][0]["files"] == [str(source)]


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


def test_plan_batches_validates_files_even_without_cache(portable_repo):
    root, config = portable_repo
    external = _write(root / "external.md")
    try:
        (config.sources[0] / "linked.md").symlink_to(external)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(ManifestError, match="outside the configured source root"):
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

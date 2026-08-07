from __future__ import annotations

import json
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable_manifest import ManifestError, ShardedManifest


def make_repo(tmp_path: Path):
    root = tmp_path / "knowledge"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources" / "design").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / ".skills").mkdir()
    (root / ".obsidian-wiki" / "config.toml").write_text(
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
        root / ".obsidian-wiki" / "config.toml",
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    return root, config


def test_source_id_is_repo_relative_posix_path(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "design" / "architecture.md"
    source.write_text("body", encoding="utf-8")
    store = ShardedManifest(config)
    assert store.source_id(source) == "sources/design/architecture.md"
    assert store.entry_path("sources/design/architecture.md") == (
        root / "wiki" / ".manifest" / "sources" / "design" / "architecture.md.json"
    )


def test_source_outside_configured_root_is_rejected(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    external = root / "notes.md"
    external.write_text("body", encoding="utf-8")
    with pytest.raises(ManifestError, match="configured source root"):
        ShardedManifest(config).source_id(external)


def test_source_id_rejects_absolute_and_traversal_strings(tmp_path: Path) -> None:
    _, config = make_repo(tmp_path)
    store = ShardedManifest(config)
    for source_id in (
        "/tmp/file.md",
        "C:/tmp/file.md",
        "sources\\file.md",
        "sources/../../file.md",
        "../sources/file.md",
    ):
        with pytest.raises(ManifestError, match="Source ID"):
            store.entry_path(source_id)


def test_upsert_writes_one_canonical_shard(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "design" / "architecture.md"
    source.write_text("body", encoding="utf-8")
    store = ShardedManifest(config)
    entry = store.upsert(
        source,
        pages=["references/layout.md", "concepts/portable-repo.md"],
        compiled_at="2026-08-07T07:30:00Z",
    )
    payload = json.loads(store.entry_path(entry.source_id).read_text(encoding="utf-8"))
    from obsidian_wiki.cache import compute_hash

    assert payload == {
        "compiled_at": "2026-08-07T07:30:00Z",
        "content_hash": f"sha256:{compute_hash(source)}",
        "pages": ["concepts/portable-repo.md", "references/layout.md"],
        "source_id": "sources/design/architecture.md",
    }
    assert store.entry_path(entry.source_id).read_text(encoding="utf-8").endswith("\n")


def test_unrelated_sources_use_unrelated_shards(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    first = root / "sources" / "a.md"
    second = root / "sources" / "b.md"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(first, pages=["concepts/a.md"], compiled_at="2026-08-07T00:00:00Z")
    store.upsert(second, pages=["concepts/b.md"], compiled_at="2026-08-07T00:00:01Z")
    assert store.entry_path("sources/a.md") != store.entry_path("sources/b.md")
    assert [entry.source_id for entry in store.iter_entries()] == ["sources/a.md", "sources/b.md"]


def test_status_uses_hash_not_mtime(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "a.md"
    source.write_text("a", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, pages=[], compiled_at="2026-08-07T00:00:00Z")
    source.touch()
    assert store.status()["unchanged"] == ["sources/a.md"]
    source.write_text("changed", encoding="utf-8")
    assert store.status()["modified"] == ["sources/a.md"]


def test_status_reports_uncompiled_and_orphaned(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    tracked = root / "sources" / "tracked.md"
    new = root / "sources" / "new.md"
    tracked.write_text("tracked", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(tracked, pages=[], compiled_at="2026-08-07T00:00:00Z")
    tracked.unlink()
    status = store.status()
    assert status["new"] == ["sources/new.md"]
    assert status["missing"] == ["sources/tracked.md"]

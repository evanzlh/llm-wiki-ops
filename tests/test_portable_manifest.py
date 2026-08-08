from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import portable_manifest as portable_manifest_module
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
        "sources",
        "sources/./file.md",
        "sources//file.md",
        "sources/file.md/",
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


def test_upsert_syncs_temporary_shard_and_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/architecture.md"
    source.write_text("body", encoding="utf-8")
    original_fsync = portable_manifest_module.os.fsync
    synced_kinds: list[str] = []

    def record_sync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        synced_kinds.append("directory" if stat.S_ISDIR(mode) else "file")
        original_fsync(descriptor)

    monkeypatch.setattr(portable_manifest_module.os, "fsync", record_sync)

    ShardedManifest(config).upsert(source)

    assert "file" in synced_kinds
    assert "directory" in synced_kinds


def test_upsert_syncs_each_new_shard_directory_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/architecture.md"
    source.write_text("body", encoding="utf-8")
    store = ShardedManifest(config)
    synced: list[Path] = []
    original_sync = store._fsync_directory

    def record_sync(path: Path) -> None:
        synced.append(path)
        original_sync(path)

    monkeypatch.setattr(store, "_fsync_directory", record_sync)

    store.upsert(source)

    assert config.vault in synced
    assert config.vault / ".manifest" in synced
    assert config.vault / ".manifest/sources" in synced
    assert config.vault / ".manifest/sources/design" in synced


@pytest.mark.parametrize("failure_kind", ["file", "directory"])
def test_upsert_reports_durability_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/architecture.md"
    source.write_text("body", encoding="utf-8")
    original_fsync = portable_manifest_module.os.fsync

    def fail_selected_sync(descriptor: int) -> None:
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if (failure_kind == "directory") == is_directory:
            raise OSError(f"{failure_kind} sync failed")
        original_fsync(descriptor)

    monkeypatch.setattr(portable_manifest_module.os, "fsync", fail_selected_sync)

    with pytest.raises(ManifestError, match="sync|write manifest shard"):
        ShardedManifest(config).upsert(source)


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
    assert [entry.source_id for entry in store.iter_entries()] == [
        "sources/a.md",
        "sources/b.md",
    ]


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


def _write_shard(root: Path, payload: object, name: str = "a.md.json") -> Path:
    path = root / "wiki" / ".manifest" / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def _valid_shard() -> dict[str, object]:
    return {
        "compiled_at": "2026-08-07T00:00:00Z",
        "content_hash": "sha256:" + "0" * 64,
        "pages": ["concepts/a.md"],
        "source_id": "sources/a.md",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content_hash", "0" * 64),
        ("content_hash", "sha256:" + "A" * 64),
        ("content_hash", "sha256:short"),
        ("compiled_at", 123),
    ],
)
def test_load_rejects_malformed_scalar_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    root, config = make_repo(tmp_path)
    payload = _valid_shard()
    payload[field] = value
    _write_shard(root, payload)

    with pytest.raises(ManifestError, match=field):
        ShardedManifest(config).iter_entries()


@pytest.mark.parametrize(
    "pages",
    [
        ["concepts/../a.md"],
        ["/concepts/a.md"],
        ["C:/concepts/a.md"],
        ["concepts\\a.md"],
        ["concepts/a.md", "concepts/a.md"],
        ["references/b.md", "concepts/a.md"],
        ["."],
    ],
)
def test_load_rejects_unsafe_or_noncanonical_pages(
    tmp_path: Path, pages: list[str]
) -> None:
    root, config = make_repo(tmp_path)
    payload = _valid_shard()
    payload["pages"] = pages
    _write_shard(root, payload)

    with pytest.raises(ManifestError, match="pages|page path"):
        ShardedManifest(config).iter_entries()


def test_iter_rejects_source_id_that_does_not_match_shard_path(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    payload = _valid_shard()
    payload["source_id"] = "sources/other.md"
    _write_shard(root, payload)

    with pytest.raises(ManifestError, match="source_id|Source ID|fields"):
        ShardedManifest(config).iter_entries()


def test_iter_rejects_symlinked_shards(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    external = tmp_path / "external.json"
    external.write_text(json.dumps(_valid_shard()), encoding="utf-8")
    shard = root / "wiki/.manifest/sources/a.md.json"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.symlink_to(external)

    with pytest.raises(ManifestError, match="ordinary file"):
        ShardedManifest(config).iter_entries()


def test_iter_rejects_nonordinary_or_unexpected_shard_entries(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    unexpected = root / "wiki/.manifest/sources/a.md.json"
    unexpected.mkdir(parents=True)

    with pytest.raises(ManifestError, match="ordinary file|manifest shard"):
        ShardedManifest(config).iter_entries()


def test_iter_rejects_symlinked_shard_root(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    entries_root = root / "wiki/.manifest/sources"
    external = tmp_path / "external-shards"
    external.mkdir()
    entries_root.parent.mkdir(parents=True, exist_ok=True)
    entries_root.symlink_to(external, target_is_directory=True)

    with pytest.raises(ManifestError, match="ordinary directory|symlink"):
        ShardedManifest(config).iter_entries()


def _replace_with_external_hardlink(tmp_path: Path, target: Path) -> None:
    external = tmp_path / f"external-{target.name}"
    external.write_bytes(target.read_bytes())
    target.unlink()
    os.link(external, target)


def test_marker_must_be_a_single_link_ordinary_file(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    _replace_with_external_hardlink(tmp_path, root / "wiki/.manifest.json")

    with pytest.raises(ManifestError, match="ordinary file|hard link|single link"):
        ShardedManifest(config)


def test_shard_must_be_a_single_link_ordinary_file(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    entry = store.upsert(source, compiled_at="2026-08-08T00:00:00Z")
    _replace_with_external_hardlink(tmp_path, store.entry_path(entry.source_id))

    with pytest.raises(ManifestError, match="ordinary file|hard link|single link"):
        store.iter_entries()


def test_upsert_rejects_hardlinked_source_files(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    external = tmp_path / "external-source.md"
    external.write_text("source", encoding="utf-8")
    source = root / "sources/design/a.md"
    os.link(external, source)

    with pytest.raises(ManifestError, match="ordinary file|hard link|single link"):
        ShardedManifest(config).upsert(source)

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import portable_manifest as portable_manifest_module
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable_manifest import (
    ManifestError,
    ManifestPreconditionError,
    ShardedManifest,
)


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


def test_validated_source_id_is_public_and_returns_source_id(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "design" / "architecture.md"
    source.write_text("body", encoding="utf-8")
    store = ShardedManifest(config)

    assert hasattr(store, "validated_source_id")
    assert store.validated_source_id(source) == "sources/design/architecture.md"


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
    synced: set[tuple[int, int]] = set()
    original_sync = portable_manifest_module.os.fsync

    def record_sync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            synced.add((metadata.st_dev, metadata.st_ino))
        original_sync(descriptor)

    monkeypatch.setattr(portable_manifest_module.os, "fsync", record_sync)

    store.upsert(source)

    for directory in (
        config.vault,
        config.vault / ".manifest",
        config.vault / ".manifest/sources",
        config.vault / ".manifest/sources/design",
    ):
        metadata = directory.stat()
        assert (metadata.st_dev, metadata.st_ino) in synced


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


def test_status_for_reports_tracked_missing_as_source_id(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "tracked.md"
    source.write_text("tracked", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, pages=[])
    source.unlink()

    assert store.status_for([source])["missing"] == ["sources/tracked.md"]


def test_status_for_reports_untracked_missing_as_source_id(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "untracked.md"

    assert ShardedManifest(config).status_for([source])["missing"] == [
        "sources/untracked.md"
    ]


def test_status_for_rejects_missing_source_outside_root(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)

    with pytest.raises(ManifestError, match="configured source root"):
        ShardedManifest(config).status_for([root / "outside-missing.md"])


def test_status_for_rejects_dangling_terminal_symlink(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "dangling.md"
    try:
        source.symlink_to(root / "sources" / "missing-target.md")
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(ManifestError, match="single-link ordinary file"):
        ShardedManifest(config).status_for([source])


def _replace_source_parent_with_external_symlink(
    root: Path, tmp_path: Path
) -> Path:
    source = root / "sources" / "nested" / "a.md"
    external = tmp_path / "external-sources"
    external.mkdir()
    (external / "a.md").write_text("tracked", encoding="utf-8")
    source.unlink()
    source.parent.rmdir()
    try:
        source.parent.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    return source


def test_status_for_unselected_rejects_external_symlinked_source_parent(
    tmp_path: Path,
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "nested" / "a.md"
    source.parent.mkdir()
    source.write_text("tracked", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, pages=[])
    _replace_source_parent_with_external_symlink(root, tmp_path)

    with pytest.raises(ManifestError, match="configured source root"):
        store.status_for([])


def test_status_for_selected_rejects_external_symlinked_source_parent(
    tmp_path: Path,
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources" / "nested" / "a.md"
    source.parent.mkdir()
    source.write_text("tracked", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, pages=[])
    source = _replace_source_parent_with_external_symlink(root, tmp_path)

    with pytest.raises(ManifestError, match="configured source root"):
        store.status_for([source])


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_prepared_wal_precedes_sidecar_and_recovers(tmp_path: Path, monkeypatch) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")

    def crash(step: str) -> None:
        if step == "prepared":
            raise SystemExit("crash after PREPARED")

    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", crash)
    with pytest.raises(SystemExit, match="PREPARED"):
        ShardedManifest(config).upsert(source)

    journal = root / ".obsidian-wiki/local/manifest-mutation/journal.json"
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "PREPARED"
    assert not list((root / "wiki").rglob(".obsidian-wiki-manifest-mutation"))

    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", lambda _: None)
    recovered = ShardedManifest(config)
    recovered.upsert(source)
    assert recovered.load("sources/design/a.md") is not None
    assert not journal.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_reader_accepts_only_wal_proven_link_window(tmp_path: Path, monkeypatch) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")

    def crash(step: str) -> None:
        if step == "linked":
            raise SystemExit("crash after link")

    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", crash)
    store = ShardedManifest(config)
    with pytest.raises(SystemExit, match="link"):
        store.upsert(source)

    shard = store.entry_path("sources/design/a.md")
    assert shard.stat().st_nlink == 2
    assert ShardedManifest(config).load("sources/design/a.md") is not None

    journal = root / ".obsidian-wiki/local/manifest-mutation/journal.json"
    journal.unlink()
    with pytest.raises(ManifestError, match="single-link"):
        ShardedManifest(config).load("sources/design/a.md")


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_noncooperative_target_creation_is_preserved_and_blocks_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, compiled_at="2026-08-08T00:00:00Z")
    target = store.entry_path("sources/design/a.md")
    owner = b"owner concurrent bytes\n"

    def interpose(step: str) -> None:
        if step == "reserved":
            target.write_bytes(owner)

    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", interpose)
    with pytest.raises(ManifestPreconditionError, match="concurrent|conflict"):
        store.upsert(source, compiled_at="2026-08-08T00:00:01Z")
    assert target.read_bytes() == owner
    journal = root / ".obsidian-wiki/local/manifest-mutation/journal.json"
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "CONFLICT"

    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", lambda _: None)
    with pytest.raises(ManifestPreconditionError, match="conflict"):
        ShardedManifest(config).remove("sources/design/a.md")
    assert target.read_bytes() == owner


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_iter_ignores_only_reserved_sidecar_directory(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source)
    sidecar = store.entry_path("sources/design/a.md").parent / ".obsidian-wiki-manifest-mutation"
    sidecar.mkdir()
    (sidecar / "candidate").write_text("internal", encoding="utf-8")
    assert [entry.source_id for entry in store.iter_entries()] == [
        "sources/design/a.md"
    ]
    (sidecar.parent / ".unknown-control").mkdir()
    (sidecar.parent / ".unknown-control/file").write_text("x", encoding="utf-8")
    with pytest.raises(ManifestError):
        store.iter_entries()


def test_manifest_mutation_capability_is_posix_not_linux_specific(monkeypatch) -> None:
    monkeypatch.setattr(portable_manifest_module.os, "name", "posix")
    monkeypatch.setattr(portable_manifest_module.sys, "platform", "darwin")
    assert portable_manifest_module._manifest_mutation_supported()


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_journal_rewrite_keeps_previous_durable_record_on_crash(
    tmp_path: Path, monkeypatch
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    real_rename = portable_manifest_module.os.rename
    journal_renames = 0

    def crash_on_second_journal_rename(source_name, target_name, *args, **kwargs):
        nonlocal journal_renames
        if source_name == ".journal.tmp" and target_name == "journal.json":
            journal_renames += 1
            if journal_renames == 2:
                raise SystemExit("crash during journal replacement")
        return real_rename(source_name, target_name, *args, **kwargs)

    monkeypatch.setattr(portable_manifest_module.os, "rename", crash_on_second_journal_rename)
    with pytest.raises(SystemExit, match="journal replacement"):
        ShardedManifest(config).upsert(source)
    journal = root / ".obsidian-wiki/local/manifest-mutation/journal.json"
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "PREPARED"

    monkeypatch.setattr(portable_manifest_module.os, "rename", real_rename)
    recovered = ShardedManifest(config)
    recovered.upsert(source)
    assert recovered.load("sources/design/a.md") is not None


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
@pytest.mark.parametrize("step", ["reserved", "installed", "applied"])
def test_each_upsert_crash_boundary_recovers_to_complete_shard(
    tmp_path: Path, monkeypatch, step: str
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, compiled_at="2026-08-08T00:00:00Z")

    def crash(current: str) -> None:
        if current == step:
            raise SystemExit(f"crash at {step}")

    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", crash)
    with pytest.raises(SystemExit, match=step):
        store.upsert(source, compiled_at="2026-08-08T00:00:01Z")
    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", lambda _: None)
    recovered = ShardedManifest(config)
    recovered.upsert(source, compiled_at="2026-08-08T00:00:01Z")
    assert recovered.load("sources/design/a.md").compiled_at == "2026-08-08T00:00:01Z"
    assert not (root / ".obsidian-wiki/local/manifest-mutation/journal.json").exists()
    assert not list((root / "wiki").rglob(".obsidian-wiki-manifest-mutation"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_remove_recovers_after_reservation_crash(tmp_path: Path, monkeypatch) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source)

    def crash(step: str) -> None:
        if step == "reserved":
            raise SystemExit("remove crash")

    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", crash)
    with pytest.raises(SystemExit, match="remove crash"):
        store.remove("sources/design/a.md")
    monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", lambda _: None)
    with ShardedManifest(config).mutation_session():
        pass
    assert store.load("sources/design/a.md") is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_manifest_protocol_never_uses_cross_directory_rename(
    tmp_path: Path, monkeypatch
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    real_rename = portable_manifest_module.os.rename

    def reject_cross_parent(source_name, target_name, *args, **kwargs):
        source_fd = kwargs.get("src_dir_fd")
        target_fd = kwargs.get("dst_dir_fd")
        if source_fd is not None and target_fd is not None:
            if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev:
                raise OSError(portable_manifest_module.errno.EXDEV, "cross-filesystem rename")
        return real_rename(source_name, target_name, *args, **kwargs)

    monkeypatch.setattr(portable_manifest_module.os, "rename", reject_cross_parent)
    store = ShardedManifest(config)
    store.upsert(source)
    store.remove("sources/design/a.md")


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_repeated_crash_recovery_keeps_control_artifacts_bounded(
    tmp_path: Path, monkeypatch
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    for index in range(20):
        crashed = False

        def crash(step: str) -> None:
            nonlocal crashed
            if step == ("linked" if index % 2 else "prepared") and not crashed:
                crashed = True
                raise SystemExit("bounded crash")

        monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", crash)
        with pytest.raises(SystemExit):
            ShardedManifest(config).upsert(source, compiled_at=f"2026-08-08T00:00:{index:02d}Z")
        monkeypatch.setattr(portable_manifest_module, "_manifest_fault_point", lambda _: None)
        with ShardedManifest(config).mutation_session():
            pass
    controls = [
        path
        for path in root.rglob("*")
        if path.name in portable_manifest_module._WAL_FILES
        or path.name in {"candidate", "reserved", ".obsidian-wiki-manifest-mutation"}
    ]
    assert controls == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX root descriptor binding")
def test_upsert_root_replacement_writes_neither_replacement_repository(
    tmp_path: Path, monkeypatch
) -> None:
    root, config = make_repo(tmp_path / "original")
    replacement, _ = make_repo(tmp_path / "replacement")
    detached = tmp_path / "detached"
    source = root / "sources/design/a.md"
    source.write_text("original", encoding="utf-8")
    replacement_source = replacement / "sources/design/a.md"
    replacement_source.write_text("replacement", encoding="utf-8")
    real_hash = portable_manifest_module.compute_hash

    def swap(path: Path) -> str:
        result = real_hash(path)
        root.rename(detached)
        replacement.rename(root)
        return result

    monkeypatch.setattr(portable_manifest_module, "compute_hash", swap)
    with pytest.raises(ManifestError, match="changed|unsafe"):
        ShardedManifest(config).upsert(source)
    assert not (root / "wiki/.manifest/sources/design/a.md.json").exists()
    assert not (detached / "wiki/.manifest/sources/design/a.md.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink"])
def test_unsafe_wal_journal_fails_closed_before_live_mutation(
    tmp_path: Path, kind: str
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    wal = root / ".obsidian-wiki/local/manifest-mutation"
    wal.mkdir(parents=True)
    journal = wal / "journal.json"
    if kind == "symlink":
        journal.symlink_to(tmp_path / "outside")
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(journal)
    else:
        outside = tmp_path / "journal-owner"
        outside.write_text("{}\n", encoding="utf-8")
        os.link(outside, journal)
    with pytest.raises(ManifestError):
        ShardedManifest(config).upsert(source)
    assert not ShardedManifest(config).entry_path("sources/design/a.md").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_unsafe_sidecar_fails_closed_without_touching_live_shard(
    tmp_path: Path, kind: str
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, compiled_at="2026-08-08T00:00:00Z")
    target = store.entry_path("sources/design/a.md")
    before = target.read_bytes()
    sidecar = target.parent / ".obsidian-wiki-manifest-mutation"
    if kind == "symlink":
        sidecar.symlink_to(tmp_path / "outside", target_is_directory=True)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(sidecar)
    with pytest.raises(ManifestError):
        store.upsert(source, compiled_at="2026-08-08T00:00:01Z")
    assert target.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX manifest WAL")
def test_unknown_sidecar_debris_blocks_mutation(tmp_path: Path) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source)
    target = store.entry_path("sources/design/a.md")
    sidecar = target.parent / ".obsidian-wiki-manifest-mutation"
    sidecar.mkdir()
    (sidecar / "owner-file").write_text("owner", encoding="utf-8")
    before = target.read_bytes()
    with pytest.raises(ManifestError, match="sidecar"):
        store.remove("sources/design/a.md")
    assert target.read_bytes() == before

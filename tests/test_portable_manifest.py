from __future__ import annotations

import errno
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX root descriptor binding")
def test_upsert_rejects_root_rebinding_without_writing_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_repo(tmp_path / "original")
    replacement, _replacement_config = make_repo(tmp_path / "replacement")
    detached = tmp_path / "detached-original"
    source = root / "sources/design/a.md"
    source.write_text("original", encoding="utf-8")
    (replacement / "sources/design/a.md").write_text(
        "replacement", encoding="utf-8"
    )
    store = ShardedManifest(config)
    real_compute_hash = portable_manifest_module.compute_hash
    replacement_before = tuple(
        (path.relative_to(replacement).as_posix(), path.read_bytes())
        for path in sorted(replacement.rglob("*"))
        if path.is_file()
    )
    swapped = False

    def swap_after_hash(path: Path) -> str:
        nonlocal swapped
        result = real_compute_hash(path)
        if not swapped:
            root.rename(detached)
            replacement.rename(root)
            swapped = True
        return result

    monkeypatch.setattr(portable_manifest_module, "compute_hash", swap_after_hash)

    with pytest.raises(ManifestError, match="changed|unsafe"):
        store.upsert(source)

    assert swapped
    replacement_after = tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )
    assert replacement_after == replacement_before


@pytest.mark.skipif(os.name != "posix", reason="POSIX root descriptor binding")
def test_remove_rejects_root_rebinding_without_unlinking_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_repo(tmp_path / "original")
    replacement, replacement_config = make_repo(tmp_path / "replacement")
    detached = tmp_path / "detached-original"
    source_id = "sources/design/a.md"
    for repository, repository_config in (
        (root, config),
        (replacement, replacement_config),
    ):
        source = repository / "sources/design/a.md"
        source.write_text(repository.name, encoding="utf-8")
        ShardedManifest(repository_config).upsert(source)
    store = ShardedManifest(config)
    replacement_shard = ShardedManifest(replacement_config).entry_path(source_id)
    replacement_bytes = replacement_shard.read_bytes()
    real_entry_path = store.entry_path
    swapped = False

    def swap_after_path(value: str) -> Path:
        nonlocal swapped
        result = real_entry_path(value)
        if not swapped:
            root.rename(detached)
            replacement.rename(root)
            swapped = True
        return result

    monkeypatch.setattr(store, "entry_path", swap_after_path)

    with pytest.raises(ManifestError, match="changed|unsafe"):
        store.remove(source_id)

    assert swapped
    assert (root / replacement_shard.relative_to(replacement)).read_bytes() == replacement_bytes


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory identity binding")
@pytest.mark.parametrize("operation", ["upsert", "remove"])
def test_manifest_mutator_rejects_vault_rebinding_without_touching_replacement(
    operation: str, tmp_path: Path
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    source_id = "sources/design/a.md"
    if operation == "remove":
        store.upsert(source)
    vault = root / "wiki"
    detached = root / "detached-wiki"
    replacement = root / "replacement-wiki"
    replacement.mkdir()
    (replacement / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
    )
    replacement_shard = replacement / ".manifest/sources/design/a.md.json"
    replacement_shard.parent.mkdir(parents=True)
    replacement_shard.write_text("owner replacement\n", encoding="utf-8")
    replacement_before = replacement_shard.read_bytes()
    vault.rename(detached)
    replacement.rename(vault)

    with pytest.raises(ManifestError, match="changed|unsafe"):
        if operation == "upsert":
            store.upsert(source)
        else:
            store.remove(source_id)

    assert (vault / ".manifest/sources/design/a.md.json").read_bytes() == replacement_before


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory identity binding")
def test_upsert_rejects_shard_parent_aba_during_child_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(source, compiled_at="2026-08-08T00:00:00Z")
    live = root / "wiki/.manifest/sources"
    detached = root / "wiki/.manifest/detached-sources"
    external = root / "wiki/.manifest/external-sources"
    external_shard = external / "design/a.md.json"
    external_shard.parent.mkdir(parents=True)
    external_shard.write_text("owner external\n", encoding="utf-8")
    live_shard = live / "design/a.md.json"
    live_before = live_shard.read_bytes()
    external_before = external_shard.read_bytes()
    real_open = portable_manifest_module.os.open
    swapped = False

    monkeypatch.setattr(
        portable_manifest_module, "_manifest_dirfd_supported", lambda: True
    )

    def swap_restore_around_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "sources" and kwargs.get("dir_fd") is not None and not swapped:
            live.rename(detached)
            external.rename(live)
            descriptor = real_open(path, flags, *args, **kwargs)
            live.rename(external)
            detached.rename(live)
            swapped = True
            return descriptor
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(portable_manifest_module.os, "open", swap_restore_around_open)

    with pytest.raises(ManifestError, match="changed|unsafe|ordinary"):
        store.upsert(source, compiled_at="2026-08-08T00:00:01Z")

    assert swapped
    assert live_shard.read_bytes() == live_before
    assert external_shard.read_bytes() == external_before


@pytest.mark.skipif(os.name != "posix", reason="POSIX target reservation")
def test_upsert_preserves_concurrent_target_replaced_at_preimage_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    entry = store.upsert(source, compiled_at="2026-08-08T00:00:00Z")
    target = store.entry_path(entry.source_id)
    expected = "sha256:" + __import__("hashlib").sha256(target.read_bytes()).hexdigest()
    concurrent = b"concurrent owner bytes\n"
    real_rename = portable_manifest_module.os.rename
    interposed = False

    def replace_before_rename(source_name, target_name, *args, **kwargs):
        nonlocal interposed
        if (
            (source_name == target.name or target_name == target.name)
            and kwargs.get("src_dir_fd") is not None
            and not interposed
        ):
            target.write_bytes(concurrent)
            interposed = True
        return real_rename(source_name, target_name, *args, **kwargs)

    monkeypatch.setattr(portable_manifest_module.os, "rename", replace_before_rename)
    monkeypatch.setattr(
        portable_manifest_module, "_manifest_dirfd_supported", lambda: True
    )

    with pytest.raises(ManifestError, match="changed|preimage|concurrent"):
        store.upsert(
            source,
            compiled_at="2026-08-08T00:00:01Z",
            expected_preimage=expected,
        )

    assert interposed
    assert not target.exists()
    assert concurrent in {
        path.read_bytes() for path in root.rglob("*.reserved-*") if path.is_file()
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX target reservation")
def test_remove_preserves_target_replaced_at_unlink_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    entry = store.upsert(source)
    target = store.entry_path(entry.source_id)
    concurrent = b"concurrent owner bytes\n"
    real_rename = portable_manifest_module.os.rename
    interposed = False

    def replace_before_reservation(source_name, target_name, *args, **kwargs):
        nonlocal interposed
        if (
            source_name == target.name
            and kwargs.get("src_dir_fd") is not None
            and not interposed
        ):
            target.write_bytes(concurrent)
            interposed = True
        return real_rename(source_name, target_name, *args, **kwargs)

    monkeypatch.setattr(
        portable_manifest_module.os, "rename", replace_before_reservation
    )
    monkeypatch.setattr(
        portable_manifest_module, "_manifest_dirfd_supported", lambda: True
    )

    with pytest.raises(ManifestError, match="changed|concurrent"):
        store.remove(entry.source_id)

    assert interposed
    assert not target.exists()
    assert concurrent in {
        path.read_bytes() for path in root.rglob("*.reserved-*") if path.is_file()
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX reservation quarantine")
@pytest.mark.parametrize("operation", ["upsert", "remove"])
def test_manifest_mutator_quarantines_reservation_changed_before_consumption(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    entry = store.upsert(source)
    target = store.entry_path(entry.source_id)
    concurrent = b"concurrent reservation bytes\n"
    original_move = portable_manifest_module._rename_noreplace
    interposed = False

    def change_reservation_before_archive(
        source_name: str,
        target_name: str,
        *,
        source_fd: int,
        target_fd: int,
    ) -> None:
        nonlocal interposed
        if source_name.startswith(f".{target.name}.reserved-") and not interposed:
            descriptor = os.open(source_name, os.O_WRONLY, dir_fd=source_fd)
            try:
                os.ftruncate(descriptor, 0)
                os.write(descriptor, concurrent)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            interposed = True
        original_move(
            source_name,
            target_name,
            source_fd=source_fd,
            target_fd=target_fd,
        )

    monkeypatch.setattr(
        portable_manifest_module,
        "_rename_noreplace",
        change_reservation_before_archive,
    )

    with pytest.raises(ManifestError, match="reservation.*changed|evidence"):
        if operation == "upsert":
            store.upsert(source)
        else:
            store.remove(entry.source_id)

    assert interposed
    evidence = list(
        (root / ".obsidian-wiki/local/manifest-reservations").glob("shard-*")
    )
    assert evidence
    assert concurrent in {path.read_bytes() for path in evidence}


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-replace capability")
@pytest.mark.parametrize("operation", ["upsert", "remove"])
def test_manifest_mutator_checks_noreplace_before_live_mutation(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = make_repo(tmp_path)
    source = root / "sources/design/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    entry = store.upsert(source)
    target = store.entry_path(entry.source_id)
    before = target.read_bytes()
    real_move = portable_manifest_module._rename_noreplace

    def unavailable(source_name, target_name, *, source_fd, target_fd):
        if source_name.startswith(".noreplace-probe-"):
            raise OSError(errno.ENOTSUP, "unsupported")
        return real_move(
            source_name,
            target_name,
            source_fd=source_fd,
            target_fd=target_fd,
        )

    monkeypatch.setattr(portable_manifest_module, "_rename_noreplace", unavailable)

    with pytest.raises(ManifestError, match="no-replace.*unavailable"):
        if operation == "upsert":
            store.upsert(source)
        else:
            store.remove(entry.source_id)

    assert target.read_bytes() == before
    assert not list(target.parent.glob(f".{target.name}.reserved-*"))

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import local_state as local_state_module
from obsidian_wiki.config import PortableConfig, load_portable_config
from obsidian_wiki.local_state import (
    LocalStateError,
    authoritative_fingerprint,
    hot_status,
    mark_hot_current,
)


@pytest.fixture
def config_fixture(tmp_path: Path) -> PortableConfig:
    root = tmp_path / "knowledge"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "wiki").mkdir()
    (root / ".skills").mkdir()
    path = root / ".obsidian-wiki" / "config.toml"
    path.write_text(
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
    (root / "wiki" / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    (root / "wiki" / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
    )
    return load_portable_config(
        path,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )


def test_hot_is_stale_until_marked(config_fixture: PortableConfig) -> None:
    config = config_fixture
    assert hot_status(config)["stale"] is True
    config.vault.joinpath("hot.md").write_text("# Hot\n", encoding="utf-8")

    mark_hot_current(config)

    assert hot_status(config)["stale"] is False
    payload = json.loads((config.local_state / "hot-state.json").read_text())
    assert payload == {
        "fingerprint": authoritative_fingerprint(config),
        "hot_hash": payload["hot_hash"],
    }
    assert payload["hot_hash"].startswith("sha256:")


def test_page_change_invalidates_and_removes_hot(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("# Hot\n", encoding="utf-8")
    mark_hot_current(config)
    page = config.vault / "concepts" / "a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# changed\n", encoding="utf-8")

    status = hot_status(config, invalidate=True)

    assert status["stale"] is True
    assert not hot.exists()


def test_manifest_operation_and_branch_changes_are_authoritative(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("# Hot\n", encoding="utf-8")
    mark_hot_current(config)

    shard = config.vault / ".manifest" / "sources" / "a.md.json"
    shard.parent.mkdir(parents=True)
    shard.write_text("{}\n", encoding="utf-8")
    assert hot_status(config)["stale"] is True

    mark_hot_current(config)
    operation = (
        config.vault
        / "journal"
        / "operations"
        / "2026"
        / "08"
        / "20260808T000000Z-abcd.md"
    )
    operation.parent.mkdir(parents=True)
    operation.write_text("# operation\n", encoding="utf-8")
    assert hot_status(config)["stale"] is True

    subprocess.run(["git", "init", "-q", str(config.root)], check=True)
    subprocess.run(
        ["git", "-C", str(config.root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(config.root), "config", "user.name", "Test"], check=True
    )
    subprocess.run(["git", "-C", str(config.root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(config.root), "commit", "-qm", "initial"], check=True
    )
    mark_hot_current(config)
    subprocess.run(["git", "-C", str(config.root), "branch", "other"], check=True)
    subprocess.run(["git", "-C", str(config.root), "switch", "-q", "other"], check=True)
    assert hot_status(config)["stale"] is True


def test_obsidian_and_hot_changes_do_not_change_authoritative_fingerprint(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("first\n", encoding="utf-8")
    before = authoritative_fingerprint(config)
    (config.vault / ".obsidian").mkdir()
    (config.vault / ".obsidian" / "workspace.json").write_text(
        '{"active":"pane"}\n', encoding="utf-8"
    )
    hot.write_text("second\n", encoding="utf-8")

    assert authoritative_fingerprint(config) == before


def test_raw_readouts_and_non_shard_files_are_not_authoritative(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    before = authoritative_fingerprint(config)
    for relative in (
        "_raw/draft.md",
        "_readouts/briefing.md",
        ".manifest/sources/editor.tmp",
    ):
        path = config.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("local or derived\n", encoding="utf-8")

    assert authoritative_fingerprint(config) == before


def test_changed_hot_hash_is_stale_and_invalidated(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("first\n", encoding="utf-8")
    mark_hot_current(config)
    hot.write_text("changed\n", encoding="utf-8")

    status = hot_status(config, invalidate=True)

    assert status["stale"] is True
    assert not hot.exists()


def test_mark_requires_an_existing_ordinary_hot_file(
    config_fixture: PortableConfig, tmp_path: Path
) -> None:
    with pytest.raises(LocalStateError, match="hot.md"):
        mark_hot_current(config_fixture)

    external = tmp_path / "external.md"
    external.write_text("outside\n", encoding="utf-8")
    try:
        (config_fixture.vault / "hot.md").symlink_to(external)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable")
    with pytest.raises(LocalStateError, match="ordinary file"):
        mark_hot_current(config_fixture)


def test_invalid_sidecar_fails_closed_without_deleting_unrelated_files(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("# Hot\n", encoding="utf-8")
    config.local_state.mkdir(parents=True)
    sidecar = config.local_state / "hot-state.json"
    sidecar.write_text("not json\n", encoding="utf-8")

    assert hot_status(config, invalidate=True)["stale"] is True
    assert not hot.exists()
    assert sidecar.read_text(encoding="utf-8") == "not json\n"


def test_concurrent_hot_replacement_is_not_invalidated(
    config_fixture: PortableConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("old hot\n", encoding="utf-8")
    mark_hot_current(config)
    page = config.vault / "concepts" / "changed.md"
    page.parent.mkdir()
    page.write_text("authoritative change\n", encoding="utf-8")

    real_fingerprint = local_state_module.authoritative_fingerprint
    calls = 0

    def replace_after_second_fingerprint(value: PortableConfig) -> str:
        nonlocal calls
        result = real_fingerprint(value)
        calls += 1
        if calls == 2:
            replacement = value.vault / ".new-hot.tmp"
            replacement.write_text("new hot\n", encoding="utf-8")
            replacement.replace(hot)
        return result

    monkeypatch.setattr(
        local_state_module,
        "authoritative_fingerprint",
        replace_after_second_fingerprint,
    )

    status = hot_status(config, invalidate=True)

    assert status["stale"] is True
    assert hot.read_text(encoding="utf-8") == "new hot\n"


def test_replacement_during_bound_invalidation_is_restored(
    config_fixture: PortableConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not local_state_module._SUPPORTS_BOUND_DIRECTORIES:
        pytest.skip("bound directory descriptors are unavailable")
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("old hot\n", encoding="utf-8")
    mark_hot_current(config)
    page = config.vault / "concepts" / "changed.md"
    page.parent.mkdir()
    page.write_text("authoritative change\n", encoding="utf-8")
    real_rename = local_state_module.os.rename
    replaced = False

    def replace_before_rename(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal replaced
        if source == "hot.md" and not replaced:
            replacement = config.vault / ".new-hot.tmp"
            replacement.write_text("new hot\n", encoding="utf-8")
            replacement.replace(hot)
            replaced = True
        real_rename(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(local_state_module.os, "rename", replace_before_rename)

    status = hot_status(config, invalidate=True)

    assert status["stale"] is True
    assert hot.read_text(encoding="utf-8") == "new hot\n"


def test_sidecar_write_stays_bound_to_opened_local_directory(
    config_fixture: PortableConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not local_state_module._SUPPORTS_BOUND_DIRECTORIES:
        pytest.skip("bound directory descriptors are unavailable")
    config = config_fixture
    hot = config.vault / "hot.md"
    hot.write_text("hot\n", encoding="utf-8")
    config.local_state.mkdir(parents=True)
    displaced = config.root / ".obsidian-wiki" / "displaced-local"
    external = tmp_path / "external-local"
    external.mkdir()
    real_write_all = local_state_module._write_all
    swapped = False

    def swap_directory(descriptor: int, data: bytes) -> None:
        nonlocal swapped
        if not swapped:
            config.local_state.rename(displaced)
            config.local_state.symlink_to(external, target_is_directory=True)
            swapped = True
        real_write_all(descriptor, data)

    monkeypatch.setattr(local_state_module, "_write_all", swap_directory)

    with pytest.raises(LocalStateError, match="contained directory"):
        mark_hot_current(config)
    assert not (external / "hot-state.json").exists()


def test_parent_git_repository_does_not_supply_branch_identity(
    config_fixture: PortableConfig,
    tmp_path: Path,
) -> None:
    config = config_fixture
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    hot = config.vault / "hot.md"
    hot.write_text("hot\n", encoding="utf-8")
    mark_hot_current(config)
    subprocess.run(["git", "-C", str(tmp_path), "branch", "other"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "switch", "-q", "other"], check=True)

    assert hot_status(config)["stale"] is False


def test_detached_head_identity_invalidates_hot(config_fixture: PortableConfig) -> None:
    config = config_fixture
    subprocess.run(["git", "init", "-q", str(config.root)], check=True)
    subprocess.run(
        ["git", "-C", str(config.root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(config.root), "config", "user.name", "Test"], check=True
    )
    subprocess.run(["git", "-C", str(config.root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(config.root), "commit", "-qm", "initial"], check=True
    )
    hot = config.vault / "hot.md"
    hot.write_text("hot\n", encoding="utf-8")
    mark_hot_current(config)

    subprocess.run(
        ["git", "-C", str(config.root), "switch", "-q", "--detach", "HEAD"],
        check=True,
    )

    assert hot_status(config)["stale"] is True

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
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

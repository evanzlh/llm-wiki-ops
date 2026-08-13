from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki import local_state as local_state_module
from obsidian_wiki.config import PortableConfig, load_portable_config
from obsidian_wiki.local_state import (
    LocalStateError,
    authoritative_fingerprint,
    hot_inputs,
    hot_status,
    mark_hot_current,
)
from obsidian_wiki.operations import (
    EMPTY_OPERATION_LOG,
    OperationChange,
    parse_operation_log,
    render_operation_log,
)


def run_cli(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
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
    (root / "wiki" / "log.md").write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    (root / "wiki" / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
    )
    return load_portable_config(
        path,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )


def _write_page(
    config: PortableConfig,
    relative: str,
    *,
    title: str,
    summary: str,
    updated: str,
) -> Path:
    page = config.vault / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\n"
        f"title: {title}\n"
        f"category: {Path(relative).parts[0]}\n"
        "tags: [cache]\n"
        "sources: [sources/input.md]\n"
        f"summary: {summary}\n"
        "created: 2026-08-01\n"
        f"updated: {updated}\n"
        "---\n"
        f"# {title}\n",
        encoding="utf-8",
    )
    return page


def _write_operation(
    config: PortableConfig,
    *,
    transaction_id: str,
    completed_at: str,
    suffix: str,
    source_ids: tuple[str, ...] = ("sources/input.md",),
    created: tuple[str, ...] = (),
    updated: tuple[str, ...] = (),
    removed: tuple[str, ...] = (),
) -> Path:
    change = OperationChange(
        transaction_id=transaction_id,
        completed_at=completed_at,
        source_ids=source_ids,
        created=created,
        updated=updated,
        removed=removed,
    )
    path = config.vault / "log.md"
    changes = parse_operation_log(path.read_text(encoding="utf-8"))
    path.write_text(render_operation_log(changes + (change,)), encoding="utf-8")
    return path


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, int, bytes | None]]:
    snapshot: dict[str, tuple[int, int, int, bytes | None]] = {}
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        content = (
            path.read_bytes() if path.is_file() and not path.is_symlink() else None
        )
        snapshot[relative] = (
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            content,
        )
    return snapshot


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
    _write_operation(
        config,
        transaction_id="tx-authoritative",
        completed_at="2026-08-08T00:00:00Z",
        suffix="abcd",
    )
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


def test_hot_cli_marks_reports_and_invalidates_local_state(
    config_fixture: PortableConfig, tmp_path: Path
) -> None:
    config = config_fixture
    home = tmp_path / "home"
    initial = run_cli(home, config.root, "hot", "status", "--json")
    assert initial.returncode == 0, initial.stderr
    assert json.loads(initial.stdout)["stale"] is True

    hot = config.vault / "hot.md"
    hot.write_text("# Hot\n", encoding="utf-8")
    marked = run_cli(home, config.root, "hot", "mark-current")
    assert marked.returncode == 0, marked.stderr
    current = run_cli(home, config.root, "hot", "status", "--json")
    assert current.returncode == 0, current.stderr
    assert json.loads(current.stdout)["stale"] is False

    page = config.vault / "concepts" / "changed.md"
    page.parent.mkdir()
    page.write_text("changed\n", encoding="utf-8")
    stale = run_cli(home, config.root, "hot", "status", "--json")
    assert stale.returncode == 0, stale.stderr
    assert json.loads(stale.stdout)["stale"] is True
    assert not hot.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ("hot", "status", "--json"),
        ("hot", "mark-current"),
        ("hot", "inputs"),
    ],
)
def test_hot_cli_fails_outside_portable_mode(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(tmp_path / "home", cwd, *arguments)

    assert result.returncode == 1
    if "--json" in arguments or arguments[-1] == "inputs":
        assert result.stderr == ""
        assert "portable repository" in json.loads(result.stdout)["error"]["message"]
        assert "Traceback" not in result.stdout
    else:
        assert "portable repository" in result.stderr
        assert "Traceback" not in result.stderr


def test_hot_inputs_returns_cjk_page_summary_and_serialized_operation(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    _write_page(
        config,
        "concepts/缓存.md",
        title="缓存",
        summary="本地派生缓存。",
        updated="2026-08-11",
    )
    _write_operation(
        config,
        transaction_id="tx-hot",
        completed_at="2026-08-11T01:00:00Z",
        suffix="abcd",
        source_ids=("sources/组会.md",),
        created=("concepts/缓存.md",),
        updated=("references/缓存策略.md",),
        removed=("skills/旧缓存.md",),
    )

    payload = hot_inputs(config, page_limit=20, operation_limit=5)

    assert payload == {
        "fingerprint": authoritative_fingerprint(config),
        "pages": [
            {
                "path": "concepts/缓存.md",
                "title": "缓存",
                "summary": "本地派生缓存。",
                "updated": "2026-08-11",
            }
        ],
        "operations": [
            {
                "transaction_id": "tx-hot",
                "completed_at": "2026-08-11T01:00:00Z",
                "source_ids": ["sources/组会.md"],
                "created": ["concepts/缓存.md"],
                "updated": ["references/缓存策略.md"],
                "removed": ["skills/旧缓存.md"],
            }
        ],
    }


def test_hot_inputs_bounds_and_sorts_pages_and_operations_deterministically(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    _write_page(
        config,
        "concepts/older.md",
        title="Older",
        summary="Old page.",
        updated="2026-08-10",
    )
    tied_concept = _write_page(
        config,
        "concepts/tied.md",
        title="Tied concept",
        summary="Concept tie.",
        updated="2026-08-12",
    )
    tied_reference = _write_page(
        config,
        "references/tied.md",
        title="Tied reference",
        summary="Reference tie.",
        updated="2026-08-12",
    )
    for name in ("index.md", "hot.md"):
        control = config.vault / name
        control.write_text(tied_reference.read_text(encoding="utf-8"), encoding="utf-8")
    dynamic = config.vault / "_raw" / "dynamic.md"
    dynamic.parent.mkdir()
    dynamic.write_text(tied_concept.read_text(encoding="utf-8"), encoding="utf-8")

    _write_operation(
        config,
        transaction_id="tx-old",
        completed_at="2026-08-10T01:00:00Z",
        suffix="cccc",
    )
    _write_operation(
        config,
        transaction_id="tx-tie-a",
        completed_at="2026-08-12T01:00:00Z",
        suffix="aaaa",
    )
    _write_operation(
        config,
        transaction_id="tx-tie-b",
        completed_at="2026-08-12T01:00:00Z",
        suffix="bbbb",
    )

    first = hot_inputs(config, page_limit=2, operation_limit=2)
    second = hot_inputs(config, page_limit=2, operation_limit=2)

    assert first == second
    assert [page["path"] for page in first["pages"]] == [
        "references/tied.md",
        "concepts/tied.md",
    ]
    assert [operation["transaction_id"] for operation in first["operations"]] == [
        "tx-tie-b",
        "tx-tie-a",
    ]


def test_hot_inputs_page_limit_uses_absolute_updated_time(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    _write_page(
        config,
        "concepts/lexically-newest.md",
        title="Lexically newest",
        summary="Earlier after normalizing its offset.",
        updated="2026-08-11T00:30:00+14:00",
    )
    _write_page(
        config,
        "concepts/date-only.md",
        title="Date only",
        summary="Midnight UTC.",
        updated="2026-08-11",
    )
    _write_page(
        config,
        "concepts/actually-newest.md",
        title="Actually newest",
        summary="Later after normalizing its offset.",
        updated="2026-08-10T23:45:00-12:00",
    )

    payload = hot_inputs(config, page_limit=2, operation_limit=0)

    assert [page["path"] for page in payload["pages"]] == [
        "concepts/actually-newest.md",
        "concepts/date-only.md",
    ]
    assert [page["updated"] for page in payload["pages"]] == [
        "2026-08-10T23:45:00-12:00",
        "2026-08-11",
    ]


def test_hot_inputs_same_instant_uses_path_as_deterministic_tie_breaker(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    _write_page(
        config,
        "concepts/alpha.md",
        title="Alpha",
        summary="UTC spelling.",
        updated="2026-08-11T00:00:00Z",
    )
    _write_page(
        config,
        "references/zulu.md",
        title="Zulu",
        summary="Equivalent offset spelling.",
        updated="2026-08-10T12:00:00-12:00",
    )

    payload = hot_inputs(config, page_limit=2, operation_limit=0)

    assert [page["path"] for page in payload["pages"]] == [
        "references/zulu.md",
        "concepts/alpha.md",
    ]


def test_hot_inputs_bounds_boundary_aware_timestamps_deterministically(
    config_fixture: PortableConfig,
) -> None:
    config = config_fixture
    _write_page(
        config,
        "concepts/earliest.md",
        title="Earliest",
        summary="Lower datetime boundary with a positive offset.",
        updated="0001-01-01T00:00:00+23:59",
    )
    _write_page(
        config,
        "concepts/latest.md",
        title="Latest",
        summary="Upper datetime boundary with a negative offset.",
        updated="9999-12-31T23:59:59-23:59",
    )

    first = hot_inputs(config, page_limit=1, operation_limit=0)
    second = hot_inputs(config, page_limit=1, operation_limit=0)

    assert first == second
    assert [page["path"] for page in first["pages"]] == ["concepts/latest.md"]
    assert first["pages"][0]["updated"] == "9999-12-31T23:59:59-23:59"


def test_hot_inputs_cli_pages_limit_uses_absolute_updated_time(
    config_fixture: PortableConfig,
    tmp_path: Path,
) -> None:
    config = config_fixture
    _write_page(
        config,
        "concepts/apparent.md",
        title="Apparent",
        summary="Lexically later but chronologically earlier.",
        updated="2026-08-11T00:30:00+14:00",
    )
    _write_page(
        config,
        "concepts/actual.md",
        title="Actual",
        summary="Chronologically later.",
        updated="2026-08-10T23:45:00-12:00",
    )

    result = run_cli(
        tmp_path / "home",
        config.root,
        "hot",
        "inputs",
        "--pages",
        "1",
        "--operations",
        "0",
    )

    assert result.returncode == 0, result.stderr
    assert [page["path"] for page in json.loads(result.stdout)["pages"]] == [
        "concepts/actual.md"
    ]


def test_hot_inputs_accepts_zero_limits(config_fixture: PortableConfig) -> None:
    config = config_fixture
    _write_page(
        config,
        "concepts/ignored.md",
        title="Ignored",
        summary="Bounded out.",
        updated="2026-08-11",
    )
    _write_operation(
        config,
        transaction_id="tx-ignored",
        completed_at="2026-08-11T01:00:00Z",
        suffix="abcd",
    )

    payload = hot_inputs(config, page_limit=0, operation_limit=0)

    assert payload == {
        "fingerprint": authoritative_fingerprint(config),
        "pages": [],
        "operations": [],
    }


@pytest.mark.parametrize(
    "page_limit,operation_limit",
    [(-1, 0), (0, -1)],
)
def test_hot_inputs_rejects_negative_limits(
    config_fixture: PortableConfig,
    page_limit: int,
    operation_limit: int,
) -> None:
    with pytest.raises(LocalStateError, match="limits must be non-negative"):
        hot_inputs(
            config_fixture,
            page_limit=page_limit,
            operation_limit=operation_limit,
        )


@pytest.mark.parametrize("kind", ["page", "operation"])
def test_hot_inputs_fails_closed_for_malformed_authoritative_files_at_zero_limit(
    config_fixture: PortableConfig,
    kind: str,
) -> None:
    config = config_fixture
    if kind == "page":
        malformed = config.vault / "concepts" / "malformed.md"
    else:
        malformed = config.vault / "log.md"
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.write_text("# Missing frontmatter\n", encoding="utf-8")

    with pytest.raises(LocalStateError, match="invalid"):
        hot_inputs(config, page_limit=0, operation_limit=0)


@pytest.mark.parametrize("kind", ["page", "operation"])
def test_hot_inputs_rejects_symlinked_authoritative_files(
    config_fixture: PortableConfig,
    tmp_path: Path,
    kind: str,
) -> None:
    config = config_fixture
    external = tmp_path / f"external-{kind}.md"
    external.write_text(
        "---\ntitle: External\nupdated: 2026-08-11\n---\n",
        encoding="utf-8",
    )
    if kind == "page":
        link = config.vault / "concepts" / "linked.md"
    else:
        link = config.vault / "log.md"
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(external)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises(LocalStateError, match="ordinary|unsafe"):
        hot_inputs(config)


def test_hot_inputs_rejects_hardlinked_operation_log(
    config_fixture: PortableConfig, tmp_path: Path
) -> None:
    log = config_fixture.vault / "log.md"
    external = tmp_path / "external-log.md"
    external.write_bytes(log.read_bytes())
    log.unlink()
    os.link(external, log)

    with pytest.raises(LocalStateError, match="single-link"):
        hot_inputs(config_fixture)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are unavailable")
def test_hot_inputs_rejects_special_operation_log(
    config_fixture: PortableConfig,
) -> None:
    log = config_fixture.vault / "log.md"
    log.unlink()
    os.mkfifo(log)

    with pytest.raises(LocalStateError, match="ordinary"):
        hot_inputs(config_fixture)


def test_hot_inputs_rejects_operation_log_change_during_read(
    config_fixture: PortableConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = config_fixture.vault / "log.md"
    original_read = local_state_module.os.read
    changed = False

    def mutate_after_log_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        data = original_read(descriptor, size)
        if data == EMPTY_OPERATION_LOG.encode("utf-8") and not changed:
            changed = True
            log.write_text(EMPTY_OPERATION_LOG + "\n", encoding="utf-8")
        return data

    monkeypatch.setattr(local_state_module.os, "read", mutate_after_log_read)

    with pytest.raises(LocalStateError, match="changed while it was being read"):
        hot_inputs(config_fixture)


def test_hot_inputs_and_cli_preserve_exact_tree_and_mtimes(
    config_fixture: PortableConfig,
    tmp_path: Path,
) -> None:
    config = config_fixture
    _write_page(
        config,
        "concepts/read-only.md",
        title="Read only",
        summary="No writes.",
        updated="2026-08-11",
    )
    _write_operation(
        config,
        transaction_id="tx-read-only",
        completed_at="2026-08-11T01:00:00Z",
        suffix="abcd",
    )
    before = _tree_snapshot(config.root)

    direct = hot_inputs(config)
    default_json = run_cli(tmp_path / "home", config.root, "hot", "inputs")
    explicit_json = run_cli(
        tmp_path / "home",
        config.root,
        "hot",
        "inputs",
        "--json",
    )

    assert default_json.returncode == 0, default_json.stderr
    assert explicit_json.returncode == 0, explicit_json.stderr
    assert default_json.stderr == explicit_json.stderr == ""
    assert default_json.stdout == explicit_json.stdout
    assert json.loads(default_json.stdout) == direct
    assert _tree_snapshot(config.root) == before


def test_hot_inputs_cli_accepts_interleaved_options_and_pretty_json(
    config_fixture: PortableConfig,
    tmp_path: Path,
) -> None:
    result = run_cli(
        tmp_path / "home",
        config_fixture.root,
        "hot",
        "inputs",
        "--operations",
        "0",
        "--pretty",
        "--pages",
        "0",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "\n  \"fingerprint\"" in result.stdout
    assert json.loads(result.stdout) == {
        "fingerprint": authoritative_fingerprint(config_fixture),
        "pages": [],
        "operations": [],
    }


@pytest.mark.parametrize(
    "option",
    ["--pages", "--operations"],
)
def test_hot_inputs_cli_reports_negative_limit_errors(
    config_fixture: PortableConfig,
    tmp_path: Path,
    option: str,
) -> None:
    result = run_cli(
        tmp_path / "home",
        config_fixture.root,
        "hot",
        "inputs",
        option,
        "-1",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert "hot input limits must be non-negative" in json.loads(result.stdout)[
        "error"
    ]["message"]
    assert "Traceback" not in result.stdout


def test_hot_inputs_cli_reports_invalid_authoritative_input_cleanly(
    config_fixture: PortableConfig,
    tmp_path: Path,
) -> None:
    malformed = config_fixture.vault / "concepts" / "malformed.md"
    malformed.parent.mkdir()
    malformed.write_text("# Missing frontmatter\n", encoding="utf-8")

    result = run_cli(
        tmp_path / "home",
        config_fixture.root,
        "hot",
        "inputs",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert json.loads(result.stdout)["error"]["message"].startswith(
        "invalid knowledge page"
    )
    assert "Traceback" not in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX repository rebinding safety")
def test_mark_hot_rejects_ordinary_repository_rebound(
    config_fixture: PortableConfig, tmp_path: Path
) -> None:
    config = config_fixture
    original = tmp_path / "original-knowledge"
    config.root.rename(original)
    (config.root / "wiki").mkdir(parents=True)
    (config.root / "wiki/hot.md").write_text("# replacement\n", encoding="utf-8")

    with pytest.raises(LocalStateError, match="repository root changed"):
        mark_hot_current(config)

    assert not config.local_state.exists()


@pytest.mark.parametrize("relative", ["concepts", "concepts/nested"])
def test_hot_inputs_fails_closed_when_authoritative_directory_is_unreadable(
    config_fixture: PortableConfig,
    relative: str,
) -> None:
    directory = config_fixture.vault / relative
    directory.mkdir(parents=True)
    directory.chmod(0)
    try:
        try:
            with os.scandir(directory) as entries:
                list(entries)
        except OSError:
            pass
        else:
            pytest.skip("directory permissions cannot be enforced")

        with pytest.raises(LocalStateError, match="unavailable|unreadable"):
            hot_inputs(config_fixture)
    finally:
        directory.chmod(0o755)


def test_hot_inputs_rejects_authoritative_drift_after_snapshot(
    config_fixture: PortableConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _write_page(
        config_fixture,
        "concepts/drift.md",
        title="Drift",
        summary="Before mutation.",
        updated="2026-08-11",
    )
    original = local_state_module._authoritative_snapshot

    def mutate_after_snapshot(
        config: PortableConfig, *, page_limit: int, operation_limit: int
    ) -> tuple[str, list[dict[str, str]], list[dict[str, object]]]:
        snapshot = original(
            config,
            page_limit=page_limit,
            operation_limit=operation_limit,
        )
        page.write_text(
            page.read_text(encoding="utf-8").replace(
                "Before mutation.", "After mutation."
            ),
            encoding="utf-8",
        )
        return snapshot

    monkeypatch.setattr(
        local_state_module,
        "_authoritative_snapshot",
        mutate_after_snapshot,
    )

    with pytest.raises(LocalStateError, match="changed during hot input verification"):
        hot_inputs(config_fixture)


@pytest.mark.parametrize(
    "old,new,issue_code",
    [
        ("tags: [cache]\n", "", "frontmatter-tags-missing"),
        ("title: Valid\n", 'title: ""\n', "frontmatter-title-empty"),
        (
            "updated: 2026-08-11\n",
            "updated: definitely-not-a-date\n",
            "frontmatter-updated-invalid",
        ),
        (
            "category: concepts\n",
            "category: entities\n",
            "frontmatter-category-path",
        ),
        ("tags: [cache]\n", "tags: cache\n", "frontmatter-tags-type"),
        ("title: Valid\n", "title: [Valid]\n", "frontmatter-title-type"),
        (
            "sources: [sources/input.md]\n",
            "sources: []\n",
            "frontmatter-sources-empty",
        ),
    ],
)
def test_hot_inputs_reuses_full_portable_page_metadata_validation(
    config_fixture: PortableConfig,
    old: str,
    new: str,
    issue_code: str,
) -> None:
    page = _write_page(
        config_fixture,
        "concepts/invalid.md",
        title="Valid",
        summary="Metadata validation.",
        updated="2026-08-11",
    )
    page.write_text(
        page.read_text(encoding="utf-8").replace(old, new),
        encoding="utf-8",
    )

    with pytest.raises(LocalStateError, match=issue_code):
        hot_inputs(config_fixture)


def test_hot_inputs_rejects_aba_summary_bytes_restored_before_verification(
    config_fixture: PortableConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _write_page(
        config_fixture,
        "concepts/aba.md",
        title="ABA",
        summary="Old summary.",
        updated="2026-08-11",
    )
    old_text = page.read_text(encoding="utf-8")
    new_text = old_text.replace("Old summary.", "New summary.")
    original = local_state_module._read_ordinary_text_bytes
    changed = False

    def read_new_then_restore(
        path: Path,
        label: str,
        *,
        root: Path,
    ) -> tuple[bytes, str]:
        nonlocal changed
        if path == page and label == "knowledge page" and not changed:
            changed = True
            page.write_text(new_text, encoding="utf-8")
            try:
                return original(path, label, root=root)
            finally:
                page.write_text(old_text, encoding="utf-8")
        return original(path, label, root=root)

    monkeypatch.setattr(
        local_state_module,
        "_read_ordinary_text_bytes",
        read_new_then_restore,
    )

    with pytest.raises(LocalStateError, match="changed during hot input verification"):
        hot_inputs(config_fixture)


def test_hot_inputs_rejects_mutation_during_verification(
    config_fixture: PortableConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _write_page(
        config_fixture,
        "concepts/verification-race.md",
        title="Verification race",
        summary="Before verification.",
        updated="2026-08-11",
    )
    original = local_state_module._git_identity
    calls = 0

    def mutate_after_verification_hash(config: PortableConfig) -> str | None:
        nonlocal calls
        result = original(config)
        calls += 1
        if calls == 2:
            page.write_text(
                page.read_text(encoding="utf-8").replace(
                    "Before verification.", "During verification."
                ),
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(
        local_state_module,
        "_git_identity",
        mutate_after_verification_hash,
    )

    with pytest.raises(LocalStateError, match="changed during hot input verification"):
        hot_inputs(config_fixture)


def test_hot_inputs_requires_cjk_source_id_below_configured_root(
    config_fixture: PortableConfig,
) -> None:
    page = _write_page(
        config_fixture,
        "concepts/source-root.md",
        title="Source root",
        summary="Configured source identity.",
        updated="2026-08-11",
    )
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "sources/input.md", "sources/组会.md"
        ),
        encoding="utf-8",
    )

    payload = hot_inputs(config_fixture)

    assert payload["pages"][0]["path"] == "concepts/source-root.md"


def test_hot_inputs_rejects_cjk_source_id_outside_configured_roots(
    config_fixture: PortableConfig,
) -> None:
    page = _write_page(
        config_fixture,
        "concepts/source-root.md",
        title="Source root",
        summary="Configured source identity.",
        updated="2026-08-11",
    )
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "sources/input.md", "outside/组会.md"
        ),
        encoding="utf-8",
    )

    with pytest.raises(LocalStateError, match="frontmatter-source-root"):
        hot_inputs(config_fixture)


def test_hot_inputs_rejects_operation_source_id_outside_configured_roots(
    config_fixture: PortableConfig,
) -> None:
    _write_operation(
        config_fixture,
        transaction_id="tx-outside-source-root",
        completed_at="2026-08-11T01:00:00Z",
        suffix="abcd",
        source_ids=("outside/组会.md",),
    )

    with pytest.raises(LocalStateError, match="configured source roots"):
        hot_inputs(config_fixture)


def test_authoritative_fingerprint_retains_legacy_canonical_format(
    config_fixture: PortableConfig,
) -> None:
    _write_page(
        config_fixture,
        "concepts/prefix.md",
        title="Prefix",
        summary="Prefix file.",
        updated="2026-08-11",
    )
    _write_page(
        config_fixture,
        "concepts/prefix/child.md",
        title="Child",
        summary="Nested prefix file.",
        updated="2026-08-11",
    )
    files = [
        [
            path.relative_to(config_fixture.root).as_posix(),
            local_state_module._hash_ordinary_file(
                path,
                "authoritative file",
                root=config_fixture.root,
            ),
        ]
        for path in sorted(
            local_state_module._authoritative_files(config_fixture),
            key=lambda item: item.relative_to(config_fixture.vault).as_posix(),
        )
    ]
    canonical = json.dumps(
        {"files": files, "git": local_state_module._git_identity(config_fixture)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected = "sha256:" + hashlib.sha256(canonical).hexdigest()

    assert authoritative_fingerprint(config_fixture) == expected

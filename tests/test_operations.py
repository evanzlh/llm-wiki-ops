from __future__ import annotations

import ast
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from obsidian_wiki import operations
from obsidian_wiki.operations import (
    EMPTY_OPERATION_LOG,
    OperationChange,
    OperationError,
    append_operation,
    append_operation_text,
    parse_operation_log,
    render_operation_block,
    render_operation_log,
)


def change(
    transaction_id: str = "tx-1",
    completed_at: str = "2026-08-07T07:30:00Z",
    source_ids: tuple[str, ...] = ("sources/a.md",),
    created: tuple[str, ...] = (),
    updated: tuple[str, ...] = (),
    removed: tuple[str, ...] = (),
) -> OperationChange:
    return OperationChange(
        transaction_id, completed_at, source_ids, created, updated, removed
    )


def test_empty_operation_log_is_exact_and_parses() -> None:
    assert EMPTY_OPERATION_LOG == (
        "---\n"
        "title: Wiki Operation Log\n"
        "operation_log_schema: 1\n"
        "---\n\n"
        "# Wiki Operation Log\n"
    )
    assert parse_operation_log(EMPTY_OPERATION_LOG) == ()


def test_render_parse_round_trip_canonicalizes_lists() -> None:
    original = change(
        source_ids=("sources/b.md", "sources/a.md", "sources/b.md"),
        created=("concepts/b.md", "concepts/a.md", "concepts/b.md"),
        updated=("references/z.md",),
    )

    text = render_operation_log((original,))

    assert text == EMPTY_OPERATION_LOG + (
        "\n## 2026-08-07T07:30:00Z · tx-1\n\n"
        "### Sources\n\n"
        "- `sources/a.md`\n"
        "- `sources/b.md`\n\n"
        "### Created\n\n"
        "- [[concepts/a]]\n"
        "- [[concepts/b]]\n\n"
        "### Updated\n\n"
        "- [[references/z]]\n\n"
        "### Removed\n\n"
        "- None\n"
    )
    assert parse_operation_log(text) == (
        change(
            source_ids=("sources/a.md", "sources/b.md"),
            created=("concepts/a.md", "concepts/b.md"),
            updated=("references/z.md",),
        ),
    )


def test_render_empty_change_lists_as_none() -> None:
    block = render_operation_block(change())
    assert block.count("- None") == 3
    assert block.startswith("\n## 2026-08-07T07:30:00Z · tx-1\n")


def test_duplicate_transaction_id_is_rejected() -> None:
    first = change()
    duplicate = change(completed_at="2026-08-07T08:00:00Z")
    with pytest.raises(OperationError, match="transaction_id"):
        render_operation_log((first, duplicate))
    with pytest.raises(OperationError, match="transaction_id"):
        append_operation_text(render_operation_log((first,)), duplicate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("title: Wiki Operation Log", "title: Wrong", 1),
        lambda text: text.replace("# Wiki Operation Log", "# Wiki Operation Log\n", 1),
        lambda text: text.replace("### Sources", "### Source", 1),
        lambda text: text.replace("### Created", "### Updated", 1),
        lambda text: text.replace("- `sources/a.md`", "- sources/a.md", 1),
        lambda text: text.replace("[[concepts/a]]", "[[concepts/a|alias]]", 1),
        lambda text: text.replace("[[concepts/a]]", "[[concepts/a#anchor]]", 1),
        lambda text: text.replace(
            "- `sources/a.md`", "- `sources/a.md`\n- `sources/a.md`", 1
        ),
        lambda text: text.replace(
            "- [[concepts/a]]", "- [[concepts/b]]\n- [[concepts/a]]", 1
        ),
        lambda text: text + "extra prose\n",
    ],
)
def test_parse_rejects_malformed_or_noncanonical_text(mutate) -> None:
    canonical = render_operation_log((change(created=("concepts/a.md",)),))
    with pytest.raises(OperationError):
        parse_operation_log(mutate(canonical))


def test_log_order_is_timestamp_only_and_stable_for_ties() -> None:
    same_time = change(transaction_id="tx-0")
    first = change(transaction_id="tx-z")
    assert parse_operation_log(render_operation_log((first, same_time))) == (
        first,
        same_time,
    )
    older = change(transaction_id="tx-old", completed_at="2026-08-07T07:29:59Z")
    with pytest.raises(OperationError, match="order"):
        render_operation_log((first, older))
    with pytest.raises(OperationError, match="older"):
        append_operation_text(render_operation_log((first,)), older)


def test_operation_change_is_frozen() -> None:
    item = change()
    with pytest.raises(FrozenInstanceError):
        item.transaction_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "item",
    [
        change(source_ids=()),
        change(source_ids=("../source.md",)),
        change(created=("../outside.md",)),
        change(created=("concepts/a.md",), updated=("concepts/a.md",)),
    ],
)
def test_change_validation_rejects_invalid_inputs(item: OperationChange) -> None:
    with pytest.raises(OperationError):
        render_operation_block(item)


def test_change_validation_wraps_invalid_scalar_types() -> None:
    invalid = change(transaction_id=42)  # type: ignore[arg-type]
    with pytest.raises(OperationError, match="transaction_id"):
        render_operation_block(invalid)


@pytest.mark.parametrize(
    "completed_at",
    [
        "2026-08-07 07:30:00Z",
        "2026-08-07T07:30Z",
        "2026-08-07T07:30:00.000Z",
        "2026-08-07T07:30:00+00:00",
        "2026-08-07Z",
    ],
)
def test_timestamp_requires_canonical_utc_spelling(completed_at: str) -> None:
    with pytest.raises(OperationError, match="completed_at"):
        render_operation_block(change(completed_at=completed_at))


def test_append_operation_atomically_replaces_log(tmp_path: Path) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    before = target.stat()

    result = append_operation(target, change(), root=tmp_path)

    after = target.stat()
    assert result == target
    assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    assert parse_operation_log(target.read_text(encoding="utf-8")) == (change(),)
    assert not list(tmp_path.glob(".log.md.tmp-*"))


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_append_rejects_unsafe_target_without_changing_external_inode(
    tmp_path: Path, kind: str
) -> None:
    target = tmp_path / "log.md"
    external = tmp_path / "external.md"
    external.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    if kind == "symlink":
        target.symlink_to(external)
    elif kind == "hardlink":
        os.link(external, target)
    else:
        if kind == "directory":
            target.mkdir()
        else:
            os.mkfifo(target)
    original = external.read_bytes()

    with pytest.raises(OperationError):
        append_operation(target, change(), root=tmp_path)

    assert external.read_bytes() == original


def test_append_rejects_noncanonical_target_path(tmp_path: Path) -> None:
    target = tmp_path / "other.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    with pytest.raises(OperationError, match="root/log.md"):
        append_operation(target, change(), root=tmp_path)


def test_concurrent_target_change_is_not_overwritten_and_temp_is_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original_check = operations._verify_preimage

    def replace_before_check(parent_fd: int, descriptor: int, identity) -> None:
        target.write_text("concurrent\n", encoding="utf-8")
        original_check(parent_fd, descriptor, identity)

    monkeypatch.setattr(operations, "_verify_preimage", replace_before_check)
    with pytest.raises(OperationError, match="changed"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_text(encoding="utf-8") == "concurrent\n"
    assert not list(tmp_path.glob(".log.md.tmp-*"))


def test_concurrent_parent_swap_does_not_touch_replacement_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "log.md").write_text("external\n", encoding="utf-8")
    original_check = operations._verify_parent
    checks = 0

    def swap_before_check(path: Path, descriptor: int, identity) -> None:
        nonlocal checks
        checks += 1
        if checks == 1:
            original_check(path, descriptor, identity)
            return
        moved = tmp_path / "moved-vault"
        root.rename(moved)
        replacement.rename(root)
        original_check(path, descriptor, identity)

    monkeypatch.setattr(operations, "_verify_parent", swap_before_check)
    with pytest.raises(OperationError, match="parent changed"):
        append_operation(target, change(), root=root)

    assert (root / "log.md").read_text(encoding="utf-8") == "external\n"
    assert not list((tmp_path / "moved-vault").glob(".log.md.tmp-*"))


def test_write_failure_preserves_log_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")

    def fail_write(descriptor: int, data) -> int:
        raise OSError("simulated")

    monkeypatch.setattr(operations.os, "write", fail_write)
    with pytest.raises(OperationError, match="write"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert not list(tmp_path.glob(".log.md.tmp-*"))


def test_postcheck_target_swap_is_preserved_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    concurrent = b"concurrent owner content\n"
    original_rename = operations._rename_noreplace
    swapped = False

    def swap_at_boundary(parent_fd: int, source: str, destination: str, *bound) -> None:
        nonlocal swapped
        if source == "log.md" and not swapped:
            swapped = True
            target.write_bytes(concurrent)
        original_rename(parent_fd, source, destination, *bound)

    monkeypatch.setattr(operations, "_rename_noreplace", swap_at_boundary)
    with pytest.raises(OperationError, match="changed"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_bytes() == concurrent
    assert not list(tmp_path.glob(".log.md.tmp-*"))


def test_postcheck_parent_swap_does_not_touch_replacement_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "log.md").write_bytes(b"external\n")
    original_rename = operations._rename_noreplace
    swapped = False

    def swap_at_boundary(parent_fd: int, source: str, destination: str, *bound) -> None:
        nonlocal swapped
        if source == "log.md" and not swapped:
            swapped = True
            root.rename(tmp_path / "moved-vault")
            replacement.rename(root)
        original_rename(parent_fd, source, destination, *bound)

    monkeypatch.setattr(operations, "_rename_noreplace", swap_at_boundary)
    with pytest.raises(OperationError, match="parent changed"):
        append_operation(target, change(), root=root)

    assert (root / "log.md").read_bytes() == b"external\n"
    assert (tmp_path / "moved-vault/log.md").read_text(
        encoding="utf-8"
    ) == EMPTY_OPERATION_LOG


def test_temp_substitution_is_rejected_before_target_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    external = tmp_path / "external.md"
    external.write_bytes(b"external\n")
    original_verify = operations._verify_temp

    def substitute(parent_fd: int, name: str, descriptor: int, identity, data) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, external.read_bytes())
        original_verify(parent_fd, name, descriptor, identity, data)

    monkeypatch.setattr(operations, "_verify_temp", substitute)
    with pytest.raises(OperationError, match="temporary"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert external.read_bytes() == b"external\n"


def test_temp_substitution_at_promotion_cannot_install_external_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    external = tmp_path / "external.md"
    external.write_bytes(b"external\n")
    original_link = operations._link_descriptor_noreplace
    substituted = False

    def substitute(parent_fd: int, descriptor: int, destination: str) -> None:
        nonlocal substituted
        if not substituted:
            substituted = True
            os.link(external, ".log.md.tmp-attacker", dst_dir_fd=parent_fd)
        original_link(parent_fd, descriptor, destination)

    monkeypatch.setattr(operations, "_link_descriptor_noreplace", substitute)
    append_operation(target, change(), root=tmp_path)

    assert parse_operation_log(target.read_text(encoding="utf-8")) == (change(),)
    assert external.read_bytes() == b"external\n"


def test_cleanup_substitution_never_unlinks_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / ".log.md.tmp-owned"
    owned.write_bytes(b"owned")
    metadata = owned.stat()
    identity = (metadata.st_dev, metadata.st_ino)
    collision = b"collision"
    original_rename = operations._rename_noreplace
    swapped = False

    def substitute(parent_fd: int, source: str, destination: str, *bound) -> None:
        nonlocal swapped
        if source == owned.name and not swapped:
            swapped = True
            owned.rename(tmp_path / "owned-moved")
            owned.write_bytes(collision)
        original_rename(parent_fd, source, destination, *bound)

    monkeypatch.setattr(operations, "_rename_noreplace", substitute)
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        operations._cleanup_owned(parent_fd, owned.name, identity)
    finally:
        os.close(parent_fd)

    assert owned.read_bytes() == collision
    assert (tmp_path / "owned-moved").read_bytes() == b"owned"


def test_cleanup_substitution_after_identity_check_preserves_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = tmp_path / ".log.md.tmp-owned"
    owned.write_bytes(b"owned")
    metadata = owned.stat()
    identity = (metadata.st_dev, metadata.st_ino)
    external = tmp_path / "external"
    external.write_bytes(b"external")
    original_stat = operations.os.stat
    substituted = False

    def substitute_after_stat(path, *args, **kwargs):
        nonlocal substituted
        result = original_stat(path, *args, **kwargs)
        if isinstance(path, str) and path.startswith(".log.md.cleanup-"):
            if not substituted:
                substituted = True
                parent_fd = kwargs["dir_fd"]
                os.rename(
                    path,
                    "owned-preserved",
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.rename(external, path, dst_dir_fd=parent_fd)
        return result

    monkeypatch.setattr(operations.os, "stat", substitute_after_stat)
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        operations._cleanup_owned(parent_fd, owned.name, identity)
    finally:
        os.close(parent_fd)

    assert any(path.read_bytes() == b"external" for path in tmp_path.iterdir())
    assert (tmp_path / "owned-preserved").read_bytes() == b"owned"


@pytest.mark.parametrize("failure", ["file_fsync", "promotion", "parent_fsync"])
def test_pipeline_failure_never_overwrites_external_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    external = tmp_path / "external.md"
    external.write_bytes(b"external\n")
    if failure == "file_fsync":
        def fail_sync(descriptor: int) -> None:
            raise OSError("fail")

        monkeypatch.setattr(operations.os, "fsync", fail_sync)
    elif failure == "promotion":
        def fail_promotion(parent_fd: int, descriptor: int, destination: str) -> None:
            raise OSError("fail")

        monkeypatch.setattr(
            operations, "_link_descriptor_noreplace", fail_promotion
        )
    else:
        original_fsync = operations.os.fsync
        calls = 0

        def fail_parent_sync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fail")
            original_fsync(descriptor)

        monkeypatch.setattr(operations.os, "fsync", fail_parent_sync)

    with pytest.raises(OperationError, match="write"):
        append_operation(target, change(), root=tmp_path)

    assert external.read_bytes() == b"external\n"
    if target.exists():
        parse_operation_log(target.read_text(encoding="utf-8"))
    assert not list(tmp_path.glob(".log.md.tmp-*"))
    assert not list(tmp_path.glob(".log.md.backup-*"))
    assert not list(tmp_path.glob(".log.md.rollback-*"))


def test_installed_verification_failure_rolls_back_preimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")

    def fail_verification(parent_fd: int, descriptor: int, identity, expected) -> None:
        raise OperationError("simulated installed verification failure")

    monkeypatch.setattr(operations, "_verify_installed", fail_verification)
    with pytest.raises(OperationError, match="installed verification"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert not list(tmp_path.glob(".log.md.tmp-*"))
    assert not list(tmp_path.glob(".log.md.backup-*"))
    assert not list(tmp_path.glob(".log.md.rollback-*"))


def test_rollback_rejects_substituted_backup_without_installing_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    external = tmp_path / "external"
    external.write_bytes(b"external")

    def substitute_backup(parent_fd: int, descriptor: int, identity, expected) -> None:
        backup = next(
            name for name in os.listdir(parent_fd) if name.startswith(".log.md.backup-")
        )
        os.rename(
            backup,
            "owner-backup-preserved",
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.link(external, backup, dst_dir_fd=parent_fd)
        raise OperationError("simulated verification failure")

    monkeypatch.setattr(operations, "_verify_installed", substitute_backup)
    with pytest.raises(OperationError, match="verification failure"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_bytes() != b"external"
    assert external.read_bytes() == b"external"
    assert (tmp_path / "owner-backup-preserved").read_text(
        encoding="utf-8"
    ) == EMPTY_OPERATION_LOG


def test_backup_substitution_immediately_before_descriptor_restore_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    external = tmp_path / "external"
    external.write_bytes(b"external")
    original_restore = operations._restore_preimage_copy
    substituted = False

    def substitute_then_restore(parent_fd, descriptor, identity, expected):
        nonlocal substituted
        if not substituted:
            substituted = True
            backup = next(
                name
                for name in os.listdir(parent_fd)
                if name.startswith(".log.md.backup-")
            )
            os.rename(
                backup,
                "owner-backup-preserved",
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.link(external, backup, dst_dir_fd=parent_fd)
        return original_restore(parent_fd, descriptor, identity, expected)

    monkeypatch.setattr(operations, "_restore_preimage_copy", substitute_then_restore)
    monkeypatch.setattr(
        operations,
        "_verify_installed",
        lambda *args: (_ for _ in ()).throw(OperationError("verification failed")),
    )
    with pytest.raises(OperationError, match="verification failed"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG
    assert external.read_bytes() == b"external"
    assert (tmp_path / "owner-backup-preserved").read_text(
        encoding="utf-8"
    ) == EMPTY_OPERATION_LOG


def test_postpromotion_different_canonical_bytes_roll_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original_link = operations._link_descriptor_noreplace

    def mutate_after_link(parent_fd: int, descriptor: int, destination: str) -> None:
        original_link(parent_fd, descriptor, destination)
        replacement = render_operation_log(
            (change(transaction_id="tx-other", completed_at="2026-08-07T08:00:00Z"),)
        ).encode("utf-8")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, replacement)

    monkeypatch.setattr(operations, "_link_descriptor_noreplace", mutate_after_link)
    with pytest.raises(OperationError, match="installed"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG


def test_preimage_metadata_change_at_move_boundary_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original_rename = operations._rename_noreplace
    changed = False

    def rewrite_before_move(
        parent_fd: int, source: str, destination: str, *bound
    ) -> None:
        nonlocal changed
        if source == "log.md" and not changed:
            changed = True
            data = target.read_bytes()
            target.write_bytes(data)
            metadata = target.stat()
            os.utime(
                target,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns - 1),
            )
        original_rename(parent_fd, source, destination, *bound)

    monkeypatch.setattr(operations, "_rename_noreplace", rewrite_before_move)
    with pytest.raises(OperationError, match="changed"):
        append_operation(target, change(), root=tmp_path)

    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG


def test_late_root_swap_fails_and_rolls_back_displaced_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "log.md").write_bytes(b"external\n")
    original_verify = operations._verify_installed
    swapped = False

    def verify_then_swap(parent_fd: int, descriptor: int, identity, expected) -> None:
        nonlocal swapped
        result = original_verify(parent_fd, descriptor, identity, expected)
        if not swapped:
            swapped = True
            root.rename(tmp_path / "moved-vault")
            replacement.rename(root)
        return result

    monkeypatch.setattr(operations, "_verify_installed", verify_then_swap)
    with pytest.raises(OperationError, match="parent changed"):
        append_operation(target, change(), root=root)

    assert (root / "log.md").read_bytes() == b"external\n"
    assert (tmp_path / "moved-vault/log.md").read_text(
        encoding="utf-8"
    ) == EMPTY_OPERATION_LOG


def test_moved_preimage_requires_full_stable_identity(tmp_path: Path) -> None:
    target = tmp_path / "guard"
    expected = EMPTY_OPERATION_LOG.encode("utf-8")
    target.write_bytes(expected)
    descriptor = os.open(target, os.O_RDONLY)
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        actual = operations._stable_identity(os.fstat(descriptor))
        changed = actual[:5] + (actual[5] - 1,) + actual[6:]
        with pytest.raises(OperationError, match="changed"):
            operations._verify_moved_preimage(
                parent_fd, target.name, descriptor, changed, expected
            )
    finally:
        os.close(descriptor)
        os.close(parent_fd)


def test_stable_identity_includes_ctime() -> None:
    fields = dict(
        st_dev=1,
        st_ino=2,
        st_mode=3,
        st_size=4,
        st_mtime_ns=5,
        st_ctime_ns=6,
        st_nlink=7,
    )
    before = operations._stable_identity(SimpleNamespace(**fields))
    fields["st_ctime_ns"] = 8
    after = operations._stable_identity(SimpleNamespace(**fields))
    assert before != after
    assert before == (1, 2, 3, 4, 5, 6, 7)


def test_changed_files_parse_with_python_38_grammar() -> None:
    repository = Path(__file__).resolve().parents[1]
    for relative in ("obsidian_wiki/operations.py", "tests/test_operations.py"):
        source = (repository / relative).read_text(encoding="utf-8")
        ast.parse(source, filename=relative, feature_version=(3, 8))

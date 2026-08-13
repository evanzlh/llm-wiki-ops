from __future__ import annotations

import ast
import os
import stat
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


def operation_lock_path(root: Path) -> Path:
    return root.parent / ("." + root.name + "-operation-log.lock")


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


def test_operation_change_accepts_journal_operations_as_knowledge_path() -> None:
    original = change(
        created=("journal/operations/entry.md",),
        updated=("journal/operations/reviewed.md",),
        removed=("journal/operations/old.md",),
    )

    assert parse_operation_log(render_operation_log((original,))) == (original,)


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


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("field", ["source", "page"])
def test_change_validation_rejects_unicode_line_separators(
    separator: str, field: str
) -> None:
    item = (
        change(source_ids=("sources/a" + separator + "b.md",))
        if field == "source"
        else change(created=("concepts/a" + separator + "b.md",))
    )
    with pytest.raises(OperationError):
        render_operation_block(item)


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

    result = append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    after = target.stat()
    assert result == target
    assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    assert parse_operation_log(target.read_text(encoding="utf-8")) == (change(),)
    assert not list(tmp_path.glob(".log.md.tmp-*"))


def test_nested_public_append_is_rejected_without_losing_outer_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    lock_path = tmp_path / "local" / "operation-log.lock"
    lock_path.parent.mkdir()
    original_replace = operations.os.replace
    nested_error = None

    def interposed_replace(source: Path, destination: Path) -> None:
        nonlocal nested_error
        if nested_error is None:
            try:
                append_operation(
                    target,
                    change(
                        transaction_id="tx-nested",
                        completed_at="2026-08-07T08:00:00Z",
                    ),
                    root=root,
                    lock_path=lock_path,
                )
            except OperationError as exc:
                nested_error = exc
        original_replace(source, destination)

    monkeypatch.setattr(operations.os, "replace", interposed_replace)

    append_operation(target, change(), root=root, lock_path=lock_path)

    assert nested_error is not None
    assert "in progress" in str(nested_error)
    assert parse_operation_log(target.read_text(encoding="utf-8")) == (change(),)
    assert lock_path.is_file()
    assert lock_path.stat().st_nlink == 1


@pytest.mark.parametrize("kind", ["inside", "symlink", "hardlink", "directory"])
def test_append_rejects_unsafe_operation_lock(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    local = tmp_path / "local"
    local.mkdir()
    lock_path = local / "operation-log.lock"
    external = tmp_path / "external.lock"
    external.write_bytes(b"")
    if kind == "inside":
        lock_path = root / "operation-log.lock"
    elif kind == "symlink":
        try:
            lock_path.symlink_to(external)
        except (NotImplementedError, OSError):
            pytest.skip("symlinks are unavailable")
    elif kind == "hardlink":
        try:
            os.link(external, lock_path)
        except (NotImplementedError, OSError):
            pytest.skip("hard links are unavailable")
    else:
        lock_path.mkdir()

    with pytest.raises(OperationError, match="lock"):
        append_operation(target, change(), root=root, lock_path=lock_path)

    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG


def test_append_preserves_existing_permission_bits(tmp_path: Path) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    target.chmod(0o600)

    append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_success_leaves_only_log_file(tmp_path: Path) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["log.md"]


def test_append_does_not_require_linux_only_primitives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    monkeypatch.delattr(os, "O_TMPFILE", raising=False)

    append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert parse_operation_log(target.read_text(encoding="utf-8")) == (change(),)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_append_rejects_unsafe_target_without_changing_external_inode(
    tmp_path: Path, kind: str
) -> None:
    target = tmp_path / "log.md"
    external = tmp_path / "external.md"
    external.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    if kind == "symlink":
        try:
            target.symlink_to(external)
        except (NotImplementedError, OSError):
            pytest.skip("symlinks are unavailable")
    elif kind == "hardlink":
        try:
            os.link(external, target)
        except (NotImplementedError, OSError):
            pytest.skip("hard links are unavailable")
    else:
        if kind == "directory":
            target.mkdir()
        else:
            if not hasattr(os, "mkfifo"):
                pytest.skip("FIFOs are unavailable")
            os.mkfifo(target)
    original = external.read_bytes()

    with pytest.raises(OperationError):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert external.read_bytes() == original


def test_parent_reparse_point_attribute_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        metadata = original_lstat(path)
        if path == tmp_path:
            return SimpleNamespace(
                **{
                    name: getattr(metadata, name)
                    for name in dir(metadata)
                    if name.startswith("st_") and name != "st_file_attributes"
                },
                st_file_attributes=0x400,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(OperationError, match="parent"):
        operations._open_parent(tmp_path)


def test_initial_temp_fstat_failure_closes_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original_fstat = operations.os.fstat
    original_close = operations.os.close
    temp_descriptor = None
    closed = []

    def fail_temp_fstat(descriptor: int):
        nonlocal temp_descriptor
        names = list(tmp_path.glob(".log.md.tmp-*"))
        if names and temp_descriptor is None:
            temp_descriptor = descriptor
            raise OSError("simulated")
        return original_fstat(descriptor)

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(operations.os, "fstat", fail_temp_fstat)
    monkeypatch.setattr(operations.os, "close", record_close)
    with pytest.raises(OperationError, match="temporary"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert temp_descriptor in closed
    assert not list(tmp_path.glob(".log.md.tmp-*"))
    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG


def test_append_rejects_noncanonical_target_path(tmp_path: Path) -> None:
    target = tmp_path / "other.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    with pytest.raises(OperationError, match="root/log.md"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))




def test_concurrent_target_change_is_not_overwritten_and_temp_is_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original_check = operations._verify_preimage

    def replace_before_check(path: Path, descriptor: int, identity, data, mode) -> None:
        target.write_text("concurrent\n", encoding="utf-8")
        original_check(path, descriptor, identity, data, mode)

    monkeypatch.setattr(operations, "_verify_preimage", replace_before_check)
    with pytest.raises(OperationError, match="changed"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert target.read_text(encoding="utf-8") == "concurrent\n"
    assert not list(tmp_path.glob(".log.md.tmp-*"))


def test_preimage_byte_drift_is_rejected_even_if_metadata_looks_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original = target.read_bytes()
    original_check = operations._verify_preimage
    original_identity = operations._stable_identity

    def ignore_change_times(metadata):
        identity = original_identity(metadata)
        return identity[:4] + (0, 0) + identity[6:]

    def drift_before_check(path, descriptor, identity, expected, mode) -> None:
        metadata = target.stat()
        changed = bytearray(original)
        changed[0] = ord("!")
        target.write_bytes(changed)
        os.utime(target, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        original_check(path, descriptor, identity, expected, mode)

    monkeypatch.setattr(operations, "_stable_identity", ignore_change_times)
    monkeypatch.setattr(operations, "_verify_preimage", drift_before_check)
    with pytest.raises(OperationError, match="changed"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert target.read_bytes() != render_operation_log((change(),)).encode("utf-8")
    assert not list(tmp_path.glob(".log.md.tmp-*"))


def test_fchmod_failure_cleanup_does_not_unlink_substituted_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    external = tmp_path / "external"
    external.write_bytes(b"external")

    def substitute_then_fail(descriptor: int, mode: int) -> None:
        temp = next(tmp_path.glob(".log.md.tmp-*"))
        temp.rename(tmp_path / "owned-temp-preserved")
        external.rename(temp)
        raise OSError("simulated")

    monkeypatch.setattr(operations.os, "fchmod", substitute_then_fail)
    with pytest.raises(OperationError, match="prepare"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    substituted = next(tmp_path.glob(".log.md.tmp-*"))
    assert substituted.read_bytes() == b"external"
    assert (tmp_path / "owned-temp-preserved").exists()
    assert target.read_text(encoding="utf-8") == EMPTY_OPERATION_LOG


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

    def swap_before_check(path: Path, descriptor, identity) -> None:
        root.rename(tmp_path / "moved-vault")
        replacement.rename(root)
        original_check(path, descriptor, identity)

    monkeypatch.setattr(operations, "_verify_parent", swap_before_check)
    with pytest.raises(OperationError, match="parent changed"):
        append_operation(target, change(), root=root, lock_path=operation_lock_path(root))

    assert (root / "log.md").read_text(encoding="utf-8") == "external\n"
    assert not list((tmp_path / "moved-vault").glob(".log.md.tmp-*"))


@pytest.mark.parametrize("failure", ["write", "file_fsync", "replace"])
def test_prereplace_pipeline_failure_preserves_log_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original = target.read_bytes()

    if failure == "write":
        monkeypatch.setattr(
            operations.os,
            "write",
            lambda descriptor, data: (_ for _ in ()).throw(OSError("simulated")),
        )
    elif failure == "file_fsync":
        monkeypatch.setattr(
            operations.os,
            "fsync",
            lambda descriptor: (_ for _ in ()).throw(OSError("simulated")),
        )
    else:
        monkeypatch.setattr(
            operations.os,
            "replace",
            lambda source, destination: (_ for _ in ()).throw(OSError("simulated")),
        )

    with pytest.raises(OperationError, match="append"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert target.read_bytes() == original
    assert not list(tmp_path.glob(".log.md.tmp-*"))


def test_temp_mutation_is_rejected_before_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original = target.read_bytes()
    original_verify = operations._verify_temp
    calls = 0

    def mutate_before_second_verify(*args) -> None:
        nonlocal calls
        calls += 1
        descriptor = args[3]
        if calls == 2:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"different canonical-looking bytes")
            os.ftruncate(descriptor, len(b"different canonical-looking bytes"))
        original_verify(*args)

    monkeypatch.setattr(operations, "_verify_temp", mutate_before_second_verify)
    with pytest.raises(OperationError, match="temporary"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert target.read_bytes() == original
    assert not list(tmp_path.glob(".log.md.tmp-*"))


def test_parent_fsync_failure_leaves_exact_installed_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    original_fsync = operations.os.fsync
    calls = 0

    def fail_parent_sync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated")
        original_fsync(descriptor)

    monkeypatch.setattr(operations.os, "fsync", fail_parent_sync)
    with pytest.raises(OperationError, match="append"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert parse_operation_log(target.read_text(encoding="utf-8")) == (change(),)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["log.md"]


def test_installed_verification_failure_leaves_exact_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")

    def fail_verification(*args, **kwargs) -> None:
        raise OperationError("simulated installed verification failure")

    monkeypatch.setattr(operations, "_verify_installed", fail_verification)
    with pytest.raises(OperationError, match="verification failure"):
        append_operation(target, change(), root=tmp_path, lock_path=operation_lock_path(tmp_path))

    assert parse_operation_log(target.read_text(encoding="utf-8")) == (change(),)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["log.md"]


def test_linux_specific_protocol_helpers_are_absent() -> None:
    assert not hasattr(operations, "_link_descriptor_noreplace")
    assert not hasattr(operations, "_rename_noreplace")
    assert not hasattr(operations, "_exchange_names")


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

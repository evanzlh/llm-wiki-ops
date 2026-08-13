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


def test_append_preserves_existing_permission_bits(tmp_path: Path) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    target.chmod(0o600)

    append_operation(target, change(), root=tmp_path)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_success_leaves_only_log_file(tmp_path: Path) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    append_operation(target, change(), root=tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["log.md"]


def test_append_does_not_require_linux_only_primitives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "log.md"
    target.write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    monkeypatch.delattr(os, "O_TMPFILE", raising=False)

    append_operation(target, change(), root=tmp_path)

    assert parse_operation_log(target.read_text(encoding="utf-8")) == (change(),)


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

    def replace_before_check(path: Path, descriptor: int, identity) -> None:
        target.write_text("concurrent\n", encoding="utf-8")
        original_check(path, descriptor, identity)

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

    def swap_before_check(path: Path, descriptor, identity) -> None:
        root.rename(tmp_path / "moved-vault")
        replacement.rename(root)
        original_check(path, descriptor, identity)

    monkeypatch.setattr(operations, "_verify_parent", swap_before_check)
    with pytest.raises(OperationError, match="parent changed"):
        append_operation(target, change(), root=root)

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
        append_operation(target, change(), root=tmp_path)

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
        append_operation(target, change(), root=tmp_path)

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
        append_operation(target, change(), root=tmp_path)

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
        append_operation(target, change(), root=tmp_path)

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

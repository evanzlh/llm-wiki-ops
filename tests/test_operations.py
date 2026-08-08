from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from obsidian_wiki import operations
from obsidian_wiki.operations import (
    OperationChange,
    OperationError,
    operation_path,
    render_operation,
    validate_operation,
    write_operation,
)


def test_render_operation_has_required_frontmatter_and_sorted_changes() -> None:
    change = OperationChange(
        transaction_id="tx-1",
        completed_at="2026-08-07T07:30:00Z",
        source_ids=("sources/b.md", "sources/a.md"),
        created=("concepts/b.md", "concepts/a.md"),
        updated=("references/z.md",),
        removed=(),
    )

    text = render_operation(change)

    assert "category: journal" in text
    assert "  - sources/a.md\n  - sources/b.md" in text
    assert text.index("[[concepts/a]]") < text.index("[[concepts/b]]")
    assert "model" not in text.lower()
    assert "agent:" not in text.lower()


def test_write_operation_uses_unique_immutable_path(tmp_path: Path) -> None:
    change = OperationChange(
        "tx-1",
        "2026-08-07T07:30:00Z",
        ("sources/a.md",),
        (),
        (),
        (),
    )

    first = write_operation(tmp_path, change, suffix="a81f")
    second = write_operation(tmp_path, change, suffix="b92e")

    assert first != second
    assert first.as_posix().endswith(
        "journal/operations/2026/08/20260807T073000Z-a81f.md"
    )
    assert first.is_file() and second.is_file()


def test_operation_change_is_immutable() -> None:
    change = OperationChange("tx-1", "2026-08-07T07:30:00Z", (), (), (), ())

    with pytest.raises(FrozenInstanceError):
        change.transaction_id = "changed"  # type: ignore[misc]


def test_existing_operation_is_never_overwritten(tmp_path: Path) -> None:
    change = OperationChange("tx-1", "2026-08-07T07:30:00Z", (), (), (), ())
    first = write_operation(tmp_path, change, suffix="a81f")
    original = first.read_bytes()

    second = write_operation(tmp_path, change, suffix="a81f")

    assert second != first
    assert first.read_bytes() == original
    assert validate_operation(second) == change


@pytest.mark.parametrize("suffix", ["abc", "A81F", "not-hex", "a81f.md"])
def test_operation_path_requires_lowercase_hex_suffix(
    tmp_path: Path, suffix: str
) -> None:
    change = OperationChange("tx-1", "2026-08-07T07:30:00Z", (), (), (), ())

    with pytest.raises(OperationError, match="suffix"):
        operation_path(tmp_path, change, suffix=suffix)


@pytest.mark.parametrize(
    "listed_path",
    ["../outside.md", "/absolute.md", r"concepts\windows.md", "hot.md"],
)
def test_render_operation_rejects_unsafe_listed_paths(listed_path: str) -> None:
    change = OperationChange("tx-1", "2026-08-07T07:30:00Z", (), (listed_path,), (), ())

    with pytest.raises(OperationError, match="page path"):
        render_operation(change)


def test_directory_swap_cannot_write_operation_outside_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    change = OperationChange("tx-1", "2026-08-07T07:30:00Z", (), (), (), ())
    original = operations._open_operation_parent

    def open_then_swap(root: Path, parts: tuple[str, ...]) -> int:
        descriptor = original(root, parts)
        month = vault / "journal/operations/2026/08"
        moved = month.with_name("08-moved")
        month.rename(moved)
        month.symlink_to(external, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(operations, "_open_operation_parent", open_then_swap)

    with pytest.raises(OperationError, match="changed"):
        write_operation(vault, change, suffix="a81f")

    assert not (external / "20260807T073000Z-a81f.md").exists()
    assert not (
        vault / "journal/operations/2026/08-moved/20260807T073000Z-a81f.md"
    ).exists()


@pytest.mark.parametrize("failure_call", [1, 2])
def test_sync_failure_removes_owned_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    change = OperationChange("tx-1", "2026-08-07T07:30:00Z", (), (), (), ())
    target = operation_path(tmp_path, change, suffix="a81f")
    target.parent.mkdir(parents=True)
    original = os.fsync
    calls = 0

    def fail_selected_sync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("simulated sync failure")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", fail_selected_sync)

    with pytest.raises(OperationError, match="write"):
        write_operation(tmp_path, change, suffix="a81f")

    assert not target.exists()

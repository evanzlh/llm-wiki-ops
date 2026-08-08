from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

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

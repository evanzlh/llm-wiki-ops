from __future__ import annotations

import pytest

from obsidian_wiki.skill_names import is_safe_skill_name


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("alpha-1", True),
        ("团队知识", True),
        ("e\u0301quipe", True),
        ("٣-data", True),
        ("_leading-separator", False),
        ("\u0301leading-mark", False),
        ("unsafe!", False),
        ("bad name", False),
        ("control\x01name", False),
        ("back\\slash", False),
        ("", False),
        (".", False),
        ("..", False),
        ("a/b", False),
        ("a\\b", False),
        ("line\nbreak", False),
    ),
)
def test_is_safe_skill_name_preserves_portable_semantics(
    name: str, expected: bool
) -> None:
    assert is_safe_skill_name(name) is expected

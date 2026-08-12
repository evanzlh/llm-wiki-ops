"""Shared validation for portable skill directory names."""

from __future__ import annotations

import unicodedata


def is_safe_skill_name(value: object) -> bool:
    """Accept exact Unicode letters/numbers plus marks and ``._-`` separators."""
    if type(value) is not str or not value or value in (".", ".."):
        return False
    if unicodedata.category(value[0])[:1] not in {"L", "N"}:
        return False
    return all(
        character in "._-" or unicodedata.category(character)[:1] in {"L", "M", "N"}
        for character in value
    )


__all__ = ["is_safe_skill_name"]

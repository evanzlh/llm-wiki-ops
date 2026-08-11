"""Strict managed-skill inventory schemas and canonical rendering."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Union

from . import IMPLEMENTATION_ID

SCHEMA_VERSION = 2
MIRROR_FORMAT = "full-copy-v1"
MANAGED_SKILLS_INVENTORY = ".obsidian-wiki/managed-skills.json"

_LEGACY_FIELDS = frozenset({"implementation", "skills", "skills_version"})
_V2_FIELDS = frozenset(
    {
        "implementation",
        "managed_skill_digests",
        "managed_skills",
        "mirror_format",
        "schema_version",
        "skills_version",
    }
)
_SAFE_SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SKILL_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _validate_skills_version(value: object) -> None:
    if type(value) is not str or not value:
        raise ValueError("skills_version must be a non-empty string")


def _validate_managed_skills(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ValueError("managed_skills must be a tuple of strings")
    if any(type(name) is not str for name in value):
        raise ValueError("managed_skills must contain only strings")
    if value != tuple(sorted(value)) or len(value) != len(set(value)):
        raise ValueError("managed_skills must be sorted and unique")
    for name in value:
        if _SAFE_SKILL_NAME.fullmatch(name) is None or name in (".", ".."):
            raise ValueError(f"unsafe skill name: {name!r}")
    return value


def _validate_digest_mapping(
    value: object, names: tuple[str, ...]
) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(  # noqa: TRY004 - schema violations share ValueError
            "managed_skill_digests must be a mapping"
        )
    copied = dict(value)
    if set(copied) != set(names):
        raise ValueError("managed_skill_digests keys must exactly match managed_skills")
    for name, digest in copied.items():
        if type(name) is not str or type(digest) is not str:
            raise ValueError("managed skill digest names and values must be strings")
        if _SKILL_DIGEST.fullmatch(digest) is None:
            raise ValueError(
                f"managed skill digest for {name!r} must be lowercase sha256: plus 64 hex digits"
            )
    return MappingProxyType(copied)


@dataclass(frozen=True)
class LegacyManagedSkillsInventory:
    skills_version: str
    managed_skills: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_skills_version(self.skills_version)
        _validate_managed_skills(self.managed_skills)


@dataclass(frozen=True)
class ManagedSkillsInventory:
    skills_version: str
    managed_skills: tuple[str, ...]
    managed_skill_digests: Mapping[str, str]
    schema_version: int = SCHEMA_VERSION
    mirror_format: str = MIRROR_FORMAT

    def __post_init__(self) -> None:
        _validate_skills_version(self.skills_version)
        names = _validate_managed_skills(self.managed_skills)
        if (
            type(self.schema_version) is not int
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValueError(f"schema_version must be exactly {SCHEMA_VERSION}")
        if type(self.mirror_format) is not str or self.mirror_format != MIRROR_FORMAT:
            raise ValueError(f"mirror_format must be exactly {MIRROR_FORMAT!r}")
        immutable_digests = _validate_digest_mapping(self.managed_skill_digests, names)
        object.__setattr__(self, "managed_skill_digests", immutable_digests)


Inventory = Union[ManagedSkillsInventory, LegacyManagedSkillsInventory]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"inventory JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _load_json_object(text: str) -> dict[str, Any]:
    if type(text) is not str:
        raise TypeError("inventory text must be a string")
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"managed skills inventory is malformed JSON: {exc}") from exc
    if type(payload) is not dict:
        raise ValueError("managed skills inventory must be a JSON object")
    return payload


def _parse_names(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise ValueError(f"{field} must be a list of strings")
    if any(type(name) is not str for name in value):
        raise ValueError(f"{field} must contain only strings")
    names = tuple(value)
    _validate_managed_skills(names)
    return names


def parse_inventory_text(text: str, *, allow_legacy: bool = False) -> Inventory:
    """Parse exact known schemas and reject every unknown field or format."""
    if type(allow_legacy) is not bool:
        raise TypeError("allow_legacy must be a bool")
    payload = _load_json_object(text)
    fields = frozenset(payload)

    if fields == _LEGACY_FIELDS:
        if not allow_legacy:
            raise ValueError(
                "legacy managed skills inventory requires allow_legacy=True"
            )
        if payload["implementation"] != IMPLEMENTATION_ID:
            raise ValueError("managed skills inventory has wrong implementation")
        return LegacyManagedSkillsInventory(
            skills_version=payload["skills_version"],
            managed_skills=_parse_names(payload["skills"], "skills"),
        )

    if fields != _V2_FIELDS:
        raise ValueError(
            "managed skills inventory fields do not match an exact known schema"
        )
    if payload["implementation"] != IMPLEMENTATION_ID:
        raise ValueError("managed skills inventory has wrong implementation")
    return ManagedSkillsInventory(
        skills_version=payload["skills_version"],
        managed_skills=_parse_names(payload["managed_skills"], "managed_skills"),
        managed_skill_digests=payload["managed_skill_digests"],
        schema_version=payload["schema_version"],
        mirror_format=payload["mirror_format"],
    )


def read_inventory(root: Path, *, allow_legacy: bool = False) -> Inventory:
    """Read one contained, single-link ordinary portable inventory file."""
    if not isinstance(root, Path):
        raise TypeError("inventory root must be a pathlib.Path")

    # Repository containment belongs to portable.py.  Import lazily so portable.py
    # can share this module's inventory path constant without an import cycle.
    from .portable import _read_single_link_ordinary_bytes

    path = root / MANAGED_SKILLS_INVENTORY
    content = _read_single_link_ordinary_bytes(root, path, "managed skills inventory")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"portable managed skills inventory is not valid UTF-8: {path}"
        ) from exc
    return parse_inventory_text(text, allow_legacy=allow_legacy)


def render_inventory(inventory: ManagedSkillsInventory) -> str:
    """Render sorted UTF-8 JSON with two-space indentation and one final newline."""
    if type(inventory) is not ManagedSkillsInventory:
        raise TypeError("render_inventory requires ManagedSkillsInventory")
    payload = {
        "implementation": IMPLEMENTATION_ID,
        "managed_skill_digests": dict(inventory.managed_skill_digests),
        "managed_skills": list(inventory.managed_skills),
        "mirror_format": inventory.mirror_format,
        "schema_version": inventory.schema_version,
        "skills_version": inventory.skills_version,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


__all__ = [
    "MANAGED_SKILLS_INVENTORY",
    "MIRROR_FORMAT",
    "SCHEMA_VERSION",
    "LegacyManagedSkillsInventory",
    "ManagedSkillsInventory",
    "parse_inventory_text",
    "read_inventory",
    "render_inventory",
]

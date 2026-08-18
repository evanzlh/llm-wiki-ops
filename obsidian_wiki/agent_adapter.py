"""Deterministically render the cross-agent external-repository adapter."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from .frontmatter import FrontmatterError, parse_frontmatter
from .safe_files import UnsafeVaultError, read_safe_file
from .skill_names import is_safe_skill_name
from .skill_trees import SkillCollection, SkillEntry, SkillTree
from . import skill_trees


ADAPTER_NAME = "llm-wiki-ops"
BUILTIN_CATALOG_START = "<!-- LLMWIKIOPS_BUILTIN_CATALOG_START -->"
BUILTIN_CATALOG_END = "<!-- LLMWIKIOPS_BUILTIN_CATALOG_END -->"

_ADAPTER_TEMPLATE = (
    Path(__file__).resolve().parent / "_data" / "adapter" / "SKILL.md.in"
)
_MAX_SKILL_DESCRIPTION_CHARS = 1024
_MAX_SKILL_BODY_LINES = 499


def _read_adapter_template() -> str:
    try:
        content = read_safe_file(_ADAPTER_TEMPLATE.parent, _ADAPTER_TEMPLATE)
    except UnsafeVaultError as exc:
        raise ValueError(f"adapter template is not a safe ordinary file: {exc}") from exc
    assert content is not None
    try:
        template = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("adapter template must be valid UTF-8") from exc
    if "\r" in template:
        raise ValueError("adapter template must use LF newlines")
    return template


def _validate_entry_path(entry: SkillEntry) -> None:
    if type(entry.path) is not str or not entry.path or "\\" in entry.path:
        raise ValueError("adapter skill collection contains an unsafe entry path")
    path = PurePosixPath(entry.path)
    if (
        path.is_absolute()
        or path.as_posix() != entry.path
        or any(part in {"", ".", ".."} for part in entry.path.split("/"))
    ):
        raise ValueError("adapter skill collection contains an unsafe entry path")
    if entry.kind not in {"directory", "file"}:
        raise ValueError("adapter skill collection contains an invalid entry kind")
    if type(entry.executable) is not bool or type(entry.content) is not bytes:
        raise ValueError("adapter skill collection contains an invalid entry")


def _validate_collection(collection: SkillCollection) -> None:
    if type(collection) is not SkillCollection or not collection.skills:
        raise ValueError("adapter skill collection must be a non-empty SkillCollection")
    if type(collection.skills) is not tuple:
        raise ValueError("adapter skill collection skills must be an immutable tuple")

    names: list[str] = []
    for skill in collection.skills:
        if type(skill) is not SkillTree:
            raise ValueError("adapter skill collection contains an invalid skill")
        if not is_safe_skill_name(skill.name):
            raise ValueError("adapter skill collection contains an unsafe skill name")
        if type(skill.description) is not str or not skill.description:
            raise ValueError("adapter skill collection skill description is required")
        if len(skill.description) > _MAX_SKILL_DESCRIPTION_CHARS:
            raise ValueError("adapter skill collection skill description is too long")
        if type(skill.entries) is not tuple or not skill.entries:
            raise ValueError("adapter skill collection skill entries are required")
        for entry in skill.entries:
            if type(entry) is not SkillEntry:
                raise ValueError("adapter skill collection contains an invalid entry")
            _validate_entry_path(entry)
        paths = tuple(entry.path for entry in skill.entries)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("adapter skill collection entries must be sorted and unique")

        captured = skill_trees._skill_tree_from_entries(
            Path("<adapter-skill-collection>"), skill.name, skill.entries
        )
        if captured.description != skill.description:
            raise ValueError(
                "adapter skill collection metadata description does not match its snapshot"
            )
        if captured.digest != skill.digest:
            raise ValueError("adapter skill collection digest does not match its snapshot")
        names.append(skill.name)

    if len(names) != len(set(names)):
        raise ValueError("adapter skill collection contains duplicate names")
    if names != sorted(names):
        raise ValueError("adapter skill collection names must be sorted")


def _validate_template(template: str) -> None:
    if template.count(BUILTIN_CATALOG_START) != 1:
        raise ValueError("adapter template must contain one catalog start marker")
    if template.count(BUILTIN_CATALOG_END) != 1:
        raise ValueError("adapter template must contain one catalog end marker")
    if template.index(BUILTIN_CATALOG_START) > template.index(BUILTIN_CATALOG_END):
        raise ValueError("adapter template catalog markers are out of order")
    placeholder = f"{BUILTIN_CATALOG_START}\n{BUILTIN_CATALOG_END}"
    if placeholder not in template:
        raise ValueError("adapter template must contain an empty catalog placeholder")
    try:
        metadata = parse_frontmatter(template)
    except FrontmatterError as exc:
        raise ValueError(f"adapter template frontmatter is invalid: {exc}") from exc
    if metadata.fields != {"name", "description"}:
        raise ValueError("adapter template frontmatter must contain only name and description")
    if metadata.scalars.get("name") != ADAPTER_NAME:
        raise ValueError("adapter template frontmatter name is invalid")
    description = metadata.scalars.get("description", "")
    if not description or len(description) > _MAX_SKILL_DESCRIPTION_CHARS:
        raise ValueError("adapter template frontmatter description is invalid")


def render_adapter_skill(collection: SkillCollection) -> str:
    """Return a byte-stable adapter containing only validated routing metadata."""
    _validate_collection(collection)
    template = _read_adapter_template()
    _validate_template(template)
    catalog = json.dumps(
        [
            {"name": skill.name, "description": skill.description}
            for skill in collection.skills
        ],
        ensure_ascii=False,
        indent=2,
    )
    replacement = f"{BUILTIN_CATALOG_START}\n{catalog}\n{BUILTIN_CATALOG_END}"
    rendered = template.replace(
        f"{BUILTIN_CATALOG_START}\n{BUILTIN_CATALOG_END}", replacement
    )
    if not rendered.endswith("\n"):
        rendered += "\n"
    if len(rendered.split("---\n", 2)[-1].splitlines()) > _MAX_SKILL_BODY_LINES:
        raise ValueError("rendered adapter skill body must be under 500 lines")
    return rendered


__all__ = [
    "ADAPTER_NAME",
    "BUILTIN_CATALOG_END",
    "BUILTIN_CATALOG_START",
    "render_adapter_skill",
]

"""Shared, dependency-free parsing for Markdown pages in a wiki graph."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_FIELD_RE = re.compile(r"^([A-Za-z_][\w-]*):", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
_MD_LINK_RE = re.compile(r"\[.*?\]\(([^)]+\.md[^)]*)\)")


@dataclass(frozen=True)
class PageGraphRecord:
    """Normalized graph data parsed from one Markdown page."""

    path: str
    node_id: str
    slug: str
    title: str
    summary: str
    fields: frozenset[str]
    links: tuple[str, ...]
    text: str


def slug(text: str) -> str:
    """Normalize a page-name component to the slug used by graph checks."""
    return text.strip().lower().replace(" ", "-")


def parse_frontmatter_values(frontmatter: str) -> dict[str, str]:
    """Extract scalar frontmatter values using lint's compatibility rules."""
    values: dict[str, str] = {}
    lines = frontmatter.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or ":" not in line or line.startswith((" ", "\t")):
            index += 1
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        value = raw.strip()
        if value in {">", ">-", "|", "|-"}:
            block: list[str] = []
            index += 1
            while index < len(lines):
                child = lines[index]
                if child.startswith(" ") or child.startswith("\t"):
                    block.append(child.strip())
                    index += 1
                    continue
                break
            values[key] = " ".join(part for part in block if part).strip()
            continue
        values[key] = value.strip("'\"")
        index += 1
    return values


def normalise_node_id(raw: str) -> str:
    """Normalize a wikilink, Markdown path, or page path to its graph node ID."""
    target = raw.strip().removeprefix("[[").removesuffix("]]")
    target = target.split("|", 1)[0].split("#", 1)[0].strip()
    if target.lower().endswith(".md"):
        target = target[:-3]
    return "/".join(slug(part) for part in target.strip("/").split("/") if part)


def parse_page_text(path: str, text: str) -> PageGraphRecord:
    """Parse a page's text into the normalized data used for graph checks."""
    relative_path = Path(path).as_posix()
    front_match = _FRONTMATTER_RE.match(text)
    frontmatter = front_match.group(1) if front_match else ""
    values = parse_frontmatter_values(frontmatter)

    links: list[str] = []
    for raw in _WIKILINK_RE.findall(text):
        target = slug(raw.split("/")[-1])
        if target:
            links.append(target)
    for href in _MD_LINK_RE.findall(text):
        target = slug(Path(href).stem)
        if target:
            links.append(target)

    page_path = Path(relative_path)
    return PageGraphRecord(
        path=relative_path,
        node_id=normalise_node_id(page_path.with_suffix("").as_posix()),
        slug=slug(page_path.stem),
        title=values.get("title", "").strip() or page_path.stem,
        summary=values.get("summary", "").strip(),
        fields=frozenset(_FIELD_RE.findall(frontmatter)),
        links=tuple(links),
        text=text,
    )


__all__ = [
    "PageGraphRecord",
    "normalise_node_id",
    "parse_frontmatter_values",
    "parse_page_text",
    "slug",
]

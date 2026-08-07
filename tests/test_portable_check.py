from __future__ import annotations

import pytest

from obsidian_wiki.frontmatter import FrontmatterError, parse_frontmatter


def test_parse_block_sources_and_required_fields() -> None:
    page = """---
title: Portable Repository
category: concepts
tags:
  - knowledge-management
sources:
  - sources/design/portable.md
  - sources/meetings/review.md
created: 2026-08-07
updated: 2026-08-07
---
# Portable Repository
"""
    parsed = parse_frontmatter(page)
    assert parsed.scalars["title"] == "Portable Repository"
    assert parsed.lists["sources"] == (
        "sources/design/portable.md",
        "sources/meetings/review.md",
    )


def test_parse_inline_sources() -> None:
    parsed = parse_frontmatter("---\ntitle: A\nsources: [sources/a.md, sources/b.md]\n---\n")
    assert parsed.lists["sources"] == ("sources/a.md", "sources/b.md")


def test_missing_frontmatter_is_rejected() -> None:
    with pytest.raises(FrontmatterError, match="frontmatter"):
        parse_frontmatter("# No metadata\n")

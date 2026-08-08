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


def test_parse_ignores_top_level_comments() -> None:
    parsed = parse_frontmatter(
        "---\n# This page was generated from authoritative sources.\ntitle: Portable Repository\n---\n"
    )
    assert parsed.scalars["title"] == "Portable Repository"


def test_parse_block_list_ignores_blank_and_comment_lines() -> None:
    page = """---
sources:
  # Primary design document.
  - sources/design/portable.md

  # Follow-up review.
  - sources/meetings/review.md
title: Portable Repository
---
"""
    parsed = parse_frontmatter(page)
    assert parsed.lists["sources"] == (
        "sources/design/portable.md",
        "sources/meetings/review.md",
    )
    assert parsed.scalars["title"] == "Portable Repository"


def test_parse_preserves_quoted_hashes_and_strips_trailing_comments() -> None:
    page = """---
title: "Portable # Repository" # Human-readable title.
sources:
  - 'sources/#draft.md' # Draft source.
tags: ["topic#portable", 'review # notes'] # Page tags.
---
"""
    parsed = parse_frontmatter(page)
    assert parsed.scalars["title"] == "Portable # Repository"
    assert parsed.lists["sources"] == ("sources/#draft.md",)
    assert parsed.lists["tags"] == ("topic#portable", "review # notes")


def test_parse_double_quoted_values_decodes_supported_escapes() -> None:
    parsed = parse_frontmatter(
        r'''---
title: "Portable \"Repository\""
aliases: ["Portable \"Repository\", v2", "sources\\windows\\page.md"]
---
'''
    )
    assert parsed.scalars["title"] == 'Portable "Repository"'
    assert parsed.lists["aliases"] == (
        'Portable "Repository", v2',
        r"sources\windows\page.md",
    )


@pytest.mark.parametrize("escape", [r"\x2Fetc/passwd", r"line\nbreak"])
def test_parse_rejects_unsupported_double_quoted_escapes(escape: str) -> None:
    with pytest.raises(FrontmatterError, match="unsupported.*escape"):
        parse_frontmatter(f'---\nsources: ["{escape}"]\n---\n')


def test_parse_single_quoted_values_use_doubled_apostrophes() -> None:
    parsed = parse_frontmatter(
        "---\ntitle: 'Owner''s # page' # trailing comment\naliases: ['owner''s, notes']\n---\n"
    )
    assert parsed.scalars["title"] == "Owner's # page"
    assert parsed.lists["aliases"] == ("owner's, notes",)


def test_parse_single_quoted_backslash_does_not_escape_apostrophe() -> None:
    with pytest.raises(FrontmatterError, match="quoted"):
        parse_frontmatter(
            r'''---
aliases: ['owner\'s, notes']
---
'''
        )


def test_parse_inline_list_preserves_quoted_comma() -> None:
    parsed = parse_frontmatter('---\naliases: ["Portable, Repository", short]\n---\n')
    assert parsed.lists["aliases"] == ("Portable, Repository", "short")


def test_parse_empty_inline_list() -> None:
    parsed = parse_frontmatter("---\ntitle: A\ntags: []\n---\n")
    assert parsed.lists["tags"] == ()


@pytest.mark.parametrize(
    "page",
    [
        "---\nsources: source.md\nsources:\n  - other.md\n---\n",
        "---\nsources:\n  - source.md\nsources: other.md\n---\n",
    ],
)
def test_duplicate_scalar_and_list_keys_are_rejected(page: str) -> None:
    with pytest.raises(FrontmatterError, match="duplicate"):
        parse_frontmatter(page)


@pytest.mark.parametrize("indent", [" ", "   ", "\t"])
def test_malformed_block_list_indentation_is_rejected(indent: str) -> None:
    page = f"---\nsources:\n{indent}- source.md\n---\n"
    with pytest.raises(FrontmatterError, match="malformed"):
        parse_frontmatter(page)


def test_nested_mapping_is_not_interpreted_as_frontmatter() -> None:
    with pytest.raises(FrontmatterError, match="malformed"):
        parse_frontmatter("---\nprovenance:\n  source: sources/a.md\n---\n")


@pytest.mark.parametrize(
    "page",
    [
        "---\ntags: [one, [two, three]]\n---\n",
        "---\ntags: [one, {name: two}]\n---\n",
        "---\nmetadata: {source: sources/a.md}\n---\n",
    ],
)
def test_nested_flow_collections_are_rejected(page: str) -> None:
    with pytest.raises(FrontmatterError, match="flow collection"):
        parse_frontmatter(page)


def test_plain_scalars_may_end_with_quote_characters() -> None:
    parsed = parse_frontmatter("---\ntitle: James' # comment\nmeasurement: 6\"\n---\n")
    assert parsed.scalars["title"] == "James'"
    assert parsed.scalars["measurement"] == '6"'


@pytest.mark.parametrize(
    "page",
    [
        '---\ntitle: "Portable Repository\n---\n',
        "---\nsources:\n  - 'sources/a.md\n---\n",
        '---\nsources: ["sources/a.md]\n---\n',
    ],
)
def test_unmatched_quotes_are_rejected(page: str) -> None:
    with pytest.raises(FrontmatterError, match="quote"):
        parse_frontmatter(page)


def test_missing_closing_delimiters_are_rejected() -> None:
    with pytest.raises(FrontmatterError, match="closing delimiter"):
        parse_frontmatter("---\ntitle: Portable Repository\n")
    with pytest.raises(FrontmatterError, match="list"):
        parse_frontmatter("---\nsources: [sources/a.md\n---\n")


def test_missing_frontmatter_is_rejected() -> None:
    with pytest.raises(FrontmatterError, match="frontmatter"):
        parse_frontmatter("# No metadata\n")

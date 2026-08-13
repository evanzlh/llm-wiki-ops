from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import obsidian_wiki.cli as cli
from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki import portable_check as portable_check_module
from obsidian_wiki.cli import skills_dir
from obsidian_wiki.config import ConfigError, load_portable_config
from obsidian_wiki.frontmatter import (
    FrontmatterError,
    Provenance,
    Relationship,
    parse_frontmatter,
    parse_relationships,
)
from obsidian_wiki.operations import (
    EMPTY_OPERATION_LOG,
    OperationChange,
    render_operation_log,
)
from obsidian_wiki.portable import (
    MANAGED_END,
    MANAGED_START,
    PROJECT_AGENT_DIRS,
    setup_portable_repo,
)
from obsidian_wiki.portable_check import CheckIssue, check_portable_repo
from obsidian_wiki.portable_manifest import ShardedManifest


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
    parsed = parse_frontmatter(
        "---\ntitle: A\nsources: [sources/a.md, sources/b.md]\n---\n"
    )
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
        r"""---
title: "Portable \"Repository\""
aliases: ["Portable \"Repository\", v2", "sources\\windows\\page.md"]
---
"""
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
            r"""---
aliases: ['owner\'s, notes']
---
"""
        )


def test_parse_inline_list_preserves_quoted_comma() -> None:
    parsed = parse_frontmatter('---\naliases: ["Portable, Repository", short]\n---\n')
    assert parsed.lists["aliases"] == ("Portable, Repository", "short")


def test_parse_empty_inline_list() -> None:
    parsed = parse_frontmatter("---\ntitle: A\ntags: []\n---\n")
    assert parsed.lists["tags"] == ()


def test_parse_provenance_block_in_any_field_order() -> None:
    parsed = parse_frontmatter(
        """---
title: A
provenance:
  ambiguous: 0.03
  extracted: 0.72 # directly supported
  inferred: "0.25"
---
"""
    )
    assert parsed.provenance == Provenance(
        extracted="0.72", inferred="0.25", ambiguous="0.03"
    )
    assert parsed.fields == frozenset({"title", "provenance"})


def test_parse_provenance_allows_quoted_colon_space_in_scalar() -> None:
    parsed = parse_frontmatter(
        '---\nprovenance:\n  extracted: "ratio: 0.72"\n  inferred: 0.25\n  ambiguous: 0.03\n---\n'
    )
    assert parsed.provenance == Provenance(
        extracted="ratio: 0.72", inferred="0.25", ambiguous="0.03"
    )


def test_parse_provenance_allows_quoted_unicode_whitespace_in_scalar() -> None:
    parsed = parse_frontmatter(
        '---\nprovenance:\n  extracted: "ratio:\u00a00.72\u2003units"\n  inferred: 0.25\n  ambiguous: 0.03\n---\n'
    )
    assert parsed.provenance == Provenance(
        extracted="ratio:\u00a00.72\u2003units", inferred="0.25", ambiguous="0.03"
    )


@pytest.mark.parametrize(
    "leaf",
    [
        "|",
        "|-",
        ">",
        ">-",
        "@bad",
        "%bad",
        "`bad`",
        ",bad",
        "]bad",
        "}bad",
        "- thing",
        "? thing",
        ": thing",
        "bad\x01value",
        "bad\x80value",
        '"unterminated',
    ],
)
def test_provenance_leaves_reject_yaml_control_syntax(leaf: str) -> None:
    page = (
        "---\nprovenance:\n"
        f"  extracted: {leaf}\n"
        "  inferred: 0.25\n  ambiguous: 0.03\n---\n"
    )

    with pytest.raises(
        FrontmatterError,
        match="provenance.*(?:scalar|syntax|indicator|control|quote|header|unsupported)",
    ):
        parse_frontmatter(page)


def test_provenance_leaves_preserve_quoted_and_valid_plain_scalars() -> None:
    parsed = parse_frontmatter(
        '''---
provenance:
  extracted: "@bad"
  inferred: -0.25
  ambiguous: :value
---
'''
    )

    assert parsed.provenance == Provenance(
        extracted="@bad", inferred="-0.25", ambiguous=":value"
    )


@pytest.mark.parametrize(
    "body",
    [
        "provenance:\n  extracted: bad{control}value\n  inferred: 0.25\n  ambiguous: 0.03",
        'provenance:\n  extracted: "bad{control}value"\n  inferred: 0.25\n  ambiguous: 0.03',
        "provenance:\n  extracted: 0.72 # invalid{control}\n  inferred: 0.25\n  ambiguous: 0.03",
        "provenance: # invalid{control}\n  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03",
        "provenance:\n  # invalid{control}\n  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03",
        "provenance:\n# invalid{control}\n  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03",
    ],
    ids=[
        "plain-leaf",
        "quoted-leaf",
        "trailing-comment",
        "header-comment",
        "full-line-comment",
        "root-comment",
    ],
)
@pytest.mark.parametrize(
    "control",
    [
        pytest.param("\x01", id="c0"),
        pytest.param("\x0b", id="c0-line-separator"),
        pytest.param("\x80", id="c1"),
        pytest.param("\x85", id="c1-line-separator"),
        pytest.param("\x7f", id="del"),
    ],
)
def test_provenance_values_and_comments_reject_control_characters(
    body: str, control: str
) -> None:
    with pytest.raises(FrontmatterError, match="provenance.*control"):
        parse_frontmatter(f"---\n{body.format(control=control)}\n---\n")


@pytest.mark.parametrize(
    "control",
    [
        pytest.param("\x0b", id="vertical-tab"),
        pytest.param("\x0c", id="form-feed"),
        pytest.param("\x85", id="next-line"),
        pytest.param("\x7f", id="delete"),
    ],
)
@pytest.mark.parametrize(
    "surrounding_whitespace",
    [pytest.param("", id="ascii"), pytest.param("\u00a0\u2003", id="unicode")],
)
def test_provenance_blocks_reject_control_only_whitespace_lines(
    control: str, surrounding_whitespace: str
) -> None:
    page = (
        "---\nprovenance:\n"
        f"  {surrounding_whitespace}{control}\n"
        "  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n"
    )

    with pytest.raises(FrontmatterError, match="provenance.*control"):
        parse_frontmatter(page)


@pytest.mark.parametrize(
    "margin",
    [
        pytest.param("\t", id="tab"),
        pytest.param("\u00a0", id="non-breaking-space"),
        pytest.param("\u2003", id="em-space"),
    ],
)
def test_provenance_header_rejects_structural_whitespace(margin: str) -> None:
    page = (
        f"---\nprovenance:{margin}\n"
        "  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n"
    )

    with pytest.raises(
        FrontmatterError, match="provenance.*(?:delimiter|tab|whitespace)"
    ):
        parse_frontmatter(page)


@pytest.mark.parametrize(
    "key",
    [
        '"provenance"',
        "'provenance'",
        r'"\x70rovenance"',
        r'"\u0070rovenance"',
        "!!str provenance",
        "&key provenance",
        "!!str &key provenance",
        "!<tag:yaml.org,2002:str> provenance",
    ],
)
def test_provenance_key_rejects_unsupported_reserved_equivalents(key: str) -> None:
    with pytest.raises(
        FrontmatterError,
        match="provenance.*(?:key|quoted|tag|anchor|reserved|unsupported)",
    ):
        parse_frontmatter(f"---\n{key}: []\n---\n")


@pytest.mark.parametrize(
    "equivalent",
    [
        '"provenance"',
        "'provenance'",
        r'"\x70rovenance"',
        "!!str provenance",
        "&key provenance",
        "!<tag:yaml.org,2002:str> provenance",
    ],
)
def test_provenance_key_rejects_reserved_equivalent_duplicate(
    equivalent: str,
) -> None:
    page = (
        f"---\n{equivalent}: []\nprovenance:\n"
        "  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n"
    )

    with pytest.raises(FrontmatterError, match="provenance.*(?:key|reserved)"):
        parse_frontmatter(page)


def test_provenance_key_ignores_unrelated_generic_keys() -> None:
    page = '''---
my-provenance: ok
provenance-note: retained
owner's-provenance: literal
measurement"provenance: literal
-meta: ok
provenance:
  extracted: 0.72
  inferred: 0.25
  ambiguous: 0.03
---
'''

    parsed = parse_frontmatter(page)

    assert parsed.provenance == Provenance(
        extracted="0.72", inferred="0.25", ambiguous="0.03"
    )
    assert parsed.scalars == {
        "my-provenance": "ok",
        "provenance-note": "retained",
        "owner's-provenance": "literal",
        'measurement"provenance': "literal",
        "-meta": "ok",
    }


def test_parse_generic_scalars_and_lists_still_allow_colon_space() -> None:
    parsed = parse_frontmatter(
        "---\ntitle: ratio: 0.72\nsources:\n  - ratio: 0.72\n---\n"
    )
    assert parsed.scalars["title"] == "ratio: 0.72"
    assert parsed.lists["sources"] == ("ratio: 0.72",)


@pytest.mark.parametrize("key", ["owner's-title", 'measurement"label'])
def test_parse_plain_keys_preserve_literal_quotes(key: str) -> None:
    parsed = parse_frontmatter(f"---\n{key}: value\n---\n")

    assert parsed.scalars[key] == "value"


def test_parse_relationships_accepts_compact_and_expanded_items_in_any_field_order() -> None:
    parsed = parse_frontmatter(
        '''---
relationships:
  - type: uses
    target: "[[concepts/attention]]"
  - # expanded form
    target: '[[concepts/lstm]]' # supported target
    type: contradicts
---
'''
    )

    assert parsed.relationships == (
        Relationship(target="[[concepts/attention]]", type="uses"),
        Relationship(target="[[concepts/lstm]]", type="contradicts"),
    )
    assert parsed.fields == frozenset({"relationships"})


def test_parse_relationships_empty_inline_list_is_present() -> None:
    parsed = parse_frontmatter("---\nrelationships: [] # comment\n---\n")

    assert parsed.relationships == ()
    assert parsed.fields == frozenset({"relationships"})


def test_relationship_block_preserves_hyphenated_top_level_field() -> None:
    page = '''---
relationships:
  - target: "[[concepts/attention]]"
    type: uses
-meta: ok
---
'''

    parsed = parse_frontmatter(page)
    expected = (Relationship(target="[[concepts/attention]]", type="uses"),)

    assert parsed.relationships == expected
    assert parsed.scalars["-meta"] == "ok"
    assert parse_relationships(page) == expected


@pytest.mark.parametrize(
    "leaf",
    [
        "|",
        "|-",
        ">",
        ">-",
        "@bad",
        "%bad",
        "`bad`",
        ",bad",
        "]bad",
        "}bad",
        "- thing",
        "? thing",
        ": thing",
        "bad\x01value",
        "bad\x80value",
        '"unterminated',
    ],
)
@pytest.mark.parametrize(
    "parser",
    [parse_frontmatter, parse_relationships],
    ids=["full", "relationships-only"],
)
def test_relationship_leaves_reject_yaml_control_syntax(
    leaf: str, parser: Callable[[str], object]
) -> None:
    page = f"---\nrelationships:\n  - target: {leaf}\n    type: uses\n---\n"

    with pytest.raises(
        FrontmatterError,
        match="relationships.*(?:scalar|syntax|indicator|control|quote|header|unsupported)",
    ):
        parser(page)


def test_relationship_leaves_preserve_quoted_and_valid_plain_scalars() -> None:
    page = '''---
relationships:
  - target: "@bad"
    type: "ratio: value\u00a0with\u2003Unicode"
  - target: -thing
    type: ?thing
  - target: :thing
    type: uses
---
'''
    expected = (
        Relationship(target="@bad", type="ratio: value\u00a0with\u2003Unicode"),
        Relationship(target="-thing", type="?thing"),
        Relationship(target=":thing", type="uses"),
    )

    assert parse_frontmatter(page).relationships == expected
    assert parse_relationships(page) == expected


@pytest.mark.parametrize(
    "key",
    [
        "\trelationships",
        "\u00a0relationships",
        "relationships\t",
        "relationships\u00a0",
        '"relationships"',
        "'relationships'",
        "!!str relationships",
        "&key relationships",
        "!!str &key relationships",
        "&key !!str relationships",
        r'"\x72elationships"',
        r'"\u0072elationships"',
        "!<tag:yaml.org,2002:str> relationships",
    ],
)
@pytest.mark.parametrize(
    "parser",
    [parse_frontmatter, parse_relationships],
    ids=["full", "relationships-only"],
)
def test_relationship_key_rejects_unsupported_reserved_equivalents(
    key: str, parser: Callable[[str], object]
) -> None:
    with pytest.raises(
        FrontmatterError,
        match="relationships.*(?:key|whitespace|quoted|tag|anchor|reserved|unsupported)",
    ):
        parser(f"---\n{key}: []\n---\n")


@pytest.mark.parametrize(
    "equivalent",
    [
        '"relationships"',
        "'relationships'",
        "!!str relationships",
        "&key relationships",
        r'"\x72elationships"',
        "!<tag:yaml.org,2002:str> relationships",
    ],
)
@pytest.mark.parametrize(
    "parser",
    [parse_frontmatter, parse_relationships],
    ids=["full", "relationships-only"],
)
def test_relationship_key_rejects_reserved_equivalent_duplicate(
    equivalent: str, parser: Callable[[str], object]
) -> None:
    page = f"---\nrelationships: []\n{equivalent}: []\n---\n"

    with pytest.raises(FrontmatterError, match="relationships.*(?:key|reserved)"):
        parser(page)


def test_relationship_key_ignores_unrelated_generic_keys() -> None:
    page = r'''---
my-relationships: ok
"\x72elationship": []
"\u0072elationship": []
!!str relationship: []
!<tag:yaml.org,2002:str> relationship: []
relationships:
  - target: "[[concepts/attention]]"
    type: uses
-meta: ok
---
'''
    expected = (Relationship(target="[[concepts/attention]]", type="uses"),)
    parsed = parse_frontmatter(page)

    assert parsed.relationships == expected
    assert parsed.scalars["my-relationships"] == "ok"
    assert parsed.scalars["-meta"] == "ok"
    assert parsed.lists[r'"\x72elationship"'] == ()
    assert parsed.lists[r'"\u0072elationship"'] == ()
    assert parsed.lists["!!str relationship"] == ()
    assert parsed.lists["!<tag:yaml.org,2002:str> relationship"] == ()
    assert parse_relationships(page) == expected


def test_relationship_key_rejects_root_indentation_in_compatibility_parser() -> None:
    page = "---\n relationships: []\n---\n"

    with pytest.raises(FrontmatterError, match="relationships.*(?:indent|key|root)"):
        parse_relationships(page)


def test_relationship_key_rejects_alias_in_compatibility_parser() -> None:
    page = "---\nanchor: &rel relationships\n*rel: []\n---\n"

    with pytest.raises(FrontmatterError, match="relationships.*alias"):
        parse_relationships(page)


def test_relationship_key_rejects_alias_duplicate_in_compatibility_parser() -> None:
    page = (
        "---\nanchor: &rel relationships\nrelationships: []\n*rel: []\n---\n"
    )

    with pytest.raises(FrontmatterError, match="relationships.*(?:alias|duplicate)"):
        parse_relationships(page)


def test_relationship_key_rejects_explicit_form_in_compatibility_parser() -> None:
    page = "---\n? relationships\n: []\n---\n"

    with pytest.raises(FrontmatterError, match="relationships.*explicit"):
        parse_relationships(page)


@pytest.mark.parametrize(
    "body",
    [
        "relationships:\n  - target: one # invalid{control}\n    type: uses",
        "relationships:\n  # invalid{control}\n  - target: one\n    type: uses",
        "relationships:\n# invalid{control}\n  - target: one\n    type: uses",
        "relationships: [] # invalid{control}",
        "relationships:\n  - # invalid{control}\n    target: one\n    type: uses",
    ],
    ids=[
        "field-trailing",
        "full-line",
        "root-full-line",
        "inline-list",
        "item-marker",
    ],
)
@pytest.mark.parametrize(
    "control",
    [
        pytest.param("\x01", id="c0"),
        pytest.param("\x0b", id="c0-line-separator"),
        pytest.param("\x80", id="c1"),
        pytest.param("\x85", id="c1-line-separator"),
        pytest.param("\x7f", id="del"),
    ],
)
@pytest.mark.parametrize(
    "parser",
    [parse_frontmatter, parse_relationships],
    ids=["full", "relationships-only"],
)
def test_relationship_comments_reject_control_characters(
    body: str,
    control: str,
    parser: Callable[[str], object],
) -> None:
    page = f"---\n{body.format(control=control)}\n---\n"

    with pytest.raises(FrontmatterError, match="relationships.*control"):
        parser(page)


def test_relationship_parser_ignores_controls_in_unrelated_legacy_content() -> None:
    page = (
        "---\nsummary: legacy # invalid\x01\x0b\x80\x85\x7f\n"
        "details: >-\n  # legacy\x01\x0b\x80\x85\x7f\nrelationships: []\n---\n"
    )

    assert parse_relationships(page) == ()


def test_relationship_parser_ignores_unrelated_nested_legacy_mapping() -> None:
    page = "---\nmetadata:\n  relationships: []\n---\n"

    assert parse_relationships(page) is None


@pytest.mark.parametrize(
    "intervening",
    [
        pytest.param("\n", id="blank"),
        pytest.param("# root comment\n", id="root-comment"),
        pytest.param("\n# root comment\n\n", id="blank-and-root-comment"),
    ],
)
def test_relationship_parser_preserves_unrelated_nested_scope_across_comments(
    intervening: str,
) -> None:
    page = (
        "---\nmetadata:\n  value: legacy\n"
        f"{intervening}"
        "  relationships: []\n---\n"
    )

    assert parse_relationships(page) is None


@pytest.mark.parametrize(
    "control",
    [
        pytest.param("\x0b", id="vertical-tab"),
        pytest.param("\x0c", id="form-feed"),
        pytest.param("\x85", id="next-line"),
        pytest.param("\x7f", id="delete"),
    ],
)
@pytest.mark.parametrize(
    "surrounding_whitespace",
    [pytest.param("", id="ascii"), pytest.param("\u00a0\u2003", id="unicode")],
)
@pytest.mark.parametrize(
    "parser",
    [parse_frontmatter, parse_relationships],
    ids=["full", "relationships-only"],
)
def test_relationship_blocks_reject_control_only_whitespace_lines(
    control: str,
    surrounding_whitespace: str,
    parser: Callable[[str], object],
) -> None:
    page = (
        "---\nrelationships:\n"
        f"  {surrounding_whitespace}{control}\n"
        "  - target: one\n    type: uses\n---\n"
    )

    with pytest.raises(FrontmatterError, match="relationships.*control"):
        parser(page)


@pytest.mark.parametrize(
    "page",
    [
        """---
provenance:
  extracted: 0.72
  inferred: 0.25
  ambiguous: 0.03
provenance:
  extracted: 0.8
  inferred: 0.1
  ambiguous: 0.1
---
""",
        """---
relationships: []
relationships:
  - target: "[[concepts/attention]]"
    type: uses
---
""",
    ],
)
def test_parse_nested_fields_reject_duplicate_top_level_keys(page: str) -> None:
    with pytest.raises(FrontmatterError, match="duplicate"):
        parse_frontmatter(page)


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (
            'relationships:\n  - target: "[[concepts/attention]]"',
            "relationships.*missing.*type",
        ),
        (
            'relationships:\n  - target: "[[concepts/attention]]"\n    type: uses\n    weight: high',
            "relationships.*unknown.*weight",
        ),
        (
            'relationships:\n  - target: one\n    target: two\n    type: uses',
            "relationships.*duplicate.*target",
        ),
        (
            'relationships:\n  - target: one\n    type:',
            "relationships.*empty.*type",
        ),
        (
            'relationships:\n   - target: one\n    type: uses',
            "relationships.*indent",
        ),
        (
            'relationships:\n\t- target: one\n    type: uses',
            "relationships.*tab",
        ),
        (
            'relationships:\n\u00a0\u00a0- target: one\n    type: uses',
            "relationships.*whitespace",
        ),
        (
            'relationships: [{target: one, type: uses}]',
            "relationships.*inline",
        ),
        (
            'relationships:\n  - target: &item one\n    type: uses',
            "relationships.*(?:tag|anchor|alias)",
        ),
        (
            'relationships:\n  - target: *item\n    type: uses',
            "relationships.*(?:tag|anchor|alias)",
        ),
        (
            'relationships:\n  - target: !!str one\n    type: uses',
            "relationships.*(?:tag|anchor|alias)",
        ),
        (
            'relationships:\n  - target: [one]\n    type: uses',
            "relationships.*flow",
        ),
        (
            'relationships:\n  - target: {name: one}\n    type: uses',
            "relationships.*flow",
        ),
        (
            'relationships:\n  - target:one\n    type: uses',
            "relationships.*delimiter",
        ),
        (
            'relationships:\n  - target:\tone\n    type: uses',
            "relationships.*(?:delimiter|tab)",
        ),
        (
            'relationships:\n  - target: \tone\n    type: uses',
            "relationships.*tab",
        ),
        (
            'relationships:\n  - target: one: nested\n    type: uses',
            "relationships.*mapping delimiter",
        ),
        (
            'relationships:\n  - target: one:\tnested\n    type: uses',
            "relationships.*mapping delimiter",
        ),
        (
            'relationships:\n  - target: one:\n    type: uses',
            "relationships.*mapping delimiter",
        ),
        (
            'relationships:\n    target: one\n    type: uses',
            "relationships.*(?:continuation|item)",
        ),
        (
            'relationships:\n  -target: one\n    type: uses',
            "relationships.*item",
        ),
        (
            'relationships:\n  - target: one\n      type: uses',
            "relationships.*indent",
        ),
    ],
)
def test_parse_relationships_rejects_invalid_structure(body: str, match: str) -> None:
    with pytest.raises(FrontmatterError, match=match):
        parse_frontmatter(f"---\n{body}\n---\n")


def test_parse_relationships_preserves_quoted_colons_and_unicode_whitespace() -> None:
    parsed = parse_frontmatter(
        '---\nrelationships:\n  - target: "topic: detail\u00a0and\u2003more"\n'
        "    type: 'uses: strongly\u00a0today'\n---\n"
    )

    assert parsed.relationships == (
        Relationship(
            target="topic: detail\u00a0and\u2003more",
            type="uses: strongly\u00a0today",
        ),
    )


def test_parse_relationships_entry_point_ignores_unrelated_block_scalar() -> None:
    page = '''---
summary: >-
  This is deliberately outside the restricted parser grammar.
  It may contain: mapping-like text.
  relationships: []
relationships:
  - target: "[[concepts/attention]]"
    type: uses
---
'''

    assert parse_relationships(page) == (
        Relationship(target="[[concepts/attention]]", type="uses"),
    )


def test_parse_relationships_entry_point_distinguishes_absent_and_empty() -> None:
    assert parse_relationships("---\ntitle: A\n---\n") is None
    assert parse_relationships("---\nrelationships: [] # comment\n---\n") == ()


@pytest.mark.parametrize(
    "page",
    [
        "---\nrelationships: []\nrelationships: []\n---\n",
        "---\nrelationships: null\n---\n",
        "---\nrelationships:\n  - target: one\n---\n",
        "---\nrelationships: []\n  - target: one\n    type: uses\n---\n",
    ],
)
def test_parse_relationships_entry_point_rejects_invalid_relationships(
    page: str,
) -> None:
    with pytest.raises(FrontmatterError, match="relationships|duplicate"):
        parse_relationships(page)


@pytest.mark.parametrize(
    "page",
    [
        "---\nrelationships:\n---\n",
        "---\nrelationships:\n  # none\n---\n",
        '---\nrelationships:\n- target: "[[a]]"\n  type: uses\n---\n',
        '---\nrelationships:\n-\ttarget: "[[a]]"\n  type: uses\n---\n',
        '---\nrelationships:\n-\u00a0target: "[[a]]"\n  type: uses\n---\n',
        '''---
relationships:
  - target: "[[a]]"
    type: uses
- target: "[[b]]"
  type: contradicts
---
''',
    ],
    ids=[
        "bare",
        "comment-only",
        "zero-indented-first",
        "zero-indented-tab-marker",
        "zero-indented-unicode-marker",
        "zero-indented-extra",
    ],
)
@pytest.mark.parametrize(
    "parser",
    [parse_frontmatter, parse_relationships],
    ids=["full", "relationships-only"],
)
def test_relationship_blocks_reject_empty_or_zero_indented_items(
    page: str, parser: Callable[[str], object]
) -> None:
    with pytest.raises(
        FrontmatterError,
        match="relationships.*(?:malformed|list|indent|item)",
    ):
        parser(page)


@pytest.mark.parametrize(
    ("page", "match"),
    [
        (
            "---\nprovenance:\n  extracted: 0.72\n  inferred: 0.25\n---\n",
            "provenance.*missing.*ambiguous",
        ),
        (
            "---\nprovenance:\n  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n  source: x\n---\n",
            "provenance.*unknown",
        ),
        (
            "---\nprovenance:\n  extracted: 0.72\n  extracted: 0.25\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*duplicate.*extracted",
        ),
        (
            "---\nprovenance:\n  extracted:\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*empty.*extracted",
        ),
        (
            "---\nprovenance:\n   extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*indent",
        ),
        (
            "---\nprovenance:\n\textracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*tab",
        ),
        (
            "---\nprovenance:\n  extracted: {value: 1.0}\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "flow collection",
        ),
        (
            "---\nprovenance:\n  extracted: &value 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "YAML.*tag.*anchor.*alias",
        ),
        (
            "---\nprovenance:\n  extracted: *value\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "YAML.*tag.*anchor.*alias",
        ),
        (
            "---\nprovenance:\n  extracted:0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar)",
        ),
        (
            "---\nprovenance:\n  extracted:\t0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar)",
        ),
        (
            "---\nprovenance:\n  extracted: \t0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|tab)",
        ),
        (
            "---\nprovenance:\n  extracted\t: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|tab)",
        ),
        (
            "---\nprovenance:\n  extracted:extra: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar)",
        ),
        (
            "---\nprovenance:\n  extracted: extra: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar)",
        ),
        (
            "---\nprovenance:\n  extracted: extra:\t0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|tab)",
        ),
        (
            "---\nprovenance:\n  extracted: extra:\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|whitespace)",
        ),
        (
            "---\nprovenance:\n  extracted: extra: # nested empty\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|whitespace)",
        ),
        (
            "---\nprovenance:\n  extracted\u00a0: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|whitespace)",
        ),
        (
            "---\nprovenance:\n  extracted: \u00a00.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|whitespace)",
        ),
        (
            "---\nprovenance:\n  extracted\u2003: 0.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|whitespace)",
        ),
        (
            "---\nprovenance:\n  extracted: \u20030.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|whitespace)",
        ),
        (
            "---\nprovenance:\n  extracted: extra:\u00a00.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|whitespace)",
        ),
        (
            "---\nprovenance:\n  extracted: extra:\u20030.72\n  inferred: 0.25\n  ambiguous: 0.03\n---\n",
            "provenance.*(?:malformed|delimiter|scalar|whitespace)",
        ),
    ],
)
def test_parse_provenance_rejects_invalid_structure(page: str, match: str) -> None:
    with pytest.raises(FrontmatterError, match=match):
        parse_frontmatter(page)


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


def test_arbitrary_nested_mapping_is_not_interpreted_as_frontmatter() -> None:
    with pytest.raises(FrontmatterError, match="malformed"):
        parse_frontmatter("---\nmetadata:\n  source: sources/a.md\n---\n")


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


@pytest.mark.parametrize(
    "item",
    [
        "!!str /etc/passwd",
        "&path /etc/passwd",
        "*path",
    ],
)
def test_unquoted_yaml_node_indicators_are_rejected(item: str) -> None:
    with pytest.raises(FrontmatterError, match="YAML.*tag.*anchor.*alias"):
        parse_frontmatter(f"---\nsources: [{item}]\n---\n")


def test_quoted_yaml_node_indicators_remain_literal_strings() -> None:
    parsed = parse_frontmatter(
        '---\nsources: ["!!str /etc/passwd", \'&path /etc/passwd\', "*path"]\n---\n'
    )
    assert parsed.lists["sources"] == (
        "!!str /etc/passwd",
        "&path /etc/passwd",
        "*path",
    )


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


def valid_repo(tmp_path: Path, name: str = "knowledge"):
    root = tmp_path / name
    setup_portable_repo(root, version=__version__, source_skills=skills_dir())
    config_path = root / ".obsidian-wiki/config.toml"
    config = load_portable_config(
        config_path,
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    (config.vault / "log.md").write_text(EMPTY_OPERATION_LOG, encoding="utf-8")
    (config.vault / "hot.md").write_text("# Hot\n", encoding="utf-8")
    source = root / "sources/a.md"
    source.write_text("authoritative source", encoding="utf-8")
    page = root / "wiki/concepts/a.md"
    page.write_text(
        """---
title: A
category: concepts
tags:
  - example
sources:
  - sources/a.md
created: 2026-08-07
updated: 2026-08-07
summary: A compiled example.
---
# A
""",
        encoding="utf-8",
    )
    store = ShardedManifest(config)
    store.upsert(
        source,
        pages=["concepts/a.md"],
        compiled_at="2026-08-07T00:00:00Z",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "add", "."], check=True, capture_output=True
    )
    return root, config, source, page, store.entry_path("sources/a.md")


def issue_codes(report: dict[str, object]) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


def issues_with_code(report: dict[str, object], code: str) -> list[dict[str, str]]:
    return [issue for issue in report["issues"] if issue["code"] == code]


def test_check_issue_contract_is_exact() -> None:
    issue = CheckIssue("example", ".", "message", "warning")
    assert (issue.code, issue.path, issue.message, issue.severity) == (
        "example",
        ".",
        "message",
        "warning",
    )


def test_valid_portable_repo_passes(tmp_path: Path) -> None:
    _, config, _, _, _ = valid_repo(tmp_path)

    assert check_portable_repo(config) == {
        "status": "pass",
        "errors": 0,
        "warnings": 0,
        "issues": [],
    }


@pytest.mark.parametrize("name", ["_archives", "_raw", "_readouts", "_staging"])
@pytest.mark.parametrize("kind", ["directory", "file", "dangling-symlink"])
def test_check_rejects_unsupported_personal_vault_artifacts(
    tmp_path: Path, name: str, kind: str
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    artifact = root / "wiki" / name
    if artifact.is_dir() and not artifact.is_symlink():
        shutil.rmtree(artifact)
    elif artifact.exists() or artifact.is_symlink():
        artifact.unlink()
    if kind == "directory":
        artifact.mkdir()
    elif kind == "file":
        artifact.write_text("personal artifact\n", encoding="utf-8")
    else:
        artifact.symlink_to(root / "missing-personal-artifact")

    report = check_portable_repo(config)

    assert {
        "code": "unsupported-personal-artifact",
        "path": f"wiki/{name}",
        "message": "Personal vault artifact is not supported",
        "severity": "error",
    } in report["issues"]


def test_valid_portable_repo_accepts_supported_nested_frontmatter(
    tmp_path: Path,
) -> None:
    _, config, _, page, _ = valid_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "---\n# A\n",
            '''provenance:
  extracted: 0.72
  inferred: 0.25
  ambiguous: 0.03
relationships:
  - target: "[[concepts/a]]"
    type: related-to
---
# A
''',
        ),
        encoding="utf-8",
    )

    assert check_portable_repo(config) == {
        "status": "pass",
        "errors": 0,
        "warnings": 0,
        "issues": [],
    }


@pytest.mark.parametrize(
    "provenance",
    [
        "provenance:\n  extracted: |\n  inferred: 0.25\n  ambiguous: 0.03",
        '"provenance": []\nprovenance:\n  extracted: 0.72\n  inferred: 0.25\n  ambiguous: 0.03',
    ],
    ids=["block-scalar", "reserved-key"],
)
def test_portable_check_rejects_unsafe_provenance_frontmatter(
    tmp_path: Path, provenance: str
) -> None:
    _, config, _, page, _ = valid_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "---\n", f"---\n{provenance}\n", 1
        ),
        encoding="utf-8",
    )

    assert "frontmatter-invalid" in issue_codes(check_portable_repo(config))


def test_check_is_read_only(tmp_path: Path) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)

    def snapshot() -> tuple[tuple[str, str, bytes | str], ...]:
        result: list[tuple[str, str, bytes | str]] = []
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if relative.parts[0] == ".git":
                continue
            if path.is_symlink():
                result.append((relative.as_posix(), "symlink", os.readlink(path)))
            elif path.is_file():
                result.append((relative.as_posix(), "file", path.read_bytes()))
            elif path.is_dir():
                result.append((relative.as_posix(), "directory", b""))
        return tuple(result)

    before = snapshot()
    check_portable_repo(config)
    assert snapshot() == before


def test_changed_source_is_an_error(tmp_path: Path) -> None:
    _, config, source, _, _ = valid_repo(tmp_path)
    source.write_text("changed", encoding="utf-8")

    assert "source-stale" in issue_codes(check_portable_repo(config))


def test_new_and_orphaned_sources_are_errors(tmp_path: Path) -> None:
    root, config, source, _, _ = valid_repo(tmp_path)
    (root / "sources/new.md").write_text("new", encoding="utf-8")
    source.unlink()

    codes = issue_codes(check_portable_repo(config))
    assert {"source-new", "source-orphaned"} <= codes


def test_git_lfs_pointer_replaces_stale_source_error(tmp_path: Path) -> None:
    _, config, source, _, _ = valid_repo(tmp_path)
    source.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0123456789abcdef\n"
        "size 42\n",
        encoding="utf-8",
    )

    report = check_portable_repo(config)
    assert "unsupported-git-lfs-pointer" in issue_codes(report)
    assert "source-stale" not in issue_codes(report)


def test_absolute_page_source_is_an_error(tmp_path: Path) -> None:
    _, config, _, page, _ = valid_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace("sources/a.md", "/tmp/a.md"),
        encoding="utf-8",
    )

    assert "absolute-page-source" in issue_codes(check_portable_repo(config))


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("other/a.md", "page-source-outside-root"),
        ("sources/missing.md", "page-source-unknown"),
    ],
)
def test_page_sources_must_be_known_ids_below_configured_root(
    tmp_path: Path, source_id: str, expected: str
) -> None:
    _, config, _, page, _ = valid_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace("sources/a.md", source_id),
        encoding="utf-8",
    )

    assert expected in issue_codes(check_portable_repo(config))


def test_missing_manifest_page_is_an_error(tmp_path: Path) -> None:
    _, config, _, _, entry_path = valid_repo(tmp_path)
    entry_path.write_text(
        entry_path.read_text(encoding="utf-8").replace(
            "concepts/a.md", "concepts/missing.md"
        ),
        encoding="utf-8",
    )

    assert "manifest-page-missing" in issue_codes(check_portable_repo(config))


def test_manifest_to_page_edge_must_be_declared_by_page(tmp_path: Path) -> None:
    _, config, _, page, _ = valid_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "sources:\n  - sources/a.md", "sources: []"
        ),
        encoding="utf-8",
    )

    assert "manifest-page-source-missing" in issue_codes(check_portable_repo(config))


def test_manifest_legacy_operation_page_is_invalid_without_inspection(
    tmp_path: Path,
) -> None:
    root, config, _, _, entry_path = valid_repo(tmp_path)
    payload = json.loads(entry_path.read_text(encoding="utf-8"))
    payload["pages"] = ["journal/operations/legacy.md"]
    entry_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    legacy = config.vault / "journal" / "operations"
    legacy.rmdir()
    external = tmp_path / "external-legacy"
    external.mkdir()
    legacy.symlink_to(external, target_is_directory=True)

    report = check_portable_repo(config)

    assert issues_with_code(report, "manifest-page-invalid") == [
        {
            "code": "manifest-page-invalid",
            "path": "wiki/journal/operations/legacy.md",
            "message": "manifest page is not a knowledge page",
            "severity": "error",
        }
    ]
    assert str(root) not in json.dumps(report)


def test_knowledge_scan_prunes_legacy_subtree_before_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config, _, _, _ = valid_repo(tmp_path)
    legacy = config.vault / "journal" / "operations"
    legacy.rmdir()
    external = tmp_path / "external-legacy"
    external.mkdir()
    legacy.symlink_to(external, target_is_directory=True)
    real_scan = portable_check_module.scan_markdown_files
    seen: list[frozenset[str]] = []

    def recording_scan(
        root: Path, *, skip_relative_subtrees: set[str]
    ):
        seen.append(frozenset(skip_relative_subtrees))
        return real_scan(root, skip_relative_subtrees=skip_relative_subtrees)

    monkeypatch.setattr(
        portable_check_module,
        "scan_markdown_files",
        recording_scan,
    )

    check_portable_repo(config)

    assert seen == [frozenset({"journal/operations"})]


def test_page_to_manifest_edge_must_be_declared_by_shard(tmp_path: Path) -> None:
    _, config, _, _, entry_path = valid_repo(tmp_path)
    payload = json.loads(entry_path.read_text(encoding="utf-8"))
    payload["pages"] = []
    entry_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert "page-manifest-edge-missing" in issue_codes(check_portable_repo(config))


@pytest.mark.parametrize(
    "category",
    [
        "concepts",
        "entities",
        "skills",
        "references",
        "synthesis",
        "journal",
        "projects",
    ],
)
def test_knowledge_pages_require_all_six_fields(tmp_path: Path, category: str) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    page = root / "wiki" / category / "incomplete.md"
    page.write_text("---\ntitle: Incomplete\n---\n", encoding="utf-8")

    assert "frontmatter-missing" in issue_codes(check_portable_repo(config))


def test_valid_operation_is_checked_without_manifest_page_membership(
    tmp_path: Path,
) -> None:
    _root, config, _, _, _ = valid_repo(tmp_path)
    (config.vault / "log.md").write_text(
        render_operation_log((OperationChange(
            transaction_id="tx-1",
            completed_at="2026-08-07T07:30:00Z",
            source_ids=("sources/a.md",),
            created=("concepts/a.md",),
            updated=(),
            removed=(),
        ),)),
        encoding="utf-8",
    )

    assert check_portable_repo(config)["status"] == "pass"


@pytest.mark.parametrize("mutation", ["malformed", "source", "noncanonical"])
def test_invalid_operation_log_is_an_error(tmp_path: Path, mutation: str) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    operation = config.vault / "log.md"
    operation.write_text(render_operation_log((OperationChange(
            transaction_id="tx-1",
            completed_at="2026-08-07T07:30:00Z",
            source_ids=("sources/a.md",),
            created=("concepts/a.md",),
            updated=(),
            removed=(),
        ),)), encoding="utf-8")
    if mutation == "malformed":
        operation.write_text("# Missing frontmatter\n", encoding="utf-8")
    elif mutation == "source":
        operation.write_text(
            operation.read_text(encoding="utf-8").replace(
                "sources/a.md", "../outside.md"
            ),
            encoding="utf-8",
        )
    else:
        operation.write_text(
            operation.read_text(encoding="utf-8").replace(
                "- `sources/a.md`", "- `sources/a.md`  "
            ),
            encoding="utf-8",
        )

    report = check_portable_repo(config)

    assert "operation-log-invalid" in issue_codes(report)
    assert (
        issues_with_code(report, "operation-log-invalid")[0]["path"]
        == operation.relative_to(root).as_posix()
    )


def test_duplicate_operation_transaction_ids_are_errors(tmp_path: Path) -> None:
    _, config, _, _, _ = valid_repo(tmp_path)
    change = OperationChange(
        transaction_id="tx-1",
        completed_at="2026-08-07T07:30:00Z",
        source_ids=("sources/a.md",),
        created=("concepts/a.md",),
        updated=(),
        removed=(),
    )
    log = config.vault / "log.md"
    block = render_operation_log((change,))[len(EMPTY_OPERATION_LOG):]
    log.write_text(EMPTY_OPERATION_LOG + block + block, encoding="utf-8")

    assert "operation-log-invalid" in issue_codes(check_portable_repo(config))


def test_operation_log_source_requires_manifest_entry(tmp_path: Path) -> None:
    _, config, _, _, _ = valid_repo(tmp_path)
    (config.vault / "log.md").write_text(
        render_operation_log((OperationChange(
            transaction_id="tx-unknown",
            completed_at="2026-08-07T07:30:00Z",
            source_ids=("sources/unknown.md",),
            created=(),
            updated=(),
            removed=(),
        ),)),
        encoding="utf-8",
    )

    assert "operation-log-invalid" in issue_codes(check_portable_repo(config))


@pytest.mark.parametrize("kind", ["ordinary", "symlink"])
def test_legacy_operation_subtree_is_ignored(
    tmp_path: Path, kind: str
) -> None:
    _, config, _, _, _ = valid_repo(tmp_path)
    legacy = config.vault / "journal" / "operations"
    legacy.parent.mkdir(exist_ok=True)
    legacy.rmdir()
    if kind == "ordinary":
        legacy.mkdir()
        (legacy / "malformed.md").write_text(
            "# Missing metadata with [[missing target]]\n", encoding="utf-8"
        )
    else:
        external = tmp_path / "external-legacy-operations"
        external.mkdir()
        (external / "malformed.md").write_text(
            "# External legacy operation\n", encoding="utf-8"
        )
        legacy.symlink_to(external, target_is_directory=True)
    page = config.vault / "concepts/a.md"
    page.write_text(
        page.read_text(encoding="utf-8") + "\n[[unrelated missing target]]\n",
        encoding="utf-8",
    )

    report = check_portable_repo(config)
    assert "lint-broken-link" in issue_codes(report)
    assert all(
        "journal/operations" not in issue["path"] for issue in report["issues"]
    )


@pytest.mark.parametrize("mutation", ["missing", "non-utf8", "symlink"])
def test_hot_view_must_be_safe_utf8_markdown(
    tmp_path: Path, mutation: str
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    hot = config.vault / "hot.md"
    hot.unlink()
    if mutation == "non-utf8":
        hot.write_bytes(b"\xff")
    elif mutation == "symlink":
        external = tmp_path / "external-hot.md"
        external.write_text("# External\n", encoding="utf-8")
        hot.symlink_to(external)

    report = check_portable_repo(config)

    assert "hot-view-invalid" in issue_codes(report)
    assert issues_with_code(report, "hot-view-invalid")[0]["path"] == "wiki/hot.md"


def test_fail_level_lint_findings_become_errors(tmp_path: Path) -> None:
    _, config, _, page, _ = valid_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8") + "\n[[Missing target]]\n", encoding="utf-8"
    )

    report = check_portable_repo(config)
    assert issues_with_code(report, "lint-broken-link") == [
        {
            "code": "lint-broken-link",
            "path": "wiki/concepts/a.md",
            "message": "broken link target: missing-target",
            "severity": "error",
        }
    ]


def _write_custom_skill(root: Path, name: str = "team-note") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use for team notes.\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill


def _copy_skill_to_all_mirrors(root: Path, skill: Path) -> None:
    for relative, _label in PROJECT_AGENT_DIRS:
        shutil.copytree(skill, root / relative / skill.name)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("change", "skill-mirror-changed"),
        ("delete", "skill-mirror-missing"),
        ("extra", "skill-mirror-extra"),
        ("symlink", "skill-mirror-unsafe"),
        ("hardlink", "skill-mirror-unsafe"),
    ],
)
def test_check_reports_complete_mirror_drift(
    tmp_path: Path, mutation: str, code: str
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    mirror_root = root / ".claude/skills"
    skill = mirror_root / "wiki-ingest"
    skill_file = skill / "SKILL.md"
    if mutation == "change":
        skill_file.write_text(
            skill_file.read_text(encoding="utf-8") + "\nMirror drift.\n",
            encoding="utf-8",
        )
    elif mutation == "delete":
        skill_file.unlink()
    elif mutation == "extra":
        _write_custom_skill(mirror_root, "mirror-only")
    elif mutation == "symlink":
        skill_file.unlink()
        skill_file.symlink_to(root / ".skills/wiki-ingest/SKILL.md")
    else:
        external = tmp_path / "hardlinked-skill.md"
        external.write_bytes(skill_file.read_bytes())
        skill_file.unlink()
        os.link(external, skill_file)

    report = check_portable_repo(config)

    assert report["status"] == "fail"
    assert code in issue_codes(report)
    assert str(root) not in json.dumps(report)


def test_check_rejects_agent_mirror_with_symlinked_ancestor(
    tmp_path: Path,
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    external = tmp_path / "external-claude"
    shutil.copytree(root / ".claude", external)
    shutil.rmtree(root / ".claude")
    (root / ".claude").symlink_to(external, target_is_directory=True)

    report = check_portable_repo(config)

    assert "skill-mirror-unsafe" in issue_codes(report)
    assert str(external) not in json.dumps(report)


def test_check_rejects_ancestor_symlink_inserted_after_mirror_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    external = tmp_path / "external-claude"
    shutil.copytree(root / ".claude", external)
    from obsidian_wiki import portable_check

    original_snapshot = portable_check.snapshot_ordinary_tree_with_unsafe
    swapped = False

    def swap_ancestor_after_snapshot(path: Path, *, anchor: Path):
        nonlocal swapped
        snapshot = original_snapshot(path, anchor=anchor)
        if not swapped and path == root / ".claude/skills":
            swapped = True
            shutil.rmtree(root / ".claude")
            (root / ".claude").symlink_to(external, target_is_directory=True)
        return snapshot

    monkeypatch.setattr(
        portable_check,
        "snapshot_ordinary_tree_with_unsafe",
        swap_ancestor_after_snapshot,
    )

    report = check_portable_repo(config)

    assert "skill-mirror-unsafe" in issue_codes(report)
    assert str(external) not in json.dumps(report)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("root-file", "skill-mirror-extra"),
        ("empty-directory", "skill-mirror-extra"),
        ("invalid-frontmatter", "skill-mirror-changed"),
    ],
)
def test_ordinary_mirror_drift_is_not_classified_as_unsafe(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    mirror_root = root / ".claude/skills"
    if mutation == "root-file":
        (mirror_root / "README.md").write_text("extra\n", encoding="utf-8")
    elif mutation == "empty-directory":
        (mirror_root / "extra-empty").mkdir()
    else:
        (mirror_root / "wiki-ingest/SKILL.md").write_text(
            "not valid frontmatter\n", encoding="utf-8"
        )

    report = check_portable_repo(config)

    assert expected_code in issue_codes(report)
    assert "skill-mirror-unsafe" not in issue_codes(report)


def test_managed_canonical_edit_is_warning_when_mirrors_match(
    tmp_path: Path,
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    paths = [root / ".skills/wiki-ingest/SKILL.md"] + [
        root / relative / "wiki-ingest/SKILL.md"
        for relative, _label in PROJECT_AGENT_DIRS
    ]
    for path in paths:
        path.write_text(
            path.read_text(encoding="utf-8") + "\nOwner extension.\n",
            encoding="utf-8",
        )

    report = check_portable_repo(config)

    assert report["status"] == "warn"
    assert issues_with_code(report, "managed-canonical-modified")[0]["severity"] == (
        "warning"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory-swap race")
def test_check_binds_canonical_discovery_against_swap_and_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    canonical_skill = root / ".skills/wiki-ingest"
    canonical_file = canonical_skill / "SKILL.md"
    canonical_file.write_text(
        canonical_file.read_text(encoding="utf-8") + "\nOwner drift.\n",
        encoding="utf-8",
    )
    external = tmp_path / "external-wiki-ingest"
    shutil.copytree(root / ".claude/skills/wiki-ingest", external)
    backup = tmp_path / "canonical-wiki-ingest-backup"
    from obsidian_wiki import skill_trees

    target_metadata = canonical_skill.lstat()
    target_identity = (target_metadata.st_dev, target_metadata.st_ino)
    original_listdir = skill_trees.os.listdir
    swapped = False

    def swap_while_descriptor_is_bound(descriptor: int):
        nonlocal swapped
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) == target_identity and not swapped:
            swapped = True
            canonical_skill.rename(backup)
            canonical_skill.symlink_to(external, target_is_directory=True)
            try:
                return original_listdir(descriptor)
            finally:
                canonical_skill.unlink()
                backup.rename(canonical_skill)
        return original_listdir(descriptor)

    monkeypatch.setattr(skill_trees.os, "listdir", swap_while_descriptor_is_bound)
    try:
        report = check_portable_repo(config)
    finally:
        if canonical_skill.is_symlink():
            canonical_skill.unlink()
            backup.rename(canonical_skill)

    assert swapped
    assert report["status"] == "fail"
    assert "canonical-skill-invalid" in issue_codes(report)
    assert canonical_file.read_text(encoding="utf-8").endswith("\nOwner drift.\n")


@pytest.mark.parametrize("mutation", ["frontmatter", "directory-name"])
def test_malformed_canonical_skill_is_reported_separately(
    tmp_path: Path, mutation: str
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    skill_file = root / ".skills/wiki-ingest/SKILL.md"
    if mutation == "frontmatter":
        skill_file.write_text("# Missing frontmatter\n", encoding="utf-8")
    else:
        skill_file.write_text(
            skill_file.read_text(encoding="utf-8").replace(
                "name: wiki-ingest", "name: wrong-name", 1
            ),
            encoding="utf-8",
        )

    report = check_portable_repo(config)

    assert "canonical-skill-invalid" in issue_codes(report)
    assert str(root) not in json.dumps(report)


@pytest.mark.parametrize("missing_from", ["one", "all"])
def test_custom_canonical_skills_must_exist_in_every_mirror(
    tmp_path: Path, missing_from: str
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    custom = _write_custom_skill(root / ".skills")
    _copy_skill_to_all_mirrors(root, custom)
    targets = [root / relative / custom.name for relative, _ in PROJECT_AGENT_DIRS]
    for target in targets if missing_from == "all" else targets[:1]:
        shutil.rmtree(target)

    report = check_portable_repo(config)

    assert "skill-mirror-missing" in issue_codes(report)
    assert "managed-skills-invalid" not in issue_codes(report)


def test_custom_skill_binary_executable_and_cjk_resources_compare_exactly(
    tmp_path: Path,
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    custom = _write_custom_skill(root / ".skills", "portable-assets")
    resource = custom / "resources/资料/二进制.dat"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(b"\x00\xffportable\n")
    executable = custom / "scripts/run.sh"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _copy_skill_to_all_mirrors(root, custom)

    report = check_portable_repo(config)

    assert report["status"] == "pass"


def test_mirror_executable_mode_change_is_detected(tmp_path: Path) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    custom = _write_custom_skill(root / ".skills", "portable-script")
    executable = custom / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _copy_skill_to_all_mirrors(root, custom)
    (root / ".claude/skills/portable-script/run.sh").chmod(0o644)

    assert "skill-mirror-changed" in issue_codes(check_portable_repo(config))


@pytest.mark.parametrize(
    "field", ["implementation", "skills_version", "managed_skill_digests"]
)
def test_managed_inventory_validates_implementation_version_and_digest_keys(
    tmp_path: Path, field: str
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    if field == "implementation":
        payload[field] = "wrong/implementation"
    elif field == "skills_version":
        payload[field] = "not a version"
    else:
        payload[field].pop(next(iter(payload[field])))
    inventory.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert "managed-skills-invalid" in issue_codes(check_portable_repo(config))


def test_managed_inventory_version_must_satisfy_repository_range(
    tmp_path: Path,
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["skills_version"] = "1.0.0"
    inventory.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert "managed-skills-invalid" in issue_codes(check_portable_repo(config))


def test_inventory_managed_name_must_exist_in_canonical_collection(
    tmp_path: Path,
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["managed_skills"].append("missing-managed")
    payload["managed_skills"].sort()
    payload["managed_skill_digests"]["missing-managed"] = "sha256:" + "0" * 64
    inventory.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    report = check_portable_repo(config)

    assert "managed-skills-invalid" in issue_codes(report)
    assert "skill-mirror-extra" not in issue_codes(report)


def test_legacy_managed_inventory_has_upgrade_guidance(tmp_path: Path) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    inventory = root / ".obsidian-wiki/managed-skills.json"
    current = json.loads(inventory.read_text(encoding="utf-8"))
    inventory.write_text(
        json.dumps(
            {
                "implementation": IMPLEMENTATION_ID,
                "skills": current["managed_skills"],
                "skills_version": current["skills_version"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = check_portable_repo(config)

    issue = issues_with_code(report, "managed-skills-legacy")[0]
    assert "upgrade-skills" in issue["message"]
    assert str(root) not in json.dumps(report)


def test_bootstrap_owner_text_outside_managed_region_is_allowed(tmp_path: Path) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    bootstrap = root / "AGENTS.md"
    bootstrap.write_text(
        "Owner preface\n" + bootstrap.read_text(encoding="utf-8") + "Owner epilogue\n",
        encoding="utf-8",
    )

    assert check_portable_repo(config)["status"] == "pass"


@pytest.mark.parametrize("mutation", ["duplicate-start", "missing-end", "stale"])
def test_bootstrap_managed_region_must_be_well_formed_and_current(
    tmp_path: Path, mutation: str
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    bootstrap = root / "CLAUDE.md"
    text = bootstrap.read_text(encoding="utf-8")
    if mutation == "duplicate-start":
        text = text.replace(MANAGED_START, MANAGED_START + "\n" + MANAGED_START)
    elif mutation == "missing-end":
        text = text.replace(MANAGED_END, "")
    else:
        text = text.replace("Read and follow `AGENTS.md`", "Read stale instructions")
    bootstrap.write_text(text, encoding="utf-8")

    assert "managed-bootstrap-invalid" in issue_codes(check_portable_repo(config))


@pytest.mark.parametrize("name", ["index.md"])
def test_stable_views_must_match_portable_templates(tmp_path: Path, name: str) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    (root / "wiki" / name).write_text("# Hand-maintained list\n", encoding="utf-8")

    assert "stable-view-modified" in issue_codes(check_portable_repo(config))


@pytest.mark.parametrize(
    "relative",
    [
        "wiki/hot.md",
        ".obsidian-wiki/local/cache.json",
        "wiki/.locks/write.lock",
        "wiki/.snapshots/one.json",
        "wiki/.transactions/one.json",
    ],
)
def test_mutable_local_state_must_not_be_tracked(tmp_path: Path, relative: str) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("local", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "-f", relative],
        check=True,
        capture_output=True,
    )

    assert "tracked-local-state" in issue_codes(check_portable_repo(config))


def test_fixed_local_state_is_rejected_when_configured_local_path_changes(
    tmp_path: Path,
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    config_path = root / ".obsidian-wiki/config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'local_state = ".obsidian-wiki/local"',
            'local_state = ".obsidian-wiki/runtime"',
        ),
        encoding="utf-8",
    )
    local = root / ".obsidian-wiki/local/cache.json"
    local.write_text("local", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "-f", ".obsidian-wiki/local/cache.json"],
        check=True,
        capture_output=True,
    )

    assert "tracked-local-state" in issue_codes(check_portable_repo(config))


def test_invalid_utf8_git_filename_does_not_disable_local_state_enforcement(
    tmp_path: Path,
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    bad_name = os.fsdecode(b"bad-\xff")
    (root / bad_name).write_text("bad filename", encoding="utf-8")
    hot = root / "wiki/hot.md"
    hot.write_text("local", encoding="utf-8")
    subprocess.run(
        [
            b"git",
            b"-C",
            os.fsencode(root),
            b"add",
            b"-f",
            b"bad-\xff",
            b"wiki/hot.md",
        ],
        check=True,
        capture_output=True,
    )

    report = check_portable_repo(config)
    assert "tracked-local-state" in issue_codes(report)
    assert "git-unavailable" not in issue_codes(report)

    proc = _run_cli(tmp_path / "home", root, "check", "--json")
    assert proc.returncode == 1
    cli_report = json.loads(proc.stdout)
    assert "tracked-local-state" in issue_codes(cli_report)
    assert "git-unavailable" not in issue_codes(cli_report)


def _replace_with_external_hardlink(tmp_path: Path, target: Path) -> None:
    external = tmp_path / f"external-{target.name}"
    external.write_bytes(target.read_bytes())
    target.unlink()
    os.link(external, target)


@pytest.mark.parametrize(
    ("target_name", "expected_code"),
    [
        ("config", "config-invalid"),
        ("source", "source-invalid"),
        ("page", "knowledge-page-invalid"),
        ("marker", "manifest-invalid"),
        ("shard", "manifest-invalid"),
        ("inventory", "managed-skills-invalid"),
        ("mirror", "skill-mirror-unsafe"),
        ("bootstrap", "managed-bootstrap-invalid"),
        ("stable-view", "stable-view-modified"),
        ("operation-log", "operation-log-invalid"),
        ("hot-view", "hot-view-invalid"),
    ],
)
def test_checker_rejects_hardlinked_managed_files(
    tmp_path: Path, target_name: str, expected_code: str
) -> None:
    root, config, source, page, shard = valid_repo(tmp_path)
    targets = {
        "config": root / ".obsidian-wiki/config.toml",
        "source": source,
        "page": page,
        "marker": root / "wiki/.manifest.json",
        "shard": shard,
        "inventory": root / ".obsidian-wiki/managed-skills.json",
        "mirror": root / ".claude/skills/wiki-ingest/SKILL.md",
        "bootstrap": root / "CLAUDE.md",
        "stable-view": root / "wiki/index.md",
        "operation-log": root / "wiki/log.md",
        "hot-view": root / "wiki/hot.md",
    }
    _replace_with_external_hardlink(tmp_path, targets[target_name])

    report = check_portable_repo(config)
    assert expected_code in issue_codes(report)
    assert str(root) not in json.dumps(report)


def test_checker_does_not_follow_symlinked_knowledge_page(tmp_path: Path) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    marker = "PORTABLE_CHECK_EXTERNAL_MARKER_8F39"
    target = "portable-check-external-target-8f39"
    external = tmp_path / "external.md"
    external.write_text(f"# {marker}\n\n[[{target}]]\n", encoding="utf-8")
    leak = root / "wiki/concepts/leak.md"
    leak.symlink_to(external)

    report = check_portable_repo(config)
    serialized = json.dumps(report)

    assert "knowledge-page-invalid" in issue_codes(report)
    assert marker not in serialized
    assert target not in serialized


def test_malformed_marker_and_config_are_reported_without_absolute_paths(
    tmp_path: Path,
) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    (root / "wiki/.manifest.json").write_text("{}\n", encoding="utf-8")
    report = check_portable_repo(config)

    assert "manifest-invalid" in issue_codes(report)
    assert str(root) not in json.dumps(report)

    config_path = root / ".obsidian-wiki/config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            f'implementation = "{IMPLEMENTATION_ID}"',
            'implementation = "wrong/implementation"',
        ),
        encoding="utf-8",
    )
    report = check_portable_repo(config)
    assert "config-invalid" in issue_codes(report)
    assert str(root) not in json.dumps(report)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content_hash", "sha256:bad"),
        ("compiled_at", 123),
        ("pages", ["concepts/../a.md"]),
        ("pages", ["concepts/a.md", "concepts/a.md"]),
        ("pages", ["references/b.md", "concepts/a.md"]),
    ],
)
def test_malformed_shards_fail_closed(
    tmp_path: Path, field: str, value: object
) -> None:
    _, config, _, _, entry_path = valid_repo(tmp_path)
    payload = json.loads(entry_path.read_text(encoding="utf-8"))
    payload[field] = value
    entry_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert "manifest-invalid" in issue_codes(check_portable_repo(config))


def test_reports_are_clone_independent(tmp_path: Path) -> None:
    first_root, first_config, _, _, first_entry = valid_repo(tmp_path, "first")
    second_root, second_config, _, _, second_entry = valid_repo(tmp_path, "second")
    for entry in (first_entry, second_entry):
        payload = json.loads(entry.read_text(encoding="utf-8"))
        payload["content_hash"] = "invalid"
        entry.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    first = check_portable_repo(first_config)
    second = check_portable_repo(second_config)
    assert first == second
    serialized = json.dumps(first)
    assert str(first_root) not in serialized
    assert str(second_root) not in serialized


def _run_cli(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


def test_cli_check_json_from_nested_source_is_nonzero_when_stale(
    tmp_path: Path,
) -> None:
    root, _, source, _, _ = valid_repo(tmp_path)
    nested = root / "sources/nested"
    nested.mkdir()
    source.write_text("changed", encoding="utf-8")

    proc = _run_cli(tmp_path / "home", nested, "check", "--json", "--pretty")

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert "source-stale" in issue_codes(report)


@pytest.mark.parametrize(("strict", "returncode"), [(False, 0), (True, 1)])
def test_cli_check_managed_canonical_warning_exit_semantics(
    tmp_path: Path, strict: bool, returncode: int
) -> None:
    root, _, _, _, _ = valid_repo(tmp_path)
    paths = [root / ".skills/wiki-ingest/SKILL.md"] + [
        root / relative / "wiki-ingest/SKILL.md"
        for relative, _label in PROJECT_AGENT_DIRS
    ]
    for path in paths:
        path.write_text(
            path.read_text(encoding="utf-8") + "\nOwner extension.\n",
            encoding="utf-8",
        )
    args = ["check", "--json"]
    if strict:
        args.append("--strict")

    proc = _run_cli(tmp_path / "home", root, *args)

    assert proc.returncode == returncode
    report = json.loads(proc.stdout)
    assert report["status"] == "warn"
    assert "managed-canonical-modified" in issue_codes(report)


def test_cli_check_wrong_implementation_never_falls_back_to_global(
    tmp_path: Path,
) -> None:
    root, _, _, _, _ = valid_repo(tmp_path)
    config = root / ".obsidian-wiki/config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            IMPLEMENTATION_ID, "wrong/implementation"
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    global_vault = tmp_path / "global-vault"
    global_vault.mkdir()
    global_config = home / ".obsidian-wiki/config"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(
        f'OBSIDIAN_VAULT_PATH="{global_vault}"\n', encoding="utf-8"
    )

    proc = _run_cli(home, root / "sources", "check", "--json")

    assert proc.returncode == 1
    assert proc.stderr == ""
    assert "implementation" in json.loads(proc.stdout)["error"]["message"]
    assert str(global_vault) not in proc.stdout + proc.stderr


def test_cli_check_outside_portable_repo_uses_exact_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    proc = _run_cli(home, project, "check", "--json")

    assert proc.returncode == 1
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["status"] == "error"


def test_cmd_check_uses_shared_portable_config_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = tuple(tmp_path.iterdir())
    monkeypatch.chdir(tmp_path)
    sinks: list[list[ConfigError]] = []

    def resolution_failure(*, error_sink: list[ConfigError]) -> None:
        sinks.append(error_sink)
        error_sink.append(ConfigError("portable config is invalid"))
        return None

    monkeypatch.setattr(cli, "_resolve_runtime", resolution_failure)

    assert cli.cmd_check(SimpleNamespace()) == 1

    captured = capsys.readouterr()
    assert len(sinks) == 1
    assert captured.out == ""
    assert captured.err == "error: portable config is invalid\n"
    assert tuple(tmp_path.iterdir()) == before


def test_cmd_check_handles_unavailable_current_directory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def cwd_failure() -> Path:
        raise PermissionError("cwd denied")

    monkeypatch.setattr(Path, "cwd", cwd_failure)

    assert cli.cmd_check(SimpleNamespace()) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: current working directory is unavailable: cwd denied\n"


def test_checker_rejects_noncanonical_configured_skills_path(tmp_path: Path) -> None:
    root, config, _, _, _ = valid_repo(tmp_path)
    (root / ".skills").rename(root / "alternate-skills")
    config_path = root / ".obsidian-wiki/config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'skills = ".skills"', 'skills = "alternate-skills"'
        ),
        encoding="utf-8",
    )

    report = check_portable_repo(config)
    assert "config-invalid" in issue_codes(report)
    assert str(root) not in json.dumps(report)

"""Tests for the discoverable query-language/v1 grammar."""

from __future__ import annotations

import json
from typing import Optional

import pytest

from obsidian_wiki.query_language import (
    GRAMMAR_VERSION,
    QueryLanguageError,
    QuerySpec,
    build_explicit_query,
    describe_query_language,
    normalize_match,
    normalize_operand,
    parse_natural_query,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ('find "中文知识图谱"', QuerySpec(mode="find", term="中文知识图谱")),
        ('LIST PAGES ABOUT "العربية"', QuerySpec(mode="list", term="العربية")),
        (
            'find path from "中文入口" to "English destination"',
            QuerySpec(mode="path", source="中文入口", target="English destination"),
        ),
    ],
)
def test_natural_queries_match_their_explicit_form(question: str, expected: QuerySpec) -> None:
    assert parse_natural_query(question) == expected
    if expected.mode == "path":
        explicit = build_explicit_query(
            "path", source=expected.source, target=expected.target
        )
    else:
        explicit = build_explicit_query(expected.mode, term=expected.term)
    assert explicit == expected


@pytest.mark.parametrize("operand", ["中文", "日本語", "العربية", "🔎", "C++ RAII"])
def test_natural_queries_preserve_unicode_operands(operand: str) -> None:
    assert parse_natural_query('find "{}"'.format(operand)) == QuerySpec(
        mode="find", term=operand
    )


def test_quoted_operands_decode_quote_and_backslash_escapes() -> None:
    assert parse_natural_query(r'find "say \"hello\" \\ now"') == QuerySpec(
        mode="find", term='say "hello" \\ now'
    )


@pytest.mark.parametrize("question", [r'find "line\nbreak"', r'find "tab\tvalue"'])
def test_natural_queries_reject_unsupported_escapes(question: str) -> None:
    with pytest.raises(QueryLanguageError) as raised:
        parse_natural_query(question)

    assert raised.value.code == "unsupported_query_structure"


@pytest.mark.parametrize("question", ['find ""', 'find " \t　 "'])
def test_natural_queries_reject_empty_normalized_operands(question: str) -> None:
    with pytest.raises(QueryLanguageError) as raised:
        parse_natural_query(question)

    assert raised.value.code == "unsupported_query_structure"


@pytest.mark.parametrize(
    "question",
    [
        'find\t"term"',
        'find  "term"',
        'list pages\nabout "term"',
        'find path from "source"\nto "target"',
    ],
)
def test_natural_queries_require_exact_template_spaces(question: str) -> None:
    with pytest.raises(QueryLanguageError) as raised:
        parse_natural_query(question)

    assert raised.value.code == "unsupported_query_structure"


def test_query_language_description_is_complete_machine_readable_authority() -> None:
    description = describe_query_language()

    assert description == {
        "grammar_version": GRAMMAR_VERSION,
        "supported_modes": ["find", "list", "path"],
        "natural_templates": [
            {
                "mode": "find",
                "template": 'find "<term>"',
                "example": 'find "注意力机制"',
                "operands": ["term"],
            },
            {
                "mode": "list",
                "template": 'list pages about "<term>"',
                "example": 'list pages about "الذكاء الاصطناعي"',
                "operands": ["term"],
            },
            {
                "mode": "path",
                "template": 'find path from "<source>" to "<target>"',
                "example": 'find path from "注意力機構" to "English destination"',
                "operands": ["source", "target"],
            },
        ],
        "canonical_cli": {
            "find": "--mode find --term <term>",
            "list": "--mode list --term <term>",
            "path": "--mode path --from <source> --to <target>",
        },
        "normalization": ["NFKC", "strip", "casefold for matching"],
        "operand_policy": {
            "representation": "opaque Unicode complete phrases",
            "normalization": ["NFKC", "strip", "casefold for matching"],
            "prohibited_operations": [
                "tokenization",
                "language detection",
                "translation",
                "synonym expansion",
                "stemming",
                "fuzzy matching",
            ],
        },
        "search_fields": ["slug", "title", "tags", "summary"],
        "match_ranking": {
            "lexical_precedence": [
                {"match_kind": "exact", "fields": ["page identity", "basename", "title"]},
                {"match_kind": "title", "fields": ["title substring"]},
                {"match_kind": "tag", "fields": ["tag substring"]},
                {"match_kind": "summary", "fields": ["summary substring"]},
            ],
            "within_lexical_band_order": ["degree", "tier"],
            "tie_breaker": "path",
        },
        "result_statuses": ["ok", "no_matches", "no_path"],
        "error_codes": [
            "unsupported_query_structure",
            "invalid_query_arguments",
            "ambiguous_operand",
            "unsupported_operation",
        ],
    }
    assert json.loads(json.dumps(description)) == description


@pytest.mark.parametrize(
    "question",
    [
        '查找 "中文"',
        'search for "中文"',
        'find "中文" please',
        'find "unterminated',
        'find path from "source" to"target"',
        'show gaps about "topic"',
        "legacy bare query",
        'fınd "term"',
    ],
)
def test_natural_queries_reject_unsupported_structures(question: str) -> None:
    with pytest.raises(QueryLanguageError) as raised:
        parse_natural_query(question)

    assert raised.value.code == "unsupported_query_structure"


@pytest.mark.parametrize(
    ("mode", "term", "source", "target"),
    [
        ("find", None, None, None),
        ("find", "term", "unexpected", None),
        ("path", None, "source", None),
        ("list", "   ", None, None),
    ],
)
def test_explicit_queries_reject_invalid_argument_combinations(
    mode: str,
    term: Optional[str],
    source: Optional[str],
    target: Optional[str],
) -> None:
    with pytest.raises(QueryLanguageError) as raised:
        build_explicit_query(mode, term=term, source=source, target=target)

    assert raised.value.code == "invalid_query_arguments"


def test_explicit_queries_reject_unknown_operations() -> None:
    with pytest.raises(QueryLanguageError) as raised:
        build_explicit_query("search", term="term")

    assert raised.value.code == "unsupported_operation"


def test_operands_normalize_nfkc_without_tokenization() -> None:
    full_width = "  Ｃ＋＋　ＲＡＩＩ  "

    assert normalize_operand(full_width) == "C++ RAII"
    assert normalize_match(full_width) == "c++ raii"
    assert parse_natural_query('find "{}"'.format(full_width)) == QuerySpec(
        mode="find", term="C++ RAII"
    )

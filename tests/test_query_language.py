"""Tests for the discoverable query-language/v1 grammar."""

from __future__ import annotations

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


def test_query_language_description_is_versioned_and_ordered() -> None:
    description = describe_query_language()

    assert description["grammar_version"] == GRAMMAR_VERSION == "query-language/v1"
    assert [item["mode"] for item in description["natural_templates"]] == [
        "find",
        "list",
        "path",
    ]
    assert description["natural_templates"][-1]["example"] == (
        'find path from "注意力机制" to "词嵌入"'
    )
    assert description["canonical_cli"]["path"] == (
        "--mode path --from <source> --to <target>"
    )
    assert description["normalization"] == ["NFKC", "strip", "casefold for matching"]
    assert description["search_fields"] == ["slug", "title", "tags", "summary"]
    assert description["result_statuses"] == ["ok", "no_matches", "no_path"]
    assert description["error_codes"] == [
        "unsupported_query_structure",
        "invalid_query_arguments",
        "ambiguous_operand",
        "unsupported_operation",
    ]


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

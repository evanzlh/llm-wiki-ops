"""The fixed, discoverable query-language/v1 grammar."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, Optional


GRAMMAR_VERSION = "query-language/v1"
QueryMode = Literal["find", "list", "path"]
_QUOTED_OPERAND = r'"((?:[^"\\]|\\["\\])*)"'


@dataclass(frozen=True)
class QuerySpec:
    """A validated query-language/v1 operation."""

    mode: QueryMode
    term: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None


@dataclass(frozen=True)
class _NaturalTemplate:
    mode: QueryMode
    template: str
    example: str
    operands: tuple[str, ...]
    pattern: re.Pattern[str]


class QueryLanguageError(ValueError):
    """A stable query-language error with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_operand(value: str) -> str:
    """Normalize a complete operand without splitting or tokenizing it."""
    return unicodedata.normalize("NFKC", value).strip()


def normalize_match(value: str) -> str:
    """Return the form used for case-insensitive operand matching."""
    return normalize_operand(value).casefold()


def _decode_quoted_operand(value: str) -> str:
    return re.sub(r'\\(["\\])', r'\1', value)


_NATURAL_TEMPLATES = (
    _NaturalTemplate(
        mode="find",
        template='find "<term>"',
        example='find "注意力机制"',
        operands=("term",),
        pattern=re.compile(
            rf"find\s+{_QUOTED_OPERAND}", re.ASCII | re.IGNORECASE
        ),
    ),
    _NaturalTemplate(
        mode="list",
        template='list pages about "<term>"',
        example='list pages about "深度学习"',
        operands=("term",),
        pattern=re.compile(
            rf"list\s+pages\s+about\s+{_QUOTED_OPERAND}",
            re.ASCII | re.IGNORECASE,
        ),
    ),
    _NaturalTemplate(
        mode="path",
        template='find path from "<source>" to "<target>"',
        example='find path from "注意力机制" to "词嵌入"',
        operands=("source", "target"),
        pattern=re.compile(
            rf"find\s+path\s+from\s+{_QUOTED_OPERAND}\s+to\s+{_QUOTED_OPERAND}",
            re.ASCII | re.IGNORECASE,
        ),
    ),
)


def build_explicit_query(
    mode: str,
    term: Optional[str] = None,
    source: Optional[str] = None,
    target: Optional[str] = None,
) -> QuerySpec:
    """Validate explicit operands and return their normalized query spec."""
    if mode not in {"find", "list", "path"}:
        raise QueryLanguageError(
            "unsupported_operation", "unsupported query mode: {}".format(mode)
        )

    supplied = {"term": term, "source": source, "target": target}
    required = {
        "find": {"term"},
        "list": {"term"},
        "path": {"source", "target"},
    }[mode]
    present = {name for name, value in supplied.items() if value is not None}
    if present != required:
        raise QueryLanguageError(
            "invalid_query_arguments",
            "mode {} requires exactly: {}".format(mode, ", ".join(sorted(required))),
        )

    normalized = {
        name: normalize_operand(value)
        for name, value in supplied.items()
        if value is not None
    }
    if any(not value for value in normalized.values()):
        raise QueryLanguageError(
            "invalid_query_arguments", "query operands must not be empty"
        )
    return QuerySpec(mode=mode, **normalized)


def parse_natural_query(question: str) -> QuerySpec:
    """Parse one complete natural-language grammar template, or fail closed."""
    for template in _NATURAL_TEMPLATES:
        match = template.pattern.fullmatch(question)
        if match is not None:
            values = [_decode_quoted_operand(value) for value in match.groups()]
            return build_explicit_query(
                template.mode, **dict(zip(template.operands, values))
            )
    raise QueryLanguageError(
        "unsupported_query_structure", "query does not match query-language/v1"
    )


def describe_query_language() -> dict[str, Any]:
    """Return the JSON-serializable contract derived from the grammar source."""
    return {
        "grammar_version": GRAMMAR_VERSION,
        "natural_templates": [
            {
                "mode": item.mode,
                "template": item.template,
                "example": item.example,
            }
            for item in _NATURAL_TEMPLATES
        ],
        "canonical_cli": {
            "find": "--mode find --term <term>",
            "list": "--mode list --term <term>",
            "path": "--mode path --from <source> --to <target>",
        },
        "normalization": ["NFKC", "strip", "casefold for matching"],
        "search_fields": ["slug", "title", "tags", "summary"],
        "result_statuses": ["ok", "no_matches", "no_path"],
        "error_codes": [
            "unsupported_query_structure",
            "invalid_query_arguments",
            "ambiguous_operand",
            "unsupported_operation",
        ],
    }


__all__ = [
    "GRAMMAR_VERSION",
    "QueryLanguageError",
    "QuerySpec",
    "build_explicit_query",
    "describe_query_language",
    "normalize_match",
    "normalize_operand",
    "parse_natural_query",
]

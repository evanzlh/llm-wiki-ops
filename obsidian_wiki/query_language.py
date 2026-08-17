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
class _ModeDefinition:
    mode: QueryMode
    natural_template: str
    example: str
    operands: tuple[str, ...]
    canonical_cli: str
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


def _compile_natural_pattern(
    template: str, operands: tuple[str, ...]
) -> re.Pattern[str]:
    """Compile a template while preserving each fixed ASCII character exactly."""
    parts = []
    remainder = template
    for operand in operands:
        placeholder = '"<{}>"'.format(operand)
        fixed_text, separator, remainder = remainder.partition(placeholder)
        if not separator:
            raise ValueError("natural template is missing {}".format(placeholder))
        parts.extend((re.escape(fixed_text), _QUOTED_OPERAND))
    parts.append(re.escape(remainder))
    return re.compile("".join(parts), re.ASCII | re.IGNORECASE)


def _define_mode(
    mode: QueryMode,
    natural_template: str,
    example: str,
    operands: tuple[str, ...],
    canonical_cli: str,
) -> _ModeDefinition:
    return _ModeDefinition(
        mode=mode,
        natural_template=natural_template,
        example=example,
        operands=operands,
        canonical_cli=canonical_cli,
        pattern=_compile_natural_pattern(natural_template, operands),
    )


_MODE_DEFINITIONS = (
    _define_mode(
        mode="find",
        natural_template='find "<term>"',
        example='find "注意力机制"',
        operands=("term",),
        canonical_cli="--mode find --term <term>",
    ),
    _define_mode(
        mode="list",
        natural_template='list pages about "<term>"',
        example='list pages about "الذكاء الاصطناعي"',
        operands=("term",),
        canonical_cli="--mode list --term <term>",
    ),
    _define_mode(
        mode="path",
        natural_template='find path from "<source>" to "<target>"',
        example='find path from "注意力機構" to "English destination"',
        operands=("source", "target"),
        canonical_cli="--mode path --from <source> --to <target>",
    ),
)
_MODE_BY_NAME = {definition.mode: definition for definition in _MODE_DEFINITIONS}

_NORMALIZATION = ("NFKC", "strip", "casefold for matching")
_OPERAND_POLICY = {
    "representation": "opaque Unicode complete phrases",
    "normalization": _NORMALIZATION,
    "prohibited_operations": (
        "tokenization",
        "language detection",
        "translation",
        "synonym expansion",
        "stemming",
        "fuzzy matching",
    ),
}
_MATCH_RANKING = {
    "lexical_precedence": (
        ("exact", ("page identity", "basename", "title")),
        ("title", ("title substring",)),
        ("tag", ("tag substring",)),
        ("summary", ("summary substring",)),
    ),
    "within_lexical_band_order": ("degree", "tier"),
    "tie_breaker": "path",
}


def build_explicit_query(
    mode: str,
    term: Optional[str] = None,
    source: Optional[str] = None,
    target: Optional[str] = None,
) -> QuerySpec:
    """Validate explicit operands and return their normalized query spec."""
    definition = _MODE_BY_NAME.get(mode)
    if definition is None:
        raise QueryLanguageError(
            "unsupported_operation", "unsupported query mode: {}".format(mode)
        )

    supplied = {"term": term, "source": source, "target": target}
    required = set(definition.operands)
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
    return QuerySpec(mode=definition.mode, **normalized)


def parse_natural_query(question: str) -> QuerySpec:
    """Parse one complete natural-language grammar template, or fail closed."""
    for definition in _MODE_DEFINITIONS:
        match = definition.pattern.fullmatch(question)
        if match is not None:
            values = [_decode_quoted_operand(value) for value in match.groups()]
            if any(not normalize_operand(value) for value in values):
                break
            return build_explicit_query(
                definition.mode, **dict(zip(definition.operands, values))
            )
    raise QueryLanguageError(
        "unsupported_query_structure", "query does not match query-language/v1"
    )


def describe_query_language() -> dict[str, Any]:
    """Return the JSON-serializable contract derived from the grammar source."""
    return {
        "grammar_version": GRAMMAR_VERSION,
        "supported_modes": [item.mode for item in _MODE_DEFINITIONS],
        "natural_templates": [
            {
                "mode": item.mode,
                "template": item.natural_template,
                "example": item.example,
                "operands": list(item.operands),
            }
            for item in _MODE_DEFINITIONS
        ],
        "canonical_cli": {
            item.mode: item.canonical_cli for item in _MODE_DEFINITIONS
        },
        "normalization": list(_NORMALIZATION),
        "operand_policy": {
            "representation": _OPERAND_POLICY["representation"],
            "normalization": list(_OPERAND_POLICY["normalization"]),
            "prohibited_operations": list(
                _OPERAND_POLICY["prohibited_operations"]
            ),
        },
        "search_fields": ["slug", "title", "tags", "summary"],
        "match_ranking": {
            "lexical_precedence": [
                {"match_kind": kind, "fields": list(fields)}
                for kind, fields in _MATCH_RANKING["lexical_precedence"]
            ],
            "within_lexical_band_order": list(
                _MATCH_RANKING["within_lexical_band_order"]
            ),
            "tie_breaker": _MATCH_RANKING["tie_breaker"],
        },
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

# Query Language v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace best-effort free-form query classification with a versioned, machine-discoverable English query grammar whose quoted operands may contain any Unicode language.

**Architecture:** Add a focused `query_language` module that owns grammar, validation, normalization, and capability description, then pass a validated `QuerySpec` into `graphrag`. Refactor the query index to use repository-relative page identities and explicit alias ambiguity, and make the CLI, runtime skill, and human documentation consume one fail-closed contract.

**Tech Stack:** Python standard library (`argparse`, `dataclasses`, `json`, `re`, `typing`, `unicodedata`), pytest, packaged Markdown skill resources.

---

## File map

- Create `obsidian_wiki/query_language.py`: grammar definitions, `QuerySpec`, normalization, natural parsing, explicit argument validation, typed language errors, and `--describe` payload.
- Create `tests/test_query_language.py`: unit contract for grammar, multilingual operands, escaping, capability discovery, and invalid forms.
- Modify `obsidian_wiki/graphrag.py`: repository-relative page identity, alias/link resolution, phrase ranking, typed execution errors, and mode dispatch over `QuerySpec`.
- Modify `tests/test_graphrag.py`: index identity, ambiguity, phrase matching, list status, and mixed-language path regressions.
- Modify `obsidian_wiki/cli.py`: query parser options, context-free description, hard cutover, JSON/human errors, and validated query execution.
- Modify `tests/test_query_cli.py`: end-to-end natural/explicit equivalence, context-free description, strict rejection, status, ambiguity, and public filtering.
- Modify `obsidian_wiki/_data/skills/wiki-query/SKILL.md`: installed capability discovery and no-guess agent contract.
- Modify `tests/test_portable_only_contract.py`: exact assertions for the installed skill protocol.
- Modify `docs/cli.md` and `docs/cli.zh-TW.md`: human query grammar, explicit forms, result statuses, and migration.
- Modify `README.md` and `README_ZH.md`: aligned landing-page pointer to the discoverable query interface.

### Task 1: Versioned query-language module

**Files:**
- Create: `obsidian_wiki/query_language.py`
- Create: `tests/test_query_language.py`

- [ ] **Step 1: Write failing tests for accepted templates and explicit forms**

Create `tests/test_query_language.py` with the following initial contract:

```python
from __future__ import annotations

import pytest

from obsidian_wiki.query_language import (
    GRAMMAR_VERSION,
    QueryLanguageError,
    QuerySpec,
    build_explicit_query,
    describe_query_language,
    parse_natural_query,
)


@pytest.mark.parametrize(
    ("natural", "explicit", "expected"),
    [
        (
            'find "注意力机制"',
            {"mode": "find", "term": "注意力机制"},
            QuerySpec(mode="find", term="注意力机制"),
        ),
        (
            'LIST PAGES ABOUT "التعلم العميق"',
            {"mode": "list", "term": "التعلم العميق"},
            QuerySpec(mode="list", term="التعلم العميق"),
        ),
        (
            'find path from "注意力机制" to "word embedding"',
            {"mode": "path", "source": "注意力机制", "target": "word embedding"},
            QuerySpec(mode="path", source="注意力机制", target="word embedding"),
        ),
    ],
)
def test_natural_and_explicit_forms_are_equivalent(natural, explicit, expected):
    assert parse_natural_query(natural) == expected
    assert build_explicit_query(**explicit) == expected


@pytest.mark.parametrize("term", ["中文", "日本語", "العربية", "🧠", "C++ RAII"])
def test_find_operand_accepts_unicode_without_language_detection(term):
    assert build_explicit_query(mode="find", term=term).term == term


def test_natural_operands_decode_only_quote_and_backslash_escapes():
    parsed = parse_natural_query(r'find "C:\\docs\\\"quoted\""')
    assert parsed.term == 'C:\\docs\\"quoted"'


def test_description_is_versioned_and_derived_from_supported_templates():
    description = describe_query_language()
    assert description["grammar_version"] == GRAMMAR_VERSION == "query-language/v1"
    assert [item["mode"] for item in description["natural_templates"]] == [
        "find",
        "list",
        "path",
    ]
    assert description["canonical_cli"]["path"] == (
        "--mode path --from <source> --to <target>"
    )
```

- [ ] **Step 2: Run the new tests and verify the module is missing**

Run:

```bash
uv run --with pytest python -m pytest tests/test_query_language.py -q -p no:cacheprovider
```

Expected: collection fails with `ModuleNotFoundError: No module named 'obsidian_wiki.query_language'`.

- [ ] **Step 3: Implement the grammar, representation, normalization, and description**

Create `obsidian_wiki/query_language.py` with these public interfaces and one grammar source:

```python
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, Optional


GRAMMAR_VERSION = "query-language/v1"
QueryMode = Literal["find", "list", "path"]
_QUOTED = r'"((?:[^"\\]|\\["\\])*)"'


@dataclass(frozen=True)
class QuerySpec:
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
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_operand(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def normalize_match(value: str) -> str:
    return normalize_operand(value).casefold()


def _decode_quoted(value: str) -> str:
    return re.sub(r'\\(["\\])', r'\1', value)


_NATURAL_TEMPLATES = (
    _NaturalTemplate(
        "find",
        'find "<term>"',
        'find "注意力机制"',
        ("term",),
        re.compile(rf"find\s+{_QUOTED}", re.ASCII | re.IGNORECASE),
    ),
    _NaturalTemplate(
        "list",
        'list pages about "<term>"',
        'list pages about "深度学习"',
        ("term",),
        re.compile(
            rf"list\s+pages\s+about\s+{_QUOTED}",
            re.ASCII | re.IGNORECASE,
        ),
    ),
    _NaturalTemplate(
        "path",
        'find path from "<source>" to "<target>"',
        'find path from "注意力机制" to "词嵌入"',
        ("source", "target"),
        re.compile(
            rf"find\s+path\s+from\s+{_QUOTED}\s+to\s+{_QUOTED}",
            re.ASCII | re.IGNORECASE,
        ),
    ),
)


def build_explicit_query(
    *,
    mode: str,
    term: Optional[str] = None,
    source: Optional[str] = None,
    target: Optional[str] = None,
) -> QuerySpec:
    if mode not in {"find", "list", "path"}:
        raise QueryLanguageError("unsupported_operation", f"unsupported query mode: {mode}")
    supplied = {"term": term, "source": source, "target": target}
    required = {"find": {"term"}, "list": {"term"}, "path": {"source", "target"}}[mode]
    present = {name for name, value in supplied.items() if value is not None}
    if present != required:
        raise QueryLanguageError(
            "invalid_query_arguments",
            f"mode {mode} requires exactly: {', '.join(sorted(required))}",
        )
    normalized = {name: normalize_operand(value) for name, value in supplied.items() if value is not None}
    if any(not value for value in normalized.values()):
        raise QueryLanguageError("invalid_query_arguments", "query operands must not be empty")
    return QuerySpec(mode=mode, **normalized)


def parse_natural_query(question: str) -> QuerySpec:
    for template in _NATURAL_TEMPLATES:
        match = template.pattern.fullmatch(question)
        if match:
            values = [_decode_quoted(value) for value in match.groups()]
            return build_explicit_query(
                mode=template.mode,
                **dict(zip(template.operands, values)),
            )
    raise QueryLanguageError(
        "unsupported_query_structure",
        "query does not match query-language/v1",
    )


def describe_query_language() -> dict[str, Any]:
    return {
        "grammar_version": GRAMMAR_VERSION,
        "natural_templates": [
            {"mode": item.mode, "template": item.template, "example": item.example}
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
```

- [ ] **Step 4: Add rejection and normalization tests**

Append these tests to `tests/test_query_language.py`:

```python
@pytest.mark.parametrize(
    "question",
    [
        "注意力机制是什么",
        'fınd "topic"',
        "show me pages about deep learning",
        'find "topic" trailing',
        'find "unterminated',
        r'find "bad\nvalue"',
        'show gaps about "topic"',
        "transformer",
    ],
)
def test_unsupported_natural_structures_fail_closed(question):
    with pytest.raises(QueryLanguageError) as raised:
        parse_natural_query(question)
    assert raised.value.code == "unsupported_query_structure"


@pytest.mark.parametrize(
    "arguments",
    [
        {"mode": "find"},
        {"mode": "find", "term": "x", "source": "y"},
        {"mode": "path", "source": "x"},
        {"mode": "list", "term": "　"},
    ],
)
def test_invalid_explicit_argument_combinations_fail_closed(arguments):
    with pytest.raises(QueryLanguageError) as raised:
        build_explicit_query(**arguments)
    assert raised.value.code == "invalid_query_arguments"


def test_nfkc_is_applied_without_tokenizing_the_operand():
    parsed = parse_natural_query('find "ＡＩ 模型"')
    assert parsed.term == "AI 模型"
```

- [ ] **Step 5: Run the module tests**

Run:

```bash
uv run --with pytest python -m pytest tests/test_query_language.py -q -p no:cacheprovider
```

Expected: all tests in `tests/test_query_language.py` pass.

- [ ] **Step 6: Commit the query-language module**

```bash
git add obsidian_wiki/query_language.py tests/test_query_language.py
git commit -m "feat: define discoverable query language v1"
```

### Task 2: Repository-relative index identity and explicit ambiguity

**Files:**
- Modify: `obsidian_wiki/graphrag.py:31-151`
- Modify: `tests/test_graphrag.py:46-335`

- [ ] **Step 1: Write failing identity and link-resolution tests**

Add these tests to `TestBuildIndex` in `tests/test_graphrag.py`, updating the existing nested journal expectation from `{"daily", "entry"}` to `{"journal/daily", "journal/operations/entry"}`:

```python
def test_same_basename_pages_keep_repository_relative_identities(self, vault):
    concepts = vault / "concepts"
    projects = vault / "projects"
    concepts.mkdir()
    projects.mkdir()
    _page(concepts, "agent", title="Concept Agent", summary="Concept summary")
    _page(projects, "agent", title="Project Agent", summary="Project summary")

    index = build_index(vault)

    assert set(index) == {"concepts/agent", "projects/agent"}
    assert index["concepts/agent"]["path"] == "concepts/agent.md"
    assert index["projects/agent"]["path"] == "projects/agent.md"


def test_qualified_link_resolves_and_ambiguous_basename_link_does_not_guess(self, vault):
    concepts = vault / "concepts"
    projects = vault / "projects"
    notes = vault / "notes"
    concepts.mkdir()
    projects.mkdir()
    notes.mkdir()
    _page(concepts, "agent", title="Concept Agent")
    _page(projects, "agent", title="Project Agent")
    _page(notes, "qualified", links=["concepts/agent"])
    _page(notes, "ambiguous", links=["agent"])

    index = build_index(vault)

    assert index["notes/qualified"]["out_links"] == ["concepts/agent"]
    assert index["notes/ambiguous"]["out_links"] == []
    assert index["notes/ambiguous"]["ambiguous_links"] == [
        {"target": "agent", "candidates": ["concepts/agent", "projects/agent"]}
    ]
```

- [ ] **Step 2: Run the focused identity tests and verify the overwrite failure**

Run:

```bash
uv run --with pytest python -m pytest tests/test_graphrag.py::TestBuildIndex -q -p no:cacheprovider
```

Expected: the new tests fail because `build_index` still keys pages by basename slug and has no `ambiguous_links` field.

- [ ] **Step 3: Implement normalized relative identities and alias maps**

In `obsidian_wiki/graphrag.py`, import `PurePosixPath` and query normalization, then replace basename identity construction with these helpers:

```python
from pathlib import Path, PurePosixPath

from .query_language import normalize_match


def _page_id(relative: str) -> str:
    return normalize_match(PurePosixPath(relative).with_suffix("").as_posix())


def _aliases(page_id: str, entry: dict) -> set[str]:
    return {
        page_id,
        normalize_match(PurePosixPath(page_id).name),
        normalize_match(entry["title"]),
    }


def _alias_map(pages: dict[str, dict]) -> dict[str, list[str]]:
    aliases: defaultdict[str, list[str]] = defaultdict(list)
    for page_id, entry in pages.items():
        for alias in _aliases(page_id, entry):
            aliases[alias].append(page_id)
    return {alias: sorted(ids) for alias, ids in aliases.items()}
```

In the first pass, use `page_id = _page_id(page.relative)`, reject a duplicate normalized relative identity with `RuntimeError("duplicate normalized query page identity: ...")`, and initialize `ambiguous_links` to an empty list. Keep the original repository-relative Markdown path in `entry["path"]`.

In the second pass, resolve links deterministically. Add `_link_candidates` and
`_record_link` so both existing regex loops use the same rules:

```python
def _link_candidates(
    raw_target: str,
    pages: dict[str, dict],
    aliases: dict[str, list[str]],
) -> list[str]:
    target = normalize_match(raw_target.removesuffix(".md"))
    if "/" in target:
        return [target] if target in pages else []
    return aliases.get(target, [])


def _record_link(
    pages: dict[str, dict],
    aliases: dict[str, list[str]],
    page_id: str,
    raw_target: str,
) -> None:
    candidates = _link_candidates(raw_target, pages, aliases)
    if len(candidates) == 1 and candidates[0] != page_id:
        target = candidates[0]
        pages[page_id]["out_links"].append(target)
        pages[target]["in_links"].append(page_id)
    elif len(candidates) > 1:
        pages[page_id]["ambiguous_links"].append(
            {"target": raw_target, "candidates": candidates}
        )
```

Build `aliases = _alias_map(pages)` once before the second pass. Call
`_record_link(pages, aliases, page_id, link)` for every `_WIKILINK_RE` match. For
every `_MD_LINK_RE` match, remove its fragment and `.md` suffix before calling the
same helper. Preserve the current external/non-Markdown exclusions and safe snapshot
behavior.

- [ ] **Step 4: Update affected existing nested-path assertions and run index tests**

Replace the affected nested-file assertions with:

```python
assert set(index) == {"journal/daily", "journal/operations/entry"}
assert index["journal/operations/entry"]["out_links"] == ["journal/daily"]

assert set(index) == {"concepts/topic", "concepts/log"}
assert index["concepts/log"]["title"] == "Nested Log"
assert index["concepts/topic"]["out_links"] == ["concepts/log"]
assert index["concepts/topic"]["in_links"] == []
```

Root-level fixture IDs such as `transformer` remain unchanged.

Run:

```bash
uv run --with pytest python -m pytest tests/test_graphrag.py::TestBuildIndex -q -p no:cacheprovider
```

Expected: all `TestBuildIndex` tests pass, including the public-only body-read sentinel tests.

- [ ] **Step 5: Commit the identity refactor**

```bash
git add obsidian_wiki/graphrag.py tests/test_graphrag.py
git commit -m "fix: preserve query page identities and ambiguity"
```

### Task 3: Execute validated query modes without token guessing

**Files:**
- Modify: `obsidian_wiki/graphrag.py:156-390`
- Modify: `tests/test_graphrag.py:338-505`

- [ ] **Step 1: Replace classifier tests with phrase and mode behavior tests**

Remove imports and tests for `classify_query`. Import `QuerySpec` and add:

```python
from obsidian_wiki.query_language import QuerySpec


def test_chinese_phrase_matches_without_query_tokenization(vault):
    _page(
        vault,
        "attention",
        title="注意力机制",
        summary="用于序列建模的加权聚合方法",
        tags=["深度学习"],
    )

    result = query(vault, QuerySpec(mode="find", term="注意力机制"))

    assert result["status"] == "ok"
    assert result["candidates"][0]["page"] == "attention.md"


def test_find_does_not_split_or_expand_a_phrase(simple_vault):
    result = query(simple_vault, QuerySpec(mode="find", term="attention unknown"))
    assert result["status"] == "no_matches"
    assert result["candidates"] == []


def test_list_reports_total_and_truncation(vault):
    for index in range(3):
        _page(vault, f"page-{index}", summary="共享主题摘要", tags=["共享主题"])

    result = query(
        vault,
        QuerySpec(mode="list", term="共享主题"),
        top_n=2,
    )

    assert result["status"] == "ok"
    assert result["total_matches"] == 3
    assert result["truncated"] is True
    assert len(result["candidates"]) == 2


def test_mixed_language_path_query(vault):
    _page(vault, "attention", title="注意力机制", links=["embedding"])
    _page(vault, "embedding", title="Word Embedding")

    result = query(
        vault,
        QuerySpec(mode="path", source="注意力机制", target="Word Embedding"),
    )

    assert result["status"] == "ok"
    assert result["path"] == ["attention.md", "embedding.md"]


def test_path_distinguishes_no_match_from_no_path(vault):
    _page(vault, "left", title="Left")
    _page(vault, "right", title="Right")

    missing = query(vault, QuerySpec(mode="path", source="Missing", target="Right"))
    disconnected = query(vault, QuerySpec(mode="path", source="Left", target="Right"))

    assert missing["status"] == "no_matches"
    assert missing["unresolved_operands"] == ["source"]
    assert disconnected["status"] == "no_path"
```

- [ ] **Step 2: Add failing ambiguity and exact `index_only` tests**

Add:

```python
def test_path_rejects_ambiguous_endpoint_alias(vault):
    for folder in ("concepts", "projects"):
        directory = vault / folder
        directory.mkdir()
        _page(directory, "agent", title=f"{folder} agent")
    _page(vault, "target", title="Target")

    with pytest.raises(graphrag.QueryExecutionError) as raised:
        query(vault, QuerySpec(mode="path", source="agent", target="Target"))

    assert raised.value.code == "ambiguous_operand"
    assert raised.value.details["operand"] == "source"
    assert raised.value.details["candidates"] == [
        "concepts/agent.md",
        "projects/agent.md",
    ]


def test_index_only_requires_exact_title_or_identity_match(simple_vault):
    exact = query(
        simple_vault,
        QuerySpec(mode="find", term="Transformer Architecture"),
    )
    partial = query(
        simple_vault,
        QuerySpec(mode="find", term="Transformer"),
    )

    assert exact["index_only"] is True
    assert partial["index_only"] is False
```

- [ ] **Step 3: Run retrieval tests and verify the old signature/classifier fails**

Run:

```bash
uv run --with pytest python -m pytest tests/test_graphrag.py -q -p no:cacheprovider
```

Expected: failures show that `query` still expects a raw question, tokenizes it, and lacks `status`, `total_matches`, `truncated`, and typed ambiguity.

- [ ] **Step 4: Implement phrase scoring and endpoint resolution**

In `obsidian_wiki/graphrag.py`, import `Optional` from `typing`, delete
`_PATH_PATTERNS`, `_GAP_PATTERNS`, `_LIST_PATTERNS`, `_TIER_WEIGHT`, and
`classify_query`. Replace term-list scoring with one normalized operand and expose
the match kind. Wide lexical score bands preserve the specified match-kind order;
degree and tier can only order pages inside one band:

```python
class QueryExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _score(page_id: str, entry: dict, operand: str) -> tuple[float, Optional[str]]:
    term = normalize_match(operand)
    title = normalize_match(entry["title"])
    tags = [normalize_match(tag) for tag in entry["tags"]]
    summary = normalize_match(entry["summary"])
    basename = normalize_match(PurePosixPath(page_id).name)
    if term in {page_id, basename, title}:
        score, kind = 40.0, "exact"
    elif term and term in title:
        score, kind = 30.0, "title"
    elif term and any(term in tag for tag in tags):
        score, kind = 20.0, "tag"
    elif term and term in summary:
        score, kind = 10.0, "summary"
    else:
        return 0.0, None
    degree = len(entry["in_links"]) + len(entry["out_links"])
    tier_bonus = {"core": 0.3, "supporting": 0.0, "peripheral": -0.3}.get(
        entry["tier"], 0.0
    )
    score += min(degree * 0.1, 2.0) + tier_bonus
    return score, kind
```

Make `rank_candidates(index, operand)` return all positive matches in deterministic
`(-score, -in_degree, path)` order with `match_kind`; slice only in the operation
dispatcher so `list` can report the real total.

Add an endpoint resolver that first checks exact page ID, basename, and title aliases.
If multiple exact aliases match, raise `QueryExecutionError("ambiguous_operand", ...)`
with candidate Markdown paths. If there is no exact alias, use ranked substring
matches; return `None` for no match and raise the same error when more than one page
has the best lexical `match_kind`. Graph degree and tier bonuses may order candidates
for display but must not resolve an operand ambiguity.

- [ ] **Step 5: Dispatch `find`, `list`, and `path` over `QuerySpec`**

Change the public signature and return statuses:

```python
def query(
    vault: Path,
    spec: QuerySpec,
    *,
    top_n: int = 8,
    max_should_read: int = 3,
    public_only: bool = False,
) -> dict[str, Any]:
    index = build_index(vault, public_only=public_only)
    if spec.mode in {"find", "list"}:
        matches = rank_candidates(index, spec.term or "")
        selected = matches[:top_n]
        status = "ok" if selected else "no_matches"
        result = _candidate_result(
            spec,
            index,
            selected,
            max_should_read=max_should_read,
            status=status,
        )
        if spec.mode == "list":
            result["total_matches"] = len(matches)
            result["truncated"] = len(matches) > len(selected)
        return result

    source = resolve_operand(index, spec.source or "", operand_name="source")
    target = resolve_operand(index, spec.target or "", operand_name="target")
    unresolved = [
        name for name, value in (("source", source), ("target", target)) if value is None
    ]
    if unresolved:
        return _path_result(spec, index, status="no_matches", unresolved=unresolved)
    raw_path = find_path(index, source, target)
    return _path_result(
        spec,
        index,
        status="ok" if raw_path else "no_path",
        raw_path=raw_path or [],
    )
```

Keep the existing candidate trust fields and `should_read_metadata`. Set
`index_only=True` only for `find` when the top candidate has `match_kind == "exact"`
and a non-empty summary. List and path operations remain `index_only=False`. Every
result contains `grammar_version: query-language/v1`, `mode`, and `status`; replace
the old `answer_type` and tokenized `stats.query_terms` fields with `mode` and
`stats.query_operands`. Update the module docstring and its test to show the strict
natural and explicit v1 forms. Update all ranking tests to pass one phrase string
instead of a list of inferred terms, and change the empty-vault expectation to
`status == "no_matches"`.

The ranking calls become:

```python
rank_candidates(idx, "transformer")
rank_candidates(idx, "nlp")
rank_candidates(idx, "zzznomatch")
rank_candidates(idx, "deep-learning")
rank_candidates(idx, "deep-learning", top_n=1)
```

- [ ] **Step 6: Run all GraphRAG tests**

Run:

```bash
uv run --with pytest python -m pytest tests/test_graphrag.py -q -p no:cacheprovider
```

Expected: all GraphRAG tests pass; no test imports or exercises `classify_query`.

- [ ] **Step 7: Commit validated retrieval modes**

```bash
git add obsidian_wiki/graphrag.py tests/test_graphrag.py
git commit -m "feat: execute validated query modes"
```

### Task 4: CLI discovery, strict parsing, and structured recovery

**Files:**
- Modify: `obsidian_wiki/cli.py:1607-1649`
- Modify: `obsidian_wiki/cli.py:2487-2515`
- Modify: `tests/test_query_cli.py`

- [ ] **Step 1: Write failing context-free discovery and hard-cutover tests**

Add to `tests/test_query_cli.py`:

```python
def test_query_describe_is_context_free_and_machine_readable(tmp_path: Path) -> None:
    proc = _run(tmp_path / "home", "query", "--describe", "--json", "--pretty")

    assert proc.returncode == 0
    assert proc.stderr == ""
    data = json.loads(proc.stdout)
    assert data["grammar_version"] == "query-language/v1"
    assert [item["mode"] for item in data["natural_templates"]] == [
        "find",
        "list",
        "path",
    ]


def test_query_rejects_legacy_bare_question_without_resolving_repository(tmp_path: Path) -> None:
    proc = _run(tmp_path / "home", "query", "transformer", "--json")

    assert proc.returncode == 2
    assert proc.stderr == ""
    error = json.loads(proc.stdout)["error"]
    assert error["code"] == "unsupported_query_structure"
    assert 'find "<term>"' in error["templates"]


def test_query_rejects_mixed_natural_and_explicit_forms(tmp_path: Path) -> None:
    proc = _run(
        tmp_path / "home",
        "query",
        'find "topic"',
        "--mode",
        "find",
        "--term",
        "topic",
        "--json",
    )

    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"]["code"] == "invalid_query_arguments"
```

- [ ] **Step 2: Convert existing successful CLI queries to canonical explicit arguments**

Add a small test-only argument helper after `_run`:

```python
def _find_args(term: str) -> tuple[str, ...]:
    return ("query", "--mode", "find", "--term", term)
```

In every existing `tests/test_query_cli.py` success or runtime-resolution test,
replace a call shaped like:

```python
proc = _run(home, "query", "runtime resolver", "--json", cwd=nested)
```

with:

```python
proc = _run(home, *_find_args("runtime resolver"), "--json", cwd=nested)
```

Use `_find_args("anything")`, `_find_args("transformer")`,
`_find_args("runtime resolver")`, and `_find_args("launch")` in the corresponding
repository-required, successful lookup, invalid-config, and `--public-only` tests so
they continue testing runtime resolution rather than grammar validation.

- [ ] **Step 3: Add natural/explicit equivalence and status tests**

Add:

```python
def test_query_natural_and_explicit_forms_return_same_result(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "attention", title="注意力机制", summary="用于序列建模")

    natural = _run(home, "query", 'find "注意力机制"', "--json", cwd=root)
    explicit = _run(
        home,
        "query",
        "--mode",
        "find",
        "--term",
        "注意力机制",
        "--json",
        cwd=root,
    )

    assert natural.returncode == explicit.returncode == 0
    assert json.loads(natural.stdout) == json.loads(explicit.stdout)


def test_query_reports_no_matches_as_success(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "known", title="Known", summary="Known summary")

    proc = _run(
        home,
        "query",
        "--mode",
        "find",
        "--term",
        "不存在",
        "--json",
        cwd=root,
    )

    assert proc.returncode == 0
    assert json.loads(proc.stdout)["status"] == "no_matches"
```

- [ ] **Step 4: Run CLI tests and verify the old parser surface fails**

Run:

```bash
uv run --with pytest python -m pytest tests/test_query_cli.py -q -p no:cacheprovider
```

Expected: new tests fail because `question` is required, `--describe` and explicit
mode arguments do not exist, and `cmd_query` still passes a raw string.

- [ ] **Step 5: Add query CLI arguments without argparse-level mode guessing**

Replace the query parser block with:

```python
qq = sub.add_parser(
    "query",
    help="run a query-language/v1 operation against the configured vault",
)
qq.add_argument(
    "question",
    nargs="?",
    help="one exact query-language/v1 natural template",
)
qq.add_argument("--describe", action="store_true", help="describe query-language/v1")
qq.add_argument("--mode", help="explicit mode: find, list, or path")
qq.add_argument("--term", help="opaque Unicode operand for find or list")
qq.add_argument("--from", dest="source", help="opaque Unicode path source operand")
qq.add_argument("--to", dest="target", help="opaque Unicode path target operand")
qq.add_argument("--top", type=int, default=8, help="maximum returned candidates")
qq.add_argument("--max-read", type=int, default=3, help="maximum suggested page reads")
qq.add_argument(
    "--public-only",
    action="store_true",
    help="exclude visibility/internal and visibility/pii before body reads",
)
qq.add_argument("--json", action="store_true", help="emit machine-readable JSON")
qq.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
qq.set_defaults(func=cmd_query)
```

Do not use argparse `choices` or `required` for query-language fields: validation
must reach the stable `QueryLanguageError` renderer in JSON mode.

- [ ] **Step 6: Implement query validation and structured errors before runtime resolution**

Add these helpers near `_print_query`:

```python
def _query_error_payload(error) -> dict[str, object]:
    from obsidian_wiki.query_language import GRAMMAR_VERSION, describe_query_language

    payload: dict[str, object] = {
        "status": "error",
        "error": {
            "code": error.code,
            "message": str(error),
            "grammar_version": GRAMMAR_VERSION,
        },
    }
    if error.code == "unsupported_query_structure":
        payload["error"]["templates"] = [
            item["template"]
            for item in describe_query_language()["natural_templates"]
        ]
    details = getattr(error, "details", None)
    if details:
        payload["error"]["details"] = details
    return payload


def _render_query_error(args: argparse.Namespace, error: Exception) -> int:
    payload = _query_error_payload(error)
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(f"error: {payload['error']['message']}", file=sys.stderr)
        for template in payload["error"].get("templates", []):
            print(f"  {template}", file=sys.stderr)
    return 2
```

Refactor `cmd_query` in this order:

```python
def cmd_query(args: argparse.Namespace) -> int:
    from obsidian_wiki.graphrag import QueryExecutionError, query
    from obsidian_wiki.query_language import (
        QueryLanguageError,
        build_explicit_query,
        describe_query_language,
        parse_natural_query,
    )

    explicit_values = (args.mode, args.term, args.source, args.target)
    try:
        if args.describe:
            has_query_option = (
                args.question is not None
                or any(value is not None for value in explicit_values)
                or args.top != 8
                or args.max_read != 3
                or args.public_only
            )
            if has_query_option:
                raise QueryLanguageError(
                    "invalid_query_arguments",
                    "--describe cannot be combined with a query",
                )
            _json_print(describe_query_language(), pretty=args.pretty)
            return 0
        if args.question is not None and any(value is not None for value in explicit_values):
            raise QueryLanguageError(
                "invalid_query_arguments",
                "natural and explicit query forms cannot be mixed",
            )
        if args.question is not None:
            spec = parse_natural_query(args.question)
        else:
            if args.mode is None:
                raise QueryLanguageError(
                    "invalid_query_arguments",
                    "provide one natural template or --mode with its operands",
                )
            spec = build_explicit_query(
                mode=args.mode,
                term=args.term,
                source=args.source,
                target=args.target,
            )
    except QueryLanguageError as exc:
        return _render_query_error(args, exc)

    runtime = _resolve_runtime()
    if runtime is None:
        return 1
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1
    try:
        result = query(
            vault,
            spec,
            top_n=args.top,
            max_should_read=args.max_read,
            public_only=args.public_only,
        )
    except QueryExecutionError as exc:
        return _render_query_error(args, exc)
    if args.json:
        _json_print(result, pretty=args.pretty)
    else:
        _print_query(result)
    return 0
```

Make `--describe` print JSON even when `--json` is omitted because description is a
machine-readable operation by definition. Reject `--describe` combined with
repository query bounds or `--public-only` as `invalid_query_arguments` as well, so
ignored options never appear accepted.

Update `_print_query` to print `mode` and `status` first, followed by candidates,
path, and `should_read`; it must no longer index the removed `answer_type` field:

```python
def _print_query(result: dict[str, object]) -> None:
    print(f"mode: {result['mode']}")
    print(f"status: {result['status']}")
    candidates = result.get("candidates", [])
    if candidates:
        print("candidates:")
        for item in candidates:
            print(f"- {item['title']} ({item['page']}) score={item['score']}")
    path = result.get("path") or []
    if path:
        print("path:")
        print(" -> ".join(path))
    should_read = result.get("should_read") or []
    if should_read:
        print("should_read:")
        for page in should_read:
            print(f"- {page}")
```

- [ ] **Step 7: Run CLI and portable contract tests**

Run:

```bash
uv run --with pytest python -m pytest tests/test_query_cli.py tests/test_portable_only_contract.py -q -p no:cacheprovider
```

Expected: query CLI tests pass; the existing skill assertion still fails until Task
5 updates the documented command.

- [ ] **Step 8: Commit the CLI cutover**

```bash
git add obsidian_wiki/cli.py tests/test_query_cli.py
git commit -m "feat: expose strict query language CLI"
```

### Task 5: Agent skill and synchronized human documentation

**Files:**
- Modify: `obsidian_wiki/_data/skills/wiki-query/SKILL.md`
- Modify: `tests/test_portable_only_contract.py:1013-1020`
- Modify: `docs/cli.md:91-103`
- Modify: `docs/cli.zh-TW.md:60-70`
- Modify: `README.md`
- Modify: `README_ZH.md`

- [ ] **Step 1: Write failing installed-skill contract assertions**

Replace the legacy query assertion in
`test_read_only_workflows_use_canonical_config_and_real_cli_surfaces` and add exact
no-guess requirements:

```python
assert "llmwikiops query --describe --json" in query
assert 'llmwikiops query --mode find --term "<term>" --json --pretty' in query
assert 'llmwikiops query --mode list --term "<term>" --json --pretty' in query
assert (
    'llmwikiops query --mode path --from "<source>" --to "<target>" '
    "--json --pretty"
) in query
assert "query-language/v1" in query
assert "must not invent aliases, paraphrases, or parameter combinations" in query
assert "unsupported_query_structure" in query
assert "ambiguous_operand" in query
assert 'llmwikiops query "<question>"' not in query
```

- [ ] **Step 2: Run the skill contract test and verify the old guidance fails**

Run:

```bash
uv run --with pytest python -m pytest tests/test_portable_only_contract.py::test_read_only_workflows_use_canonical_config_and_real_cli_surfaces -q -p no:cacheprovider
```

Expected: FAIL because `wiki-query` still instructs the agent to send an arbitrary
`<question>`.

- [ ] **Step 3: Replace the skill retrieval section with the capability contract**

In `obsidian_wiki/_data/skills/wiki-query/SKILL.md`, retain the authority preflight,
read-only rules, public filtering, trust rules, and answer contract. Replace the
current retrieval command and free-form answer-type claims with this normative text:

````markdown
## Query-language discovery

Before the first query in this workflow, read the installed command contract:

```bash
llmwikiops query --describe --json
```

Require `grammar_version: query-language/v1`. The installed description is the
syntax authority. Prefer the explicit forms below and use opaque Unicode operands:

```bash
llmwikiops query --mode find --term "<term>" --json --pretty
llmwikiops query --mode list --term "<term>" --json --pretty
llmwikiops query --mode path --from "<source>" --to "<target>" --json --pretty
```

The only human-oriented natural templates are `find "<term>"`, `list pages about
"<term>"`, and `find path from "<source>" to "<target>"`. The English shell is
fixed; operands may use any language. You must not invent aliases, paraphrases, or
parameter combinations.

On `unsupported_query_structure`, rewrite once using a template returned by the
installed description. On `ambiguous_operand`, show the returned candidate paths and
ask the user to disambiguate. Treat `no_matches` and `no_path` as valid bounded
results, not parser failures. Stop on an unsupported grammar version.
````

Continue to tell the agent to use candidates, summaries, trust metadata, bounded
`should_read`, and returned paths; remove references to a `gap` answer type.

- [ ] **Step 4: Update CLI documentation with exact v1 syntax and migration**

Replace the query line in `docs/cli.md` with:

```markdown
llmwikiops query --describe --json
llmwikiops query 'find "TERM"' [--top TOP] [--max-read MAX_READ] [--public-only] [--json] [--pretty]
llmwikiops query --mode find --term TERM [--top TOP] [--max-read MAX_READ] [--public-only] [--json] [--pretty]
llmwikiops query --mode list --term TERM [--top TOP] [--max-read MAX_READ] [--public-only] [--json] [--pretty]
llmwikiops query --mode path --from SOURCE --to TARGET [--top TOP] [--max-read MAX_READ] [--public-only] [--json] [--pretty]
```

Immediately below it, document `query-language/v1`, fixed English syntax, opaque
Unicode operands, `ok`/`no_matches`/`no_path`, strict exit-2 syntax failures, and the
hard migration from `llmwikiops query "topic"` to `--mode find --term "topic"`.

Make the equivalent update in Traditional Chinese in `docs/cli.zh-TW.md`, keeping the
command examples byte-for-byte identical.

- [ ] **Step 5: Add aligned README discovery pointers**

Before each README's Documentation section, add matching paragraphs with the same
commands. English:

```markdown
Knowledge queries use a discoverable, versioned grammar. Inspect it with
`llmwikiops query --describe --json`; agents should prefer explicit calls such as
`llmwikiops query --mode find --term "注意力机制" --json`. The syntax shell is fixed
English while quoted operands may use any language. See the CLI reference for the
complete fail-closed contract.
```

Simplified Chinese:

```markdown
知识检索采用可发现、带版本的语法。通过
`llmwikiops query --describe --json` 查看完整能力；Agent 应优先使用
`llmwikiops query --mode find --term "注意力机制" --json` 等显式调用。句法外壳固定为
英文，引号内参数可以使用任何语言。完整的失败关闭契约见 CLI 参考。
```

- [ ] **Step 6: Run skill and documentation checks**

Run:

```bash
uv run --with pytest python -m pytest tests/test_portable_only_contract.py tests/test_portable_human_docs.py -q -p no:cacheprovider
uv run python tools/check_readme_sync.py
```

Expected: both pytest files pass, and the sync tool prints
`README_ZH.md is up to date with README.md.`

- [ ] **Step 7: Commit the agent and human contract**

```bash
git add README.md README_ZH.md docs/cli.md docs/cli.zh-TW.md obsidian_wiki/_data/skills/wiki-query/SKILL.md tests/test_portable_only_contract.py
git commit -m "docs: publish query language v1 contract"
```

### Task 6: Cross-version verification and cleanup

**Files:**
- Modify only files already listed when verification reveals a regression directly caused by this feature.

- [ ] **Step 1: Search for stale free-form query surfaces**

Run:

```bash
rg -n --glob '!docs/superpowers/**' 'llmwikiops query "(topic|<question>|transformer|runtime resolver|launch)"|classify_query|answer_type.*gap' README.md README_ZH.md docs obsidian_wiki tests
```

Expected: no output; no active product documentation, runtime skill, implementation,
or current test uses the removed free-form surface. Historical design and plan files
are excluded and must not be rewritten.

- [ ] **Step 2: Run all focused query tests with bytecode and pytest cache disabled**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest tests/test_query_language.py tests/test_graphrag.py tests/test_query_cli.py tests/test_portable_only_contract.py -q -p no:cacheprovider
```

Expected: all focused tests pass with zero failures.

- [ ] **Step 3: Run README synchronization**

Run:

```bash
uv run python tools/check_readme_sync.py
```

Expected: exit 0 and `README_ZH.md is up to date with README.md.`

- [ ] **Step 4: Run the complete test suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
```

Expected: the full suite passes with zero failures.

- [ ] **Step 5: Inspect the final change set**

Run:

```bash
git status --short
git diff --check
git log --oneline -6
```

Expected: only intentional query-language files are modified, `git diff --check`
prints nothing, and the task commits appear in order.

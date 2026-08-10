# Restricted Nested Frontmatter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make portable transactions accept the framework-defined `provenance` mapping and `relationships` list of mappings while every other nested YAML shape remains rejected.

**Architecture:** Extend the existing line-oriented safe parser with explicit immutable `Provenance` and `Relationship` values, plus an authoritative top-level field set. Portable validators consume the complete parsed representation; lint reuses the same restricted relationship-block parser while retaining its existing broad, read-only extraction of unrelated legacy scalar fields such as block summaries.

**Tech Stack:** Python 3.8+, frozen dataclasses, pytest, the existing portable transaction/check/lint layers, and Git-based collaboration tests. No YAML dependency is added.

---

## Scope and file map

The approved design is
`docs/superpowers/specs/2026-08-10-restricted-nested-frontmatter-design.md`.
This plan implements only that design. Portable CLI context warnings, `info`
resolution, and transaction recovery guidance remain separate subprojects.

- Modify `obsidian_wiki/frontmatter.py`: own the two supported nested types,
  their exact grammar, duplicate handling, field-name accounting, and a
  relationship-only compatibility entry point for lint.
- Modify `obsidian_wiki/transaction.py`: use the authoritative parsed field set
  in candidate and existing-page validation.
- Modify `obsidian_wiki/portable_check.py`: use the authoritative parsed field
  set during read-only repository checks.
- Modify `obsidian_wiki/operations.py`: keep operation pages exact by counting
  supported nested fields as unexpected fields.
- Modify `obsidian_wiki/lint.py`: delete the independent relationship parser and
  consume typed relationships from `frontmatter.py`.
- Modify `tests/test_portable_check.py`: cover parser grammar and portable check
  acceptance/rejection.
- Modify `tests/test_transaction.py`: prove canonical nested frontmatter commits
  without bypassing validation.
- Modify `tests/test_operations.py`: prove nested knowledge metadata cannot be
  smuggled into immutable operation pages.
- Modify `tests/test_portable_migration.py`: prove legacy source rewriting
  preserves both supported nested blocks.
- Modify `tests/test_lint.py` and `tests/test_trust.py`: preserve lint behavior
  and typed relationship semantics through the shared parser.
- Modify `tests/test_portable_collaboration_e2e.py`: prove transaction, Git
  clone, doctor, and check compatibility.
- Modify `docs/architecture.md`: document the deliberately restricted
  frontmatter boundary without changing the landing-page READMEs.

### Task 1: Add the typed provenance representation and grammar

**Files:**
- Modify: `tests/test_portable_check.py:9-223`
- Modify: `obsidian_wiki/frontmatter.py:1-205`

- [ ] **Step 1: Write failing provenance parser tests**

Extend the import in `tests/test_portable_check.py` and add these tests beside
the existing parser tests:

```python
from obsidian_wiki.frontmatter import (
    FrontmatterError,
    Provenance,
    parse_frontmatter,
)


def test_parse_framework_provenance_mapping() -> None:
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
        extracted="0.72",
        inferred="0.25",
        ambiguous="0.03",
    )
    assert parsed.fields == frozenset({"title", "provenance"})


@pytest.mark.parametrize(
    ("block", "match"),
    [
        (
            "provenance:\n  extracted: 1.0\n  inferred: 0.0\n",
            "provenance.*missing.*ambiguous",
        ),
        (
            "provenance:\n  extracted: 1.0\n  inferred: 0.0\n  ambiguous: 0.0\n  source: x",
            "provenance.*unknown.*source",
        ),
        (
            "provenance:\n  extracted: 1.0\n  extracted: 0.5\n  inferred: 0.0\n  ambiguous: 0.0",
            "provenance.*duplicate.*extracted",
        ),
        (
            "provenance:\n  extracted:\n  inferred: 0.0\n  ambiguous: 1.0",
            "provenance.*empty.*extracted",
        ),
        (
            "provenance:\n   extracted: 1.0\n  inferred: 0.0\n  ambiguous: 0.0",
            "provenance.*indent",
        ),
        (
            "provenance:\n\textracted: 1.0\n  inferred: 0.0\n  ambiguous: 0.0",
            "provenance.*indent",
        ),
        (
            "provenance:\n  extracted: {value: 1.0}\n  inferred: 0.0\n  ambiguous: 0.0",
            "flow collection",
        ),
        (
            "provenance:\n  extracted: &score 1.0\n  inferred: 0.0\n  ambiguous: *score",
            "tag.*anchor.*alias",
        ),
    ],
)
def test_parse_rejects_non_framework_provenance_shapes(
    block: str, match: str
) -> None:
    with pytest.raises(FrontmatterError, match=match):
        parse_frontmatter(f"---\n{block}\n---\n")
```

Replace `test_nested_mapping_is_not_interpreted_as_frontmatter` with a test that
continues to reject an arbitrary nested key:

```python
def test_arbitrary_nested_mapping_is_not_interpreted_as_frontmatter() -> None:
    with pytest.raises(FrontmatterError, match="malformed"):
        parse_frontmatter("---\nmetadata:\n  source: sources/a.md\n---\n")
```

- [ ] **Step 2: Run the provenance tests and verify red state**

Run:

```bash
uv run pytest tests/test_portable_check.py -k 'provenance or arbitrary_nested' -q
```

Expected: the canonical provenance test fails because `Provenance` and
`Frontmatter.provenance` do not exist, while the arbitrary nested mapping test
still passes.

- [ ] **Step 3: Add immutable types and exact provenance parsing**

Add these definitions in `obsidian_wiki/frontmatter.py`:

```python
_PROVENANCE_FIELDS = frozenset({"extracted", "inferred", "ambiguous"})


@dataclass(frozen=True)
class Provenance:
    extracted: str
    inferred: str
    ambiguous: str


@dataclass(frozen=True)
class Relationship:
    target: str
    type: str


@dataclass(frozen=True)
class Frontmatter:
    scalars: dict[str, str]
    lists: dict[str, tuple[str, ...]]
    provenance: Provenance | None = None
    relationships: tuple[Relationship, ...] | None = None

    @property
    def fields(self) -> frozenset[str]:
        names = set(self.scalars) | set(self.lists)
        if self.provenance is not None:
            names.add("provenance")
        if self.relationships is not None:
            names.add("relationships")
        return frozenset(names)
```

Factor the opening/closing delimiter check into this helper so both public
entry points use identical document boundaries:

```python
def _document_lines(text: str) -> tuple[list[str], int]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("frontmatter opening delimiter is missing")
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if closing is None:
        raise FrontmatterError("frontmatter closing delimiter is missing")
    return lines, closing
```

Add an exact two-space mapping parser. It keeps leaf values as strings and
names every structural failure:

```python
def _parse_provenance(
    lines: list[str], index: int, closing: int
) -> tuple[Provenance, int]:
    values: dict[str, str] = {}
    while index < closing:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if not line[0].isspace():
            break
        if (
            not line.startswith("  ")
            or len(line) <= 2
            or line[2].isspace()
            or ":" not in line[2:]
        ):
            raise FrontmatterError("malformed provenance indentation")
        key, raw = line[2:].split(":", 1)
        key = key.strip()
        if key not in _PROVENANCE_FIELDS:
            raise FrontmatterError(f"provenance has unknown field: {key!r}")
        if key in values:
            raise FrontmatterError(f"provenance has duplicate field: {key!r}")
        value = _scalar(raw)
        if not value:
            raise FrontmatterError(f"provenance has empty field: {key!r}")
        values[key] = value
        index += 1
    missing = sorted(_PROVENANCE_FIELDS - set(values))
    if missing:
        raise FrontmatterError(
            "provenance is missing required fields: " + ", ".join(missing)
        )
    return (
        Provenance(
            extracted=values["extracted"],
            inferred=values["inferred"],
            ambiguous=values["ambiguous"],
        ),
        index,
    )
```

Update `parse_frontmatter` to use `_document_lines`, one `seen` set for every
top-level key, and this dispatch before ordinary empty-value list parsing:

```python
seen: set[str] = set()
provenance: Provenance | None = None
relationships: tuple[Relationship, ...] | None = None

# After splitting key/raw:
if not key or key in seen:
    raise FrontmatterError(f"duplicate or empty frontmatter key: {key!r}")
seen.add(key)
raw = _strip_comment(raw).strip()
if key == "provenance":
    if raw:
        raise FrontmatterError("provenance must be a block mapping")
    provenance, index = _parse_provenance(lines, index + 1, closing)
    continue
```

Return all four fields explicitly:

```python
return Frontmatter(
    scalars=scalars,
    lists=lists,
    provenance=provenance,
    relationships=relationships,
)
```

- [ ] **Step 4: Run parser tests and verify green state**

Run:

```bash
uv run pytest tests/test_portable_check.py -k 'parse' -q
```

Expected: every parser test passes; arbitrary nested mappings and advanced YAML
features remain rejected.

- [ ] **Step 5: Commit the provenance parser slice**

```bash
git add obsidian_wiki/frontmatter.py tests/test_portable_check.py
git commit -m "feat: parse restricted provenance frontmatter"
```

### Task 2: Add the typed relationships grammar

**Files:**
- Modify: `tests/test_portable_check.py:9-223`
- Modify: `obsidian_wiki/frontmatter.py:1-280`

- [ ] **Step 1: Write failing relationship grammar tests**

Import `Relationship` and add:

```python
def test_parse_framework_relationships_in_both_block_item_forms() -> None:
    parsed = parse_frontmatter(
        """---
relationships:
  - type: uses
    target: "[[concepts/attention]]"
  - # expanded form
    target: '[[concepts/lstm]]' # supported target
    type: contradicts
---
"""
    )

    assert parsed.relationships == (
        Relationship(target="[[concepts/attention]]", type="uses"),
        Relationship(target="[[concepts/lstm]]", type="contradicts"),
    )
    assert parsed.fields == frozenset({"relationships"})


def test_parse_empty_framework_relationships_list() -> None:
    parsed = parse_frontmatter("---\nrelationships: [] # no typed edges\n---\n")

    assert parsed.relationships == ()
    assert parsed.fields == frozenset({"relationships"})


@pytest.mark.parametrize(
    "page",
    [
        """---
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
provenance:
  extracted: 0.5
  inferred: 0.5
  ambiguous: 0.0
---
""",
        """---
relationships: []
relationships:
  - target: "[[a]]"
    type: related_to
---
""",
    ],
)
def test_duplicate_nested_top_level_keys_are_rejected(page: str) -> None:
    with pytest.raises(FrontmatterError, match="duplicate"):
        parse_frontmatter(page)


@pytest.mark.parametrize(
    ("block", "match"),
    [
        (
            'relationships:\n  - target: "[[a]]"',
            "relationships.*missing.*type",
        ),
        (
            'relationships:\n  - target: "[[a]]"\n    type: uses\n    weight: 1',
            "relationships.*unknown.*weight",
        ),
        (
            'relationships:\n  - target: "[[a]]"\n    target: "[[b]]"\n    type: uses',
            "relationships.*duplicate.*target",
        ),
        (
            'relationships:\n  - target: "[[a]]"\n    type:',
            "relationships.*empty.*type",
        ),
        (
            'relationships:\n   - target: "[[a]]"\n    type: uses',
            "relationships.*indent",
        ),
        (
            'relationships: [{target: "[[a]]", type: uses}]',
            "relationships.*block list",
        ),
        (
            'relationships:\n  - target: "[[a]]"\n    type: &kind uses',
            "tag.*anchor.*alias",
        ),
    ],
)
def test_parse_rejects_non_framework_relationship_shapes(
    block: str, match: str
) -> None:
    with pytest.raises(FrontmatterError, match=match):
        parse_frontmatter(f"---\n{block}\n---\n")
```

- [ ] **Step 2: Run the relationship tests and verify red state**

Run:

```bash
uv run pytest tests/test_portable_check.py -k 'relationship' -q
```

Expected: the acceptance tests fail because `relationships` is still treated as
an ordinary scalar list.

- [ ] **Step 3: Implement the fixed two-field list-of-mappings parser**

Add the relationship field set and exact block parser:

```python
_RELATIONSHIP_FIELDS = frozenset({"target", "type"})


def _relationship_field(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        raise FrontmatterError("malformed relationships field")
    key, value_raw = raw.split(":", 1)
    key = key.strip()
    if key not in _RELATIONSHIP_FIELDS:
        raise FrontmatterError(f"relationships has unknown field: {key!r}")
    value = _scalar(value_raw)
    if not value:
        raise FrontmatterError(f"relationships has empty field: {key!r}")
    return key, value


def _finish_relationship(values: dict[str, str]) -> Relationship:
    missing = sorted(_RELATIONSHIP_FIELDS - set(values))
    if missing:
        raise FrontmatterError(
            "relationships item is missing required fields: " + ", ".join(missing)
        )
    return Relationship(target=values["target"], type=values["type"])


def _parse_relationships_block(
    lines: list[str], index: int, closing: int
) -> tuple[tuple[Relationship, ...], int]:
    items: list[Relationship] = []
    current: dict[str, str] | None = None
    while index < closing:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if not line[0].isspace():
            break
        if line == "  -" or (
            line.startswith("  - ") and not _strip_comment(line[4:])
        ):
            if current is not None:
                items.append(_finish_relationship(current))
            current = {}
            index += 1
            continue
        if line.startswith("  - "):
            if current is not None:
                items.append(_finish_relationship(current))
            key, value = _relationship_field(line[4:])
            current = {key: value}
            index += 1
            continue
        if (
            current is None
            or not line.startswith("    ")
            or len(line) <= 4
            or line[4].isspace()
        ):
            raise FrontmatterError("malformed relationships indentation")
        key, value = _relationship_field(line[4:])
        if key in current:
            raise FrontmatterError(f"relationships has duplicate field: {key!r}")
        current[key] = value
        index += 1
    if current is None:
        raise FrontmatterError("relationships block must contain a list item")
    items.append(_finish_relationship(current))
    return tuple(items), index
```

In `parse_frontmatter`, dispatch `relationships` before ordinary inline/block
list parsing:

```python
if key == "relationships":
    if raw == "[]":
        relationships = ()
        index += 1
        continue
    if raw:
        raise FrontmatterError("relationships must be a block list")
    relationships, index = _parse_relationships_block(lines, index + 1, closing)
    continue
```

The implementation must use the existing `_scalar` function for every nested
leaf, so quoted hash characters work and unquoted tags, anchors, aliases, and
flow collections remain rejected.

- [ ] **Step 4: Add a relationship-only compatibility entry point for lint**

Add this public function to `obsidian_wiki/frontmatter.py`. It intentionally
ignores unrelated top-level values, allowing read-only lint to retain support
for pre-existing block scalar summaries without duplicating relationship
grammar:

```python
def parse_relationships(text: str) -> tuple[Relationship, ...] | None:
    lines, closing = _document_lines(text)
    relationships: tuple[Relationship, ...] | None = None
    found = False
    index = 1
    while index < closing:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#") or line[0].isspace():
            index += 1
            continue
        if ":" not in line:
            index += 1
            continue
        key, raw = line.split(":", 1)
        if key.strip() != "relationships":
            index += 1
            continue
        if found:
            raise FrontmatterError("duplicate frontmatter key: 'relationships'")
        found = True
        raw = _strip_comment(raw).strip()
        if raw == "[]":
            relationships = ()
            index += 1
            continue
        if raw:
            raise FrontmatterError("relationships must be a block list")
        relationships, index = _parse_relationships_block(lines, index + 1, closing)
    return relationships
```

- [ ] **Step 5: Run the complete parser test slice**

Run:

```bash
uv run pytest tests/test_portable_check.py -k 'parse' -q
```

Expected: all parser tests pass, including legacy scalar/list cases and the
new exact nested shapes.

- [ ] **Step 6: Commit the relationship grammar slice**

```bash
git add obsidian_wiki/frontmatter.py tests/test_portable_check.py
git commit -m "feat: parse restricted relationship frontmatter"
```

### Task 3: Wire the authoritative field set into portable consumers

**Files:**
- Modify: `obsidian_wiki/transaction.py:176-190,1564-1578`
- Modify: `obsidian_wiki/portable_check.py:392-410`
- Modify: `obsidian_wiki/operations.py:720-733`
- Modify: `tests/test_transaction.py:25-36,1198-1220`
- Modify: `tests/test_portable_check.py:231-299`
- Modify: `tests/test_operations.py:59-77`
- Modify: `tests/test_portable_migration.py:503-535`

- [ ] **Step 1: Write a failing canonical transaction test**

Add this fixture text near `PAGE` in `tests/test_transaction.py`:

```python
FRAMEWORK_PAGE = PAGE.replace(
    "updated: 2026-08-07\n",
    """updated: 2026-08-07
relationships:
  - target: "[[index]]"
    type: related_to
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
""",
)
```

Add the commit regression:

```python
def test_commit_accepts_framework_nested_frontmatter(
    tmp_path: Path, operation_writer
) -> None:
    root, config = make_config(tmp_path)
    manager = TransactionManager(config, operation_writer=operation_writer(config))
    record = manager.begin([add_source(root)], transaction_id="tx-nested")
    candidate_page(record, "concepts/a.md", FRAMEWORK_PAGE)

    result = manager.commit(
        "tx-nested", completed_at="2026-08-07T01:00:00Z"
    )

    assert result.created == ("concepts/a.md",)
    assert (config.vault / "concepts/a.md").read_text(encoding="utf-8") == FRAMEWORK_PAGE
    assert ShardedManifest(config).load("sources/a.md").pages == ("concepts/a.md",)
```

- [ ] **Step 2: Write portable-check and operation exactness tests**

Add to `tests/test_portable_check.py`:

```python
def test_valid_repo_accepts_framework_nested_frontmatter(tmp_path: Path) -> None:
    _, config, _, page, _ = valid_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "summary: A compiled example.",
            """summary: A compiled example.
relationships:
  - target: "[[concepts/a]]"
    type: related_to
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0""",
        ),
        encoding="utf-8",
    )

    assert check_portable_repo(config)["status"] == "pass"
```

Add to `tests/test_operations.py`:

```python
def test_operation_rejects_knowledge_only_nested_frontmatter(tmp_path: Path) -> None:
    change = OperationChange(
        "tx-1", "2026-08-07T07:30:00Z", ("sources/a.md",), (), (), ()
    )
    path = write_operation(tmp_path, change, suffix="a81f")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "updated: 2026-08-07",
            """updated: 2026-08-07
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(OperationError, match="frontmatter fields"):
        validate_operation(path)
```

Add to `tests/test_portable_migration.py`:

```python
def test_apply_preserves_framework_nested_frontmatter(tmp_path: Path) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    nested = """relationships:
  - target: "[[index]]"
    type: related_to
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
"""
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "created: 2026-08-07", nested + "created: 2026-08-07"
        ),
        encoding="utf-8",
    )
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert plan.blockers == ()
    apply_migration(plan, installed_version="2026.8", source_skills=skills_dir())

    migrated = page.read_text(encoding="utf-8")
    assert nested in migrated
    assert str(source) not in migrated
    assert "sources/a.md" in migrated
```

- [ ] **Step 3: Run the new consumer tests and verify the field-set failure**

Run:

```bash
uv run pytest \
  tests/test_transaction.py::test_commit_accepts_framework_nested_frontmatter \
  tests/test_portable_check.py::test_valid_repo_accepts_framework_nested_frontmatter \
  tests/test_operations.py::test_operation_rejects_knowledge_only_nested_frontmatter \
  tests/test_portable_migration.py::test_apply_preserves_framework_nested_frontmatter \
  -q
```

Expected before consumer changes: the two knowledge-page tests pass through the
parser, but the operation exactness test fails because the old field-set
expression does not count `provenance`.

- [ ] **Step 4: Replace hand-built field sets with `Frontmatter.fields`**

Make these exact substitutions:

```python
# obsidian_wiki/transaction.py, both candidate and knowledge-page checks
fields = frontmatter.fields

# obsidian_wiki/portable_check.py
fields = parsed.fields

# obsidian_wiki/operations.py
fields = parsed.fields
```

Do not change `_REQUIRED_FRONTMATTER`, `_REQUIRED_FIELDS`, or
`_FRONTMATTER_FIELDS`. The first two remain the six-field page baseline; the
operation set remains exact and therefore rejects both knowledge-only fields.

- [ ] **Step 5: Run focused transaction/check/operation suites**

Run:

```bash
uv run pytest \
  tests/test_transaction.py \
  tests/test_portable_check.py \
  tests/test_operations.py \
  tests/test_portable_migration.py \
  -q
```

Expected: all tests pass. Transaction source ownership still comes exclusively
from `frontmatter.lists["sources"]`.

- [ ] **Step 6: Commit the portable consumer slice**

```bash
git add \
  obsidian_wiki/transaction.py \
  obsidian_wiki/portable_check.py \
  obsidian_wiki/operations.py \
  tests/test_transaction.py \
  tests/test_portable_check.py \
  tests/test_operations.py \
  tests/test_portable_migration.py
git commit -m "fix: accept framework metadata in portable pages"
```

### Task 4: Make lint consume the shared typed relationships

**Files:**
- Modify: `obsidian_wiki/lint.py:5-180`
- Modify: `tests/test_lint.py:17-125,344-376`
- Modify: `tests/test_trust.py:727-837`

- [ ] **Step 1: Add lint compatibility and fail-closed tests**

Add to `tests/test_lint.py`:

```python
def test_lint_uses_shared_relationship_parser_with_block_summary(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    alpha = _page(vault, "concepts/alpha.md", links=["beta"])
    _page(vault, "concepts/beta.md", links=["alpha"])
    alpha.write_text(
        alpha.read_text(encoding="utf-8").replace(
            "summary: Short summary.",
            """summary: >-
  A legacy folded summary remains readable by lint.
relationships:
  - target: "[[concepts/beta]]"
    type: related_to""",
        ),
        encoding="utf-8",
    )

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["missing_summaries"] == []
    assert report["findings"]["typed_relationship_issues"] == []


def test_lint_maps_structurally_invalid_relationships_to_one_issue(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    alpha = _page(vault, "concepts/alpha.md")
    alpha.write_text(
        alpha.read_text(encoding="utf-8").replace(
            "summary: Short summary.",
            """summary: Short summary.
relationships:
  - target: "[[concepts/beta]]"
    type: related_to
    weight: 5""",
        ),
        encoding="utf-8",
    )

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["typed_relationship_issues"] == [
        {
            "page": "concepts/alpha.md",
            "index": 0,
            "issue": "malformed_relationship_entry",
        }
    ]
```

Change `test_empty_relationship_cli_extension_cannot_hide_missing_relation_type`
to expect `malformed_relationship_entry`, because an empty nested leaf is now a
structural error under the approved schema:

```python
assert json.loads(baseline.stdout)["findings"]["typed_relationship_issues"] == [
    {
        "page": "concepts/alpha.md",
        "index": 0,
        "issue": "malformed_relationship_entry",
    }
]
```

- [ ] **Step 2: Run the lint tests and verify red state**

Run:

```bash
uv run pytest \
  tests/test_lint.py::test_lint_uses_shared_relationship_parser_with_block_summary \
  tests/test_lint.py::test_lint_maps_structurally_invalid_relationships_to_one_issue \
  tests/test_lint.py::test_empty_relationship_cli_extension_cannot_hide_missing_relation_type \
  -q
```

Expected: the empty-leaf expectation and the new shared-parser behavior fail
against lint's independent permissive parser.

- [ ] **Step 3: Delete lint's independent relationship grammar**

In `obsidian_wiki/lint.py`, import the shared parser:

```python
from obsidian_wiki.frontmatter import FrontmatterError, parse_relationships
```

Delete `_RELATIONSHIP_LIST_FIELD_RE`, `_RELATIONSHIP_ITEM_START_RE`,
`_RELATIONSHIP_FIELD_RE`, `_relationship_scalar`, and `_parse_relationships`.
Replace them with this representation adapter only:

```python
def _typed_relationships(text: str) -> list[dict[str, str]]:
    try:
        relationships = parse_relationships(text)
    except FrontmatterError:
        return [{"parse_error": "malformed_relationship_entry"}]
    return [
        {"target": relationship.target, "type": relationship.type}
        for relationship in relationships or ()
    ]
```

Change `_parse_page` to use the complete text rather than the regex-extracted
frontmatter for relationship parsing:

```python
"relationships": _typed_relationships(text),
```

Keep `_parse_frontmatter_values` unchanged. It is a read-only compatibility
extractor for title/summary and is not used by portable transaction validation.

- [ ] **Step 4: Preserve all typed relationship semantic diagnostics**

Run:

```bash
uv run pytest tests/test_lint.py tests/test_trust.py -q
```

Expected: all tests pass. Valid types, invalid types, exact-path resolution,
missing targets, ambiguous targets, target-first items, dash-only items, empty
lists, and owner schema extensions retain their existing results. Unknown,
duplicate, missing, and empty structural fields fail closed as one
`malformed_relationship_entry` issue.

- [ ] **Step 5: Commit the lint unification slice**

```bash
git add obsidian_wiki/lint.py tests/test_lint.py tests/test_trust.py
git commit -m "refactor: share restricted relationship parsing"
```

### Task 5: Prove clone portability and document the boundary

**Files:**
- Modify: `tests/test_portable_collaboration_e2e.py:1-285`
- Modify: `docs/architecture.md:97-120,274-281`

- [ ] **Step 1: Write a transaction-to-clone end-to-end regression**

Import `sys` and `parse_frontmatter`, then add this CLI helper to
`tests/test_portable_collaboration_e2e.py`:

```python
def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = _git_environment(root)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1])
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
)
```

First replace `_page` so every existing collaboration ingest also exercises the
canonical framework shape:

```python
def _page(*, title: str, source_id: str, created: str) -> str:
    return f"""---
title: {title}
category: concepts
tags:
  - example
sources:
  - {source_id}
created: {created}
updated: {created}
summary: Knowledge compiled from {source_id}.
relationships:
  - target: "[[index]]"
    type: related_to
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
---
# {title}
"""
```

Then add the regression. It does not edit the live page after commit; both
nested blocks pass through `TransactionManager.commit`:

```python
def test_framework_nested_frontmatter_survives_transaction_and_clone(
    tmp_path: Path,
) -> None:
    seed = _portable_seed(tmp_path)
    _git(seed, "init", "-q")
    _git(seed, "config", "user.email", "seed@example.invalid")
    _git(seed, "config", "user.name", "Seed")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "portable seed")

    source_id = "sources/framework-page.md"
    page = "concepts/framework-page.md"
    _ingest(
        seed,
        source_id=source_id,
        page=page,
        title="Framework Page",
        transaction_id="framework-nested",
        started_at="2026-08-10T01:00:00Z",
        completed_at="2026-08-10T01:05:00Z",
        operation_suffix="f12a",
        source_bytes=b"framework nested frontmatter\n",
    )
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "ingest framework page")

    clone = tmp_path / "clone"
    _clone(seed, clone)
    for root in (seed, clone):
        doctor = _cli(root, "doctor", "--json")
        check = _cli(root, "check", "--json")
        assert doctor.returncode == 0, doctor.stdout + doctor.stderr
        assert check.returncode == 0, check.stdout + check.stderr
        assert json.loads(doctor.stdout)["status"] == "pass"
        assert json.loads(check.stdout)["status"] == "pass"
        parsed = parse_frontmatter((root / "wiki" / page).read_text(encoding="utf-8"))
        assert parsed.provenance is not None
        assert parsed.relationships is not None
```

- [ ] **Step 2: Run the end-to-end test**

Run:

```bash
uv run pytest \
  tests/test_portable_collaboration_e2e.py::test_framework_nested_frontmatter_survives_transaction_and_clone \
  -q
```

Expected: PASS. Both the original repository and a fresh clone pass doctor and
check with both nested fields intact.

- [ ] **Step 3: Document the restricted parser boundary**

After the required-frontmatter sentence in `docs/architecture.md`, add:

```markdown
Portable validation deliberately accepts a restricted frontmatter grammar:
top-level scalar values, scalar lists, the framework `provenance` mapping
(`extracted`, `inferred`, `ambiguous`), and the framework `relationships` list
of mappings (`target`, `type`). These are the only supported nested shapes.
Arbitrary nested YAML, flow mappings, tags, anchors, and aliases fail closed;
the CLI does not load general YAML objects from knowledge pages.
```

README files do not change because this is architecture detail rather than a
landing-page command or workflow change.

- [ ] **Step 4: Run the complete feature test set**

Run:

```bash
uv run pytest \
  tests/test_portable_check.py \
  tests/test_transaction.py \
  tests/test_operations.py \
  tests/test_portable_migration.py \
  tests/test_lint.py \
  tests/test_trust.py \
  tests/test_portable_collaboration_e2e.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the end-to-end and documentation slice**

```bash
git add tests/test_portable_collaboration_e2e.py docs/architecture.md
git commit -m "test: cover nested frontmatter across clones"
```

### Task 6: Final security and regression verification

**Files:**
- Verify only; no planned file changes.

- [ ] **Step 1: Prove the new grammar remains narrow**

Run:

```bash
uv run pytest tests/test_portable_check.py -k 'parse or nested or flow or yaml' -q
```

Expected: all selected parser/security tests pass.

- [ ] **Step 2: Run the full suite**

Run:

```bash
uv run pytest -q
```

Expected: the complete suite passes, including all subprocess subtests.

- [ ] **Step 3: Run repository hygiene checks**

Run:

```bash
git diff --check
python tools/check_readme_sync.py
git status --short
```

Expected: `git diff --check` is silent; the README parity checker reports no new
English/Simplified-Chinese drift introduced by this branch slice; status shows
no uncommitted implementation changes.

- [ ] **Step 4: Inspect the exact implementation delta**

Run:

```bash
git log --oneline 800f80e..HEAD
git diff --stat 800f80e..HEAD
git diff 800f80e..HEAD -- \
  obsidian_wiki/frontmatter.py \
  obsidian_wiki/transaction.py \
  obsidian_wiki/portable_check.py \
  obsidian_wiki/operations.py \
  obsidian_wiki/lint.py \
  tests/test_portable_check.py \
  tests/test_transaction.py \
  tests/test_operations.py \
  tests/test_portable_migration.py \
  tests/test_lint.py \
  tests/test_trust.py \
  tests/test_portable_collaboration_e2e.py \
  docs/architecture.md
```

Expected: only the approved two nested shapes are newly accepted; source
ownership, operation-page exactness, and every unrelated nested structure
remain fail-closed.

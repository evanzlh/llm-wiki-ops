# Restricted Nested Frontmatter Design

## Status

Approved in conversation on 2026-08-10. This document records the agreed
frontmatter compatibility and security boundary before implementation.

## Problem

The framework page template defines two optional nested frontmatter structures:
a `provenance` mapping and a `relationships` list of mappings. The shared safe
frontmatter parser currently accepts only scalars and lists of scalars. As a
result, a page generated from the canonical `llm-wiki` and `wiki-ingest` skill
instructions is rejected by portable transaction validation before it can be
committed.

`wiki-lint` separately implements a partial parser for `relationships`, so the
same page can be interpreted differently by lint and by the portable write
path. Adding a general YAML dependency would remove the immediate syntax gap,
but would also expose tags, anchors, aliases, arbitrary nesting, and other YAML
semantics that the deliberately small parser currently excludes.

## Goals

- Accept the exact nested structures defined by the framework page schema.
- Keep transaction validation, portable check, migration, and lint on one safe
  frontmatter representation.
- Preserve existing scalar and scalar-list behavior.
- Continue to reject arbitrary nested YAML, flow mappings, tags, anchors,
  aliases, duplicate fields, and ambiguous indentation.
- Keep structural parsing separate from schema semantics such as confidence
  ranges, relationship type policy, and relationship target resolution.
- Avoid a new YAML dependency.

## Non-goals

- Implementing YAML generally or promising compatibility with arbitrary
  Obsidian frontmatter.
- Supporting nested mappings under keys other than `provenance` and
  `relationships`.
- Supporting inline/flow mappings or lists of inline mappings.
- Changing the six required portable page fields: `title`, `category`, `tags`,
  `sources`, `created`, and `updated`.
- Making `provenance` or `relationships` mandatory on every page.
- Moving lint policy, graph resolution, or trust calculations into the parser.

## Design

### 1. Extend the parsed representation with framework types

The shared `Frontmatter` value gains two explicit optional fields rather than
generic nested dictionaries:

```python
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
```

The leaf values remain strings, matching the existing parser. Numeric
interpretation and domain validation stay in consumers that own those rules.
Explicit types make the supported surface visible and prevent callers from
treating the parser as a generic YAML loader.

The representation also exposes one authoritative way to obtain all parsed
top-level field names. Consumers must use it instead of repeatedly combining
only `scalars` and `lists`; therefore present `provenance` and `relationships`
blocks participate in duplicate detection and field-presence checks without
becoming portable required fields.

### 2. Recognize only the `provenance` mapping

The parser accepts this block form:

```yaml
provenance:
  extracted: 0.72
  inferred: 0.25
  ambiguous: 0.03
```

It requires exactly the three framework keys, each once, with a scalar leaf
value at exactly two-space indentation. Ordering is not significant. Blank
lines, full-line comments, quoted scalars, and trailing comments retain the
same handling as existing frontmatter values.

Missing, unknown, or duplicate child keys are structural errors. Empty values,
additional nesting, tabs, unexpected indentation, flow mappings, tags,
anchors, and aliases are rejected. The parser does not decide whether the
values are decimal numbers, fall in `[0, 1]`, or sum to approximately one;
those are lint/schema concerns.

### 3. Recognize only the `relationships` list of mappings

The parser accepts the canonical compact block-item form:

```yaml
relationships:
  - target: "[[concepts/attention]]"
    type: uses
  - target: "[[concepts/lstm]]"
    type: contradicts
```

It also accepts the equivalent expanded block-item form already recognized by
lint:

```yaml
relationships:
  -
    target: "[[concepts/attention]]"
    type: uses
```

Each item requires exactly one `target` and one `type`, in either order. The
item marker is fixed at two-space indentation and continuation fields at
four-space indentation. Unknown or duplicate item fields, missing fields,
empty scalar values, malformed item boundaries, additional nesting, tabs, and
flow mappings are rejected.

`relationships: []` is the one supported inline spelling and represents a
present but empty relationship list. No other inline relationship syntax is
accepted. The parser does not validate relationship type allowlists, wikilink
shape, target existence, ambiguity, or graph cycles; lint continues to own
those semantics.

### 4. Preserve the restricted YAML boundary

An empty top-level value continues to mean an ordinary scalar list unless the
key is exactly `provenance` or `relationships`. Consequently this remains an
error:

```yaml
metadata:
  source: sources/a.md
```

Nested flow collections remain errors everywhere, including under the two
recognized keys. YAML directives, block scalars, typed tags, anchors, aliases,
merge keys, multi-document input, and implicit object construction are not
added. The implementation extends the existing line-oriented parser and does
not import a general YAML loader.

### 5. Use one representation across consumers

Portable transaction validation and portable check accept pages containing
either supported block and use the authoritative parsed field-name set when
checking required fields. Source ownership continues to come only from the
top-level scalar list named `sources`; neither nested block can influence the
portable source boundary.

Migration and operation-page validation retain their current scalar/list
behavior. Operation pages require an exact field set and therefore continue to
reject knowledge-only nested fields.

`wiki-lint` removes its independent `relationships` text parser and consumes
the shared typed relationships. Its existing relationship type and target
checks remain unchanged. Lint may add or retain semantic checks for provenance,
but a semantic problem must be reported as a lint issue rather than reclassified
as unsupported YAML syntax.

### 6. Error and compatibility behavior

Malformed recognized blocks raise `FrontmatterError` with a message that names
the affected top-level field and structural problem. Transaction validation
continues to wrap that error as `invalid candidate frontmatter`; portable check
continues to report `frontmatter-invalid`.

Existing pages that contain only scalars and scalar lists parse identically.
Pages with arbitrary nested mappings remain invalid. The formerly explicit
test that rejects all nested `provenance` mappings is replaced by acceptance
tests for the exact framework block and rejection tests for an unsupported
`provenance` child shape.

## Testing

### Parser tests

Add focused tests for:

- canonical and reordered `provenance` fields;
- quoted values, comments, and accepted whitespace;
- compact, expanded, reordered, multiple, and empty `relationships` forms;
- duplicate top-level and child keys;
- missing, unknown, empty, over-indented, tab-indented, and nested child values;
- flow mappings/lists of mappings, YAML tags, anchors, and aliases;
- continued rejection of a nested mapping under any other key;
- unchanged scalar and scalar-list behavior.

### Consumer tests

Add portable transaction and check regressions using a complete page generated
from the framework template with both nested blocks. Prove that commit succeeds,
the page remains source-bound, and malformed variants still fail closed.

Update lint tests to prove the shared parser preserves the current typed
relationship diagnostics for invalid types, missing targets, ambiguous targets,
and expanded block-item syntax. Cover operation and migration behavior where an
exact field set or source-list rewrite could otherwise regress.

### End-to-end regression

In an isolated portable repository, ingest an authoritative source using the
canonical page frontmatter shape, commit it through the transaction protocol,
run `doctor` and `check`, clone the resulting Git repository, and run them again.
The test must not require removing `provenance` or `relationships` from the
generated candidate.

## Acceptance Criteria

- A canonical skill-generated page containing both framework nested structures
  commits through the portable transaction path without a diagnostic bypass.
- `doctor` and `check` accept the committed page before and after a clone.
- Lint reads relationships from the shared parser and preserves its semantic
  diagnostics.
- Only the two named shapes are newly accepted; arbitrary nested YAML and
  advanced YAML features remain rejected.
- Existing scalar/list pages, migrations, operation pages, and source-boundary
  checks behave unchanged.
- Focused and full test suites pass.

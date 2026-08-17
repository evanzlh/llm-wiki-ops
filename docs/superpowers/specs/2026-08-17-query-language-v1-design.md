# Query Language v1 Design

**Date:** 2026-08-17

## Summary

Replace the current best-effort natural-language classification in `llmwikiops
query` with a small, versioned, discoverable query language. The language uses
fixed English syntax and treats quoted operands as opaque Unicode strings. Agents
must discover and use the published grammar instead of guessing accepted natural
language. Unsupported structures fail closed and never fall back to a different
query mode.

This change preserves the existing architectural boundary: Python performs
deterministic parsing, bounded retrieval, graph traversal, validation, and
structured reporting; the invoking agent reads the selected evidence and writes
the answer.

## Goals

- Give humans and agents an exact contract for every accepted query structure.
- Support query operands in any language without maintaining language-specific
  sentence tokenizers.
- Make the canonical agent interface unambiguous and machine discoverable.
- Distinguish invalid syntax, valid queries with no matches, ambiguous operands,
  and valid endpoints with no graph path.
- Eliminate silent query-mode fallback and silent same-basename page replacement.
- Preserve bounded reads, trust metadata, and metadata-first public filtering.

## Non-goals

- Accept arbitrary natural-language questions.
- Detect the language of an operand, translate it, or expand synonyms.
- Add embeddings, semantic search, stemming, or language-specific tokenization.
- Add Boolean expressions, negation, nesting, or implicit multi-term semantics.
- Claim to perform knowledge-gap analysis without a separately specified,
  verifiable operation.
- Turn the deterministic CLI into an answer-generating model runtime.

## Query Language Contract

The initial grammar version is `query-language/v1`. It accepts exactly three
English natural templates:

```text
find "<term>"
list pages about "<term>"
find path from "<source>" to "<target>"
```

The equivalent canonical CLI forms are:

```bash
llmwikiops query --mode find --term "注意力机制"
llmwikiops query --mode list --term "深度学习"
llmwikiops query --mode path --from "注意力机制" --to "词嵌入"
```

The English structural tokens are ASCII case-insensitive but have no aliases or
synonyms. Natural-template operands must be double quoted. Within an operand,
`\"` represents a literal double quote and `\\` represents a literal backslash;
all other escape sequences are invalid. Extracted operands are normalized with
Unicode NFKC followed by trimming surrounding whitespace. Their language and
remaining content are otherwise opaque to the parser.

The natural and explicit forms produce the same internal `QuerySpec`. The two
forms cannot be mixed. Missing operands, extra text, malformed quoting, unsupported
escapes, unknown modes, and invalid parameter combinations are errors. There is no
fallback from an unrecognized structure to `find` or another mode.

`query-language/v1` deliberately omits the current `gap` label. The existing
implementation only ranks ordinary keyword candidates after recognizing a gap
phrase; it does not compute knowledge gaps. A future grammar version may add a gap
operation only after its observable semantics and evidence requirements are
specified.

## Capability Discovery

`llmwikiops query --describe --json` is the machine-readable authority for the
installed query language. It runs without resolving repository configuration or
reading a vault. Its response includes:

- `grammar_version`;
- every accepted natural template and its operand names;
- each canonical explicit CLI form;
- representative multilingual operand examples;
- normalization and matching rules;
- supported modes and stable error codes.

The human `--help` output summarizes the same contract. Parser definitions and
description output must derive from one grammar definition so that documentation
cannot silently diverge from executable behavior.

An illustrative description fragment is:

```json
{
  "grammar_version": "query-language/v1",
  "natural_templates": [
    {
      "mode": "find",
      "template": "find \"<term>\"",
      "example": "find \"注意力机制\""
    },
    {
      "mode": "list",
      "template": "list pages about \"<term>\"",
      "example": "list pages about \"深度学习\""
    },
    {
      "mode": "path",
      "template": "find path from \"<source>\" to \"<target>\"",
      "example": "find path from \"注意力机制\" to \"词嵌入\""
    }
  ],
  "canonical_cli": {
    "find": "--mode find --term <term>",
    "list": "--mode list --term <term>",
    "path": "--mode path --from <source> --to <target>"
  }
}
```

## Agent Contract

The `wiki-query` skill reads `llmwikiops query --describe --json` once before a
query workflow and treats the installed grammar as authoritative. It prefers the
explicit `--mode` forms, while the natural templates remain available for humans.
The skill must not invent aliases, paraphrases, or parameter combinations absent
from the capability response.

On `unsupported_query_structure`, the agent may rewrite the request once using a
template returned by the CLI. It must not improvise another structure. On
`ambiguous_operand`, the agent reports the candidates and asks for disambiguation
instead of selecting one. If it does not support the returned `grammar_version`, it
stops and reports the incompatibility.

## Retrieval Semantics

The retrieval layer receives a validated `QuerySpec`; it no longer classifies a raw
question. Query operands and indexed values use the same NFKC, case-folding, and
surrounding-whitespace normalization. An operand is matched as one complete phrase.
No word segmentation, synonym expansion, translation, or semantic inference occurs.

The v1 searchable fields remain slug, title, tags, and summary. Ranking order is:

1. exact slug or title match;
2. title substring match;
3. tag substring match;
4. summary substring match.

Existing tier and bounded degree weighting may break ties after a lexical match but
must never surface a page that has no lexical match.

`find` returns ranked candidates and a bounded `should_read` set. `list` returns the
bounded matching page list, the total match count, and a `truncated` flag; its wording
does not promise that the default response contains every match. `path` resolves both
operands first and then performs the existing bounded bidirectional-link BFS. If an
endpoint alias has multiple equally valid pages, resolution stops with
`ambiguous_operand` rather than choosing by rank.

## Page Identity and Link Resolution

The index uses the normalized repository-relative Markdown path without the `.md`
suffix as the internal page identity. A basename or title is an alias, not an
identity. This prevents pages such as `concepts/agent.md` and `projects/agent.md`
from replacing each other in the index.

Path-qualified links resolve directly. An unqualified basename link resolves only
when its alias is unique. Ambiguous aliases are retained as ambiguity information
for structured reporting and are never silently attached to one page. This identity
change is limited to the query index and does not redefine repository-wide page
identity in this workstream.

## Components and Data Flow

### Query language module

A focused `query_language.py` module owns:

- the grammar definition and `grammar_version`;
- the immutable `QuerySpec` representation;
- natural-template parsing;
- explicit-argument validation;
- capability-description generation;
- typed parse and validation errors.

### CLI

`cli.py` invokes capability description before runtime resolution. Otherwise it
parses either the natural positional form or the explicit mode form, rejects mixed
forms, resolves the configured vault, and sends the validated `QuerySpec` to the
retrieval layer.

JSON mode emits only structured output. Human mode sends concise diagnostics and
legal rewrite examples to stderr. Parse and validation failures exit with status 2.

### Retrieval layer

`graphrag.py` builds the safe metadata-first index, executes the requested operation,
and returns candidates, paths, read bounds, trust metadata, and operation status. It
does not parse natural language or generate an answer.

### Skill and documentation

`wiki-query/SKILL.md` documents capability discovery and the agent rules. CLI docs
and both README language surfaces use the accepted syntax and canonical explicit
forms consistently. Human-facing explanation belongs in `docs/`; README remains a
landing page.

## Result and Error Contract

Valid operations return a stable `status` distinct from their `mode`:

- `ok`: the operation produced a result;
- `no_matches`: syntax was valid but no page matched;
- `no_path`: both path endpoints resolved but no bounded graph path exists.

Stable validation errors include:

- `unsupported_query_structure`;
- `invalid_query_arguments`;
- `ambiguous_operand`;
- `unsupported_operation`.

Structured errors contain the error code, grammar version, a concise message, and
only the legal templates or disambiguation candidates relevant to recovery. They do
not include vault body content. Invalid syntax and argument combinations exit 2;
valid `no_matches` and `no_path` results remain successful executions.

## Migration

The change is a hard cutover. A bare legacy question is rejected:

```bash
llmwikiops query "transformer"
```

The diagnostic supplies legal rewrites:

```bash
llmwikiops query 'find "transformer"'
llmwikiops query --mode find --term "transformer"
```

No deprecated compatibility branch executes the legacy query because doing so
would preserve the guessing behavior this design removes. Installed skills, docs,
and tests change in the same release.

## Safety and Compatibility Properties

- Query remains strictly read-only.
- `--public-only` continues filtering restricted metadata before body or link
  extraction.
- Page contents remain untrusted evidence and never become instructions.
- Existing `--top` and `--max-read` bounds remain in force for applicable modes.
- Trust metadata remains attached to candidates and suggested reads.
- Invalid portable configuration continues to fail closed for real queries;
  only context-free `--describe` bypasses repository resolution.

## Verification

Tests must cover:

- equivalence of all three natural templates and their explicit forms;
- Chinese, English, Japanese, Arabic, emoji, and code symbols as operands;
- NFKC, case-folding, whitespace, quote, and backslash behavior;
- rejection of Chinese structural shells, English paraphrases, trailing text,
  malformed quotes, illegal escapes, mixed forms, and invalid mode arguments;
- rejection of the legacy bare-question form without executing retrieval;
- context-free, parser-derived `--describe --json` output;
- distinct `ok`, `no_matches`, `no_path`, syntax-error, and ambiguity responses;
- same-basename pages and ambiguous title/basename resolution;
- path queries with mixed-language endpoints;
- preservation of metadata-first `--public-only` filtering;
- synchronization of `README.md`, `README_ZH.md`, CLI docs, and the installed
  `wiki-query` skill.

Verification commands include focused query tests, the README synchronization check,
and the full suite required by `AGENTS.md`:

```bash
uv run --with pytest python -m pytest tests/test_graphrag.py tests/test_query_cli.py -q -p no:cacheprovider
uv run python tools/check_readme_sync.py
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
```

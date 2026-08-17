"""GraphRAG retrieval for validated query-language/v1 operations.

The strict natural form is discovered from the query language and can be called as::

    llmwikiops query 'find "注意力机制"'

The equivalent explicit form is::

    llmwikiops query --mode find --term "注意力机制"

Validated ``QuerySpec`` values select ``find``, ``list``, or ``path`` retrieval.
Each operand is one opaque normalized phrase: retrieval never tokenizes, translates,
or expands it. Results report ``grammar_version``, ``mode``, ``status``, ranked
``candidates``, a repository-relative ``path``, bounded ``should_read`` suggestions
with trust metadata, ``index_only``, and query statistics.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath
from typing import Any, Optional
from urllib.parse import urlsplit

from .frontmatter import FrontmatterError, frontmatter_values, split_frontmatter
from .query_language import (
    DEGREE_BONUS_CAP,
    DEGREE_BONUS_INCREMENT,
    DEFAULT_TIER_BONUS,
    GRAMMAR_VERSION,
    MATCH_BASE_SCORES,
    MATCH_KIND_PRIORITY,
    QuerySpec,
    TIER_BONUSES,
    candidate_sort_key,
    normalize_match,
)
from .safe_files import read_markdown_snapshot, scan_markdown_headers


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
_MD_LINK_RE = re.compile(r"\[.*?\]\(([^)]+)\)")

SKIP_DIRS = frozenset(
    "_archived .obsidian".split()
)
BLOCKED_PUBLIC_TAGS = frozenset({"visibility/internal", "visibility/pii"})
ROOT_VIEW_FILES = frozenset({"index.md", "log.md", "hot.md"})


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


def _wikilink_candidates(
    raw_target: str,
    pages: dict[str, dict],
    aliases: dict[str, list[str]],
) -> list[str]:
    target = normalize_match(raw_target.removesuffix(".md"))
    if "/" in target and target in pages:
        return [target]
    return aliases.get(target, [])


def _record_edge(pages: dict[str, dict], page_id: str, target: str) -> None:
    if target != page_id:
        pages[page_id]["out_links"].append(target)
        pages[target]["in_links"].append(page_id)


def _record_wikilink(
    pages: dict[str, dict],
    aliases: dict[str, list[str]],
    page_id: str,
    raw_target: str,
) -> None:
    candidates = _wikilink_candidates(raw_target, pages, aliases)
    if len(candidates) == 1:
        _record_edge(pages, page_id, candidates[0])
    elif len(candidates) > 1:
        pages[page_id]["ambiguous_links"].append(
            {"target": raw_target, "candidates": candidates}
        )


def _record_resolved_markdown_link(
    pages: dict[str, dict], page_id: str, target: str
) -> None:
    if target in pages:
        _record_edge(pages, page_id, target)


def _markdown_link_target(href: str, source_relative: str) -> str | None:
    destination = href.strip()
    if destination.startswith("<"):
        closing = destination.find(">", 1)
        if closing < 0:
            return None
        destination = destination[1:closing].strip()
    else:
        destination = destination.split(None, 1)[0] if destination else ""
    if not destination or destination.startswith("//"):
        return None
    try:
        parsed = urlsplit(destination)
    except ValueError:
        return None
    if parsed.scheme or not parsed.path.endswith(".md"):
        return None
    if parsed.path.startswith("/"):
        resolved = posixpath.normpath(parsed.path.lstrip("/"))
    else:
        parent = PurePosixPath(source_relative).parent.as_posix()
        resolved = posixpath.normpath(posixpath.join(parent, parsed.path))
    if resolved == ".." or resolved.startswith("../"):
        return None
    return _page_id(resolved)


def build_index(vault: Path, *, public_only: bool = False) -> dict[str, dict]:
    """Build a lightweight index dict from vault frontmatter and wikilinks.

    Returns:
        {slug: {title, tags, summary, category, tier, out_links, in_links, path}}
    """
    pages: dict[str, dict] = {}

    headers = scan_markdown_headers(
        vault,
        skip_dirs=SKIP_DIRS,
        skip_relative_files=ROOT_VIEW_FILES,
    )
    eligible = []

    # First pass: collect all slugs and frontmatter
    for header in headers:
        page = header
        page_id = _page_id(page.relative)
        try:
            text = page.text()
        except ValueError:
            if public_only:
                continue
            raise

        try:
            parsed, _body = split_frontmatter(text)
        except FrontmatterError:
            if public_only:
                continue
            raise
        values = frontmatter_values(parsed)
        title = str(values.get("title", "")).strip()
        raw_tags = values.get("tags", ())
        tags = (
            [str(tag) for tag in raw_tags]
            if isinstance(raw_tags, tuple)
            else ([str(raw_tags)] if raw_tags else [])
        )

        if public_only and BLOCKED_PUBLIC_TAGS.intersection(tags):
            continue

        summary = str(values.get("summary", "")).strip()

        category = str(values.get("category", "")).strip() or str(
            Path(page.relative).parent
        )

        tier = str(values.get("tier", "supporting")).strip()
        lifecycle = str(values.get("lifecycle", "")).strip()
        updated = str(values.get("updated", "")).strip()

        if page_id in pages:
            raise RuntimeError("duplicate normalized query page identity")

        pages[page_id] = {
            "title": title or page.path.stem,
            "tags": tags,
            "summary": summary,
            "category": category,
            "tier": tier,
            "visibility": [tag for tag in tags if tag.startswith("visibility/")],
            "lifecycle": lifecycle,
            "updated": updated,
            "path": page.relative,
            "out_links": [],
            "in_links": [],
            "ambiguous_links": [],
        }
        eligible.append(header)

    # Second pass: extract wikilinks
    aliases = _alias_map(pages)
    for header in eligible:
        page = read_markdown_snapshot(header)
        page_id = _page_id(page.relative)
        if page_id not in pages:
            continue
        text = page.text(errors="replace")

        for link in _WIKILINK_RE.findall(text):
            _record_wikilink(pages, aliases, page_id, link)

        for href in _MD_LINK_RE.findall(text):
            target = _markdown_link_target(href, page.relative)
            if target is not None:
                _record_resolved_markdown_link(pages, page_id, target)

    return pages


# ---------------------------------------------------------------------------
# Scoring / ranking
# ---------------------------------------------------------------------------

class QueryExecutionError(RuntimeError):
    """A stable retrieval error with a machine-readable code and details."""

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


def _invalid_query_arguments(
    message: str,
    *,
    details: Optional[dict] = None,
) -> QueryExecutionError:
    return QueryExecutionError(
        "invalid_query_arguments",
        message,
        details=details,
    )


def _validate_integer_bound(name: str, value: Any, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid_query_arguments(
            "{} must be an integer greater than or equal to {}".format(
                name, minimum
            ),
            details={"argument": name},
        )


def _score(
    page_id: str,
    entry: dict,
    operand: str,
) -> tuple[float, Optional[str]]:
    term = normalize_match(operand)
    title = normalize_match(entry["title"])
    tags = [normalize_match(tag) for tag in entry["tags"]]
    summary = normalize_match(entry["summary"])
    basename = normalize_match(PurePosixPath(page_id).name)

    if term in {page_id, basename, title}:
        score, match_kind = MATCH_BASE_SCORES["exact"], "exact"
    elif term and term in title:
        score, match_kind = MATCH_BASE_SCORES["title"], "title"
    elif term and any(term in tag for tag in tags):
        score, match_kind = MATCH_BASE_SCORES["tag"], "tag"
    elif term and term in summary:
        score, match_kind = MATCH_BASE_SCORES["summary"], "summary"
    else:
        return 0.0, None

    degree = len(entry["in_links"]) + len(entry["out_links"])
    tier_bonus = TIER_BONUSES.get(entry.get("tier", "supporting"), DEFAULT_TIER_BONUS)
    return (
        score + min(degree * DEGREE_BONUS_INCREMENT, DEGREE_BONUS_CAP) + tier_bonus,
        match_kind,
    )


def rank_candidates(
    index: dict[str, dict],
    operand: str,
    top_n: Optional[int] = None,
) -> list[dict]:
    if top_n is not None:
        _validate_integer_bound("top_n", top_n, minimum=1)

    scored = []
    for page_id, entry in index.items():
        score, match_kind = _score(page_id, entry, operand)
        if match_kind is None:
            continue
        scored.append(
            {
                "slug": page_id,
                "page": entry["path"],
                "title": entry["title"],
                "score": score,
                "match_kind": match_kind,
                "summary": entry["summary"],
                "tier": entry["tier"],
                "visibility": entry["visibility"],
                "lifecycle": entry["lifecycle"],
                "updated": entry["updated"],
                "in_degree": len(entry["in_links"]),
            }
        )
    scored.sort(key=candidate_sort_key)
    return scored if top_n is None else scored[:top_n]


# ---------------------------------------------------------------------------
# Multi-hop path finding (BFS)
# ---------------------------------------------------------------------------

def find_path(
    index: dict[str, dict],
    source_slug: str,
    target_slug: str,
    max_depth: int = 4,
) -> list[str] | None:
    """BFS shortest path from source to target through wikilinks."""
    if source_slug not in index or target_slug not in index:
        return None
    if source_slug == target_slug:
        return [source_slug]

    queue: deque[tuple[str, list[str]]] = deque([(source_slug, [source_slug])])
    visited = {source_slug}

    while queue:
        node, path = queue.popleft()
        if len(path) > max_depth:
            continue
        for neighbour in index[node]["out_links"] + index[node]["in_links"]:
            if neighbour in visited:
                continue
            visited.add(neighbour)
            new_path = path + [neighbour]
            if neighbour == target_slug:
                return new_path
            queue.append((neighbour, new_path))
    return None


# ---------------------------------------------------------------------------
# Validated operand resolution and result construction
# ---------------------------------------------------------------------------

def _ambiguous_operand(
    operand_name: str,
    page_ids: list[str],
    index: dict[str, dict],
) -> QueryExecutionError:
    candidates = sorted(index[page_id]["path"] for page_id in page_ids)
    return QueryExecutionError(
        "ambiguous_operand",
        "query operand matches more than one page",
        details={"operand": operand_name, "candidates": candidates},
    )


def resolve_operand(
    index: dict[str, dict],
    operand: str,
    *,
    operand_name: str,
) -> Optional[str]:
    """Resolve one opaque operand to a page identity, or report ambiguity."""
    term = normalize_match(operand)
    exact_ids = {
        page_id
        for page_id, entry in index.items()
        if term in _aliases(page_id, entry)
    }
    if term in index:
        exact_ids.add(term)
    if term.endswith(".md"):
        page_id = term.removesuffix(".md")
        if page_id in index:
            exact_ids.add(page_id)

    exact = sorted(exact_ids)
    if len(exact) > 1:
        raise _ambiguous_operand(operand_name, exact, index)
    if exact:
        return exact[0]

    matches = rank_candidates(index, operand)
    if not matches:
        return None
    best_priority = min(MATCH_KIND_PRIORITY[item["match_kind"]] for item in matches)
    best = [
        item["slug"]
        for item in matches
        if MATCH_KIND_PRIORITY[item["match_kind"]] == best_priority
    ]
    if len(best) > 1:
        raise _ambiguous_operand(operand_name, best, index)
    return best[0]


def _query_operands(spec: QuerySpec) -> dict[str, str]:
    if spec.mode in {"find", "list"}:
        return {"term": spec.term or ""}
    return {"source": spec.source or "", "target": spec.target or ""}


def _is_nonempty_operand(value: Any) -> bool:
    return isinstance(value, str) and bool(normalize_match(value))


def _validate_query_spec(spec: Any) -> QuerySpec:
    if not isinstance(spec, QuerySpec):
        raise _invalid_query_arguments("query requires a QuerySpec")
    if not isinstance(spec.mode, str) or spec.mode not in (
        "find",
        "list",
        "path",
    ):
        raise _invalid_query_arguments("query mode must be find, list, or path")

    if spec.mode in ("find", "list"):
        valid = (
            _is_nonempty_operand(spec.term)
            and spec.source is None
            and spec.target is None
        )
    else:
        valid = (
            spec.term is None
            and _is_nonempty_operand(spec.source)
            and _is_nonempty_operand(spec.target)
        )
    if not valid:
        raise _invalid_query_arguments(
            "query operands do not match mode {}".format(spec.mode)
        )
    return spec


def _trust_by_path(index: dict[str, dict]) -> dict[str, dict]:
    return {
        entry["path"]: {
            "page": entry["path"],
            "visibility": entry["visibility"],
            "lifecycle": entry["lifecycle"],
            "updated": entry["updated"],
        }
        for entry in index.values()
    }


def _god_nodes_relevant(index: dict[str, dict], operands: list[str]) -> list[str]:
    degree = {
        page_id: len(entry["in_links"]) + len(entry["out_links"])
        for page_id, entry in index.items()
    }
    god_pages = sorted(
        index,
        key=lambda page_id: (-degree[page_id], index[page_id]["path"]),
    )[:10]
    normalized = [normalize_match(operand) for operand in operands if operand]
    return [
        index[page_id]["path"]
        for page_id in god_pages
        if any(
            term in normalize_match(index[page_id]["title"])
            or any(term in normalize_match(tag) for tag in index[page_id]["tags"])
            for term in normalized
        )
    ][:5]


def _public_candidate(candidate: dict) -> dict:
    return {
        "page": candidate["page"],
        "title": candidate["title"],
        "score": round(candidate["score"], 2),
        "match_kind": candidate["match_kind"],
        "summary": candidate["summary"],
        "tier": candidate["tier"],
        "visibility": candidate["visibility"],
        "lifecycle": candidate["lifecycle"],
        "updated": candidate["updated"],
    }


def _base_result(
    spec: QuerySpec,
    index: dict[str, dict],
    *,
    status: str,
    candidates: list[dict],
    path: list[str],
    should_read: list[str],
    index_only: bool,
) -> dict[str, Any]:
    operands = _query_operands(spec)
    trust = _trust_by_path(index)
    return {
        "grammar_version": GRAMMAR_VERSION,
        "mode": spec.mode,
        "status": status,
        "candidates": [_public_candidate(candidate) for candidate in candidates],
        "path": path,
        "god_nodes_relevant": _god_nodes_relevant(index, list(operands.values())),
        "should_read": should_read,
        "should_read_metadata": [trust[page] for page in should_read],
        "index_only": index_only,
        "stats": {
            "indexed_pages": len(index),
            "query_operands": operands,
        },
    }


def _candidate_result(
    spec: QuerySpec,
    index: dict[str, dict],
    candidates: list[dict],
    *,
    exact_match_count: int,
    max_should_read: int,
    status: str,
) -> dict[str, Any]:
    top = candidates[0] if candidates else None
    index_only = bool(
        spec.mode == "find"
        and top
        and exact_match_count == 1
        and top["match_kind"] == "exact"
        and top["summary"]
    )
    should_read = [] if index_only else [
        item["page"] for item in candidates[:max_should_read]
    ]
    return _base_result(
        spec,
        index,
        status=status,
        candidates=candidates,
        path=[],
        should_read=should_read,
        index_only=index_only,
    )


def _path_result(
    spec: QuerySpec,
    index: dict[str, dict],
    *,
    status: str,
    raw_path: Optional[list[str]] = None,
    unresolved: Optional[list[str]] = None,
    max_should_read: int,
) -> dict[str, Any]:
    path = [index[page_id]["path"] for page_id in (raw_path or [])]
    result = _base_result(
        spec,
        index,
        status=status,
        candidates=[],
        path=path,
        should_read=path[:max_should_read],
        index_only=False,
    )
    if unresolved is not None:
        result["unresolved_operands"] = unresolved
    return result


# ---------------------------------------------------------------------------
# Main query entry point
# ---------------------------------------------------------------------------

def query(
    vault: Path,
    spec: QuerySpec,
    *,
    top_n: int = 8,
    max_should_read: int = 3,
    public_only: bool = False,
) -> dict[str, Any]:
    spec = _validate_query_spec(spec)
    _validate_integer_bound("top_n", top_n, minimum=1)
    _validate_integer_bound("max_should_read", max_should_read, minimum=0)

    index = build_index(vault, public_only=public_only)
    if spec.mode in {"find", "list"}:
        matches = rank_candidates(index, spec.term or "")
        selected = matches[:top_n]
        result = _candidate_result(
            spec,
            index,
            selected,
            exact_match_count=sum(
                candidate["match_kind"] == "exact" for candidate in matches
            ),
            max_should_read=max_should_read,
            status="ok" if selected else "no_matches",
        )
        if spec.mode == "list":
            result["total_matches"] = len(matches)
            result["truncated"] = len(matches) > len(selected)
        return result

    source = resolve_operand(index, spec.source or "", operand_name="source")
    target = resolve_operand(index, spec.target or "", operand_name="target")
    unresolved = [
        name
        for name, value in (("source", source), ("target", target))
        if value is None
    ]
    if unresolved:
        return _path_result(
            spec,
            index,
            status="no_matches",
            unresolved=unresolved,
            max_should_read=max_should_read,
        )

    raw_path = find_path(index, source, target)
    return _path_result(
        spec,
        index,
        status="ok" if raw_path else "no_path",
        raw_path=raw_path or [],
        max_should_read=max_should_read,
    )

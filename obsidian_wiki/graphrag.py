"""GraphRAG query index for wiki-query.

Builds a compact in-memory index from bounded vault frontmatter and eligible-page
wikilinks, then answers structural and factual queries against it without requiring
an agent to open page bodies. Equivalent to graphify's "query the compiled graph instead of
raw files" — saves reading 10–50 pages for questions answerable from the
graph structure.

The agent calls:
  llmwikiops query "<question>" [options]

And gets back a JSON response:
{
  "answer_type": "direct" | "path" | "list" | "gap",
  "candidates": [{"page": "...", "score": 0.N, "summary": "...",
                  "visibility": [], "lifecycle": "...", "updated": "..."}, ...],
  "path": ["page-a", "page-b", "page-c"],   # multi-hop, if applicable
  "god_nodes_relevant": ["page", ...],        # hub pages related to query terms
  "should_read": ["page-a.md", "page-b.md"], # pages worth opening for full detail
  "should_read_metadata": [{"page": "page-a.md", "visibility": [],
                            "lifecycle": "...", "updated": "..."}],
  "index_only": true/false                    # true = answer is complete without page reads
}

The `should_read` list is the key output: it tells the agent exactly which pages
to open, replacing the current approach of opening 10+ pages speculatively.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath
from typing import Any

from .frontmatter import FrontmatterError, frontmatter_values, split_frontmatter
from .query_language import normalize_match
from .safe_files import read_markdown_snapshot, scan_markdown_headers


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
_MD_LINK_RE = re.compile(r"\[.*?\]\(([^)]+)\)")
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

SKIP_DIRS = frozenset(
    "_archived .obsidian".split()
)
BLOCKED_PUBLIC_TAGS = frozenset({"visibility/internal", "visibility/pii"})
ROOT_VIEW_FILES = frozenset({"index.md", "log.md", "hot.md"})


def _slug(s: str) -> str:
    return s.strip().lower().replace(" ", "-")


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


def _markdown_link_target(href: str) -> str | None:
    destination = href.strip()
    path = re.split(r"[?#]", destination, maxsplit=1)[0]
    if (
        not path.endswith(".md")
        or path.startswith("//")
        or _URI_SCHEME_RE.match(path)
    ):
        return None
    return path.removesuffix(".md")


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
            _record_link(pages, aliases, page_id, link)

        for href in _MD_LINK_RE.findall(text):
            target = _markdown_link_target(href)
            if target is not None:
                _record_link(pages, aliases, page_id, target)

    return pages


# ---------------------------------------------------------------------------
# Scoring / ranking
# ---------------------------------------------------------------------------

_TIER_WEIGHT = {"core": 1.3, "supporting": 1.0, "peripheral": 0.7}


def _score(slug: str, entry: dict, terms: list[str]) -> float:
    score = 0.0
    title_lower = entry["title"].lower()
    summary_lower = entry["summary"].lower()
    tags_lower = [t.lower() for t in entry["tags"]]
    for term in terms:
        t = term.lower()
        if t == slug or t == title_lower:
            score += 10.0
        elif t in title_lower:
            score += 6.0
        elif any(t in tag for tag in tags_lower):
            score += 4.0
        elif t in summary_lower:
            score += 2.0

    if score > 0:
        # Degree bonus only when at least one term matched — prevents degree
        # noise from surfacing irrelevant pages
        degree = len(entry["in_links"]) + len(entry["out_links"])
        score += min(degree * 0.1, 2.0)
        score *= _TIER_WEIGHT.get(entry.get("tier", "supporting"), 1.0)
    return score


def rank_candidates(
    index: dict[str, dict],
    terms: list[str],
    top_n: int = 8,
) -> list[dict]:
    scored = [
        {
            "slug": slug,
            "page": entry["path"],
            "title": entry["title"],
            "score": _score(slug, entry, terms),
            "summary": entry["summary"],
            "tier": entry["tier"],
            "visibility": entry["visibility"],
            "lifecycle": entry["lifecycle"],
            "updated": entry["updated"],
            "in_degree": len(entry["in_links"]),
        }
        for slug, entry in index.items()
    ]
    scored.sort(key=lambda x: (-x["score"], -x["in_degree"]))
    return [c for c in scored[:top_n] if c["score"] > 0]


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
# Query classification
# ---------------------------------------------------------------------------

_PATH_PATTERNS = re.compile(
    r"how (?:is|are|does) (.+?) (?:connected|related|linked) to (.+?)[\?]?$"
    r"|trace (?:the )?(?:chain|path) from (.+?) to (.+?)[\?]?$"
    r"|what connects (.+?) (?:to|and) (.+?)[\?]?$",
    re.IGNORECASE,
)

_GAP_PATTERNS = re.compile(
    r"what (?:do|don'?t) I (?:not )?know about|what.?s missing|what gaps|open questions",
    re.IGNORECASE,
)

_LIST_PATTERNS = re.compile(
    r"(?:list|show|find|give me) (?:all|every|pages about)",
    re.IGNORECASE,
)


def classify_query(question: str) -> tuple[str, list[str]]:
    """Return (answer_type, extracted_terms).

    answer_type: "path" | "gap" | "list" | "direct"
    """
    m = _PATH_PATTERNS.search(question)
    if m:
        groups = [g for g in m.groups() if g]
        terms = groups[:2] if len(groups) >= 2 else [question]
        return "path", terms

    if _GAP_PATTERNS.search(question):
        # Extract what the gap is about
        terms = re.sub(r"what (?:do|don't) I (?:not )?know about|what.?s missing", "", question, flags=re.IGNORECASE).strip().split()
        return "gap", terms

    if _LIST_PATTERNS.search(question):
        terms = re.sub(r"(?:list|show|find|give me) (?:all|every|pages about)", "", question, flags=re.IGNORECASE).strip().split()
        return "list", terms

    # Default: extract meaningful terms (drop stop words)
    stop = {"what", "the", "a", "an", "is", "are", "how", "does", "do", "in", "of", "to", "for", "and", "or"}
    terms = [w.strip("?,.'\"") for w in question.split() if w.lower().strip("?,.'\"") not in stop and len(w) > 2]
    return "direct", terms


# ---------------------------------------------------------------------------
# Main query entry point
# ---------------------------------------------------------------------------

def query(
    vault: Path,
    question: str,
    *,
    top_n: int = 8,
    max_should_read: int = 3,
    public_only: bool = False,
) -> dict[str, Any]:
    index = build_index(vault, public_only=public_only)
    if not index:
        return {
            "answer_type": "direct",
            "candidates": [],
            "path": [],
            "god_nodes_relevant": [],
            "should_read": [],
            "should_read_metadata": [],
            "index_only": True,
            "note": "Vault appears empty.",
        }

    answer_type, terms = classify_query(question)

    # God nodes relevant to the query
    degree = {s: len(e["in_links"]) + len(e["out_links"]) for s, e in index.items()}
    god_slugs = sorted(degree, key=lambda s: -degree[s])[:10]
    term_set = {t.lower() for t in terms}
    god_relevant = [
        index[s]["path"] for s in god_slugs
        if any(t in index[s]["title"].lower() or t in " ".join(index[s]["tags"]).lower() for t in term_set)
    ][:5]

    path_result: list[str] = []
    if answer_type == "path" and len(terms) >= 2:
        src_slug = _slug(terms[0])
        tgt_slug = _slug(terms[1])
        # Try to find slugs by scoring if exact match fails
        if src_slug not in index:
            cands = rank_candidates(index, [terms[0]], top_n=1)
            src_slug = cands[0]["slug"] if cands else src_slug
        if tgt_slug not in index:
            cands = rank_candidates(index, [terms[1]], top_n=1)
            tgt_slug = cands[0]["slug"] if cands else tgt_slug
        raw_path = find_path(index, src_slug, tgt_slug)
        if raw_path:
            path_result = [index[s]["path"] for s in raw_path if s in index]

    candidates = rank_candidates(index, terms, top_n=top_n)

    # Decide whether page reads are needed
    top_candidate = candidates[0] if candidates else None
    index_only = False
    if top_candidate and top_candidate["score"] >= 10.0 and top_candidate["summary"]:
        index_only = True  # Exact title match with a summary — likely answerable from index

    should_read = [c["page"] for c in candidates[:max_should_read] if not index_only]
    if path_result and not index_only:
        # Add path pages to should_read, deduplicated
        for p in path_result:
            if p not in should_read:
                should_read.append(p)
        should_read = should_read[:max_should_read + 2]
    trust_by_path = {
        entry["path"]: {
            "page": entry["path"],
            "visibility": entry["visibility"],
            "lifecycle": entry["lifecycle"],
            "updated": entry["updated"],
        }
        for entry in index.values()
    }

    return {
        "answer_type": answer_type,
        "candidates": [
            {
                "page": c["page"],
                "title": c["title"],
                "score": round(c["score"], 2),
                "summary": c["summary"],
                "tier": c["tier"],
                "visibility": c["visibility"],
                "lifecycle": c["lifecycle"],
                "updated": c["updated"],
            }
            for c in candidates
        ],
        "path": path_result,
        "god_nodes_relevant": god_relevant,
        "should_read": should_read,
        "should_read_metadata": [trust_by_path[path] for path in should_read],
        "index_only": index_only,
        "stats": {
            "indexed_pages": len(index),
            "query_terms": terms,
        },
    }

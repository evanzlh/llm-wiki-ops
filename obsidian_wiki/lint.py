"""Vault lint checks for wiki structure and metadata hygiene."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from pathlib import Path
from typing import Any

from obsidian_wiki.frontmatter import FrontmatterError, parse_relationships
from obsidian_wiki.page_graph import normalise_node_id, parse_page_text
from obsidian_wiki.safe_files import MarkdownFile, scan_markdown_files
from obsidian_wiki.trust import (
    ALLOWED_LIFECYCLES,
    TRUST_LEDGER_RELATIVE_PATH,
    check_trust_ledger,
    validate_trust_metadata,
)

SKIP_DIRS = frozenset("_archived _bootstrap .obsidian .git".split())
REQUIRED_FRONTMATTER = (
    "title",
    "category",
    "tags",
    "sources",
    "created",
    "updated",
)
# Introduced by the trust-ledger rollout (#28, #132). Legacy pages that predate
# the schema are missing these by construction; enforcement is staged behind
# lint_vault's strict_trust switch so upgrading obsidian-wiki doesn't fail-close
# every pre-existing page until a vault owner explicitly opts into strict mode
# after a backfill/review pass.
TRUST_REQUIRED_FRONTMATTER = (
    "base_confidence",
    "lifecycle",
)
ROOT_VIEW_FILES = frozenset({"index.md", "log.md", "hot.md"})
RESERVED_PAGE_STEMS = frozenset({"_insights"})
ALLOWED_RELATIONSHIP_TYPES = frozenset(
    {"extends", "implements", "contradicts", "derived_from", "uses", "replaces", "related_to"}
)

def _relative_subtree_strings(
    skip_relative_subtrees: Collection[tuple[str, ...]],
) -> set[str]:
    try:
        return {"/".join(parts) for parts in skip_relative_subtrees}
    except TypeError as exc:
        raise ValueError("lint skip subtree must be a relative path tuple") from exc


def _iter_pages(
    vault: Path,
    *,
    skip_relative_subtrees: Collection[str] = (),
) -> tuple[MarkdownFile, ...]:
    return scan_markdown_files(
        vault,
        skip_dirs=SKIP_DIRS,
        skip_relative_files=ROOT_VIEW_FILES,
        skip_relative_subtrees=skip_relative_subtrees,
    )


def _typed_relationships(text: str) -> list[dict[str, str]]:
    try:
        relationships = parse_relationships(text)
    except FrontmatterError:
        return [{"parse_error": "malformed_relationship_entry"}]
    return [
        {"target": relationship.target, "type": relationship.type}
        for relationship in relationships or ()
    ]


def _parse_page(snapshot: MarkdownFile) -> dict[str, Any]:
    text = snapshot.text(errors="replace")
    parsed = parse_page_text(snapshot.relative, text)

    return {
        "path": parsed.path,
        "node_id": parsed.node_id,
        "slug": parsed.slug,
        "title": parsed.title,
        "summary": parsed.summary,
        "fields": set(parsed.fields),
        "links": list(parsed.links),
        "relationships": (
            _typed_relationships(parsed.text)
            if parsed.text.startswith("---\n") and "\n---" in parsed.text[4:]
            else []
        ),
        "text": text,
        "snapshot": snapshot,
    }


def lint_vault(
    vault: Path,
    *,
    require_trust_ledger: bool = True,
    strict_trust: bool = False,
    allowed_relationship_types: Collection[str] | None = None,
    allowed_lifecycles: Collection[str] | None = None,
    required_trust_fields: Collection[str] | None = None,
    skip_relative_subtrees: Collection[tuple[str, ...]] = (),
    schema_source: str = "framework-defaults",
) -> dict[str, Any]:
    excluded_paths = _relative_subtree_strings(skip_relative_subtrees)
    relationship_types = frozenset(
        ALLOWED_RELATIONSHIP_TYPES
        if allowed_relationship_types is None
        else allowed_relationship_types
    )
    lifecycles = frozenset(
        ALLOWED_LIFECYCLES if allowed_lifecycles is None else allowed_lifecycles
    )
    trust_fields = (
        tuple(required_trust_fields)
        if required_trust_fields is not None
        else TRUST_REQUIRED_FRONTMATTER
    )
    pages = [
        _parse_page(snapshot)
        for snapshot in _iter_pages(
            vault,
            skip_relative_subtrees=excluded_paths,
        )
    ]
    slug_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    node_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        slug_index[page["slug"]].append(page)
        node_index[page["node_id"]].append(page)
    by_slug = {slug: matches[0] for slug, matches in slug_index.items()}
    incoming: dict[str, int] = defaultdict(int)

    broken_links: list[dict[str, str]] = []
    for page in pages:
        for target in page["links"]:
            if target == page["slug"]:
                continue
            if target not in by_slug:
                broken_links.append({"page": page["path"], "target": target})
                continue
            incoming[target] += 1

    missing_frontmatter = []
    confidence_missing_fields = []
    trust_metadata_errors = []
    for page in pages:
        if page["slug"] in RESERVED_PAGE_STEMS:
            continue
        missing = [field for field in REQUIRED_FRONTMATTER if field not in page["fields"]]
        if missing:
            missing_frontmatter.append({"page": page["path"], "missing": missing})
        missing_trust = [field for field in trust_fields if field not in page["fields"]]
        if missing_trust:
            confidence_missing_fields.append({"page": page["path"], "missing": missing_trust})
        try:
            validate_trust_metadata(
                vault / page["path"],
                text=page["snapshot"].text(),
                allowed_lifecycles=lifecycles,
                required_trust_keys=(),
            )
        except ValueError as exc:
            trust_metadata_errors.append({"page": page["path"], "issue": str(exc)})

    title_index: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        title_index[page["title"].strip().lower()].append(page["path"])
    duplicate_titles = [
        {"title": title, "pages": paths}
        for title, paths in title_index.items()
        if title and len(paths) > 1
    ]
    duplicate_titles.sort(key=lambda item: (item["title"], item["pages"]))

    missing_summaries = [
        page["path"]
        for page in pages
        if page["slug"] not in RESERVED_PAGE_STEMS
        and ("summary" not in page["fields"] or not page["summary"])
    ]

    orphan_pages = []
    for page in pages:
        if page["slug"] in RESERVED_PAGE_STEMS:
            continue
        outgoing = sum(1 for target in page["links"] if target in by_slug and target != page["slug"])
        if outgoing == 0 and incoming.get(page["slug"], 0) == 0:
            orphan_pages.append(page["path"])

    typed_relationship_issues: list[dict[str, Any]] = []
    for page in pages:
        for index, relationship in enumerate(page["relationships"]):
            if "parse_error" in relationship:
                typed_relationship_issues.append(
                    {
                        "page": page["path"],
                        "index": index,
                        "issue": relationship["parse_error"],
                    }
                )
                continue
            relation_type = relationship.get("type", "")
            target_raw = relationship.get("target", "")
            if relation_type not in relationship_types:
                typed_relationship_issues.append(
                    {
                        "page": page["path"],
                        "index": index,
                        "issue": "invalid_type",
                        "type": relation_type,
                    }
                )
                continue
            target = normalise_node_id(target_raw)
            matches = node_index.get(target, []) if "/" in target else slug_index.get(target, [])
            if len(matches) > 1:
                typed_relationship_issues.append(
                    {
                        "page": page["path"],
                        "index": index,
                        "issue": "ambiguous_target",
                        "target": target,
                    }
                )
                continue
            resolved = matches[0] if matches else None
            if resolved is None:
                typed_relationship_issues.append(
                    {
                        "page": page["path"],
                        "index": index,
                        "issue": "missing_target",
                        "target": target,
                    }
                )
            elif resolved["node_id"] == page["node_id"]:
                typed_relationship_issues.append(
                    {
                        "page": page["path"],
                        "index": index,
                        "issue": "self_reference",
                        "target": target,
                    }
                )

    ledger_path = vault / TRUST_LEDGER_RELATIVE_PATH
    candidate_trust_report = check_trust_ledger(
        vault,
        ledger_path,
        allowed_lifecycles=lifecycles,
        required_trust_keys=trust_fields,
        skip_relative_subtrees=excluded_paths,
        schema_source=schema_source,
    )
    ledger_is_missing = candidate_trust_report["errors"] == [
        {"issue": "ledger_missing", "path": str(ledger_path)}
    ]
    trust_report = (
        None
        if ledger_is_missing and not require_trust_ledger
        else candidate_trust_report
    )

    findings = {
        "broken_links": broken_links,
        "missing_frontmatter": missing_frontmatter,
        "duplicate_titles": duplicate_titles,
        "missing_summaries": sorted(missing_summaries),
        "orphan_pages": sorted(orphan_pages),
        "typed_relationship_issues": typed_relationship_issues,
        "confidence_missing_fields": confidence_missing_fields,
        "trust_metadata_errors": trust_metadata_errors,
        "confidence_review_stale": trust_report["stale"] if trust_report else [],
        "confidence_unreviewed": trust_report["unreviewed"] if trust_report else [],
        "confidence_mismatches": trust_report["score_mismatches"] if trust_report else [],
        "confidence_ledger_errors": trust_report["errors"] if trust_report else [],
    }
    counts = {name: len(items) for name, items in findings.items()}

    # Staged migration (#28, #146): a missing trust ledger or trust frontmatter
    # on legacy pages only fails the vault when the owner has explicitly opted
    # into strict_trust. Ledger presence alone never silently enables strict
    # enforcement; core structural findings (broken links, missing core
    # frontmatter) always fail regardless of trust mode.
    trust_finding_names = (
        "confidence_missing_fields",
        "confidence_mismatches",
        "confidence_ledger_errors",
        "confidence_review_stale",
        "confidence_unreviewed",
    )
    trust_findings_present = any(counts[name] for name in trust_finding_names)
    trust_fails = strict_trust and any(
        counts[name]
        for name in (
            "confidence_missing_fields",
            "confidence_mismatches",
            "confidence_ledger_errors",
            "confidence_review_stale",
        )
    )

    if (
        counts["broken_links"]
        or counts["missing_frontmatter"]
        or counts["trust_metadata_errors"]
        or trust_fails
    ):
        status = "fail"
    elif (
        any(
            counts[name]
            for name in ("duplicate_titles", "missing_summaries", "orphan_pages", "typed_relationship_issues")
        )
        or trust_findings_present
    ):
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "schema": {
            "source": schema_source,
            "allowed_lifecycles": sorted(lifecycles),
            "allowed_relationship_types": sorted(relationship_types),
            "required_trust_fields": list(trust_fields),
        },
        "stats": {
            "pages": len(pages),
            "link_count": sum(len(page["links"]) for page in pages),
            "findings": counts,
            "trust": trust_report["counts"] if trust_report else {"ledger": "not_configured"},
        },
        "findings": findings,
    }

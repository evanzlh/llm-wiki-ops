"""Pure validation for the prospective state of a wiki transaction."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import posixpath
from pathlib import PurePosixPath
import re
from typing import Optional
from urllib.parse import SplitResult, urlsplit

from obsidian_wiki.frontmatter import FrontmatterError, parse_frontmatter
from obsidian_wiki.page_graph import normalise_node_id, slug as page_slug


_REQUIRED_FIELDS = (
    "title",
    "category",
    "tags",
    "sources",
    "created",
    "updated",
)
_SCALAR_FIELDS = ("title", "category", "created", "updated")
_LIST_FIELDS = ("tags", "sources")
_SEMANTIC_CATEGORIES = frozenset(
    {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")


@dataclass(frozen=True)
class ProspectivePage:
    """One page in the post-transaction vault view."""

    path: str
    text: str
    candidate: bool


@dataclass(frozen=True)
class ValidationIssue:
    """A deterministic validation finding for a prospective page."""

    code: str
    path: str
    message: str
    target: Optional[str] = None

    def as_dict(self) -> dict[str, object]:
        """Return a stable, JSON-ready representation of this issue."""
        result: dict[str, object] = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.target is not None:
            result["target"] = self.target
        return result


@dataclass(frozen=True)
class _GraphRecord:
    """The shared-normalized identity needed for prospective graph checks."""

    path: str
    node_id: str
    slug: str


def _issue(
    issues: list[ValidationIssue],
    code: str,
    path: str,
    message: str,
    target: Optional[str] = None,
) -> None:
    issues.append(ValidationIssue(code, path, message, target))


def _valid_date_or_aware_timestamp(value: str) -> bool:
    if _DATE_RE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if not _TIMESTAMP_RE.match(value):
        return False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return timestamp.tzinfo is not None and timestamp.utcoffset() is not None


def _expected_category(path: str) -> Optional[str]:
    parts = PurePosixPath(path).parts
    if not parts:
        return ""
    if parts[0] != "projects":
        return parts[0]
    if len(parts) == 2:
        return "projects"
    project_name = parts[1]
    page_name = PurePosixPath(parts[-1]).stem
    if len(parts) == 3 and page_name == project_name:
        return "projects"
    nested_category = parts[2]
    if len(parts) >= 4 and nested_category in _SEMANTIC_CATEGORIES - {"projects"}:
        return nested_category
    return None


def _normalise_page_path(path: str) -> str:
    return PurePosixPath(path).as_posix()


def _graph_record(path: str) -> _GraphRecord:
    normalized_path = _normalise_page_path(path)
    page_path = PurePosixPath(normalized_path)
    return _GraphRecord(
        path=normalized_path,
        node_id=normalise_node_id(page_path.with_suffix("").as_posix()),
        slug=page_slug(page_path.stem),
    )


def _validate_candidate_semantics(
    page: ProspectivePage,
    transaction_source_ids: tuple[str, ...],
    issues: list[ValidationIssue],
) -> None:
    path = _normalise_page_path(page.path)
    try:
        frontmatter = parse_frontmatter(page.text)
    except FrontmatterError as exc:
        _issue(
            issues,
            "frontmatter-invalid",
            path,
            "restricted frontmatter is invalid: " + str(exc),
        )
        return

    fields = frontmatter.fields
    for field in _REQUIRED_FIELDS:
        if field not in fields:
            _issue(
                issues,
                "frontmatter-" + field + "-missing",
                path,
                "required frontmatter field is missing: " + field,
            )

    for field in _SCALAR_FIELDS:
        if field not in fields:
            continue
        if field in frontmatter.lists:
            _issue(
                issues,
                "frontmatter-" + field + "-type",
                path,
                "frontmatter " + field + " must be a scalar",
            )
            continue
        if not frontmatter.scalars.get(field, "").strip():
            _issue(
                issues,
                "frontmatter-" + field + "-empty",
                path,
                "frontmatter " + field + " must not be empty",
            )

    for field in _LIST_FIELDS:
        if field in fields and field not in frontmatter.lists:
            _issue(
                issues,
                "frontmatter-" + field + "-type",
                path,
                "frontmatter " + field + " must be a list",
            )

    category = frontmatter.scalars.get("category", "").strip()
    expected_category = _expected_category(path)
    if category and category not in _SEMANTIC_CATEGORIES:
        _issue(
            issues,
            "frontmatter-category-unsupported",
            path,
            "frontmatter category is not supported: " + category,
            category,
        )
    if expected_category is None:
        _issue(
            issues,
            "frontmatter-category-path",
            path,
            "project page path must be an overview or use a semantic category",
        )
    elif category and category != expected_category:
        _issue(
            issues,
            "frontmatter-category-path",
            path,
            "frontmatter category must be " + expected_category + " for this path",
            expected_category,
        )

    for field in ("created", "updated"):
        value = frontmatter.scalars.get(field, "").strip()
        if value and not _valid_date_or_aware_timestamp(value):
            _issue(
                issues,
                "frontmatter-" + field + "-invalid",
                path,
                "frontmatter " + field + " must be an ISO date or timezone-aware timestamp",
            )

    sources = frontmatter.lists.get("sources")
    if sources is not None:
        if not sources:
            _issue(
                issues,
                "frontmatter-sources-empty",
                path,
                "frontmatter sources must not be empty",
            )
        if len(set(sources)) != len(sources):
            _issue(
                issues,
                "frontmatter-sources-duplicate",
                path,
                "frontmatter sources must not contain duplicates",
            )
        allowed_sources = set(transaction_source_ids)
        for source in sorted(set(sources) - allowed_sources):
            _issue(
                issues,
                "frontmatter-sources-foreign",
                path,
                "frontmatter source is outside the transaction: " + source,
                source,
            )


def _markdown_target(target_path: str, source_path: str) -> str:
    if not target_path:
        return ""
    if target_path.startswith("/"):
        return normalise_node_id(target_path)
    parent = PurePosixPath(source_path).parent.as_posix()
    resolved = posixpath.normpath(posixpath.join(parent, target_path))
    return normalise_node_id(resolved)


def _is_external_link(raw: str) -> bool:
    value = raw.strip()
    parsed = _split_url(value)
    return value.startswith("//") or parsed is None or bool(parsed.scheme)


def _split_url(value: str) -> Optional[SplitResult]:
    try:
        return urlsplit(value)
    except ValueError:
        return None


def _wikilink_target(raw: str) -> str:
    target_path = raw.split("|", 1)[0].split("#", 1)[0].strip()
    if not target_path or _is_external_link(target_path):
        return ""
    suffix = PurePosixPath(target_path).suffix
    if suffix and suffix.lower() != ".md":
        return ""
    return normalise_node_id(target_path)


def _markdown_destination_target(raw: str, source_path: str) -> str:
    destination = raw.strip()
    if destination.startswith("<"):
        closing = destination.find(">", 1)
        if closing < 0:
            return ""
        destination = destination[1:closing].strip()
    else:
        destination = destination.split(None, 1)[0] if destination else ""
    if not destination or _is_external_link(destination):
        return ""
    parsed = _split_url(destination)
    if parsed is None:
        return ""
    target_path = parsed.path
    if not target_path.endswith(".md"):
        return ""
    return _markdown_target(target_path, source_path)


@dataclass(frozen=True)
class _LinkSpan:
    """One syntactically complete local-link candidate in the input stream."""

    start: int
    end: int
    target_start: int
    target_end: int
    syntax: str
    label_end: Optional[int] = None
    destination_start: Optional[int] = None
    destination_end: Optional[int] = None


def _matching_brackets(text: str) -> dict[int, int]:
    """Return balanced square-bracket pairs in one forward pass."""
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for index, char in enumerate(text):
        if char == "[":
            stack.append(index)
        elif char == "]" and stack:
            pairs[stack.pop()] = index
    return pairs


@dataclass
class _DestinationContext:
    opening: int
    quote: Optional[str] = None
    in_angle_destination: bool = False
    saw_destination_content: bool = False
    title_separator_seen: bool = False


def _matching_destination_parentheses(
    text: str, link_openings: set[int]
) -> dict[int, int]:
    """Pair destinations while treating title and angle parentheses as content."""
    stack: list[_DestinationContext] = []
    pairs: dict[int, int] = {}
    index = 0
    while index < len(text):
        char = text[index]
        if not stack:
            if char == "(":
                stack.append(_DestinationContext(index))
            index += 1
            continue

        context = stack[-1]
        if context.quote is not None:
            if char == "\\" and index + 1 < len(text):
                index += 2
                continue
            if char == context.quote:
                context.quote = None
            elif char == "(" and index in link_openings:
                stack.append(_DestinationContext(index))
            index += 1
            continue

        if context.in_angle_destination:
            if char == ">":
                context.in_angle_destination = False
            elif char == "(" and index in link_openings:
                stack.append(_DestinationContext(index))
            index += 1
            continue

        if char in {"'", '"'} and context.title_separator_seen:
            context.quote = char
        elif char == "<" and not context.saw_destination_content:
            context.in_angle_destination = True
            context.saw_destination_content = True
        elif char == "(":
            stack.append(_DestinationContext(index))
        elif char == ")":
            pairs[context.opening] = index
            stack.pop()
        elif char.isspace():
            if context.saw_destination_content:
                context.title_separator_seen = True
        else:
            context.saw_destination_content = True
        index += 1
    return pairs


def _discard_invalid_outer_markdown(
    candidates: list[_LinkSpan],
) -> tuple[_LinkSpan, ...]:
    """Discard Markdown links whose visible label contains another Markdown link."""
    markdown_candidates = [
        candidate for candidate in candidates if candidate.syntax == "markdown"
    ]
    invalid = {
        owner
        for owner, next_markdown in zip(markdown_candidates, markdown_candidates[1:])
        if owner.label_end is not None and next_markdown.start < owner.label_end
    }
    return tuple(candidate for candidate in candidates if candidate not in invalid)


def _suppress_destination_embedded_syntax(
    candidates: tuple[_LinkSpan, ...],
) -> tuple[_LinkSpan, ...]:
    """Suppress candidates that begin inside a retained Markdown destination/title."""
    pending_owners: list[_LinkSpan] = []
    pending_index = 0
    active_owners: list[_LinkSpan] = []
    retained: list[_LinkSpan] = []

    for candidate in candidates:
        while (
            pending_index < len(pending_owners)
            and pending_owners[pending_index].destination_start is not None
            and pending_owners[pending_index].destination_start <= candidate.start
        ):
            active_owners.append(pending_owners[pending_index])
            pending_index += 1

        while (
            active_owners
            and active_owners[-1].destination_end is not None
            and active_owners[-1].destination_end <= candidate.start
        ):
            active_owners.pop()

        if not active_owners:
            retained.append(candidate)

        if candidate.syntax == "markdown":
            pending_owners.append(candidate)

    return tuple(retained)


def _apply_span_precedence(candidates: list[_LinkSpan]) -> tuple[_LinkSpan, ...]:
    """Retain visible links with linear invalid-owner and destination passes."""
    valid_owners = _discard_invalid_outer_markdown(candidates)
    return _suppress_destination_embedded_syntax(valid_owners)


def _link_spans(text: str) -> tuple[_LinkSpan, ...]:
    """Find complete Markdown and wikilink spans with syntax-aware precedence."""
    brackets = _matching_brackets(text)
    link_openings = {
        label_end + 1
        for label_end in brackets.values()
        if label_end + 1 < len(text) and text[label_end + 1] == "("
    }
    parentheses = _matching_destination_parentheses(text, link_openings)
    candidates: list[_LinkSpan] = []
    for start, label_end in brackets.items():
        second_opening = start + 1
        second_close = brackets.get(second_opening)
        is_wikilink = (
            text.startswith("[[", start)
            and second_close is not None
            and label_end == second_close + 1
        )
        if is_wikilink:
            candidates.append(
                _LinkSpan(start, label_end + 1, start + 2, second_close, "wikilink")
            )
            continue

        destination_opening = label_end + 1
        destination_end = parentheses.get(destination_opening)
        if (
            destination_opening < len(text)
            and text[destination_opening] == "("
            and destination_end is not None
        ):
            candidates.append(
                _LinkSpan(
                    start,
                    destination_end + 1,
                    destination_opening + 1,
                    destination_end,
                    "markdown",
                    label_end=label_end + 1,
                    destination_start=destination_opening + 1,
                    destination_end=destination_end,
                )
            )

    ordered = sorted(candidates, key=lambda span: (span.start, span.end, span.syntax))
    return _apply_span_precedence(ordered)


def _page_links(path: str, text: str) -> tuple[str, ...]:
    """Extract targets from completed, non-overlapping link constructs."""
    targets: list[str] = []
    for span in _link_spans(text):
        raw_target = text[span.target_start : span.target_end]
        target = (
            _wikilink_target(raw_target)
            if span.syntax == "wikilink"
            else _markdown_destination_target(raw_target, path)
        )
        if target:
            targets.append(target)
    return tuple(targets)


def _validate_graph(pages: tuple[ProspectivePage, ...], issues: list[ValidationIssue]) -> None:
    records = [(page, _graph_record(page.path)) for page in pages]
    by_slug: dict[str, list[_GraphRecord]] = defaultdict(list)
    by_node_id: dict[str, list[_GraphRecord]] = defaultdict(list)
    for _page, record in records:
        by_slug[record.slug].append(record)
        by_node_id[record.node_id].append(record)

    for slug, matches in by_slug.items():
        if len(matches) < 2:
            continue
        for record in matches:
            _issue(
                issues,
                "duplicate-page-identity",
                record.path,
                "duplicate page identity: " + slug,
                slug,
            )

    for page, record in records:
        for target in _page_links(record.path, page.text):
            if target == record.node_id or ("/" not in target and target == record.slug):
                continue
            matches = by_node_id[target] if "/" in target else by_slug[target]
            if not matches:
                _issue(
                    issues,
                    "broken-link",
                    record.path,
                    "broken link target: " + target,
                    target,
                )
            elif len(matches) > 1:
                _issue(
                    issues,
                    "ambiguous-link",
                    record.path,
                    "ambiguous link target: " + target,
                    target,
                )


def validate_prospective_pages(
    pages: tuple[ProspectivePage, ...], transaction_source_ids: tuple[str, ...]
) -> tuple[ValidationIssue, ...]:
    """Validate candidate metadata and all links in a proposed vault state."""
    issues: list[ValidationIssue] = []
    for page in pages:
        if page.candidate:
            _validate_candidate_semantics(page, transaction_source_ids, issues)
    _validate_graph(pages, issues)
    return tuple(sorted(issues, key=lambda issue: (issue.path, issue.code, issue.target or "")))


__all__ = ["ProspectivePage", "ValidationIssue", "validate_prospective_pages"]

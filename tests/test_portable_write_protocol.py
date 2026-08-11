import json
import re
from pathlib import Path

import pytest

from obsidian_wiki.transaction import TransactionRecord
from obsidian_wiki.transaction_guidance import guidance_for_record


ROOT = Path(__file__).resolve().parents[1]
WRITE_SKILLS = (
    "claude-history-ingest",
    "codex-history-ingest",
    "copilot-history-ingest",
    "cross-linker",
    "daily-update",
    "hermes-history-ingest",
    "openclaw-history-ingest",
    "pi-history-ingest",
    "tag-taxonomy",
    "wiki-agent",
    "wiki-capture",
    "wiki-dashboard",
    "wiki-dedup",
    "wiki-import",
    "wiki-ingest",
    "wiki-lint",
    "wiki-rebuild",
    "wiki-research",
    "wiki-stage-commit",
    "wiki-status",
    "wiki-synthesize",
    "wiki-update",
)
READ_SKILLS = ("wiki-query", "wiki-narrate", "wiki-digest")


def skill_text(name: str) -> str:
    return (ROOT / ".skills" / name / "SKILL.md").read_text(encoding="utf-8")


def markdown_section(text: str, heading: str) -> str:
    marker = f"{heading}\n"
    start = text.index(marker) + len(marker)
    level = len(heading) - len(heading.lstrip("#"))
    candidates = [
        index
        for sublevel in range(2, level + 1)
        if (index := text.find(f"\n{'#' * sublevel} ", start)) >= 0
    ]
    return text[start : min(candidates, default=len(text))]


def h2_headings(text: str) -> list[tuple[str, int, int]]:
    headings: list[tuple[str, int, int]] = []
    fence: tuple[str, int] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        fence_match = re.match(
            r"^[ \t]{0,3}(?P<marker>`{3,}|~{3,})(?P<tail>.*)$", content
        )
        if fence_match is not None:
            marker = fence_match.group("marker")
            tail = fence_match.group("tail")
            if fence is None:
                fence = (marker[0], len(marker))
            elif (
                marker[0] == fence[0]
                and len(marker) >= fence[1]
                and not tail.strip()
            ):
                fence = None
            offset += len(line)
            continue

        if fence is None:
            heading_match = re.match(
                r"^##[ \t]+(?P<title>[^\r\n]+?)[ \t]*$", content
            )
            if heading_match is not None:
                headings.append(
                    (heading_match.group("title"), offset, offset + len(content))
                )
        offset += len(line)
    return headings


def h2_section(
    text: str,
    heading: str,
    *,
    relative: str,
    next_heading: str | None = None,
) -> str:
    headings = h2_headings(text)
    matches = [match for match in headings if match[0] == heading]
    assert len(matches) == 1, (
        f"{relative}: expected exactly one H2 {heading!r}, found {len(matches)}"
    )
    match = matches[0]
    position = headings.index(match)
    following = headings[position + 1 :]
    end = following[0][1] if following else len(text)

    if next_heading is not None:
        later = [item for item in headings if item[0] == next_heading]
        assert len(later) == 1, (
            f"{relative}: expected exactly one H2 {next_heading!r}, found {len(later)}"
        )
        assert later[0][1] > match[1], (
            f"{relative}: H2 {next_heading!r} must follow H2 {heading!r}"
        )
        assert following and following[0] == later[0], (
            f"{relative}: H2 {next_heading!r} must be the next H2 after {heading!r}"
        )

    return text[match[2] : end]


def fenced_json_after(text: str, label: str) -> object:
    labeled = text.split(label, 1)[1]
    fenced = labeled.split("```json\n", 1)[1].split("\n```", 1)[0]
    return json.loads(fenced)


def test_every_write_skill_routes_portable_writes_to_transactions() -> None:
    for name in WRITE_SKILLS:
        text = skill_text(name)
        assert "Portable Write Protocol" in text, name


def test_canonical_protocol_owns_begin_commit_recovery_and_hot_commands() -> None:
    text = skill_text("llm-wiki")
    for required in (
        "obsidian-wiki transaction begin",
        "obsidian-wiki transaction delete",
        "obsidian-wiki transaction commit",
        "obsidian-wiki transaction list",
        "obsidian-wiki transaction retry",
        "obsidian-wiki transaction restore",
        "obsidian-wiki transaction abort",
        "obsidian-wiki transaction discard",
        "obsidian-wiki hot status --json",
        "obsidian-wiki hot mark-current",
        "Do not commit, push, or open a pull request",
        "index.md and log.md are stable",
        "hot.md is local and ignored",
        "Personal mode",
    ):
        assert required in text


@pytest.mark.parametrize(
    ("scope", "phrase"),
    (
        ("completion", "transaction validate <id> --json --pretty"),
        ("completion", "created = updated = started_at"),
        ("completion", "preserve the existing `created`"),
        ("completion", "keep the repository root as the command working directory"),
        ("completion", "do not `cd` into `candidate_vault`"),
        ("completion", "hot inputs --json --pretty"),
        ("manifest-v2", "cache-check --configured"),
        ("manifest-v2", "preserve Unicode filenames"),
    ),
    ids=lambda value: value.replace(" ", "-") if isinstance(value, str) else None,
)
def test_canonical_portable_protocol_defines_runtime_safety_rules(
    scope: str, phrase: str
) -> None:
    relative = ".skills/llm-wiki/SKILL.md"
    text = skill_text("llm-wiki")
    if scope == "completion":
        scoped = h2_section(
            text,
            "Portable Repository completion",
            relative=relative,
            next_heading="Personal mode completion",
        )
    else:
        scoped = markdown_section(text, "### Portable Repository mode — manifest v2")
    assert phrase in scoped, f"{relative} {scope}: missing {phrase!r}"


def test_read_skills_never_trust_stale_hot_state() -> None:
    for name in READ_SKILLS:
        text = skill_text(name)
        assert "obsidian-wiki hot status --json" in text, name
        assert "stale" in text.lower(), name


def test_portable_stage_commit_uses_cli_transactions_not_staging_folder() -> None:
    text = skill_text("wiki-stage-commit")
    assert "obsidian-wiki transaction list --json" in text
    assert "_staging/ remains personal-mode" in text
    assert "Return immediately after the selected portable action" in text
    assert "If the transaction list is empty" in text
    assert "Commit a reviewed active transaction" in text
    assert "retry a failed transaction" in text
    assert "restore only for an explicit rollback" in text


def test_portable_read_outputs_fail_closed_instead_of_direct_writing() -> None:
    narrate = skill_text("wiki-narrate")
    assert "`--save` is unsupported in Portable Repository mode" in narrate
    assert "never write `_readouts/`" in narrate

    digest = skill_text("wiki-digest")
    assert "`sources: []` is Personal-mode-only" in digest
    assert "source-backed candidate" in digest
    assert "review the candidate and commit it through the Portable Write Protocol" in digest


def test_portable_mode_branches_before_mutation_and_uses_real_sources() -> None:
    capture = skill_text("wiki-capture")
    branch = capture.index("**Portable Write Protocol branch:**")
    create_raw = capture.index("Ensure `$OBSIDIAN_RAW_DIR` exists")
    assert branch < create_raw

    synthesize = skill_text("wiki-synthesize")
    assert "actual authoritative source files traced from" in synthesize
    assert "never use compiled vault pages as transaction sources" in synthesize
    assert "`sources` must contain only the transaction's repository-relative Source IDs" in synthesize
    assert "Compiled vault page paths belong in body links" in synthesize


def test_portable_status_insights_are_transactional_knowledge_pages() -> None:
    status = skill_text("wiki-status")
    assert "synthesis/wiki-insights.md" in status
    assert "`_insights.md` remains Personal-mode-only" in status
    assert "review and commit the candidate" in status
    assert "read the previous snapshot from `synthesis/wiki-insights.md`" in status
    assert "read the previous snapshot from `_insights.md`" in status
    assert "`_insights.md` reads and writes are Personal-mode-only" in status
    for required_frontmatter in (
        "title: Wiki Insights",
        "category: synthesis",
        "tags: [wiki-insights, meta/graph]",
        "sources: [<transaction Source IDs>]",
        "created: <started_at, or preserved value on update>",
        "updated: <started_at>",
    ):
        assert required_frontmatter in status
    assert "preserve the existing `created` value" in status


def test_query_read_only_contract_allows_only_local_hot_freshness_state() -> None:
    query = skill_text("wiki-query")
    assert "local ignored hot freshness state is derived state, not wiki content" in query
    assert "does not permit a compiled-page write" in query


def test_cli_and_configuration_docs_cover_portable_context_and_recovery() -> None:
    cli = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    setup = markdown_section(cli, "## Setup & inspection")
    for required in (
        "obsidian-wiki info --json --pretty",
        "portable-context-overridden",
        "context_warnings",
    ):
        assert required in setup

    transactions = markdown_section(
        cli, "## Portable transactions and local hot state"
    )
    for required in (
        "`recovery`",
        '"status"',
        '"error"',
        '"code"',
        '"message"',
        '"transaction_id"',
        '"transaction_status"',
        '"inspect_command"',
        "preferred_action",
        "alternatives",
        "command",
        "reason",
        "requires",
        "recommended_action",
        "allowed_actions",
        "config-error",
        "manifest-error",
        "transaction-error",
        "validated retained record",
        "obsidian-wiki transaction list --json",
    ):
        assert required in transactions

    configuration = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    explicit = markdown_section(
        configuration, "### Explicit selections from a Portable Repository CWD"
    )
    for required in (
        "same vault",
        "sync",
        "sync-setup",
        "branch and pull request",
        "dangling symlink",
        "no `.env` or global fallback",
        "does not parse or load",
        "cannot block the explicit selection",
    ):
        assert required in explicit


def test_cli_transaction_json_examples_match_recovery_guidance() -> None:
    cli = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    transactions = markdown_section(
        cli, "## Portable transactions and local hot state"
    )
    failure = fenced_json_after(
        transactions, "Transaction JSON failures use this exact envelope shape"
    )
    listed = fenced_json_after(
        transactions,
        "`obsidian-wiki transaction list --json` remains a top-level array.",
    )

    assert isinstance(failure, dict)
    assert failure["status"] == "error"
    recovery = failure["recovery"]
    assert isinstance(recovery, dict)
    assert isinstance(listed, list) and len(listed) == 1
    listed_record = listed[0]
    assert isinstance(listed_record, dict)
    assert recovery["transaction_id"] == listed_record["transaction_id"]
    assert recovery["transaction_status"] == listed_record["status"]

    record = TransactionRecord(
        transaction_id=recovery["transaction_id"],
        status=recovery["transaction_status"],
        started_at="2026-08-10T00:00:00+00:00",
        source_ids=("sources/a.md",),
        workspace=Path("workspace"),
        candidate_vault=Path("candidate"),
        preimages={"concepts/a.md": "sha256:abc"},
        deletions=("concepts/old.md",),
    )
    guidance = guidance_for_record(record)
    serialized = guidance.as_dict()

    assert len(recovery["alternatives"]) == len(serialized["alternatives"])
    assert recovery["preferred_action"] == serialized["preferred_action"]
    assert recovery["alternatives"] == serialized["alternatives"]
    allowed_actions = [
        serialized["preferred_action"],
        *serialized["alternatives"],
    ]
    assert len(listed_record["allowed_actions"]) == len(allowed_actions)
    assert listed_record["recommended_action"] == serialized["preferred_action"]
    assert listed_record["allowed_actions"] == allowed_actions

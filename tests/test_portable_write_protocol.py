from pathlib import Path


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
        "created: TIMESTAMP",
        "updated: TIMESTAMP",
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

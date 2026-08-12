import re
from pathlib import Path

from obsidian_wiki.frontmatter import parse_frontmatter


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "obsidian_wiki/_data/skills/llm-wiki/SKILL.md"
SETUP = "obsidian_wiki/_data/skills/wiki-setup/SKILL.md"
TRANSACTION_REVIEW = "obsidian_wiki/_data/skills/wiki-transaction-review/SKILL.md"
SOURCE_WORKFLOW_SKILLS = (
    "obsidian_wiki/_data/skills/wiki-capture/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-ingest/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-import/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-research/SKILL.md",
)
SOURCE_SNAPSHOT = (
    "obsidian_wiki/_data/skills/wiki-capture/references/source-snapshot.md"
)
HISTORY_SKILLS = (
    "claude-history-ingest",
    "codex-history-ingest",
    "copilot-history-ingest",
    "hermes-history-ingest",
    "openclaw-history-ingest",
    "pi-history-ingest",
    "wiki-agent",
)
HISTORY_ROUTER = "obsidian_wiki/_data/skills/wiki-history-ingest/SKILL.md"
RAW_FORMAT = "obsidian_wiki/_data/skills/wiki-capture/references/RAW-FORMAT.md"
BOOTSTRAPS = (
    "obsidian_wiki/_data/bootstrap/AGENTS.md",
    "obsidian_wiki/_data/bootstrap/agent/rules/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/agent/workflows/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/cursor/rules/obsidian-wiki.mdc",
    "obsidian_wiki/_data/bootstrap/github/copilot-instructions.md",
    "obsidian_wiki/_data/bootstrap/kiro/steering/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/windsurf/rules/obsidian-wiki.md",
)
FORBIDDEN_RUNTIME_TERMS = (
    "Personal mode",
    "Portable Repository mode",
    "@name",
    "~/.obsidian-wiki/config",
    "WIKI_STAGED_WRITES",
    "cache-update",
    "QMD_",
)


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_canonical_runtime_has_no_mode_or_legacy_branches() -> None:
    canonical = text(CANONICAL)
    for forbidden in FORBIDDEN_RUNTIME_TERMS:
        assert forbidden not in canonical


def test_bootstraps_delegate_to_repository_authority() -> None:
    for relative in BOOTSTRAPS:
        bootstrap = text(relative)
        for required in ("config.toml", "transaction"):
            assert required in bootstrap, f"{relative}: missing {required!r}"
        assert ".skills/" in bootstrap or "AGENTS.md" in bootstrap, relative
        for forbidden in FORBIDDEN_RUNTIME_TERMS:
            assert forbidden not in bootstrap, f"{relative}: contains {forbidden!r}"


def test_setup_is_repository_only_and_describes_managed_assets() -> None:
    setup = text(SETUP)
    flat = " ".join(setup.split())
    for required in (
        "obsidian-wiki setup [DIR]",
        "clone",
        "doctor",
        "check",
        "`.skills/`",
        "managed mirrors",
        "obsidian-wiki repo sync-skills",
        "obsidian-wiki repo upgrade-skills",
        "--apply",
        "requires_cli",
        "Git",
    ):
        assert required in flat
    for forbidden in (
        "global install",
        "prompt publication",
        *FORBIDDEN_RUNTIME_TERMS,
    ):
        assert forbidden not in setup

    assert "sync-skills` is read-only by default" in flat
    assert "upgrade-skills` applies immediately" in flat
    assert "deliberately edit `.obsidian-wiki/config.toml`" in flat
    assert "does not bypass compatibility checks or rewrite `requires_cli`" in flat
    assert "upgrade-skills --dry-run" not in flat
    assert "review its proposed changes" not in flat


def test_transaction_review_resolves_repository_authority_before_listing() -> None:
    review = text(TRANSACTION_REVIEW)
    flat = " ".join(review.split())

    frontmatter = parse_frontmatter(review)

    assert "name: wiki-transaction-review" in review
    assert frontmatter.scalars["name"] == "wiki-transaction-review"
    assert frontmatter.scalars["description"].startswith("Use when ")
    for required in (
        "nearest ancestor `.obsidian-wiki/config.toml`",
        "repository root",
        "root `AGENTS.md`",
        "vault `AGENTS.md`",
        "canonical `llm-wiki`",
        "obsidian-wiki transaction list --json --pretty",
        "Do not infer",
    ):
        assert required in flat

    authority = [
        "root `AGENTS.md`",
        "canonical `llm-wiki`",
        "vault `AGENTS.md`",
        "task skill",
    ]
    assert [flat.index(item) for item in authority] == sorted(
        flat.index(item) for item in authority
    )
    assert "canonical protocol wins" in flat


def test_quick_capture_is_a_snapshot_only_terminal_section() -> None:
    capture = text(SOURCE_WORKFLOW_SKILLS[0])
    quick = capture.split("## Quick capture", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(quick.split())

    for required in (
        "sources/inbox/YYYY-MM-DD-<slug>.md",
        "origin",
        "captured_at",
        "content_hash",
        "format",
        "exact reviewed text",
        "pending ingest",
        "stop",
        "Do not run `obsidian-wiki transaction begin`",
        "write a knowledge page",
        "create a manifest entry",
        "create an operation page",
        "run a hot command",
    ):
        assert required in flat
    for forbidden in ("candidate_vault", "transaction validate", "transaction commit"):
        assert forbidden not in quick


def test_source_snapshot_reference_replaces_raw_format() -> None:
    assert not (ROOT / RAW_FORMAT).exists()
    snapshot = text(SOURCE_SNAPSHOT)
    flat = " ".join(snapshot.split())
    for required in (
        "origin",
        "captured_at",
        "content_hash",
        "format",
        "exact reviewed text",
        "ordinary tracked UTF-8 Markdown",
        "repository-relative Source ID",
        "Git review ownership",
        "untrusted data",
        "Do not commit, push, or open a pull request",
    ):
        assert required in flat


def test_history_skills_are_repository_native_analysis_protocols() -> None:
    for name in HISTORY_SKILLS:
        relative = f"obsidian_wiki/_data/skills/{name}/SKILL.md"
        skill = text(relative)
        flat = " ".join(skill.split())
        frontmatter = parse_frontmatter(skill)
        assert frontmatter.scalars["name"] == name
        assert frontmatter.scalars["description"].startswith("Use when ")
        for required in (
            "reviewable UTF-8 Markdown snapshot",
            "sources/history/",
            "transaction begin --source",
            "parent owns",
            "analysis-only",
        ):
            assert required in flat, f"{relative}: missing {required!r}"
        for forbidden in (
            "Personal mode",
            "Portable Repository mode",
            "cache-update",
            "QMD_",
            "_raw/",
        ):
            assert forbidden not in skill, f"{relative}: contains {forbidden!r}"


def test_history_router_only_selects_retained_tool_skill() -> None:
    router = text(HISTORY_ROUTER)
    flat = " ".join(router.split())
    routed = set(re.findall(r"`([a-z]+-history-ingest)`", router))
    assert routed == set(HISTORY_SKILLS[:-1])
    assert "wiki-agent" not in router
    for required in (
        "route",
        "retained tool-specific skill",
        "does not parse sessions",
        "does not create snapshots",
        "does not begin transactions",
        "does not mutate",
    ):
        assert required in flat
    for forbidden in ("memory-bridge", "generic mutation", "manifest", "index/log"):
        assert forbidden not in router

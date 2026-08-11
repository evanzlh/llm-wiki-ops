import re
from pathlib import Path

import pytest

from obsidian_wiki import SOURCE_INSTALL_COMMAND


ROOT = Path(__file__).resolve().parents[1]
CORE = (
    "AGENTS.md",
    ".skills/llm-wiki/SKILL.md",
    ".skills/wiki-ingest/SKILL.md",
    ".skills/wiki-update/SKILL.md",
    ".skills/wiki-status/SKILL.md",
    ".skills/wiki-query/SKILL.md",
    ".skills/wiki-context-pack/SKILL.md",
)
PORTABLE_WRITE_SKILLS = (
    ".skills/claude-history-ingest/SKILL.md",
    ".skills/codex-history-ingest/SKILL.md",
    ".skills/copilot-history-ingest/SKILL.md",
    ".skills/cross-linker/SKILL.md",
    ".skills/daily-update/SKILL.md",
    ".skills/hermes-history-ingest/SKILL.md",
    ".skills/openclaw-history-ingest/SKILL.md",
    ".skills/pi-history-ingest/SKILL.md",
    ".skills/tag-taxonomy/SKILL.md",
    ".skills/wiki-agent/SKILL.md",
    ".skills/wiki-capture/SKILL.md",
    ".skills/wiki-dashboard/SKILL.md",
    ".skills/wiki-dedup/SKILL.md",
    ".skills/wiki-import/SKILL.md",
    ".skills/wiki-ingest/SKILL.md",
    ".skills/wiki-lint/SKILL.md",
    ".skills/wiki-rebuild/SKILL.md",
    ".skills/wiki-research/SKILL.md",
    ".skills/wiki-stage-commit/SKILL.md",
    ".skills/wiki-status/SKILL.md",
    ".skills/wiki-synthesize/SKILL.md",
    ".skills/wiki-update/SKILL.md",
)
HISTORY_WRITE_SKILLS = (
    ".skills/claude-history-ingest/SKILL.md",
    ".skills/codex-history-ingest/SKILL.md",
    ".skills/copilot-history-ingest/SKILL.md",
    ".skills/hermes-history-ingest/SKILL.md",
    ".skills/openclaw-history-ingest/SKILL.md",
    ".skills/pi-history-ingest/SKILL.md",
    ".skills/wiki-agent/SKILL.md",
)
PORTABLE_COMPLETION_REQUIREMENTS = (
    "obsidian-wiki transaction validate",
    "obsidian-wiki transaction commit",
    "obsidian-wiki hot status --json",
    "obsidian-wiki hot inputs --json --pretty",
    "obsidian-wiki hot mark-current --json",
    "Stop the portable workflow here",
)
PORTABLE_COMPLETION_PROHIBITION = (
    "Do not run `cache-update`, edit manifest shards, update `index.md` or `log.md`, "
    "write `hot.md` as part of the transaction, refresh Personal QMD tracking, "
    "create a Git snapshot, commit, or push."
)
PORTABLE_EXECUTABLE_COMMANDS = (
    (
        "cache-update",
        re.compile(
            r"^[ \t]*(?:(?:[-*+]|[$>])[ \t]+)?`?obsidian-wiki[ \t]+cache-update\b",
            re.MULTILINE,
        ),
    ),
    (
        "git commit",
        re.compile(
            r"^[ \t]*(?:(?:[-*+]|[$>])[ \t]+)?`?git[ \t]+commit\b",
            re.MULTILINE,
        ),
    ),
    (
        "git push",
        re.compile(
            r"^[ \t]*(?:(?:[-*+]|[$>])[ \t]+)?`?git[ \t]+push\b",
            re.MULTILINE,
        ),
    ),
    (
        "QMD refresh",
        re.compile(
            r"^[ \t]*(?:(?:[-*+]|[$>])[ \t]+)?`?"
            r"(?:qmd|\$\{QMD_CLI:-qmd\})[ \t]+(?:update|embed)\b",
            re.MULTILINE,
        ),
    ),
)
BOOTSTRAPS = (
    ".agent/rules/obsidian-wiki.md",
    ".cursor/rules/obsidian-wiki.mdc",
    ".windsurf/rules/obsidian-wiki.md",
    ".kiro/steering/obsidian-wiki.md",
    ".github/copilot-instructions.md",
)
HUMAN_DOCS = (
    "docs/configuration.md",
    "docs/installation.md",
    "docs/agents.md",
    "docs/cli.md",
    "docs/architecture.md",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _h2_headings(text: str) -> list[tuple[str, int, int]]:
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


def _h2_section(
    text: str,
    heading: str,
    *,
    relative: str,
    next_heading: str | None = None,
) -> str:
    headings = _h2_headings(text)
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


def test_h2_section_ignores_fenced_completion_headings() -> None:
    text = """## Prelude
```markdown
## Portable Repository completion
```
~~~markdown
## Personal mode completion
~~~
## Portable Repository completion
portable body
## Personal mode completion
personal body
## Epilogue
"""

    section = _h2_section(
        text,
        "Portable Repository completion",
        relative="synthetic.md",
        next_heading="Personal mode completion",
    )

    assert "portable body" in section
    assert "personal body" not in section


def test_core_protocol_documents_portable_precedence() -> None:
    for relative in CORE:
        text = _text(relative)
        assert ".obsidian-wiki/config.toml" in text, relative
        assert "@name" in text, relative
        assert "README_TW.md" not in text, relative


def test_canonical_protocol_has_the_complete_resolution_order() -> None:
    expected = (
        "explicit `@name`",
        "nearest ancestor `.obsidian-wiki/config.toml`",
        "nearest ancestor `.env` containing `OBSIDIAN_VAULT_PATH`",
        "`~/.obsidian-wiki/config`",
        "setup guidance",
    )
    for relative in ("AGENTS.md", ".skills/llm-wiki/SKILL.md"):
        text = _text(relative)
        positions = [text.index(item) for item in expected]
        assert positions == sorted(positions), relative
        assert "configured `sources`" in text, relative
        assert "<vault>/AGENTS.md" in text, relative
        assert "absolute `OBSIDIAN_WIKI_REPO`" in text, relative


def test_core_skills_and_bootstraps_point_to_the_canonical_protocol() -> None:
    for relative in (*CORE[2:], *BOOTSTRAPS):
        text = _text(relative)
        assert "Config Resolution Protocol" in text, relative
        assert "AGENTS.md" in text or "llm-wiki/SKILL.md" in text, relative
        assert ".obsidian-wiki/config.toml" in text, relative


def test_portable_setup_never_writes_global_config() -> None:
    text = _text(".skills/wiki-setup/SKILL.md")
    assert "obsidian-wiki setup --portable" in text
    assert "does not write `~/.obsidian-wiki/config`" in text
    assert "Personal mode" in text
    assert "Portable Repository mode" in text
    for phrase in (
        SOURCE_INSTALL_COMMAND,
        "only an ordinary `.git` directory",
        "does not run `git init`",
        "setup first, then `git init`",
        "legacy layouts need explicit migration",
    ):
        assert phrase in text


def test_human_docs_cover_the_portable_repository_contract() -> None:
    combined = "\n".join(_text(relative) for relative in HUMAN_DOCS)
    for phrase in (
        "implementation = \"evanzlh/obsidian-wiki\"",
        "repo upgrade-skills",
        "regular Markdown files",
        "Linux and macOS",
        "does not contain `.venv`",
        SOURCE_INSTALL_COMMAND,
        "only an ordinary `.git` directory",
        "does not run `git init`",
    ):
        assert phrase in combined


def test_readmes_keep_the_portable_commands_in_translation_parity() -> None:
    commands = (
        "obsidian-wiki setup --portable ./team-knowledge",
        "obsidian-wiki repo upgrade-skills",
    )
    for command in commands:
        assert command in _text("README.md")
        assert command in _text("README_ZH.md")


@pytest.mark.parametrize(
    "relative",
    PORTABLE_WRITE_SKILLS,
    ids=lambda relative: Path(relative).parent.name,
)
def test_portable_write_skills_have_local_completion_branches(relative: str) -> None:
    portable = _h2_section(
        _text(relative),
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    for required in PORTABLE_COMPLETION_REQUIREMENTS:
        assert required in portable, f"{relative}: missing {required!r}"
    collapsed = " ".join(portable.split())
    assert PORTABLE_COMPLETION_PROHIBITION in collapsed, (
        f"{relative}: missing standardized portable prohibition"
    )
    for command, pattern in PORTABLE_EXECUTABLE_COMMANDS:
        assert pattern.search(portable) is None, (
            f"{relative}: portable branch contains executable {command} command"
        )


def test_portable_ingest_completion_forbids_personal_tracking_steps() -> None:
    relative = ".skills/wiki-ingest/SKILL.md"
    portable = _h2_section(
        _text(relative),
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    for forbidden in (
        "obsidian-wiki cache-update",
        "Add entries for any new pages",
        "Append an entry",
        "Rewrite the **Recent Activity** section",
    ):
        assert forbidden not in portable, (
            f"{relative}: contains legacy action {forbidden!r}"
        )


@pytest.mark.parametrize(
    "relative",
    HISTORY_WRITE_SKILLS,
    ids=lambda relative: Path(relative).parent.name,
)
def test_history_family_materializes_portable_source_snapshots(relative: str) -> None:
    text = _text(relative)
    shared = text.split("## Portable Repository completion", 1)[0]
    portable = _h2_section(
        text,
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    shared_flat = " ".join(shared.split())
    portable_flat = " ".join(portable.split())

    for required in (
        "Select one terminal workflow after the shared analysis and page-preparation steps",
        "parent agent resolves config and mode",
        "owner `AGENTS.md`",
        "analysis-only workers",
        "Personal append mode uses manifest v1",
        "Portable append mode compares discovered agent/session identity and content hash against existing reviewed snapshots",
    ):
        assert required in shared_flat, (
            f"{relative}: missing shared history guard {required!r}"
        )

    for required in (
        "external history cache and selected session files are transient analysis input",
        "never Portable Source IDs",
        "parent agent creates",
        "small, reviewable UTF-8 Markdown or plain-text snapshot",
        "strictly below the configured `sources` root",
        "agent identity",
        "session identity",
        "relevant excerpts",
        "source timestamps",
        "content hash",
        "no machine-local absolute paths",
        "If an adequate snapshot cannot be created",
        "stop or use Personal mode",
        "Compute full source closure",
        "existing `sources` Source ID",
        "updated or deleted",
        "Preserve valid Unicode",
        "repository root as the command CWD",
        "do not `cd` into it",
        "created = updated = started_at",
        "preserve the existing `created`",
        "obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty",
        "obsidian-wiki transaction delete <id> <vault-relative-page.md>",
        "Review every warning",
        "Fix every issue",
        "status-aware recovery",
        "recovery.preferred_action",
        "recommended_action",
        "allowed_actions",
        "obsidian-wiki transaction abort <id> --json",
        "obsidian-wiki transaction retry <id> --json",
        "obsidian-wiki transaction restore <id> --json",
        "obsidian-wiki transaction discard <id> --json",
        "no trusted transaction ID",
        "outcome is ambiguous",
        "after commit succeeds or recovery is fully resolved",
        "use only those bounded inputs to write the semantic `hot.md` as the agent",
    ):
        assert required in portable_flat, (
            f"{relative}: missing Portable history rule {required!r}"
        )

    snapshot = portable_flat.index(
        "small, reviewable UTF-8 Markdown or plain-text snapshot"
    )
    closure = portable_flat.index("Compute full source closure")
    begin = portable_flat.index("obsidian-wiki transaction begin")
    assert snapshot < closure < begin, (
        f"{relative}: snapshot must precede source closure and transaction begin"
    )


@pytest.mark.parametrize(
    "relative",
    HISTORY_WRITE_SKILLS,
    ids=lambda relative: Path(relative).parent.name,
)
def test_history_family_personal_completion_is_concrete_and_terminal(
    relative: str,
) -> None:
    text = _text(relative)
    shared = text.split("## Portable Repository completion", 1)[0]
    portable = _h2_section(
        text,
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    personal = _h2_section(text, "Personal mode completion", relative=relative)
    shared_flat = " ".join(shared.split())
    portable_flat = " ".join(portable.split())
    personal_flat = " ".join(personal.split())

    for forbidden in (
        "suppress the direct manifest",
        "delegate the selected authoritative session sources",
    ):
        assert forbidden not in shared_flat, (
            f"{relative}: legacy mode bypass {forbidden!r}"
        )
    assert "<agent>://" not in portable_flat, (
        f"{relative}: Portable branch permits pseudo-source provenance"
    )

    for required in (
        "Use this branch only when config resolution selected Personal mode",
        "Write the prepared pages directly below `<resolved-vault-path>`",
        "manifest v1",
        "<resolved-vault-path>/.manifest.json",
        "obsidian-wiki cache-update <resolved-vault-path>",
        "<resolved-vault-path>/index.md",
        "<resolved-vault-path>/log.md",
        "<resolved-vault-path>/hot.md",
        "<resolved-qmd-cli> update",
        "qmd://<resolved-qmd-wiki-collection>/",
        "Personal Git snapshot",
        "config resolution does not export these values into the parent shell",
        "Do not fall through into Portable Repository completion",
    ):
        assert required in personal_flat, (
            f"{relative}: missing Personal history rule {required!r}"
        )
    for forbidden in (
        "$OBSIDIAN_VAULT_PATH",
        "$QMD_WIKI_COLLECTION",
        "${QMD_CLI:-qmd}",
    ):
        assert forbidden not in personal_flat, (
            f"{relative}: Personal branch assumes shell export {forbidden!r}"
        )


@pytest.mark.parametrize(
    "relative",
    HISTORY_WRITE_SKILLS[:-1],
    ids=lambda relative: Path(relative).parent.name,
)
def test_bulk_history_append_mode_does_not_parse_portable_manifest_as_v1(
    relative: str,
) -> None:
    text = _text(relative)
    append = text.split("### Append Mode (default)", 1)[1].split(
        "### Full Mode", 1
    )[0]
    append_flat = " ".join(append.split())

    for required in (
        "Personal mode: check manifest v1",
        "Portable Repository mode: compare discovered agent/session identity and content hash against existing reviewed snapshots",
    ):
        assert required in append_flat, (
            f"{relative}: missing mode-specific append rule {required!r}"
        )
    assert "Check `.manifest.json`" not in append, (
        f"{relative}: append mode still assumes Personal manifest shape"
    )


def test_claude_history_helpers_cannot_bypass_parent_completion() -> None:
    claude = _text(".skills/claude-history-ingest/SKILL.md")
    manifest_helper = claude.split("### Append Mode", 1)[1].split(
        "### Pre-extraction", 1
    )[0]
    pre_extraction = claude.split("### Pre-extraction", 1)[1].split(
        "### Conversation Sampling Heuristic", 1
    )[0]
    for required in (
        "Personal mode only",
        "<resolved-wiki-repository-path>/scripts/manifest.py",
        "<resolved-vault-path>",
    ):
        assert required in manifest_helper, f"Claude helper missing {required!r}"
    for required in (
        "Portable Repository mode",
        "transient analysis input",
        "analysis-only",
        "parent agent",
        "source snapshot",
        "Personal mode",
    ):
        assert required in pre_extraction, (
            f"Claude pre-extraction helper missing {required!r}"
        )


def test_wiki_agent_targeted_flow_cannot_bypass_parent_completion() -> None:
    targeted = _h2_section(
        _text(".skills/wiki-agent/SKILL.md"),
        "Portable Repository completion",
        relative=".skills/wiki-agent/SKILL.md",
        next_heading="Personal mode completion",
    )
    for required in (
        "targeted session slice",
        "selected 3–5 sessions",
        "candidate `sources` cites only snapshot Source IDs",
        "Return the synthesized answer only after the selected completion branch finishes",
    ):
        assert required in targeted, f"wiki-agent targeted flow missing {required!r}"


def test_wiki_update_completion_closes_mode_and_runtime_bypasses() -> None:
    relative = ".skills/wiki-update/SKILL.md"
    text = _text(relative)
    portable = _h2_section(
        text,
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    personal = _h2_section(text, "Personal mode completion", relative=relative)
    shared = text.split("## Portable Repository completion", 1)[0]
    shared_flat = " ".join(shared.split())
    portable_flat = " ".join(portable.split())
    personal_flat = " ".join(personal.split())

    for required in (
        "Select one terminal workflow after the shared analysis and page-preparation steps",
        "parent agent owns mode resolution",
        "owner `AGENTS.md`",
    ):
        assert required in shared_flat, (
            f"{relative}: missing shared guard {required!r}"
        )
    for forbidden in (
        "suppress all direct",
        "uses manifest v2 through `obsidian-wiki cache-check` and `cache-update`",
    ):
        assert forbidden not in shared_flat, (
            f"{relative}: legacy bypass {forbidden!r}"
        )

    for required in (
        "source closure before `transaction begin`",
        "existing `sources` Source ID",
        "repository root as the command CWD",
        "do not `cd` into it",
        "created = updated = started_at",
        "preserve the existing `created`",
        "obsidian-wiki transaction delete",
        "Review every warning",
        "Fix every issue",
        "status-aware recovery",
        "allowed_actions",
        "no trusted transaction ID",
        "repository-relative Source IDs",
        "Preserve valid Unicode",
        "cache-check --configured",
    ):
        assert required in portable_flat, (
            f"{relative}: missing portable rule {required!r}"
        )
    for forbidden in (
        "$OBSIDIAN_VAULT_PATH",
        "After compiling each authoritative source, update its one shard",
    ):
        assert forbidden not in portable_flat, (
            f"{relative}: portable bypass {forbidden!r}"
        )

    for required in (
        "Write the prepared pages directly",
        "manifest v1",
        "obsidian-wiki cache-update <resolved-vault-path>",
        "<resolved-vault-path>/index.md",
        "<resolved-vault-path>/log.md",
        "<resolved-vault-path>/hot.md",
        "<resolved-qmd-cli> update",
        "qmd://<resolved-qmd-wiki-collection>/",
        "Git delta",
        "config resolution does not export these values into the parent shell",
    ):
        assert required in personal_flat, (
            f"{relative}: missing Personal rule {required!r}"
        )
    for forbidden in (
        "$OBSIDIAN_VAULT_PATH",
        "$QMD_WIKI_COLLECTION",
        "${QMD_CLI:-qmd}",
    ):
        assert forbidden not in personal_flat, (
            f"{relative}: shell assumption {forbidden!r}"
        )


def test_wiki_update_shared_phase_is_read_only_and_capture_precedes_closure() -> None:
    relative = ".skills/wiki-update/SKILL.md"
    text = _text(relative)
    shared = text.split("## Portable Repository completion", 1)[0]
    portable = _h2_section(
        text,
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    personal = _h2_section(text, "Personal mode completion", relative=relative)
    shared_flat = " ".join(shared.split())
    portable_flat = " ".join(portable.split())
    personal_flat = " ".join(personal.split())

    assert "shared analysis and page-preparation steps are strictly read-only" in shared_flat
    for forbidden in (
        "capture any necessary external material",
        "ordinary Git snapshot below `sources`",
        "small, reviewable text source snapshot",
    ):
        assert forbidden not in shared_flat, f"{relative}: shared mutation {forbidden!r}"

    for required in (
        "parent may write a small, reviewable text source snapshot",
        "below the configured `sources` root",
        "It is a source file, not a Git snapshot",
        "do not commit or publish it",
    ):
        assert required in portable_flat, (
            f"{relative}: missing Portable capture rule {required!r}"
        )
    assert portable.index("parent may write a small, reviewable text source snapshot") < (
        portable.index("Compute source closure")
    )
    assert "Personal sources follow Personal manifest v1 and cache rules" in personal_flat


def test_wiki_update_external_only_first_update_materializes_before_delta() -> None:
    relative = ".skills/wiki-update/SKILL.md"
    text = _text(relative)
    shared = text.split("## Portable Repository completion", 1)[0]
    portable = _h2_section(
        text,
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    shared_flat = " ".join(shared.split())
    portable_flat = " ".join(portable.split())

    assert "cache-check --configured" not in shared_flat
    for required in (
        "carry the selected source paths, pending source proposal, and prepared page/removal changes in memory",
        "external-only first update",
        "materialize and review it before `cache-check`",
        "A failed source-snapshot creation or review stops before any transaction",
        "Never take a no-change exit while a source proposal is pending",
        "After every selected source file exists",
    ):
        assert required in portable_flat or required in shared_flat, (
            f"{relative}: missing first-update rule {required!r}"
        )

    materialize = portable.index("materialize and review it before `cache-check`")
    cache_check = portable.index("obsidian-wiki cache-check --configured")
    closure = portable.index("Compute source closure")
    begin = portable.index("obsidian-wiki transaction begin")
    assert materialize < cache_check < closure < begin


def test_wiki_update_portable_delta_preserves_missing_cli_shapes() -> None:
    relative = ".skills/wiki-update/SKILL.md"
    portable = _h2_section(
        _text(relative),
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    portable_flat = " ".join(portable.split())

    for required in (
        "Inspect `missing` first",
        "reported `missing` entries",
        "report the exact returned values",
        "an explicitly selected absent path may be absolute",
        "tracked missing entries are repository-relative Source IDs",
        "Distinguish those shapes",
        "For an absolute selected path, correct the selection or materialize/restore that ordinary file",
        "For a tracked Source ID, restore the corresponding source or complete a supported migration",
        "do not start a transaction or mutate the live vault",
        "apply only the stated source remediation",
        "then rerun `cache-check --configured`",
        "rerun `cache-check --configured`",
        "Never treat a missing-only result as no change",
        "no `new` or `modified` entries",
        "no prepared page creations, updates, or removals",
        "Never bypass a pending source proposal",
    ):
        assert required in portable_flat, (
            f"{relative}: missing Portable delta rule {required!r}"
        )
    assert "stop without mutation or a transaction" not in portable_flat
    assert "report every exact repository-relative Source ID" not in portable_flat
    assert portable_flat.index("Inspect `missing` first") < portable_flat.index(
        "no `new` or `modified` entries"
    )


def test_wiki_update_categories_match_validator_semantic_paths() -> None:
    relative = ".skills/wiki-update/SKILL.md"
    shared = _text(relative).split("## Portable Repository completion", 1)[0]
    shared_flat = " ".join(shared.split())

    for required in (
        "`projects/<project-name>/<project-name>.md` uses `category: projects`",
        "`projects/<project-name>/concepts/` uses `category: concepts`",
        "`projects/<project-name>/skills/` uses `category: skills`",
        "`projects/<project-name>/references/` uses `category: references`",
        "Global pages use the category matching their top-level semantic directory",
        "The validator checks `category` against the page's semantic path",
        "Global `concepts/` page example",
    ):
        assert required in shared_flat, (
            f"{relative}: missing category/path rule {required!r}"
        )


def test_wiki_update_links_follow_resolved_runtime_contract() -> None:
    relative = ".skills/wiki-update/SKILL.md"
    text = _text(relative)
    shared = text.split("## Portable Repository completion", 1)[0]
    shared_flat = " ".join(shared.split())

    for required in (
        "Apply the resolved `OBSIDIAN_LINK_FORMAT`",
        "`wikilink` means Obsidian `[[wikilinks]]`",
        "`markdown` means standard Markdown links",
    ):
        assert required in shared_flat, (
            f"{relative}: missing link-format rule {required!r}"
        )
    assert "Add `[[wikilinks]]`" not in shared


def test_wiki_update_hot_waits_for_commit_or_resolved_recovery() -> None:
    relative = ".skills/wiki-update/SKILL.md"
    portable = _h2_section(
        _text(relative),
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    portable_flat = " ".join(portable.split())

    for required in (
        "after commit succeeds or recovery is fully resolved",
        "abort, restore, discard, or another allowed terminal recovery action",
        "Do not run the hot flow while a transaction outcome is ambiguous",
    ):
        assert required in portable_flat, (
            f"{relative}: missing hot recovery gate {required!r}"
        )


def test_wiki_update_personal_removals_finish_before_tracking() -> None:
    relative = ".skills/wiki-update/SKILL.md"
    personal = _h2_section(
        _text(relative), "Personal mode completion", relative=relative
    )
    personal_flat = " ".join(personal.split())

    for required in (
        "reviewed obsolete pages",
        "remove obsolete backlinks",
        "Only after every deletion and page write succeeds",
        "remove deleted pages from `pages_in_vault`",
        "remove or update source-to-page mappings",
        "remove their `index.md` entries",
        "Record deletion counts in `log.md`",
        "reflect the conceptual removal in `hot.md`",
        "Run Personal tracking and QMD only after deletions and writes succeed",
    ):
        assert required in personal_flat, (
            f"{relative}: missing Personal removal rule {required!r}"
        )


def test_portable_url_ingest_uses_repository_snapshot_and_parent_transaction() -> None:
    relative = ".skills/wiki-ingest/references/url-sources.md"
    portable = _h2_section(
        _text(relative),
        "Portable Repository URL flow",
        relative=relative,
        next_heading="Personal mode URL flow",
    )
    for required in (
        "strictly below a configured `sources` root",
        "`origin_url` metadata",
        "repository-relative Source ID",
        "one parent-owned transaction",
        "candidate knowledge pages only",
        "A live URL must never appear in `sources`",
        "`misc/` is Personal-only",
        "`sources: []` is invalid",
        "Do not write directly to the live vault, manifest, `index.md`, `log.md`, or `hot.md`",
        "Portable Repository completion",
    ):
        assert required in portable, f"{relative}: missing {required!r}"

    main = _text(".skills/wiki-ingest/SKILL.md")
    assert "Portable Repository URL flow" in main
    assert "parent-owned Portable Repository completion" in main


def test_large_folder_workers_are_analysis_only_and_parent_owns_completion() -> None:
    text = _text(".skills/wiki-ingest/SKILL.md")
    section = text.split("### Step 0: Batch Planning for Large Folders", 1)[1].split(
        "### Ingesting Git Repositories", 1
    )[0]
    for required in (
        "parent resolves config and mode",
        "owner `AGENTS.md`",
        "analysis-only workers",
        "distilled page proposals",
        "source mappings",
        "one source closure",
        "single transaction",
        "Personal central writes",
    ):
        assert required in section, f"large-folder flow missing {required!r}"
    for forbidden in (
        "Step 1 onward",
        "complete independently",
        "Ingest these files into the wiki at",
    ):
        assert forbidden not in section, f"large-folder flow contains {forbidden!r}"


def test_pageindex_reference_separates_portable_analysis_from_personal_runtime() -> None:
    relative = ".skills/wiki-ingest/references/pageindex.md"
    portable = _h2_section(
        _text(relative),
        "Portable Repository mode",
        relative=relative,
        next_heading="Personal mode",
    )
    for required in (
        "skip PageIndex",
        "analysis-only output",
        "repository-root CWD",
        "transient analysis input",
        "small, reviewable Markdown or plain-text snapshot",
        "origin URL or identifier",
        "relevant extracted text",
        "content hash when available",
        "page citations",
        "candidate `sources` cites only the snapshot's repository-relative Source ID",
        "Binary PDFs, images, and attachments are Personal-only",
        "never Portable Source IDs",
        "unsupported in Portable mode or use Personal mode",
        "does not export values into the parent shell",
        "Do not change CWD, source `.env`, or edit any manifest",
    ):
        assert required in portable, f"{relative}: missing {required!r}"
    for forbidden in (
        "authoritative PDF",
        "original repository-relative Source ID of an ordinary PDF",
    ):
        assert forbidden not in portable, f"{relative}: binary source authority {forbidden!r}"
    for pattern in (
        re.compile(r"^[ \t]*cd\s", re.MULTILINE),
        re.compile(r"^[ \t]*set -a", re.MULTILINE),
        re.compile(r"\$PAGEINDEX_(?:REPO|WORKSPACE|MODEL)"),
    ):
        assert pattern.search(portable) is None, f"{relative}: unsafe Portable command"

    main = _text(".skills/wiki-ingest/SKILL.md")
    pageindex = main.split("### Long-PDF preprocessing", 1)[1].split(
        "### Academic papers", 1
    )[0]
    assert "Portable Repository mode" in pageindex
    assert "Personal mode" in pageindex


def test_academic_attachments_are_mode_gated() -> None:
    text = _text(".skills/wiki-ingest/SKILL.md")
    academic = text.split("### Academic papers", 1)[1].split("### Step 1b", 1)[0]
    portable = academic.split("**Portable Repository mode", 1)[1].split(
        "**Personal mode", 1
    )[0]
    for required in (
        "small, reviewable Markdown or plain-text snapshot",
        "strictly below a configured repository `sources` root",
        "origin URL or identifier",
        "relevant extracted text",
        "content hash when available",
        "page citations",
        "candidate `sources` cites only the snapshot's repository-relative Source ID",
        "Binary PDFs, images, and attachments are Personal-only",
        "never Portable Source IDs",
        "unsupported in Portable mode or use Personal mode",
        "Markdown candidate pages only",
        "candidate_vault/attachments",
        "non-Markdown candidate",
    ):
        assert required in portable, f"academic Portable flow missing {required!r}"
    for forbidden in (
        "cite its repository-relative Source ID",
        "save `attachments/",
    ):
        assert forbidden not in portable


def test_personal_ingest_commands_use_concrete_resolved_values() -> None:
    main_relative = ".skills/wiki-ingest/SKILL.md"
    main = _text(main_relative)
    personal = _h2_section(main, "Personal mode completion", relative=main_relative)
    for required in (
        "<resolved-vault-path>/hot.md",
        "<resolved-qmd-cli> update",
        "qmd://<resolved-qmd-wiki-collection>/",
        "config resolution does not export these values into the parent shell",
    ):
        assert required in personal, f"{main_relative}: missing {required!r}"
    for forbidden in (
        "$OBSIDIAN_VAULT_PATH",
        "$QMD_WIKI_COLLECTION",
        "${QMD_CLI:-qmd}",
    ):
        assert forbidden not in personal, f"{main_relative}: shell assumption {forbidden!r}"

    url = _text(".skills/wiki-ingest/references/url-sources.md")
    assert "$OBSIDIAN_VAULT_PATH" not in url

    for required in (
        "<resolved-vault-path>/_raw/",
        "search `<resolved-vault-path>`",
    ):
        assert required in main, f"{main_relative}: missing {required!r}"
    for forbidden in (
        "OBSIDIAN_VAULT_PATH/_raw/",
        "search `OBSIDIAN_VAULT_PATH`",
    ):
        assert forbidden not in main, f"{main_relative}: unresolved path prose {forbidden!r}"

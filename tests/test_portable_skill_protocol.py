import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID, SOURCE_INSTALL_COMMAND
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.frontmatter import parse_frontmatter
from obsidian_wiki.transaction import (
    TransactionError,
    TransactionManager,
    validate_candidate_page,
    validate_candidate_path,
)
from obsidian_wiki.trust import validate_trust_metadata


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
BULK_HISTORY_WRITE_SKILLS = HISTORY_WRITE_SKILLS[:-1]
MAINTENANCE_WRITE_SKILLS = (
    ".skills/cross-linker/SKILL.md",
    ".skills/tag-taxonomy/SKILL.md",
    ".skills/wiki-dedup/SKILL.md",
    ".skills/wiki-lint/SKILL.md",
    ".skills/wiki-rebuild/SKILL.md",
    ".skills/wiki-status/SKILL.md",
    ".skills/wiki-synthesize/SKILL.md",
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


def _numbered_steps(section: str) -> list[tuple[int, str, str]]:
    matches = list(
        re.finditer(
            r"(?m)^(?P<number>\d+)\. \*\*(?P<title>.+?)\*\*(?P<tail>.*)$",
            section,
        )
    )
    steps: list[tuple[int, str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        body = match.group("tail") + section[match.end() : end]
        steps.append((int(match.group("number")), match.group("title"), body))
    return steps


def _fenced_block_after(text: str, label: str, language: str) -> str:
    assert label in text, f"missing fenced-example label {label!r}"
    remainder = text.split(label, 1)[1]
    opener = f"```{language}\n"
    assert opener in remainder, f"{label!r}: missing {language!r} fence"
    fenced = remainder.split(opener, 1)[1]
    assert "\n```" in fenced, f"{label!r}: unclosed fence"
    return fenced.split("\n```", 1)[0] + "\n"


def _portable_transaction(tmp_path: Path, source_ids: tuple[str, ...]):
    root = tmp_path / "knowledge"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / ".skills").mkdir()
    config_path = root / ".obsidian-wiki" / "config.toml"
    config_path.write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    (root / "wiki" / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
    )
    sources: list[Path] = []
    for source_id in source_ids:
        source = root / source_id
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"reviewed snapshot: {source_id}\n", encoding="utf-8")
        sources.append(source)
    config = load_portable_config(
        config_path,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    record = TransactionManager(config).begin(
        sources,
        transaction_id="history-contract",
        started_at="2026-08-11T12:00:00+00:00",
    )
    return root, config, record


def _write_candidate(record, relative: str, content: str) -> Path:
    canonical = validate_candidate_path(record.candidate_vault, relative)
    path = record.candidate_vault / canonical
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


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
    MAINTENANCE_WRITE_SKILLS,
    ids=lambda relative: Path(relative).parent.name,
)
def test_maintenance_family_has_safe_portable_completion(relative: str) -> None:
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
    personal_flat = " ".join(
        _h2_section(text, "Personal mode completion", relative=relative).split()
    )

    for required in (
        "Select one terminal workflow after the shared read-only analysis",
        "parent agent resolves config and mode",
        "owner `AGENTS.md`",
    ):
        assert required in shared_flat, f"{relative}: missing shared gate {required!r}"

    for required in (
        "repository root as the command CWD",
        "Compute complete source closure",
        "existing `sources` Source IDs",
        "updated or deleted live page",
        "candidate `sources` field",
        "set union",
        "never compiled vault page paths",
        "repository-relative Source IDs",
        "Preserve valid Unicode",
        "created = updated = started_at",
        "preserve the existing `created`",
        "candidate replacements or new knowledge pages",
        "transaction delete",
        "Review every warning",
        "Fix every issue",
        "status-aware recovery",
        "recovery.preferred_action",
        "recommended_action",
        "allowed_actions",
        "no trusted transaction ID",
        "outcome is ambiguous",
        "after commit succeeds or recovery is fully resolved",
        "use only those bounded inputs to write the semantic `hot.md` as the agent",
    ):
        assert required in portable_flat, (
            f"{relative}: missing Portable maintenance rule {required!r}"
        )

    assert "Use this branch only when config resolution selected Personal mode" in personal_flat
    assert "Personal central files, QMD refresh, and Git snapshot rules" in personal_flat

    special_requirements = {
        ".skills/cross-linker/SKILL.md": ("existing source closure",),
        ".skills/tag-taxonomy/SKILL.md": ("existing source closure",),
        ".skills/wiki-dedup/SKILL.md": ("existing source closure", "redirect stubs"),
        ".skills/wiki-lint/SKILL.md": (
            "existing source closure",
            "Read-only lint requires no transaction",
        ),
        ".skills/wiki-rebuild/SKILL.md": (
            "unsupported in Portable Repository mode",
            "archive, restore, or bulk clear",
        ),
        ".skills/wiki-status/SKILL.md": (
            "small, reviewable authoritative source snapshot",
            "return the insights analysis without writing",
        ),
        ".skills/wiki-synthesize/SKILL.md": (
            "union of the input pages' actual authoritative Source IDs",
            "report the synthesis opportunity without writing",
        ),
    }
    for required in special_requirements[relative]:
        assert required in portable_flat, (
            f"{relative}: missing special rule {required!r}"
        )


def test_maintenance_replacement_and_deletion_share_source_closure(
    tmp_path: Path,
) -> None:
    retained_source = "sources/维护/设计.md"
    removed_source = "sources/维护/旧方案.md"
    _, config, record = _portable_transaction(
        tmp_path, (retained_source, removed_source)
    )
    candidate = _write_candidate(
        record,
        "concepts/维护策略.md",
        f"""---
title: 维护策略
category: concepts
tags: [maintenance]
sources:
  - {retained_source}
summary: 以完整来源闭包维护候选替换与删除。
created: 2026-08-01T12:00:00+00:00
updated: {record.started_at}
---
# 维护策略
""",
    )
    manager = TransactionManager(config)
    manager.mark_delete(record.transaction_id, "concepts/旧维护策略.md")

    assert validate_candidate_page(candidate, record.source_ids) == (retained_source,)
    loaded = manager.load(record.transaction_id)
    assert loaded.source_ids == tuple(sorted((retained_source, removed_source)))
    assert loaded.deletions == ("concepts/旧维护策略.md",)
    parsed = parse_frontmatter(candidate.read_text(encoding="utf-8"))
    assert parsed.scalars["updated"] == record.started_at


def test_dedup_uses_exclusive_secondary_page_dispositions(tmp_path: Path) -> None:
    relative = ".skills/wiki-dedup/SKILL.md"
    portable = _h2_section(
        _text(relative),
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    for required in (
        "Choose exactly one disposition for each secondary path",
        "redirect stub candidate and do not declare that path for deletion",
        "declare it with `transaction delete` and do not write a candidate at that path",
        "required frontmatter",
        "non-empty `sources`",
    ):
        assert required in " ".join(portable.split()), required

    source_id = "sources/维护/去重.md"
    _, config, record = _portable_transaction(tmp_path, (source_id,))
    _write_candidate(
        record,
        "concepts/重复项.md",
        f"""---
title: 重复项重定向
category: concepts
tags: [maintenance]
sources: [{source_id}]
summary: 合法的去重重定向候选。
created: {record.started_at}
updated: {record.started_at}
---
# 重复项重定向
""",
    )
    with pytest.raises(TransactionError, match="conflicts with candidate page"):
        TransactionManager(config).mark_delete(
            record.transaction_id, "concepts/重复项.md"
        )


def test_tag_taxonomy_fails_before_partial_portable_mutation() -> None:
    relative = ".skills/tag-taxonomy/SKILL.md"
    portable = _h2_section(
        _text(relative),
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    flat = " ".join(portable.split())
    gate = flat.index("requires any `_meta/taxonomy.md` change")
    begin = flat.index("obsidian-wiki transaction begin")
    assert gate < begin
    for required in (
        "the entire logical operation is unsupported",
        "stop before `transaction begin` or any page mutation",
        "do not partially normalize pages",
    ):
        assert required in flat


def test_tag_taxonomy_audit_only_stops_at_each_completion_branch() -> None:
    relative = ".skills/tag-taxonomy/SKILL.md"
    text = _text(relative)
    portable = " ".join(
        _h2_section(
            text,
            "Portable Repository completion",
            relative=relative,
            next_heading="Personal mode completion",
        ).split()
    )
    personal = " ".join(
        _h2_section(text, "Personal mode completion", relative=relative).split()
    )
    for completion in (portable, personal):
        audit = completion.index("If the selected intent was Mode 1 audit-only")
        assert audit < completion.index("Mode 2")
        for required in (
            "return the audit report and stop",
            "no transaction",
            "no normalization",
            "no central-file mutation",
        ):
            assert required in completion
    assert portable.index("Mode 1 audit-only") < portable.index(
        "requires any `_meta/taxonomy.md` change"
    )
    assert portable.index("Mode 1 audit-only") < portable.index(
        "obsidian-wiki transaction begin"
    )


@pytest.mark.parametrize(
    ("relative", "gate"),
    (
        (
            ".skills/cross-linker/SKILL.md",
            "no EXTRACTED or INFERRED link candidate remains",
        ),
        (
            ".skills/tag-taxonomy/SKILL.md",
            "normalization produces no page changes",
        ),
        (
            ".skills/wiki-dedup/SKILL.md",
            "no approved merge produces a page creation, update, or deletion",
        ),
        (
            ".skills/wiki-lint/SKILL.md",
            "approved consolidate plan contains no page change",
        ),
        (
            ".skills/wiki-rebuild/SKILL.md",
            "supported candidate-only rebuild contains no replacement, creation, or deletion",
        ),
        (
            ".skills/wiki-synthesize/SKILL.md",
            "no synthesis page, backlink update, or deletion remains",
        ),
    ),
    ids=(
        "cross-linker",
        "tag-taxonomy",
        "wiki-dedup",
        "wiki-lint",
        "wiki-rebuild",
        "wiki-synthesize",
    ),
)
def test_maintenance_noop_stops_before_transaction(
    relative: str, gate: str
) -> None:
    portable = " ".join(
        _h2_section(
            _text(relative),
            "Portable Repository completion",
            relative=relative,
            next_heading="Personal mode completion",
        ).split()
    )
    noop = portable.index(gate)
    assert noop < portable.index("Compute complete source closure")
    assert noop < portable.index("obsidian-wiki transaction begin")
    for required in (
        "report no changes and stop",
        "do not create an empty transaction or operation journal",
        "do not refresh `hot.md`",
    ):
        assert required in portable


def test_status_portable_manifest_uses_configured_read_only_cache() -> None:
    text = _text(".skills/wiki-status/SKILL.md")
    portable_manifest = text.split(
        "### Portable Repository mode — manifest v2", 1
    )[1].split("## Step 1: Scan Current Sources", 1)[0]
    assert (
        "obsidian-wiki cache-check --configured <source1> [source2 ...] "
        "--json --pretty"
    ) in portable_manifest
    assert "transaction commit is the sole shard writer" in portable_manifest
    assert 'cache-check "$OBSIDIAN_VAULT_PATH"' not in portable_manifest
    cache_update_pattern = dict(PORTABLE_EXECUTABLE_COMMANDS)["cache-update"]
    assert cache_update_pattern.search(portable_manifest) is None


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
def test_history_portable_completion_has_structured_review_and_terminal_order(
    relative: str,
) -> None:
    portable = _h2_section(
        _text(relative),
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    steps = _numbered_steps(portable)
    expected_titles = (
        "Create or select reviewable source snapshots.",
        "Review and accept every selected snapshot.",
        "Compute complete source closure.",
        "Begin exactly once.",
        "Write candidates.",
        "Declare removals.",
        "Validate candidates.",
        "Commit the passing transaction.",
        "Use status-aware recovery.",
        "Refresh local hot context after the terminal gate.",
        "Report and stop.",
    )
    assert [number for number, _, _ in steps[:11]] == list(range(1, 12)), relative
    assert tuple(title for _, title, _ in steps[:11]) == expected_titles, relative
    bodies = {title: " ".join(body.split()) for _, title, body in steps}

    review = bodies["Review and accept every selected snapshot."]
    for required in (
        "parent agent reviews and accepts every selected snapshot",
        "rejected, incomplete, unsafe, or cannot be traced",
        "stop before `transaction begin`",
    ):
        assert required in review, f"{relative}: incomplete review gate {required!r}"

    closure = bodies["Compute complete source closure."]
    for required in (
        "`live-page sources`",
        "updated or deleted",
        "`accepted snapshots`",
        "newly created, changed existing, or unchanged and reused",
        "`candidate citations`",
        "every Source ID that any candidate `sources` field will cite",
        "set union",
    ):
        assert required in closure, f"{relative}: incomplete closure set {required!r}"

    hot = bodies["Refresh local hot context after the terminal gate."]
    assert "commit succeeds or recovery is fully resolved" in hot, relative


def test_changed_existing_snapshot_can_source_a_new_candidate(tmp_path: Path) -> None:
    live_page_source = "sources/history/codex/session-old/slice-legacy.md"
    changed_snapshot = "sources/history/codex/session-7/slice-auth.md"
    reused_snapshot = "sources/history/codex/session-7/slice-retries.md"
    new_snapshot = "sources/history/codex/session-8/slice-cache.md"
    complete_closure = (
        live_page_source,
        changed_snapshot,
        reused_snapshot,
        new_snapshot,
    )
    _, _, record = _portable_transaction(tmp_path, complete_closure)
    page = _write_candidate(
        record,
        "concepts/auth-middleware.md",
        f"""---
title: Authentication Middleware
category: concepts
tags: [authentication]
sources:
  - {changed_snapshot}
summary: Authentication middleware knowledge compiled from an accepted changed snapshot.
provenance:
  extracted: 0.80
  inferred: 0.20
  ambiguous: 0.00
base_confidence: 0.42
lifecycle: draft
lifecycle_changed: 2026-08-11
created: 2026-08-11T12:00:00+00:00
updated: 2026-08-11T12:00:00+00:00
---
# Authentication Middleware
""",
    )

    incomplete = tuple(
        source_id for source_id in record.source_ids if source_id != changed_snapshot
    )
    with pytest.raises(TransactionError, match="source outside the transaction"):
        validate_candidate_page(page, incomplete)
    assert validate_candidate_page(page, record.source_ids) == (changed_snapshot,)
    assert validate_trust_metadata(page)["lifecycle"] == "draft"


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
    personal_marker = "**Personal mode delta:**"
    portable_marker = "**Portable Repository mode delta:**"
    selected_marker = "**After mode-specific delta selection:**"
    assert personal_marker in append, f"{relative}: missing Personal append delta"
    assert portable_marker in append, f"{relative}: missing Portable append delta"
    assert selected_marker in append, f"{relative}: missing gated append handoff"
    personal = append.split(personal_marker, 1)[1].split(portable_marker, 1)[0]
    portable = append.split(portable_marker, 1)[1].split(selected_marker, 1)[0]
    personal_flat = " ".join(personal.split())
    portable_flat = " ".join(portable.split())

    for required in ("manifest v1", "`ingested_at`"):
        assert required in personal_flat, (
            f"{relative}: Personal append delta missing {required!r}"
        )
    for required in (
        "configured `sources`",
        "reviewed snapshot",
        "agent/session identity",
        "recorded content hash",
    ):
        assert required in portable_flat, (
            f"{relative}: Portable append delta missing {required!r}"
        )
    for forbidden in (
        "manifest v1",
        ".manifest.json",
        "`ingested_at`",
        "file mtime",
        "modification time",
    ):
        assert forbidden not in portable_flat, (
            f"{relative}: Portable append delta uses Personal rule {forbidden!r}"
        )
    assert "In either mode" not in append, (
        f"{relative}: append mode falls through to a shared Personal delta rule"
    )


@pytest.mark.parametrize(
    "relative",
    HISTORY_WRITE_SKILLS[:-1],
    ids=lambda relative: Path(relative).parent.name,
)
def test_bulk_history_survey_has_separate_personal_and_portable_delta_rules(
    relative: str,
) -> None:
    text = _text(relative)
    survey = text.split("## Step 1: Survey and Compute Delta", 1)[1].split(
        "## Step 2:", 1
    )[0]
    personal_marker = "**Personal mode survey:**"
    portable_marker = "**Portable Repository mode survey:**"
    assert personal_marker in survey, f"{relative}: missing Personal survey"
    assert portable_marker in survey, f"{relative}: missing Portable survey"
    personal = survey.split(personal_marker, 1)[1].split(portable_marker, 1)[0]
    portable = survey.split(portable_marker, 1)[1]
    personal_flat = " ".join(personal.split())
    portable_flat = " ".join(portable.split())

    for required in ("manifest v1", "`ingested_at`"):
        assert required in personal_flat, (
            f"{relative}: Personal survey missing {required!r}"
        )
    for required in (
        "configured `sources`",
        "reviewed snapshot",
        "agent/session identity",
        "recorded content hash",
    ):
        assert required in portable_flat, (
            f"{relative}: Portable survey missing {required!r}"
        )
    for forbidden in (
        "manifest v1",
        ".manifest.json",
        "`ingested_at`",
        "file mtime",
        "modification time",
    ):
        assert forbidden not in portable_flat, (
            f"{relative}: Portable survey uses Personal rule {forbidden!r}"
        )


@pytest.mark.parametrize(
    "relative",
    HISTORY_WRITE_SKILLS[:-1],
    ids=lambda relative: Path(relative).parent.name,
)
def test_bulk_history_portable_completion_excludes_personal_delta_state(
    relative: str,
) -> None:
    portable = _h2_section(
        _text(relative),
        "Portable Repository completion",
        relative=relative,
        next_heading="Personal mode completion",
    )
    for forbidden in (
        ".manifest.json",
        "`ingested_at`",
        "file mtime",
        "modification time",
    ):
        assert forbidden not in portable, (
            f"{relative}: Portable completion uses Personal delta state {forbidden!r}"
        )


@pytest.mark.parametrize(
    "relative",
    BULK_HISTORY_WRITE_SKILLS,
    ids=lambda relative: Path(relative).parent.name,
)
def test_bulk_history_project_overviews_follow_portable_semantic_paths(
    relative: str,
) -> None:
    shared = _text(relative).split("## Portable Repository completion", 1)[0]
    shared_flat = " ".join(shared.split())
    for required in (
        "`projects/<name>/<name>.md` uses `category: projects`",
        "Portable Repository mode omits `source_path`",
        "machine-local or absolute path",
        "accepted snapshot Source IDs",
        "Personal manifest v1 may retain the concrete absolute history path",
    ):
        assert required in shared_flat, (
            f"{relative}: project overview rule missing {required!r}"
        )


def test_canonical_project_overview_example_passes_real_validators(
    tmp_path: Path,
) -> None:
    example = _fenced_block_after(
        _text(".skills/llm-wiki/SKILL.md"),
        "Each project directory has an overview page structured like this:",
        "markdown",
    )
    parsed = parse_frontmatter(example)
    assert parsed.scalars["category"] == "projects"
    assert "source_path" not in parsed.fields
    assert parsed.scalars["summary"]
    assert parsed.provenance is not None
    assert parsed.scalars["lifecycle"] == "draft"
    date.fromisoformat(parsed.scalars["lifecycle_changed"])
    source_ids = parsed.lists["sources"]

    _, _, record = _portable_transaction(tmp_path, source_ids)
    candidate = _write_candidate(
        record,
        "projects/my-project/my-project.md",
        example,
    )
    assert validate_candidate_page(candidate, record.source_ids) == tuple(
        sorted(source_ids)
    )
    trust = validate_trust_metadata(candidate)
    assert trust["confidence"] is not None
    assert trust["lifecycle"] == "draft"


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


def test_wiki_agent_candidate_category_matches_its_semantic_path() -> None:
    text = _text(".skills/wiki-agent/SKILL.md")
    candidate = text.split("## Step 5: Distill Blobs into Wiki Pages", 1)[1].split(
        "## Step 6:", 1
    )[0]
    candidate_flat = " ".join(candidate.split())

    assert "category: skill|concept|entity|synthesis" not in candidate_flat
    for required in (
        "`skills/<slug>.md` → `category: skills`",
        "`concepts/<slug>.md` → `category: concepts`",
        "`entities/<slug>.md` → `category: entities`",
        "`synthesis/<slug>.md` → `category: synthesis`",
        "category: concepts",
        "The candidate path and `category` must use one matching pair",
        "The validator checks this semantic path/category match",
    ):
        assert required in candidate_flat, (
            f"wiki-agent candidate guidance missing {required!r}"
        )


def test_wiki_agent_candidate_example_passes_real_validators(tmp_path: Path) -> None:
    example = _fenced_block_after(
        _text(".skills/wiki-agent/SKILL.md"),
        "### Valid Portable concept candidate example",
        "markdown",
    )
    parsed = parse_frontmatter(example)
    assert parsed.scalars["category"] == "concepts"
    assert parsed.scalars["summary"]
    assert parsed.provenance is not None
    assert parsed.scalars["base_confidence"] == "0.42"
    assert parsed.scalars["lifecycle"] == "draft"
    date.fromisoformat(parsed.scalars["lifecycle_changed"])
    assert "confidence" not in parsed.fields
    source_ids = parsed.lists["sources"]

    _, _, record = _portable_transaction(tmp_path, source_ids)
    candidate = _write_candidate(record, "concepts/auth-middleware.md", example)
    assert validate_candidate_page(candidate, record.source_ids) == tuple(
        sorted(source_ids)
    )
    trust = validate_trust_metadata(candidate)
    assert trust["confidence"] == 0.42
    assert trust["lifecycle"] == "draft"

    invalid = candidate.with_name("invalid-lifecycle.md")
    invalid.write_text(
        example.replace("lifecycle: draft", "lifecycle: stable"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid lifecycle: stable"):
        validate_trust_metadata(invalid)


def test_wiki_agent_slice_identity_retains_two_queries_for_one_session(
    tmp_path: Path,
) -> None:
    raw = _fenced_block_after(
        _text(".skills/wiki-agent/SKILL.md"),
        "### Stable targeted slice identity example",
        "json",
    )
    example = json.loads(raw)
    assert example["agent"] == example["agent"].lower()
    assert not Path(example["session"]).is_absolute()
    slices = example["slices"]
    assert len(slices) == 2
    assert slices[0]["query"] != slices[1]["query"]

    source_ids: list[str] = []
    for item in slices:
        normalized = " ".join(item["query"].split()).lower()
        assert item["normalized_query"] == normalized
        assert item["anchors"] == sorted(item["anchors"])
        payload = normalized + "\n" + "\n".join(sorted(item["anchors"]))
        expected_slice = "q-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
        assert item["slice"] == expected_slice
        expected_source = (
            f"sources/history/{example['agent']}/{example['session']}/"
            f"{expected_slice}.md"
        )
        assert item["source_id"] == expected_source
        assert not Path(item["source_id"]).is_absolute()
        source_ids.append(item["source_id"])
    assert len(set(source_ids)) == 2

    _, _, record = _portable_transaction(tmp_path, tuple(source_ids))
    candidate = _write_candidate(
        record,
        "synthesis/session-slices.md",
        """---
title: Session Slice Comparison
category: synthesis
tags: [cross-agent]
sources:
"""
        + "".join(f"  - {source_id}\n" for source_id in source_ids)
        + """summary: Two distinct query slices from one session remain independently reproducible.
provenance:
  extracted: 0.50
  inferred: 0.50
  ambiguous: 0.00
base_confidence: 0.42
lifecycle: draft
lifecycle_changed: 2026-08-11
created: 2026-08-11T12:00:00+00:00
updated: 2026-08-11T12:00:00+00:00
---
# Session Slice Comparison
""",
    )
    assert validate_candidate_page(candidate, record.source_ids) == tuple(
        sorted(source_ids)
    )


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

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

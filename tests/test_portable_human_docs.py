import re
import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _flat_text(text: str) -> str:
    return " ".join(text.split())


def _headings(text: str) -> list[tuple[int, str, int, int]]:
    headings: list[tuple[int, str, int, int]] = []
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
                r"^(?P<marks>#{2,3})[ \t]+(?P<title>[^\r\n]+?)[ \t]*$",
                content,
            )
            if heading_match is not None:
                headings.append(
                    (
                        len(heading_match.group("marks")),
                        heading_match.group("title"),
                        offset,
                        offset + len(line),
                    )
                )
        offset += len(line)
    return headings


def _section(relative: str, level: int, title: str) -> str:
    text = _text(relative)
    headings = _headings(text)
    matches = [item for item in headings if item[:2] == (level, title)]
    assert len(matches) == 1, (
        f"{relative}: expected one H{level} {title!r}, found {len(matches)}"
    )
    selected = matches[0]
    position = headings.index(selected)
    end = next(
        (
            heading[2]
            for heading in headings[position + 1 :]
            if heading[0] <= level
        ),
        len(text),
    )
    return text[selected[3] : end]


def _fenced_blocks(text: str, language: str) -> list[str]:
    blocks: list[str] = []
    marker: tuple[str, int] | None = None
    selected = False
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        fence_match = re.match(
            r"^[ \t]{0,3}(?P<marker>`{3,}|~{3,})(?P<tail>.*)$", content
        )
        if marker is None:
            if fence_match is None:
                continue
            opening = fence_match.group("marker")
            marker = (opening[0], len(opening))
            info = fence_match.group("tail").strip().split(None, 1)
            selected = bool(info and info[0] == language)
            lines = []
            continue

        if fence_match is not None:
            closing = fence_match.group("marker")
            if (
                closing[0] == marker[0]
                and len(closing) >= marker[1]
                and not fence_match.group("tail").strip()
            ):
                if selected:
                    blocks.append("".join(lines))
                marker = None
                selected = False
                lines = []
                continue
        if selected:
            lines.append(line)
    assert marker is None, "unclosed Markdown fence"
    return blocks


def _bash_commands(section: str) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    for block in _fenced_blocks(section, "bash"):
        pending = ""
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            pending += stripped
            if pending.endswith("\\"):
                pending = pending[:-1] + " "
                continue
            commands.append((pending, shlex.split(pending, comments=True)))
            pending = ""
        assert not pending, "unfinished shell continuation"
    return commands


def _command(
    commands: list[tuple[str, list[str]]], prefix: tuple[str, ...]
) -> tuple[str, list[str]]:
    matches = [item for item in commands if tuple(item[1][: len(prefix)]) == prefix]
    assert matches, f"missing command prefix: {' '.join(prefix)}"
    return matches[0]


def test_section_parser_ignores_headings_inside_fences(tmp_path: Path) -> None:
    relative = tmp_path / "synthetic.md"
    relative.write_text(
        "## Target\n```markdown\n## Not a boundary\n```\nbody\n## Next\n",
        encoding="utf-8",
    )
    text = relative.read_text(encoding="utf-8")
    headings = _headings(text)
    assert [(level, title) for level, title, _start, _end in headings] == [
        (2, "Target"),
        (2, "Next"),
    ]


def test_cli_transaction_section_documents_preflight_contract() -> None:
    section = _section("docs/cli.md", 2, "Portable transactions and local hot state")
    flat = _flat_text(section)
    commands = _bash_commands(section)
    _command(commands, ("obsidian-wiki", "transaction", "begin"))
    _command(commands, ("obsidian-wiki", "transaction", "validate"))
    _command(commands, ("obsidian-wiki", "transaction", "commit"))

    begin_fields = re.search(
        r"JSON result includes at least (?P<fields>.*?)\.", flat
    )
    assert begin_fields is not None
    documented = set(re.findall(r"`([^`]+)`", begin_fields.group("fields")))
    assert {
        "transaction_id",
        "status",
        "started_at",
        "source_ids",
        "workspace",
        "candidate_vault",
        "snapshots",
        "deletions",
    } <= documented

    report_fields = re.search(r"JSON report contains (?P<fields>.*?)\.", flat)
    assert report_fields is not None
    documented = set(re.findall(r"`([^`]+)`", report_fields.group("fields")))
    assert documented == {
        "transaction_id",
        "status",
        "candidate_pages",
        "deletions",
        "issues",
        "warnings",
    }
    for required in (
        "Exit status is `0` for `pass` and `1` for `fail`",
        "prospective vault = (live knowledge pages - declared deletions) + "
        "candidate replacements",
        "non-empty subset of the transaction's `source_ids`",
        "before any recovery snapshot or live-vault promotion",
    ):
        assert required in flat


def test_cli_lower_level_section_documents_cache_contract() -> None:
    section = _section("docs/cli.md", 2, "Lower-level commands")
    flat = _flat_text(section)
    commands = _bash_commands(section)
    configured = _command(commands, ("obsidian-wiki", "cache-check", "--configured"))
    assert configured[1][-2:] == ["--json", "--pretty"]
    legacy = [
        command
        for command in commands
        if command[1][:2] == ["obsidian-wiki", "cache-check"]
        and "--configured" not in command[1]
    ]
    assert legacy and legacy[0][1][-2:] == ["--json", "--pretty"]
    _command(commands, ("obsidian-wiki", "cache-update"))
    _command(commands, ("obsidian-wiki", "cache-hash"))
    for required in (
        "Cache output is JSON by default",
        "`context_warnings`",
        "structured JSON stdout remains parseable and stderr stays empty",
        "`cache-update` is a low-level compatibility interface",
        "It is not a Portable transaction completion step",
    ):
        assert required in flat


def test_portable_manifest_sections_route_drift_through_transactions() -> None:
    configuration = _flat_text(
        _section("docs/configuration.md", 2, "Manifest mode selected by configuration")
    )
    assert (
        "Use `obsidian-wiki cache-check` and `cache-update` for v2 state"
        not in configuration
    )
    for required in (
        "Use `obsidian-wiki cache-check --configured` for Portable v2 freshness",
        "compile or recompile candidate pages through a transaction",
        "transaction commit owns the affected manifest shards",
        "not a Portable transaction completion step",
    ):
        assert required in configuration

    section = _section("docs/cli.md", 2, "Portable repository validation")
    flat = _flat_text(section)
    commands = _bash_commands(section)
    raw_begin, begin = _command(commands, ("obsidian-wiki", "transaction", "begin"))
    assert "--source <source1> [source2 ...]" in raw_begin
    assert begin[-2:] == ["--json", "--pretty"]
    _command(commands, ("obsidian-wiki", "transaction", "validate"))
    _command(commands, ("obsidian-wiki", "transaction", "commit"))
    _command(commands, ("obsidian-wiki", "check"))
    for required in (
        "For `source-new` or `source-stale`, compile or recompile the source "
        "through a transaction",
        "every Source ID cited by a candidate",
        "every existing source cited by a page being updated or deleted",
        "After commit, rerun `obsidian-wiki check`",
        "git rm <vault>/.manifest/sources/<relative>.json",
        "whole-file Git deletion",
    ):
        assert required in flat


def test_cli_transaction_section_documents_hot_inputs_contract() -> None:
    section = _section("docs/cli.md", 2, "Portable transactions and local hot state")
    flat = _flat_text(section)
    commands = _bash_commands(section)
    _command(commands, ("obsidian-wiki", "hot", "status"))
    _raw, inputs = _command(commands, ("obsidian-wiki", "hot", "inputs"))
    assert inputs[inputs.index("--pages") + 1] == "50"
    assert inputs[inputs.index("--operations") + 1] == "10"
    assert inputs[-2:] == ["--json", "--pretty"]
    _command(commands, ("obsidian-wiki", "hot", "mark-current"))
    for required in (
        "read-only",
        "`fingerprint`, `pages`, and `operations`",
        "Each validated immutable operation record",
    ):
        assert required in flat


def test_architecture_portable_lifecycle_places_validation_before_snapshots() -> None:
    flat = _flat_text(_section("docs/architecture.md", 3, "Portable write lifecycle"))
    for required in (
        "prospective vault = (live knowledge pages - declared deletions) + "
        "candidate replacements",
        "candidate-to-candidate",
        "unchanged live pages",
        "before recovery snapshots or promotion",
        "reviewed candidate bytes are the bytes promoted",
    ):
        assert required in flat


def test_configuration_sections_document_runtime_and_version_contracts() -> None:
    resolution = _flat_text(_section("docs/configuration.md", 2, "How config is resolved"))
    for required in (
        "does not export `OBSIDIAN_VAULT_PATH` into the parent shell",
        "config-aware command such as `cache-check --configured`",
    ):
        assert required in resolution

    portable = _flat_text(
        _section("docs/configuration.md", 2, "Portable Repository configuration")
    )
    for required in (
        "release-tag-based compatible PEP 440 range",
        "Exact development-build pins",
        "exact CLI/source-revision compatibility or reproducibility",
        "high-churn",
        "`setup-version-stale`",
        "independent from Portable `requires_cli` compatibility",
    ):
        assert required in portable
    assert "byte-for-byte reproducibility" not in portable


def test_agents_portable_protocol_documents_cwd_timestamps_and_recovery() -> None:
    flat = _flat_text(_section("docs/agents.md", 2, "Portable agent write protocol"))
    for required in (
        "Keep the repository root as the command working directory",
        "do not `cd` into it",
        "runtime-only absolute path",
        "`created = updated = started_at`",
        "preserve `created` and set `updated = started_at`",
        "explicit adjacent Portable Repository completion and Personal mode completion branches",
        "validate before commit",
        "hot status` → `hot inputs` → semantic rewrite → `hot mark-current",
    ):
        assert required in flat


def test_skills_portable_contract_is_mode_local() -> None:
    flat = _flat_text(_section("docs/skills.md", 2, "Portable write-skill contract"))
    for required in (
        "explicit adjacent Portable Repository completion and Personal mode completion branches",
        "repository root as CWD",
        "runtime-only absolute `candidate_vault`",
        "transaction `started_at`",
        "status-aware recovery",
        "hot freshness gate",
    ):
        assert required in flat

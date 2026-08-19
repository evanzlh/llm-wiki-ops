import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import cli, portable
from obsidian_wiki.skill_trees import discover_skill_collection
from tools import capture_legacy_skill_digests as capture_tool


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "obsidian_wiki/_data"
DISCOVERY_DIRS = tuple(relative for relative, _label in cli.PROJECT_AGENT_DIRS)
BOOTSTRAP_TARGETS = (
    "CLAUDE.md",
    "GEMINI.md",
    ".hermes.md",
    ".agent/rules/llmwikiops.md",
    ".agent/workflows/llmwikiops.md",
    ".cursor/rules/llmwikiops.mdc",
    ".windsurf/rules/llmwikiops.md",
    ".kiro/steering/llmwikiops.md",
    ".github/copilot-instructions.md",
)
CATALOG = DATA / "legacy-skill-digests-v1.json"
CAPTURE_TOOL = ROOT / "tools/capture_legacy_skill_digests.py"
ADAPTER_TEMPLATE = DATA / "adapter/SKILL.md.in"
ADAPTER_BOOTSTRAP_GATE_END = "<!-- LLMWIKIOPS_ADAPTER_BOOTSTRAP_GATE_END -->"
ADAPTER_EOF = "<!-- LLMWIKIOPS_ADAPTER_EOF -->"
RUNTIME_BOOTSTRAPS = tuple(DATA / relative for relative in (
    "bootstrap/AGENTS.md",
    "bootstrap/agent/rules/llmwikiops.md",
    "bootstrap/agent/workflows/llmwikiops.md",
    "bootstrap/cursor/rules/llmwikiops.mdc",
    "bootstrap/github/copilot-instructions.md",
    "bootstrap/kiro/steering/llmwikiops.md",
    "bootstrap/windsurf/rules/llmwikiops.md",
))
AUTHORITY_ORDER = """Read authority in this exact order:

1. `<root>/AGENTS.md`
2. `<root>/.skills/llm-wiki/SKILL.md`
3. `<vault>/AGENTS.md` when present
4. `<root>/.skills/<selected-task>/SKILL.md`"""
IMMUTABLE_BINDING = "Once validated, keep `<root>` immutable for the whole workflow."
METADATA_REROUTE = (
    "Target repository metadata overrides the adapter's generated snapshot and "
    "forces route reevaluation."
)


def test_runtime_bootstraps_share_exact_authority_and_binding_protocol() -> None:
    assert len(RUNTIME_BOOTSTRAPS) == 7
    for path in RUNTIME_BOOTSTRAPS:
        contents = path.read_text(encoding="utf-8")
        assert contents.count(AUTHORITY_ORDER) == 1, path
        assert contents.count(IMMUTABLE_BINDING) == 1, path
        assert contents.count(METADATA_REROUTE) == 1, path


def _write_skill(source: Path, body: str = "# Demo\n") -> None:
    skill = source / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\n" + body,
        encoding="utf-8",
    )


def _capture(source: Path, label: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CAPTURE_TOOL),
            "--source",
            str(source),
            "--label",
            label,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_framework_root_has_no_wiki_skill_discovery_tree() -> None:
    assert not (ROOT / ".skills").exists()
    assert [relative for relative in DISCOVERY_DIRS if (ROOT / relative).exists()] == []


def test_package_data_is_the_only_runtime_asset_source() -> None:
    assert cli.skills_dir() == DATA / "skills"
    assert cli.bootstrap_dir() == DATA / "bootstrap"
    assert (DATA / "skills/llm-wiki/SKILL.md").is_file()
    assert (DATA / "bootstrap/AGENTS.md").is_file()


def test_external_adapter_remains_a_packaged_template_not_a_discoverable_skill() -> None:
    assert ADAPTER_TEMPLATE.is_file()
    assert ADAPTER_TEMPLATE.name == "SKILL.md.in"
    assert not (ADAPTER_TEMPLATE.parent / "SKILL.md").exists()
    assert ADAPTER_TEMPLATE.parent.parent == DATA
    for relative in DISCOVERY_DIRS:
        assert not (ROOT / relative / "llm-wiki-ops").exists()


def test_external_adapter_preflights_before_ordinary_repository_reads() -> None:
    template = ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    assert "<wiki-cli> info --json" in template
    info = template.index("<wiki-cli> info --json")
    check = template.index("<wiki-cli> check", info)
    ordinary = template.index("ordinary bounded file tools", check)

    assert info < check < ordinary
    assert "On either failure, stop before ordinary repository reads" in template
    assert "do not search for a different root" in template
    assert "Keep the current business working directory unchanged" in template


def test_external_adapter_catalog_enumeration_is_direct_and_nonrecursive() -> None:
    template = ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    template_text = " ".join(template.split())

    assert "Never run an unbounded or recursive find or search" in template_text
    assert "List the configured skills directory at exactly one level" in template_text
    assert "only each direct child and its direct `SKILL.md`" in template_text


def test_external_adapter_preflight_commands_are_strictly_serialized() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    assert (
        "Do not launch `info` and `check` concurrently or speculatively"
        in template_text
    )
    assert "The `info` process must complete successfully" in template_text
    assert "`runtime.status` is `resolved`" in template_text
    assert "Only then start `check`" in template_text
    assert "complete successfully before any ordinary external read" in template_text


def test_external_adapter_finishes_catalog_reroute_before_any_authority_body() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    assert "frontmatter for every direct skill" in template_text
    assert "finish the metadata merge and rerun routing" in template_text
    assert "before reading any external authority or task body" in template_text
    assert "root or vault `AGENTS.md`" in template_text


def test_external_adapter_allows_one_query_operation_per_user_request() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    assert "For one user query request, execute exactly one selected" in template_text
    assert "`query-language/v1` CLI operation" in template_text
    assert "If it returns no match, report that result" in template_text
    assert "do not retry with another term or mode" in template_text


def test_external_adapter_uses_preflight_only_bounded_read_boundary() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    for required in (
        "After successful preflight, use ordinary bounded file tools",
        "read routing frontmatter within 64 KiB",
        "limit each complete external file read to 1 MiB",
        "consumption limits, not a per-read metadata or TOCTOU protocol",
        "If relevant repository evidence changes after preflight",
    ):
        assert required in template_text

    for forbidden in (
        "os.lstat",
        "stat.S_ISREG",
        "os.path.isfile",
        "Path.is_file",
        "same process immediately before reading",
        "A prior command's check never authorizes a later read",
        "hash-only reads",
    ):
        assert forbidden not in template_text


def test_external_adapter_frontmatter_reads_are_bounded_to_64_kib() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    assert "read at most 64 KiB" in template_text
    assert "Never use `Path.read_text()`, `cat`, `sed`, or an equivalent" in template_text
    assert "whole or unbounded read during the frontmatter phase" in template_text


def test_external_adapter_authority_bodies_are_one_synchronous_sequence() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    assert "one synchronous authority sequence" in template_text
    assert "complete each bounded read before opening the next file" in template_text
    root_agents = template_text.index("root `AGENTS.md` if present")
    canonical = template_text.index("direct canonical `llm-wiki/SKILL.md`", root_agents)
    vault_agents = template_text.index("configured vault `AGENTS.md` if present", canonical)
    selected = template_text.index("direct selected task `SKILL.md`", vault_agents)
    assert root_agents < canonical < vault_agents < selected


def test_external_adapter_authority_bodies_use_distinct_sequential_processes() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    assert "one distinct sequential tool call or process per authority body" in template_text
    assert "open exactly one authority body in that call or process" in template_text
    assert "Never loop over or combine multiple authority bodies" in template_text
    assert "Skip an absent optional authority file" in template_text
    assert "If the selected task is `llm-wiki`, read it once" in template_text
    assert "load that delegated task body afterward in its own distinct bounded call" in template_text
    assert "before executing its operation" in template_text


def test_external_adapter_canonical_body_uses_verified_configured_skills_path() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    assert "`<configured-skills-path>/llm-wiki/SKILL.md`" in template_text
    assert "derived from verified info, config, and catalog evidence" in template_text
    assert "Never invent `<root>/llm-wiki/SKILL.md`" in template_text
    assert "or another conventional canonical path" in template_text


def test_external_adapter_enforces_recovery_review_and_hot_write_gates() -> None:
    template_text = " ".join(ADAPTER_TEMPLATE.read_text(encoding="utf-8").split())

    assert "bounded-inspect every reported candidate" in template_text
    assert "A status or validation envelope alone is not review" in template_text
    assert "Reading existing `hot.md` is not regeneration" in template_text
    assert "never call `hot mark-current` after a read-only or no-write path" in template_text


def test_external_adapter_front_loads_a_complete_read_gate_before_authority() -> None:
    template = ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    body = template.split("---\n", 2)[2]
    title_end = body.index("\n", body.index("# External LLMWikiOps repository adapter"))
    gate_end = body.index(ADAPTER_BOOTSTRAP_GATE_END)

    assert body[title_end:].lstrip().startswith("## Bootstrap gate — read to EOF first")
    assert template.count(ADAPTER_BOOTSTRAP_GATE_END) == 1
    assert gate_end < body.index("Operate on an external LLMWikiOps repository")
    assert gate_end < body.index("## Authority and routing")
    assert template.count(ADAPTER_EOF) == 1
    assert template.endswith(ADAPTER_EOF + "\n")
    assert ADAPTER_EOF not in template[: -len(ADAPTER_EOF + "\n")]


def test_external_adapter_partial_read_grants_no_repository_authorization() -> None:
    template = ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    gate = template.split("# External LLMWikiOps repository adapter", 1)[1].split(
        ADAPTER_BOOTSTRAP_GATE_END, 1
    )[0]

    for required in (
        (
            "Before any external-repository tool call, file read, directory listing, "
            "search, or command"
        ),
        "MUST load this adapter's `SKILL.md` completely through EOF",
        "one complete, non-range read through EOF",
        "A partial or range read with `sed`, `head`, `tail`, or an equivalent tool",
        "even when its requested range includes the whole file or terminal EOF marker",
        "grants no authorization to access the external repository",
        "next action MUST continue reading this adapter",
        "Do not access the external repository until the terminal EOF marker",
    ):
        assert required in gate


def test_runtime_asset_lookup_has_no_checkout_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "checkout" / "obsidian_wiki"
    package.mkdir(parents=True)
    (package.parent / ".skills").mkdir()
    (package.parent / "AGENTS.md").write_text("checkout fallback\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_pkg_dir", lambda: package)

    with pytest.raises(FileNotFoundError, match="bundled skills.*Reinstall"):
        cli.skills_dir()
    with pytest.raises(FileNotFoundError, match="bundled bootstrap.*Reinstall"):
        cli.bootstrap_dir()


def test_packaged_bootstrap_resource_must_be_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "obsidian_wiki"
    data = package / "_data"
    data.mkdir(parents=True)
    (data / "bootstrap").write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_pkg_dir", lambda: package)

    with pytest.raises(FileNotFoundError, match="bundled bootstrap.*Reinstall"):
        cli.bootstrap_dir()


def test_framework_bootstraps_are_ordinary_development_pointers() -> None:
    for relative in BOOTSTRAP_TARGETS:
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink(), relative
        text = path.read_text(encoding="utf-8")
        assert "AGENTS.md" in text, relative
        assert "Config Resolution Protocol" not in text, relative
        assert "Skill Routing" not in text, relative

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "LLMWikiOps — Framework Development" in agents
    assert "source of the `LLMWikiOps` framework" in agents
    assert "not an initialized\nwiki repository" in agents
    assert "Do not resolve a vault" in agents
    assert "obsidian_wiki/_data/skills" in agents
    assert "obsidian_wiki/_data/bootstrap" in agents
    runtime = (DATA / "bootstrap/AGENTS.md").read_text(encoding="utf-8")
    assert "Config Resolution Protocol" in runtime


def test_framework_development_commands_provision_pytest_explicitly() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert (
        "uv run --with pytest python -m pytest tests/test_portable_setup.py -q"
        in agents
    )
    assert (
        "PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q "
        "-p no:cacheprovider"
    ) in agents


def test_automatic_stop_capture_surface_is_absent() -> None:
    hook_name = "wiki-stop-" + "capture.sh"
    packaged_hook_reference = hook_name[:-3]
    session_hook_reference = "session-end Stop " + "hook"
    assert not (ROOT / ".claude/settings.json").exists()
    assert not (ROOT / ".claude/hooks" / hook_name).exists()
    assert not (DATA / "hooks" / hook_name).exists()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    setup = (DATA / "skills/wiki-setup/SKILL.md").read_text(encoding="utf-8")
    capture = (DATA / "skills/wiki-capture/SKILL.md").read_text(encoding="utf-8")
    for text in (pyproject, setup, capture):
        assert packaged_hook_reference not in text
        assert session_hook_reference not in text


def test_explicit_quick_capture_remains_documented() -> None:
    capture = (DATA / "skills/wiki-capture/SKILL.md").read_text(encoding="utf-8")
    assert "/wiki-capture --quick" in capture
    assert "Quick mode" in capture


def test_legacy_digest_capture_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    _write_skill(source)

    first = _capture(source, "baseline", output)
    assert first.returncode == 0, first.stderr
    expected = output.read_bytes()
    assert expected.endswith(b"\n")
    assert output.read_text(encoding="utf-8") == json.dumps(
        {
            "schema_version": 1,
            "collections": [
                {
                    "label": "baseline",
                    "skills": {
                        "demo": discover_skill_collection(
                            source, ignore_source_artifacts=True
                        ).skills[0].digest
                    },
                }
            ],
        },
        indent=2,
        sort_keys=True,
    ) + "\n"

    second = _capture(source, "baseline", output)
    assert second.returncode == 0, second.stderr
    assert output.read_bytes() == expected


def test_runtime_legacy_digest_catalog_loader_matches_captured_package_data() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))

    collections = portable._load_legacy_skill_digest_catalog()

    assert [dict(collection) for collection in collections] == [
        item["skills"] for item in payload["collections"]
    ]
    with pytest.raises(TypeError):
        collections[0]["wiki-ingest"] = "sha256:" + "0" * 64  # type: ignore[index]


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema_version":1,"schema_version":1,"collections":[]}',
        '{"schema_version":2,"collections":[]}',
        '{"schema_version":1,"collections":[{"label":"x","skills":{}}]}',
    ],
)
def test_runtime_legacy_digest_catalog_loader_fails_closed(
    payload: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = tmp_path / "legacy.json"
    catalog.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(portable, "_LEGACY_SKILL_DIGEST_CATALOG", catalog)

    with pytest.raises(ValueError, match="legacy skill digest catalog"):
        portable._load_legacy_skill_digest_catalog()


def test_legacy_digest_capture_rejects_invalid_label_and_changed_overwrite(
    tmp_path: Path,
) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    assert _capture(source, "baseline", output).returncode == 0
    original = output.read_bytes()

    (source / "demo/SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\n# Changed\n",
        encoding="utf-8",
    )
    changed = _capture(source, "baseline", output)
    assert changed.returncode != 0
    assert output.read_bytes() == original

    empty = _capture(source, "   ", tmp_path / "empty.json")
    assert empty.returncode != 0
    assert not (tmp_path / "empty.json").exists()


@pytest.mark.parametrize("target_exists", [False, True], ids=["dangling", "live"])
def test_legacy_digest_capture_rejects_output_symlink(
    tmp_path: Path, target_exists: bool
) -> None:
    source = tmp_path / "skills"
    expected = tmp_path / "expected.json"
    target = tmp_path / "target.json"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    assert _capture(source, "baseline", expected).returncode == 0
    if target_exists:
        target.write_bytes(expected.read_bytes())
    try:
        output.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))

    result = _capture(source, "baseline", output)

    assert result.returncode != 0
    assert "ordinary single-link regular file" in result.stderr
    assert output.is_symlink()
    if target_exists:
        assert target.read_bytes() == expected.read_bytes()
    else:
        assert not target.exists()


def test_legacy_digest_capture_rejects_output_directory(tmp_path: Path) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    output.mkdir()

    result = _capture(source, "baseline", output)

    assert result.returncode != 0
    assert "ordinary single-link regular file" in result.stderr
    assert output.is_dir()


def test_legacy_digest_capture_rejects_output_fifo(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes unavailable")
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    try:
        os.mkfifo(output)
    except OSError as exc:
        pytest.skip("named pipes unavailable: {}".format(exc))

    result = _capture(source, "baseline", output)

    assert result.returncode != 0
    assert "ordinary single-link regular file" in result.stderr
    assert output.exists()


def test_legacy_digest_capture_rejects_multiply_linked_output(tmp_path: Path) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    linked = tmp_path / "catalog-linked.json"
    _write_skill(source)
    assert _capture(source, "baseline", output).returncode == 0
    original = output.read_bytes()
    try:
        os.link(output, linked)
    except OSError as exc:
        pytest.skip("hard links unavailable: {}".format(exc))

    result = _capture(source, "baseline", output)

    assert result.returncode != 0
    assert "ordinary single-link regular file" in result.stderr
    assert output.read_bytes() == original
    assert linked.read_bytes() == original


def test_legacy_digest_capture_create_does_not_follow_racing_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    _write_skill(source)
    target.write_bytes(b"untouched")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_open = capture_tool.os.open
    raced = False

    def race_output(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal raced
        if not raced and Path(path) == output:
            output.symlink_to(target)
            raced = True
        return real_open(path, flags, mode)

    monkeypatch.setattr(capture_tool.os, "open", race_output)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_TOOL),
            "--source",
            str(source),
            "--label",
            "baseline",
            "--output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit):
        capture_tool.main()

    assert raced
    assert output.is_symlink()
    assert target.read_bytes() == b"untouched"


def test_legacy_digest_capture_removes_partial_new_output_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    real_write = capture_tool.os.write
    calls = 0

    def fail_after_partial_write(descriptor: int, content: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, content[:1])
        raise OSError("simulated output failure")

    monkeypatch.setattr(capture_tool.os, "write", fail_after_partial_write)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_TOOL),
            "--source",
            str(source),
            "--label",
            "baseline",
            "--output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit):
        capture_tool.main()

    assert calls == 2
    assert not output.exists()


def test_legacy_digest_capture_closes_but_does_not_unlink_on_initial_fstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    real_open = capture_tool.os.open
    real_fstat = capture_tool.os.fstat
    output_descriptor = None

    def remember_output(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal output_descriptor
        descriptor = real_open(path, flags, mode)
        if Path(path) == output:
            output_descriptor = descriptor
        return descriptor

    def fail_output_fstat(descriptor: int) -> os.stat_result:
        if descriptor == output_descriptor:
            raise OSError("simulated fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(capture_tool.os, "open", remember_output)
    monkeypatch.setattr(capture_tool.os, "fstat", fail_output_fstat)

    with pytest.raises(ValueError, match="fstat|inspect|output"):
        capture_tool._write_new_output(output, b"content")

    assert output_descriptor is not None
    with pytest.raises(OSError):
        real_fstat(output_descriptor)
    assert output.read_bytes() == b""


def test_legacy_digest_capture_does_not_remove_regular_replacement_before_lstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"attacker replacement")
    real_lstat = capture_tool.Path.lstat
    replaced = False

    def replace_before_lstat(path: Path) -> os.stat_result:
        nonlocal replaced
        if path == output and not replaced:
            replacement.replace(output)
            replaced = True
        return real_lstat(path)

    monkeypatch.setattr(capture_tool.Path, "lstat", replace_before_lstat)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._write_new_output(output, b"expected")

    assert replaced
    assert output.read_bytes() == b"attacker replacement"


def test_legacy_digest_capture_fstat_failure_does_not_remove_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    target.write_bytes(b"wrong")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_open = capture_tool.os.open
    real_fstat = capture_tool.os.fstat
    output_descriptor = None

    def remember_output(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal output_descriptor
        descriptor = real_open(path, flags, mode)
        if Path(path) == output:
            output_descriptor = descriptor
        return descriptor

    def replace_then_fail(descriptor: int) -> os.stat_result:
        if descriptor == output_descriptor:
            output.unlink()
            output.symlink_to(target)
            raise OSError("simulated fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(capture_tool.os, "open", remember_output)
    monkeypatch.setattr(capture_tool.os, "fstat", replace_then_fail)

    with pytest.raises(ValueError, match="fstat|inspect|output"):
        capture_tool._write_new_output(output, b"content")

    assert output.is_symlink()
    assert target.read_bytes() == b"wrong"


def test_legacy_digest_capture_rejects_read_path_replaced_by_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    output.write_bytes(b"expected")
    target.write_bytes(b"wrong")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_read = capture_tool.os.read
    replaced = False

    def replace_after_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        content = real_read(descriptor, size)
        if content and not replaced:
            output.unlink()
            output.symlink_to(target)
            replaced = True
        return content

    monkeypatch.setattr(capture_tool.os, "read", replace_after_read)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._read_existing_output(output)

    assert replaced
    assert output.is_symlink()
    assert target.read_bytes() == b"wrong"


def test_legacy_digest_capture_rejects_read_path_replaced_during_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    output.write_bytes(b"expected")
    target.write_bytes(b"wrong")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_close = capture_tool.os.close
    replaced = False

    def close_then_replace(descriptor: int) -> None:
        nonlocal replaced
        real_close(descriptor)
        output.unlink()
        output.symlink_to(target)
        replaced = True

    monkeypatch.setattr(capture_tool.os, "close", close_then_replace)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._read_existing_output(output)

    assert replaced
    assert output.is_symlink()
    assert target.read_bytes() == b"wrong"


def test_legacy_digest_capture_rejects_write_path_replaced_by_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    target.write_bytes(b"wrong")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_write = capture_tool.os.write
    replaced = False

    def replace_after_write(descriptor: int, content: bytes) -> int:
        nonlocal replaced
        written = real_write(descriptor, content)
        if not replaced:
            output.unlink()
            output.symlink_to(target)
            replaced = True
        return written

    monkeypatch.setattr(capture_tool.os, "write", replace_after_write)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._write_new_output(output, b"expected")

    assert replaced
    assert output.is_symlink()
    assert target.read_bytes() == b"wrong"


def test_legacy_digest_capture_rejects_read_hardlink_added_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    linked = tmp_path / "linked.json"
    output.write_bytes(b"expected")
    real_read = capture_tool.os.read
    linked_after_open = False

    def link_after_read(descriptor: int, size: int) -> bytes:
        nonlocal linked_after_open
        content = real_read(descriptor, size)
        if content and not linked_after_open:
            try:
                os.link(output, linked)
            except OSError as exc:
                pytest.skip("hard links unavailable: {}".format(exc))
            linked_after_open = True
        return content

    monkeypatch.setattr(capture_tool.os, "read", link_after_read)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._read_existing_output(output)

    assert linked_after_open
    assert output.read_bytes() == b"expected"
    assert linked.read_bytes() == b"expected"


def test_legacy_digest_capture_rejects_write_hardlink_added_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    linked = tmp_path / "linked.json"
    real_write = capture_tool.os.write
    linked_after_open = False

    def link_after_write(descriptor: int, content: bytes) -> int:
        nonlocal linked_after_open
        written = real_write(descriptor, content)
        if not linked_after_open:
            try:
                os.link(output, linked)
            except OSError as exc:
                pytest.skip("hard links unavailable: {}".format(exc))
            linked_after_open = True
        return written

    monkeypatch.setattr(capture_tool.os, "write", link_after_write)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._write_new_output(output, b"expected")

    assert linked_after_open
    assert not output.exists()
    assert linked.read_bytes() == b"expected"


def test_legacy_digest_capture_converts_read_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    output.write_bytes(b"expected")
    real_close = capture_tool.os.close

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("simulated close failure")

    monkeypatch.setattr(capture_tool.os, "close", close_then_fail)

    with pytest.raises(ValueError, match="close failure"):
        capture_tool._read_existing_output(output)


def test_legacy_digest_capture_preserves_read_error_over_close_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    output.write_bytes(b"expected")
    real_close = capture_tool.os.close

    def fail_read(descriptor: int, size: int) -> bytes:
        raise OSError("simulated primary read failure")

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("simulated secondary close failure")

    monkeypatch.setattr(capture_tool.os, "read", fail_read)
    monkeypatch.setattr(capture_tool.os, "close", close_then_fail)

    with pytest.raises(ValueError, match="primary read failure") as error:
        capture_tool._read_existing_output(output)
    assert "secondary close failure" not in str(error.value)


def test_committed_legacy_catalog_contains_no_removed_personal_skills() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert [item["label"] for item in payload["collections"]] == [
        "portable-adapter-baseline-7596215"
    ]
    catalog = payload["collections"][0]["skills"]
    discovered = discover_skill_collection(
        DATA / "skills", ignore_source_artifacts=True
    )
    current_names = {skill.name for skill in discovered.skills}
    removed = {
        "memory-bridge",
        "wiki-dashboard",
        "wiki-stage-commit",
        "wiki-switch",
    }

    catalog_names = set(catalog)
    assert removed.isdisjoint(catalog_names)
    assert {"llm-wiki", "wiki-ingest"} <= current_names & catalog_names
    assert current_names - catalog_names == {"wiki-transaction-review"}
    assert "wiki-transaction-review" in current_names
    assert "wiki-transaction-review" not in catalog
    for digest in catalog.values():
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)

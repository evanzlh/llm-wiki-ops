from __future__ import annotations

import ast
import errno
import json
import os
import stat
import subprocess
import sys
from dataclasses import MISSING, FrozenInstanceError, fields
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import pytest

from obsidian_wiki import agent_adapter, cli
from obsidian_wiki.agent_adapter import (
    ADAPTER_BOOTSTRAP_GATE_END,
    ADAPTER_DESCRIPTION,
    ADAPTER_EOF,
    ADAPTER_NAME,
    render_adapter_skill,
)
from obsidian_wiki.frontmatter import parse_frontmatter
from obsidian_wiki.skill_trees import discover_skill_collection

ROOT = Path(__file__).resolve().parents[1]

ADAPTER_DIGEST = "sha256:" + "a" * 64
SECOND_ADAPTER_DIGEST = "sha256:" + "b" * 64
EXPECTED_TARGET_ROOTS = {
    "codex": ".codex/skills",
    "claude": ".claude/skills",
    "cursor": ".cursor/skills",
    "windsurf": ".codeium/windsurf/skills",
    "opencode": ".config/opencode/skills",
    "pi": ".pi/agent/skills",
    "kiro": ".kiro/skills",
}

ADAPTER_GIT_PREFIX = ["git", "-C", "/test/repo", "--literal-pathspecs"]
EXPECTED_ADAPTER_GIT_ARGV = (
    [
        *ADAPTER_GIT_PREFIX,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        "<task-path>",
    ],
    [*ADAPTER_GIT_PREFIX, "add", "--", "<task-path>"],
    [*ADAPTER_GIT_PREFIX, "diff", "--cached", "--", "<task-path>"],
    [
        *ADAPTER_GIT_PREFIX,
        "diff",
        "--cached",
        "--check",
        "--",
        "<task-path>",
    ],
    [
        *ADAPTER_GIT_PREFIX,
        "commit",
        "-m",
        "<task summary>",
        "--",
        "<task-path>",
    ],
)


def _documented_adapter_git_argv(contents: str) -> tuple[list[str], ...]:
    prefix = ", ".join(repr(part) for part in ADAPTER_GIT_PREFIX[:3])
    return tuple(
        ast.literal_eval(line.strip().replace("<git-cli>", prefix, 1))
        for line in contents.splitlines()
        if line.strip().startswith("[<git-cli>,")
    )


def run_adapter_cli(
    home: Path,
    cwd: Path,
    *arguments: str,
    environ: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("CODEX_HOME", None)
    if environ is not None:
        env.update(environ)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki", *arguments],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def adapter_home_snapshot(home: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    if not home.exists():
        return ()
    snapshot: list[tuple[str, str, bytes | None]] = []
    for path in sorted(home.rglob("*")):
        relative = path.relative_to(home).as_posix()
        if path.is_symlink():
            snapshot.append((relative, "symlink", os.readlink(path).encode()))
        elif path.is_dir():
            snapshot.append((relative, "directory", None))
        else:
            snapshot.append((relative, "file", path.read_bytes()))
    return tuple(snapshot)


@pytest.mark.parametrize("target", sorted(EXPECTED_TARGET_ROOTS))
def test_cli_installs_only_one_explicit_adapter_and_is_idempotent(
    target: str, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    work_before = adapter_home_snapshot(work)

    installed = run_adapter_cli(
        home, work, "agent", "install-adapter", "--agent", target
    )

    destination = home / EXPECTED_TARGET_ROOTS[target] / ADAPTER_NAME
    assert installed.returncode == 0, installed.stderr
    assert installed.stderr == ""
    assert installed.stdout == f"installed {target} adapter at {destination}\n"
    assert (destination / "SKILL.md").is_file()
    installed_text = (destination / "SKILL.md").read_text(encoding="utf-8").lower()
    for required in (
        "explicit user request authorizes ordinary local steps",
        "observable progress",
        "ask immediately before",
    ):
        assert required in installed_text
    assert (destination / ".llmwikiops-managed.json").is_file()
    assert [
        path
        for path in home.rglob(ADAPTER_NAME)
        if path.is_dir() and path != destination
    ] == []
    assert adapter_home_snapshot(work) == work_before
    before = adapter_home_snapshot(home)

    unchanged = run_adapter_cli(
        home, work, "agent", "install-adapter", "--agent", target
    )

    assert unchanged.returncode == 0, unchanged.stderr
    assert unchanged.stderr == ""
    assert unchanged.stdout == f"unchanged {target} adapter at {destination}\n"
    assert adapter_home_snapshot(home) == before
    assert adapter_home_snapshot(work) == work_before


@pytest.mark.parametrize(
    "arguments",
    [
        ("agent", "install-adapter"),
        ("agent", "install-adapter", "--agent", "unknown"),
        (
            "agent",
            "install-adapter",
            "--agent",
            "codex",
            "--agent",
            "claude",
        ),
        ("agent", "install-adapter", "--all"),
        ("agent", "install-adapter", "--age", "codex"),
        ("agent", "install-adapter", "--agen", "codex"),
        ("agent", "install-adapter", "--a", "codex"),
        ("agent", "install-adapter", "--agent", "codex", "--force"),
        (
            "agent",
            "install-adapter",
            "--agent",
            "codex",
            "--destination",
            "elsewhere",
        ),
        ("-C", ".", "agent", "install-adapter", "--agent", "codex"),
        ("--repo", ".", "agent", "install-adapter", "--agent", "codex"),
    ],
)
def test_cli_rejects_implicit_or_expanded_adapter_installation_surface(
    arguments: tuple[str, ...], tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    before = adapter_home_snapshot(home)
    work_before = adapter_home_snapshot(work)

    result = run_adapter_cli(home, work, *arguments)

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert adapter_home_snapshot(home) == before
    assert adapter_home_snapshot(work) == work_before


def test_cli_codex_uses_explicit_codex_home_and_escapes_destination(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    codex_home = tmp_path / "codex\nforged"

    result = run_adapter_cli(
        home,
        work,
        "agent",
        "install-adapter",
        "--agent",
        "codex",
        environ={"CODEX_HOME": str(codex_home)},
    )

    destination = codex_home / "skills" / ADAPTER_NAME
    escaped_destination = str(destination).replace("\n", r"\n")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        f"installed codex adapter at {escaped_destination}"
    ]
    assert (destination / "SKILL.md").is_file()
    assert adapter_home_snapshot(home) == ()
    assert adapter_home_snapshot(work) == ()


@pytest.mark.parametrize("target", sorted(set(EXPECTED_TARGET_ROOTS) - {"codex"}))
def test_cli_non_codex_targets_ignore_codex_home(
    target: str, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    override = tmp_path / "must-not-be-used"

    result = run_adapter_cli(
        home,
        work,
        "agent",
        "install-adapter",
        "--agent",
        target,
        environ={"CODEX_HOME": str(override)},
    )

    destination = home / EXPECTED_TARGET_ROOTS[target] / ADAPTER_NAME
    assert result.returncode == 0, result.stderr
    assert (destination / "SKILL.md").is_file()
    assert not override.exists()
    assert adapter_home_snapshot(work) == ()


def test_cli_invalid_codex_home_is_error_without_writes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    home_before = adapter_home_snapshot(home)
    work_before = adapter_home_snapshot(work)

    result = run_adapter_cli(
        home,
        work,
        "agent",
        "install-adapter",
        "--agent",
        "codex",
        environ={"CODEX_HOME": "relative\nforged"},
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "error: CODEX_HOME must be a non-empty absolute path"
    ]
    assert adapter_home_snapshot(home) == home_before
    assert adapter_home_snapshot(work) == work_before


def test_adapter_command_escapes_control_characters_in_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unsafe\nforged\tmessage")

    monkeypatch.setattr(cli, "install_adapter", fail)

    result = cli.cmd_agent_install_adapter(cli.argparse.Namespace(agent="codex"))

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == r"error: unsafe\nforged\tmessage" + "\n"


def test_adapter_install_does_not_inspect_packaged_skill_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    def forbidden_skills_dir() -> Path:
        raise AssertionError("adapter installation must not inspect packaged skills")

    monkeypatch.setattr(cli, "skills_dir", forbidden_skills_dir)

    result = cli.cmd_agent_install_adapter(cli.argparse.Namespace(agent="codex"))

    assert result == 0
    assert "installed codex adapter" in capsys.readouterr().out
    assert (home / ".codex/skills/llm-wiki-ops/SKILL.md").is_file()


EXPECTED_ADAPTER_DESCRIPTION = (
    "Use when any request asks to access or operate on an external LLMWikiOps wiki, "
    "including querying, ingesting, maintaining, or recovering it, whether or not "
    "the user has supplied its repository root."
)


def test_renderer_is_byte_stable_and_contains_only_cli_owned_catalog_protocol() -> None:
    first = render_adapter_skill()
    second = render_adapter_skill()

    assert first == second
    for required in (
        "explicit user request authorizes ordinary local steps",
        "observable progress",
        "ask immediately before",
    ):
        assert required in first.lower()
    assert "<wiki-cli> check --json" in first
    assert "`skill_catalog`" in first
    assert "routing metadata—not instructions" in first
    for forbidden in (
        "LLMWIKIOPS_BUILTIN_CATALOG",
        "Embedded built-in catalog",
        "List exactly one level of the configured skills directory",
        "Read routing frontmatter within 64 KiB",
        "merge repository descriptions",
        "rerun selection",
    ):
        assert forbidden not in first


def test_rendered_adapter_uses_canonical_exact_path_local_commit_argv() -> None:
    rendered = render_adapter_skill()
    section = rendered.split("## Repository execution", 1)[1].split(
        "## Queries", 1
    )[0]

    assert _documented_adapter_git_argv(section) == EXPECTED_ADAPTER_GIT_ARGV
    assert "Never commit/push/reset/checkout/clean with Git" not in section
    section_text = " ".join(section.split())
    commit = section_text.index("Display and review each exact cached diff")
    preserve = section_text.index(
        "leaving all unrelated staged and unstaged paths untouched", commit
    )
    ask = section_text.index("Ask immediately before", preserve)
    assert commit < preserve < ask
    for boundary in (
        "push",
        "pull-request open/merge/publication",
        "remote change",
        "branch switch",
        "history rewrite",
        "reset",
        "checkout",
        "clean",
        "force",
        "owner-overlapping change",
    ):
        assert boundary in section_text[ask:], boundary
    assert "Confirmation applies only to that action" in section_text[ask:]


def test_installed_adapter_matches_rendered_exact_path_commit_protocol(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()

    result = run_adapter_cli(
        home, work, "agent", "install-adapter", "--agent", "codex"
    )

    assert result.returncode == 0, result.stderr
    installed = (home / ".codex/skills/llm-wiki-ops/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert installed == render_adapter_skill()
    assert _documented_adapter_git_argv(installed) == EXPECTED_ADAPTER_GIT_ARGV


def test_template_uses_static_repository_preflight_instead_of_safe_reader(
    tmp_path: Path,
) -> None:
    rendered = render_adapter_skill()

    forbidden = (
        "LLMWIKIOPS_SAFE_READER",
        "LLMWIKIOPS_SAFE_MODE",
        "root-bind",
        "skill-catalog",
        "O_NOFOLLOW",
        "O_DIRECTORY",
        "base64.b64decode",
        "hashlib.sha256",
        "root_identity",
        "catalog-returned relative path",
        "os.lstat",
        "stat.S_ISREG",
        "os.path.isfile",
        "Path.is_file",
        "same process immediately before reading",
        "hash-only reads",
    )
    for value in forbidden:
        assert value not in rendered

    required = (
        "## Supported repository model",
        "user-controlled local filesystem",
        "quiescent",
        "<wiki-cli> = llmwikiops -C <exact-root>",
        "<git-cli> = git -C <exact-root>",
        "<wiki-cli> info --json",
        "<wiki-cli> check --json",
        "consumption limits, not a per-read metadata or TOCTOU protocol",
        "unsupported concurrent modification",
    )
    for value in required:
        assert value in rendered


def test_template_distinguishes_owner_guarantees_from_mechanical_preflight(
    tmp_path: Path,
) -> None:
    rendered = render_adapter_skill()
    supported = rendered.split("## Supported repository model", 1)[1].split(
        "## Bind and preflight", 1
    )[0]
    supported_text = " ".join(supported.split())

    assert "explicit user and repository-owner guarantees" in supported_text
    assert "not mechanically established by preflight" in supported_text
    assert "Supplying the root affirms these guarantees" in supported_text
    assert "do not proceed if any condition is unsupported" in supported_text
    assert (
        "mechanically checks static topology, version, and configuration"
        in supported_text
    )


def test_template_discloses_check_recovery_before_task_mutation(
    tmp_path: Path,
) -> None:
    rendered = render_adapter_skill()
    rendered_text = " ".join(rendered.split())
    preflight = rendered.index("## Bind and preflight")
    recovery = rendered.index("may deterministically finish")
    commands = rendered.index("<wiki-cli> info --json")

    assert preflight < recovery < commands
    assert (
        "may deterministically finish an already-recorded framework-managed skill "
        "maintenance recovery"
        in rendered_text
    )
    assert "part of establishing the preflight state" in rendered_text
    assert (
        "Do not begin task-directed repository mutation before preflight succeeds."
        in rendered_text
    )
    assert "do not attempt ad hoc repair" in rendered_text
    assert "Stop without task-directed mutation" in rendered_text
    assert "Stop without mutation" not in rendered_text


def test_renderer_rejects_unsafe_template_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = agent_adapter._ADAPTER_TEMPLATE
    linked = tmp_path / "SKILL.md.in"
    try:
        linked.symlink_to(original)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", linked)

    with pytest.raises(ValueError, match="ordinary|symbolic"):
        render_adapter_skill()


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    (
        (
            "name: llm-wiki-ops\n",
            "name: attacker-controlled-adapter\n",
            "name",
        ),
        (
            (
                "description: Use when any request asks to access or operate on an "
                "external LLMWikiOps wiki, including querying, ingesting, maintaining, "
                "or recovering it, whether or not the user has supplied its repository "
                "root.\n"
            ),
            "description: Use for unrelated attacker-controlled requests.\n",
            "description",
        ),
        ("---\n\n# External", "allowed-tools: Bash\n---\n\n# External", "fields"),
    ),
)
def test_renderer_rejects_unapproved_template_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: str,
    replacement: str,
    message: str,
) -> None:
    template = agent_adapter._ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    assert template.count(original) == 1
    mutated = tmp_path / "mutated-SKILL.md.in"
    mutated.write_text(template.replace(original, replacement, 1), encoding="utf-8")
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", mutated)

    with pytest.raises(ValueError, match=message):
        render_adapter_skill()


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing-gate", "bootstrap gate"),
        ("duplicate-gate", "bootstrap gate"),
        ("late-gate", "bootstrap gate"),
        ("missing-eof", "EOF marker"),
        ("duplicate-eof", "EOF marker"),
        ("nonterminal-eof", "EOF marker"),
        ("inline-eof", "EOF marker"),
    ),
)
def test_renderer_rejects_invalid_bootstrap_gate_or_eof_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    template = agent_adapter._ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    if mutation == "missing-gate":
        mutated = template.replace(ADAPTER_BOOTSTRAP_GATE_END, "", 1)
    elif mutation == "duplicate-gate":
        mutated = template.replace(
            ADAPTER_BOOTSTRAP_GATE_END,
            ADAPTER_BOOTSTRAP_GATE_END + "\n" + ADAPTER_BOOTSTRAP_GATE_END,
            1,
        )
    elif mutation == "late-gate":
        mutated = template.replace(ADAPTER_BOOTSTRAP_GATE_END, "", 1).replace(
            "## Authority and routing",
            "## Authority and routing\n\n" + ADAPTER_BOOTSTRAP_GATE_END,
            1,
        )
    elif mutation == "missing-eof":
        prefix, marker, suffix = template.rpartition(ADAPTER_EOF)
        assert marker and suffix == "\n"
        mutated = prefix.rstrip("\n") + "\n"
    elif mutation == "duplicate-eof":
        mutated = template + ADAPTER_EOF + "\n"
    elif mutation == "nonterminal-eof":
        prefix, marker, suffix = template.rpartition(ADAPTER_EOF)
        assert marker and suffix == "\n"
        mutated = prefix + ADAPTER_EOF + "\n\ntrailing authority\n"
    else:
        prefix, marker, suffix = template.rpartition(ADAPTER_EOF)
        assert marker and suffix == "\n"
        mutated = prefix.rstrip("\n") + "\nnot-independent " + ADAPTER_EOF + "\n"
    path = tmp_path / f"{mutation}.in"
    path.write_text(mutated, encoding="utf-8")
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", path)

    with pytest.raises(ValueError, match=message):
        render_adapter_skill()


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-supported",
        "duplicate-supported",
        "late-supported",
        "missing-preflight",
        "duplicate-preflight",
        "missing-catalog-reads",
        "duplicate-catalog-reads",
        "missing-route",
        "duplicate-route",
        "missing-info",
        "duplicate-info",
        "missing-check",
        "duplicate-check",
        "missing-skill-catalog",
        "duplicate-skill-catalog",
        "reordered-info-check",
        "reordered-supported-preflight",
        "reordered-catalog-route",
    ),
)
def test_renderer_rejects_invalid_static_repository_protocol_anchors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    template = agent_adapter._ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    supported = "## Supported repository model"
    preflight = "## Bind and preflight"
    info = "<wiki-cli> info --json"
    check = "<wiki-cli> check --json"
    skill_catalog = "Require `skill_catalog` to be a nonempty array."
    catalog_reads = "## CLI-owned catalog and bounded reads"
    route = "## Route and load authority"
    if mutation == "missing-supported":
        mutated = template.replace(supported, "## Repository constraints", 1)
    elif mutation == "duplicate-supported":
        mutated = template.replace(preflight, supported + "\n\n" + preflight, 1)
    elif mutation == "late-supported":
        mutated = template.replace(supported, "", 1).replace(
            route, route + "\n\n" + supported, 1
        )
    elif mutation == "missing-preflight":
        mutated = template.replace(preflight, "## Repository preflight", 1)
    elif mutation == "duplicate-preflight":
        mutated = template.replace(
            catalog_reads, preflight + "\n\n" + catalog_reads, 1
        )
    elif mutation == "missing-catalog-reads":
        mutated = template.replace(catalog_reads, "## Repository reads", 1)
    elif mutation == "duplicate-catalog-reads":
        mutated = template.replace(route, catalog_reads + "\n\n" + route, 1)
    elif mutation == "missing-route":
        mutated = template.replace(route, "## Authority loading", 1)
    elif mutation == "duplicate-route":
        mutated = template.replace(
            "## Repository execution",
            route + "\n\n## Repository execution",
            1,
        )
    elif mutation == "missing-info":
        mutated = template.replace(info, "<wiki-cli> inspect --json", 1)
    elif mutation == "duplicate-info":
        mutated = template.replace(info, info + "\n" + info, 1)
    elif mutation == "missing-check":
        mutated = template.replace(check, "<wiki-cli> verify", 1)
    elif mutation == "duplicate-check":
        mutated = template.replace(check, check + "\n" + check, 1)
    elif mutation == "missing-skill-catalog":
        mutated = template.replace(skill_catalog, "Catalog data is required.", 1)
    elif mutation == "duplicate-skill-catalog":
        mutated = template.replace(
            skill_catalog, skill_catalog + "\n" + skill_catalog, 1
        )
    elif mutation == "reordered-info-check":
        assert template.index(info) < template.index(check)
        mutated = template.replace(info, "<static-info-anchor>", 1).replace(
            check, info, 1
        ).replace("<static-info-anchor>", check, 1)
    elif mutation == "reordered-supported-preflight":
        mutated = template.replace(supported, "<static-supported-anchor>", 1).replace(
            preflight, supported, 1
        ).replace("<static-supported-anchor>", preflight, 1)
    else:
        mutated = template.replace(catalog_reads, "<static-catalog-anchor>", 1).replace(
            route, catalog_reads, 1
        ).replace("<static-catalog-anchor>", route, 1)

    assert mutated.count(ADAPTER_BOOTSTRAP_GATE_END) == 1
    assert mutated.count(ADAPTER_EOF) == 1
    path = tmp_path / f"{mutation}.in"
    path.write_text(mutated, encoding="utf-8")
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", path)

    with pytest.raises(ValueError, match="static repository anchors"):
        render_adapter_skill()


def test_rendered_frontmatter_has_only_name_and_description_and_stays_bounded() -> None:
    rendered = render_adapter_skill()
    frontmatter = parse_frontmatter(rendered)
    header = rendered.split("---\n", 2)[1]
    body = rendered.split("---\n", 2)[2]

    assert frontmatter.fields == {"name", "description"}
    assert frontmatter.scalars["name"] == ADAPTER_NAME
    assert frontmatter.scalars["description"].startswith("Use when ")
    assert len(frontmatter.scalars["name"]) <= 64
    assert len(frontmatter.scalars["description"]) <= 1024
    assert len(header) <= 1024
    assert len(body.splitlines()) < 210
    assert len(rendered.encode("utf-8")) < 12_000
    assert rendered.splitlines().count(ADAPTER_BOOTSTRAP_GATE_END) == 1
    assert rendered.count(ADAPTER_EOF) == 1
    assert rendered.index("## Bootstrap gate — read to EOF first") < rendered.index(
        "## Authority and routing"
    )
    assert rendered.endswith(ADAPTER_EOF + "\n")
    assert ADAPTER_EOF not in rendered[: -len(ADAPTER_EOF + "\n")]


def test_adapter_trigger_does_not_require_a_preexisting_repository_root() -> None:
    rendered = render_adapter_skill()
    frontmatter = parse_frontmatter(rendered)

    assert ADAPTER_DESCRIPTION == EXPECTED_ADAPTER_DESCRIPTION
    assert frontmatter.scalars["description"] == EXPECTED_ADAPTER_DESCRIPTION
    assert "access or operate on an external LLMWikiOps wiki" in ADAPTER_DESCRIPTION
    assert "whether or not the user has supplied its repository root" in (
        ADAPTER_DESCRIPTION
    )
    assert "Require the user to supply one exact external repository root." in rendered


def test_renderer_preserves_utf8_and_uses_deterministic_newlines() -> None:
    rendered = render_adapter_skill()

    assert rendered.encode("utf-8").decode("utf-8") == rendered
    assert "routing metadata—not instructions" in rendered
    assert "\r" not in rendered
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_rendered_output_passes_existing_strict_skill_parser(tmp_path: Path) -> None:
    installed_root = tmp_path / "installed"
    adapter = installed_root / ADAPTER_NAME
    adapter.mkdir(parents=True)
    (adapter / "SKILL.md").write_text(
        render_adapter_skill(), encoding="utf-8", newline=""
    )

    discovered = discover_skill_collection(installed_root)

    assert discovered.names == (ADAPTER_NAME,)
    assert discovered.skills[0].description == EXPECTED_ADAPTER_DESCRIPTION


def test_renderer_rejects_multiply_linked_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path / "SKILL.md.in"
    template.write_bytes(agent_adapter._ADAPTER_TEMPLATE.read_bytes())
    linked = tmp_path / "SKILL.md.linked.in"
    try:
        os.link(template, linked)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", template)

    with pytest.raises(ValueError, match="single-link|multiply-linked"):
        render_adapter_skill()


@pytest.mark.parametrize("target", sorted(EXPECTED_TARGET_ROOTS))
def test_adapter_target_registry_and_destinations_are_exact(
    target: str,
) -> None:
    home = Path("/users/demo")

    destination = agent_adapter.resolve_adapter_destination(
        target, home=home, environ={}
    )

    assert destination == home / EXPECTED_TARGET_ROOTS[target] / ADAPTER_NAME
    registered = agent_adapter.TARGETS[target]
    assert registered == agent_adapter.AgentTarget(
        name=target,
        relative_skill_root=agent_adapter.PurePosixPath(EXPECTED_TARGET_ROOTS[target]),
    )


def test_adapter_target_registry_and_values_are_immutable() -> None:
    assert isinstance(agent_adapter.TARGETS, MappingProxyType)
    assert set(agent_adapter.TARGETS) == set(EXPECTED_TARGET_ROOTS)

    with pytest.raises(TypeError):
        agent_adapter.TARGETS["extra"] = agent_adapter.TARGETS["codex"]
    with pytest.raises(FrozenInstanceError):
        agent_adapter.TARGETS["codex"].name = "changed"


def test_codex_target_honors_only_an_absolute_nonempty_codex_home() -> None:
    home = Path("/users/demo")
    override = Path("/opt/codex")

    assert (
        agent_adapter.resolve_adapter_destination(
            "codex", home=home, environ={"CODEX_HOME": str(override)}
        )
        == override / "skills" / ADAPTER_NAME
    )
    for target in set(EXPECTED_TARGET_ROOTS) - {"codex"}:
        assert (
            agent_adapter.resolve_adapter_destination(
                target, home=home, environ={"CODEX_HOME": str(override)}
            )
            == home / EXPECTED_TARGET_ROOTS[target] / ADAPTER_NAME
        )


@pytest.mark.parametrize("value", ["", "relative", "../codex", "./codex"])
def test_codex_target_rejects_empty_or_relative_codex_home(value: str) -> None:
    with pytest.raises(ValueError, match="CODEX_HOME|absolute|non-empty"):
        agent_adapter.resolve_adapter_destination(
            "codex", home=Path("/users/demo"), environ={"CODEX_HOME": value}
        )


@pytest.mark.parametrize(
    "target", [None, "", "Codex", " codex", "codex ", "codex,claude", ["codex"]]
)
def test_adapter_target_rejects_missing_multiple_unknown_or_noncanonical_names(
    target: object,
) -> None:
    with pytest.raises((TypeError, ValueError), match="target"):
        agent_adapter.resolve_adapter_destination(
            target, home=Path("/users/demo"), environ={}
        )


@pytest.mark.parametrize("home", ["/users/demo", Path("relative"), Path("../home")])
def test_adapter_target_rejects_non_path_or_nonabsolute_home(home: object) -> None:
    with pytest.raises((TypeError, ValueError), match="home|absolute"):
        agent_adapter.resolve_adapter_destination("claude", home=home, environ={})


def test_adapter_target_rejects_path_subclasses_before_calling_overrides() -> None:
    class MaliciousPath(type(Path())):
        def is_absolute(self) -> bool:
            raise AssertionError("must reject subclass before calling is_absolute")

        @property
        def parts(self) -> tuple[str, ...]:
            raise AssertionError("must reject subclass before reading parts")

        def joinpath(self, *pathsegments: str) -> Path:
            raise AssertionError("must reject subclass before calling joinpath")

    with pytest.raises(TypeError, match="home|concrete|pathlib"):
        agent_adapter.resolve_adapter_destination(
            "claude", home=MaliciousPath("/users/demo"), environ={}
        )


def test_adapter_destination_resolution_performs_no_filesystem_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "destination resolution must not inspect or write filesystem"
        )

    for method in ("exists", "is_dir", "resolve", "expanduser", "mkdir", "write_bytes"):
        monkeypatch.setattr(Path, method, forbidden)

    assert agent_adapter.resolve_adapter_destination(
        "codex", home=Path("/users/demo"), environ={}
    ) == Path("/users/demo/.codex/skills/llm-wiki-ops")


def make_adapter_record(
    *, files: dict[str, str] | None = None
) -> agent_adapter.ManagedAdapterRecord:
    return agent_adapter.ManagedAdapterRecord(
        schema_version=1,
        implementation="evanzlh/llm-wiki-ops",
        cli_version="2026.8.18",
        target="codex",
        files={"SKILL.md": ADAPTER_DIGEST} if files is None else files,
    )


def expected_adapter_record_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "implementation": "evanzlh/llm-wiki-ops",
        "cli_version": "2026.8.18",
        "target": "codex",
        "files": {"SKILL.md": ADAPTER_DIGEST},
    }


def test_managed_adapter_record_round_trip_is_canonical_utf8_json() -> None:
    record = make_adapter_record()

    rendered = agent_adapter.render_managed_record(record)

    assert rendered == (
        json.dumps(
            expected_adapter_record_payload(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    assert agent_adapter.parse_managed_record(rendered) == record
    assert rendered.endswith(b"\n") and not rendered.endswith(b"\n\n")


def test_managed_adapter_record_and_files_are_immutable_and_copied() -> None:
    source = {"SKILL.md": ADAPTER_DIGEST, "README.md": SECOND_ADAPTER_DIGEST}
    record = make_adapter_record(files=source)
    source["SKILL.md"] = SECOND_ADAPTER_DIGEST

    assert isinstance(record.files, MappingProxyType)
    assert list(record.files) == ["README.md", "SKILL.md"]
    assert record.files["SKILL.md"] == ADAPTER_DIGEST
    with pytest.raises(TypeError):
        record.files["SKILL.md"] = SECOND_ADAPTER_DIGEST
    with pytest.raises(FrozenInstanceError):
        record.target = "claude"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("target"),
        lambda payload: payload.__setitem__("unexpected", True),
        lambda payload: payload.__setitem__("schema_version", True),
        lambda payload: payload.__setitem__("schema_version", 2),
        lambda payload: payload.__setitem__("implementation", "other/wiki"),
        lambda payload: payload.__setitem__("target", "Codex"),
        lambda payload: payload.__setitem__("target", "unknown"),
        lambda payload: payload.__setitem__("cli_version", ""),
        lambda payload: payload.__setitem__("cli_version", " 2026.8.18"),
        lambda payload: payload.__setitem__("cli_version", "2026.8.18\n"),
        lambda payload: payload.__setitem__("files", []),
        lambda payload: payload.__setitem__("files", {}),
        lambda payload: payload.__setitem__("files", {"README.md": ADAPTER_DIGEST}),
        lambda payload: payload.__setitem__(
            "files",
            {"SKILL.md": ADAPTER_DIGEST, ".llmwikiops-managed.json": ADAPTER_DIGEST},
        ),
    ],
)
def test_managed_adapter_record_rejects_wrong_schema_and_scalar_values(
    mutation,
) -> None:
    payload = expected_adapter_record_payload()
    mutation(payload)

    with pytest.raises(ValueError):
        agent_adapter.parse_managed_record(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )


@pytest.mark.parametrize(
    "filename",
    [
        "",
        ".",
        "..",
        "/SKILL.md",
        "./SKILL.md",
        "docs/SKILL.md",
        r"docs\SKILL.md",
        "bad\x00name",
    ],
)
def test_managed_adapter_record_rejects_unsafe_or_noncanonical_filenames(
    filename: str,
) -> None:
    payload = expected_adapter_record_payload()
    payload["files"] = {"SKILL.md": ADAPTER_DIGEST, filename: SECOND_ADAPTER_DIGEST}

    with pytest.raises(ValueError, match="file|name|safe"):
        agent_adapter.parse_managed_record(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "digest",
    [True, "", "sha256:" + "A" * 64, "sha256:" + "a" * 63, "md5:" + "a" * 64],
)
def test_managed_adapter_record_rejects_noncanonical_digests(digest: object) -> None:
    payload = expected_adapter_record_payload()
    payload["files"] = {"SKILL.md": digest}

    with pytest.raises(ValueError, match="digest"):
        agent_adapter.parse_managed_record(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "contents",
    [
        b"",
        b"not json",
        b"[]",
        b"null",
        b'{"schema_version": 1, "schema_version": 1}',
        b"\xff",
        "not bytes",
    ],
)
def test_managed_adapter_record_parser_rejects_malformed_duplicate_or_nonbytes(
    contents: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        agent_adapter.parse_managed_record(contents)


def test_managed_adapter_record_parser_rejects_duplicate_nested_file_key() -> None:
    payload = expected_adapter_record_payload()
    text = json.dumps(payload)
    text = text.replace(
        json.dumps(payload["files"]),
        '{"SKILL.md": "' + ADAPTER_DIGEST + '", "SKILL.md": "' + ADAPTER_DIGEST + '"}',
    )

    with pytest.raises(ValueError, match="duplicate"):
        agent_adapter.parse_managed_record(text.encode())


def test_desired_adapter_contains_exact_rendered_skill_and_matching_record() -> None:
    desired = agent_adapter.build_desired_adapter("claude", "2026.8.20")
    expected_skill = render_adapter_skill().encode("utf-8")
    record = agent_adapter.parse_managed_record(desired.managed_record)

    assert desired.target == "claude"
    assert desired.skill_md == expected_skill
    assert record.target == "claude"
    assert record.files == {"SKILL.md": "sha256:" + sha256(expected_skill).hexdigest()}
    assert desired.managed_record == agent_adapter.render_managed_record(record)
    with pytest.raises(FrozenInstanceError):
        desired.target = "codex"


def test_desired_adapter_rejects_malformed_or_noncanonical_record_bytes() -> None:
    record = make_adapter_record()
    canonical = agent_adapter.render_managed_record(record)
    noncanonical = json.dumps(expected_adapter_record_payload()).encode("utf-8")

    with pytest.raises(ValueError, match="record|JSON"):
        agent_adapter.DesiredAdapter(
            target="codex", skill_md=b"skill", managed_record=b"not json"
        )
    assert noncanonical != canonical
    with pytest.raises(ValueError, match="canonical|record"):
        agent_adapter.DesiredAdapter(
            target="codex", skill_md=b"skill", managed_record=noncanonical
        )


def test_desired_adapter_rejects_record_target_or_skill_digest_mismatch() -> None:
    skill_md = b"adapter skill bytes\n"
    digest = "sha256:" + sha256(skill_md).hexdigest()
    wrong_digest = "sha256:" + "0" * 64

    wrong_target = agent_adapter.ManagedAdapterRecord(
        schema_version=1,
        implementation="evanzlh/llm-wiki-ops",
        cli_version="2026.8.18",
        target="claude",
        files={"SKILL.md": digest},
    )
    with pytest.raises(ValueError, match="target"):
        agent_adapter.DesiredAdapter(
            target="codex",
            skill_md=skill_md,
            managed_record=agent_adapter.render_managed_record(wrong_target),
        )

    wrong_skill = make_adapter_record(files={"SKILL.md": wrong_digest})
    with pytest.raises(ValueError, match="digest|SKILL.md"):
        agent_adapter.DesiredAdapter(
            target="codex",
            skill_md=skill_md,
            managed_record=agent_adapter.render_managed_record(wrong_skill),
        )


def test_desired_adapter_rejects_unrepresented_extra_managed_files() -> None:
    skill_md = b"adapter skill bytes\n"
    record = make_adapter_record(
        files={
            "SKILL.md": "sha256:" + sha256(skill_md).hexdigest(),
            "README.md": SECOND_ADAPTER_DIGEST,
        }
    )

    with pytest.raises(ValueError, match="files|SKILL.md|artifact"):
        agent_adapter.DesiredAdapter(
            target="codex",
            skill_md=skill_md,
            managed_record=agent_adapter.render_managed_record(record),
        )


def make_desired_install(
    *, target: str = "codex", version: str = "2026.8.18", skill: bytes = b"skill\n"
) -> agent_adapter.DesiredAdapter:
    record = agent_adapter.ManagedAdapterRecord(
        schema_version=1,
        implementation="evanzlh/llm-wiki-ops",
        cli_version=version,
        target=target,
        files={"SKILL.md": "sha256:" + sha256(skill).hexdigest()},
    )
    return agent_adapter.DesiredAdapter(
        target=target,
        skill_md=skill,
        managed_record=agent_adapter.render_managed_record(record),
    )


def write_adapter_tree(path: Path, desired: agent_adapter.DesiredAdapter) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_bytes(desired.skill_md)
    (path / agent_adapter.MANAGED_ADAPTER_RECORD).write_bytes(desired.managed_record)


def test_adapter_installation_types_are_frozen_and_exact() -> None:
    file = agent_adapter.ManagedFileSnapshot("SKILL.md", (1, 2), b"skill\n")
    tree = agent_adapter.ManagedTreeSnapshot("llm-wiki-ops", (3, 4), (file,))
    inspection = agent_adapter.AdapterInstallInspection("current", tree, None)
    result = agent_adapter.AdapterInstallResult(
        "unchanged", "codex", Path("/tmp/skills/llm-wiki-ops")
    )

    assert file == agent_adapter.ManagedFileSnapshot(
        name="SKILL.md", identity=(1, 2), content=b"skill\n"
    )
    assert tree.files == (file,)
    assert inspection.snapshot == tree and inspection.error is None
    assert result.status == "unchanged" and result.target == "codex"
    with pytest.raises(FrozenInstanceError):
        result.status = "installed"
    inspection_fields = {item.name: item for item in fields(agent_adapter.AdapterInstallInspection)}
    assert inspection_fields["snapshot"].default is MISSING
    assert inspection_fields["error"].default is None


@pytest.mark.parametrize(
    ("fixture", "expected"),
    (
        ("missing", "missing"),
        ("current", "current"),
        ("old", "managed-upgrade"),
        ("drift", "owner-drift"),
        ("missing-record", "unmanaged"),
        ("malformed-record", "unmanaged"),
        ("unknown-file", "owner-drift"),
    ),
)
def test_adapter_installation_inspection_classification_is_read_only(
    tmp_path: Path, fixture: str, expected: str
) -> None:
    destination = tmp_path / "skills" / ADAPTER_NAME
    desired = make_desired_install()
    old = make_desired_install(version="2026.1.1", skill=b"old skill\n")
    if fixture != "missing":
        if fixture == "old":
            write_adapter_tree(destination, old)
        else:
            write_adapter_tree(destination, desired)
    if fixture == "drift":
        (destination / "SKILL.md").write_bytes(b"owner changed\n")
    elif fixture == "missing-record":
        (destination / agent_adapter.MANAGED_ADAPTER_RECORD).unlink()
    elif fixture == "malformed-record":
        (destination / agent_adapter.MANAGED_ADAPTER_RECORD).write_bytes(b"not json")
    elif fixture == "unknown-file":
        (destination / "README.md").write_bytes(b"unknown\n")

    before = sorted(
        (str(path.relative_to(tmp_path)), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*")
    )
    inspected = agent_adapter.inspect_adapter_installation(destination, desired)
    after = sorted(
        (str(path.relative_to(tmp_path)), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*")
    )

    assert inspected.status == expected
    assert before == after
    assert (inspected.snapshot is not None) is (
        fixture not in {"missing", "unknown-file"}
    )


@pytest.mark.parametrize("unsafe", ("symlink", "hardlink", "fifo", "directory"))
def test_adapter_installation_inspection_rejects_unsafe_topology(
    tmp_path: Path, unsafe: str
) -> None:
    destination = tmp_path / "skills" / ADAPTER_NAME
    desired = make_desired_install()
    write_adapter_tree(destination, desired)
    skill = destination / "SKILL.md"
    original = skill.read_bytes()
    skill.unlink()
    try:
        if unsafe == "symlink":
            outside = tmp_path / "outside"
            outside.write_bytes(original)
            skill.symlink_to(outside)
        elif unsafe == "hardlink":
            outside = tmp_path / "outside"
            outside.write_bytes(original)
            os.link(outside, skill)
        elif unsafe == "fifo":
            os.mkfifo(skill)
        else:
            skill.mkdir()
    except OSError as exc:
        pytest.skip(f"{unsafe} unavailable: {exc}")

    inspected = agent_adapter.inspect_adapter_installation(destination, desired)

    assert inspected.status == "error"
    assert inspected.error


def test_adapter_installation_inspection_reports_open_errors_not_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "skills" / ADAPTER_NAME
    desired = make_desired_install()
    write_adapter_tree(destination, desired)
    real_open = os.open

    def denied(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if path == "SKILL.md":
            raise PermissionError("denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    inspected = agent_adapter.inspect_adapter_installation(destination, desired)
    assert inspected.status == "error"
    assert "denied" in (inspected.error or "")


def test_adapter_installation_inspection_reports_nested_disappearance_not_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "skills" / ADAPTER_NAME
    desired = make_desired_install()
    write_adapter_tree(destination, desired)
    real_open = os.open

    def disappearing(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if path == "SKILL.md":
            raise FileNotFoundError("changed")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", disappearing)

    inspected = agent_adapter.inspect_adapter_installation(destination, desired)

    assert inspected.status == "error"
    assert "changed" in (inspected.error or "")


def test_adapter_installation_inspection_rejects_directory_name_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "skills" / ADAPTER_NAME
    moved = tmp_path / "moved"
    desired = make_desired_install()
    write_adapter_tree(destination, desired)
    real_listdir = os.listdir
    swapped = False

    def swapping_listdir(path: object) -> list[str]:
        nonlocal swapped
        names = real_listdir(path)
        if (
            isinstance(path, int)
            and not swapped
            and set(names)
            == {
                "SKILL.md",
                agent_adapter.MANAGED_ADAPTER_RECORD,
            }
        ):
            destination.rename(moved)
            destination.mkdir()
            (destination / "SKILL.md").write_bytes(b"replacement\n")
            swapped = True
        return names

    monkeypatch.setattr(os, "listdir", swapping_listdir)

    inspected = agent_adapter.inspect_adapter_installation(destination, desired)

    assert swapped
    assert inspected.status == "error"
    assert "changed" in (inspected.error or "")


def test_adapter_installation_inspection_rejects_file_name_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "skills" / ADAPTER_NAME
    desired = make_desired_install()
    write_adapter_tree(destination, desired)
    skill = destination / "SKILL.md"
    moved = tmp_path / "moved-skill"
    real_read = os.read
    swapped = False

    def swapping_read(descriptor: int, size: int) -> bytes:
        nonlocal swapped
        content = real_read(descriptor, size)
        if content == desired.skill_md and not swapped:
            skill.rename(moved)
            skill.write_bytes(b"replacement\n")
            swapped = True
        return content

    monkeypatch.setattr(os, "read", swapping_read)

    inspected = agent_adapter.inspect_adapter_installation(destination, desired)

    assert swapped
    assert inspected.status == "error"
    assert "changed" in (inspected.error or "")


def test_adapter_installation_fresh_idempotent_and_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    tokens = iter(tuple(str(index) * 32 for index in range(1, 9)))
    monkeypatch.setattr(agent_adapter.secrets, "token_hex", lambda size: next(tokens))

    installed = agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    unchanged = agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    upgraded = agent_adapter.install_adapter(
        "codex", cli_version="2", home=home, environ={}
    )

    expected = home / ".codex/skills" / ADAPTER_NAME
    assert installed == agent_adapter.AdapterInstallResult(
        "installed", "codex", expected
    )
    assert unchanged == agent_adapter.AdapterInstallResult(
        "unchanged", "codex", expected
    )
    assert upgraded == agent_adapter.AdapterInstallResult("upgraded", "codex", expected)
    desired = agent_adapter.build_desired_adapter("codex", "2")
    old_desired = agent_adapter.build_desired_adapter("codex", "1")
    assert (expected / "SKILL.md").read_bytes() == desired.skill_md
    assert (
        expected / agent_adapter.MANAGED_ADAPTER_RECORD
    ).read_bytes() == desired.managed_record
    assert not list(expected.parent.glob(".llm-wiki-ops.*-*"))
    retained_root = expected.parent.parent / ".llmwikiops-retained"
    assert stat.S_IMODE(retained_root.stat().st_mode) == 0o700
    retained = list(retained_root.iterdir())
    assert len(retained) == 1
    assert (retained[0] / "SKILL.md").read_bytes() == old_desired.skill_md
    assert (
        retained[0] / agent_adapter.MANAGED_ADAPTER_RECORD
    ).read_bytes() == old_desired.managed_record


@pytest.mark.parametrize("kind", ("unmanaged", "drift"))
def test_adapter_installation_preserves_unmanaged_or_owner_drift(
    tmp_path: Path, kind: str
) -> None:
    destination = tmp_path / "home/.codex/skills" / ADAPTER_NAME
    desired = agent_adapter.build_desired_adapter("codex", "1")
    write_adapter_tree(destination, desired)
    if kind == "unmanaged":
        (destination / agent_adapter.MANAGED_ADAPTER_RECORD).unlink()
    else:
        (destination / "SKILL.md").write_bytes(b"owner edit\n")
    before = {path.name: path.read_bytes() for path in destination.iterdir()}

    with pytest.raises(ValueError, match="unmanaged|drift|preserve"):
        agent_adapter.install_adapter(
            "codex",
            cli_version="2",
            home=tmp_path / "home",
            environ={},
        )

    assert {path.name: path.read_bytes() for path in destination.iterdir()} == before


@pytest.mark.parametrize("state", ("current", "unmanaged", "owner-drift"))
def test_adapter_installation_read_only_outcomes_do_not_create_retention_root(
    tmp_path: Path, state: str
) -> None:
    home = tmp_path / "home"
    config_root = home / ".codex"
    destination = config_root / "skills" / ADAPTER_NAME
    desired = agent_adapter.build_desired_adapter("codex", "1")
    write_adapter_tree(destination, desired)
    if state == "unmanaged":
        (destination / agent_adapter.MANAGED_ADAPTER_RECORD).unlink()
    elif state == "owner-drift":
        (destination / "SKILL.md").write_bytes(b"owner edit\n")

    def config_snapshot() -> tuple[tuple[str, str, bytes | None, int], ...]:
        return tuple(
            sorted(
                (
                    str(path.relative_to(config_root)),
                    "directory" if path.is_dir() else "file",
                    path.read_bytes() if path.is_file() else None,
                    stat.S_IMODE(path.stat().st_mode),
                )
                for path in config_root.rglob("*")
            )
        )

    before = config_snapshot()
    if state == "current":
        result = agent_adapter.install_adapter(
            "codex",
            cli_version="1",
            home=home,
            environ={},
        )
        assert result.status == "unchanged"
    else:
        with pytest.raises(ValueError, match="unmanaged|drift|preserv"):
            agent_adapter.install_adapter(
                "codex",
                cli_version="2",
                home=home,
                environ={},
            )

    assert config_snapshot() == before
    assert not (config_root / ".llmwikiops-retained").exists()


@pytest.mark.parametrize(
    "point",
    (
        "staged-files",
        "staged-record",
        "live-moved-to-backup",
        "stage-promoted",
        "backup-removed",
    ),
)
def test_adapter_installation_checkpoint_failure_recovers_on_rerun(
    tmp_path: Path, point: str
) -> None:
    home = tmp_path / "home"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )

    class InjectedFailure(RuntimeError):
        pass

    seen: list[str] = []

    def checkpoint(name: str) -> None:
        seen.append(name)
        if name == point:
            raise InjectedFailure(point)

    with pytest.raises(InjectedFailure, match=point):
        agent_adapter.install_adapter(
            "codex",
            cli_version="2",
            home=home,
            environ={},
            checkpoint=checkpoint,
        )
    result = agent_adapter.install_adapter(
        "codex", cli_version="2", home=home, environ={}
    )

    destination = home / ".codex/skills" / ADAPTER_NAME
    desired = agent_adapter.build_desired_adapter("codex", "2")
    assert result.status in {"unchanged", "upgraded"}
    assert (destination / "SKILL.md").read_bytes() == desired.skill_md
    assert (
        agent_adapter.inspect_adapter_installation(destination, desired).status
        == "current"
    )
    order = [
        "staged-files",
        "staged-record",
        "live-moved-to-backup",
        "stage-promoted",
        "backup-removed",
    ]
    assert seen == order[: order.index(point) + 1]
    if point == "backup-removed":
        assert list((home / ".codex/.llmwikiops-retained").iterdir())


def test_adapter_installation_preserves_replaced_partial_stage_at_checkpoint(
    tmp_path: Path,
) -> None:
    retained_root = tmp_path / "home/.codex/.llmwikiops-retained"
    desired = agent_adapter.build_desired_adapter("codex", "1")
    evidence = tmp_path / "original-stage-evidence"

    class InjectedFailure(RuntimeError):
        pass

    def replace_stage(name: str) -> None:
        if name != "staged-files":
            return
        stage = next(retained_root.glob(".llmwikiops-retained-*"))
        stage.rename(evidence)
        stage.mkdir(mode=0o700)
        (stage / "SKILL.md").write_bytes(desired.skill_md)
        raise InjectedFailure(name)

    with pytest.raises(InjectedFailure, match="staged-files"):
        agent_adapter.install_adapter(
            "codex",
            cli_version="1",
            home=tmp_path / "home",
            environ={},
            checkpoint=replace_stage,
        )

    replacement = next(retained_root.glob(".llmwikiops-retained-*"))
    assert replacement.exists()
    assert (replacement / "SKILL.md").read_bytes() == desired.skill_md
    assert (evidence / "SKILL.md").read_bytes() == desired.skill_md


def test_adapter_installation_preserves_stage_replaced_during_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retained_root = tmp_path / "home/.codex/.llmwikiops-retained"
    desired = agent_adapter.build_desired_adapter("codex", "1")
    evidence = tmp_path / "write-stage-evidence"
    real_write = os.write
    swapped = False

    def swapping_write(descriptor: int, content: bytes) -> int:
        nonlocal swapped
        written = real_write(descriptor, content)
        if content == desired.skill_md and not swapped:
            stage = next(retained_root.glob(".llmwikiops-retained-*"))
            stage.rename(evidence)
            stage.mkdir(mode=0o700)
            (stage / "SKILL.md").write_bytes(desired.skill_md)
            swapped = True
        return written

    monkeypatch.setattr(os, "write", swapping_write)

    with pytest.raises(ValueError, match="changed|stage|unsafe"):
        agent_adapter.install_adapter(
            "codex",
            cli_version="1",
            home=tmp_path / "home",
            environ={},
        )

    replacement = next(retained_root.glob(".llmwikiops-retained-*"))
    assert swapped
    assert replacement.exists()
    assert (replacement / "SKILL.md").read_bytes() == desired.skill_md
    assert (evidence / "SKILL.md").read_bytes() == desired.skill_md


@pytest.mark.parametrize("failure_kind", ("partial-write", "fsync"))
def test_adapter_installation_retains_partial_stage_after_io_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    home = tmp_path / "home"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    root = home / ".codex/skills"
    failed = False
    if failure_kind == "partial-write":
        real_write = os.write
        wrote_partial = False

        def failing_write(descriptor: int, content: bytes) -> int:
            nonlocal failed, wrote_partial
            if not wrote_partial:
                wrote_partial = True
                return real_write(descriptor, content[: max(1, len(content) // 2)])
            if not failed:
                failed = True
                raise OSError(errno.ENOSPC, "injected partial write failure")
            return real_write(descriptor, content)

        monkeypatch.setattr(os, "write", failing_write)
        expected_errno = errno.ENOSPC
    else:
        real_fsync = os.fsync

        def failing_fsync(descriptor: int) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(errno.EIO, "injected fsync failure")
            real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", failing_fsync)
        expected_errno = errno.EIO

    with pytest.raises(OSError) as raised:
        agent_adapter.install_adapter(
            "codex", cli_version="2", home=home, environ={}
        )

    assert raised.value.errno == expected_errno
    assert failed
    assert not list(root.glob(".llm-wiki-ops.stage-*"))
    assert list((home / ".codex/.llmwikiops-retained").iterdir())

    rerun = agent_adapter.install_adapter(
        "codex", cli_version="2", home=home, environ={}
    )
    assert rerun.status == "upgraded"


def test_adapter_installation_retains_empty_stage_after_open_emfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    real_open = os.open
    failed = False

    def failing_stage_open(path: object, *args: object, **kwargs: object) -> int:
        nonlocal failed
        if (
            not failed
            and isinstance(path, str)
            and path.startswith(
                (".llm-wiki-ops.stage-", ".llmwikiops-retained-")
            )
        ):
            failed = True
            raise OSError(errno.EMFILE, "injected stage open failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_stage_open)
    with pytest.raises(OSError) as raised:
        agent_adapter.install_adapter(
            "codex", cli_version="1", home=home, environ={}
        )

    assert raised.value.errno == errno.EMFILE
    assert failed
    assert not list((home / ".codex/skills").glob(".llm-wiki-ops.stage-*"))
    retained = list((home / ".codex/.llmwikiops-retained").iterdir())
    assert len(retained) == 1
    assert retained[0].is_dir() and not list(retained[0].iterdir())

    rerun = agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    assert rerun.status == "installed"


def test_adapter_installation_preserves_stage_replacement_on_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    retained_root = home / ".codex/.llmwikiops-retained"
    evidence = tmp_path / "original-empty-stage"
    real_open = os.open
    replaced = False

    def replacing_stage_open(path: object, *args: object, **kwargs: object) -> int:
        nonlocal replaced
        if (
            not replaced
            and isinstance(path, str)
            and path.startswith(
                (".llm-wiki-ops.stage-", ".llmwikiops-retained-")
            )
        ):
            replaced = True
            parent = retained_root if retained_root.exists() else home / ".codex/skills"
            stage = parent / path
            stage.rename(evidence)
            stage.mkdir(mode=0o700)
            (stage / "owner-evidence").write_bytes(b"preserve\n")
            raise OSError(errno.EMFILE, "injected replaced stage failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", replacing_stage_open)
    with pytest.raises(OSError, match="replaced stage failure"):
        agent_adapter.install_adapter(
            "codex", cli_version="1", home=home, environ={}
        )

    assert replaced and evidence.is_dir()
    assert not list((home / ".codex/skills").glob(".llm-wiki-ops.stage-*"))
    replacements = [
        path
        for path in retained_root.iterdir()
        if (path / "owner-evidence").exists()
    ]
    assert len(replacements) == 1
    assert (replacements[0] / "owner-evidence").read_bytes() == b"preserve\n"


def test_retention_preserves_source_swap_without_deleting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "skills"
    retained_root = tmp_path / "retained"
    artifact_name = ".llm-wiki-ops.backup-" + "a" * 32
    artifact = root / artifact_name
    evidence = tmp_path / "source-swap-evidence"
    desired = make_desired_install()
    write_adapter_tree(artifact, desired)
    artifact.chmod(0o700)
    real_rename = agent_adapter._rename_noreplace_between

    with (
        agent_adapter._open_or_create_directory(root) as source_fd,
        agent_adapter._open_or_create_directory(retained_root) as retained_fd,
    ):
        snapshot = agent_adapter._snapshot_child(source_fd, artifact_name)

        def swapping_source(
            source_parent: int,
            source: str,
            destination_parent: int,
            destination: str,
        ) -> None:
            artifact.rename(evidence)
            write_adapter_tree(artifact, desired)
            artifact.chmod(0o700)
            real_rename(
                source_parent, source, destination_parent, destination
            )

        monkeypatch.setattr(
            agent_adapter, "_rename_noreplace_between", swapping_source
        )
        with pytest.raises(ValueError, match="changed|retained|evidence|refus"):
            agent_adapter._retain_snapshot(source_fd, retained_fd, snapshot)

    assert evidence.exists()
    assert list(retained_root.iterdir())


def test_retention_preserves_replaced_retained_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "skills"
    retained_root = tmp_path / "retained"
    artifact_name = ".llm-wiki-ops.backup-" + "b" * 32
    artifact = root / artifact_name
    evidence = tmp_path / "retained-swap-evidence"
    desired = make_desired_install()
    write_adapter_tree(artifact, desired)
    artifact.chmod(0o700)
    real_rename = agent_adapter._rename_noreplace_between

    with (
        agent_adapter._open_or_create_directory(root) as source_fd,
        agent_adapter._open_or_create_directory(retained_root) as retained_fd,
    ):
        snapshot = agent_adapter._snapshot_child(source_fd, artifact_name)

        def swapping_retained(
            source_parent: int,
            source: str,
            destination_parent: int,
            destination: str,
        ) -> None:
            real_rename(
                source_parent, source, destination_parent, destination
            )
            retained = retained_root / destination
            retained.rename(evidence)
            write_adapter_tree(retained, desired)
            retained.chmod(0o700)

        monkeypatch.setattr(
            agent_adapter, "_rename_noreplace_between", swapping_retained
        )
        with pytest.raises(ValueError, match="changed|retained|evidence|refus"):
            agent_adapter._retain_snapshot(source_fd, retained_fd, snapshot)

    assert evidence.exists()
    assert list(retained_root.iterdir())


def test_adapter_installation_fails_closed_if_retention_root_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    retained_root = home / ".codex/.llmwikiops-retained"
    evidence = tmp_path / "retention-root-evidence"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    real_rename = agent_adapter._rename_noreplace_between
    swapped = False

    def replacing_root(
        source_parent: int,
        source: str,
        destination_parent: int,
        destination: str,
    ) -> None:
        nonlocal swapped
        real_rename(source_parent, source, destination_parent, destination)
        if destination.startswith(".llmwikiops-retained-") and not swapped:
            retained_root.rename(evidence)
            retained_root.mkdir(mode=0o700)
            swapped = True

    monkeypatch.setattr(
        agent_adapter, "_rename_noreplace_between", replacing_root
    )

    with pytest.raises(ValueError, match="retention|changed|replaced|identity"):
        agent_adapter.install_adapter(
            "codex", cli_version="2", home=home, environ={}
        )

    assert swapped
    assert retained_root.exists()
    assert list(evidence.iterdir())


def test_retention_preserves_original_name_rebuilt_after_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "skills"
    retained_root = tmp_path / "retained"
    artifact_name = ".llm-wiki-ops.backup-" + "c" * 32
    artifact = root / artifact_name
    desired = make_desired_install()
    write_adapter_tree(artifact, desired)
    artifact.chmod(0o700)
    real_rename = agent_adapter._rename_noreplace_between

    with (
        agent_adapter._open_or_create_directory(root) as source_fd,
        agent_adapter._open_or_create_directory(retained_root) as retained_fd,
    ):
        snapshot = agent_adapter._snapshot_child(source_fd, artifact_name)

        def rebuilding_original(
            source_parent: int,
            source: str,
            destination_parent: int,
            destination: str,
        ) -> None:
            real_rename(
                source_parent, source, destination_parent, destination
            )
            artifact.mkdir(mode=0o700)
            (artifact / "owner-evidence").write_bytes(b"preserve\n")

        monkeypatch.setattr(
            agent_adapter, "_rename_noreplace_between", rebuilding_original
        )
        with pytest.raises(ValueError, match="rebuilt|retained|evidence|refus"):
            agent_adapter._retain_snapshot(source_fd, retained_fd, snapshot)

    assert (artifact / "owner-evidence").read_bytes() == b"preserve\n"
    assert list(retained_root.iterdir())


def test_retention_collision_exdev_and_missing_capability_preserve_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "skills"
    retained_root = tmp_path / "retained"
    artifact_name = ".llm-wiki-ops.backup-" + "d" * 32
    artifact = root / artifact_name
    desired = make_desired_install()
    write_adapter_tree(artifact, desired)
    artifact.chmod(0o700)
    token = "e" * 32
    collision = retained_root / (".llmwikiops-retained-" + token)
    collision.mkdir(parents=True, mode=0o700)
    retained_root.chmod(0o700)
    (collision / "evidence").write_bytes(b"collision\n")
    monkeypatch.setattr(agent_adapter.secrets, "token_hex", lambda size: token)

    with (
        agent_adapter._open_or_create_directory(root) as source_fd,
        agent_adapter._open_or_create_directory(retained_root) as retained_fd,
    ):
        snapshot = agent_adapter._snapshot_child(source_fd, artifact_name)
        with pytest.raises((FileExistsError, ValueError), match="exist|collision"):
            agent_adapter._retain_snapshot(source_fd, retained_fd, snapshot)

        monkeypatch.setattr(
            agent_adapter, "_same_filesystem", lambda left, right: False
        )
        with pytest.raises(OSError, match="filesystem|cross-device"):
            agent_adapter._retain_snapshot(source_fd, retained_fd, snapshot)

        monkeypatch.setattr(
            agent_adapter, "_same_filesystem", lambda left, right: True
        )

        def unsupported(*args: object) -> None:
            raise OSError(errno.ENOTSUP, "renameat2 unavailable")

        monkeypatch.setattr(
            agent_adapter, "_rename_noreplace_between", unsupported
        )
        with pytest.raises(OSError, match="unavailable"):
            agent_adapter._retain_snapshot(source_fd, retained_fd, snapshot)

    assert artifact.exists()
    assert (collision / "evidence").read_bytes() == b"collision\n"


@pytest.mark.parametrize("capability_errno", (errno.ENOSYS, errno.ENOTSUP, errno.EINVAL))
def test_adapter_upgrade_probes_rename_noreplace_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability_errno: int
) -> None:
    home = tmp_path / "home"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    config_root = home / ".codex"
    retained_root = config_root / ".llmwikiops-retained"
    retained_root.mkdir(mode=0o700, exist_ok=True)

    def config_snapshot() -> tuple[tuple[str, str, bytes | None, int], ...]:
        return tuple(
            sorted(
                (
                    str(path.relative_to(config_root)),
                    "directory" if path.is_dir() else "file",
                    path.read_bytes() if path.is_file() else None,
                    stat.S_IMODE(path.stat().st_mode),
                )
                for path in config_root.rglob("*")
            )
        )

    before = config_snapshot()
    events: list[str] = []
    real_write_stage = agent_adapter._write_stage

    def observed_write_stage(*args: object, **kwargs: object) -> object:
        events.append("stage-write")
        return real_write_stage(*args, **kwargs)

    def unsupported(*args: object) -> None:
        events.append("rename-probe")
        raise OSError(capability_errno, "injected rename capability failure")

    monkeypatch.setattr(agent_adapter, "_write_stage", observed_write_stage)
    monkeypatch.setattr(agent_adapter, "_call_atomic_noreplace", unsupported)

    with pytest.raises(OSError, match="injected rename capability failure"):
        agent_adapter.install_adapter(
            "codex", cli_version="2", home=home, environ={}
        )

    assert events == ["rename-probe"]
    assert config_snapshot() == before
    assert not list((config_root / "skills").glob(".llm-wiki-ops.stage-*"))


@pytest.mark.parametrize("existing_live", (False, True))
def test_actual_filesystem_rename_failure_leaves_stage_only_in_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_live: bool
) -> None:
    home = tmp_path / "home"
    root = home / ".codex/skills"
    old = agent_adapter.build_desired_adapter("codex", "1")
    if existing_live:
        agent_adapter.install_adapter(
            "codex", cli_version="1", home=home, environ={}
        )
    real_call = agent_adapter._call_atomic_noreplace

    def unsupported_on_real_path(
        source_parent: int,
        source: bytes,
        destination_parent: int,
        destination: bytes,
    ) -> None:
        if source:
            raise OSError(errno.EOPNOTSUPP, "filesystem rejects atomic rename")
        real_call(source_parent, source, destination_parent, destination)

    monkeypatch.setattr(
        agent_adapter, "_call_atomic_noreplace", unsupported_on_real_path
    )
    with pytest.raises(OSError) as raised:
        agent_adapter.install_adapter(
            "codex", cli_version="2", home=home, environ={}
        )

    assert raised.value.errno == errno.EOPNOTSUPP
    assert not list(root.glob(".llm-wiki-ops.stage-*"))
    live = root / ADAPTER_NAME
    if existing_live:
        assert (live / "SKILL.md").read_bytes() == old.skill_md
        assert (live / agent_adapter.MANAGED_ADAPTER_RECORD).read_bytes() == (
            old.managed_record
        )
    else:
        assert not live.exists()
    desired = agent_adapter.build_desired_adapter("codex", "2")
    retained = list((home / ".codex/.llmwikiops-retained").iterdir())
    assert len(retained) == 1
    assert (retained[0] / "SKILL.md").read_bytes() == desired.skill_md
    assert (retained[0] / agent_adapter.MANAGED_ADAPTER_RECORD).read_bytes() == (
        desired.managed_record
    )


def test_upgrade_restores_backup_when_retained_stage_promotion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    root = home / ".codex/skills"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    old = agent_adapter.build_desired_adapter("codex", "1")
    real_call = agent_adapter._call_atomic_noreplace
    real_path_calls = 0

    def fail_stage_promotion_once(
        source_parent: int,
        source: bytes,
        destination_parent: int,
        destination: bytes,
    ) -> None:
        nonlocal real_path_calls
        if source:
            real_path_calls += 1
            if real_path_calls == 2:
                assert not list(root.glob(".llm-wiki-ops.stage-*"))
                raise OSError(errno.EOPNOTSUPP, "stage promotion rejected")
        real_call(source_parent, source, destination_parent, destination)

    monkeypatch.setattr(
        agent_adapter, "_call_atomic_noreplace", fail_stage_promotion_once
    )
    with pytest.raises(OSError, match="stage promotion rejected"):
        agent_adapter.install_adapter(
            "codex", cli_version="2", home=home, environ={}
        )

    live = root / ADAPTER_NAME
    assert (live / "SKILL.md").read_bytes() == old.skill_md
    assert not list(root.glob(".llm-wiki-ops.backup-*"))
    assert not list(root.glob(".llm-wiki-ops.stage-*"))
    retained_before = tuple((home / ".codex/.llmwikiops-retained").iterdir())
    assert retained_before

    rerun = agent_adapter.install_adapter(
        "codex", cli_version="2", home=home, environ={}
    )
    assert rerun.status == "upgraded"
    assert all(path.exists() for path in retained_before)


def test_rename_noreplace_probe_passes_uncreatable_empty_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    calls: list[tuple[bytes, bytes]] = []

    def unavailable_source(
        source_parent: int,
        source: bytes,
        destination_parent: int,
        destination: bytes,
        flag: int,
    ) -> int:
        calls.append((source, destination))
        agent_adapter.ctypes.set_errno(errno.ENOENT)
        return -1

    monkeypatch.setattr(
        agent_adapter,
        "_resolve_atomic_noreplace",
        lambda: (unavailable_source, 1),
    )
    with agent_adapter._open_or_create_directory(root) as parent_fd:
        agent_adapter._probe_rename_noreplace(parent_fd, parent_fd)

    assert calls == [(b"", b"")]
    assert not list(root.iterdir())


def test_adapter_rename_probe_cannot_move_racing_owner_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    skills_root = home / ".codex/skills"
    retained_root = home / ".codex/.llmwikiops-retained"
    retained_root.mkdir(mode=0o700, exist_ok=True)
    calls: list[tuple[bytes, bytes]] = []

    def racing_rename(
        source_parent: int,
        source: bytes,
        destination_parent: int,
        destination: bytes,
        flag: int,
    ) -> int:
        calls.append((source, destination))
        evidence_fd = os.open(
            "owner-evidence",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=source_parent,
        )
        try:
            os.write(evidence_fd, b"preserve\n")
        finally:
            os.close(evidence_fd)
        if source:
            raced_fd = os.open(
                source,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=source_parent,
            )
            os.close(raced_fd)
            os.rename(
                source,
                destination,
                src_dir_fd=source_parent,
                dst_dir_fd=destination_parent,
            )
            return 0
        agent_adapter.ctypes.set_errno(errno.ENOENT)
        return -1

    monkeypatch.setattr(
        agent_adapter, "_resolve_atomic_noreplace", lambda: (racing_rename, 1)
    )

    class StopBeforeStage(RuntimeError):
        pass

    def stop_before_stage(*args: object, **kwargs: object) -> object:
        raise StopBeforeStage

    monkeypatch.setattr(agent_adapter, "_write_stage", stop_before_stage)

    with pytest.raises(StopBeforeStage):
        agent_adapter.install_adapter(
            "codex", cli_version="2", home=home, environ={}
        )

    assert calls == [(b"", b"")]
    assert (skills_root / "owner-evidence").read_bytes() == b"preserve\n"
    assert not list(retained_root.iterdir())


@pytest.mark.parametrize(
    ("platform", "symbol", "flag"),
    (("linux", "renameat2", 1), ("darwin", "renameatx_np", 0x4)),
)
def test_atomic_noreplace_resolver_selects_platform_primitive_and_ctypes_signature(
    platform: str, symbol: str, flag: int
) -> None:
    class FakeRename:
        argtypes: tuple[object, ...] | None = None
        restype: object | None = None

    function = FakeRename()
    library = type("FakeLibrary", (), {symbol: function})()

    resolved, resolved_flag = agent_adapter._resolve_atomic_noreplace(
        platform, library
    )

    assert resolved is function
    assert resolved_flag == flag
    assert function.argtypes == (
        agent_adapter.ctypes.c_int,
        agent_adapter.ctypes.c_char_p,
        agent_adapter.ctypes.c_int,
        agent_adapter.ctypes.c_char_p,
        agent_adapter.ctypes.c_uint,
    )
    assert function.restype is agent_adapter.ctypes.c_int


@pytest.mark.parametrize("platform", ("darwin", "unsupported-test-os"))
def test_atomic_noreplace_resolver_rejects_missing_or_unsupported_primitive(
    platform: str,
) -> None:
    with pytest.raises(OSError) as raised:
        agent_adapter._resolve_atomic_noreplace(platform, object())

    assert raised.value.errno == errno.ENOTSUP


def test_adapter_installation_rejects_unsupported_platform_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    root = home / ".codex/skills"
    live_before = {
        path.name: path.read_bytes() for path in (root / ADAPTER_NAME).iterdir()
    }
    monkeypatch.setattr(agent_adapter.sys, "platform", "unsupported-test-os")

    def forbidden_stage(*args: object, **kwargs: object) -> object:
        raise AssertionError("unsupported platforms must fail before staging")

    monkeypatch.setattr(agent_adapter, "_write_stage", forbidden_stage)

    with pytest.raises(OSError) as raised:
        agent_adapter.install_adapter(
            "codex", cli_version="2", home=home, environ={}
        )

    assert raised.value.errno == errno.ENOTSUP
    assert not list(root.glob(".llm-wiki-ops.stage-*"))
    assert {
        path.name: path.read_bytes() for path in (root / ADAPTER_NAME).iterdir()
    } == live_before


def test_adapter_installation_never_overwrites_racing_live_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "home/.codex/skills"
    live = root / ADAPTER_NAME
    retained_root = tmp_path / "home/.codex/.llmwikiops-retained"
    real_rename = agent_adapter._rename_noreplace_between
    raced = False
    raced_identity: tuple[int, int] | None = None

    def racing_rename(
        source_parent_fd: int,
        source: str,
        destination_parent_fd: int,
        destination: str,
    ) -> None:
        nonlocal raced, raced_identity
        if destination == ADAPTER_NAME and not raced:
            live.mkdir()
            metadata = live.stat()
            raced_identity = (metadata.st_dev, metadata.st_ino)
            raced = True
        real_rename(
            source_parent_fd, source, destination_parent_fd, destination
        )

    monkeypatch.setattr(agent_adapter, "_rename_noreplace_between", racing_rename)

    with pytest.raises(ValueError, match="live|race|exist|preserv"):
        agent_adapter.install_adapter(
            "codex",
            cli_version="1",
            home=tmp_path / "home",
            environ={},
        )

    assert raced
    metadata = live.stat()
    assert (metadata.st_dev, metadata.st_ino) == raced_identity
    assert not list(live.iterdir())
    assert list(retained_root.glob(".llmwikiops-retained-*"))


@pytest.mark.parametrize("flag", ("O_NOFOLLOW", "O_DIRECTORY"))
def test_adapter_installation_inspection_fails_closed_without_posix_open_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    destination = tmp_path / "skills" / ADAPTER_NAME
    desired = make_desired_install()
    write_adapter_tree(destination, desired)
    monkeypatch.delattr(os, flag)

    inspected = agent_adapter.inspect_adapter_installation(destination, desired)

    assert inspected.status == "error"
    assert flag in (inspected.error or "")


def test_adapter_installation_preserves_ambiguous_recovery_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "home/.codex/skills"
    ambiguous = root / (".llm-wiki-ops.stage-" + "a" * 32)
    ambiguous.mkdir(parents=True)
    (ambiguous / "evidence").write_bytes(b"do not delete")

    with pytest.raises(ValueError, match="ambiguous|recovery|preserve"):
        agent_adapter.install_adapter(
            "codex",
            cli_version="1",
            home=tmp_path / "home",
            environ={},
        )

    assert (ambiguous / "evidence").read_bytes() == b"do not delete"


def test_adapter_installation_recovers_verified_stage_beside_clean_old_live(
    tmp_path: Path,
) -> None:
    root = tmp_path / "home/.codex/skills"
    live = root / ADAPTER_NAME
    old = agent_adapter.build_desired_adapter("codex", "1")
    desired = agent_adapter.build_desired_adapter("codex", "2")
    write_adapter_tree(live, old)
    stage = root / (".llm-wiki-ops.stage-" + "a" * 32)
    write_adapter_tree(stage, desired)
    stage.chmod(0o700)

    result = agent_adapter.install_adapter(
        "codex",
        cli_version="2",
        home=tmp_path / "home",
        environ={},
    )

    assert result.status == "upgraded"
    assert (live / "SKILL.md").read_bytes() == desired.skill_md
    assert not stage.exists()


def test_adapter_installation_preserves_recovery_tree_with_wrong_mode(
    tmp_path: Path,
) -> None:
    desired = agent_adapter.build_desired_adapter("codex", "1")
    stage = tmp_path / "home/.codex/skills" / (".llm-wiki-ops.stage-" + "a" * 32)
    write_adapter_tree(stage, desired)
    stage.chmod(0o755)

    with pytest.raises(ValueError, match="ambiguous|recovery|preserv"):
        agent_adapter.install_adapter(
            "codex",
            cli_version="1",
            home=tmp_path / "home",
            environ={},
        )

    assert stage.exists()


def test_adapter_installation_avoids_path_recursive_mutation_apis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("forbidden path-based recursive mutation")

    monkeypatch.setattr(Path, "mkdir", forbidden)
    destination_root = tmp_path / "prepared/.codex/skills"
    os.makedirs(destination_root)
    result = agent_adapter.install_adapter(
        "codex",
        cli_version="1",
        home=tmp_path / "prepared",
        environ={},
    )
    assert result.status == "installed"


def test_adapter_installation_never_unlinks_or_removes_managed_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("managed trees must only be retained")

    monkeypatch.setattr(os, "unlink", forbidden)
    monkeypatch.setattr(os, "rmdir", forbidden)

    result = agent_adapter.install_adapter(
        "codex", cli_version="2", home=home, environ={}
    )

    assert result.status == "upgraded"
    assert list((home / ".codex/.llmwikiops-retained").iterdir())


def test_adapter_installation_rejects_unsafe_retention_root_and_ignores_contents(
    tmp_path: Path
) -> None:
    home = tmp_path / "home"
    agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    retained_root = home / ".codex/.llmwikiops-retained"
    retained_root.mkdir(mode=0o700, exist_ok=True)
    ignored = retained_root / "owner-evidence"
    ignored.mkdir(mode=0o700)
    (ignored / "unknown").write_bytes(b"preserve\n")

    unchanged = agent_adapter.install_adapter(
        "codex", cli_version="1", home=home, environ={}
    )
    assert unchanged.status == "unchanged"
    assert (ignored / "unknown").read_bytes() == b"preserve\n"

    retained_root.chmod(0o755)
    with pytest.raises(ValueError, match="retention|mode|0700|unsafe"):
        agent_adapter.install_adapter(
            "codex", cli_version="2", home=home, environ={}
        )
    assert (ignored / "unknown").read_bytes() == b"preserve\n"

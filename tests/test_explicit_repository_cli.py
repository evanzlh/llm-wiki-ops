from __future__ import annotations

import json
import os
import subprocess
import sys
import shlex
from pathlib import Path

import pytest

import obsidian_wiki.cli as cli
from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki.config import load_portable_config, resolve_repository
from obsidian_wiki.portable import setup_portable_repo
from obsidian_wiki.portable_manifest import (
    ManifestPreconditionError,
    ShardedManifest,
)
from obsidian_wiki.transaction import TransactionManager
from obsidian_wiki.trust import (
    TRUST_LEDGER_RELATIVE_PATH,
    build_trust_ledger,
    write_trust_ledger,
)


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_REPOSITORY_AWARE_COMMANDS = {
    "info",
    "doctor",
    "check",
    "repo",
    "transaction",
    "manifest",
    "hot",
    "batch-plan",
    "graph-analyse",
    "cache-check",
    "lint",
    "trust-record",
    "trust-check",
    "query",
    "context-pack",
    "context",
}


def setup_repository(root: Path) -> Path:
    (root / ".llmwikiops").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / ".skills").mkdir()
    (root / ".llmwikiops" / "config.toml").write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".llmwikiops/local"
''',
        encoding="utf-8",
    )
    (root / "wiki" / ".manifest.json").write_text(
        '{"schema_version":2,"storage":"sharded","entries":".manifest/sources"}\n',
        encoding="utf-8",
    )
    (root / "wiki" / "log.md").write_text(
        "# Operation Log\n\n",
        encoding="utf-8",
    )
    return root


def run_cli(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki", *arguments],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def test_repository_aware_command_scope_is_closed() -> None:
    assert cli._REPOSITORY_AWARE_COMMANDS == EXPECTED_REPOSITORY_AWARE_COMMANDS


@pytest.mark.parametrize("selector", ["-C", "--repo"])
def test_info_selects_an_external_exact_root(
    tmp_path: Path, selector: str
) -> None:
    business = tmp_path / "business"
    repository = tmp_path / "knowledge"
    business.mkdir()
    setup_repository(repository)

    result = run_cli(business, selector, str(repository), "info", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime"]["root"] == str(repository)


def test_explicit_repository_never_falls_back_to_an_ancestor(tmp_path: Path) -> None:
    repository = setup_repository(tmp_path / "knowledge")
    child = repository / "business"
    child.mkdir()

    result = run_cli(tmp_path, "-C", str(child), "info", "--json")

    assert result.returncode == 1
    runtime = json.loads(result.stdout)["runtime"]
    assert runtime["status"] == "error"
    assert "must have direct" in runtime["error"]
    assert str(child) in runtime["error"]


def test_explicit_repository_wins_over_invocation_cwd_repository(
    tmp_path: Path,
) -> None:
    invocation_repository = setup_repository(tmp_path / "business")
    selected_repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(
        invocation_repository,
        "-C",
        str(selected_repository),
        "info",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime"]["root"] == str(selected_repository)


@pytest.mark.parametrize(
    "arguments",
    (
        ("-C", "{root}", "--repo", "{root}", "info"),
        ("--repo", "{root}", "-C", "{root}", "info"),
        ("-C", "{root}", "-C", "{root}", "info"),
    ),
)
def test_repository_option_may_be_supplied_only_once(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(
        tmp_path,
        *(token.format(root=repository) for token in arguments),
    )

    assert result.returncode == 2
    assert "repository option may be supplied only once" in result.stderr


def test_repository_option_after_subcommand_is_rejected(tmp_path: Path) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(tmp_path, "info", "-C", str(repository), "--json")

    assert result.returncode == 2


@pytest.mark.parametrize("selector", ["-C", "--repo"])
@pytest.mark.parametrize("version_option", ["-V", "--version"])
def test_repository_selector_is_rejected_for_version_even_with_aware_command(
    tmp_path: Path,
    selector: str,
    version_option: str,
) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(
        tmp_path,
        selector,
        str(repository),
        version_option,
        "info",
    )

    assert result.returncode == 2
    assert "repository option is not valid for this command" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    (
        ("-C", "{root}", "setup", "--help"),
        ("--repo={root}", "list", "--help"),
        ("-C{root}", "--version", "info", "--help"),
        ("--repo", "{root}", "-V", "doctor", "--help"),
        ("--help", "-C{root}", "setup"),
        ("-C{root}", "info", "--version", "--help"),
    ),
)
def test_help_cannot_bypass_repository_selector_scope(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(
        tmp_path,
        *(token.format(root=repository) for token in arguments),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "repository option is not valid for this command" in result.stderr
    assert "show this help message" not in result.stderr
    assert cli.version_label() not in result.stderr


def test_ordinary_and_repository_aware_help_remain_available(tmp_path: Path) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    ordinary = run_cli(tmp_path, "setup", "--help")
    aware = run_cli(tmp_path, "-C", str(repository), "info", "--help")

    assert ordinary.returncode == aware.returncode == 0
    assert ordinary.stderr == aware.stderr == ""
    assert ordinary.stdout.startswith("usage: llmwikiops setup")
    assert aware.stdout.startswith("usage: llmwikiops info")


@pytest.mark.parametrize(
    "arguments",
    (
        ("--version", "--help"),
        ("--version", "garbage"),
        ("-V", "garbage", "--help"),
    ),
)
def test_version_remains_an_immediate_first_action(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    result = run_cli(tmp_path, *arguments)

    assert result.returncode == 0
    assert result.stdout == f"{cli.version_label()}\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        (("--", "info", "--repo={root}", "--help"), "must precede"),
        (("--", "setup", "-C", "{root}", "--help"), "must precede"),
        (
            ("-C", "{root}", "--", "info", "--repo", "{other}", "--help"),
            "only once",
        ),
        (
            ("-C", "{root}", "--", "setup", "--help"),
            "not valid for this command",
        ),
        (
            (
                "-C",
                "{root}",
                "repo",
                "--",
                "sync-skills",
                "--repo={other}",
                "--help",
            ),
            "only once",
        ),
        (
            ("repo", "--", "sync-skills", "-C", "{root}", "--help"),
            "must precede",
        ),
        (
            (
                "-C",
                "{root}",
                "repo",
                "--",
                "sync-skills",
                "--version",
                "--help",
            ),
            "not valid for this command",
        ),
        (
            (
                "transaction",
                "--",
                "commit",
                "--repo={root}",
                "--json",
            ),
            "must precede",
        ),
        (
            (
                "-C",
                "{root}",
                "transaction",
                "--",
                "commit",
                "--repo",
                "{other}",
                "--json",
            ),
            "only once",
        ),
    ),
)
def test_subparser_separators_cannot_hide_repository_selector_errors(
    tmp_path: Path,
    arguments: tuple[str, ...],
    message: str,
) -> None:
    repository = setup_repository(tmp_path / "knowledge")
    other = tmp_path / "other"

    result = run_cli(
        tmp_path,
        *(token.format(root=repository, other=other) for token in arguments),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert message in result.stderr
    assert "show this help message" not in result.stderr
    assert cli.version_label() not in result.stderr


def test_leaf_positional_separator_keeps_repository_like_operand_literal(
    tmp_path: Path,
) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(tmp_path, "-C", str(repository), "query", "--", "-Cliteral")

    assert result.returncode == 2
    assert "repository option" not in result.stderr
    assert "query does not match" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    (
        ("--rep={root}", "setup", "--help"),
        ("--rep", "{root}", "setup", "--help"),
    ),
)
def test_abbreviated_repository_options_are_rejected_before_help(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(
        tmp_path,
        *(token.format(root=repository) for token in arguments),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "show this help message" not in result.stderr


@pytest.mark.parametrize(
    "arguments",
    (
        ("-VC{root}", "setup", "--help"),
        ("info", "-C{root}", "--help"),
    ),
)
def test_clustered_or_late_repository_selector_cannot_hide_behind_help(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(
        tmp_path,
        *(token.format(root=repository) for token in arguments),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "show this help message" not in result.stderr


SHORT_CLUSTER_PREFIXES = (
    "h",
    "V",
    "hV",
    "Vh",
    "hh",
    "VV",
    "hVh",
    "VhV",
)


@pytest.mark.parametrize("prefix", SHORT_CLUSTER_PREFIXES)
@pytest.mark.parametrize(
    "placement",
    ("before-independent", "before-aware", "after-selector"),
)
def test_every_global_short_flag_cluster_containing_repository_is_rejected(
    tmp_path: Path,
    prefix: str,
    placement: str,
) -> None:
    repository = setup_repository(tmp_path / "knowledge")
    other = tmp_path / "other"
    cluster = f"-{prefix}C{other}"
    if placement == "before-independent":
        arguments = (cluster, "setup")
    elif placement == "before-aware":
        arguments = (cluster, "info")
    else:
        arguments = ("-C", str(repository), cluster, "info")

    result = run_cli(tmp_path, *arguments)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "show this help message" not in result.stderr
    assert cli.version_label() not in result.stderr


def test_characters_after_leading_repository_short_option_are_its_value(
    tmp_path: Path,
) -> None:
    business = tmp_path / "business"
    business.mkdir()
    repository = setup_repository(business / "hV-repository")

    result = run_cli(business, "-ChV-repository", "info", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime"]["root"] == str(repository)


@pytest.mark.parametrize("repository_name", ("-", "-1", "-1.5", "-.5"))
def test_two_token_repository_accepts_dash_prefixed_path_values(
    tmp_path: Path,
    repository_name: str,
) -> None:
    business = tmp_path / "business"
    business.mkdir()
    repository = setup_repository(business / repository_name)

    result = run_cli(business, "-C", repository_name, "info", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime"]["root"] == str(repository)


@pytest.mark.parametrize(
    ("value", "is_option"),
    (
        ("-", False),
        ("-1", False),
        ("-1.5", False),
        ("-.5", False),
        ("--", True),
        ("--json", True),
        ("--arbitrary", True),
        ("-x", True),
        ("-1.", True),
        ("-1e3", True),
        ("-Cother", True),
        ("-hVCother", True),
    ),
)
def test_repository_value_option_classification_matches_argparse(
    value: str,
    is_option: bool,
) -> None:
    assert cli._repository_value_is_option(value) is is_option


@pytest.mark.parametrize("missing_value", ("--", "--json", "-x"))
def test_malformed_repository_value_keeps_transaction_recovery_rootless(
    tmp_path: Path,
    missing_value: str,
) -> None:
    result = run_cli(
        tmp_path,
        "-C",
        missing_value,
        "transaction",
        "commit",
        "--json",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "transaction-error"
    assert "argument -C/--repo: expected one argument" in payload["error"]["message"]
    assert payload["recovery"]["inspect_command"] == (
        "llmwikiops transaction list --json"
    )
    assert "llmwikiops -C" not in result.stdout


def test_repository_scanner_topology_matches_argparse_parser() -> None:
    top_level, nested = cli._parser_topology(cli.build_parser())

    assert cli._TOP_LEVEL_COMMANDS == top_level
    assert cli._NESTED_SUBCOMMANDS == nested


REPOSITORY_INDEPENDENT_INVOCATIONS = (
    ("setup", "created"),
    ("list",),
    ("ast-extract", "input.py"),
    ("cache-hash", "input.md"),
    ("sessions-build",),
    ("sessions-query", "sentinel"),
    ("sessions-show", "sentinel"),
    ("sessions-clusters",),
    ("sessions-name", "--from", "names.json"),
    ("--version",),
    ("-V",),
)


@pytest.mark.parametrize("invocation", REPOSITORY_INDEPENDENT_INVOCATIONS)
def test_repository_independent_commands_reject_selector_before_running(
    tmp_path: Path, invocation: tuple[str, ...]
) -> None:
    repository = setup_repository(tmp_path / "knowledge")
    business = tmp_path / "business"
    business.mkdir()
    before = sorted(str(path.relative_to(business)) for path in business.rglob("*"))

    result = run_cli(business, "-C", str(repository), *invocation)

    assert result.returncode == 2
    assert "repository option is not valid for this command" in result.stderr
    assert sorted(str(path.relative_to(business)) for path in business.rglob("*")) == before


def test_query_describe_validates_explicit_repository_first(tmp_path: Path) -> None:
    business = tmp_path / "business"
    unconfigured = tmp_path / "not-a-wiki"
    business.mkdir()
    unconfigured.mkdir()

    result = run_cli(
        business,
        "-C",
        str(unconfigured),
        "query",
        "--describe",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "config-error"
    assert "must have direct" in payload["error"]["message"]


@pytest.mark.parametrize("question", ("transformer", ""))
def test_query_error_follow_up_commands_keep_explicit_repository(
    tmp_path: Path,
    question: str,
) -> None:
    business = tmp_path / "business"
    business.mkdir()
    repository = setup_repository(tmp_path / "knowledge root")

    result = run_cli(business, "-C", "../knowledge root", "query", question)

    assert result.returncode == 2
    commands = [
        shlex.split(line.strip())
        for line in result.stderr.splitlines()
        if line.strip().startswith("llmwikiops ")
    ]
    assert commands
    for command in commands:
        assert command[:4] == ["llmwikiops", "-C", str(repository), "query"]


def test_implicit_query_rewrite_commands_remain_byte_identical() -> None:
    assert cli._query_recovery_forms("transformer") == (
        "llmwikiops query 'find \"transformer\"'",
        "llmwikiops query --mode find --term=transformer",
    )


def test_empty_repository_selector_is_a_structured_configuration_error(
    tmp_path: Path,
) -> None:
    business = tmp_path / "business"
    business.mkdir()

    result = run_cli(business, "-C", "", "info", "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    assert "explicit repository path must be non-empty" in result.stdout
    assert "Traceback" not in result.stdout


def test_explicit_transaction_config_failure_keeps_repository_in_recovery(
    tmp_path: Path,
) -> None:
    business = tmp_path / "business"
    unconfigured = tmp_path / "not-a-wiki"
    business.mkdir()
    unconfigured.mkdir()

    result = run_cli(
        business,
        "-C",
        str(unconfigured),
        "transaction",
        "list",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["recovery"]["inspect_command"] == (
        f"{shlex.join(['llmwikiops', '-C', str(unconfigured)])} "
        "transaction list --json"
    )


def test_explicit_transaction_parse_failure_keeps_structured_recovery(
    tmp_path: Path,
) -> None:
    business = tmp_path / "business"
    repository = setup_repository(tmp_path / "knowledge")
    business.mkdir()

    result = run_cli(
        business,
        "-C",
        str(repository),
        "transaction",
        "commit",
        "--json",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "transaction-error"
    assert payload["recovery"]["inspect_command"] == (
        f"{shlex.join(['llmwikiops', '-C', str(repository)])} "
        "transaction list --json"
    )


def test_explicit_query_parse_failure_keeps_structured_json_error(
    tmp_path: Path,
) -> None:
    business = tmp_path / "business"
    repository = setup_repository(tmp_path / "knowledge")
    business.mkdir()

    result = run_cli(
        business,
        "--repo",
        str(repository),
        "query",
        "--j",
        "--json",
    )

    assert result.returncode == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "invalid_query_arguments"


@pytest.mark.parametrize(
    "selector",
    ("--repo={repository}", "-C{repository}"),
)
def test_attached_selector_transaction_parse_failure_keeps_bound_recovery(
    tmp_path: Path,
    selector: str,
) -> None:
    business = tmp_path / "business"
    repository = setup_repository(tmp_path / "knowledge")
    business.mkdir()
    relative = os.path.relpath(repository, business)

    result = run_cli(
        business,
        selector.format(repository=relative),
        "transaction",
        "commit",
        "--json",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "transaction-error"
    assert payload["recovery"]["inspect_command"] == (
        f"{shlex.join(['llmwikiops', '-C', str(repository)])} "
        "transaction list --json"
    )


@pytest.mark.parametrize(
    "selector",
    ("--repo={repository}", "-C{repository}"),
)
def test_attached_selector_query_parse_failure_keeps_structured_json(
    tmp_path: Path,
    selector: str,
) -> None:
    business = tmp_path / "business"
    repository = setup_repository(tmp_path / "knowledge")
    business.mkdir()

    result = run_cli(
        business,
        selector.format(repository=repository),
        "query",
        "--j",
        "--json",
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["error"]["code"] == (
        "invalid_query_arguments"
    )


def test_empty_attached_repository_is_a_structured_configuration_error(
    tmp_path: Path,
) -> None:
    business = tmp_path / "business"
    business.mkdir()

    result = run_cli(
        business,
        "--repo=",
        "transaction",
        "list",
        "--json",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "config-error"
    assert "explicit repository path must be non-empty" in payload["error"]["message"]


@pytest.mark.parametrize(
    "arguments",
    (
        ("-C{root}", "--repo", "{root}", "info"),
        ("--repo={root}", "-C{root}", "info"),
        ("-C{root}", "--repo={root}", "setup", "--help"),
        ("-C{root}", "info", "--repo={root}", "--help"),
        ("-C{root}", "info", "--rep={root}", "--help"),
        ("-C{root}", "info", "-VC{root}", "--help"),
    ),
)
def test_repeated_attached_repository_mixtures_are_rejected_once(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(
        tmp_path,
        *(token.format(root=repository) for token in arguments),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "repository option may be supplied only once" in result.stderr
    assert "show this help message" not in result.stderr


@pytest.mark.parametrize(
    "arguments",
    (
        ("-C", "--version", "transaction", "commit", "--json"),
        ("--repo", "--help", "info"),
        ("-C", "-hV", "transaction", "commit", "--json"),
        ("-C", "-Vh", "transaction", "commit", "--json"),
        ("-C", "--repo={root}", "info", "--help"),
    ),
)
def test_option_token_is_never_consumed_as_repository_value(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    repository = setup_repository(tmp_path / "knowledge")

    result = run_cli(
        tmp_path,
        *(token.format(root=repository) for token in arguments),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "expected one argument" in result.stderr
    assert "transaction list --json" not in result.stderr
    assert "show this help message" not in result.stderr


def test_main_resolves_explicit_repository_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = setup_repository(tmp_path / "knowledge")
    config = load_portable_config(
        repository / ".llmwikiops" / "config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    calls: list[Path] = []

    def tracked_resolve(root: Path, **_: object):
        calls.append(root)
        return config

    monkeypatch.setattr(cli, "resolve_repository", tracked_resolve)

    assert cli.main(["-C", str(repository), "info", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["runtime"]["root"] == str(repository)
    assert calls == [repository]


REPOSITORY_AWARE_INVOCATIONS = (
    ("info", "--json"),
    ("doctor", "--json"),
    ("check", "--json"),
    ("repo", "sync-skills", "--json"),
    ("repo", "upgrade-skills"),
    ("transaction", "list", "--json"),
    ("manifest", "resolve-conflict", "--keep-live", "--json"),
    ("hot", "status", "--json"),
    ("hot", "inputs", "--json"),
    ("hot", "mark-current", "--json"),
    ("batch-plan", "--pretty"),
    ("graph-analyse", "--pretty"),
    ("cache-check", "sources/input.md", "--json"),
    ("lint", "--json"),
    (
        "trust-record",
        "--all",
        "--reviewed-at",
        "2026-08-18T00:00:00Z",
        "--approved",
        "--json",
    ),
    ("trust-check", "--json"),
    ("query", "--mode", "find", "--term", "sentinel", "--json"),
    ("context-pack", "sentinel", "--json"),
    ("context", "sentinel", "--json"),
)


def setup_full_repository(root: Path, marker: str) -> Path:
    setup_portable_repo(root, version=__version__, source_skills=cli.skills_dir())
    (root / "sources" / "input.md").write_text(marker, encoding="utf-8")
    page = root / "wiki" / "concepts" / f"{marker}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f'''---
title: {marker}
category: concepts
tags: []
created: 2026-08-18
updated: 2026-08-18
base_confidence: 0.80
lifecycle: reviewed
sources:
  - sources/input.md
---
# {marker}
''',
        encoding="utf-8",
    )
    config = resolve_repository(
        root,
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    ShardedManifest(config).upsert(
        root / "sources" / "input.md",
        pages=[page.relative_to(config.vault).as_posix()],
        compiled_at="2026-08-18T00:00:00Z",
    )
    ledger = build_trust_ledger(
        config.vault,
        reviewed_at="2026-08-18T00:00:00Z",
    )
    write_trust_ledger(
        config.vault / TRUST_LEDGER_RELATIVE_PATH,
        ledger,
        vault=config.vault,
        repository_root=config.root,
        root_identity=config.root_identity,
    )
    return root


@pytest.mark.parametrize("explicit", (False, True))
def test_repo_skill_drift_follow_up_keeps_repository_binding(
    tmp_path: Path,
    explicit: bool,
) -> None:
    repository = tmp_path / "knowledge root"
    setup_portable_repo(
        repository,
        version=__version__,
        source_skills=cli.skills_dir(),
    )
    (repository / ".claude" / "skills" / "wiki-digest" / "SKILL.md").unlink()
    business = tmp_path / "business"
    business.mkdir()
    if explicit:
        cwd = business
        arguments = ("-C", "../knowledge root", "repo", "sync-skills")
        prefix = shlex.join(("llmwikiops", "-C", str(repository)))
    else:
        cwd = repository
        arguments = ("repo", "sync-skills")
        prefix = "llmwikiops"

    result = run_cli(cwd, *arguments)

    assert result.returncode == 1
    assert (
        f"Run `{prefix} repo sync-skills --apply` to rebuild all mirrors."
        in result.stdout
    )


def tree_snapshot(root: Path) -> tuple[tuple[str, int, bytes | None], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.lstat().st_mode,
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
        )
        for path in sorted(root.rglob("*"))
    )


def prepare_manifest_conflict(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import obsidian_wiki.portable_manifest as manifest_module

    config = resolve_repository(
        repository,
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    store = ShardedManifest(config)
    source = repository / "sources" / "input.md"
    store.upsert(source)
    target = store.entry_path("sources/input.md")

    def interpose(step: str) -> None:
        if step == "reserved":
            target.write_bytes(b"owner-selected-live\n")

    monkeypatch.setattr(manifest_module, "_manifest_fault_point", interpose)
    with pytest.raises(ManifestPreconditionError):
        store.upsert(source, compiled_at="2026-08-18T00:00:00Z")


@pytest.mark.parametrize("invocation", REPOSITORY_AWARE_INVOCATIONS)
def test_repository_aware_commands_use_only_selected_repository(
    tmp_path: Path,
    invocation: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    business = setup_full_repository(tmp_path / "business", "business-marker")
    repository = setup_full_repository(tmp_path / "knowledge", "sentinel")
    if invocation[0] == "manifest":
        prepare_manifest_conflict(repository, monkeypatch)
    business_before = tree_snapshot(business)

    result = run_cli(business, "-C", str(repository), *invocation)

    assert result.returncode == 0, (invocation, result.stdout, result.stderr)
    assert "repository option is not valid" not in result.stderr
    assert "repository not configured" not in result.stdout + result.stderr
    assert tree_snapshot(business) == business_before


TRANSACTION_OPERATIONS = (
    "begin",
    "list",
    "delete",
    "validate",
    "commit",
    "retry",
    "restore",
    "discard",
    "abort",
)


def prepare_transaction_operation(repository: Path, operation: str) -> str:
    if operation == "begin":
        return ""
    config = resolve_repository(
        repository,
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    manager = TransactionManager(config)
    transaction_id = "tx-explicit"
    record = manager.begin(
        [repository / "sources" / "input.md"],
        transaction_id=transaction_id,
    )
    if operation in {"restore", "discard"}:
        manager.commit(transaction_id)
    elif operation == "retry":
        metadata = record.workspace / "metadata.json"
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        payload["status"] = "failed"
        payload["residual_postimages"] = {}
        payload["rollback_exclusions"] = {}
        metadata.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manager.lock_path.unlink()
    return transaction_id


def transaction_invocation(operation: str, transaction_id: str) -> tuple[str, ...]:
    if operation == "begin":
        return ("begin", "--source", "sources/input.md", "--json")
    if operation == "list":
        return ("list", "--json")
    if operation == "delete":
        return ("delete", transaction_id, "concepts/unused.md", "--json")
    return (operation, transaction_id, "--json")


def recovery_commands(value: object) -> list[str]:
    commands: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"command", "inspect_command"} and isinstance(item, str):
                commands.append(item)
            else:
                commands.extend(recovery_commands(item))
    elif isinstance(value, list):
        for item in value:
            commands.extend(recovery_commands(item))
    return commands


@pytest.mark.parametrize("operation", TRANSACTION_OPERATIONS)
def test_every_transaction_operation_stays_bound_to_explicit_repository(
    tmp_path: Path,
    operation: str,
) -> None:
    business = setup_full_repository(tmp_path / "business", "business-marker")
    repository = setup_full_repository(tmp_path / "knowledge", "sentinel")
    transaction_id = prepare_transaction_operation(repository, operation)
    business_before = tree_snapshot(business)

    result = run_cli(
        business,
        "-C",
        str(repository),
        "transaction",
        *transaction_invocation(operation, transaction_id),
    )

    assert result.returncode == 0, (operation, result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert tree_snapshot(business) == business_before
    if isinstance(payload, dict) and "workspace" in payload:
        assert Path(payload["workspace"]).is_relative_to(repository)
    prefix = f"{shlex.join(['llmwikiops', '-C', str(repository)])} transaction "
    commands = recovery_commands(payload)
    assert all(command.startswith(prefix) for command in commands)
    if operation == "list":
        assert commands

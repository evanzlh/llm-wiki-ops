from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import cli


ROOT = Path(__file__).resolve().parents[1]
SPECIAL_WORKFLOW_SKILLS = {
    "graph-colorize",
    "obsidian-layout-adjustment",
    "vault-skill-factory",
    "wiki-context-pack",
    "wiki-digest",
    "wiki-export",
    "wiki-narrate",
    "wiki-query",
}
REMOVED_SKILL_PATHS = {
    "obsidian_wiki/_data/skills/memory-bridge",
    "obsidian_wiki/_data/skills/wiki-dashboard",
    "obsidian_wiki/_data/skills/wiki-stage-commit",
    "obsidian_wiki/_data/skills/wiki-switch",
}


def run_cli(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )


def test_setup_directory_creates_portable_repository(tmp_path: Path) -> None:
    target = tmp_path / "knowledge"

    result = run_cli(tmp_path / "home", tmp_path, "setup", str(target))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert f"Repository scaffolded at {target.absolute()}" in result.stdout
    assert f"Open {target.absolute() / 'wiki'} in Obsidian" in result.stdout
    assert (target / ".obsidian-wiki/config.toml").is_file()


def test_setup_defaults_to_current_directory(tmp_path: Path) -> None:
    target = tmp_path / "knowledge"
    target.mkdir()

    result = run_cli(tmp_path / "home", target, "setup")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert f"Repository scaffolded at {target.absolute()}" in result.stdout
    assert (target / ".obsidian-wiki/config.toml").is_file()


def test_bare_cli_prints_help_without_writing_repository_state(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()

    result = run_cli(tmp_path / "home", work)

    assert result.returncode == 0, result.stderr
    assert "usage: obsidian-wiki" in result.stdout
    assert "setup" in result.stdout
    assert "portable repository setup and maintenance" in result.stdout.lower()
    assert result.stderr == ""
    assert not (work / ".obsidian-wiki").exists()


def test_cli_has_no_global_agent_installation_surface() -> None:
    assert not hasattr(cli, "GLOBAL_AGENT_DIRS")
    assert not hasattr(cli, "_agent_install_payload")
    assert cli.__doc__ is not None
    assert "portable repository setup and maintenance" in cli.__doc__.lower()


@pytest.mark.parametrize(
    ("command", "legacy_label", "expected_returncode"),
    [("info", "agent installs", 0), ("doctor", "agent-installs", 1)],
)
def test_inspection_commands_do_not_report_global_agent_installations(
    command: str, legacy_label: str, expected_returncode: int, tmp_path: Path
) -> None:
    result = run_cli(tmp_path / "home", tmp_path, command)

    assert result.returncode == expected_returncode
    assert legacy_label not in result.stdout.lower()
    assert "Traceback" not in result.stderr


def test_portable_info_ignores_residual_legacy_home_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    setup = run_cli(home, tmp_path, "setup", str(repository))
    assert setup.returncode == 0, setup.stderr
    legacy_config = home / ".obsidian-wiki/config"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text(
        'OBSIDIAN_VAULT_PATH="/tmp/legacy-vault"\n', encoding="utf-8"
    )

    result = run_cli(home, repository / "wiki", "info", "--json")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "Traceback" not in result.stderr
    runtime = json.loads(result.stdout)["runtime"]
    assert runtime["status"] == "resolved"
    assert runtime["root"] == str(repository)


def test_portable_commands_do_not_emit_global_setup_warnings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    setup = run_cli(home, tmp_path, "setup", str(repository))
    assert setup.returncode == 0, setup.stderr

    result = run_cli(home, repository, "list")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "setup has never been run" not in result.stderr
    assert "setup --vault" not in result.stderr


@pytest.mark.parametrize(
    "legacy_args",
    [
        ["--portable"],
        ["--vault", "vault"],
        ["--project", "project"],
        ["--project-only"],
        ["--copy"],
        ["--remote", "https://example.test/wiki.git"],
    ],
)
def test_setup_rejects_removed_arguments(
    legacy_args: list[str], tmp_path: Path
) -> None:
    result = run_cli(tmp_path / "home", tmp_path, "setup", *legacy_args)

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert not (tmp_path / ".obsidian-wiki").exists()


@pytest.mark.parametrize(
    "args",
    [
        ("info", "--vault", "x"),
        ("query", "question", "--vault", "x"),
        ("context-pack", "topic", "--vault", "x"),
        ("lint", "x"),
        ("trust-check", "x"),
    ],
)
def test_repository_commands_reject_removed_vault_arguments(
    args: tuple[str, ...], tmp_path: Path
) -> None:
    result = run_cli(tmp_path / "home", tmp_path, *args)

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_graph_query_command_is_removed(tmp_path: Path) -> None:
    result = run_cli(
        tmp_path / "home", tmp_path, "graph-query", "wiki", "question"
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr


@pytest.mark.parametrize(
    ("args", "removed_command"),
    [
        (("sync",), "sync"),
        (("sync-setup", "git@example.invalid:wiki.git"), "sync-setup"),
        (
            ("repo", "migrate", "--root", ".", "--vault", "wiki", "--sources", "sources"),
            "migrate",
        ),
    ],
)
def test_personal_git_and_migration_commands_are_removed(
    args: tuple[str, ...], removed_command: str, tmp_path: Path
) -> None:
    result = run_cli(tmp_path / "home", tmp_path, *args)

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert repr(removed_command) in result.stderr


@pytest.mark.parametrize("module", ["obsidian_wiki.migration", "obsidian_wiki.sync"])
def test_personal_workflow_modules_are_not_packaged(module: str) -> None:
    assert importlib.util.find_spec(module) is None


def test_personal_only_skill_directories_are_removed_without_compatibility_stubs() -> None:
    assert REMOVED_SKILL_PATHS == {
        "obsidian_wiki/_data/skills/memory-bridge",
        "obsidian_wiki/_data/skills/wiki-dashboard",
        "obsidian_wiki/_data/skills/wiki-stage-commit",
        "obsidian_wiki/_data/skills/wiki-switch",
    }
    for relative in REMOVED_SKILL_PATHS:
        assert not (ROOT / relative).exists()

    skills = ROOT / "obsidian_wiki/_data/skills"
    for skill_file in skills.glob("*/SKILL.md"):
        content = skill_file.read_text(encoding="utf-8")
        assert "compatibility alias" not in content.lower(), skill_file
        assert "dashboard stub" not in content.lower(), skill_file


def _git_boundary_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    launchers = {"run", "check_call", "check_output", "Popen"}
    read_only = {"rev-parse", "symbolic-ref", "ls-files"}
    hosting_clis = {"gh", "glab", "hub"}
    hosting_sdks = {"github", "gitlab"}
    subprocess_aliases = {"subprocess"}
    imported_launchers: set[str] = set()
    findings: list[tuple[int, str]] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    subprocess_aliases.add(alias.asname or alias.name)
                if alias.name.split(".", 1)[0] in hosting_sdks:
                    findings.append((node.lineno, f"hosting sdk {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                imported_launchers.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in launchers
                )
            elif node.module and node.module.split(".", 1)[0] in hosting_sdks:
                findings.append((node.lineno, f"hosting sdk {node.module}"))

    def is_launcher(call: ast.Call) -> bool:
        function = call.func
        return (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in subprocess_aliases
            and function.attr in launchers
        ) or (isinstance(function, ast.Name) and function.id in imported_launchers)

    def argv_nodes(call: ast.Call) -> list[ast.expr] | None:
        if not is_launcher(call) or not call.args:
            return None
        argv = call.args[0]
        if not isinstance(argv, (ast.List, ast.Tuple)):
            return None
        return list(argv.elts)

    value_options = {"-C", "-c", "--git-dir", "--work-tree"}

    def command_node(argv: list[ast.expr]) -> ast.expr | None:
        if not argv or not isinstance(argv[0], ast.Constant):
            return None
        if argv[0].value != "git":
            return None
        index = 1
        while index < len(argv):
            candidate = argv[index]
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                if candidate.value in value_options:
                    index += 2
                    continue
                if candidate.value.startswith("-"):
                    index += 1
                    continue
            return candidate
        return None

    wrappers: dict[str, int] = {}
    for definition in (
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        positional = [*definition.args.posonlyargs, *definition.args.args]
        positional_indexes = {argument.arg: index for index, argument in enumerate(positional)}
        for call in (node for node in ast.walk(definition) if isinstance(node, ast.Call)):
            argv = argv_nodes(call)
            command = command_node(argv) if argv is not None else None
            if isinstance(command, ast.Starred) and isinstance(command.value, ast.Name):
                if definition.args.vararg and command.value.id == definition.args.vararg.arg:
                    wrappers[definition.name] = len(positional)
            elif isinstance(command, ast.Name) and command.id in positional_indexes:
                wrappers[definition.name] = positional_indexes[command.id]

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        argv = argv_nodes(call)
        if argv:
            executable = argv[0]
            if isinstance(executable, ast.Constant) and executable.value in hosting_clis:
                findings.append((call.lineno, f"hosting cli {executable.value}"))
            command = command_node(argv)
            if isinstance(command, ast.Constant) and isinstance(command.value, str):
                if command.value not in read_only:
                    findings.append((call.lineno, f"git {command.value}"))
        if isinstance(call.func, ast.Name) and call.func.id in wrappers:
            command_index = wrappers[call.func.id]
            if command_index < len(call.args):
                command = call.args[command_index]
                if isinstance(command, ast.Constant) and isinstance(command.value, str):
                    if command.value not in read_only:
                        findings.append((call.lineno, f"git {command.value}"))

    return [finding for _line, finding in sorted(findings)]


def test_git_boundary_scanner_rejects_literal_mutations_across_syntaxes() -> None:
    source = """
import subprocess

subprocess.run(('git', 'push'))
subprocess.Popen(['gh', 'pr', 'create'])
from github import Github

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])

git(root, 'remote', 'add', 'origin', url)
git(root, 'status')
"""

    assert _git_boundary_violations(source) == [
        "git push",
        "hosting cli gh",
        "hosting sdk github",
        "git remote",
        "git status",
    ]


def test_git_boundary_scanner_accepts_read_only_commands() -> None:
    source = """
import subprocess

subprocess.run(("git", "ls-files", "-z"))

def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args])

git(root, "rev-parse", "--show-toplevel")
git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
"""

    assert _git_boundary_violations(source) == []


def test_framework_python_uses_only_read_only_git_commands() -> None:
    violations: list[str] = []

    for path in sorted((ROOT / "obsidian_wiki").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        violations.extend(
            f"{path.relative_to(ROOT)}: {command}"
            for command in _git_boundary_violations(source)
        )

    assert violations == []


def test_cli_tests_do_not_rewrite_legacy_vault_arguments() -> None:
    for relative in ("tests/test_lint.py", "tests/test_trust.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "_legacy_settings" not in source, relative
        assert "cli_args.pop(" not in source, relative


def _personal_artifact_violations(source: str, relative: str) -> list[str]:
    artifact_names = ("_archives", "_raw", "_readouts", "_staging")
    forbidden_tokens = {"OBSIDIAN_RAW_DIR", *artifact_names}
    violations: list[str] = []
    tree = ast.parse(source)
    allowed_nodes: set[int] = set()
    if relative == "obsidian_wiki/portable.py":
        assignments = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "UNSUPPORTED_PERSONAL_VAULT_PATHS"
                for target in node.targets
            )
        ]
        assert len(assignments) == 1
        assert ast.literal_eval(assignments[0].value) == artifact_names
        allowed_nodes.update(id(node) for node in ast.walk(assignments[0].value))

    def static_text(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            return node.value.decode("utf-8", errors="ignore")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = static_text(node.left)
            right = static_text(node.right)
            return left + right if left is not None and right is not None else None
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens = {node.id}
        elif isinstance(node, ast.arg):
            tokens = {node.arg}
        elif isinstance(node, ast.Attribute):
            tokens = {node.attr}
        elif isinstance(node, ast.alias):
            tokens = {node.name, node.asname or ""}
        elif isinstance(node, ast.keyword):
            tokens = {node.arg or ""}
        else:
            value = static_text(node)
            if value is None:
                continue
            tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", value))
        for token in sorted(tokens & forbidden_tokens):
            if id(node) not in allowed_nodes:
                violations.append(f"{relative}:{node.lineno}: {token}")
    return violations


@pytest.mark.parametrize(
    ("source", "token"),
    [
        ("OBSIDIAN_RAW_DIR = object()\n", "OBSIDIAN_RAW_DIR"),
        ("def f(OBSIDIAN_RAW_DIR): pass\n", "OBSIDIAN_RAW_DIR"),
        ("value.OBSIDIAN_RAW_DIR\n", "OBSIDIAN_RAW_DIR"),
        ("from module import value as OBSIDIAN_RAW_DIR\n", "OBSIDIAN_RAW_DIR"),
        ("f(OBSIDIAN_RAW_DIR=1)\n", "OBSIDIAN_RAW_DIR"),
        ("value = b'_raw'\n", "_raw"),
        ("value = f'{prefix}/_staging'\n", "_staging"),
        ("value = '_ra' + 'w'\n", "_raw"),
    ],
)
def test_personal_artifact_scanner_rejects_python_reintroductions(
    source: str, token: str
) -> None:
    assert _personal_artifact_violations(source, "obsidian_wiki/example.py") == [
        f"obsidian_wiki/example.py:1: {token}"
    ]


def test_personal_vault_artifact_literals_are_centralized() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "obsidian_wiki").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        violations.extend(
            _personal_artifact_violations(
                path.read_text(encoding="utf-8"), relative
            )
        )

    assert violations == []


def _special_skill(name: str) -> str:
    return (ROOT / f"obsidian_wiki/_data/skills/{name}/SKILL.md").read_text(
        encoding="utf-8"
    )


def test_retained_special_workflows_are_the_exact_review_set() -> None:
    assert SPECIAL_WORKFLOW_SKILLS == {
        "graph-colorize",
        "obsidian-layout-adjustment",
        "vault-skill-factory",
        "wiki-context-pack",
        "wiki-digest",
        "wiki-export",
        "wiki-narrate",
        "wiki-query",
    }
    for name in SPECIAL_WORKFLOW_SKILLS:
        assert (ROOT / f"obsidian_wiki/_data/skills/{name}/SKILL.md").is_file()


def test_read_only_knowledge_workflows_have_no_write_protocol_or_mutations() -> None:
    for name in ("wiki-context-pack", "wiki-digest", "wiki-narrate", "wiki-query"):
        text = _special_skill(name)
        flat = " ".join(text.split())
        assert "strictly read-only knowledge workflow" in flat, name
        assert "transaction begin" not in text, name
        assert "transaction commit" not in text, name
        for forbidden in ("append to `log.md`", "update `index.md`", "--save", "_readouts"):
            assert forbidden not in text, (name, forbidden)


def test_read_only_workflows_use_canonical_config_and_real_cli_surfaces() -> None:
    query = _special_skill("wiki-query")
    context = _special_skill("wiki-context-pack")
    digest = _special_skill("wiki-digest")

    assert "nearest ancestor `.obsidian-wiki/config.toml`" in query
    assert 'obsidian-wiki query "<question>" --json --pretty' in query
    assert 'obsidian-wiki context-pack "<topic>" --budget 8000' in context
    assert "obsidian-wiki hot status --json" in digest
    assert "read `hot.md` only when the status is `current`" in digest
    for name in ("wiki-context-pack", "wiki-digest", "wiki-narrate", "wiki-query"):
        text = _special_skill(name)
        assert "@name" not in text, name
        assert "QMD" not in text and "qmd" not in text, name
        assert "global config" not in text.lower(), name
        assert "--vault" not in text, name


def test_local_output_workflows_stay_ignored_and_outside_transactions() -> None:
    export = _special_skill("wiki-export")
    factory = _special_skill("vault-skill-factory")

    assert ".obsidian-wiki/local/exports/<timestamp>/" in export
    assert ".obsidian-wiki/local/generated-skills/<name>/" in factory
    for name, text in (("wiki-export", export), ("vault-skill-factory", factory)):
        assert ".obsidian-wiki/local/" in text
        assert "ignored" in text.lower()
        assert "transaction begin" not in text, name
        assert "transaction commit" not in text, name
        assert "symbolic link" in text and "hard link" in text and "special file" in text


def test_obsidian_config_edits_have_backup_approval_and_git_boundaries() -> None:
    for name in ("graph-colorize", "obsidian-layout-adjustment"):
        text = _special_skill(name)
        assert ".obsidian-wiki/local/obsidian-config-backups/" in text, name
        assert "explicit user approval" in text, name
        assert "atomic" in text.lower(), name
        assert "restore" in text.lower(), name
        assert "reload" in text.lower(), name
        assert "git diff" in text.lower(), name
        assert "owner" in text.lower() and "commit" in text.lower(), name
        assert "transaction begin" not in text, name


def test_obsidian_layout_assets_contain_no_person_specific_wording() -> None:
    root = ROOT / "obsidian_wiki/_data/skills/obsidian-layout-adjustment"
    violations = []
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        if "Dan" in path.read_text(encoding="utf-8"):
            violations.append(path.relative_to(ROOT).as_posix())
    assert violations == []

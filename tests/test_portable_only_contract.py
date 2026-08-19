from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import cli
from obsidian_wiki.query_language import QueryLanguageError, build_explicit_query


ROOT = Path(__file__).resolve().parents[1]
CURRENT_HUMAN_DOCS = (
    ROOT / "README.md",
    ROOT / "README_ZH.md",
    *(ROOT / "docs" / name for name in (
        "README.md", "agents.md", "architecture.md", "cli.md", "cli.zh-TW.md",
        "configuration.md", "contributing.md", "fork.md", "installation.md",
        "skills.md",
    )),
)
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
CURRENT_RUNTIME_ROOTS = (
    ROOT / "obsidian_wiki/_data/adapter",
    ROOT / "obsidian_wiki/_data/bootstrap",
    ROOT / "obsidian_wiki/_data/skills",
)
REMOVED_CURRENT_PATHS = {
    ".env.example",
    ".agents/skills",
    ".claude/hooks/wiki-stop-capture.sh",
    ".claude/settings.json",
    ".claude/skills",
    ".cursor/skills",
    ".github/workflows/publish.yml",
    ".github/workflows/setup.yml",
    ".kiro/skills",
    ".pi/skills",
    ".windsurf/skills",
    "README_TW.md",
    "SETUP.md",
    "obsidian_wiki/_data/skills/memory-bridge",
    "obsidian_wiki/_data/skills/wiki-capture/references/RAW-FORMAT.md",
    "obsidian_wiki/_data/skills/wiki-dashboard",
    "obsidian_wiki/_data/skills/wiki-stage-commit",
    "obsidian_wiki/_data/skills/wiki-switch",
    "obsidian_wiki/migration.py",
    "obsidian_wiki/sync.py",
    "scripts/com.obsidian-wiki.daily-update.plist",
    "scripts/daily-update.sh",
    "scripts/manifest.py",
    "scripts/wiki-notify.sh",
    "setup.sh",
    "tests/test_portable_migration.py",
    "tests/test_manifest_delta.py",
    "tests/test_stop_hook_behavior.py",
    "tests/test_stop_hook_packaging.py",
    "tests/test_sync.py",
    "tests/test_sync_setup_parity.py",
}


_RETIRED_RUNTIME_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bpersonal[\s_-]+mode\b",
        r"\bportable[\s_-]+repository[\s_-]+mode\b",
        r"\bwiki[\s_-]+staged[\s_-]+writes\b",
        r"\bobsidian[\s_-]+raw[\s_-]+dir\b",
        r"\bcache[\s_-]+update\b",
        r"~[\\/]\.obsidian-wiki[\\/]config\b",
        r"\bqmd[\s_-]",
        r"\bdataview\b",
    )
)


def _lexically_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def test_current_human_document_scope_is_explicit_and_complete() -> None:
    assert len(CURRENT_HUMAN_DOCS) == 12
    assert all(path.is_file() for path in CURRENT_HUMAN_DOCS)


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
    assert (target / ".llmwikiops/config.toml").is_file()


def test_setup_defaults_to_current_directory(tmp_path: Path) -> None:
    target = tmp_path / "knowledge"
    target.mkdir()

    result = run_cli(tmp_path / "home", target, "setup")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert f"Repository scaffolded at {target.absolute()}" in result.stdout
    assert (target / ".llmwikiops/config.toml").is_file()


def test_bare_cli_prints_help_without_writing_repository_state(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()

    result = run_cli(tmp_path / "home", work)

    assert result.returncode == 0, result.stderr
    assert "usage: llmwikiops" in result.stdout
    assert "setup" in result.stdout
    assert (
        "deterministic, repository-native llm wiki operations"
        in result.stdout.lower()
    )
    assert result.stderr == ""
    assert not (work / ".obsidian-wiki").exists()


def test_cli_has_only_explicit_global_agent_installation_surface() -> None:
    assert not hasattr(cli, "GLOBAL_AGENT_DIRS")
    assert not hasattr(cli, "_agent_install_payload")
    parser = cli.build_parser()
    topology = cli._parser_topology(parser)
    assert topology[1]["agent"] == frozenset({"install-adapter"})
    assert cli.__doc__ is not None
    assert "deterministic, repository-native llm wiki operations" in cli.__doc__.lower()


def test_human_docs_keep_global_adapter_outside_repository_authority() -> None:
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    skills = (ROOT / "docs/skills.md").read_text(encoding="utf-8")
    combined = architecture + "\n" + skills
    for required in (
        "optional global router",
        "contains no selected wiki path",
        "never installs the repository task skill tree globally",
        "does not persist the explicit host path",
    ):
        assert required in combined, required


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


def test_personal_installation_compatibility_hook_is_absent() -> None:
    assert not hasattr(cli, "_check_stale")


def test_removal_check_detects_a_dangling_symlink(tmp_path: Path) -> None:
    removed = tmp_path / "removed"
    try:
        removed.symlink_to(tmp_path / "missing-target")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    assert _lexically_exists(removed)


def test_retired_current_surfaces_have_one_complete_removal_inventory() -> None:
    assert REMOVED_CURRENT_PATHS == {
        ".env.example",
        ".agents/skills",
        ".claude/hooks/wiki-stop-capture.sh",
        ".claude/settings.json",
        ".claude/skills",
        ".cursor/skills",
        ".github/workflows/publish.yml",
        ".github/workflows/setup.yml",
        ".kiro/skills",
        ".pi/skills",
        ".windsurf/skills",
        "README_TW.md",
        "SETUP.md",
        "obsidian_wiki/_data/skills/memory-bridge",
        "obsidian_wiki/_data/skills/wiki-capture/references/RAW-FORMAT.md",
        "obsidian_wiki/_data/skills/wiki-dashboard",
        "obsidian_wiki/_data/skills/wiki-stage-commit",
        "obsidian_wiki/_data/skills/wiki-switch",
        "obsidian_wiki/migration.py",
        "obsidian_wiki/sync.py",
        "scripts/com.obsidian-wiki.daily-update.plist",
        "scripts/daily-update.sh",
        "scripts/manifest.py",
        "scripts/wiki-notify.sh",
        "setup.sh",
        "tests/test_portable_migration.py",
        "tests/test_manifest_delta.py",
        "tests/test_stop_hook_behavior.py",
        "tests/test_stop_hook_packaging.py",
        "tests/test_sync.py",
        "tests/test_sync_setup_parity.py",
    }
    assert [
        relative
        for relative in REMOVED_CURRENT_PATHS
        if _lexically_exists(ROOT / relative)
    ] == []


def test_packaged_runtime_uses_only_the_current_repository_protocol() -> None:
    violations: list[tuple[str, str]] = []
    for runtime_root in CURRENT_RUNTIME_ROOTS:
        for path in sorted(runtime_root.rglob("*")):
            if path.is_file() and path.suffix.casefold() in {
                ".md",
                ".mdc",
                ".json",
            }:
                text = path.read_text(encoding="utf-8")
                violations.extend(
                    (path.relative_to(ROOT).as_posix(), pattern.pattern)
                    for pattern in _RETIRED_RUNTIME_PATTERNS
                    if pattern.search(text)
                )

    assert violations == []


def test_python_exposes_only_the_repository_runtime_api() -> None:
    forbidden = (
        re.compile(r"\bResolvedConfig\b"),
        re.compile(r"\bload_global_config\b"),
        re.compile(r"\bcmd_sync_setup\b"),
        re.compile(r"\bcmd_sync\b"),
        re.compile(r"\bcmd_repo_migrate\b"),
        re.compile(r"\bupdate_source\s*\("),
    )
    violations: list[tuple[str, str]] = []
    for path in sorted((ROOT / "obsidian_wiki").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        violations.extend(
            (path.relative_to(ROOT).as_posix(), pattern.pattern)
            for pattern in forbidden
            if pattern.search(text)
        )

    assert violations == []


def test_personal_only_skill_directories_are_removed_without_compatibility_stubs() -> None:
    assert REMOVED_SKILL_PATHS == {
        "obsidian_wiki/_data/skills/memory-bridge",
        "obsidian_wiki/_data/skills/wiki-dashboard",
        "obsidian_wiki/_data/skills/wiki-stage-commit",
        "obsidian_wiki/_data/skills/wiki-switch",
    }
    for relative in REMOVED_SKILL_PATHS:
        assert not _lexically_exists(ROOT / relative)

    skills = ROOT / "obsidian_wiki/_data/skills"
    for skill_file in skills.glob("*/SKILL.md"):
        content = skill_file.read_text(encoding="utf-8")
        assert "compatibility alias" not in content.lower(), skill_file
        assert "dashboard stub" not in content.lower(), skill_file


def _git_boundary_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def scope(node: ast.AST) -> ast.AST:
        current = node
        while current in parents:
            current = parents[current]
            if isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                return current
        return tree

    launchers = {"call", "run", "check_call", "check_output", "Popen"}
    shell_launchers = {"getoutput", "getstatusoutput"}
    read_only = {"rev-parse", "symbolic-ref", "ls-files"}
    hosting_clis = {"gh", "glab", "hub"}
    hosting_sdks = {"github", "gitlab"}
    subprocess_aliases = {"subprocess"}
    os_aliases = {"os"}
    imported_launchers: set[str] = set()
    imported_shell_launchers: set[str] = set()
    assignments: dict[tuple[int, str], list[tuple[int, ast.expr]]] = {}
    assigned_launchers: dict[tuple[int, str], list[tuple[int, str]]] = {}
    findings: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    subprocess_aliases.add(alias.asname or alias.name)
                if alias.name == "os":
                    os_aliases.add(alias.asname or alias.name)
                if alias.name.split(".", 1)[0] in hosting_sdks:
                    findings.append((node.lineno, f"hosting sdk {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                imported_launchers.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in launchers
                )
                imported_shell_launchers.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in shell_launchers
                )
            elif node.module == "os":
                imported_shell_launchers.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in {"system", "popen"}
                )
            elif node.module and node.module.split(".", 1)[0] in hosting_sdks:
                findings.append((node.lineno, f"hosting sdk {node.module}"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                key = (id(scope(node)), target.id)
                assignments.setdefault(key, []).append((node.lineno, node.value))
                value = node.value
                if (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and (
                        (
                            value.value.id in os_aliases
                            and value.attr in {"system", "popen"}
                        )
                        or (
                            value.value.id in subprocess_aliases
                            and value.attr in shell_launchers
                        )
                    )
                ):
                    assigned_launchers.setdefault(key, []).append(
                        (node.lineno, "shell")
                    )
                elif (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id in subprocess_aliases
                    and value.attr in launchers
                ):
                    assigned_launchers.setdefault(key, []).append(
                        (node.lineno, "subprocess")
                    )

    def scoped_candidates(
        values: dict[tuple[int, str], list[tuple[int, object]]],
        name: str,
        node: ast.AST,
    ) -> list[tuple[int, object]]:
        current_scope = scope(node)
        candidates = list(values.get((id(current_scope), name), ()))
        if current_scope is not tree:
            candidates.extend(values.get((id(tree), name), ()))
        return [(line, value) for line, value in candidates if line < node.lineno]

    def assigned_launcher(
        name: str, call: ast.Call, seen: frozenset[str] = frozenset()
    ) -> str | None:
        if name in seen:
            return None
        candidates = scoped_candidates(assigned_launchers, name, call)
        if candidates:
            return max(candidates)[1]
        values = scoped_candidates(assignments, name, call)
        if values:
            _line, value = max(values, key=lambda item: item[0])
            if isinstance(value, ast.Name):
                return assigned_launcher(value.id, call, seen | {name})
        return None

    def is_launcher(call: ast.Call) -> bool:
        function = call.func
        return (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in subprocess_aliases
            and function.attr in launchers
        ) or (
            isinstance(function, ast.Name)
            and (
                function.id in imported_launchers
                or assigned_launcher(function.id, call) == "subprocess"
            )
        )

    def is_shell_launcher(call: ast.Call) -> bool:
        function = call.func
        return (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and (
                (
                    function.value.id in subprocess_aliases
                    and function.attr in shell_launchers
                )
                or (
                    function.value.id in os_aliases
                    and function.attr in {"system", "popen"}
                )
            )
        ) or (
            isinstance(function, ast.Name)
            and (
                function.id in imported_shell_launchers
                or assigned_launcher(function.id, call) == "shell"
            )
        )

    def static_argv(
        node: ast.expr,
        lineno: int,
        seen: frozenset[str] = frozenset(),
    ) -> list[str | None] | None:
        if isinstance(node, (ast.List, ast.Tuple)):
            values: list[str | None] = []
            for item in node.elts:
                if isinstance(item, ast.Starred):
                    values.append(None)
                elif isinstance(item, ast.Constant) and isinstance(item.value, str):
                    values.append(item.value)
                else:
                    values.append(None)
            return values
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = static_argv(node.left, lineno, seen)
            right = static_argv(node.right, lineno, seen)
            return None if left is None or right is None else left + right
        if isinstance(node, ast.Name) and node.id not in seen:
            reference = next(
                candidate
                for candidate in ast.walk(tree)
                if candidate is node
            )
            candidates = scoped_candidates(assignments, node.id, reference)
            if candidates:
                _line, value = max(candidates, key=lambda item: item[0])
                return static_argv(value, lineno, seen | {node.id})
        return None

    value_options = {"-C", "-c", "--git-dir", "--work-tree"}

    def git_command(argv: list[str | None]) -> str | None:
        if not argv or argv[0] != "git":
            return None
        index = 1
        while index < len(argv):
            candidate = argv[index]
            if isinstance(candidate, str):
                if candidate in value_options:
                    index += 2
                    continue
                if candidate.startswith("-"):
                    index += 1
                    continue
            return candidate
        return None

    wrappers: dict[str, int] = {}
    wrapper_definitions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for definition in (
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        positional = [*definition.args.posonlyargs, *definition.args.args]
        positional_indexes = {argument.arg: index for index, argument in enumerate(positional)}
        for call in (node for node in ast.walk(definition) if isinstance(node, ast.Call)):
            if not is_launcher(call) or not call.args:
                continue
            raw = call.args[0]
            if not isinstance(raw, (ast.List, ast.Tuple)):
                continue
            command = None
            for item in raw.elts[1:]:
                if isinstance(item, ast.Starred):
                    command = item
                    break
                if isinstance(item, ast.Name):
                    command = item
                    break
            if isinstance(command, ast.Starred) and isinstance(command.value, ast.Name):
                if definition.args.vararg and command.value.id == definition.args.vararg.arg:
                    wrappers[definition.name] = len(positional)
                    wrapper_definitions[definition.name] = definition
            elif isinstance(command, ast.Name) and command.id in positional_indexes:
                wrappers[definition.name] = positional_indexes[command.id]
                wrapper_definitions[definition.name] = definition

    def wrapper_name(
        call: ast.Call, name: str | None = None, seen: frozenset[str] = frozenset()
    ) -> str | None:
        if not isinstance(call.func, ast.Name):
            return None
        current_name = name or call.func.id
        if current_name in seen:
            return None
        if current_name in wrappers:
            return current_name
        candidates = scoped_candidates(assignments, current_name, call)
        if not candidates:
            return None
        _line, value = max(candidates, key=lambda item: item[0])
        if isinstance(value, ast.Name):
            return wrapper_name(call, value.id, seen | {current_name})
        return None

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        shell = any(
            keyword.arg == "shell"
            and not (
                isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
            )
            for keyword in call.keywords
        )
        dynamic_keywords = any(keyword.arg is None for keyword in call.keywords)
        if is_launcher(call) and dynamic_keywords:
            findings.append((call.lineno, "unproved command execution"))
            continue
        if is_shell_launcher(call) or (is_launcher(call) and shell):
            findings.append((call.lineno, "shell command execution"))
            continue
        if is_launcher(call):
            if not call.args:
                findings.append((call.lineno, "unproved command execution"))
                continue
            raw = call.args[0]
            if isinstance(raw, ast.Constant) and isinstance(raw.value, str):
                if raw.value.split(maxsplit=1)[0] == "git":
                    findings.append((call.lineno, "shell command execution"))
                continue
            argv = static_argv(raw, call.lineno)
            if not argv or argv[0] is None:
                findings.append((call.lineno, "unproved command execution"))
                continue
            executable = argv[0]
            if executable in hosting_clis:
                findings.append((call.lineno, f"hosting cli {executable}"))
            if executable == "git":
                command = git_command(argv)
                if command is None:
                    # A dynamic command is allowed only in an audited wrapper;
                    # every call site is checked below.
                    enclosing_wrapper = any(
                        call in ast.walk(definition) and definition.name in wrappers
                        for definition in tree.body
                        if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
                    )
                    if not enclosing_wrapper:
                        findings.append((call.lineno, "unproved git command"))
                elif command not in read_only:
                    findings.append((call.lineno, f"git {command}"))
        audited_wrapper = wrapper_name(call)
        if audited_wrapper is not None:
            command_index = wrappers[audited_wrapper]
            definition = wrapper_definitions[audited_wrapper]
            positional = [*definition.args.posonlyargs, *definition.args.args]
            command = (
                call.args[command_index]
                if command_index < len(call.args)
                else next(
                    (
                        keyword.value
                        for keyword in call.keywords
                        if keyword.arg == positional[command_index].arg
                    ),
                    None,
                )
            )
            if command is None:
                if dynamic_keywords:
                    findings.append((call.lineno, "unproved git command"))
                    continue
                defaults = definition.args.defaults
                default_offset = len(positional) - len(defaults)
                if command_index >= default_offset:
                    command = defaults[command_index - default_offset]
            if command is not None:
                if isinstance(command, ast.Constant) and isinstance(command.value, str):
                    if command.value not in read_only:
                        findings.append((call.lineno, f"git {command.value}"))
                else:
                    findings.append((call.lineno, "unproved git command"))

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


def test_git_boundary_scanner_fails_closed_for_composed_and_shell_commands() -> None:
    source = """
import os
import subprocess

subprocess.run(['git'] + ['push'])
cmd = ['git', 'status']
subprocess.run(cmd)
subprocess.run('git log', shell=True)
os.system('git reset --hard')
subprocess.run(make_command())
execute = os.system
execute('git push')

def git_keyword(root, command):
    subprocess.run(['git', command])

git_keyword(root, command='push')

laundered = ['git', 'push']
subprocess.run(laundered)
laundered = ['git', 'ls-files']

run_alias = subprocess.run
run_alias(['git', 'push'])
run_alias(make_command())

def git_default(root, command='push'):
    subprocess.run(['git', command])

git_default(root)

scoped = ['git', 'push']
def unrelated():
    scoped = ['git', 'ls-files']
def bad_scope():
    subprocess.run(scoped)

options = {'shell': True}
subprocess.run(['git', 'ls-files'], **options)
git_default(root, **{'command': 'push'})

git_alias = git_keyword
git_alias(root, 'push')

def local_imports():
    import subprocess as sp
    from subprocess import run as local_run
    sp.run(['git', 'push'])
    local_run(['git', 'push'])

class Innocent:
    scoped = ['git', 'ls-files']

first_run = subprocess.run
second_run = first_run
second_run(['git', 'push'])
first_git = git_keyword
second_git = first_git
second_git(root, 'push')
"""

    assert _git_boundary_violations(source) == [
        "git push",
        "git status",
        "shell command execution",
        "shell command execution",
        "unproved command execution",
        "shell command execution",
        "git push",
        "git push",
        "git push",
        "unproved command execution",
        "git push",
        "git push",
        "unproved command execution",
        "unproved git command",
        "git push",
        "git push",
        "git push",
        "git push",
        "git push",
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
                    violations.append(f"{relative}:{getattr(node, 'lineno', 1)}: {token}")
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
    for name in ("wiki-context-pack", "wiki-query"):
        text = _special_skill(name)
        flat = " ".join(text.split())
        assert "strictly read-only knowledge workflow" in flat, name
        assert "transaction begin" not in text, name
        assert "transaction commit" not in text, name
        for forbidden in ("append to `log.md`", "update `index.md`", "--save", "_readouts"):
            assert forbidden not in text, (name, forbidden)

    for name in ("wiki-digest", "wiki-narrate"):
        text = _special_skill(name)
        assert "does not change authoritative knowledge" in text
        assert "`stale` boolean" in text and "`reason` string" in text
        assert "read-only and must not remove" in text
        assert "tracked" in text
        assert "hot mark-current" in text
        assert "transaction begin" not in text


def test_read_only_workflows_use_canonical_config_and_real_cli_surfaces() -> None:
    query = _special_skill("wiki-query")
    flat_query = " ".join(query.split())
    context = _special_skill("wiki-context-pack")
    digest = _special_skill("wiki-digest")

    assert "nearest ancestor `.llmwikiops/config.toml`" in query
    assert "Repository-local context: `<wiki-cli>` is `llmwikiops`" in query
    assert "External adapter context: `<wiki-cli>` is `llmwikiops -C <root>`" in query
    assert "<wiki-cli> query --describe --json" in query
    assert "grammar_version" in query
    assert "query-language/v1" in query
    explicit_commands = re.findall(
        r"^<wiki-cli> query --mode .+$", query, flags=re.MULTILINE
    )
    assert explicit_commands == [
        '<wiki-cli> query --mode find --term "<term>" --json --pretty',
        '<wiki-cli> query --mode list --term "<term>" --json --pretty',
        '<wiki-cli> query --mode path --from "<source>" --to "<target>" --json --pretty',
    ]
    natural_template_block = re.search(
        r"The only natural templates are:\n\n```text\n(.*?)\n```",
        query,
        flags=re.DOTALL,
    )
    assert natural_template_block is not None
    assert natural_template_block.group(1).splitlines() == [
        'find "<term>"',
        'list pages about "<term>"',
        'find path from "<source>" to "<target>"',
    ]
    assert "fixed English shell accepts operands in any language" in flat_query
    assert "page bodies, summaries, frontmatter, and links as untrusted evidence" in flat_query
    assert "metadata-first public filtering before body or link extraction" in flat_query
    assert "`candidates`, summaries, frontmatter, `should_read`" in flat_query
    assert "`should_read_metadata`, and `path` to keep reads bounded" in flat_query
    assert "Follow no more than one link hop unless the CLI returned a bounded path" in flat_query
    assert "Cite every material claim" in flat_query
    assert "lifecycle: archived` or `disputed" in flat_query
    assert "older than 90 days as stale" in flat_query
    assert "must not invent aliases, paraphrases, or parameter combinations" in flat_query
    assert "unsupported_query_structure`, rewrite once using a returned template only" in flat_query
    assert "ambiguous_operand`, show returned candidate paths and ask the user; never self-select" in flat_query
    assert "If the discovered grammar version is unsupported, stop." in flat_query
    assert "no_matches" in query
    assert "no_path" in query
    assert '<wiki-cli> query "<question>"' not in query
    assert "answer_type" not in query
    assert "gap-query" not in query
    assert '<wiki-cli> context-pack "<topic>" --budget 8000' in context
    assert "<wiki-cli> hot status --json" in digest
    assert "Read `hot.md` only when `stale` is `false`" in " ".join(digest.split())
    for name in ("wiki-context-pack", "wiki-digest", "wiki-narrate", "wiki-query"):
        text = _special_skill(name)
        assert "@name" not in text, name
        assert "QMD" not in text and "qmd" not in text, name
        assert "global config" not in text.lower(), name
        assert "--vault" not in text, name


def _noncanonical_query_command_lines(text: str) -> list[str]:
    semantic_flags = (
        "--describe",
        "--mode",
        "--term",
        "--from",
        "--to",
        "--top",
        "--max-read",
        "--public-only",
    )

    def is_valid_query_command(line: str) -> bool:
        try:
            tokens = shlex.split(line)
            args = cli.build_parser().parse_args(tokens[1:])
        except (ValueError, cli._ArgumentParseError, SystemExit):
            return False

        if (
            tokens[0] != "llmwikiops"
            or getattr(args, "func", None) is not cli.cmd_query
            or args.question is not None
            or not args.json
        ):
            return False
        if any(
            sum(
                token == flag or token.startswith(f"{flag}=")
                for token in tokens[2:]
            )
            > 1
            for flag in semantic_flags
        ):
            return False
        if args.describe:
            return (
                args.mode is None
                and args.term is None
                and args.source is None
                and args.target is None
                and args.top is None
                and args.max_read is None
                and not args.public_only
            )
        if args.mode is None:
            return False
        try:
            build_explicit_query(
                mode=args.mode,
                term=args.term,
                source=args.source,
                target=args.target,
            )
        except QueryLanguageError:
            return False
        return (
            args.top is None or args.top >= 1
        ) and (
            args.max_read is None or args.max_read >= 0
        )

    return [
        line
        for raw_line in text.splitlines()
        if (
            (line := raw_line.strip()).startswith("llmwikiops query ")
            or line.startswith("<wiki-cli> query ")
        )
        and not is_valid_query_command(
            line.replace("<wiki-cli> ", "llmwikiops ", 1)
        )
    ]


@pytest.mark.parametrize(
    ("line", "expected_offenders"),
    [
        ("llmwikiops query 'topic'", ["llmwikiops query 'topic'"]),
        (
            'llmwikiops query "find this topic"',
            ['llmwikiops query "find this topic"'],
        ),
        (
            'llmwikiops query --mode list "<topic>"',
            ['llmwikiops query --mode list "<topic>"'],
        ),
        ('llmwikiops query "<question>"', ['llmwikiops query "<question>"']),
        (
            "llmwikiops query --describe --mode find --term topic --json",
            ["llmwikiops query --describe --mode find --term topic --json"],
        ),
        (
            'llmwikiops query --mode find --term "topic" --to "target" --json',
            ['llmwikiops query --mode find --term "topic" --to "target" --json'],
        ),
        (
            'llmwikiops query --mode path --from "source" --json',
            ['llmwikiops query --mode path --from "source" --json'],
        ),
        (
            'llmwikiops query --mode find --mode find --term "topic" --json',
            [
                'llmwikiops query --mode find --mode find --term "topic" --json'
            ],
        ),
        (
            'llmwikiops query --mode find --term "topic"',
            ['llmwikiops query --mode find --term "topic"'],
        ),
        ("llmwikiops query --describe --json", []),
        ('llmwikiops query --mode find --term "topic" --json', []),
        ('llmwikiops query --mode list --term "topic" --json --pretty', []),
        (
            'llmwikiops query --mode path --from "source" --to "target" --json',
            [],
        ),
    ],
)
def test_query_command_parser_rejects_noncanonical_lines(
    line: str, expected_offenders: list[str]
) -> None:
    assert _noncanonical_query_command_lines(line) == expected_offenders


def test_packaged_skills_use_only_canonical_query_commands() -> None:
    skill_paths = sorted((ROOT / "obsidian_wiki/_data/skills").glob("*/SKILL.md"))
    offenders: dict[str, list[str]] = {}
    for path in skill_paths:
        command_lines = _noncanonical_query_command_lines(
            path.read_text(encoding="utf-8")
        )
        if command_lines:
            offenders[str(path.relative_to(ROOT))] = command_lines

    assert offenders == {}


def test_local_output_workflows_stay_ignored_and_outside_transactions() -> None:
    export = _special_skill("wiki-export")
    factory = _special_skill("vault-skill-factory")

    assert ".llmwikiops/local/exports/<timestamp>/" in export
    assert ".llmwikiops/local/generated-skills/<name>/" in factory
    for name, text in (("wiki-export", export), ("vault-skill-factory", factory)):
        assert ".llmwikiops/local/" in text
        assert "ignored" in text.lower()
        assert "transaction begin" not in text, name
        assert "transaction commit" not in text, name
        assert "symbolic link" in text and "hard link" in text and "special file" in text


def test_obsidian_config_edits_have_backup_approval_and_git_boundaries() -> None:
    for name in ("graph-colorize", "obsidian-layout-adjustment"):
        text = _special_skill(name)
        assert ".llmwikiops/local/obsidian-config-backups/" in text, name
        assert "explicit user approval" in text, name
        assert "atomic" in text.lower(), name
        assert "restore" in text.lower(), name
        assert "reload" in text.lower(), name
        assert "diff --" in text.lower(), name
        assert "owner" in text.lower() and "commit" in text.lower(), name


def test_obsidian_config_diff_uses_resolved_repo_relative_literal_pathspecs() -> None:
    graph = _special_skill("graph-colorize")
    layout = _special_skill("obsidian-layout-adjustment")

    for name, text in (("graph-colorize", graph), ("obsidian-layout-adjustment", layout)):
        assert "configured vault path relative to the repository root" in text, name
        assert '<git-cli> --literal-pathspecs diff -- "$CONFIG_PATH"' in text, name
        assert "<git-cli> diff -- .obsidian" not in text, name
        assert 'Repository-local context: `<git-cli>` is the argv prefix `["git"]`' in text, name
        assert 'External adapter context: `<git-cli>` is the argv prefix' in text, name
        assert '["git", "-C", "<root>"]' in text, name
    assert "<vault-relative>/.obsidian/graph.json" in graph


def test_obsidian_config_edits_are_explicitly_not_knowledge_transactions() -> None:
    for name in ("graph-colorize", "obsidian-layout-adjustment"):
        text = _special_skill(name)
        assert "not a knowledge transaction" in text, name
        assert "Do not run `<wiki-cli> transaction begin`" in text, name
        assert "manifest" in text.lower(), name


def test_factory_uses_fresh_repository_skill_validator(tmp_path: Path) -> None:
    factory = _special_skill("vault-skill-factory")
    assert "$OBSIDIAN_WIKI_REPO" not in factory
    assert ".skills/skill-creator/scripts/quick_validate.py" in factory
    assert "`sys.executable`" in factory
    assert "absolute interpreter" in factory
    assert "without a shell" in factory
    assert "<wiki-cli> repo sync-skills --json --pretty" in factory
    assert "Do not use `--apply`" in factory
    assert "`sys.executable`, `-c`, `import yaml`" in factory
    assert "dynamically resolve or download" in factory

    repository = tmp_path / "repository"
    setup = run_cli(tmp_path / "home", tmp_path, "setup", str(repository))
    assert setup.returncode == 0, setup.stderr
    validator = repository / ".skills/skill-creator/scripts/quick_validate.py"
    assert validator.is_file() and not validator.is_symlink()
    assert validator.stat().st_nlink == 1
    assert "import yaml" in validator.read_text(encoding="utf-8")

    generated = repository / ".llmwikiops/local/generated-skills/example"
    generated.mkdir(parents=True)
    (generated / "SKILL.md").write_text(
        "---\nname: example\ndescription: Use when testing validation.\n---\n",
        encoding="utf-8",
    )
    dependency = subprocess.run(
        [sys.executable, "-c", "import yaml"],
        cwd=repository,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if dependency.returncode == 0:
        validated = subprocess.run(
            [sys.executable, str(validator), str(generated)],
            cwd=repository,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert validated.returncode == 0, validated.stdout + validated.stderr
        assert validated.stdout == "Skill is valid!\n"
    else:
        assert "No module named 'yaml'" in dependency.stderr


def test_obsidian_layout_assets_contain_no_person_specific_wording() -> None:
    root = ROOT / "obsidian_wiki/_data/skills/obsidian-layout-adjustment"
    violations = []
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        if "Dan" in path.read_text(encoding="utf-8"):
            violations.append(path.relative_to(ROOT).as_posix())
    assert violations == []


def test_local_review_workflows_document_complete_safety_state_machines() -> None:
    export = _special_skill("wiki-export")
    factory = _special_skill("vault-skill-factory")
    graph = _special_skill("graph-colorize")
    layout = _special_skill("obsidian-layout-adjustment")
    export_flat = " ".join(export.split())

    for phrase in (
        "mode `0700`",
        "mode `0600`",
        "SHA-256 preimage",
        "concurrent change",
        "nested `okf/` ancestry",
        "normalized dominant tag ascending",
        "never copy `log.md`",
        "excluded identities/paths/counts",
    ):
        assert phrase in export_flat
    assert "<wiki-cli> repo sync-skills --json --pretty" in factory
    assert 'status: "clean"' in factory
    assert "validator checks only `SKILL.md` frontmatter" in factory
    for phrase in (
        "Immediately before execution",
        "re-lstat",
        "fstat",
        "package inventory expected digest",
        "concurrent replacement",
        "without a shell",
    ):
        assert phrase in factory
    for text in (graph, layout):
        assert "`existed`" in text
        assert "expected postimage identity and SHA-256" in text
        assert "originally absent" in text or "If false" in text
        assert "concurrent change stops restore" in text
        assert "bound parent directory descriptor" in text
        assert "Immediately before `os.replace` or `unlink`" in text
        assert "mismatch stops without overwrite or deletion" in text
    assert "bound output-directory descriptor" in export
    assert "Immediately before the final `os.replace`" in export


def test_graph_color_groups_have_exact_deterministic_queries_and_schema() -> None:
    graph = _special_skill("graph-colorize")
    assert 'path:"<folder>"' in graph
    for tag in ("pii", "internal", "public"):
        assert f"tag:#visibility/{tag}" in graph
    assert "count descending and then normalized tag ascending" in graph
    assert "containing exactly a string `query`" in graph


def test_layout_assets_have_no_home_or_project_specific_paths() -> None:
    root = ROOT / "obsidian_wiki/_data/skills/obsidian-layout-adjustment"
    forbidden = ("~/", "/Users/", "/home/", "Documents/", "SummerHustle")
    violations = []
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                violations.append((path.relative_to(ROOT).as_posix(), token))
    assert violations == []

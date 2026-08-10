from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import obsidian_wiki.cli as cli
import obsidian_wiki.sync as sync
from obsidian_wiki import IMPLEMENTATION_ID, __version__


ROOT = Path(__file__).resolve().parents[1]


def write_portable(root: Path, body: str | None = None) -> Path:
    config = root / ".obsidian-wiki" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        body
        or f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"

[paths]
vault = "wiki"
sources = ["sources", "imports"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    return config


def write_legacy(path: Path, vault: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'OBSIDIAN_VAULT_PATH="{vault}"\n',
        encoding="utf-8",
    )
    return path


def run_info(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", "info", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


def warning_codes(warnings: object) -> list[str]:
    assert isinstance(warnings, list)
    return [warning["code"] for warning in warnings]


def test_info_json_reports_portable_runtime_and_global_installation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    config = write_portable(project)
    global_vault = tmp_path / "global-vault"
    write_legacy(home / ".obsidian-wiki" / "config", global_vault)

    result = run_info(home, project, "--json", "--pretty")

    assert result.returncode == 0
    assert result.stderr == ""
    data = payload(result)
    runtime = data["runtime"]
    assert runtime == {
        "status": "resolved",
        "mode": "portable",
        "source": str(config.resolve()),
        "vault": str((project / "wiki").resolve()),
        "portable": {
            "root": str(project.resolve()),
            "sources": [
                str((project / "sources").resolve()),
                str((project / "imports").resolve()),
            ],
            "skills": str((project / ".skills").resolve()),
            "local_state": str((project / ".obsidian-wiki" / "local").resolve()),
        },
    }
    assert data["installation"]["global_default"]["vault"] == str(global_vault)
    assert data["warnings"] == []


def test_info_json_explicit_vault_warns_when_portable_context_is_overridden(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    write_portable(project)
    write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-vault")
    vault = (project / "wiki").resolve()

    result = run_info(home, project, "--vault", str(vault), "--json")

    assert result.returncode == 0
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["mode"] == "explicit"
    assert [warning["code"] for warning in data["warnings"]] == [
        "portable-context-overridden"
    ]


def test_info_json_without_config_is_available_with_setup_guidance(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = run_info(home, cwd, "--json")

    assert result.returncode == 0
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["status"] == "unconfigured"
    assert "obsidian-wiki setup" in data["runtime"]["guidance"]
    assert data["installation"]["version"]


def test_info_json_invalid_authoritative_portable_is_one_error_document(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    write_portable(project, "this is not valid TOML = [")

    result = run_info(home, project, "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    data = payload(result)
    assert data["runtime"]["status"] == "error"
    assert "invalid portable configuration" in data["runtime"]["error"]
    assert data["installation"]["skills"]


def test_info_human_output_separates_runtime_installation_and_warning(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    write_portable(project)
    vault = (project / "wiki").resolve()

    result = run_info(home, project, "--vault", str(vault))

    assert result.returncode == 0
    assert "Runtime context" in result.stdout
    assert "CLI installation" in result.stdout
    assert "skills root:" in result.stdout
    assert result.stderr.count("portable context") == 1


def test_info_human_output_uses_indented_section_layout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    config = write_portable(project)

    result = run_info(home, project)

    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[:10] == [
        "Runtime context",
        "  status: resolved",
        "  mode: portable",
        f"  source: {config.resolve()}",
        f"  vault: {(project / 'wiki').resolve()}",
        f"  repository: {project.resolve()}",
        f"  source: {(project / 'sources').resolve()}",
        f"  source: {(project / 'imports').resolve()}",
        f"  skills: {(project / '.skills').resolve()}",
        f"  local state: {(project / '.obsidian-wiki' / 'local').resolve()}",
    ]
    assert lines[10] == ""
    assert lines[11] == "CLI installation"
    assert all(line.startswith("  ") for line in lines[12:19])
    assert lines[14].startswith("  skills root: ")
    assert lines[18] == "  agent installs:"
    assert lines[19:]
    assert all(line.startswith("    ") for line in lines[19:])


@pytest.mark.parametrize(
    ("name", "setup", "arg", "expected_mode"),
    [
        ("named", "named", "@work", "named"),
        ("env", "env", None, "env"),
        ("global", "global", None, "global"),
        ("explicit", "none", "explicit-wiki", "explicit"),
    ],
)
def test_info_json_uses_each_resolution_source(
    tmp_path: Path, name: str, setup: str, arg: str | None, expected_mode: str
) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project" / "nested"
    cwd.mkdir(parents=True)
    if setup == "named":
        write_legacy(home / ".obsidian-wiki" / "config.work", tmp_path / "named-wiki")
    elif setup == "env":
        write_legacy(cwd.parent / ".env", tmp_path / "env-wiki")
    elif setup == "global":
        write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-wiki")

    result = run_info(home, cwd, *( ["--vault", arg] if arg else [] ), "--json")

    assert result.returncode == 0, (name, result.stderr)
    assert result.stderr == ""
    assert payload(result)["runtime"]["mode"] == expected_mode


def test_info_explicit_does_not_mutate_config_files_or_symlink_targets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    default = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "default")
    named_target = write_legacy(home / "saved-work-config", tmp_path / "work")
    named = home / ".obsidian-wiki" / "config.work"
    named.symlink_to(named_target)
    before = {
        "default": default.read_bytes(),
        "named_target": named_target.read_bytes(),
        "named_link": os.readlink(named),
    }

    result = run_info(home, cwd, "--vault", "@work", "--json")

    assert result.returncode == 0
    assert result.stderr == ""
    assert default.read_bytes() == before["default"]
    assert named_target.read_bytes() == before["named_target"]
    assert os.readlink(named) == before["named_link"]


def test_info_json_has_no_human_rendering_glyphs_or_headings(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = run_info(tmp_path / "home", cwd, "--json")

    assert result.returncode == 0
    for token in ("⚠️", "✅", "Runtime context", "CLI installation"):
        assert token not in result.stdout


def test_info_invalid_utf8_global_config_preserves_json_and_human_contracts(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    config = home / ".obsidian-wiki" / "config"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"OBSIDIAN_VAULT_PATH=\xff\n")
    args = ("--vault", str(tmp_path / "explicit-vault"))

    json_result = run_info(home, cwd, *args, "--json")

    assert json_result.returncode == 0
    assert json_result.stderr == ""
    data = payload(json_result)
    assert data["runtime"]["status"] == "resolved"
    assert data["installation"]["global_default"] == {
        "configured": True,
        "vault": None,
        "setup_version": None,
        "sync_remote": None,
    }
    assert warning_codes(data["warnings"]) == [
        "installation-global-config-invalid"
    ]

    human_result = run_info(home, cwd, *args)

    assert human_result.returncode == 0
    assert "Traceback" not in human_result.stderr
    assert human_result.stderr.count("warning: could not inspect global config") == 1


def test_installation_payload_parses_global_shell_syntax_and_normalizes_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = home / ".obsidian-wiki" / "config"
    config.parent.mkdir(parents=True)
    config.write_text(
        "export OBSIDIAN_VAULT_PATH='vaults/default' # personal vault\n"
        f"OBSIDIAN_WIKI_VERSION='{__version__}' # current CLI\n",
        encoding="utf-8",
    )
    expected_vault = (config.parent / "vaults" / "default").resolve()
    inspected: list[Path] = []
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)
    monkeypatch.setattr(
        sync, "get_remote", lambda vault: inspected.append(vault) or "git@example/wiki"
    )

    installation, warnings = cli._installation_payload()

    assert installation["global_default"] == {
        "configured": True,
        "vault": str(expected_vault),
        "setup_version": __version__,
        "sync_remote": "git@example/wiki",
    }
    assert inspected == [expected_vault]
    assert warnings == []


def test_installation_payload_contains_invalid_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = home / ".obsidian-wiki" / "config"
    config.parent.mkdir(parents=True)
    config.write_text("OBSIDIAN_VAULT_PATH='unterminated\n", encoding="utf-8")
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)

    installation, warnings = cli._installation_payload()

    assert installation["global_default"] == {
        "configured": True,
        "vault": None,
        "setup_version": None,
        "sync_remote": None,
    }
    assert warning_codes(warnings) == ["installation-global-config-invalid"]
    assert "unterminated quoted value" in warnings[0]["message"]
    assert warnings[0]["hint"] == "fix the global config or run: obsidian-wiki setup"


@pytest.mark.parametrize(
    "inspection_error",
    [
        PermissionError("global config denied"),
        UnicodeError("global config decode failed"),
    ],
    ids=["filesystem", "decode"],
)
def test_installation_payload_contains_expected_global_inspection_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inspection_error: BaseException,
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)

    def fail_inspection(_path: Path, *, home: Path):
        raise inspection_error

    monkeypatch.setattr(cli, "load_global_config", fail_inspection)

    installation, warnings = cli._installation_payload()

    assert installation["global_default"]["configured"] is True
    assert installation["global_default"]["vault"] is None
    assert warning_codes(warnings) == ["installation-global-config-invalid"]
    assert str(inspection_error) in warnings[0]["message"]


def test_installation_payload_contains_git_inspection_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)

    def missing_git(_vault: Path) -> str | None:
        raise FileNotFoundError("git executable not found")

    monkeypatch.setattr(sync, "get_remote", missing_git)

    installation, warnings = cli._installation_payload()

    assert installation["global_default"]["sync_remote"] is None
    assert warning_codes(warnings) == ["installation-sync-inspection-failed"]
    assert "git executable not found" in warnings[0]["message"]
    assert warnings[0]["hint"] == "check Git availability and vault permissions"


def test_installation_payload_does_not_swallow_programming_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)

    def programmer_error(_vault: Path) -> str | None:
        raise TypeError("unexpected programmer error")

    monkeypatch.setattr(sync, "get_remote", programmer_error)

    with pytest.raises(TypeError, match="unexpected programmer error"):
        cli._installation_payload()


def test_agent_install_payload_requires_readable_skill_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    root = home / ".agent" / "skills"
    good = root / "good"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text("# Good\n", encoding="utf-8")
    (root / "empty").mkdir()
    regular_file = root / "ordinary.txt"
    regular_file.write_text("not a skill\n", encoding="utf-8")
    (root / "link-to-file").symlink_to(regular_file)
    (root / "broken").symlink_to(root / "missing")
    linked_target = tmp_path / "linked-skill"
    linked_target.mkdir()
    (linked_target / "SKILL.md").write_text("# Linked\n", encoding="utf-8")
    (root / "linked").symlink_to(linked_target, target_is_directory=True)
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(
        cli,
        "GLOBAL_AGENT_DIRS",
        [(".agent/skills", "test agent", None)],
    )
    bundled = {"good", "linked", "empty", "link-to-file", "broken"}

    partial = cli._agent_install_payload(bundled)[0]
    complete = cli._agent_install_payload({"good", "linked"})[0]

    assert partial["installed"] == 2
    assert partial["missing"] == ["broken", "empty", "link-to-file"]
    assert partial["status"] == "partial"
    assert complete["installed"] == 2
    assert complete["missing"] == []
    assert complete["status"] == "complete"


def test_agent_directory_permission_error_becomes_installation_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    blocked = home / ".agent" / "skills"
    blocked.mkdir(parents=True)
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)
    monkeypatch.setattr(
        cli,
        "GLOBAL_AGENT_DIRS",
        [(".agent/skills", "test agent", None)],
    )
    original_iterdir = Path.iterdir

    def permission_error(path: Path):
        if path == blocked:
            raise PermissionError("agent directory denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", permission_error)

    installation, warnings = cli._installation_payload()

    assert installation["agent_installs"][0]["status"] == "partial"
    assert warning_codes(warnings) == ["installation-agent-skills-unreadable"]
    assert str(blocked) in warnings[0]["message"]
    assert "agent directory denied" in warnings[0]["message"]


def test_stale_warning_and_agent_payload_share_skill_validity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    root = home / ".claude" / "skills"
    good = root / "good"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text("# Good\n", encoding="utf-8")
    (root / "empty").mkdir()
    regular_file = root / "ordinary.txt"
    regular_file.write_text("not a skill\n", encoding="utf-8")
    (root / "link-to-file").symlink_to(regular_file)
    (root / "broken").symlink_to(root / "missing")
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)
    monkeypatch.setattr(
        cli,
        "GLOBAL_AGENT_DIRS",
        [(".claude/skills", "test agent", None)],
    )
    bundled = {"good", "empty", "link-to-file", "broken"}

    record = cli._agent_install_payload(bundled)[0]
    warnings = cli._stale_install_warnings(bundled)

    assert record["missing"] == ["broken", "empty", "link-to-file"]
    assert warning_codes(warnings) == ["agent-skills-missing"]
    assert warnings[0]["message"].startswith("3 skill(s) missing")


def test_cmd_info_contains_global_config_metadata_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)
    original_is_file = Path.is_file

    def metadata_failure(path: Path) -> bool:
        if path == config:
            raise PermissionError("global config metadata denied")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", metadata_failure)
    json_args = argparse.Namespace(
        vault=str(tmp_path / "explicit-vault"), json=True, pretty=False
    )

    returncode = cli.cmd_info(json_args)
    captured = capsys.readouterr()

    assert returncode == 0
    assert captured.err == ""
    data = json.loads(captured.out)
    assert warning_codes(data["warnings"]) == [
        "installation-global-config-invalid"
    ]

    human_args = argparse.Namespace(
        vault=str(tmp_path / "explicit-vault"), json=False, pretty=False
    )
    returncode = cli.cmd_info(human_args)
    captured = capsys.readouterr()

    assert returncode == 0
    assert "Traceback" not in captured.err
    assert captured.err.count("warning: could not inspect global config") == 1


def test_agent_root_metadata_failure_is_structured_and_stale_check_is_resilient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    root = home / ".claude" / "skills"
    root.mkdir(parents=True)
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)
    monkeypatch.setattr(
        cli,
        "GLOBAL_AGENT_DIRS",
        [(".claude/skills", "test agent", None)],
    )
    original_is_dir = Path.is_dir

    def metadata_failure(path: Path) -> bool:
        if path == root:
            raise PermissionError("agent root metadata denied")
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", metadata_failure)

    installation, warnings = cli._installation_payload()

    assert installation["agent_installs"][0]["status"] == "partial"
    assert warning_codes(warnings) == ["installation-agent-skills-unreadable"]

    cli._check_stale()
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("warning: could not inspect agent skills") == 1


def test_unreadable_skill_entry_does_not_poison_valid_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    root = home / ".claude" / "skills"
    for name in ("alpha", "bad", "gamma"):
        skill = root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    bad_skill = root / "bad" / "SKILL.md"
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)
    monkeypatch.setattr(
        cli,
        "GLOBAL_AGENT_DIRS",
        [(".claude/skills", "test agent", None)],
    )
    original_open = Path.open

    def read_failure(path: Path, *args, **kwargs):
        if path == bad_skill:
            raise PermissionError("SKILL.md denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read_failure)
    bundled = {"alpha", "bad", "gamma"}
    inspection_warnings: list[dict[str, str]] = []

    record = cli._agent_install_payload(
        bundled, warning_sink=inspection_warnings
    )[0]
    stale_warnings = cli._stale_install_warnings(bundled)

    assert record["installed"] == 2
    assert record["missing"] == ["bad"]
    assert record["status"] == "partial"
    assert warning_codes(inspection_warnings) == [
        "installation-agent-skill-unreadable"
    ]
    assert str(bad_skill) in inspection_warnings[0]["message"]
    assert warning_codes(stale_warnings) == ["agent-skills-missing"]
    assert stale_warnings[0]["message"].startswith("1 skill(s) missing")


def test_info_json_contains_malformed_global_vault_for_explicit_and_global_runtime(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    config = home / ".obsidian-wiki" / "config"
    config.parent.mkdir(parents=True)
    config.write_text('OBSIDIAN_VAULT_PATH="bad\x00vault"\n', encoding="utf-8")

    explicit = run_info(
        home,
        cwd,
        "--vault",
        str(tmp_path / "explicit-vault"),
        "--json",
    )

    assert explicit.returncode == 0
    assert explicit.stderr == ""
    assert "\x00" not in explicit.stdout
    explicit_payload = payload(explicit)
    assert explicit_payload["runtime"]["status"] == "resolved"
    assert warning_codes(explicit_payload["warnings"]) == [
        "installation-global-config-invalid"
    ]

    selected = run_info(home, cwd, "--json")

    assert selected.returncode == 1
    assert selected.stderr == ""
    assert "\x00" not in selected.stdout
    selected_payload = payload(selected)
    assert selected_payload["runtime"]["status"] == "error"
    assert "invalid vault path" in selected_payload["runtime"]["error"]
    assert warning_codes(selected_payload["warnings"]) == [
        "installation-global-config-invalid"
    ]


def test_bundled_inventory_failure_marks_existing_agent_status_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    config = write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "vault")
    root = home / ".agent" / "skills"
    skill = root / "existing"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Existing\n", encoding="utf-8")
    monkeypatch.setattr(cli, "HOME", home)
    monkeypatch.setattr(cli, "GLOBAL_CONFIG", config)
    monkeypatch.setattr(
        cli,
        "GLOBAL_AGENT_DIRS",
        [(".agent/skills", "test agent", None)],
    )

    def inventory_failure() -> list[str]:
        raise PermissionError("bundled inventory denied")

    monkeypatch.setattr(cli, "list_skills", inventory_failure)

    installation, warnings = cli._installation_payload()

    assert installation["bundled_skills"] is None
    assert warning_codes(warnings) == ["installation-bundled-skills-unreadable"]
    assert installation["agent_installs"] == [
        {
            "label": "test agent",
            "path": str(root),
            "status": "unknown",
            "installed": None,
            "bundled": None,
            "missing": None,
        }
    ]

    cli._print_info(
        {
            "runtime": {"status": "unconfigured"},
            "installation": installation,
            "warnings": warnings,
        }
    )
    captured = capsys.readouterr()
    assert "  bundled skills: (unknown)" in captured.out
    assert "    test agent: ?/? (unknown)" in captured.out

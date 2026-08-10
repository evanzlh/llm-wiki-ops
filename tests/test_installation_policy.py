import os
import shutil
import subprocess
from pathlib import Path

import pytest

from obsidian_wiki import SOURCE_INSTALL_COMMAND, SOURCE_REINSTALL_COMMAND


ROOT = Path(__file__).resolve().parents[1]


def _uv_tool_environment(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    ignored = {
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_NO_CACHE",
        "UV_OFFLINE",
        "UV_REFRESH",
        "UV_REFRESH_PACKAGE",
        "UV_REINSTALL",
        "UV_REINSTALL_PACKAGE",
    }
    env = {
        key: value
        for key, value in env.items()
        if key not in ignored and not key.startswith("GIT_")
    }
    env["PYTHONNOUSERSITE"] = "1"
    env.update(
        HOME=str(tmp_path / "home"),
        UV_TOOL_DIR=str(tmp_path / "tools"),
        UV_TOOL_BIN_DIR=str(tmp_path / "bin"),
        UV_CACHE_DIR=str(tmp_path / "cache"),
    )
    return env


def test_uv_tool_environment_ignores_parent_behavior_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/inherited/python/path")
    monkeypatch.setenv("PYTHONHOME", "/inherited/python/home")
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
    for key in (
        "UV_NO_CACHE",
        "UV_OFFLINE",
        "UV_REFRESH",
        "UV_REFRESH_PACKAGE",
        "UV_REINSTALL",
        "UV_REINSTALL_PACKAGE",
    ):
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("GIT_DIR", "/inherited/git/dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/inherited/git/work-tree")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.setenv("UV_INDEX_URL", "https://index.example.invalid/simple")
    monkeypatch.setenv("INSTALLATION_POLICY_UNRELATED", "retained")

    env = _uv_tool_environment(tmp_path)

    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert not {
        "UV_NO_CACHE",
        "UV_OFFLINE",
        "UV_REFRESH",
        "UV_REFRESH_PACKAGE",
        "UV_REINSTALL",
        "UV_REINSTALL_PACKAGE",
    } & env.keys()
    assert not any(key.startswith("GIT_") for key in env)
    assert env["HOME"] == str(tmp_path / "home")
    assert env["UV_TOOL_DIR"] == str(tmp_path / "tools")
    assert env["UV_TOOL_BIN_DIR"] == str(tmp_path / "bin")
    assert env["UV_CACHE_DIR"] == str(tmp_path / "cache")
    assert env["UV_INDEX_URL"] == "https://index.example.invalid/simple"
    assert env["INSTALLATION_POLICY_UNRELATED"] == "retained"


def test_unsupported_install_entrypoints_are_absent() -> None:
    absent = (
        "setup.sh",
        "SETUP.md",
        ".github/workflows/publish.yml",
        ".github/workflows/setup.yml",
    )
    assert [path for path in absent if (ROOT / path).exists()] == []


def test_build_metadata_is_retained_for_uv_source_install() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'build-backend = "hatchling.build"' in pyproject
    assert 'obsidian-wiki = "obsidian_wiki.cli:main"' in pyproject
    assert '".skills" = "obsidian_wiki/_data/skills"' in pyproject


def test_source_reinstall_command_refreshes_cached_builds() -> None:
    assert (
        SOURCE_REINSTALL_COMMAND
        == "uv tool install --force --reinstall --link-mode copy ."
    )


def test_bilingual_readmes_disclose_the_fork_and_only_source_install() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README_ZH.md").read_text(encoding="utf-8")
    assert not (ROOT / "README_TW.md").exists()
    for text in (english, chinese):
        assert "Ar9av/obsidian-wiki" in text
        assert "5ef66b6bec8b26bab6594ac37fb4d8371469fbab" in text
        assert "git clone https://github.com/evanzlh/obsidian-wiki.git" in text
        assert SOURCE_INSTALL_COMMAND in text
        assert SOURCE_REINSTALL_COMMAND in text
        assert "docs/fork.md" in text
        assert "pip install obsidian-wiki" not in text
        assert "setup.sh" not in text


def test_fork_policy_is_explicit() -> None:
    policy = (ROOT / "docs/fork.md").read_text(encoding="utf-8")
    assert "independently" in policy
    assert "does not track future upstream changes" in policy
    assert "Portable Repository mode" in policy


def test_contributor_skill_flow_rebuilds_installed_cli_before_setup() -> None:
    contributing = (ROOT / "docs/contributing.md").read_text(encoding="utf-8")
    adding_skill = contributing.split("## Adding a new skill", 1)[1].split(
        "## Keeping both READMEs in sync", 1
    )[0]
    rebuild = adding_skill.index(SOURCE_REINSTALL_COMMAND)
    assert rebuild < adding_skill.index("obsidian-wiki setup")
    assert rebuild < adding_skill.index("Test by saying")


def test_agents_describes_obsidian_wiki_repo_as_bundled_data_root() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "`OBSIDIAN_WIKI_REPO` (installed CLI bundled-data root)" in agents
    assert "`OBSIDIAN_WIKI_REPO` (where this repo is cloned)" not in agents


def test_factory_resolves_skill_creator_from_bundled_data_root() -> None:
    factory = (ROOT / ".skills/vault-skill-factory/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "$OBSIDIAN_WIKI_REPO/skills/skill-creator/scripts/" in factory
    assert "$OBSIDIAN_WIKI_REPO/.skills/skill-creator/scripts/" not in factory


def test_no_unsupported_install_guidance_remains() -> None:
    checked_roots = (ROOT / "docs", ROOT / ".skills")
    files = [ROOT / "README.md", ROOT / "README_ZH.md", ROOT / "AGENTS.md"]
    for base in checked_roots:
        files.extend(
            path
            for path in base.rglob("*")
            if path.is_file() and "superpowers" not in path.parts
        )
    banned = (
        "pip install obsidian-wiki",
        "pipx install obsidian-wiki",
        "npx skills add Ar9av/obsidian-wiki",
        "bash setup.sh",
        "uv tool install git+",
        "uv tool install .",
        "uv tool install --force .",
        "uv tool install --force --link-mode copy .",
    )
    offenders = {
        path.relative_to(ROOT).as_posix(): token
        for path in files
        for token in banned
        if token in path.read_text(encoding="utf-8", errors="ignore")
    }
    assert offenders == {}


def test_no_unsupported_install_guidance_in_user_facing_tooling() -> None:
    checked_roots = (ROOT / ".github", ROOT / "obsidian_wiki", ROOT / "tools")
    files = [ROOT / "pyproject.toml", ROOT / ".env.example"]
    for base in checked_roots:
        files.extend(
            path
            for path in base.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    banned = (
        "pip install obsidian-wiki",
        "pipx install obsidian-wiki",
        "npx skills add Ar9av/obsidian-wiki",
        "bash setup.sh",
        "uv tool install git+",
        "uv tool install .",
        "uv tool install --force .",
        "uv tool install --force --link-mode copy .",
    )
    offenders = {
        path.relative_to(ROOT).as_posix(): token
        for path in files
        for token in banned
        if token in path.read_text(encoding="utf-8", errors="ignore")
    }
    assert offenders == {}


@pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv is required by the supported installer"
)
def test_uv_tool_install_survives_source_move(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "dist", "build", "__pycache__"
        ),
        symlinks=True,
    )
    env = _uv_tool_environment(tmp_path)
    git_dir = source / ".git"
    assert not git_dir.exists(), "source copy unexpectedly retained Git metadata"
    subprocess.run(
        ["git", "init"],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert git_dir.is_dir() and not git_dir.is_symlink()
    assert git_dir.resolve().is_relative_to(source.resolve())
    subprocess.run(
        ["git", "add", "--all"],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Installation Policy Test",
            "-c",
            "user.email=installation-policy@example.invalid",
            "commit",
            "-m",
            "test: establish source baseline",
        ],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        SOURCE_INSTALL_COMMAND.split(),
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    marker = "<!-- source-reinstall-marker: cached-build-refresh -->"
    source_skill = source / ".skills" / "wiki-ingest" / "SKILL.md"
    source_skill.write_text(
        source_skill.read_text(encoding="utf-8") + f"\n{marker}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", ".skills/wiki-ingest/SKILL.md"],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Installation Policy Test",
            "-c",
            "user.email=installation-policy@example.invalid",
            "commit",
            "-m",
            "test: update source skill marker",
            "--only",
            ".skills/wiki-ingest/SKILL.md",
        ],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        SOURCE_REINSTALL_COMMAND.split(),
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    source.rename(tmp_path / "source-moved")
    bin_dir = Path(env["UV_TOOL_BIN_DIR"])
    executable = shutil.which("obsidian-wiki", path=str(bin_dir))
    assert executable is not None, f"obsidian-wiki was not installed in {bin_dir}"
    result = subprocess.run(
        [executable, "--version"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert "evanzlh/obsidian-wiki" in result.stdout
    info = subprocess.run(
        [executable, "info"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    skills_lines = [
        line.partition(":")[2].strip()
        for line in info.stdout.splitlines()
        if line.startswith("skills:")
    ]
    assert len(skills_lines) == 1, info.stdout
    skills_path = Path(skills_lines[0]).resolve()
    tool_dir = Path(env["UV_TOOL_DIR"]).resolve()
    assert skills_path.is_relative_to(tool_dir), (
        f"bundled skills resolved outside isolated tool dir: {skills_path}"
    )
    bundled_files = [
        path
        for path in skills_path.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    assert bundled_files, f"no regular bundled skill files found below {skills_path}"
    hard_linked = {
        path.relative_to(skills_path).as_posix(): path.stat().st_nlink
        for path in bundled_files
        if path.stat().st_nlink != 1
    }
    assert hard_linked == {}, f"bundled skill files have multiple links: {hard_linked}"
    bundled = subprocess.run(
        [executable, "list"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert "wiki-ingest" in bundled.stdout.splitlines()
    assert marker in (skills_path / "wiki-ingest" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    portable = tmp_path / "portable"
    setup = subprocess.run(
        [executable, "setup", "--portable", str(portable)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert "Portable repository scaffolded" in setup.stdout
    doctor = subprocess.run(
        [executable, "doctor"],
        cwd=portable,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert "obsidian-wiki doctor: pass" in doctor.stdout
    subprocess.run(
        ["git", "init"],
        cwd=portable,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    check = subprocess.run(
        [executable, "check"],
        cwd=portable,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert "portable check: pass (0 errors, 0 warnings)" in check.stdout
    home = Path(env["HOME"])
    assert not (home / ".obsidian-wiki").exists()
    assert not any(
        (home / agent / "skills").exists() for agent in (".claude", ".codex", ".agents")
    )

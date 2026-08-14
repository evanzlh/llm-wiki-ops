from configparser import ConfigParser
from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

import pytest

from obsidian_wiki import IMPLEMENTATION_ID, SOURCE_INSTALL_COMMAND, SOURCE_REINSTALL_COMMAND
from obsidian_wiki.config import ConfigError, load_portable_config
from obsidian_wiki.portable import PROJECT_AGENT_DIRS, render_portable_config


ROOT = Path(__file__).resolve().parents[1]


def _safe_tree_snapshot(
    root: Path,
) -> tuple[tuple[str, str, int, str | None, int, int, int], ...]:
    entries: list[tuple[str, str, int, str | None, int, int, int]] = []

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(metadata.st_mode)
            identity = (
                stat.S_IFMT(metadata.st_mode),
                metadata.st_rdev,
                metadata.st_size,
            )
            if stat.S_ISLNK(metadata.st_mode):
                entries.append(
                    (relative, "symlink", mode, os.readlink(path), *identity)
                )
            elif stat.S_ISDIR(metadata.st_mode):
                entries.append((relative, "directory", mode, None, *identity))
                visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                entries.append(
                    (
                        relative,
                        "file",
                        mode,
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                        *identity,
                    )
                )
            else:
                entries.append((relative, "special", mode, None, *identity))

    visit(root)
    return tuple(entries)


def test_safe_home_snapshot_detects_added_agent_skill_tree(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    before = _safe_tree_snapshot(home)
    skill = home / ".gemini/skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# unexpected global skill\n", encoding="utf-8")

    assert _safe_tree_snapshot(home) != before


def test_safe_home_snapshot_records_special_file_identity(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fifo = home / "owner-fifo"
    try:
        os.mkfifo(fifo)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"FIFO creation is unavailable: {exc}")
    metadata = fifo.lstat()

    assert _safe_tree_snapshot(home) == (
        (
            "owner-fifo",
            "special",
            stat.S_IMODE(metadata.st_mode),
            None,
            stat.S_IFMT(metadata.st_mode),
            metadata.st_rdev,
            metadata.st_size,
        ),
    )


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
        XDG_CACHE_HOME=str(tmp_path / "xdg-cache"),
        XDG_CONFIG_HOME=str(tmp_path / "xdg-config"),
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
    assert env["XDG_CACHE_HOME"] == str(tmp_path / "xdg-cache")
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "xdg-config")
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
    assert 'llmwikiops = "obsidian_wiki.cli:main"' in pyproject
    assert 'obsidian-wiki = "obsidian_wiki.cli:main"' not in pyproject
    assert '".skills" = "obsidian_wiki/_data/skills"' not in pyproject
    assert (ROOT / "obsidian_wiki/_data/skills/llm-wiki/SKILL.md").is_file()


@pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv is required by the supported builder"
)
def test_distribution_artifacts_contain_runtime_assets_not_discovery_trees(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dist"
    output.mkdir()
    assert not list(output.iterdir())
    subprocess.run(
        ["uv", "build", "--out-dir", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    output_files = sorted(path for path in output.iterdir() if path.is_file())
    artifacts = [
        path
        for path in output_files
        if path.suffix == ".whl" or path.name.endswith(".tar.gz")
    ]
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    non_artifact_files = [path for path in output_files if path not in artifacts]
    assert len(wheels) == 1
    assert len(sdists) == 1
    assert non_artifact_files == [output / ".gitignore"]
    assert not [
        path for path in output_files if path.name.startswith("obsidian_wiki-")
    ]
    assert all(path.name.startswith("llm_wiki_ops-") for path in artifacts)

    expected_data = {
        path.relative_to(ROOT).as_posix()
        for root in (
            ROOT / "obsidian_wiki/_data/skills",
            ROOT / "obsidian_wiki/_data/bootstrap",
        )
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }
    expected_data.add("obsidian_wiki/_data/legacy-skill-digests-v1.json")
    forbidden_roots = (
        ".skills/",
        ".claude/skills/",
        ".cursor/skills/",
        ".windsurf/skills/",
        ".agents/skills/",
        ".pi/skills/",
        ".kiro/skills/",
    )

    for artifact in artifacts:
        if artifact in wheels:
            with zipfile.ZipFile(artifact) as archive:
                raw_names = archive.namelist()
                assert "obsidian_wiki/_data/.env.example" not in raw_names
                metadata_path = next(
                    name for name in raw_names if name.endswith(".dist-info/METADATA")
                )
                entry_points_path = next(
                    name
                    for name in raw_names
                    if name.endswith(".dist-info/entry_points.txt")
                )
                metadata = BytesParser(policy=policy.default).parsebytes(
                    archive.read(metadata_path)
                )
                entry_points = ConfigParser()
                entry_points.optionxform = str
                entry_points.read_string(archive.read(entry_points_path).decode("utf-8"))
                assert metadata["Name"] == "llm-wiki-ops"
                assert metadata["Summary"] == (
                    "LLM-oriented operational framework for durable Markdown "
                    "knowledge bases"
                )
                project_urls = {
                    name: url
                    for value in metadata.get_all("Project-URL", [])
                    for name, url in [value.split(", ", 1)]
                }
                assert project_urls == {
                    "Homepage": "https://github.com/evanzlh/llm-wiki-ops",
                    "Repository": "https://github.com/evanzlh/llm-wiki-ops",
                    "Issues": "https://github.com/evanzlh/llm-wiki-ops/issues",
                    "Changelog": "https://github.com/evanzlh/llm-wiki-ops/releases",
                    "Upstream": "https://github.com/Ar9av/obsidian-wiki",
                }
                assert entry_points.defaults() == {}
                assert entry_points.sections() == ["console_scripts"]
                assert dict(entry_points["console_scripts"]) == {
                    "llmwikiops": "obsidian_wiki.cli:main"
                }
        else:
            with tarfile.open(artifact) as archive:
                raw_names = archive.getnames()
        names = {
            "/".join(Path(name).parts[1:])
            if Path(name).parts
            and Path(name).parts[0].startswith("llm_wiki_ops-")
            else name
            for name in raw_names
        }

        assert expected_data <= names, artifact.name
        assert "obsidian_wiki/__init__.py" in names, artifact.name
        assert not {
            name
            for name in names
            if name.startswith(forbidden_roots)
            or name.endswith("/.claude/settings.json")
            or "wiki-stop-capture" in name
        }, artifact.name


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
        assert "git clone https://github.com/evanzlh/llm-wiki-ops.git" in text
        assert SOURCE_INSTALL_COMMAND in text
        assert SOURCE_REINSTALL_COMMAND in text
        assert "docs/fork.md" in text
        assert "pip install llm-wiki-ops" not in text
        assert "setup.sh" not in text


def test_portable_cli_upgrade_docs_require_two_step_compatibility_protocol() -> None:
    english_paths = (
        "README.md",
        "docs/installation.md",
        "docs/cli.md",
        "docs/contributing.md",
    )
    marker = "two-step CLI and repository upgrade protocol"
    for relative in english_paths:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert marker in text, relative
        protocol = text.split(marker, 1)[1]
        section_ends = [
            index
            for heading in ("\n## ", "\n### ")
            if (index := protocol.find(heading)) >= 0
        ]
        if section_ends:
            protocol = protocol[: min(section_ends)]
        protocol = " ".join(protocol.split())
        protocol_folded = protocol.casefold()
        for required in (
            "`requires_cli`",
            "PEP 440",
            "branch",
            "collaborator",
            "does not rewrite",
            "commit",
            SOURCE_REINSTALL_COMMAND,
        ):
            assert required.casefold() in protocol_folded, (relative, required)
        assert "fail closed" in protocol_folded or "fails closed" in protocol_folded
        constraint = protocol.index("`requires_cli`")
        upgrade = protocol.index("llmwikiops repo upgrade-skills")
        check = protocol.index("llmwikiops check")
        diff = protocol.index("git diff")
        assert constraint < upgrade < check < diff, relative

    chinese = (ROOT / "README_ZH.md").read_text(encoding="utf-8")
    marker_zh = "两步 CLI 与仓库升级协议"
    assert marker_zh in chinese
    protocol_zh = chinese.split(marker_zh, 1)[1].split("\n## ", 1)[0]
    protocol_zh = " ".join(protocol_zh.split())
    for required in (
        "`requires_cli`",
        "PEP 440",
        "分支",
        "协作者",
        "失败并停止",
        "不会改写",
        "提交",
        SOURCE_REINSTALL_COMMAND,
    ):
        assert required in protocol_zh, required
    constraint = protocol_zh.index("`requires_cli`")
    upgrade = protocol_zh.index("llmwikiops repo upgrade-skills")
    check = protocol_zh.index("llmwikiops check")
    diff = protocol_zh.index("git diff")
    assert constraint < upgrade < check < diff


def test_upgrade_version_transition_fails_closed_until_owner_edits_constraint(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".obsidian-wiki/config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        render_portable_config(version="2026.8.3"), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="requires CLI"):
        load_portable_config(
            config_path,
            installed_version="2026.9.1",
            implementation=IMPLEMENTATION_ID,
        )

    reviewed = config_path.read_text(encoding="utf-8").replace(
        'requires_cli = ">=2026.8,<2026.9"',
        'requires_cli = ">=2026.8,<2026.10"',
    )
    config_path.write_text(reviewed, encoding="utf-8")
    loaded = load_portable_config(
        config_path,
        installed_version="2026.9.1",
        implementation=IMPLEMENTATION_ID,
    )
    assert loaded.requires_cli == ">=2026.8,<2026.10"


def test_cli_quick_reference_does_not_skip_requires_cli_upgrade_step() -> None:
    cli = (ROOT / "docs/cli.md").read_text(encoding="utf-8")
    quick_reference = cli.split(
        "## Upgrade protocol", 1
    )[0]

    assert "llmwikiops repo upgrade-skills" not in quick_reference


def test_fork_policy_is_explicit() -> None:
    policy = (ROOT / "docs/fork.md").read_text(encoding="utf-8")
    assert "independently" in policy
    assert "does not track future upstream changes" in policy
    assert "single repository product" in policy


def test_contributor_skill_flow_rebuilds_installed_cli_before_setup() -> None:
    contributing = (ROOT / "docs/contributing.md").read_text(encoding="utf-8")
    adding_skill = contributing.split("## Test a skill change", 1)[1].split(
        "## Documentation", 1
    )[0]
    rebuild = adding_skill.index(SOURCE_REINSTALL_COMMAND)
    assert rebuild < adding_skill.index("llmwikiops setup")
    assert adding_skill.index("llmwikiops setup") < adding_skill.index(
        "llmwikiops check"
    )
    assert "llmwikiops repo sync-skills" in adding_skill
    assert "tests/test_asset_artifact_parity.py" in adding_skill
    assert "source checkout as a runtime fallback" in adding_skill


def test_agents_routes_repository_authority_without_global_source_variables() -> None:
    agents = (ROOT / "obsidian_wiki/_data/bootstrap/AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "canonical" in agents.casefold()
    assert "task" in agents.casefold()
    assert "nearest" in agents.casefold()
    assert "OBSIDIAN_WIKI_REPO" not in agents


def test_factory_uses_safe_managed_validator_from_nearest_repository(
    tmp_path: Path,
) -> None:
    factory = (ROOT / "obsidian_wiki/_data/skills/vault-skill-factory/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "$OBSIDIAN_WIKI_REPO" not in factory
    assert ".skills/skill-creator/scripts/quick_validate.py" in factory
    assert "llmwikiops repo sync-skills --json --pretty" in factory
    assert 'status: "clean"' in factory
    assert "Do not use `--apply`" in factory
    assert "uv run --with" not in factory
    assert "dynamically resolve or download" in factory
    assert "`sys.executable`" in factory
    assert "absolute interpreter" in factory
    assert "without a shell" in factory
    assert "Immediately before execution" in factory
    assert "package inventory expected digest" in factory

    repository = tmp_path / "repository"
    setup = subprocess.run(
        [sys.executable, "-m", "obsidian_wiki", "setup", str(repository)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert setup.returncode == 0, setup.stderr
    validator = repository / ".skills/skill-creator/scripts/quick_validate.py"
    metadata = validator.lstat()
    assert validator.is_file() and not validator.is_symlink()
    assert metadata.st_nlink == 1

    preflight = subprocess.run(
        [
            sys.executable,
            "-m",
            "obsidian_wiki",
            "repo",
            "sync-skills",
            "--json",
            "--pretty",
        ],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert preflight.returncode == 0, preflight.stderr
    payload = json.loads(preflight.stdout)
    assert payload["status"] == "clean"
    assert payload["warnings"] == []


def test_no_unsupported_install_guidance_remains() -> None:
    checked_roots = (ROOT / "docs", ROOT / "obsidian_wiki/_data/skills")
    files = [
        ROOT / "README.md",
        ROOT / "README_ZH.md",
        ROOT / "AGENTS.md",
        ROOT / "obsidian_wiki/_data/bootstrap/AGENTS.md",
    ]
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
    files = [ROOT / "pyproject.toml"]
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


def test_distribution_declares_the_posix_safety_boundary() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    classifiers = set(project["classifiers"])
    assert "Operating System :: OS Independent" not in classifiers
    assert "Operating System :: POSIX" in classifiers
    assert "Operating System :: POSIX :: Linux" in classifiers
    assert "Operating System :: MacOS :: MacOS X" in classifiers

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README_ZH.md").read_text(encoding="utf-8")
    installation = (ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
    assert "Linux or macOS" in readme
    assert "Linux 或 macOS" in readme_zh
    assert "Linux or macOS" in installation


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
    home = Path(env["HOME"])
    home.mkdir(parents=True)
    sentinel = home / "owner-sentinel.txt"
    sentinel.write_text("owner home remains unchanged\n", encoding="utf-8")
    home_before = _safe_tree_snapshot(home)
    canonical_skill_names = sorted(
        path.name
        for path in (source / "obsidian_wiki/_data/skills").iterdir()
        if path.is_dir()
    )
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
    source_skill = (
        source / "obsidian_wiki" / "_data" / "skills" / "wiki-ingest" / "SKILL.md"
    )
    source_skill.write_text(
        source_skill.read_text(encoding="utf-8") + f"\n{marker}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "obsidian_wiki/_data/skills/wiki-ingest/SKILL.md"],
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
            "obsidian_wiki/_data/skills/wiki-ingest/SKILL.md",
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
    original_source_path = source.resolve()
    moved_source = tmp_path / "source-moved"
    source.rename(moved_source)
    bin_dir = Path(env["UV_TOOL_BIN_DIR"])
    executable = shutil.which("llmwikiops", path=str(bin_dir))
    legacy_executable = shutil.which("obsidian-wiki", path=str(bin_dir))
    assert executable is not None, f"llmwikiops was not installed in {bin_dir}"
    assert legacy_executable is None, "obsidian-wiki must not be installed as an alias"
    result = subprocess.run(
        [executable, "--version"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    interpreter = Path(executable).read_text(encoding="utf-8").splitlines()[0][2:]
    installed_version = subprocess.run(
        [
            interpreter,
            "-c",
            "from importlib.metadata import version; print(version('llm-wiki-ops'))",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    assert installed_version != "0.0.0+dev"
    assert result.stdout == f"llmwikiops {installed_version} (evanzlh/llm-wiki-ops)\n"
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
        if line.strip().startswith("skills root:")
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
    assert bundled.stdout.splitlines() == canonical_skill_names
    assert marker in (skills_path / "wiki-ingest" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    bootstrap_root = skills_path.parent / "bootstrap"
    expected_bootstraps = (
        "AGENTS.md",
        "cursor/rules/obsidian-wiki.mdc",
        "windsurf/rules/obsidian-wiki.md",
        "kiro/steering/obsidian-wiki.md",
        "agent/rules/obsidian-wiki.md",
        "agent/workflows/obsidian-wiki.md",
        "github/copilot-instructions.md",
    )
    assert [
        relative
        for relative in expected_bootstraps
        if not (bootstrap_root / relative).is_file()
        or (bootstrap_root / relative).is_symlink()
    ] == []
    portable = tmp_path / "portable"
    setup = subprocess.run(
        [executable, "setup", str(portable)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert setup.returncode == 0, setup.stdout + setup.stderr
    assert "Repository scaffolded" in setup.stdout
    assert not (portable / ".git").exists() and not (portable / ".git").is_symlink()
    canonical_query = portable / ".skills/wiki-query/SKILL.md"
    query_bytes = canonical_query.read_bytes()
    assert b"Answer questions by searching the compiled LLMWikiOps" in query_bytes
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        mirrored = portable / agent_relative / "wiki-query/SKILL.md"
        assert mirrored.read_bytes() == query_bytes
    doctor = subprocess.run(
        [executable, "doctor"],
        cwd=portable,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "llmwikiops doctor: pass" in doctor.stdout
    check = subprocess.run(
        [executable, "check", "--json", "--pretty"],
        cwd=portable,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert json.loads(check.stdout) == {
        "status": "warn",
        "errors": 0,
        "warnings": 1,
        "issues": [
            {
                "code": "git-unavailable",
                "path": ".",
                "message": "Git is unavailable or the repository is not a worktree",
                "severity": "warning",
            }
        ],
    }
    sync = subprocess.run(
        [executable, "repo", "sync-skills", "--json"],
        cwd=portable,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert sync.returncode == 0, sync.stdout + sync.stderr
    sync_payload = json.loads(sync.stdout)
    assert sync_payload["status"] == "clean"
    assert sync_payload["canonical_skills"] == canonical_skill_names
    assert sync_payload["warnings"] == []
    assert len(sync_payload["targets"]) == len(PROJECT_AGENT_DIRS)
    assert all(
        target["added"] == []
        and target["changed"] == []
        and target["removed"] == []
        and target["unsafe"] == []
        for target in sync_payload["targets"]
    )
    assert _safe_tree_snapshot(home) == home_before
    assert not (home / ".obsidian-wiki").exists()
    assert not (home / ".obsidian-wiki").is_symlink()
    assert not (portable / ".git").exists() and not (portable / ".git").is_symlink()
    for arguments in (("rev-parse", "--git-dir"), ("log", "-1"), ("remote",)):
        probe = subprocess.run(
            ["git", *arguments],
            cwd=portable,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert probe.returncode != 0, (arguments, probe.stdout, probe.stderr)

    subprocess.run(
        ["git", "init"],
        cwd=portable,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["git", "add", "--all"],
        cwd=portable,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=portable,
        env=env,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout.rstrip(b"\0").split(b"\0")
    forbidden_paths = (
        str(original_source_path).encode(),
        str(moved_source.resolve()).encode(),
        str(tool_dir).encode(),
    )
    for encoded_relative in tracked:
        if not encoded_relative:
            continue
        relative = os.fsdecode(encoded_relative)
        payload = (portable / relative).read_bytes()
        assert not [value for value in forbidden_paths if value in payload], relative
    assert not (home / ".obsidian-wiki").exists()
    assert not (home / ".obsidian-wiki").is_symlink()
    assert not any(
        (home / agent / "skills").exists() for agent in (".claude", ".codex", ".agents")
    )
    assert _safe_tree_snapshot(home) == home_before

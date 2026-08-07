from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


def test_bilingual_readmes_disclose_the_fork_and_only_source_install() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README_ZH.md").read_text(encoding="utf-8")
    assert not (ROOT / "README_TW.md").exists()
    for text in (english, chinese):
        assert "Ar9av/obsidian-wiki" in text
        assert "5ef66b6bec8b26bab6594ac37fb4d8371469fbab" in text
        assert "git clone https://github.com/evanzlh/obsidian-wiki.git" in text
        assert "uv tool install ." in text
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
    rebuild = adding_skill.index("uv tool install --force .")
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
        files.extend(path for path in base.rglob("*") if path.is_file())
    banned = (
        "pip install obsidian-wiki",
        "pipx install obsidian-wiki",
        "npx skills add Ar9av/obsidian-wiki",
        "bash setup.sh",
        "uv tool install git+",
    )
    offenders = {
        path.relative_to(ROOT).as_posix(): token
        for path in files
        for token in banned
        if token in path.read_text(encoding="utf-8", errors="ignore")
    }
    assert offenders == {}

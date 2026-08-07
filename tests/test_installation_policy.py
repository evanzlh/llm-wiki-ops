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

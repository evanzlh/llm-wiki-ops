import json
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import cli
from obsidian_wiki.skill_trees import discover_skill_collection


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "obsidian_wiki/_data"
DISCOVERY_DIRS = tuple(relative for relative, _label in cli.PROJECT_AGENT_DIRS)
BOOTSTRAP_TARGETS = (
    "CLAUDE.md",
    "GEMINI.md",
    ".hermes.md",
    ".agent/rules/obsidian-wiki.md",
    ".agent/workflows/obsidian-wiki.md",
    ".cursor/rules/obsidian-wiki.mdc",
    ".windsurf/rules/obsidian-wiki.md",
    ".kiro/steering/obsidian-wiki.md",
    ".github/copilot-instructions.md",
)
CATALOG = DATA / "legacy-skill-digests-v1.json"
CAPTURE_TOOL = ROOT / "tools/capture_legacy_skill_digests.py"


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
    )


def test_framework_root_has_no_wiki_skill_discovery_tree() -> None:
    assert not (ROOT / ".skills").exists()
    assert [relative for relative in DISCOVERY_DIRS if (ROOT / relative).exists()] == []


def test_package_data_is_the_only_runtime_asset_source() -> None:
    assert cli.skills_dir() == DATA / "skills"
    assert cli.bootstrap_dir() == DATA / "bootstrap"
    assert (DATA / "skills/llm-wiki/SKILL.md").is_file()
    assert (DATA / "bootstrap/AGENTS.md").is_file()


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


def test_missing_packaged_bootstrap_file_fails_instead_of_skipping(
    tmp_path: Path,
) -> None:
    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir()

    with pytest.raises(FileNotFoundError, match="bootstrap file.*Reinstall"):
        cli._resolve_bootstrap_src(bootstrap, "AGENTS.md")


def test_framework_bootstraps_are_ordinary_development_pointers() -> None:
    for relative in BOOTSTRAP_TARGETS:
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink(), relative
        text = path.read_text(encoding="utf-8")
        assert "AGENTS.md" in text, relative
        assert "Config Resolution Protocol" not in text, relative
        assert "Skill Routing" not in text, relative

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Obsidian Wiki — Framework Development" in agents
    assert "source of the `obsidian-wiki` framework" in agents
    assert "not an initialized\nwiki repository" in agents
    assert "Do not resolve a vault" in agents
    assert "obsidian_wiki/_data/skills" in agents
    assert "obsidian_wiki/_data/bootstrap" in agents
    runtime = (DATA / "bootstrap/AGENTS.md").read_text(encoding="utf-8")
    assert "Config Resolution Protocol" in runtime


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


def test_committed_legacy_catalog_covers_every_bundled_skill() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert [item["label"] for item in payload["collections"]] == [
        "portable-adapter-baseline-7596215"
    ]
    discovered = discover_skill_collection(
        DATA / "skills", ignore_source_artifacts=True
    )
    assert payload["collections"][0]["skills"] == {
        skill.name: skill.digest for skill in discovered.skills
    }

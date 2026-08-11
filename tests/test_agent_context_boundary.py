import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import cli
from obsidian_wiki.skill_trees import discover_skill_collection
from tools import capture_legacy_skill_digests as capture_tool


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
        timeout=30,
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


def test_framework_development_commands_provision_pytest_explicitly() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert (
        "uv run --with pytest python -m pytest tests/test_portable_setup.py -q"
        in agents
    )
    assert (
        "PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q "
        "-p no:cacheprovider"
    ) in agents


def test_automatic_stop_capture_surface_is_absent() -> None:
    hook_name = "wiki-stop-" + "capture.sh"
    packaged_hook_reference = hook_name[:-3]
    session_hook_reference = "session-end Stop " + "hook"
    assert not (ROOT / ".claude/settings.json").exists()
    assert not (ROOT / ".claude/hooks" / hook_name).exists()
    assert not (DATA / "hooks" / hook_name).exists()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    setup = (DATA / "skills/wiki-setup/SKILL.md").read_text(encoding="utf-8")
    capture = (DATA / "skills/wiki-capture/SKILL.md").read_text(encoding="utf-8")
    for text in (pyproject, setup, capture):
        assert packaged_hook_reference not in text
        assert session_hook_reference not in text


def test_explicit_quick_capture_remains_documented() -> None:
    capture = (DATA / "skills/wiki-capture/SKILL.md").read_text(encoding="utf-8")
    assert "/wiki-capture --quick" in capture
    assert "Quick mode" in capture


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


@pytest.mark.parametrize("target_exists", [False, True], ids=["dangling", "live"])
def test_legacy_digest_capture_rejects_output_symlink(
    tmp_path: Path, target_exists: bool
) -> None:
    source = tmp_path / "skills"
    expected = tmp_path / "expected.json"
    target = tmp_path / "target.json"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    assert _capture(source, "baseline", expected).returncode == 0
    if target_exists:
        target.write_bytes(expected.read_bytes())
    try:
        output.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))

    result = _capture(source, "baseline", output)

    assert result.returncode != 0
    assert "ordinary single-link regular file" in result.stderr
    assert output.is_symlink()
    if target_exists:
        assert target.read_bytes() == expected.read_bytes()
    else:
        assert not target.exists()


def test_legacy_digest_capture_rejects_output_directory(tmp_path: Path) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    output.mkdir()

    result = _capture(source, "baseline", output)

    assert result.returncode != 0
    assert "ordinary single-link regular file" in result.stderr
    assert output.is_dir()


def test_legacy_digest_capture_rejects_output_fifo(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes unavailable")
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    try:
        os.mkfifo(output)
    except OSError as exc:
        pytest.skip("named pipes unavailable: {}".format(exc))

    result = _capture(source, "baseline", output)

    assert result.returncode != 0
    assert "ordinary single-link regular file" in result.stderr
    assert output.exists()


def test_legacy_digest_capture_rejects_multiply_linked_output(tmp_path: Path) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    linked = tmp_path / "catalog-linked.json"
    _write_skill(source)
    assert _capture(source, "baseline", output).returncode == 0
    original = output.read_bytes()
    try:
        os.link(output, linked)
    except OSError as exc:
        pytest.skip("hard links unavailable: {}".format(exc))

    result = _capture(source, "baseline", output)

    assert result.returncode != 0
    assert "ordinary single-link regular file" in result.stderr
    assert output.read_bytes() == original
    assert linked.read_bytes() == original


def test_legacy_digest_capture_create_does_not_follow_racing_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    _write_skill(source)
    target.write_bytes(b"untouched")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_open = capture_tool.os.open
    raced = False

    def race_output(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal raced
        if not raced and Path(path) == output:
            output.symlink_to(target)
            raced = True
        return real_open(path, flags, mode)

    monkeypatch.setattr(capture_tool.os, "open", race_output)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_TOOL),
            "--source",
            str(source),
            "--label",
            "baseline",
            "--output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit):
        capture_tool.main()

    assert raced
    assert output.is_symlink()
    assert target.read_bytes() == b"untouched"


def test_legacy_digest_capture_removes_partial_new_output_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "skills"
    output = tmp_path / "catalog.json"
    _write_skill(source)
    real_write = capture_tool.os.write
    calls = 0

    def fail_after_partial_write(descriptor: int, content: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, content[:1])
        raise OSError("simulated output failure")

    monkeypatch.setattr(capture_tool.os, "write", fail_after_partial_write)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_TOOL),
            "--source",
            str(source),
            "--label",
            "baseline",
            "--output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit):
        capture_tool.main()

    assert calls == 2
    assert not output.exists()


def test_legacy_digest_capture_closes_but_does_not_unlink_on_initial_fstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    real_open = capture_tool.os.open
    real_fstat = capture_tool.os.fstat
    output_descriptor = None

    def remember_output(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal output_descriptor
        descriptor = real_open(path, flags, mode)
        if Path(path) == output:
            output_descriptor = descriptor
        return descriptor

    def fail_output_fstat(descriptor: int) -> os.stat_result:
        if descriptor == output_descriptor:
            raise OSError("simulated fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(capture_tool.os, "open", remember_output)
    monkeypatch.setattr(capture_tool.os, "fstat", fail_output_fstat)

    with pytest.raises(ValueError, match="fstat|inspect|output"):
        capture_tool._write_new_output(output, b"content")

    assert output_descriptor is not None
    with pytest.raises(OSError):
        real_fstat(output_descriptor)
    assert output.read_bytes() == b""


def test_legacy_digest_capture_does_not_remove_regular_replacement_before_lstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"attacker replacement")
    real_lstat = capture_tool.Path.lstat
    replaced = False

    def replace_before_lstat(path: Path) -> os.stat_result:
        nonlocal replaced
        if path == output and not replaced:
            replacement.replace(output)
            replaced = True
        return real_lstat(path)

    monkeypatch.setattr(capture_tool.Path, "lstat", replace_before_lstat)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._write_new_output(output, b"expected")

    assert replaced
    assert output.read_bytes() == b"attacker replacement"


def test_legacy_digest_capture_fstat_failure_does_not_remove_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    target.write_bytes(b"wrong")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_open = capture_tool.os.open
    real_fstat = capture_tool.os.fstat
    output_descriptor = None

    def remember_output(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal output_descriptor
        descriptor = real_open(path, flags, mode)
        if Path(path) == output:
            output_descriptor = descriptor
        return descriptor

    def replace_then_fail(descriptor: int) -> os.stat_result:
        if descriptor == output_descriptor:
            output.unlink()
            output.symlink_to(target)
            raise OSError("simulated fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(capture_tool.os, "open", remember_output)
    monkeypatch.setattr(capture_tool.os, "fstat", replace_then_fail)

    with pytest.raises(ValueError, match="fstat|inspect|output"):
        capture_tool._write_new_output(output, b"content")

    assert output.is_symlink()
    assert target.read_bytes() == b"wrong"


def test_legacy_digest_capture_rejects_read_path_replaced_by_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    output.write_bytes(b"expected")
    target.write_bytes(b"wrong")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_read = capture_tool.os.read
    replaced = False

    def replace_after_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        content = real_read(descriptor, size)
        if content and not replaced:
            output.unlink()
            output.symlink_to(target)
            replaced = True
        return content

    monkeypatch.setattr(capture_tool.os, "read", replace_after_read)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._read_existing_output(output)

    assert replaced
    assert output.is_symlink()
    assert target.read_bytes() == b"wrong"


def test_legacy_digest_capture_rejects_read_path_replaced_during_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    output.write_bytes(b"expected")
    target.write_bytes(b"wrong")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_close = capture_tool.os.close
    replaced = False

    def close_then_replace(descriptor: int) -> None:
        nonlocal replaced
        real_close(descriptor)
        output.unlink()
        output.symlink_to(target)
        replaced = True

    monkeypatch.setattr(capture_tool.os, "close", close_then_replace)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._read_existing_output(output)

    assert replaced
    assert output.is_symlink()
    assert target.read_bytes() == b"wrong"


def test_legacy_digest_capture_rejects_write_path_replaced_by_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    target = tmp_path / "target.json"
    target.write_bytes(b"wrong")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    probe.unlink()
    real_write = capture_tool.os.write
    replaced = False

    def replace_after_write(descriptor: int, content: bytes) -> int:
        nonlocal replaced
        written = real_write(descriptor, content)
        if not replaced:
            output.unlink()
            output.symlink_to(target)
            replaced = True
        return written

    monkeypatch.setattr(capture_tool.os, "write", replace_after_write)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._write_new_output(output, b"expected")

    assert replaced
    assert output.is_symlink()
    assert target.read_bytes() == b"wrong"


def test_legacy_digest_capture_rejects_read_hardlink_added_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    linked = tmp_path / "linked.json"
    output.write_bytes(b"expected")
    real_read = capture_tool.os.read
    linked_after_open = False

    def link_after_read(descriptor: int, size: int) -> bytes:
        nonlocal linked_after_open
        content = real_read(descriptor, size)
        if content and not linked_after_open:
            try:
                os.link(output, linked)
            except OSError as exc:
                pytest.skip("hard links unavailable: {}".format(exc))
            linked_after_open = True
        return content

    monkeypatch.setattr(capture_tool.os, "read", link_after_read)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._read_existing_output(output)

    assert linked_after_open
    assert output.read_bytes() == b"expected"
    assert linked.read_bytes() == b"expected"


def test_legacy_digest_capture_rejects_write_hardlink_added_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    linked = tmp_path / "linked.json"
    real_write = capture_tool.os.write
    linked_after_open = False

    def link_after_write(descriptor: int, content: bytes) -> int:
        nonlocal linked_after_open
        written = real_write(descriptor, content)
        if not linked_after_open:
            try:
                os.link(output, linked)
            except OSError as exc:
                pytest.skip("hard links unavailable: {}".format(exc))
            linked_after_open = True
        return written

    monkeypatch.setattr(capture_tool.os, "write", link_after_write)

    with pytest.raises(ValueError, match="changed|ordinary"):
        capture_tool._write_new_output(output, b"expected")

    assert linked_after_open
    assert not output.exists()
    assert linked.read_bytes() == b"expected"


def test_legacy_digest_capture_converts_read_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    output.write_bytes(b"expected")
    real_close = capture_tool.os.close

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("simulated close failure")

    monkeypatch.setattr(capture_tool.os, "close", close_then_fail)

    with pytest.raises(ValueError, match="close failure"):
        capture_tool._read_existing_output(output)


def test_legacy_digest_capture_preserves_read_error_over_close_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "catalog.json"
    output.write_bytes(b"expected")
    real_close = capture_tool.os.close

    def fail_read(descriptor: int, size: int) -> bytes:
        raise OSError("simulated primary read failure")

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("simulated secondary close failure")

    monkeypatch.setattr(capture_tool.os, "read", fail_read)
    monkeypatch.setattr(capture_tool.os, "close", close_then_fail)

    with pytest.raises(ValueError, match="primary read failure") as error:
        capture_tool._read_existing_output(output)
    assert "secondary close failure" not in str(error.value)


def test_committed_legacy_catalog_covers_every_bundled_skill_name() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert [item["label"] for item in payload["collections"]] == [
        "portable-adapter-baseline-7596215"
    ]
    discovered = discover_skill_collection(
        DATA / "skills", ignore_source_artifacts=True
    )
    assert set(payload["collections"][0]["skills"]) == {
        skill.name for skill in discovered.skills
    }

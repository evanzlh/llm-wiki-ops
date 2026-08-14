"""Tests for the doctor CLI command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

import obsidian_wiki.cli as cli
from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki.cli import list_skills, skills_dir
from obsidian_wiki.config import ConfigError, load_portable_config
from obsidian_wiki.portable import PROJECT_AGENT_DIRS, setup_portable_repo
from obsidian_wiki.portable_manifest import ShardedManifest


def _run(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def _write_config(home: Path, vault: Path, *, version: str | None = None) -> None:
    config_dir = home / ".llmwikiops"
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = [f'OBSIDIAN_VAULT_PATH="{vault}"']
    if version is not None:
        lines.append(f'OBSIDIAN_WIKI_VERSION="{version}"')
    (config_dir / "config").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_vault(vault: Path, *, manifest: str = '{"sources": {}}') -> None:
    vault.mkdir(parents=True, exist_ok=True)
    for name in ("index.md", "log.md", "hot.md"):
        (vault / name).write_text(f"# {name}\n", encoding="utf-8")
    (vault / ".manifest.json").write_text(manifest, encoding="utf-8")


def _install_all_skills(home: Path) -> None:
    target = home / ".claude" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    for name in list_skills():
        skill_dir = target / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")


def _make_portable_repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "knowledge"
    setup_portable_repo(root, version=__version__, source_skills=skills_dir())
    hot = root / "wiki/hot.md"
    if hot.exists():
        hot.unlink()
    nested = root / "work/nested"
    nested.mkdir(parents=True)
    return root, nested


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _portable_snapshot(root: Path) -> tuple[tuple[str, str, bytes | str], ...]:
    entries: list[tuple[str, str, bytes | str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        name = relative.as_posix()
        if path.is_symlink():
            entries.append((name, "symlink", os.readlink(path)))
        elif path.is_dir():
            entries.append((name, "directory", ""))
        elif path.is_file():
            entries.append((name, "file", path.read_bytes()))
        else:
            entries.append((name, "special", ""))
    return tuple(entries)


def _replace_portable_path(
    root: Path, *, old: str, new: str
) -> None:
    config = root / ".llmwikiops/config.toml"
    text = config.read_text(encoding="utf-8")
    assert old in text
    config.write_text(text.replace(old, new), encoding="utf-8")


def _portable_check(proc: subprocess.CompletedProcess[str], name: str) -> dict[str, str]:
    report = json.loads(proc.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    if name in checks:
        return checks[name]
    # Unsafe configured topology is now rejected by the shared resolver before
    # doctor reaches its redundant portable-paths inspection.
    config = checks.get("portable-config")
    if config is not None and config["status"] == "fail":
        return config
    raise KeyError(name)


@pytest.mark.parametrize("legacy_option", ["--vault", "--project"])
def test_doctor_rejects_non_repository_selectors(
    legacy_option: str, tmp_path: Path
) -> None:
    proc = _run(tmp_path / "home", "doctor", legacy_option, "x")

    assert proc.returncode == 2
    assert "unrecognized arguments" in proc.stderr


def test_doctor_json_structures_unavailable_current_directory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def cwd_failure() -> Path:
        raise FileNotFoundError("cwd deleted")

    monkeypatch.setattr(Path, "cwd", cwd_failure)

    returncode = cli.cmd_doctor(Namespace(json=True, pretty=False, strict=False))

    captured = capsys.readouterr()
    assert returncode == 1
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["status"] == "fail"
    assert report["checks"] == [
        {
            "name": "portable-config",
            "status": "fail",
            "detail": "current working directory is unavailable: cwd deleted",
            "hint": "run: llmwikiops setup [DIR]",
        }
    ]


def test_doctor_json_structures_portable_candidate_metadata_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def resolution_failure(**_kwargs: object) -> None:
        raise ConfigError("portable config is invalid")

    def metadata_failure(_path: Path) -> bool:
        raise PermissionError("config metadata denied")

    monkeypatch.setattr(cli, "resolve_config", resolution_failure)
    monkeypatch.setattr(Path, "exists", metadata_failure)

    returncode = cli.cmd_doctor(Namespace(json=True, pretty=False, strict=False))

    captured = capsys.readouterr()
    assert returncode == 1
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["status"] == "fail"
    assert report["checks"][0]["detail"] == (
        "portable configuration inspection failed: config metadata denied"
    )


def test_doctor_portable_mode_ignores_global_config_and_agent_installs(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    global_vault = tmp_path / "global-vault"
    _make_vault(global_vault)
    _write_config(home, global_vault)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "pass"
    checks = {check["name"]: check for check in report["checks"]}
    for name in (
        "portable-config",
        "implementation",
        "portable-paths",
        "project-skills",
    ):
        assert checks[name]["status"] == "pass"
    assert "global-config" not in checks
    assert "agent-installs" not in checks
    assert str(root / "wiki") in json.dumps(report)
    assert str(global_vault) not in json.dumps(report)
    assert not (root / "wiki/hot.md").exists()


@pytest.mark.parametrize(
    ("strict", "expected_returncode"), [(False, 0), (True, 1)]
)
def test_doctor_reports_matching_managed_canonical_edits_as_warning(
    tmp_path: Path, strict: bool, expected_returncode: int
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    paths = [root / ".skills/wiki-ingest/SKILL.md"] + [
        root / relative / "wiki-ingest/SKILL.md"
        for relative, _label in PROJECT_AGENT_DIRS
    ]
    for path in paths:
        path.write_text(
            path.read_text(encoding="utf-8") + "\nOwner extension.\n",
            encoding="utf-8",
        )
    args = ["doctor", "--json"]
    if strict:
        args.append("--strict")

    proc = _run(home, *args, cwd=nested)

    assert proc.returncode == expected_returncode
    report = json.loads(proc.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["project-skills"]["status"] == "warn"
    assert "managed-canonical-modified" in checks["project-skills"]["detail"]


def test_doctor_fails_on_portable_skill_mirror_drift(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    mirror = root / ".claude/skills/wiki-ingest/SKILL.md"
    mirror.write_text(
        mirror.read_text(encoding="utf-8") + "\nMirror drift.\n",
        encoding="utf-8",
    )

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["project-skills"]["status"] == "fail"
    assert "skill-mirror-changed" in checks["project-skills"]["detail"]


def test_doctor_portable_mode_does_not_require_global_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _root, nested = _make_portable_repo(tmp_path)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "pass"
    assert all(
        check["name"] not in {"global-config", "agent-installs"}
        for check in report["checks"]
    )


def test_doctor_portable_mode_reports_exact_v2_shard_count(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    config = load_portable_config(
        root / ".llmwikiops/config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    first = root / "sources/first.md"
    second = root / "sources/second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    store = ShardedManifest(config)
    store.upsert(first, compiled_at="2026-08-08T00:00:00Z")
    store.upsert(second, compiled_at="2026-08-08T00:00:01Z")

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    detail = _portable_check(proc, "portable-paths")["detail"]
    assert "manifest shards=2" in detail


@pytest.mark.parametrize("target_name", ["marker", "shard"])
def test_doctor_rejects_hardlinked_manifest_files(
    tmp_path: Path, target_name: str
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    config = load_portable_config(
        root / ".llmwikiops/config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    source = root / "sources/a.md"
    source.write_text("source", encoding="utf-8")
    store = ShardedManifest(config)
    entry = store.upsert(source, compiled_at="2026-08-08T00:00:00Z")
    target = (
        root / "wiki/.manifest.json"
        if target_name == "marker"
        else store.entry_path(entry.source_id)
    )
    external = tmp_path / f"external-{target.name}"
    external.write_bytes(target.read_bytes())
    target.unlink()
    os.link(external, target)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    check = _portable_check(proc, "portable-paths")
    assert check["status"] == "fail"
    assert "hard link" in check["detail"].lower() or "multiple links" in check[
        "detail"
    ].lower()
    assert str(root) not in check["detail"]


def test_doctor_fresh_portable_clone_allows_lazy_paths_without_mutation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    origin, _nested = _make_portable_repo(tmp_path)
    _git(origin, "init")
    _git(origin, "config", "user.email", "doctor@example.invalid")
    _git(origin, "config", "user.name", "Doctor Test")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", "portable fixture")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), str(clone))
    lazy_paths = (clone / ".llmwikiops/local", clone / "sources")
    assert all(not path.exists() and not path.is_symlink() for path in lazy_paths)
    before = _portable_snapshot(clone)

    proc = _run(home, "doctor", "--json", cwd=clone)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["status"] == "pass"
    assert _portable_check(proc, "portable-paths")["status"] == "pass"
    assert _portable_snapshot(clone) == before
    assert all(not path.exists() and not path.is_symlink() for path in lazy_paths)


def test_doctor_allows_multiple_missing_nested_sources_and_local_state(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    shutil.rmtree(root / ".llmwikiops/local")
    (root / "sources").rmdir()
    _replace_portable_path(
        root,
        old='sources = ["sources"]',
        new='sources = ["sources/inbox", "imports/deep/nested"]',
    )
    lazy_paths = (
        root / "sources/inbox",
        root / "imports/deep/nested",
        root / ".llmwikiops/local",
    )
    before = _portable_snapshot(root)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _portable_check(proc, "portable-paths")["status"] == "pass"
    assert _portable_snapshot(root) == before
    assert all(not path.exists() and not path.is_symlink() for path in lazy_paths)


@pytest.mark.parametrize(
    "entry_kind",
    (
        "file",
        "symlink",
        pytest.param("fifo", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO")),
    ),
)
def test_doctor_rejects_unsafe_canonical_local_state_without_mutating_owner_entry(
    entry_kind: str, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    local_state = root / ".llmwikiops" / "local"
    shutil.rmtree(local_state)

    if entry_kind == "file":
        owner_bytes = b"owner local state evidence\n"
        local_state.write_bytes(owner_bytes)
        before = ("file", local_state.read_bytes(), local_state.stat().st_ino)
    elif entry_kind == "symlink":
        target = root / "owner-local-target"
        assert not target.exists()
        local_state.symlink_to(target, target_is_directory=True)
        before = ("symlink", os.readlink(local_state), local_state.lstat().st_ino)
    else:
        os.mkfifo(local_state)
        before = ("fifo", local_state.lstat().st_mode, local_state.lstat().st_ino)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    check = _portable_check(proc, "portable-paths")
    assert check["status"] == "fail"
    detail = check["detail"].lower()
    assert "local" in detail
    if entry_kind == "symlink":
        assert "symlink" in detail
        after = ("symlink", os.readlink(local_state), local_state.lstat().st_ino)
    elif entry_kind == "file":
        assert "directory" in detail
        after = ("file", local_state.read_bytes(), local_state.stat().st_ino)
    else:
        assert "directory" in detail
        after = ("fifo", local_state.lstat().st_mode, local_state.lstat().st_ino)
    assert after == before


@pytest.mark.parametrize(
    ("old", "new", "relative"),
    [
        ('sources = ["sources"]', 'sources = ["runtime/inbox"]', "runtime/inbox"),
    ],
)
def test_doctor_rejects_dangling_symlink_at_lazy_path(
    old: str,
    new: str,
    relative: str,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    _replace_portable_path(root, old=old, new=new)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(root / "missing-target", target_is_directory=True)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    check = _portable_check(proc, "portable-paths")
    assert check["status"] == "fail"
    assert "symlink" in check["detail"].lower()


@pytest.mark.parametrize(
    ("old", "new", "leaf_relative", "ancestor_relative"),
    [
        (
            'sources = ["sources"]',
            'sources = ["runtime/deep/inbox"]',
            "runtime/deep/inbox",
            "runtime",
        ),
    ],
)
@pytest.mark.parametrize("entry_kind", ["file", "symlink", "fifo"])
@pytest.mark.parametrize("position", ["leaf", "ancestor"])
def test_doctor_rejects_unsafe_existing_component_of_lazy_path(
    old: str,
    new: str,
    leaf_relative: str,
    ancestor_relative: str,
    entry_kind: str,
    position: str,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    _replace_portable_path(root, old=old, new=new)
    unsafe = root / (leaf_relative if position == "leaf" else ancestor_relative)
    unsafe.parent.mkdir(parents=True, exist_ok=True)
    if entry_kind == "file":
        unsafe.write_text("not a directory", encoding="utf-8")
    elif entry_kind == "symlink":
        target = root / "safe-target"
        target.mkdir()
        unsafe.symlink_to(target, target_is_directory=True)
    else:
        os.mkfifo(unsafe)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    check = _portable_check(proc, "portable-paths")
    assert check["status"] == "fail"
    detail = check["detail"].lower()
    if entry_kind == "symlink":
        assert "symlink" in detail
    else:
        assert "directory" in detail


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('sources = ["sources"]', 'sources = ["../outside"]'),
    ],
)
def test_doctor_rejects_escaping_lazy_path(
    old: str,
    new: str,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    _replace_portable_path(root, old=old, new=new)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["status"] == "fail"
    assert "repository-relative" in json.dumps(report)


@pytest.mark.parametrize("relative", ["wiki", ".skills"])
def test_doctor_still_requires_portable_vault_and_skills(
    relative: str,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    shutil.rmtree(root / relative)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    assert _portable_check(proc, "portable-paths")["status"] == "fail"


@pytest.mark.parametrize(
    ("old", "new", "alias", "target"),
    [
        ('vault = "wiki"', 'vault = "vault-alias"', "vault-alias", "wiki"),
        (
            'skills = ".skills"',
            'skills = "skills-alias"',
            "skills-alias",
            ".skills",
        ),
    ],
)
def test_doctor_rejects_lexical_symlink_for_required_portable_path(
    old: str,
    new: str,
    alias: str,
    target: str,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    _replace_portable_path(root, old=old, new=new)
    (root / alias).symlink_to(root / target, target_is_directory=True)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    check = _portable_check(proc, "portable-paths")
    assert check["status"] == "fail"
    assert "symlink" in check["detail"].lower()


def test_doctor_wrong_portable_implementation_fails_without_global_fallback(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    config = root / ".llmwikiops/config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            IMPLEMENTATION_ID, "Ar9av/obsidian-wiki"
        ),
        encoding="utf-8",
    )
    global_vault = tmp_path / "global-vault"
    _make_vault(global_vault)
    _write_config(home, global_vault)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["implementation"]["status"] == "fail"
    assert str(global_vault) not in json.dumps(report)
    assert "global-config" not in checks


def test_doctor_dangling_portable_config_symlink_fails_without_global_fallback(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    config = root / ".llmwikiops/config.toml"
    config.unlink()
    config.symlink_to(tmp_path / "missing-config.toml")
    global_vault = tmp_path / "global-vault"
    _make_vault(global_vault)
    _write_config(home, global_vault)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["portable-config"]["status"] == "fail"
    assert "symlink" in json.dumps(report).lower()
    assert str(global_vault) not in json.dumps(report)


@pytest.mark.parametrize("entry_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize(
    "relative",
    [
        ".llmwikiops/config.toml",
        "CLAUDE.md",
        "wiki/index.md",
        ".skills/wiki-ingest/SKILL.md",
        ".claude/skills/wiki-ingest/SKILL.md",
    ],
)
def test_doctor_portable_mode_rejects_unsafe_managed_paths_without_fallback(
    entry_kind: str,
    relative: str,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    target = root / relative
    content = target.read_bytes()
    target.unlink()
    external = tmp_path / "external-managed-file"
    external.write_bytes(content)
    if entry_kind == "symlink":
        target.symlink_to(external)
    else:
        os.link(external, target)
    global_vault = tmp_path / "global-vault"
    _make_vault(global_vault)
    _write_config(home, global_vault)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["status"] == "fail"
    assert any(check["status"] == "fail" for check in report["checks"])
    details = json.dumps(report).lower()
    assert "symlink" in details if entry_kind == "symlink" else "hard link" in details
    assert str(global_vault) not in json.dumps(report)


def test_doctor_rejects_agent_mirror_with_symlinked_ancestor(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, nested = _make_portable_repo(tmp_path)
    external = tmp_path / "external-claude"
    shutil.copytree(root / ".claude", external)
    shutil.rmtree(root / ".claude")
    (root / ".claude").symlink_to(external, target_is_directory=True)

    proc = _run(home, "doctor", "--json", cwd=nested)

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["project-skills"]["status"] == "fail"
    assert "skill-mirror-unsafe" in checks["project-skills"]["detail"]
    assert str(external) not in json.dumps(report)

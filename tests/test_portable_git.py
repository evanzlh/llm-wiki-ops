from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki.cli import run_doctor
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.git_support import discover_git_root, git_branch_id, tracked_paths
from obsidian_wiki.portable import setup_portable_repo
from obsidian_wiki.portable_check import check_portable_repo


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def test_vault_discovers_enclosing_repo_root(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    vault = root / "wiki"
    vault.mkdir(parents=True)
    git(root, "init", "-q")

    assert discover_git_root(vault) == root.resolve()
    assert not (vault / ".git").exists()


def test_non_repo_returns_none(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()

    assert discover_git_root(vault) is None


def test_branch_id_uses_branch_or_detached_head(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "seed").write_text("x", encoding="utf-8")
    git(root, "add", "seed")
    git(root, "commit", "-q", "-m", "seed")
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")

    assert git_branch_id(root) == branch

    head = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "--detach", "HEAD")

    assert git_branch_id(root) == head


def test_branch_id_without_git_returns_no_git(tmp_path: Path) -> None:
    assert git_branch_id(tmp_path) == "no-git"


def test_tracked_paths_are_repo_relative_and_sorted(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    git(root, "init", "-q")
    (root / "z.md").write_text("z", encoding="utf-8")
    nested = root / "a"
    nested.mkdir()
    (nested / "b.md").write_text("b", encoding="utf-8")
    git(root, "add", "z.md", "a/b.md")

    assert tracked_paths(root) == ("a/b.md", "z.md")


def test_tracked_paths_without_git_are_empty(tmp_path: Path) -> None:
    assert tracked_paths(tmp_path) == ()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits byte-oriented path names")
def test_discovery_preserves_spaces_and_non_utf8_path_bytes(tmp_path: Path) -> None:
    root = tmp_path / os.fsdecode(b" knowledge-\xff ")
    vault = root / "wiki"
    vault.mkdir(parents=True)
    git(root, "init", "-q")

    assert discover_git_root(vault) == root.resolve()


def test_read_only_git_facts_ignore_trace_file_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    git(root, "init", "-q")
    trace = tmp_path / "git-trace.log"
    trace2 = tmp_path / "git-trace2.json"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace2))

    assert discover_git_root(root) == root.resolve()
    assert git_branch_id(root)
    assert tracked_paths(root) == ()
    assert not trace.exists()
    assert not trace2.exists()


def test_portable_check_and_doctor_reject_different_enclosing_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_skills = tmp_path / "source-skills"
    skill = source_skills / "wiki-query"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Query\n", encoding="utf-8")
    parent = tmp_path / "outer"
    root = parent / "knowledge"
    setup_portable_repo(root, version=__version__, source_skills=source_skills)
    config = load_portable_config(
        root / ".obsidian-wiki/config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    git(parent, "init", "-q")

    check_report = check_portable_repo(config)
    monkeypatch.chdir(config.vault)
    doctor_report = run_doctor()

    assert "git-root-mismatch" in {
        issue["code"] for issue in check_report["issues"]
    }
    portable_git = next(
        check for check in doctor_report["checks"] if check["name"] == "portable-git"
    )
    assert portable_git["status"] == "fail"
    assert "different Git worktree" in portable_git["detail"]

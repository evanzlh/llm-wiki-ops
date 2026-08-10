"""CLI regression coverage for GitHub vault sync (obsidian_wiki/sync.py).

The setup, sync-setup, and sync commands share this implementation so their git
plumbing stays consistent.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki.cli import main
from obsidian_wiki import sync as sync_module
from obsidian_wiki.sync import configure_sync, get_remote, run_sync


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_dir), *args], check=True,
                           capture_output=True, text=True)


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def remote_repo(tmp_path):
    """A bare repo to act as a real push target."""
    r = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(r)], check=True)
    return r


class TestGetRemote:
    def test_not_a_git_repo(self, vault):
        assert get_remote(vault) is None

    def test_no_origin_set(self, vault):
        _git(vault, "init", "-q")
        assert get_remote(vault) is None

    def test_returns_configured_remote(self, vault):
        _git(vault, "init", "-q")
        _git(vault, "remote", "add", "origin", "https://example.com/x.git")
        assert get_remote(vault) == "https://example.com/x.git"


class TestConfigureSync:
    def test_missing_vault_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            configure_sync(tmp_path / "nope", "https://example.com/x.git")

    def test_blank_remote_raises(self, vault):
        with pytest.raises(ValueError):
            configure_sync(vault, "   ")

    def test_inits_git_repo(self, vault):
        configure_sync(vault, "https://example.com/x.git")
        assert (vault / ".git").is_dir()

    def test_does_not_reinit_existing_repo(self, vault):
        _git(vault, "init", "-q")
        (vault / "existing.md").write_text("keep me")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "seed")
        configure_sync(vault, "https://example.com/x.git")
        log = _git(vault, "log", "--oneline").stdout
        assert "seed" in log

    def test_writes_gitignore(self, vault):
        configure_sync(vault, "https://example.com/x.git")
        content = (vault / ".gitignore").read_text()
        assert ".obsidian/workspace.json" in content
        assert ".trash/" in content

    def test_does_not_overwrite_existing_gitignore(self, vault):
        vault.mkdir(exist_ok=True)
        (vault / ".gitignore").write_text("custom-rule/\n")
        configure_sync(vault, "https://example.com/x.git")
        assert (vault / ".gitignore").read_text() == "custom-rule/\n"

    def test_sets_remote(self, vault):
        configure_sync(vault, "https://example.com/x.git")
        assert get_remote(vault) == "https://example.com/x.git"

    def test_updates_existing_remote(self, vault):
        configure_sync(vault, "https://example.com/old.git")
        configure_sync(vault, "https://example.com/new.git")
        assert get_remote(vault) == "https://example.com/new.git"

    def test_returns_confirmation_messages(self, vault):
        messages = configure_sync(vault, "https://example.com/x.git")
        joined = " ".join(messages)
        assert "Initialized git repo" in joined
        assert "https://example.com/x.git" in joined


class TestRunSync:
    def test_vault_missing(self, tmp_path):
        code, message = run_sync(tmp_path / "nope")
        assert code == 1
        assert "not found" in message

    def test_not_a_git_repo(self, vault):
        code, message = run_sync(vault)
        assert code == 1
        assert "sync-setup" in message

    def test_nothing_to_commit(self, vault):
        _git(vault, "init", "-q")
        code, message = run_sync(vault)
        assert code == 0
        assert "nothing to commit" in message

    def test_commits_and_pushes(self, vault, remote_repo):
        configure_sync(vault, str(remote_repo))
        _git(vault, "config", "user.email", "test@example.com")
        _git(vault, "config", "user.name", "Test")
        (vault / "note.md").write_text("hello")
        code, message = run_sync(vault)
        assert code == 0
        assert "pushed to" in message
        log = _git(vault, "log", "--oneline").stdout
        assert "sync " in log

    def test_second_run_with_no_changes_is_clean(self, vault, remote_repo):
        configure_sync(vault, str(remote_repo))
        _git(vault, "config", "user.email", "test@example.com")
        _git(vault, "config", "user.name", "Test")
        (vault / "note.md").write_text("hello")
        run_sync(vault)
        code, message = run_sync(vault)
        assert code == 0
        assert "nothing to commit" in message


def _portable_repository(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / ".skills").mkdir()
    vault = root / "wiki"
    vault.mkdir()
    (root / ".obsidian-wiki/config.toml").write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = "=={__version__}"

[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "portable baseline")
    return root


@pytest.mark.parametrize(
    "arguments",
    [
        ("sync",),
        ("sync-setup", "https://example.invalid/knowledge.git"),
        ("sync", "--vault", "wiki"),
        (
            "sync-setup",
            "https://example.invalid/knowledge.git",
            "--vault",
            "../personal-vault",
        ),
    ],
)
def test_portable_sync_commands_refuse_automatic_git_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
) -> None:
    root = _portable_repository(tmp_path)
    note = root / "wiki/note.md"
    note.write_text("pending\n", encoding="utf-8")
    before_head = _git(root, "rev-parse", "HEAD").stdout.strip()
    before_status = _git(root, "status", "--porcelain=v1").stdout
    before_remotes = _git(root, "remote", "-v").stdout
    monkeypatch.setattr(
        sync_module,
        "run_sync",
        lambda *_args: (_ for _ in ()).throw(AssertionError("run_sync invoked")),
    )
    monkeypatch.setattr(
        sync_module,
        "configure_sync",
        lambda *_args: (_ for _ in ()).throw(AssertionError("configure_sync invoked")),
    )
    monkeypatch.chdir(root)

    result = main(list(arguments))

    captured = capsys.readouterr()
    assert result == 1
    assert "portable repositories use branch and pull-request workflows" in (
        captured.out + captured.err
    )
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == before_head
    assert _git(root, "status", "--porcelain=v1").stdout == before_status
    assert _git(root, "diff", "--cached", "--quiet").returncode == 0
    assert _git(root, "remote", "-v").stdout == before_remotes
    assert not (root / "wiki/.git").exists()


def test_portable_sync_refuses_explicit_vault_with_dangling_config_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _portable_repository(tmp_path)
    config = root / ".obsidian-wiki/config.toml"
    config.unlink()
    config.symlink_to(root / "missing-config.toml")
    note = root / "wiki/note.md"
    note.write_text("pending\n", encoding="utf-8")
    before_head = _git(root, "rev-parse", "HEAD").stdout.strip()
    before_status = _git(root, "status", "--porcelain=v1").stdout
    before_remotes = _git(root, "remote", "-v").stdout
    monkeypatch.setattr(
        sync_module,
        "run_sync",
        lambda *_args: (_ for _ in ()).throw(AssertionError("run_sync invoked")),
    )
    monkeypatch.setattr(
        sync_module,
        "configure_sync",
        lambda *_args: (_ for _ in ()).throw(AssertionError("configure_sync invoked")),
    )
    monkeypatch.chdir(root)

    result = main(["sync", "--vault", "../personal-vault"])

    captured = capsys.readouterr()
    assert result == 1
    assert "portable repositories use branch and pull-request workflows" in (
        captured.out + captured.err
    )
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == before_head
    assert _git(root, "status", "--porcelain=v1").stdout == before_status
    assert _git(root, "remote", "-v").stdout == before_remotes
    assert not (root / "wiki/.git").exists()

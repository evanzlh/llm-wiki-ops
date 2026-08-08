from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki.config import PortableConfig, load_portable_config
from obsidian_wiki.operations import write_operation
from obsidian_wiki.portable import setup_portable_repo
from obsidian_wiki.portable_check import check_portable_repo
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.transaction import TransactionManager


def _git_environment(root: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    test_home = root.resolve().parent / ".git-e2e-home"
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(test_home),
            "XDG_CONFIG_HOME": str(test_home / "xdg"),
        }
    )
    return environment


def _git_command(root: Path, *args: str) -> list[str]:
    return [
        "git",
        "-c",
        "core.autocrlf=true",
        "-c",
        "core.safecrlf=false",
        "-C",
        str(root),
        *args,
    ]


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _git_command(root, *args),
        text=True,
        capture_output=True,
        check=check,
        env=_git_environment(root),
    )


def _git_bytes(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        _git_command(root, *args),
        capture_output=True,
        check=True,
        env=_git_environment(root),
    )


def _clone(source: Path, target: Path) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=true",
            "-c",
            "core.safecrlf=false",
            "clone",
            "-q",
            str(source),
            str(target),
        ],
        check=True,
        capture_output=True,
        env=_git_environment(source),
    )


def _config(root: Path) -> PortableConfig:
    return load_portable_config(
        root / ".obsidian-wiki/config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )


def _portable_seed(tmp_path: Path) -> Path:
    source_skills = tmp_path / "framework-skills"
    skill = source_skills / "wiki-query"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Query\n", encoding="utf-8")
    root = tmp_path / "seed"
    setup_portable_repo(root, version=__version__, source_skills=source_skills)
    return root


def _page(*, title: str, source_id: str, created: str) -> str:
    return f"""---
title: {title}
category: concepts
tags:
  - example
sources:
  - {source_id}
created: {created}
updated: {created}
summary: Knowledge compiled from {source_id}.
---
# {title}
"""


def _operation_writer(config: PortableConfig, suffix: str):
    def writer(change):
        return write_operation(
            config.vault,
            change,
            suffix=suffix,
            cleanup_root=config.local_state,
        )

    return writer


def _ingest(
    root: Path,
    *,
    source_id: str,
    page: str,
    title: str,
    transaction_id: str,
    started_at: str,
    completed_at: str,
    operation_suffix: str,
    source_bytes: bytes,
) -> tuple[PortableConfig, Path]:
    config = _config(root)
    source = root / source_id
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(source_bytes)
    manager = TransactionManager(
        config,
        operation_writer=_operation_writer(config, operation_suffix),
    )
    record = manager.begin(
        [source], transaction_id=transaction_id, started_at=started_at
    )
    candidate = record.candidate_vault / page
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        _page(title=title, source_id=source_id, created=completed_at[:10]),
        encoding="utf-8",
    )
    manager.commit(transaction_id, completed_at=completed_at)
    return config, ShardedManifest(config).entry_path(source_id)


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    opening, frontmatter, _body = text.split("---", 2)
    assert opening == ""
    return frontmatter


def test_clone_location_does_not_change_manifest_or_page_provenance(
    tmp_path: Path,
) -> None:
    seed = _portable_seed(tmp_path)
    clone_a = tmp_path / "clone-a"
    clone_b = tmp_path / "different-parent" / "clone-b"
    clone_b.parent.mkdir()
    shutil.copytree(seed, clone_a)
    shutil.copytree(seed, clone_b)
    inputs = {
        "source_id": "sources/design/a.md",
        "page": "concepts/design-a.md",
        "title": "Design A",
        "transaction_id": "portable-fixed",
        "started_at": "2026-08-08T00:00:00Z",
        "completed_at": "2026-08-08T00:05:00Z",
        "operation_suffix": "c33e",
        "source_bytes": b"same design source\n",
    }

    config_a, shard_a = _ingest(clone_a, **inputs)
    config_b, shard_b = _ingest(clone_b, **inputs)

    manifest_payload_a = json.loads(shard_a.read_text(encoding="utf-8"))
    manifest_payload_b = json.loads(shard_b.read_text(encoding="utf-8"))
    page_frontmatter_a = _frontmatter(config_a.vault / inputs["page"])
    page_frontmatter_b = _frontmatter(config_b.vault / inputs["page"])
    manifest_relative_path_a = shard_a.relative_to(clone_a).as_posix()
    manifest_relative_path_b = shard_b.relative_to(clone_b).as_posix()

    assert manifest_payload_a == manifest_payload_b
    assert page_frontmatter_a == page_frontmatter_b
    assert "/clone-a/" not in json.dumps(manifest_payload_a)
    assert "/clone-b/" not in json.dumps(manifest_payload_b)
    assert str(clone_a.resolve()) not in json.dumps(manifest_payload_a)
    assert str(clone_b.resolve()) not in json.dumps(manifest_payload_b)
    assert manifest_relative_path_a == manifest_relative_path_b


def test_unrelated_source_transactions_merge_without_conflicts(tmp_path: Path) -> None:
    seed = _portable_seed(tmp_path)
    _git(seed, "init", "-q")
    _git(seed, "config", "user.email", "seed@example.invalid")
    _git(seed, "config", "user.name", "Seed")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "portable seed")
    alice = tmp_path / "alice"
    bob = tmp_path / "bob"
    _clone(seed, alice)
    _clone(seed, bob)
    for root, name in ((alice, "Alice"), (bob, "Bob")):
        _git(root, "config", "user.email", f"{name.lower()}@example.invalid")
        _git(root, "config", "user.name", name)

    _ingest(
        alice,
        source_id="sources/alice.md",
        page="concepts/alice.md",
        title="Alice",
        transaction_id="alice-transaction",
        started_at="2026-08-08T01:00:00Z",
        completed_at="2026-08-08T01:05:00Z",
        operation_suffix="a11c",
        source_bytes=b"alice source\n",
    )
    _git(alice, "add", "-A")
    _git(alice, "commit", "-qm", "ingest alice")

    _ingest(
        bob,
        source_id="sources/bob.md",
        page="concepts/bob.md",
        title="Bob",
        transaction_id="bob-transaction",
        started_at="2026-08-08T02:00:00Z",
        completed_at="2026-08-08T02:05:00Z",
        operation_suffix="b22d",
        source_bytes=b"bob source\n",
    )
    _git(bob, "add", "-A")
    _git(bob, "commit", "-qm", "ingest bob")

    bob_branch = _git(bob, "branch", "--show-current").stdout.strip()
    _git(alice, "remote", "add", "bob", str(bob))
    _git(alice, "fetch", "-q", "bob", bob_branch)
    merge = _git(alice, "merge", "--no-edit", f"bob/{bob_branch}", check=False)
    unmerged = _git(alice, "diff", "--name-only", "--diff-filter=U")

    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert unmerged.stdout == ""
    assert (alice / "wiki/concepts/alice.md").is_file()
    assert (alice / "wiki/concepts/bob.md").is_file()
    bob_source = b"bob source\n"
    assert (alice / "sources/bob.md").read_bytes() == bob_source
    assert _git_bytes(alice, "show", "HEAD:sources/bob.md").stdout == bob_source
    for relative_path in (
        "wiki/concepts/bob.md",
        "wiki/journal/operations/2026/08/20260808T020500Z-b22d.md",
    ):
        working_tree_bytes = (alice / relative_path).read_bytes()
        assert b"\r\n" not in working_tree_bytes
        assert (
            _git_bytes(alice, "show", f"HEAD:{relative_path}").stdout
            == working_tree_bytes
        )
    assert check_portable_repo(_config(alice)) == {
        "status": "pass",
        "errors": 0,
        "warnings": 0,
        "issues": [],
    }

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki.config import PortableConfig, load_portable_config
from obsidian_wiki.frontmatter import parse_frontmatter
from obsidian_wiki.operations import parse_operation_log, render_operation_log
from obsidian_wiki.portable import PROJECT_AGENT_DIRS, setup_portable_repo
from obsidian_wiki.portable_check import check_portable_repo
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.skill_trees import discover_skill_collection, skill_catalog
from obsidian_wiki.transaction import TransactionManager


def _run_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
    text: bool = True,
    check: bool = False,
):
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=text,
            capture_output=True,
            check=check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        rendered = " ".join(argv)
        raise AssertionError(
            f"command {rendered} timed out after {timeout} seconds"
        ) from exc


def test_command_timeout_reports_the_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def expire(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "status"], 30)

    monkeypatch.setattr(subprocess, "run", expire)

    with pytest.raises(AssertionError, match=r"git status.*30 seconds"):
        _run_command(["git", "status"], cwd=tmp_path, timeout=30)


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
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": os.devnull,
            "HOME": str(test_home),
            "XDG_CONFIG_HOME": str(test_home / "xdg"),
        }
    )
    return environment


def _git_command(root: Path, *args: str) -> list[str]:
    return [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
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
    return _run_command(
        _git_command(root, *args),
        cwd=root,
        timeout=30,
        text=True,
        check=check,
        env=_git_environment(root),
    )


def _git_bytes(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return _run_command(
        _git_command(root, *args),
        cwd=root,
        timeout=30,
        text=False,
        check=True,
        env=_git_environment(root),
    )


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = _git_environment(root)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1])
    return _run_command(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=root,
        timeout=60,
        env=environment,
        text=True,
        check=False,
    )


def _clone(source: Path, target: Path) -> None:
    _run_command(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.autocrlf=true",
            "-c",
            "core.safecrlf=false",
            "clone",
            "-q",
            str(source),
            str(target),
        ],
        cwd=source,
        timeout=30,
        text=False,
        env=_git_environment(source),
        check=True,
    )


def _config(root: Path) -> PortableConfig:
    return load_portable_config(
        root / ".llmwikiops/config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )


def _portable_seed(tmp_path: Path) -> Path:
    source_skills = tmp_path / "framework-skills"
    skill = source_skills / "wiki-query"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: wiki-query\n"
        "description: Search the compiled wiki and synthesize a cited answer.\n"
        "---\n\n"
        "# Query\n\n"
        "Inspect summaries before reading only the relevant page bodies.\n",
        encoding="utf-8",
    )
    reference = skill / "references/查询说明.md"
    reference.parent.mkdir()
    reference.write_text("# 查询说明\n", encoding="utf-8")
    root = tmp_path / "seed"
    setup_portable_repo(root, version=__version__, source_skills=source_skills)
    return root


def test_portable_seed_exposes_complete_useful_skill_mirrors(tmp_path: Path) -> None:
    root = _portable_seed(tmp_path)
    canonical = discover_skill_collection(root / ".skills")

    for agent_relative, _label in PROJECT_AGENT_DIRS:
        assert discover_skill_collection(root / agent_relative) == canonical
        mirrored = root / agent_relative / "wiki-query/SKILL.md"
        text = mirrored.read_text(encoding="utf-8")
        assert "description: Search the compiled wiki" in text
        assert "Inspect summaries before reading" in text
        assert "Portable adapter" not in text
        assert "../../../.skills/" not in text


def test_cjk_custom_skill_sync_check_and_transaction_survive_clone_move(
    tmp_path: Path,
) -> None:
    root = _portable_seed(tmp_path / "原始知识库")
    custom = root / ".skills/团队知识"
    custom.mkdir()
    (custom / "SKILL.md").write_text(
        "---\n"
        "name: 团队知识\n"
        "description: 维护团队知识库的协作约定。\n"
        "---\n\n"
        "# 团队知识\n\n"
        "保留中文名称和资源路径。\n",
        encoding="utf-8",
    )
    resource = custom / "references/协作约定.md"
    resource.parent.mkdir()
    resource.write_text("# 协作约定\n", encoding="utf-8")

    dry = _cli(root, "repo", "sync-skills", "--json")
    assert dry.returncode == 1, dry.stdout + dry.stderr
    assert json.loads(dry.stdout)["status"] == "drift"
    assert not (root / ".claude/skills/团队知识").exists()

    applied = _cli(root, "repo", "sync-skills", "--apply", "--json")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert json.loads(applied.stdout)["status"] == "applied"
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        assert discover_skill_collection(root / agent_relative) == (
            discover_skill_collection(root / ".skills")
        )
        assert (root / agent_relative / "团队知识/references/协作约定.md").read_bytes() == (
            resource.read_bytes()
        )

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "seed@example.invalid")
    _git(root, "config", "user.name", "Seed")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "add custom CJK skill")

    moved = tmp_path / "新的位置" / "知识库克隆"
    moved.parent.mkdir()
    _clone(root, moved)

    checked = _cli(moved, "check", "--json")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["status"] == "pass"

    source_id = "sources/验证/迁移检查.md"
    source = moved / source_id
    source.parent.mkdir(parents=True)
    source.write_text("# 迁移检查\n", encoding="utf-8")
    begun = _cli(
        moved, "transaction", "begin", "--source", str(source), "--json"
    )
    assert begun.returncode == 0, begun.stdout + begun.stderr
    transaction = json.loads(begun.stdout)
    candidate = Path(transaction["candidate_vault"]) / "concepts/迁移检查.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        _page(
            title="迁移检查",
            source_id=source_id,
            created=transaction["started_at"],
        ),
        encoding="utf-8",
    )
    validated = _cli(
        moved,
        "transaction",
        "validate",
        transaction["transaction_id"],
        "--json",
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert json.loads(validated.stdout)["status"] == "pass"

    old_root = str(root.resolve()).encode("utf-8")
    new_root = str(moved.resolve()).encode("utf-8")
    tracked = _git(moved, "ls-files", "-z").stdout.rstrip("\0").split("\0")
    for relative in tracked:
        data = (moved / relative).read_bytes()
        assert old_root not in data, relative
        assert new_root not in data, relative


def _page(
    *,
    title: str,
    source_id: str,
    created: str,
    relationship_target: str | None = None,
) -> str:
    relationships = (
        "relationships:\n"
        f'  - target: "[[{relationship_target}]]"\n'
        "    type: related_to\n"
        if relationship_target is not None
        else ""
    )
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
{relationships}provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
---
# {title}
"""


_CJK_SOURCE_ID = "sources/meetings/2026-08-06-组会纪要.md"
_CJK_PAGE = "references/2026-08-06-组会纪要.md"


def cjk_candidate_page(started_at: str) -> str:
    return f"""---
title: 组会纪要
category: references
tags:
  - collaboration
sources:
  - {_CJK_SOURCE_ID}
created: {started_at}
updated: {started_at}
summary: 版本管理决策的组会纪要。
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
---
# 组会纪要
"""


def _ingest(
    root: Path,
    *,
    source_id: str,
    page: str,
    title: str,
    transaction_id: str,
    started_at: str,
    completed_at: str,
    source_bytes: bytes,
    relationship_target: str | None = None,
) -> tuple[PortableConfig, Path]:
    config = _config(root)
    source = root / source_id
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(source_bytes)
    manager = TransactionManager(config)
    record = manager.begin(
        [source], transaction_id=transaction_id, started_at=started_at
    )
    candidate = record.candidate_vault / page
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        _page(
            title=title,
            source_id=source_id,
            created=completed_at[:10],
            relationship_target=relationship_target,
        ),
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


def test_unrelated_source_transactions_only_need_owner_resolution_for_log(
    tmp_path: Path,
) -> None:
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
        source_bytes=b"bob source\n",
    )
    _git(bob, "add", "-A")
    _git(bob, "commit", "-qm", "ingest bob")

    bob_branch = _git(bob, "branch", "--show-current").stdout.strip()
    _git(alice, "remote", "add", "bob", str(bob))
    _git(alice, "fetch", "-q", "bob", bob_branch)
    merge = _git(alice, "merge", "--no-edit", f"bob/{bob_branch}", check=False)
    unmerged = _git(alice, "diff", "--name-only", "--diff-filter=U")

    assert merge.returncode == 1, merge.stdout + merge.stderr
    assert unmerged.stdout.splitlines() == ["wiki/log.md"]
    assert (alice / "wiki/concepts/alice.md").is_file()
    assert (alice / "wiki/concepts/bob.md").is_file()
    bob_source = b"bob source\n"
    assert (alice / "sources/bob.md").read_bytes() == bob_source
    alice_log = parse_operation_log(
        _git_bytes(alice, "show", "HEAD:wiki/log.md").stdout.decode("utf-8")
    )
    bob_log = parse_operation_log(
        _git_bytes(alice, "show", "MERGE_HEAD:wiki/log.md").stdout.decode("utf-8")
    )
    merged_records = tuple(
        sorted(alice_log + bob_log, key=lambda record: record.completed_at)
    )
    (alice / "wiki/log.md").write_text(
        render_operation_log(merged_records), encoding="utf-8"
    )
    _git(alice, "add", "wiki/log.md")
    _git(alice, "commit", "--no-edit")
    assert _git_bytes(alice, "show", "HEAD:sources/bob.md").stdout == bob_source

    for relative_path in ("wiki/concepts/bob.md", "wiki/log.md"):
        working_tree_bytes = (alice / relative_path).read_bytes()
        assert b"\r\n" not in working_tree_bytes
        assert (
            _git_bytes(alice, "show", f"HEAD:{relative_path}").stdout
            == working_tree_bytes
        )
    check_report = check_portable_repo(_config(alice))
    assert check_report.pop("skill_catalog") == skill_catalog(
        discover_skill_collection(alice / ".skills")
    )
    assert check_report == {
        "status": "pass",
        "errors": 0,
        "warnings": 0,
        "issues": [],
    }


def test_framework_nested_frontmatter_survives_transaction_and_clone(
    tmp_path: Path,
) -> None:
    seed = _portable_seed(tmp_path)
    _git(seed, "init", "-q")
    _git(seed, "config", "user.email", "seed@example.invalid")
    _git(seed, "config", "user.name", "Seed")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "portable seed")

    source_id = "sources/framework-page.md"
    page = "concepts/framework-page.md"
    _ingest(
        seed,
        source_id=source_id,
        page=page,
        title="Framework Page",
        transaction_id="framework-nested",
        started_at="2026-08-10T01:00:00Z",
        completed_at="2026-08-10T01:05:00Z",
        source_bytes=b"framework nested frontmatter\n",
        relationship_target="concepts/framework-page",
    )
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "ingest framework page")

    clone = tmp_path / "clone"
    _clone(seed, clone)
    for root in (seed, clone):
        doctor = _cli(root, "doctor", "--json")
        check = _cli(root, "check", "--json")
        assert doctor.returncode == 0, doctor.stdout + doctor.stderr
        assert check.returncode == 0, check.stdout + check.stderr
        assert json.loads(doctor.stdout)["status"] == "pass"
        assert json.loads(check.stdout)["status"] == "pass"
        parsed = parse_frontmatter((root / "wiki" / page).read_text(encoding="utf-8"))
        assert parsed.provenance is not None
        assert parsed.relationships is not None


def test_cjk_source_id_survives_cache_transaction_operation_and_check(
    tmp_path: Path,
) -> None:
    root = _portable_seed(tmp_path / "知识库")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "seed@example.invalid")
    _git(root, "config", "user.name", "Seed")
    _git(root, "config", "core.autocrlf", "true")
    assert _git(root, "config", "--get", "core.autocrlf").stdout.strip() == "true"
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "portable seed")

    source = root / _CJK_SOURCE_ID
    source.parent.mkdir(parents=True)
    source.write_text("# 组会纪要\n\n版本管理决策。\n", encoding="utf-8")
    _git(root, "add", "--", _CJK_SOURCE_ID)
    _git(root, "commit", "-qm", "owner: review CJK meeting source")
    owner_head = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert (
        _git_bytes(root, "show", f"HEAD:{_CJK_SOURCE_ID}").stdout
        == source.read_bytes()
    )

    cache = _cli(
        root,
        "cache-check",
        _CJK_SOURCE_ID,
        "--json",
    )
    assert cache.returncode == 0, cache.stdout + cache.stderr
    assert json.loads(cache.stdout) == {
        "new": [_CJK_SOURCE_ID],
        "modified": [],
        "unchanged": [],
        "missing": [],
    }

    begun = _cli(
        root,
        "transaction",
        "begin",
        "--source",
        _CJK_SOURCE_ID,
        "--json",
    )
    assert begun.returncode == 0, begun.stdout + begun.stderr
    transaction = json.loads(begun.stdout)
    assert transaction["started_at"]
    assert transaction["source_ids"] == [_CJK_SOURCE_ID]
    candidate_vault = Path(transaction["candidate_vault"])
    assert candidate_vault.is_absolute()
    assert candidate_vault.is_dir()

    candidate_text = cjk_candidate_page(transaction["started_at"])
    candidate_metadata = parse_frontmatter(candidate_text)
    assert candidate_metadata.scalars["created"] == transaction["started_at"]
    assert candidate_metadata.scalars["updated"] == transaction["started_at"]
    assert candidate_metadata.lists["sources"] == (_CJK_SOURCE_ID,)
    assert str(root.resolve()) not in candidate_text
    assert str(source.resolve()) not in candidate_text
    assert str(candidate_vault) not in candidate_text

    candidate = candidate_vault / _CJK_PAGE
    candidate.parent.mkdir(parents=True)
    candidate.write_text(candidate_text, encoding="utf-8")

    validated = _cli(
        root,
        "transaction",
        "validate",
        transaction["transaction_id"],
        "--json",
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert json.loads(validated.stdout) == {
        "transaction_id": transaction["transaction_id"],
        "status": "pass",
        "candidate_pages": [_CJK_PAGE],
        "deletions": [],
        "issues": [],
        "warnings": [],
    }

    committed = _cli(
        root,
        "transaction",
        "commit",
        transaction["transaction_id"],
        "--json",
    )
    assert committed.returncode == 0, committed.stdout + committed.stderr
    commit_payload = json.loads(committed.stdout)
    assert commit_payload["created"] == [_CJK_PAGE]
    assert commit_payload["updated"] == []
    assert commit_payload["removed"] == []

    cache_after_commit = _cli(
        root,
        "cache-check",
        _CJK_SOURCE_ID,
        "--json",
    )
    assert cache_after_commit.returncode == 0, (
        cache_after_commit.stdout + cache_after_commit.stderr
    )
    assert json.loads(cache_after_commit.stdout) == {
        "new": [],
        "modified": [],
        "unchanged": [_CJK_SOURCE_ID],
        "missing": [],
    }

    checked = _cli(root, "check", "--json")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    check_payload = json.loads(checked.stdout)
    assert check_payload.pop("skill_catalog") == skill_catalog(
        discover_skill_collection(root / ".skills")
    )
    assert check_payload == {
        "status": "pass",
        "errors": 0,
        "warnings": 0,
        "issues": [],
    }

    shard = (
        root
        / "wiki"
        / ".manifest"
        / "sources"
        / "meetings"
        / "2026-08-06-组会纪要.md.json"
    )
    shard_payload = json.loads(shard.read_text(encoding="utf-8"))
    assert shard_payload["source_id"] == _CJK_SOURCE_ID
    assert shard_payload["pages"] == [_CJK_PAGE]

    promoted = root / "wiki" / _CJK_PAGE
    promoted_text = promoted.read_text(encoding="utf-8")
    assert promoted_text == candidate_text
    promoted_metadata = parse_frontmatter(promoted_text)
    assert promoted_metadata.scalars["created"] == transaction["started_at"]
    assert promoted_metadata.scalars["updated"] == transaction["started_at"]
    assert promoted_metadata.lists["sources"] == (_CJK_SOURCE_ID,)
    assert commit_payload["log_path"] == "log.md"
    operation = root / "wiki" / commit_payload["log_path"]
    operation_changes = parse_operation_log(operation.read_text(encoding="utf-8"))
    operation_change = next(
        change
        for change in operation_changes
        if change.transaction_id == transaction["transaction_id"]
    )
    assert operation_change.transaction_id == transaction["transaction_id"]
    assert operation_change.source_ids == (_CJK_SOURCE_ID,)
    operation_text = operation.read_text(encoding="utf-8")
    assert f"- `{_CJK_SOURCE_ID}`" in operation_text
    assert f"[[{Path(_CJK_PAGE).with_suffix('').as_posix()}]]" in operation_text
    assert str(root.resolve()) not in operation_text

    durable_paths = {
        _CJK_SOURCE_ID,
        f"wiki/{_CJK_PAGE}",
        "wiki/.manifest/sources/meetings/2026-08-06-组会纪要.md.json",
        f"wiki/{commit_payload['log_path']}",
    }
    framework_outputs = durable_paths - {
        _CJK_SOURCE_ID,
        f"wiki/{commit_payload['log_path']}",
    }
    untracked = set(
        _git(root, "ls-files", "--others", "--exclude-standard", "-z")
        .stdout.rstrip("\0")
        .split("\0")
    )
    assert untracked == framework_outputs
    assert not any(path.startswith(".llmwikiops/local/") for path in untracked)
    assert _git(root, "diff", "--name-only").stdout.splitlines() == ["wiki/log.md"]
    assert _git(root, "diff", "--cached", "--quiet", check=False).returncode == 0
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == owner_head
    local_transaction = (
        f".llmwikiops/local/transactions/{transaction['transaction_id']}"
    )
    ignored = _git(root, "check-ignore", "-q", local_transaction, check=False)
    assert ignored.returncode == 0

    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "ingest CJK meeting")

    head_paths = set(
        _git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD")
        .stdout.rstrip("\0")
        .split("\0")
    )
    assert durable_paths <= head_paths
    assert not any(path.startswith(".llmwikiops/local/") for path in head_paths)
    assert _git(root, "status", "--porcelain=v1", "-z").stdout == ""

    durable_files = {
        _CJK_SOURCE_ID: source,
        f"wiki/{_CJK_PAGE}": promoted,
        "wiki/.manifest/sources/meetings/2026-08-06-组会纪要.md.json": shard,
        f"wiki/{commit_payload['log_path']}": operation,
    }
    for relative, path in durable_files.items():
        assert _git_bytes(root, "show", f"HEAD:{relative}").stdout == path.read_bytes()

    clone = tmp_path / "知识库克隆"
    _clone(root, clone)
    clone_paths = set(
        _git(clone, "ls-tree", "-r", "--name-only", "-z", "HEAD")
        .stdout.rstrip("\0")
        .split("\0")
    )
    assert durable_paths <= clone_paths
    assert not any(path.startswith(".llmwikiops/local/") for path in clone_paths)
    for relative, path in durable_files.items():
        expected = path.read_bytes()
        clone_path = clone / relative
        assert clone_path.is_file()
        assert clone_path.read_bytes() == expected
        assert _git_bytes(clone, "show", f"HEAD:{relative}").stdout == expected


def test_task_scoped_source_transaction_and_result_commits_preserve_unrelated_changes(
    tmp_path: Path,
) -> None:
    root = _portable_seed(tmp_path)
    unrelated = root / "owner-notes.md"
    unrelated.write_bytes(b"owner baseline\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "agent@example.invalid")
    _git(root, "config", "user.name", "Agent")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "portable seed")

    unrelated_bytes = b"unrelated owner change\n"
    unrelated.write_bytes(unrelated_bytes)
    source_id = "sources/task-scoped.md"
    source = root / source_id
    source.write_text("# Task-scoped source\n", encoding="utf-8")
    _git(root, "--literal-pathspecs", "add", "--", source_id)
    staged_source = _git(
        root, "--literal-pathspecs", "diff", "--cached", "--", source_id
    ).stdout
    assert source_id in staged_source
    _git(root, "--literal-pathspecs", "diff", "--cached", "--check", "--", source_id)
    _git(
        root,
        "--literal-pathspecs",
        "commit",
        "-m",
        "source: add task-scoped authority",
        "--",
        source_id,
    )
    source_create_commit = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert set(
        _git(
            root,
            "show",
            "--pretty=format:",
            "--name-only",
            source_create_commit,
        ).stdout.splitlines()
    ) == {source_id}

    warning = _cli(root, "check", "--json")
    assert warning.returncode == 0, warning.stdout + warning.stderr
    warning_payload = json.loads(warning.stdout)
    assert warning_payload["status"] == "warn"
    assert warning_payload["issues"] == [
        {
            "code": "source-new",
            "path": source_id,
            "message": "source is not present in the manifest",
            "severity": "warning",
        }
    ]

    begun = _cli(root, "transaction", "begin", "--source", source_id, "--json")
    assert begun.returncode == 0, begun.stdout + begun.stderr
    first = json.loads(begun.stdout)
    page_id = "concepts/task-scoped.md"
    candidate = Path(first["candidate_vault"]) / page_id
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        _page(
            title="Task Scoped",
            source_id=source_id,
            created=first["started_at"],
        ),
        encoding="utf-8",
    )
    validated = _cli(
        root, "transaction", "validate", first["transaction_id"], "--json"
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert json.loads(validated.stdout)["status"] == "pass"
    committed = _cli(
        root, "transaction", "commit", first["transaction_id"], "--json"
    )
    assert committed.returncode == 0, committed.stdout + committed.stderr
    first_result = json.loads(committed.stdout)
    assert first_result["created"] == [page_id]

    hot_status = _cli(root, "hot", "status", "--json")
    assert hot_status.returncode == 0, hot_status.stdout + hot_status.stderr
    assert json.loads(hot_status.stdout)["stale"] is True
    hot_inputs = _cli(root, "hot", "inputs", "--json")
    assert hot_inputs.returncode == 0, hot_inputs.stdout + hot_inputs.stderr
    first_hot_inputs = json.loads(hot_inputs.stdout)
    assert any(page["path"] == page_id for page in first_hot_inputs["pages"])
    assert any(
        operation["transaction_id"] == first["transaction_id"]
        for operation in first_hot_inputs["operations"]
    )
    hot = root / "wiki/hot.md"
    hot_before = hot.read_bytes()
    hot.write_text(
        "# Hot\n\n"
        + "\n".join(
            f"- [[{page['path']}|{page['title']}]]: {page['summary']}"
            for page in first_hot_inputs["pages"]
        )
        + "\n\n"
        + "\n".join(
            f"- Transaction `{operation['transaction_id']}`"
            for operation in first_hot_inputs["operations"]
        )
        + "\n",
        encoding="utf-8",
    )
    assert hot.read_bytes() != hot_before
    marked = _cli(root, "hot", "mark-current", "--json")
    assert marked.returncode == 0, marked.stdout + marked.stderr
    assert json.loads(marked.stdout) == {"stale": False, "status": "current"}
    current = _cli(root, "hot", "status", "--json")
    assert current.returncode == 0, current.stdout + current.stderr
    assert json.loads(current.stdout) == {
        "stale": False,
        "reason": "current",
        "fingerprint": first_hot_inputs["fingerprint"],
    }
    checked = _cli(root, "check", "--json")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["status"] == "pass"

    shard = ShardedManifest(_config(root)).entry_path(source_id)
    first_result_paths = [
        f"wiki/{page_id}",
        shard.relative_to(root).as_posix(),
        f"wiki/{first_result['log_path']}",
    ]
    hot_diff = _git(root, "diff", "--quiet", "--", "wiki/hot.md", check=False)
    assert hot_diff.returncode == 1
    if hot_diff.returncode == 1:
        first_result_paths.append("wiki/hot.md")
    _git(root, "--literal-pathspecs", "add", "--", *first_result_paths)
    assert set(
        _git(root, "diff", "--cached", "--name-only", "--", *first_result_paths)
        .stdout.splitlines()
    ) == set(first_result_paths)
    _git(
        root,
        "--literal-pathspecs",
        "diff",
        "--cached",
        "--check",
        "--",
        *first_result_paths,
    )
    _git(
        root,
        "--literal-pathspecs",
        "commit",
        "-m",
        "wiki: compile task-scoped source",
        "--",
        *first_result_paths,
    )
    first_result_commit = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert set(
        _git(
            root,
            "show",
            "--pretty=format:",
            "--name-only",
            first_result_commit,
        ).stdout.splitlines()
    ) == set(first_result_paths)

    source.write_text("# Task-scoped source\n\nUpdated authority.\n", encoding="utf-8")
    _git(root, "--literal-pathspecs", "add", "--", source_id)
    assert source_id in _git(
        root, "--literal-pathspecs", "diff", "--cached", "--", source_id
    ).stdout
    _git(root, "--literal-pathspecs", "diff", "--cached", "--check", "--", source_id)
    _git(
        root,
        "--literal-pathspecs",
        "commit",
        "-m",
        "source: update task-scoped authority",
        "--",
        source_id,
    )
    source_update_commit = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert set(
        _git(
            root,
            "show",
            "--pretty=format:",
            "--name-only",
            source_update_commit,
        ).stdout.splitlines()
    ) == {source_id}

    begun = _cli(root, "transaction", "begin", "--source", source_id, "--json")
    assert begun.returncode == 0, begun.stdout + begun.stderr
    second = json.loads(begun.stdout)
    candidate = Path(second["candidate_vault"]) / page_id
    candidate.parent.mkdir(parents=True)
    updated_page = _page(
        title="Task Scoped",
        source_id=source_id,
        created=first["started_at"],
    ).replace(
        f"updated: {first['started_at']}",
        f"updated: {second['started_at']}",
    )
    candidate.write_text(updated_page + "\nUpdated knowledge.\n", encoding="utf-8")
    validated = _cli(
        root, "transaction", "validate", second["transaction_id"], "--json"
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert json.loads(validated.stdout)["status"] == "pass"
    committed = _cli(
        root, "transaction", "commit", second["transaction_id"], "--json"
    )
    assert committed.returncode == 0, committed.stdout + committed.stderr
    second_result = json.loads(committed.stdout)
    assert second_result["updated"] == [page_id]

    hot_status = _cli(root, "hot", "status", "--json")
    assert hot_status.returncode == 0, hot_status.stdout + hot_status.stderr
    assert json.loads(hot_status.stdout)["stale"] is True
    hot_inputs = _cli(root, "hot", "inputs", "--json")
    assert hot_inputs.returncode == 0, hot_inputs.stdout + hot_inputs.stderr
    second_hot_inputs = json.loads(hot_inputs.stdout)
    assert any(page["path"] == page_id for page in second_hot_inputs["pages"])
    assert any(
        operation["transaction_id"] == second["transaction_id"]
        for operation in second_hot_inputs["operations"]
    )
    hot_before = hot.read_bytes()
    hot.write_text(
        "# Hot\n\n"
        + "\n".join(
            f"- [[{page['path']}|{page['title']}]]: {page['summary']}"
            for page in second_hot_inputs["pages"]
        )
        + "\n\n"
        + "\n".join(
            f"- Transaction `{operation['transaction_id']}`"
            for operation in second_hot_inputs["operations"]
        )
        + "\n",
        encoding="utf-8",
    )
    assert hot.read_bytes() != hot_before
    marked = _cli(root, "hot", "mark-current", "--json")
    assert marked.returncode == 0, marked.stdout + marked.stderr
    assert json.loads(marked.stdout) == {"stale": False, "status": "current"}
    current = _cli(root, "hot", "status", "--json")
    assert current.returncode == 0, current.stdout + current.stderr
    assert json.loads(current.stdout) == {
        "stale": False,
        "reason": "current",
        "fingerprint": second_hot_inputs["fingerprint"],
    }
    checked = _cli(root, "check", "--json")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["status"] == "pass"

    second_result_paths = [
        f"wiki/{page_id}",
        shard.relative_to(root).as_posix(),
        f"wiki/{second_result['log_path']}",
    ]
    hot_diff = _git(root, "diff", "--quiet", "--", "wiki/hot.md", check=False)
    assert hot_diff.returncode == 1
    if hot_diff.returncode == 1:
        second_result_paths.append("wiki/hot.md")
    _git(root, "--literal-pathspecs", "add", "--", *second_result_paths)
    assert set(
        _git(root, "diff", "--cached", "--name-only", "--", *second_result_paths)
        .stdout.splitlines()
    ) == set(second_result_paths)
    _git(
        root,
        "--literal-pathspecs",
        "diff",
        "--cached",
        "--check",
        "--",
        *second_result_paths,
    )
    _git(
        root,
        "--literal-pathspecs",
        "commit",
        "-m",
        "wiki: recompile task-scoped source",
        "--",
        *second_result_paths,
    )
    second_result_commit = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert set(
        _git(
            root,
            "show",
            "--pretty=format:",
            "--name-only",
            second_result_commit,
        ).stdout.splitlines()
    ) == set(second_result_paths)

    checked = _cli(root, "check", "--json")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["status"] == "pass"
    assert unrelated.read_bytes() == unrelated_bytes
    assert _git(root, "status", "--short").stdout.splitlines() == [
        " M owner-notes.md"
    ]
    for commit in (
        source_create_commit,
        first_result_commit,
        source_update_commit,
        second_result_commit,
    ):
        assert "owner-notes.md" not in _git(
            root, "show", "--pretty=format:", "--name-only", commit
        ).stdout.splitlines()

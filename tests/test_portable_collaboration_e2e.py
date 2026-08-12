from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki.config import PortableConfig, load_portable_config
from obsidian_wiki.frontmatter import parse_frontmatter
from obsidian_wiki.operations import write_operation
from obsidian_wiki.portable import PROJECT_AGENT_DIRS, setup_portable_repo
from obsidian_wiki.portable_check import check_portable_repo
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.skill_trees import discover_skill_collection
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


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = _git_environment(root)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1])
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
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
relationships:
  - target: "[[index]]"
    type: related_to
provenance:
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
relationships:
  - target: "[[index]]"
    type: related_to
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
---
# 组会纪要
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
        operation_suffix="f12a",
        source_bytes=b"framework nested frontmatter\n",
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

    cache = _cli(
        root,
        "cache-check",
        "--configured",
        str(source),
        "--json",
    )
    assert cache.returncode == 0, cache.stdout + cache.stderr
    assert json.loads(cache.stdout) == {
        "new": [_CJK_SOURCE_ID],
        "modified": [],
        "unchanged": [],
        "missing": [],
        "context_warnings": [],
    }

    begun = _cli(
        root,
        "transaction",
        "begin",
        "--source",
        str(source),
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
        "--configured",
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
        "context_warnings": [],
    }

    checked = _cli(root, "check", "--json")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout) == {
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
    operation = root / "wiki" / commit_payload["operation_path"]
    operation_text = operation.read_text(encoding="utf-8")
    operation_metadata = parse_frontmatter(operation_text)
    assert operation_metadata.lists["sources"] == (_CJK_SOURCE_ID,)
    assert f"[[{Path(_CJK_PAGE).with_suffix('').as_posix()}]]" in operation_text
    assert str(root.resolve()) not in operation_text

    durable_paths = {
        _CJK_SOURCE_ID,
        f"wiki/{_CJK_PAGE}",
        "wiki/.manifest/sources/meetings/2026-08-06-组会纪要.md.json",
        f"wiki/{commit_payload['operation_path']}",
    }
    untracked = set(
        _git(root, "ls-files", "--others", "--exclude-standard", "-z")
        .stdout.rstrip("\0")
        .split("\0")
    )
    assert untracked == durable_paths
    assert not any(path.startswith(".obsidian-wiki/local/") for path in untracked)
    assert _git(root, "diff", "--quiet", check=False).returncode == 0
    assert _git(root, "diff", "--cached", "--quiet", check=False).returncode == 0
    local_transaction = (
        f".obsidian-wiki/local/transactions/{transaction['transaction_id']}"
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
    assert not any(path.startswith(".obsidian-wiki/local/") for path in head_paths)
    assert _git(root, "status", "--porcelain=v1", "-z").stdout == ""

    durable_files = {
        _CJK_SOURCE_ID: source,
        f"wiki/{_CJK_PAGE}": promoted,
        "wiki/.manifest/sources/meetings/2026-08-06-组会纪要.md.json": shard,
        f"wiki/{commit_payload['operation_path']}": operation,
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
    assert not any(path.startswith(".obsidian-wiki/local/") for path in clone_paths)
    for relative, path in durable_files.items():
        expected = path.read_bytes()
        clone_path = clone / relative
        assert clone_path.is_file()
        assert clone_path.read_bytes() == expected
        assert _git_bytes(clone, "show", f"HEAD:{relative}").stdout == expected

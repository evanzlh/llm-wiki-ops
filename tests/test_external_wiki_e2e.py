from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID, __version__, cli
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.portable import setup_portable_repo
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.transaction import TransactionError, TransactionManager
from obsidian_wiki.trust import (
    TRUST_LEDGER_RELATIVE_PATH,
    build_trust_ledger,
    write_trust_ledger,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1]
CLI_LAUNCHER = (str(Path(sys.executable).absolute()), "-m", "obsidian_wiki")


def _environment(agent_home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(agent_home),
            "PYTHONPATH": str(SOURCE_ROOT),
            "XDG_CONFIG_HOME": str(agent_home / ".config"),
        }
    )
    return environment


def _run_cli(
    cwd: Path,
    agent_home: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*CLI_LAUNCHER, *arguments],
        cwd=cwd,
        env=_environment(agent_home),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def _run_selected(
    cwd: Path,
    agent_home: Path,
    repository: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return _run_cli(cwd, agent_home, "-C", str(repository), *arguments)


def _json_result(result: subprocess.CompletedProcess[str]) -> object:
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _page(title: str, source_id: str, *, extra_body: str = "") -> str:
    return f"""---
title: {title}
category: concepts
tags:
  - external-e2e
sources:
  - {source_id}
created: 2026-08-18
updated: 2026-08-18
summary: {title} is available from an explicitly selected external wiki.
base_confidence: 0.90
lifecycle: reviewed
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
---
# {title}

{extra_body}
"""


def _setup_repository(root: Path, sentinel: str, *, extra_body: str = "") -> Path:
    setup_portable_repo(root, version=__version__, source_skills=cli.skills_dir())
    source = root / "sources/input.md"
    source.write_text(f"# Input\n\n{sentinel}\n", encoding="utf-8")
    page = root / "wiki/concepts/sentinel.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_page(sentinel, "sources/input.md", extra_body=extra_body))
    config = load_portable_config(
        root / ".llmwikiops/config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    ShardedManifest(config).upsert(
        source,
        pages=[page.relative_to(config.vault).as_posix()],
        compiled_at="2026-08-18T00:00:00Z",
    )
    ledger = build_trust_ledger(config.vault, reviewed_at="2026-08-18T00:00:00Z")
    write_trust_ledger(
        config.vault / TRUST_LEDGER_RELATIVE_PATH,
        ledger,
        vault=config.vault,
        repository_root=config.root,
        root_identity=config.root_identity,
    )
    return root


def _tree_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    paths = [root, *sorted(root.rglob("*"))]
    snapshot: list[tuple[object, ...]] = []
    for path in paths:
        metadata = path.lstat()
        kind = stat.S_IFMT(metadata.st_mode)
        content = path.read_bytes() if stat.S_ISREG(metadata.st_mode) else None
        snapshot.append(
            (
                path.relative_to(root).as_posix(),
                kind,
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                content,
            )
        )
    return tuple(snapshot)


def _write_candidate(candidate_vault: Path, relative: str, title: str) -> Path:
    page = candidate_vault / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_page(title, "sources/input.md"), encoding="utf-8")
    return page


def _execute_recovery_command(
    command: str,
    *,
    cwd: Path,
    agent_home: Path,
) -> subprocess.CompletedProcess[str]:
    tokens = shlex.split(command)
    assert tokens[0] == "llmwikiops"
    return _run_cli(cwd, agent_home, *tokens[1:])


def test_external_wiki_adapter_lifecycle_from_unrelated_cwd(
    tmp_path: Path,
) -> None:
    business = tmp_path / "business"
    wiki = tmp_path / "wiki"
    agent_home = tmp_path / "agent-home"
    business.mkdir()
    agent_home.mkdir()
    sentinel = "external-wiki-sentinel"
    _setup_repository(wiki, sentinel)
    business_marker = business / "owner-data.bin"
    business_marker.write_bytes(b"owner bytes\x00must remain unchanged\n")
    business_before = _tree_snapshot(business)

    installed = _run_cli(
        business,
        agent_home,
        "agent",
        "install-adapter",
        "--agent",
        "codex",
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert (agent_home / ".codex/skills/llm-wiki-ops/SKILL.md").is_file()

    info = _json_result(_run_selected(business, agent_home, wiki, "info", "--json"))
    assert isinstance(info, dict)
    assert info["runtime"]["root"] == str(wiki)

    query = _json_result(
        _run_selected(
            business,
            agent_home,
            wiki,
            "query",
            "--mode",
            "find",
            "--term",
            sentinel,
            "--json",
        )
    )
    assert sentinel in json.dumps(query)
    context = _json_result(
        _run_selected(business, agent_home, wiki, "context-pack", sentinel, "--json")
    )
    assert sentinel in json.dumps(context)

    begun = _json_result(
        _run_selected(
            business,
            agent_home,
            wiki,
            "transaction",
            "begin",
            "--source",
            "sources/input.md",
            "--json",
        )
    )
    assert isinstance(begun, dict)
    transaction_id = str(begun["transaction_id"])
    candidate_vault = Path(str(begun["candidate_vault"]))
    assert candidate_vault.is_relative_to(wiki)
    _write_candidate(candidate_vault, "concepts/compiled.md", "Compiled sentinel")

    validated = _json_result(
        _run_selected(
            business,
            agent_home,
            wiki,
            "transaction",
            "validate",
            transaction_id,
            "--json",
        )
    )
    assert isinstance(validated, dict) and validated["status"] == "pass"
    committed = _json_result(
        _run_selected(
            business,
            agent_home,
            wiki,
            "transaction",
            "commit",
            transaction_id,
            "--json",
        )
    )
    assert isinstance(committed, dict)
    assert "concepts/compiled.md" in committed["created"]

    transactions = _json_result(
        _run_selected(
            business, agent_home, wiki, "transaction", "list", "--json"
        )
    )
    assert isinstance(transactions, list)
    assert any(item["transaction_id"] == transaction_id for item in transactions)

    hot_status = _json_result(
        _run_selected(business, agent_home, wiki, "hot", "status", "--json")
    )
    assert isinstance(hot_status, dict) and hot_status["stale"] is True
    hot_inputs = _json_result(
        _run_selected(business, agent_home, wiki, "hot", "inputs", "--json")
    )
    assert isinstance(hot_inputs, dict)
    assert len(hot_inputs["pages"]) <= 50
    assert len(hot_inputs["operations"]) <= 10
    (wiki / "wiki/hot.md").write_text(
        "# Hot\n\n"
        + "\n".join(
            f"- [[{page['path']}|{page['title']}]]: {page['summary']}"
            for page in hot_inputs["pages"]
        )
        + "\n",
        encoding="utf-8",
    )
    marked = _json_result(
        _run_selected(
            business, agent_home, wiki, "hot", "mark-current", "--json"
        )
    )
    assert marked == {"stale": False, "status": "current"}

    config = load_portable_config(
        wiki / ".llmwikiops/config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )

    def fail_log(_change: object) -> None:
        raise OSError("retained transaction injection")

    failing_manager = TransactionManager(config, log_writer=fail_log)
    failed = failing_manager.begin(
        [wiki / "sources/input.md"], transaction_id="retained-external"
    )
    _write_candidate(failed.candidate_vault, "concepts/recovered.md", "Recovered")
    with pytest.raises(TransactionError, match="retained transaction injection"):
        failing_manager.commit("retained-external")
    assert TransactionManager(config).load("retained-external").status == "failed"

    retained = _json_result(
        _run_selected(
            business, agent_home, wiki, "transaction", "list", "--json"
        )
    )
    assert isinstance(retained, list)
    failed_payload = next(
        item for item in retained if item["transaction_id"] == "retained-external"
    )
    recovery_command = failed_payload["recommended_action"]["command"]
    expected_prefix = shlex.join(("llmwikiops", "-C", str(wiki)))
    assert recovery_command.startswith(f"{expected_prefix} transaction retry ")
    recovered = _execute_recovery_command(
        recovery_command, cwd=business, agent_home=agent_home
    )
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert TransactionManager(config).load("retained-external").status == "complete"

    assert business_marker.read_bytes() == b"owner bytes\x00must remain unchanged\n"
    assert _tree_snapshot(business) == business_before
    assert candidate_vault.is_relative_to(wiki)
    assert config.local_state.is_relative_to(wiki)
    assert (config.vault / str(committed["log_path"])).is_relative_to(wiki)


def test_external_wiki_binding_rejects_or_ignores_alternate_roots(
    tmp_path: Path,
) -> None:
    business = tmp_path / "business"
    wiki = tmp_path / "wiki"
    agent_home = tmp_path / "agent-home"
    alternate = business / "alternate-wiki"
    business.mkdir()
    agent_home.mkdir()
    _setup_repository(alternate, "alternate-sentinel")
    _setup_repository(
        wiki,
        "selected-sentinel",
        extra_body=f"A note may mention {alternate.resolve()} without selecting it.",
    )
    child = wiki / "unconfigured-child"
    child.mkdir()
    business_before = _tree_snapshot(business)
    alternate_before = _tree_snapshot(alternate)

    missing = _run_cli(
        business,
        agent_home,
        "query",
        "--mode",
        "find",
        "--term",
        "selected-sentinel",
        "--json",
    )
    assert missing.returncode == 1
    assert "repository" in missing.stdout + missing.stderr

    child_root = _run_selected(
        business,
        agent_home,
        child,
        "query",
        "--mode",
        "find",
        "--term",
        "selected-sentinel",
        "--json",
    )
    assert child_root.returncode == 1
    assert str(child) in child_root.stdout + child_root.stderr

    from_alternate = _json_result(
        _run_selected(
            alternate,
            agent_home,
            wiki,
            "query",
            "--mode",
            "find",
            "--term",
            "selected-sentinel",
            "--json",
        )
    )
    assert "selected-sentinel" in json.dumps(from_alternate)
    assert "alternate-sentinel" not in json.dumps(from_alternate)

    absolute_note = _json_result(
        _run_selected(
            business,
            agent_home,
            wiki,
            "query",
            "--mode",
            "find",
            "--term",
            str(alternate.resolve()),
            "--json",
        )
    )
    assert str(alternate.resolve()) in json.dumps(absolute_note)

    assert _tree_snapshot(alternate) == alternate_before
    assert _tree_snapshot(business) == business_before

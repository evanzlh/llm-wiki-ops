from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from obsidian_wiki.cli import (
    _list_record_payload,
    _normalize_cache_check_argv,
    _render_transaction_failure,
    build_parser,
)
from obsidian_wiki.transaction import TransactionError, TransactionRecord
from obsidian_wiki.transaction_guidance import guidance_for_record
from obsidian_wiki.transaction_validation import TransactionValidationReport
from obsidian_wiki import cli as cli_module


def test_runtime_exports_only_new_repository_variable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    config = SimpleNamespace(
        root=root,
        vault=root / "wiki",
        sources=(root / "sources",),
        settings={},
    )

    values = cli_module._config_values(config)

    assert values["LLMWIKIOPS_REPO"] == str(root)
    assert "OBSIDIAN_WIKI_REPO" not in values


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "obsidian_wiki/_data/skills/llm-wiki/SKILL.md"
TRANSACTION_REVIEW = (
    ROOT / "obsidian_wiki/_data/skills/wiki-transaction-review/SKILL.md"
)
SOURCE_WORKFLOW_SKILLS = (
    ROOT / "obsidian_wiki/_data/skills/wiki-capture/SKILL.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-ingest/SKILL.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-import/SKILL.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-research/SKILL.md",
)
SOURCE_WORKFLOW_REFERENCES = (
    ROOT / "obsidian_wiki/_data/skills/wiki-capture/references/source-snapshot.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-ingest/references/ingest-prompts.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-ingest/references/pageindex.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-ingest/references/url-sources.md",
)
HISTORY_SKILLS = tuple(
    ROOT / f"obsidian_wiki/_data/skills/{name}/SKILL.md"
    for name in (
        "claude-history-ingest",
        "codex-history-ingest",
        "copilot-history-ingest",
        "hermes-history-ingest",
        "openclaw-history-ingest",
        "pi-history-ingest",
        "wiki-agent",
    )
)
HISTORY_FORMAT_REFERENCES = tuple(
    ROOT
    / f"obsidian_wiki/_data/skills/{name}-history-ingest/references/{name}-data-format.md"
    for name in ("claude", "codex", "copilot", "hermes", "openclaw")
)
MAINTENANCE_SKILLS = tuple(
    ROOT / f"obsidian_wiki/_data/skills/{name}/SKILL.md"
    for name in (
        "cross-linker",
        "daily-update",
        "tag-taxonomy",
        "wiki-dedup",
        "wiki-lint",
        "wiki-rebuild",
        "wiki-status",
        "wiki-synthesize",
        "wiki-update",
    )
)
GIT_CONTEXT_PROTOCOL = """- Repository-local context: `<git-cli>` is the argv prefix `["git"]`; run it
  with the validated root as `cwd`.
- External adapter context: `<git-cli>` is the argv prefix
  `["git", "-C", "<root>"]`; keep the caller's CWD unchanged.
Append every Git subcommand and path as separate argv elements; `<git-cli>` is
an argv prefix, never one shell token."""
NON_REPOSITORY_RUNTIME_SKILLS = {
    "impl-validator",
    "session-brain",
    "session-search",
    "skill-creator",
    "wiki-history-ingest",
}


@pytest.mark.parametrize("external", [False, True])
def test_git_examples_use_context_aware_argv_prefix(
    tmp_path: Path, external: bool
) -> None:
    repository = tmp_path / ("external" if external else "local")
    repository.mkdir()
    subprocess.run(
        ["git", "init"], cwd=repository, check=True, capture_output=True, text=True
    )
    cwd = tmp_path / "outside" if external else repository
    cwd.mkdir(exist_ok=True)
    prefix = ["git", "-C", str(repository)] if external else ["git"]

    source_id = "sources/tracked file.md"
    source = repository / source_id
    source.parent.mkdir()
    source.write_text("reviewed\n", encoding="utf-8")
    (repository / "notes.txt").write_text("owner staging\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "user.name", "Protocol Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "protocol@example.invalid"],
        cwd=repository,
        check=True,
    )
    ls_files = (
        "--literal-pathspecs",
        "ls-files",
        "--error-unmatch",
        "--",
        source_id,
    )
    status = (
        "--literal-pathspecs",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        source_id,
    )
    subprocess.run(
        [*prefix, "--literal-pathspecs", "add", "--", source_id],
        cwd=cwd,
        check=True,
    )
    subprocess.run(
        [*prefix, "--literal-pathspecs", "add", "--", "notes.txt"],
        cwd=cwd,
        check=True,
    )
    subprocess.run(
        [*prefix, "--literal-pathspecs", "diff", "--cached", "--", source_id],
        cwd=cwd,
        check=True,
    )
    subprocess.run(
        [*prefix, "--literal-pathspecs", "diff", "--cached", "--check", "--", source_id],
        cwd=cwd,
        check=True,
    )
    subprocess.run(
        [*prefix, "--literal-pathspecs", "commit", "-m", "review source", "--", source_id],
        cwd=cwd,
        check=True,
    )
    committed_paths = subprocess.run(
        [*prefix, "show", "--format=", "--name-only", "HEAD"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert committed_paths == [source_id]
    assert subprocess.run(
        [*prefix, "diff", "--cached", "--name-only"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout == "notes.txt\n"

    listed = subprocess.run(
        [*prefix, *ls_files], cwd=cwd, check=True, capture_output=True, text=True
    )
    clean = subprocess.run(
        [*prefix, *status], cwd=cwd, check=True, capture_output=True, text=True
    )
    assert listed.stdout == f"{source_id}\n"
    assert clean.stdout == ""


@pytest.mark.parametrize("state", ("absent", "modified", "unchanged"))
def test_source_checkpoint_commits_only_expected_task_owned_changes(
    tmp_path: Path, state: str
) -> None:
    repo = tmp_path / state
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Protocol Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "protocol@example.invalid"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)

    source_id = "sources/evidence.md"
    source = repo / source_id
    source.parent.mkdir()
    if state != "absent":
        source.write_text("reviewed\n", encoding="utf-8")
        subprocess.run(
            ["git", "--literal-pathspecs", "add", "--", source_id],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "--literal-pathspecs", "commit", "-m", "source", "--", source_id],
            cwd=repo,
            check=True,
        )
    if state == "absent":
        source.write_text("new evidence\n", encoding="utf-8")
    elif state == "modified":
        source.write_text("updated evidence\n", encoding="utf-8")

    (repo / "unrelated.md").write_text("leave staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "unrelated.md"], cwd=repo, check=True)
    before = subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            source_id,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if state == "absent":
        assert before == f"?? {source_id}\n"
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    if before:
        subprocess.run(
            ["git", "--literal-pathspecs", "add", "--", source_id],
            cwd=repo,
            check=True,
        )
        displayed = subprocess.run(
            ["git", "--literal-pathspecs", "diff", "--cached", "--", source_id],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert source_id in displayed
        subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "diff",
                "--cached",
                "--check",
                "--",
                source_id,
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "commit",
                "-m",
                "review source",
                "--",
                source_id,
            ],
            cwd=repo,
            check=True,
        )
    else:
        assert state == "unchanged"

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if state == "unchanged":
        assert head_after == head_before
    else:
        assert head_after != head_before
    assert subprocess.run(
        ["git", "show", "--format=", "--name-only", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines() == [source_id]
    assert subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout == "unrelated.md\n"
    assert subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            source_id,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout == ""

    documented_ls_files = (
        '[<git-cli>, "--literal-pathspecs", "ls-files", "--error-unmatch", '
        '"--", "<Source ID>"]'
    )
    documented_status = (
        '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", '
        '"--untracked-files=all", "--", "<Source ID>"]'
    )
    runtime_markdown = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "obsidian_wiki/_data/skills").rglob("*.md")
    )
    assert documented_ls_files in runtime_markdown
    assert documented_status in runtime_markdown

    for path in (
        path
        for path in (ROOT / "obsidian_wiki/_data/skills").glob("*/SKILL.md")
        if path.parent.name not in NON_REPOSITORY_RUNTIME_SKILLS
    ):
        contents = path.read_text(encoding="utf-8")
        assert contents.count(GIT_CONTEXT_PROTOCOL) == 1, path
        examples = contents.replace(GIT_CONTEXT_PROTOCOL, "")
        assert '["git",' not in examples, path
        assert not re.search(r"(?m)^git (?:--literal-pathspecs|diff|status|rev-parse)", examples), path
        assert not re.search(r"`git (?:--literal-pathspecs|diff|status|rev-parse)", examples), path


def _history_file_topology_ok(root: Path, selected: Path) -> bool:
    try:
        root_info = os.lstat(root)
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            return False
        relative = selected.relative_to(root)
        current = root
        for part in relative.parts[:-1]:
            current /= part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return False
        terminal = os.lstat(selected)
        return stat.S_ISREG(terminal.st_mode) and terminal.st_nlink == 1
    except (FileNotFoundError, OSError, ValueError):
        return False


def _history_snapshot_action(
    *,
    exists: bool,
    ordinary_single_link: bool,
    head_exists: bool,
    tracked: bool,
    clean_before_update: bool,
    expected_identity: tuple[str, str, str],
    stored_identity: tuple[str, str, str] | None,
) -> str:
    if not exists:
        return (
            "create"
            if head_exists and not tracked and clean_before_update
            else "collision-fail-closed"
        )
    if (
        ordinary_single_link
        and head_exists
        and tracked
        and clean_before_update
        and stored_identity == expected_identity
    ):
        return "owner-reviewed-atomic-replacement"
    return "collision-fail-closed"


def _history_existing_git_gate(repo: Path, target: Path) -> bool:
    relative = target.relative_to(repo).as_posix()
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode:
        return False
    tracked = subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "ls-files",
            "--error-unmatch",
            "--",
            relative,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode or tracked.stdout != f"{relative}\n":
        return False
    status = subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            relative,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return status.returncode == 0 and status.stdout == ""


def _literal_path_status(repo: Path, relative: str) -> str:
    return subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            relative,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _literal_path_is_indexed(repo: Path, relative: str) -> bool:
    listed = subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "ls-files",
            "--error-unmatch",
            "--",
            relative,
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return listed.returncode == 0 and listed.stdout == f"{relative}\n"


def test_canonical_protocol_has_required_top_level_sections() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    headings = re.findall(r"(?m)^## (.+)$", text)
    assert headings[:3] == [
        "Configuration",
        "Authority and provenance",
        "Knowledge write protocol",
    ]


def test_canonical_protocol_defines_configuration_and_authority() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for required in (
        "nearest ancestor `.llmwikiops/config.toml`",
        "llmwikiops setup [DIR]",
        "fail closed",
        "user-supplied exact root",
        "vault `AGENTS.md` when present",
        "repository-relative Source ID",
        "reviewed Markdown snapshot",
    ):
        assert required in flat


def test_canonical_protocol_defines_dual_context_cli_and_git_forms() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    for required in (
        "Repository-local context: `<wiki-cli>` is `llmwikiops`.",
        "External adapter context: `<wiki-cli>` is `llmwikiops -C <root>` for the\n"
        "  validated immutable root.",
        "`<wiki-cli> transaction <operation>`",
        "`<wiki-cli> hot <operation>`",
        "`<wiki-cli> check`",
        "`<git-cli>` is the argv prefix `[\"git\"]`",
        "`[\"git\", \"-C\", \"<root>\"]`",
    ):
        assert required in text
    for required in (
        '[<git-cli>, "--literal-pathspecs", "add", "--", "<task-path>"]',
        '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<task-path>"]',
        '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<task-path>"]',
        '[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<task-path>"]',
    ):
        assert required in text
    for preserved in (
        "repository-relative Source ID",
        "candidate_vault",
        "transaction commit owns `log.md`",
    ):
        assert preserved in text


def test_canonical_protocol_is_one_eight_step_transaction() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    protocol = text.split("## Knowledge write protocol", 1)[1]
    steps = re.findall(r"(?m)^(\d+)\. \*\*(.+?)\*\*", protocol)
    assert [int(number) for number, _ in steps] == list(range(1, 9))
    for required in (
        "keep the wiki\n   read-only while building the source closure",
        "transaction begin --source",
        "--json --pretty",
        "candidate_vault",
        "transaction delete",
        "transaction validate",
        "transaction commit",
        "transaction list --json --pretty",
        "recommended_action",
        "allowed_actions",
        "hot status --json",
        "hot inputs --json --pretty",
        "hot mark-current",
    ):
        assert required in protocol


def test_canonical_hot_refresh_requires_a_real_update_before_mark_current() -> None:
    flat = " ".join(CANONICAL.read_text(encoding="utf-8").split())

    assert "Reading existing `hot.md` is not regeneration" in flat
    assert "verify a content-changing working-tree diff" in flat
    assert "never run `<wiki-cli> hot mark-current --json` after a read-only or no-write path" in flat


def test_transaction_recovery_reviews_reported_candidates_before_retry() -> None:
    flat = " ".join(TRANSACTION_REVIEW.read_text(encoding="utf-8").split())

    assert "bounded-inspect every page returned in `candidate_pages`" in flat
    assert "A status or validation envelope alone is not candidate review" in flat
    assert "Before a retry or other recovery action that promotes candidates" in flat


def test_promotion_and_hot_overlap_guards_precede_every_mutation() -> None:
    for path in (CANONICAL, *MAINTENANCE_SKILLS):
        flat = " ".join(path.read_text(encoding="utf-8").split())
        promotion_guard = flat.index("pre-promotion overlap guard")
        initial_commit = flat.index("<wiki-cli> transaction commit <id> --json --pretty")
        hot_guard = flat.index("pre-hot-write overlap guard")
        hot_write = flat.index("tracked `hot.md` working-tree diff", hot_guard)
        assert promotion_guard < initial_commit, path
        assert hot_guard < hot_write, path
        for required in (
            "every candidate page and deletion target",
            "every affected manifest shard",
            "vault-relative `log_path`",
            "staged or unstaged",
            "stop before transaction mutation",
            '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<promotion-path>"]',
            '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<hot-path>"]',
            "stop before hot mutation",
        ):
            assert required in flat, f"{path}: missing {required!r}"


@pytest.mark.parametrize(
    "relative",
    (
        "wiki/concepts/affected.md",
        "wiki/.manifest/sources/ab/source.json",
        "wiki/log.md",
        "wiki/hot.md",
    ),
)
@pytest.mark.parametrize("state", ("staged", "unstaged"))
def test_literal_overlap_guard_detects_each_mutation_target_state(
    tmp_path: Path, relative: str, state: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Overlap Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "overlap@example.invalid"],
        cwd=repo,
        check=True,
    )
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", relative], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    target.write_text("owner overlap\n", encoding="utf-8")
    if state == "staged":
        subprocess.run(
            ["git", "--literal-pathspecs", "add", "--", relative],
            cwd=repo,
            check=True,
        )

    status = _literal_path_status(repo, relative)
    assert status.startswith("M ") if state == "staged" else status.startswith(" M")
    before = target.read_bytes()
    if status == "":
        target.write_text("transaction or hot mutation\n", encoding="utf-8")
    assert target.read_bytes() == before


def test_every_promotion_capable_retry_requires_fresh_bounded_review() -> None:
    for path in (CANONICAL, TRANSACTION_REVIEW, *MAINTENANCE_SKILLS):
        flat = " ".join(path.read_text(encoding="utf-8").split())
        for required in (
            "Immediately before every promotion-capable retry",
            "fresh validation",
            "current `candidate_pages`",
            "current deletion set",
            "Failed-state checks alone are insufficient",
        ):
            assert required in flat, f"{path}: missing {required!r}"


def test_begin_passes_the_complete_source_closure_to_one_option() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    command = (
        "<wiki-cli> transaction begin --source <source1> [source2 ...] "
        "--json --pretty"
    )
    assert command in text
    assert text.count("--source") == 1
    assert "Repeat `--source`" not in text


def test_canonical_protocol_defines_task_scoped_autonomy_and_risk_escalation() -> None:
    flat = " ".join(CANONICAL.read_text(encoding="utf-8").split())
    for required in (
        "explicit user request authorizes ordinary local steps",
        "create or update an in-scope Source snapshot",
        "explicit task request authorizes Agent materialization and review",
        "insufficient or ambiguous evidence",
        "owner-overlapping dirty paths",
        "stage and locally commit exact task-owned paths",
        "inspect the staged diff",
        "leave unrelated paths untouched",
        "ask immediately before",
        "push",
        "remote",
        "rewrite branch history",
        "overwrite a dirty owner path",
        "discard",
        "abort",
        "retained recovery evidence",
        "semantic ambiguity",
    ):
        assert required in flat
    assert "never edit manifest shards directly" in flat
    assert "transaction commit owns `log.md`" in flat


def test_canonical_uses_agent_review_and_closes_the_local_lifecycle() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "owner review described above" not in text
    for required in (
        "Agent substantive review",
        "exact-path local authority checkpoint",
        "<wiki-cli> check --json --pretty",
        "`created`, `updated`, and `removed`",
        "affected manifest shards",
        "vault-relative `log_path`",
        "changed `hot.md`",
        "display the exact staged patch",
        "one exact-path local result commit",
    ):
        assert required in flat


def test_root_scoped_git_prefixes_every_vault_relative_result_path() -> None:
    documents = (
        CANONICAL,
        TRANSACTION_REVIEW,
        *SOURCE_WORKFLOW_SKILLS,
        *HISTORY_SKILLS,
        *MAINTENANCE_SKILLS,
    )
    for path in documents:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        for required in (
            "configured vault root",
            "validated repository root",
            "repository-relative vault prefix",
            "vault-relative",
            "`created`",
            "`updated`",
            "`removed`",
            "`log_path`",
            "`hot.md`",
            "manifest shard",
            "already repository-relative",
            "absolute",
            "NUL",
            "backslash",
            "ambiguous",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        assert "prefix" in flat.lower(), path

    for path in (CANONICAL, TRANSACTION_REVIEW, *MAINTENANCE_SKILLS):
        flat = " ".join(path.read_text(encoding="utf-8").split())
        assert "never hardcode `wiki/`" in flat, path


def test_candidate_contract_preserves_transaction_validated_invariants() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for required in (
        "title`, `category`, `tags`, `sources`, `created`, and `updated",
        "created = updated = started_at",
        "preserve the existing `created`",
        "non-empty subset of the transaction source closure",
        "concepts/`, `entities/`, `skills/`, `references/`, `synthesis/`, `journal/`, or `projects/",
        "OBSIDIAN_LINK_FORMAT",
    ):
        assert required in flat


def test_recovery_contract_matches_failure_and_list_payloads(
    capsys,
) -> None:
    record = TransactionRecord(
        transaction_id="tx-1",
        status="failed",
        started_at="2026-08-12T00:00:00+00:00",
        source_ids=("sources/a.md",),
        workspace=Path("/tmp/tx-1"),
        candidate_vault=Path("/tmp/tx-1/wiki"),
        preimages={},
        deletions=(),
    )
    guidance = guidance_for_record(record)
    listed = _list_record_payload(record, guidance)
    assert "error" not in listed and "recovery" not in listed
    assert set(listed["recommended_action"]) == {"command", "reason", "requires"}
    assert listed["recommended_action"] in listed["allowed_actions"]
    assert all(
        set(action) == {"command", "reason", "requires"}
        and isinstance(action["requires"], list)
        for action in listed["allowed_actions"]
    )

    class Manager:
        def load(self, transaction_id: str) -> TransactionRecord:
            assert transaction_id == "tx-1"
            return record

    args = argparse.Namespace(json=True, pretty=False)
    result = _render_transaction_failure(
        args,
        TransactionError("promotion failed"),
        manager=Manager(),
        transaction_id="tx-1",
    )
    assert result == 1
    envelope = json.loads(capsys.readouterr().out)
    assert set(envelope) == {"status", "error", "recovery"}
    assert set(envelope["error"]) == {"code", "message"}
    assert set(envelope["recovery"]) == {
        "transaction_id",
        "transaction_status",
        "inspect_command",
        "preferred_action",
        "alternatives",
    }
    assert envelope["recovery"]["transaction_id"] == listed["transaction_id"]


def test_recovery_protocol_cross_checks_identity_requirements_and_outcomes() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for required in (
        "Save the failed command envelope",
        "does not repeat `error` or `recovery`",
        "exactly one retained record",
        "empty, missing, mismatched, duplicated, or ambiguous",
        "satisfy every string in the action's `requires` list",
        "After each action, reload current structured state",
        "There is no fixed recovery attempt count",
        "Do not repeat the same action with identical inputs and unchanged state",
        "retry` proceeds automatically when its current requirements hold",
        "restore` proceeds automatically only when recorded originals can be restored with no owner drift",
        "Ask for action-specific confirmation before work-losing `discard` or `abort`",
        "Ask before `restore` when owner drift is present",
        "Only a successful `transaction commit` or `transaction retry` is a knowledge commit",
        "restore`, `abort`, and `discard` do not trigger hot refresh",
    ):
        assert required in flat


def test_transaction_review_fields_follow_cli_payload_ownership() -> None:
    record = TransactionRecord(
        transaction_id="tx-review",
        status="active",
        started_at="2026-08-12T00:00:00+00:00",
        source_ids=("sources/a.md",),
        workspace=Path("/tmp/tx-review"),
        candidate_vault=Path("/tmp/tx-review/wiki"),
        preimages={},
        deletions=("concepts/obsolete.md",),
    )
    listed = _list_record_payload(record, guidance_for_record(record))
    validated = TransactionValidationReport(
        transaction_id="tx-review",
        status="pass",
        candidate_pages=("concepts/a.md",),
        deletions=("concepts/obsolete.md",),
        issues=(),
    ).as_dict()

    assert "source_ids" in listed
    assert "candidate_pages" not in listed
    assert "candidate_pages" in validated
    assert "source_ids" not in validated

    text = TRANSACTION_REVIEW.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "list record's `source_ids`" in flat
    assert "validation report's `candidate_pages`" in flat


def test_transaction_review_uses_sparse_safe_diff_and_race_aware_actions() -> None:
    text = TRANSACTION_REVIEW.read_text(encoding="utf-8")
    flat = " ".join(text.split())

    for required in (
        "<wiki-cli> transaction list --json --pretty",
        "candidate_vault",
        "source_ids",
        "candidate_pages",
        "deletions",
        "status",
        "recommended_action",
        "allowed_actions",
        "prospective diff",
        "configured vault",
        "vault-relative",
        "sparse",
        "absolute",
        "`..`",
        "symbolic link",
        "hard link",
        "special file",
        "Do not recursively diff",
        "<wiki-cli> transaction validate <id> --json --pretty",
        "<wiki-cli> transaction commit <id> --json --pretty",
        "inspection-only request",
        "remain read-only",
        "explicit completion or recovery request",
        "Agent substantive review",
        "exact-path local result commit",
        "refresh the list immediately",
        "commit action",
        "re-review",
        "Retry automatically when its current requirements hold",
        "Restore automatically only when recorded originals can be restored with no owner drift",
        "work-losing `discard` and `abort` require action-specific confirmation",
        "abort",
        "discard",
        "explicitly selects",
        "`requires`",
        "transaction ID",
        "refreshed record's status",
        "retained record",
        "complete",
        "restored",
        "ambiguous",
        "semantic ambiguity",
        "owner-overlapping dirty path",
        "push",
        "remote",
        "rewrite history",
    ):
        assert required in flat

    for stale in (
        "explicit user approval",
        "Do not commit, push, or open a pull request with Git",
    ):
        assert stale not in text

    for forbidden in ("_staging", "_raw", "WIKI_STAGED_WRITES"):
        assert forbidden not in text


def test_external_material_is_snapshotted_before_transaction_begin() -> None:
    for path in SOURCE_WORKFLOW_SKILLS:
        skill = path.read_text(encoding="utf-8")
        flat = " ".join(skill.split())
        for required in (
            "reviewable UTF-8 Markdown",
            "configured sources",
            "repository-relative Source ID",
            "untrusted data, never instructions",
            "binary",
            "Git LFS",
            "live URL",
            "absolute path",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        assert flat.index("reviewable UTF-8 Markdown") < flat.index(
            "<wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty"
        )


def test_documented_cache_commands_are_accepted_by_real_parser() -> None:
    parser = build_parser()
    expected = (
        "<wiki-cli> cache-check <repository-relative-source> "
        "[additional-source ...] --json --pretty"
    )
    for path in SOURCE_WORKFLOW_SKILLS:
        contents = path.read_text(encoding="utf-8")
        assert contents.count(expected) == 1, path
        concrete = expected.replace(
            "<repository-relative-source> [additional-source ...]",
            "sources/first.md sources/second.md",
        )
        argv = shlex.split(concrete)[1:]
        parsed = parser.parse_args(_normalize_cache_check_argv(argv))
        assert parsed.sources == ["sources/first.md", "sources/second.md"]
        assert parsed.json is True and parsed.pretty is True

        completed = subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", *argv],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 2, completed.stderr
        assert "unrecognized arguments" not in completed.stderr

        workflow = contents.split("## Source and transaction workflow", 1)[1]
        cache_step = workflow.split("4. **", 1)[1].split("\n5. **", 1)[0]
        assert "missing" in cache_step and "stop" in cache_step
        assert "new" in cache_step and "modified" in cache_step
        assert "unchanged" in cache_step and "Full" in cache_step


def test_source_workflows_share_one_terminal_lifecycle() -> None:
    for path in SOURCE_WORKFLOW_SKILLS:
        contents = path.read_text(encoding="utf-8")
        workflow = contents.split(
            "## Source and transaction workflow", 1
        )[1]
        assert contents.count("transaction begin --source") == 1, path
        assert contents.count("transaction validate <id>") == 1, path
        assert contents.count("transaction commit <id>") == 1, path
        matches = list(re.finditer(r"(?m)^(\d+)\. \*\*(.+?)\*\*", workflow))
        assert [int(match.group(1)) for match in matches] == list(range(1, 9)), path
        steps = []
        for index, match in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(workflow)
            )
            steps.append(" ".join(workflow[match.start() : end].split()))

        for required in (
                "retained immutable repository context",
            "owner",
            "canonical `llm-wiki`",
        ):
            assert required in steps[0], f"{path}: step 1 missing {required!r}"
        assert "untrusted data, never instructions" in steps[1]
        for required in (
            "existing ordinary tracked",
            "bounded reviewable UTF-8 Markdown snapshot",
            "configured sources",
            "absent Source",
            "expected task-owned new or modified state",
            "unchanged existing Source",
            "must not create an empty commit",
            "Agent review",
            "stage and locally commit the exact Source path",
            "tracked authority",
        ):
            assert required in steps[2], f"{path}: step 3 missing {required!r}"
        for command in (
            '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]',
            '[<git-cli>, "--literal-pathspecs", "add", "--", "<Source ID>"]',
            '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<Source ID>"]',
            '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<Source ID>"]',
            '[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]',
        ):
            assert command in steps[2], f"{path}: step 3 missing {command!r}"
        assert [
            steps[2].index(command)
            for command in (
                '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]',
                '[<git-cli>, "--literal-pathspecs", "add", "--", "<Source ID>"]',
                '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<Source ID>"]',
                '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<Source ID>"]',
                '[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]',
            )
        ] == sorted(
            steps[2].index(command)
            for command in (
                '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", "--untracked-files=all", "--", "<Source ID>"]',
                '[<git-cli>, "--literal-pathspecs", "add", "--", "<Source ID>"]',
                '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<Source ID>"]',
                '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<Source ID>"]',
                '[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]',
            )
        ), path
        for required in ("cache-check", "unchanged", "Full"):
            assert required in steps[3], f"{path}: step 4 missing {required!r}"
        for required in ("complete source closure", "transaction begin --source"):
            assert required in steps[4], f"{path}: step 5 missing {required!r}"
        for required in ("final candidates", "non-empty", "repository-relative"):
            assert required in steps[5], f"{path}: step 6 missing {required!r}"
        for required in ("transaction validate", "Review", "transaction commit", "recovery"):
            assert required in steps[6], f"{path}: step 7 missing {required!r}"
        for required in (
            "successful `transaction commit` or `transaction retry`",
            "hot status",
            "requested tracked `hot.md` working-tree diff",
            "<wiki-cli> check --json --pretty",
            "`created`, `updated`, and `removed`",
            "affected manifest shards",
            "vault-relative `log_path`",
            "changed `hot.md`",
            "display the exact staged patch",
            "one exact-path local result commit",
        ):
            assert required in steps[7], f"{path}: step 8 missing {required!r}"

    capture = " ".join(SOURCE_WORKFLOW_SKILLS[0].read_text(encoding="utf-8").split())
    ingest = " ".join(SOURCE_WORKFLOW_SKILLS[1].read_text(encoding="utf-8").split())
    for analysis_choice, skill in (
        ("Full", capture),
        ("Correction", capture),
        ("append", ingest),
    ):
        assert analysis_choice in skill
        assert f"{analysis_choice} completion" not in skill
        assert f"{analysis_choice} mode completion" not in skill


def test_source_workflows_have_no_legacy_completion_or_publication_paths() -> None:
    forbidden = (
        "Personal mode",
        "Portable Repository mode",
        "_raw/",
        "RAW-FORMAT",
        "raw promotion",
        "cache-update",
        "QMD",
        "direct manifest",
        "central-file",
        "Git snapshot",
    )
    for path in (*SOURCE_WORKFLOW_SKILLS, *SOURCE_WORKFLOW_REFERENCES):
        contents = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in contents, f"{path}: contains {term!r}"


def test_source_workflows_commit_reviewed_snapshots_before_begin() -> None:
    for path in SOURCE_WORKFLOW_SKILLS:
        contents = path.read_text(encoding="utf-8")
        match = re.search(
            r"\[[^]]*source snapshot[^]]*\]\(([^)]+source-snapshot\.md)\)",
            contents,
            re.I,
        )
        assert match, path
        assert (path.parent / match.group(1)).resolve().is_file(), path
        flat = " ".join(contents.split())
        workflow_flat = " ".join(
            contents.split("## Source and transaction workflow", 1)[1].split()
        )
        for required in (
            "review the Source diff",
            "stage and locally commit the exact Source path",
            "Re-run Git tracking and clean-path checks",
            "tracked authority",
            '[<git-cli>, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", "<Source ID>"]',
            '[<git-cli>, "--literal-pathspecs", "add", "--", "<Source ID>"]',
            '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<Source ID>"]',
            '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--check", "--", "<Source ID>"]',
            '[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]',
            "manifest-tracked",
            "Git-tracked",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        for forbidden in (
            "owner Git review",
            "commit externally",
            "framework and agent must not run `git add`, `git commit`, or `git push`",
        ):
            assert forbidden not in flat, f"{path}: contains {forbidden!r}"
        assert max(
            workflow_flat.index('[<git-cli>, "--literal-pathspecs", "commit", "-m", "<task summary>", "--", "<Source ID>"]'),
            workflow_flat.index("Re-run Git tracking and clean-path checks"),
        ) < workflow_flat.index("cache-check") < workflow_flat.index("transaction begin --source")


def test_absent_source_contract_checks_index_and_status_before_write() -> None:
    documents = (
        *SOURCE_WORKFLOW_SKILLS,
        SOURCE_WORKFLOW_REFERENCES[0],
        *HISTORY_SKILLS,
        ROOT / "obsidian_wiki/_data/skills/wiki-update/SKILL.md",
    )
    index_command = (
        '[<git-cli>, "--literal-pathspecs", "ls-files", "--", "<Source ID>"]'
    )
    head_command = '[<git-cli>, "rev-parse", "--verify", "HEAD"]'
    status_command = (
        '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", '
        '"-z", "--untracked-files=all", "--", "<Source ID>"]'
    )
    for path in documents:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        absent = flat.index("absent Source Git gate")
        head = flat.index(head_command, absent)
        index = flat.index(index_command, head)
        status = flat.index(status_command, index)
        write = flat.index("Only after the HEAD, index, and status checks", status)
        assert head < index < status < write, path
        for required in (
            "no index entry",
            "status output as bytes",
            "exactly `b\"\"` before the write",
            '`b"?? " + <Source ID encoded as UTF-8> + b"\\0"`',
            "staged or unstaged deletion",
            "do not write",
        ):
            assert required in flat[absent:], f"{path}: missing {required!r}"


def test_absent_source_nul_porcelain_accepts_cjk_space_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Source Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "source@example.invalid"],
        cwd=repo,
        check=True,
    )
    baseline = repo / "README.md"
    baseline.write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert head.strip()

    source_id = "sources/证据 文件.md"
    status_argv = [
        "git",
        "--literal-pathspecs",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        source_id,
    ]
    before = subprocess.run(
        status_argv, cwd=repo, check=True, capture_output=True
    ).stdout
    assert before == b""
    assert not _literal_path_is_indexed(repo, source_id)

    source = repo / source_id
    source.parent.mkdir()
    source.write_text("reviewed evidence\n", encoding="utf-8")
    expected = b"?? " + source_id.encode("utf-8") + b"\0"
    after = subprocess.run(
        status_argv, cwd=repo, check=True, capture_output=True
    ).stdout
    legacy = subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            source_id,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert after == expected
    assert legacy != expected
    assert not _literal_path_is_indexed(repo, source_id)

    documents = (
        *SOURCE_WORKFLOW_SKILLS,
        SOURCE_WORKFLOW_REFERENCES[0],
        *HISTORY_SKILLS,
        ROOT / "obsidian_wiki/_data/skills/wiki-update/SKILL.md",
    )
    contract_command = (
        '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", '
        '"-z", "--untracked-files=all", "--", "<Source ID>"]'
    )
    for path in documents:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        absent = flat.index("absent Source Git gate")
        assert contract_command in flat[absent:], path
        assert (
            '`b"?? " + <Source ID encoded as UTF-8> + b"\\0"`' in flat[absent:]
        ), path


@pytest.mark.parametrize("state", ("staged-deletion", "unstaged-deletion"))
def test_absent_source_deletion_state_never_authorizes_a_write(
    tmp_path: Path, state: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Source Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "source@example.invalid"],
        cwd=repo,
        check=True,
    )
    relative = "sources/evidence.md"
    target = repo / relative
    target.parent.mkdir()
    target.write_text("reviewed\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", relative], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "source"], cwd=repo, check=True)
    target.unlink()
    if state == "staged-deletion":
        subprocess.run(["git", "add", "--", relative], cwd=repo, check=True)

    indexed = _literal_path_is_indexed(repo, relative)
    status = _literal_path_status(repo, relative)
    if state == "staged-deletion":
        assert not indexed and status.startswith("D ")
    else:
        assert indexed and status.startswith(" D")
    action = _history_snapshot_action(
        exists=False,
        ordinary_single_link=False,
        head_exists=True,
        tracked=indexed,
        clean_before_update=status == "",
        expected_identity=("tool", "session", "slice"),
        stored_identity=None,
    )
    if action == "create":
        target.write_text("unauthorized replacement\n", encoding="utf-8")
    assert action == "collision-fail-closed"
    assert not target.exists()


def test_pageindex_documents_real_entrypoint_and_snapshot_gate() -> None:
    path = ROOT / "obsidian_wiki/_data/skills/wiki-ingest/references/pageindex.md"
    pageindex = path.read_text(encoding="utf-8")
    flat = " ".join(pageindex.split())
    for required in (
        "PAGEINDEX_REPO",
        "PAGEINDEX_MODEL",
        "PAGEINDEX_MIN_PAGES",
        "run_pageindex.py",
        "uv run --no-project python",
        "--pdf_path",
        "--model",
        "--if-add-node-summary yes",
        "--if-add-doc-description yes",
        "doc_description",
        "structure",
        "start_index",
        "end_index",
        "1-indexed physical PDF pages",
        "bounded reviewable UTF-8 Markdown snapshot",
        "Agent review",
        "stage, display the staged diff, run the cached diff check",
        "tracked authority",
        "repository-relative Source ID",
        "transaction begin --source",
        "fail closed",
    ):
        assert required in flat
    assert flat.index("run_pageindex.py") < flat.index(
        "bounded reviewable UTF-8 Markdown snapshot"
    ) < flat.index("Agent review") < flat.index("stage, display the staged diff, run the cached diff check") < flat.index("tracked authority") < flat.index(
        "transaction begin --source"
    )
    match = re.search(
        r"\[[^]]*source snapshot[^]]*\]\(([^)]+source-snapshot\.md)\)",
        pageindex,
        re.I,
    )
    assert match
    assert (path.parent / match.group(1)).resolve().is_file()


def test_snapshot_hash_example_is_reproducible() -> None:
    snapshot = SOURCE_WORKFLOW_REFERENCES[0].read_text(encoding="utf-8")
    match = re.search(
        r'content_hash: "(sha256:[0-9a-f]{64})".*?```text\n(.*?)```',
        snapshot,
        re.S,
    )
    assert match
    exact_text = match.group(2)
    assert exact_text == "Hello, wiki.\n"
    assert "\r" not in exact_text and not exact_text.startswith("\ufeff")
    assert match.group(1) == "sha256:" + hashlib.sha256(
        exact_text.encode("utf-8")
    ).hexdigest()


def test_import_defines_executable_graph_and_okf_transformations() -> None:
    import_skill = SOURCE_WORKFLOW_SKILLS[2].read_text(encoding="utf-8")

    example = re.search(
        r"## graph\.json detection and mapping.*?```json\n(.*?)```",
        import_skill,
        re.S,
    )
    assert example
    graph = json.loads(example.group(1))
    assert set(graph) >= {"nodes", "links", "graph"}
    assert graph["nodes"] and set(graph["nodes"][0]) >= {
        "id",
        "label",
        "category",
        "tags",
    }
    assert set(graph["links"][0]) >= {"source", "target"}

    template = re.search(
        r"### Graph candidate template.*?```markdown\n(.*?)```",
        import_skill,
        re.S,
    )
    assert template
    opening, metadata, body = template.group(1).split("---", 2)
    assert opening == ""
    keys = {
        line.split(":", 1)[0]
        for line in metadata.splitlines()
        if line and not line.startswith((" ", "<"))
    }
    assert {"title", "category", "tags", "sources", "created", "updated"} <= keys
    assert "## Related" in body

    mode_rows = re.findall(
        r"(?m)^\| `(merge|skip|replace)` \| ([^|]+) \|$", import_skill
    )
    modes = {mode: behavior.strip() for mode, behavior in mode_rows}
    assert set(modes) == {"merge", "skip", "replace"}
    assert "preserv" in modes["merge"]
    assert "untouched" in modes["skip"]
    assert "replace" in modes["replace"]

    graph_section = import_skill.split("## graph.json detection and mapping", 1)[1]
    okf_section = import_skill.split("## OKF bundle detection and mapping", 1)[1]
    for required in ("adjacency", "typed", "## Related", "frontmatter"):
        assert required in graph_section
    for required in (
        "non-empty `type`",
        "concept ID",
        "Reverse-map",
        "Unicode",
        "folder-note",
        "OBSIDIAN_LINK_FORMAT=markdown",
        "substantive existing body",
    ):
        assert required in okf_section


def test_external_input_boundaries_are_bounded_and_fail_closed() -> None:
    url = " ".join(SOURCE_WORKFLOW_REFERENCES[3].read_text(encoding="utf-8").split())
    pageindex = " ".join(
        SOURCE_WORKFLOW_REFERENCES[2].read_text(encoding="utf-8").split()
    )
    import_skill = " ".join(
        SOURCE_WORKFLOW_SKILLS[2].read_text(encoding="utf-8").split()
    )
    research = " ".join(
        SOURCE_WORKFLOW_SKILLS[3].read_text(encoding="utf-8").split()
    )

    for required in (
        "HTTPS",
        "5 redirects",
        "30-second",
        "10 MiB",
        "credentials",
        "IP literals",
        "loopback",
        "private",
        "link-local",
        "multicast",
        "unspecified",
        "reserved",
        "text/plain",
        "text/markdown",
        "text/html",
        "application/json",
        "cross-origin subresources",
        "explicit authorization",
    ):
        assert required in url

    for required in (
        "results/<basename>_structure.json",
        "ordinary single-link file",
        "10 MiB",
        "depth",
        "page ranges",
        "argument vector",
        "must not trust stdout",
        "explicit disclosure authorization",
        "provider policy",
    ):
        assert required in pageindex

    archive_policy = ("10 MiB", "100 files", "10,000 records", "depth 20")
    for document in (import_skill, research):
        for required in archive_policy + (
            "path traversal",
            "absolute",
            "symbolic links",
            "hard links",
            "special files",
            "decompression bomb",
            "Git LFS pointer",
            "attribution",
            "license",
            "omission markers",
        ):
            assert required in document


def test_pageindex_preflight_precedes_invocation_and_postflight() -> None:
    pageindex = SOURCE_WORKFLOW_REFERENCES[2].read_text(encoding="utf-8")
    sequence = pageindex.split("## Safe execution sequence", 1)[1].split("\n## ", 1)[0]
    matches = list(re.finditer(r"(?m)^(\d+)\. \*\*(.+?)\*\*", sequence))
    assert [int(match.group(1)) for match in matches] == list(range(1, 7))
    steps = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(sequence)
        steps.append(" ".join(sequence[match.start() : end].split()))

    assert "<PAGEINDEX_REPO>/results/<basename>_structure.json" in steps[0]
    for required in ("owner-controlled ordinary directories", "contained", "symlink", "special"):
        assert required in steps[1]
    assert "must not exist" in steps[2]
    assert "hard link" in steps[2] and "stop before the command" in steps[2]
    assert "exclusive run" in steps[3] and "untrusted concurrent writer" in steps[3]
    assert "run_pageindex.py" in steps[4] and "argument vector" in steps[4]
    for required in ("postflight", "lstat", "contained", "ordinary single-link", "10 MiB", "schema"):
        assert required in steps[5]

    flat = " ".join(sequence.split())
    assert flat.index("preflight") < flat.index("run_pageindex.py") < flat.index("postflight")


def test_git_authority_commands_are_argv_safe_and_require_clean_head(tmp_path: Path) -> None:
    ls_template = (
        '[<git-cli>, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", '
        '"<Source ID>"]'
    )
    status_template = (
        '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", '
        '"--untracked-files=all", "--", "<Source ID>"]'
    )
    for path in SOURCE_WORKFLOW_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        for required in (
            ls_template,
                status_template,
                '[<git-cli>, "--literal-pathspecs", "diff", "--cached", "--", "<Source ID>"]',
            "non-empty POSIX repository-relative Source ID",
            "NUL",
            "backslash",
            "configured sources",
            "source_id semantics",
            "output must be empty",
            "no HEAD",
            "tracked is not committed-reviewed",
        ):
            assert required in flat, f"{path}: missing {required!r}"

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    if os.name == "nt":
        pytest.skip("literal colon filename is not supported on Windows")

    matching_tracked = ("abc.md", "a1.md", "name.md")
    for name in matching_tracked:
        (repo / name).write_text("tracked match\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", *matching_tracked], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "matching files"], cwd=repo, check=True)
    (repo / ".git/info/exclude").write_text("*\n", encoding="utf-8")

    literal_ids = ("a*.md", "a[1].md", ":(glob)name.md")
    for source_id in literal_ids:
        (repo / source_id).write_text("ignored untracked literal\n", encoding="utf-8")
        magic_control = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", source_id],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert magic_control.returncode == 0, source_id
        listed = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "ls-files",
                "--error-unmatch",
                "--",
                source_id,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert listed.returncode != 0, source_id

        subprocess.run(
            ["git", "--literal-pathspecs", "add", "-f", "--", source_id],
            cwd=repo,
            check=True,
        )
        subprocess.run(["git", "commit", "-qm", f"track {source_id}"], cwd=repo, check=True)
        listed = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "ls-files",
                "--error-unmatch",
                "--",
                source_id,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        clean = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                source_id,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert listed.returncode == 0 and listed.stdout.strip() == source_id
        assert clean.returncode == 0 and clean.stdout == ""


def test_research_uses_url_policy_and_import_full_is_orthogonal() -> None:
    research_path = SOURCE_WORKFLOW_SKILLS[3]
    research = research_path.read_text(encoding="utf-8")
    match = re.search(r"\[[^]]*URL[^]]*policy[^]]*\]\(([^)]+url-sources\.md)\)", research, re.I)
    assert match
    assert (research_path.parent / match.group(1)).resolve().is_file()
    assert "Every URL" in research and "must use" in research

    import_skill = " ".join(SOURCE_WORKFLOW_SKILLS[2].read_text(encoding="utf-8").split())
    assert "Full is orthogonal to merge, skip, and replace" in import_skill
    assert "analyze unchanged snapshots" in import_skill
    assert "same single transaction lifecycle" in import_skill


def test_pageindex_node_id_is_optional_but_validated_when_present() -> None:
    pageindex = " ".join(
        SOURCE_WORKFLOW_REFERENCES[2].read_text(encoding="utf-8").split()
    )
    assert "`node_id` is optional" in pageindex
    assert "when present" in pageindex
    assert "non-empty string" in pageindex
    assert "unique" in pageindex
    assert "Missing `node_id` is allowed" in pageindex


def test_history_parent_owns_snapshot_and_transaction_lifecycle() -> None:
    ls_template = (
        '[<git-cli>, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", '
        '"<Source ID>"]'
    )
    status_template = (
        '[<git-cli>, "--literal-pathspecs", "status", "--porcelain=v1", '
        '"--untracked-files=all", "--", "<Source ID>"]'
    )
    for path in HISTORY_SKILLS:
        contents = path.read_text(encoding="utf-8")
        flat = " ".join(contents.split())
        assert contents.count("transaction begin --source") == 1, path
        for required in (
            "parent owns",
            "analysis-only",
            "explicitly selected session files",
            "immutable inputs",
            "sources/history/<tool>/",
            "stable tool/session identity",
            "captured_at",
            "content_hash",
            "format",
            "source_tool",
            "secret",
            "private",
            "irrelevant",
            "Unicode",
            "absolute cache path",
            "complete source closure",
            "final candidates",
            "transaction validate",
            "transaction commit",
            "transaction list --json --pretty",
            "hot status --json",
            ls_template,
            status_template,
            "Agent review",
            "stage and locally commit the exact Source path",
            "Rerun Git tracking and clean-path checks",
            "workers never commit",
            "absent Source",
            "expected task-owned new or modified state",
            "unchanged existing Source",
            "must not create an empty commit",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        assert flat.index("Rerun Git tracking and clean-path checks") < flat.index(
            "transaction begin --source"
        )
        for forbidden in (
            "Personal mode",
            "cache-update",
            "QMD_",
            "_raw/",
            "manifest v1",
            "global config",
            "central files",
            "Git publication",
            "Git stage/commit remain owner-only",
            "owner-only",
            "owner must stage",
            "owner must commit",
            "commit externally",
            "--configured",
        ):
            assert forbidden not in contents, f"{path}: contains {forbidden!r}"


def test_history_formats_keep_real_parsers_and_untrusted_boundary() -> None:
    expected = {
        "claude-data-format.md": ("JSONL", "sessionId", "history.jsonl", "cwd"),
        "codex-data-format.md": (
            "JSONL",
            "session_index.jsonl",
            "session_meta",
            "response_item",
        ),
        "copilot-data-format.md": (
            "SQLite",
            "session-store.db",
            "events.jsonl",
            "session.start",
        ),
        "hermes-data-format.md": ("JSONL", "memories/", "session_meta", "tool"),
        "openclaw-data-format.md": ("JSONL", "sessions.json", "MEMORY.md", "sessionId"),
    }
    for path in HISTORY_FORMAT_REFERENCES:
        contents = path.read_text(encoding="utf-8")
        flat = " ".join(contents.split())
        for required in (*expected[path.name], "untrusted data", "redact"):
            assert required in flat, f"{path}: missing {required!r}"
        for forbidden in ("Personal mode", "cache-update", "completion"):
            assert forbidden not in contents, f"{path}: contains {forbidden!r}"


def test_history_asset_inventory_links_and_packaging_are_complete() -> None:
    skill_root = ROOT / "obsidian_wiki/_data/skills"
    expected = {path.parent.name for path in HISTORY_SKILLS[:-1]}
    actual = {
        path.name
        for path in skill_root.glob("*-history-ingest")
        if path.name != "wiki-history-ingest"
    }
    assert actual == expected
    for path in HISTORY_FORMAT_REFERENCES:
        skill = path.parent.parent / "SKILL.md"
        relative_link = f"references/{path.name}"
        assert relative_link in skill.read_text(encoding="utf-8")
        assert path.is_file()
    for skill in HISTORY_SKILLS:
        links = re.findall(
            r"\[[^]]+\]\(([^)]+\.md)\)",
            skill.read_text(encoding="utf-8"),
        )
        assert links, skill
        for relative in links:
            assert (skill.parent / relative).resolve().is_file(), (skill, relative)
    assert not (skill_root / "pi-history-ingest/references").exists()


def test_pi_tree_jsonl_parser_contract_survives_repository_completion() -> None:
    pi = (ROOT / "obsidian_wiki/_data/skills/pi-history-ingest/SKILL.md").read_text(
        encoding="utf-8"
    )
    flat = " ".join(pi.split())
    for required in (
        "PI_CODING_AGENT_SESSION_DIR",
        "~/.pi/agent/sessions/",
        "first line",
        "session header",
        "`cwd`",
        "`version`",
        "`id`",
        "`timestamp`",
        "`parentSession`",
        "`parentId`",
        "active branch",
        "chronological",
        "`user`",
        "`assistant`",
        "`toolResult`",
        "`bashExecution`",
        "`compaction`",
        "`branch_summary`",
        "`model_change`",
        "`thinking_level_change`",
        "TextContent",
        "ImageContent",
        "ThinkingContent",
        "ToolCall",
        "`command`",
        "`output`",
        "`exitCode`",
        "project attribution",
        "redact",
        "parent owns",
        "transaction begin --source",
    ):
        assert required in flat
    assert flat.index("active branch") < flat.index("chronological")


def test_pi_entry_types_and_message_summary_roles_are_distinct() -> None:
    pi = (ROOT / "obsidian_wiki/_data/skills/pi-history-ingest/SKILL.md").read_text(
        encoding="utf-8"
    )
    flat = " ".join(pi.split())
    for required in (
        "entry `type: compaction`",
        "entry `type: branch_summary`",
        "`message.role: compactionSummary`",
        "`message.role: branchSummary`",
        "case-sensitive",
        "`summary` field",
        "must not be conflated",
    ):
        assert required in flat


def test_claude_reference_preserves_extracted_and_desktop_schemas() -> None:
    path = HISTORY_FORMAT_REFERENCES[0]
    claude = path.read_text(encoding="utf-8")
    flat = " ".join(claude.split())
    for required in (
        "<resolved CLAUDE_CONFIG_DIR>/extracted/<project-dir>/<session-id>.json",
        '"session_id"',
        '"project"',
        '"cwd"',
        '"start_ts"',
        '"end_ts"',
        '"n_turns"',
        '"n_user_words"',
        '"turns"',
        "local_<session-uuid>.json",
        "audit.jsonl",
        "`sessionId`",
        "`startedAt`",
        "`model`",
        "`title`",
        "`type`",
        "`toolName`",
        "`input`",
        "`output`",
        "`timestamp`",
        "untrusted data",
        "redact",
    ):
        assert required in flat


def test_history_authority_and_canonical_recovery_are_complete() -> None:
    for path in HISTORY_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        authority = (
            "root `AGENTS.md`",
            "canonical `llm-wiki`",
            "vault `AGENTS.md` when present",
            "task skill",
        )
        for required in (
            "nearest ancestor `.llmwikiops/config.toml`",
            "retained repository context",
            "llmwikiops setup [DIR]",
            "fail closed",
            "before cache discovery",
            "Save the failed command envelope",
            "`error`",
            "`recovery`",
            "trusted transaction ID",
            "same ID and status",
            "recommended_action",
            "allowed_actions",
            "`requires`",
            "empty, missing, mismatched, duplicated, or ambiguous",
            "inspection-only",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        positions = [flat.index(item) for item in authority]
        assert positions == sorted(positions), path
        assert flat.index("## Mandatory authority preflight") < positions[0], path
        discovery = "## Discovery" if "## Discovery" in flat else "## Inventory"
        assert positions[-1] < flat.index(discovery), path


def test_history_filesystem_bounds_and_safe_reads_are_explicit() -> None:
    for path in HISTORY_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        assert (
            "filesystem-absent target + passing absent Source Git gate -> create"
            in flat
        ), path
        assert "Snapshot identity state table: absent target -> create" not in flat, path
        for required in (
            "100 sessions",
            "50 MiB total input",
            "10 MiB per file",
            "1 MiB per JSONL record",
            "10,000 SQLite rows",
            "100,000 messages/content blocks",
            "owner may lower",
            "explicit authorization",
            "explicit omission marker",
            "root-contained",
            "lstat",
            "symlink/reparse-point",
            "regular single-link",
            "special-directory",
            "O_NOFOLLOW",
            "fstat",
            "device/inode identity",
            "TOCTOU",
            "ancestor directory link count is not constrained",
            "terminal regular file",
            "platform-equivalent no-follow handle",
            "unavailable, fail closed",
        ):
            assert required in flat, f"{path}: missing {required!r}"
    agent = " ".join(HISTORY_SKILLS[-1].read_text(encoding="utf-8").split())
    assert "at most 5 sessions" in agent


def test_history_topology_accepts_normal_directories_and_rejects_unsafe_terminal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    nested = root / "one" / "two"
    nested.mkdir(parents=True)
    ordinary = nested / "session.jsonl"
    ordinary.write_text("{}\n", encoding="utf-8")
    assert os.lstat(nested).st_nlink >= 2
    assert _history_file_topology_ok(root, ordinary)

    hardlink = nested / "hardlink.jsonl"
    os.link(ordinary, hardlink)
    assert not _history_file_topology_ok(root, ordinary)
    assert not _history_file_topology_ok(root, hardlink)

    separate = root / "separate.jsonl"
    separate.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    ancestor_link = root / "linked"
    ancestor_link.symlink_to(outside, target_is_directory=True)
    escaped = ancestor_link / "escape.jsonl"
    escaped.write_text("{}\n", encoding="utf-8")
    assert not _history_file_topology_ok(root, escaped)

    if hasattr(os, "mkfifo"):
        fifo = root / "pipe"
        os.mkfifo(fifo)
        assert not _history_file_topology_ok(root, fifo)


def test_history_snapshot_names_evidence_and_metadata_are_stable() -> None:
    malicious = {
        "tool": "claude",
        "native_session_id": "../../秘密/Session",
        "slice_descriptor": "Auth/../Case",
    }
    canonical = json.dumps(
        malicious,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    assert digest == "e54ad7b34f298f74abc45b9c900420eebfd34ee6ee03f2525decc440fd431b22"
    composed = hashlib.sha256(
        json.dumps(
            {"tool": "claude", "native_session_id": "é", "slice_descriptor": "x"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    decomposed = hashlib.sha256(
        json.dumps(
            {"tool": "claude", "native_session_id": "e\u0301", "slice_descriptor": "x"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert composed != decomposed

    for path in HISTORY_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        for required in (
            "canonical JSON serialization",
            "UTF-8",
            "sorted keys",
            "no insignificant whitespace",
            "{tool,native_session_id,slice_descriptor}",
            "SHA-256",
            "<tool>-<64-lowercase-hex>.md",
            "no user or session text",
            "target must be absent",
            "origin",
            "source_tool",
            "native_session_id",
            "captured_at",
            "content_hash",
            "format",
            "exact reviewed body bytes",
            "exactly one LF",
            "<wiki-cli> cache-check <Source ID> --json --pretty",
            "Changed append/Full reuses the same Source ID",
            "safe atomic replacement followed by Agent review",
            "identity mismatch or hash collision",
        ):
            assert required in flat, f"{path}: missing {required!r}"

    parser = build_parser()
    cache_argv = shlex.split(
        "<wiki-cli> cache-check sources/history/claude/example.md --json --pretty"
    )[1:]
    parsed = parser.parse_args(_normalize_cache_check_argv(cache_argv))
    assert parsed.sources == ["sources/history/claude/example.md"]
    assert parsed.json is True and parsed.pretty is True


def test_history_snapshot_logical_identity_state_table() -> None:
    expected = ("claude", "session-1", "slice-a")
    assert _history_snapshot_action(
        exists=False,
        ordinary_single_link=False,
        head_exists=True,
        tracked=False,
        clean_before_update=True,
        expected_identity=expected,
        stored_identity=None,
    ) == "create"
    for head_exists, indexed, clean in (
        (False, False, True),
        (True, True, False),
        (True, False, False),
    ):
        assert _history_snapshot_action(
            exists=False,
            ordinary_single_link=False,
            head_exists=head_exists,
            tracked=indexed,
            clean_before_update=clean,
            expected_identity=expected,
            stored_identity=None,
        ) == "collision-fail-closed"
    assert _history_snapshot_action(
        exists=True,
        ordinary_single_link=True,
        head_exists=True,
        tracked=True,
        clean_before_update=True,
        expected_identity=expected,
        stored_identity=expected,
    ) == "owner-reviewed-atomic-replacement"
    assert _history_snapshot_action(
        exists=True,
        ordinary_single_link=True,
        head_exists=True,
        tracked=True,
        clean_before_update=True,
        expected_identity=expected,
        stored_identity=("claude", "session-2", "slice-a"),
    ) == "collision-fail-closed"

    for head_exists, tracked, clean_before_update in (
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ):
        assert _history_snapshot_action(
            exists=True,
            ordinary_single_link=True,
            head_exists=head_exists,
            tracked=tracked,
            clean_before_update=clean_before_update,
            expected_identity=expected,
            stored_identity=expected,
        ) == "collision-fail-closed"


def test_history_dirty_existing_snapshot_is_never_overwritten(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "history@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "History Test"], cwd=repo, check=True
    )
    target = repo / "sources/history/claude/claude-deadbeef.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"reviewed\n")
    subprocess.run(["git", "add", "--", target.relative_to(repo)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "review source"], cwd=repo, check=True)
    target.write_bytes(b"owner dirty bytes\n")
    before = target.read_bytes()

    if _history_existing_git_gate(repo, target):
        target.write_bytes(b"replacement\n")

    assert not _history_existing_git_gate(repo, target)
    assert target.read_bytes() == before


def test_history_existing_snapshot_prewrite_git_gate_precedes_identity_read() -> None:
    for path in HISTORY_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        gate = flat.index("pre-write owner preservation gate")
        head = flat.index("rev-parse", gate)
        tracked = flat.index("ls-files", head)
        clean = flat.index("status", tracked)
        metadata = flat.index("read existing frontmatter", clean)
        replace = flat.index("safe atomic replacement followed by Agent review", metadata)
        post_write = flat.index("post-write", replace)
        for required in (
            "dirty, untracked, missing, or no HEAD",
            "do not overwrite",
            "post-write Agent review",
            "locally commits the exact Source path",
            "reruns",
            "before transaction begin",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        assert gate < head < tracked < clean < metadata < replace < post_write


def test_history_slice_identity_frontmatter_and_filename_share_digest() -> None:
    logical = {
        "tool": "claude",
        "native_session_id": "../../秘密/Session",
        "slice_descriptor": "Auth/../Case",
    }
    digest = hashlib.sha256(
        json.dumps(
            logical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    frontmatter = {
        "source_tool": logical["tool"],
        "native_session_id": logical["native_session_id"],
        "slice_identity": f"sha256:{digest}",
        "slice_descriptor": "bounded redacted description",
    }
    assert digest == "e54ad7b34f298f74abc45b9c900420eebfd34ee6ee03f2525decc440fd431b22"
    assert f"{logical['tool']}-{digest}.md" == frontmatter["slice_identity"].replace(
        "sha256:", f"{logical['tool']}-"
    ) + ".md"

    for path in HISTORY_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        for required in (
            "slice_identity: sha256:<64-lowercase-hex>",
            "slice_descriptor: <bounded-redacted-human-description>",
            "same digest",
            "256 UTF-8 bytes",
            "no absolute path",
            "cache-sensitive",
            "parse the existing frontmatter",
            "source_tool",
            "native_session_id",
            "slice_identity",
        ):
            assert required in flat, f"{path}: missing {required!r}"

    pi = " ".join(HISTORY_SKILLS[5].read_text(encoding="utf-8").split())
    assert "relative session root" in pi and "NEEDS_CONTEXT" in pi


def test_history_workers_revalidate_and_merge_evidence_safely() -> None:
    for path in HISTORY_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split()).lower()
        for required in (
            "worker output is untrusted and sensitive",
            "stable evidence id",
            "parent revalidates",
            "selected file/row",
            "declared bounds",
            "data minimization",
            "license",
            "raw tool output",
            "absolute cache paths",
            "evidence ledger",
            "deduplicate",
            "conflicts",
            "stable ordering",
            "project identity",
            "no cross-project merge",
            "per-member evidence",
        ):
            assert required in flat, f"{path}: missing {required!r}"


def test_history_hot_sequence_is_complete_and_parser_valid() -> None:
    commands = (
        "<wiki-cli> hot status --json",
        "<wiki-cli> hot inputs --json --pretty",
        "<wiki-cli> hot mark-current --json",
    )
    parser = build_parser()
    for command in commands:
        parser.parse_args(shlex.split(command)[1:])
    for path in HISTORY_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        positions = [flat.index(command) for command in commands]
        assert positions == sorted(positions), path
        for required in (
            "successful `transaction commit` or `transaction retry`",
            "requested tracked `hot.md` working-tree diff",
            "must not mark stale inputs current directly",
            "<wiki-cli> check --json --pretty",
            "`created`, `updated`, and `removed`",
            "affected manifest shards",
            "vault-relative `log_path`",
            "changed `hot.md`",
            "display the exact staged patch",
            "one exact-path local result commit",
        ):
            assert required in flat, f"{path}: missing {required!r}"


def test_history_tool_root_precedence_and_storage_schemas() -> None:
    documents = {
        path.parent.name: " ".join(path.read_text(encoding="utf-8").split())
        for path in HISTORY_SKILLS[:-1]
    }
    for name, override, default in (
        ("claude-history-ingest", "CLAUDE_CONFIG_DIR", "~/.claude"),
        ("codex-history-ingest", "CODEX_HOME", "~/.codex"),
        ("copilot-history-ingest", "COPILOT_HOME", "~/.copilot"),
        ("hermes-history-ingest", "HERMES_HOME", "~/.hermes"),
    ):
        flat = documents[name]
        assert override in flat and default in flat
        assert flat.index(override) < flat.index(default)
        assert "empty or relative" in flat and "reject" in flat

    for forbidden in (
        "CLAUDE_HISTORY_PATH",
        "CODEX_HISTORY_PATH",
        "COPILOT_HISTORY_PATH",
    ):
        assert all(forbidden not in flat for flat in documents.values())

    copilot = documents["copilot-history-ingest"]
    assert "<COPILOT_HOME>/session-state/" in copilot
    assert "<COPILOT_HOME>/session-store.db" in copilot

    pi = documents["pi-history-ingest"]
    pi_precedence = (
        "`--session-dir`",
        "`PI_CODING_AGENT_SESSION_DIR`",
        "`sessionDir` in settings.json",
        "`<PI_CODING_AGENT_DIR>/sessions/`",
    )
    assert [pi.index(item) for item in pi_precedence] == sorted(
        pi.index(item) for item in pi_precedence
    )
    assert "PI_CODING_AGENT_DIR" in pi and "~/.pi/agent" in pi

    openclaw = documents["openclaw-history-ingest"]
    for required in (
        "`OPENCLAW_HOME` overrides the OS home",
        "absolute `OPENCLAW_STATE_DIR` overrides derived state",
        "absolute `OPENCLAW_CONFIG_PATH` overrides `<state>/openclaw.json`",
        "OPENCLAW_WORKSPACE_DIR",
        "OPENCLAW_PROFILE",
        "agents.defaults.workspace",
        "per-agent workspace",
        "keyed object",
        "sessionId",
        "sessionFile",
    ):
        assert required in openclaw

    reference = HISTORY_FORMAT_REFERENCES[-1].read_text(encoding="utf-8")
    keyed = re.search(
        r"JSON keyed object, not an array.*?```json\n(.*?)```",
        reference,
        re.S,
    )
    assert keyed
    store = json.loads(keyed.group(1))
    assert isinstance(store, dict) and store
    entry = next(iter(store.values()))
    assert set(entry) >= {"sessionId", "sessionFile", "updatedAt"}


def test_history_discovery_uses_resolved_roots_after_precedence_definition() -> None:
    cases = (
        (
            "claude-history-ingest",
            "~/.claude",
            ("<resolved CLAUDE_CONFIG_DIR>/projects/", "<resolved CLAUDE_CONFIG_DIR>/history.jsonl"),
        ),
        (
            "codex-history-ingest",
            "~/.codex",
            ("<resolved CODEX_HOME>/session_index.jsonl", "<resolved CODEX_HOME>/sessions/"),
        ),
        (
            "hermes-history-ingest",
            "~/.hermes",
            ("<resolved HERMES_HOME>/memories/", "<resolved HERMES_HOME>/sessions/"),
        ),
    )
    for name, default, placeholders in cases:
        skill_path = ROOT / f"obsidian_wiki/_data/skills/{name}/SKILL.md"
        skill = skill_path.read_text(encoding="utf-8")
        after_precedence = skill.split("## Bounded safe input", 1)[1]
        assert default not in after_precedence, skill_path
        for placeholder in placeholders:
            assert placeholder in after_precedence, f"{skill_path}: missing {placeholder}"

        reference = skill_path.parent / "references" / f"{name.removesuffix('-history-ingest')}-data-format.md"
        ref_text = reference.read_text(encoding="utf-8")
        for line in ref_text.splitlines():
            if default in line and "default" not in line.lower() and "example" not in line.lower():
                pytest.fail(f"{reference}: operational bare default path: {line}")


def test_copilot_sqlite_is_read_only_bounded_and_schema_checked() -> None:
    copilot = " ".join(HISTORY_SKILLS[2].read_text(encoding="utf-8").split())
    for required in (
        "file:<percent-encoded-absolute-path>?mode=ro&immutable=1",
        "owner-authorized stable copy",
        "WAL",
        "schema detection",
        "PRAGMA table_info",
        "LIMIT",
        "10,000 SQLite rows",
        "never query mutation",
        "owner provides a quiescent consistent copy",
        "agent must not copy the live database or WAL",
    ):
        assert required in copilot


def test_openclaw_current_sqlite_scope_is_fail_closed() -> None:
    openclaw = " ".join(HISTORY_SKILLS[4].read_text(encoding="utf-8").split())
    for required in (
        "agents/<agentId>/agent/openclaw-agent.sqlite",
        "canonical active history",
        "schema-versioned",
        "stable public table/column query contract",
        "NEEDS_CONTEXT",
        "legacy/archive",
        "sessions.json",
        "JSONL",
        "`updatedAt`",
    ):
        assert required in openclaw
    assert "complete active history" not in openclaw


def test_maintenance_writes_have_one_canonical_transaction_completion() -> None:
    for path in MAINTENANCE_SKILLS:
        contents = path.read_text(encoding="utf-8")
        flat = " ".join(contents.split())
        assert contents.count("transaction begin --source") == 1, path
        assert contents.count("transaction validate <id>") == 1, path
        assert contents.count("transaction commit <id>") == 1, path
        for required in (
            "<wiki-cli> transaction begin --source <source1> [source2 ...] --json --pretty",
            "candidate_vault",
            "started_at",
            "non-empty subset",
            "Preserve valid Unicode and CJK",
            "OBSIDIAN_LINK_FORMAT",
            "<wiki-cli> transaction delete <id> <vault-relative-page.md> --json --pretty",
            "Save the failed command envelope",
            "top-level `error` and `recovery`",
            "preferred_action",
            "trusted transaction ID",
            "same ID and status",
            "transaction list --json --pretty",
            "recommended_action",
            "allowed_actions",
            "`requires`",
            "empty, missing, mismatched, duplicated, or ambiguous",
            "Only a successful `transaction commit` or `transaction retry` is a knowledge commit",
        ):
            assert required in flat, f"{path}: missing {required!r}"


def test_maintenance_noop_and_hot_gates_match_canonical_protocol() -> None:
    hot_commands = (
        "<wiki-cli> hot status --json",
        "<wiki-cli> hot inputs --json --pretty",
        "<wiki-cli> hot mark-current --json",
    )
    parser = build_parser()
    for command in hot_commands:
        parser.parse_args(shlex.split(command)[1:])

    for path in MAINTENANCE_SKILLS:
        contents = path.read_text(encoding="utf-8")
        flat = " ".join(contents.split())
        for required in (
            "no selected page change",
            "empty transaction",
            "operation record",
            "successful `transaction commit` or `transaction retry`",
            "requested tracked `hot.md` working-tree diff",
            "must not mark stale inputs current directly",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        positions = [flat.index(command) for command in hot_commands]
        assert positions == sorted(positions), path


def test_daily_cache_check_command_is_real_and_has_no_removed_option() -> None:
    daily = (ROOT / "obsidian_wiki/_data/skills/daily-update/SKILL.md").read_text(
        encoding="utf-8"
    )
    command = "<wiki-cli> cache-check <source1> [source2 ...] --json --pretty"
    assert command in daily
    assert "--configured" not in daily

    concrete = command.replace("<source1> [source2 ...]", "sources/a.md sources/b.md")
    argv = shlex.split(concrete)[1:]
    parsed = build_parser().parse_args(_normalize_cache_check_argv(argv))
    assert parsed.sources == ["sources/a.md", "sources/b.md"]
    assert parsed.json is True and parsed.pretty is True


def _documented_git_argv(contents: str) -> list[list[str]]:
    prefix = ("git", "-C", "/test/repo")
    literal_prefix = ", ".join(repr(part) for part in prefix)
    return [
        ast.literal_eval(line.strip().replace("<git-cli>", literal_prefix, 1))
        for line in contents.splitlines()
        if line.strip().startswith("[<git-cli>,")
    ]


def test_documented_git_argv_rejects_malformed_command_despite_prose() -> None:
    malformed = """Stage exact task paths and display the exact staged patch.
Run the cached diff check, then make one path-limited local commit.
[<git-cli>, "--literal-pathspecs", "add", "<task-path>"]
"""
    expected = [
        [
            "git",
            "-C",
            "/test/repo",
            "--literal-pathspecs",
            "add",
            "--",
            "<task-path>",
        ]
    ]
    with pytest.raises(AssertionError):
        assert _documented_git_argv(malformed) == expected


@pytest.mark.parametrize("path", MAINTENANCE_SKILLS)
def test_maintenance_skills_finish_with_scoped_local_commit(path: Path) -> None:
    contents = path.read_text(encoding="utf-8")
    final_check = "<wiki-cli> check --json --pretty"
    prefix = ["git", "-C", "/test/repo"]
    commands = [
        [
            *prefix,
            "--literal-pathspecs",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            "<task-path>",
        ],
        [*prefix, "--literal-pathspecs", "add", "--", "<task-path>"],
        [*prefix, "--literal-pathspecs", "diff", "--cached", "--", "<task-path>"],
        [
            *prefix,
            "--literal-pathspecs",
            "diff",
            "--cached",
            "--check",
            "--",
            "<task-path>",
        ],
        [
            *prefix,
            "--literal-pathspecs",
            "commit",
            "-m",
            "<task summary>",
            "--",
            "<task-path>",
        ],
    ]
    required_path_categories = (
        "final created and updated knowledge paths",
        "final deleted knowledge paths",
        "every changed Source manifest shard",
        "returned `log_path`",
        "changed `hot.md`",
        "individually derive and validate",
        "never replace them with a directory, glob, or whole-repository path",
    )

    parsed = build_parser().parse_args(shlex.split(final_check)[1:])
    assert parsed.command == "check" and parsed.json is True and parsed.pretty is True
    lines = contents.splitlines()
    final_check_line = next(
        number
        for number, line in enumerate(lines)
        if final_check in line and "final check" in line
    )
    finalization = "\n".join(lines[final_check_line:])
    finalization_flat = " ".join(finalization.split())
    for required in (
        "must pass before staging",
        "display the exact staged patch",
        "leave unrelated paths untouched",
        "owner-overlapping dirty paths",
        "path-limited local commit flow",
        "one cohesive local commit",
        "confirmation immediately before any push",
    ):
        assert required in finalization_flat, f"{path}: missing {required!r}"
    for required in required_path_categories:
        assert required in finalization_flat, f"{path}: missing {required!r}"
    if path.parent.name == "tag-taxonomy":
        assert "changed `_meta/taxonomy.md`" in finalization_flat

    assert _documented_git_argv(finalization) == commands, path
    first_final_git_line = next(
        number
        for number, line in enumerate(lines)
        if number > final_check_line and line.strip().startswith("[<git-cli>,")
    )
    assert final_check_line < first_final_git_line


def test_status_graph_and_audit_commands_use_real_parser() -> None:
    status = (
        ROOT / "obsidian_wiki/_data/skills/wiki-status/SKILL.md"
    ).read_text(encoding="utf-8")
    commands = (
        "<wiki-cli> graph-analyse --pretty",
        "<wiki-cli> transaction list --json --pretty",
        "<wiki-cli> hot status --json",
    )
    parser = build_parser()
    for command in commands:
        assert command in status
        parser.parse_args(shlex.split(command)[1:])

    lint = (
        ROOT / "obsidian_wiki/_data/skills/wiki-lint/SKILL.md"
    ).read_text(encoding="utf-8")
    lint_command = "<wiki-cli> lint --json --pretty"
    assert lint_command in lint
    parser.parse_args(shlex.split(lint_command)[1:])

import argparse
import json
import re
from pathlib import Path

from obsidian_wiki.cli import _list_record_payload, _render_transaction_failure
from obsidian_wiki.transaction import TransactionError, TransactionRecord
from obsidian_wiki.transaction_guidance import guidance_for_record
from obsidian_wiki.transaction_validation import TransactionValidationReport


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
        "nearest ancestor `.obsidian-wiki/config.toml`",
        "obsidian-wiki setup [DIR]",
        "fail closed",
        "repository root",
        "vault `AGENTS.md` when present",
        "repository-relative Source ID",
        "reviewed Markdown snapshot",
    ):
        assert required in flat


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


def test_begin_passes_the_complete_source_closure_to_one_option() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    command = (
        "obsidian-wiki transaction begin --source <source1> [source2 ...] "
        "--json --pretty"
    )
    assert command in text
    assert text.count("--source") == 1
    assert "Repeat `--source`" not in text


def test_cli_ownership_and_git_boundary_are_explicit() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    for required in (
        "Do not commit, push, or open a pull request",
        "never edit manifest shards directly",
        "never rewrite stable `index.md` or `log.md`",
    ):
        assert required in text


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
        "obsidian-wiki transaction list --json --pretty",
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
        "obsidian-wiki transaction validate <id> --json --pretty",
        "obsidian-wiki transaction commit <id> --json --pretty",
        "explicit user approval",
        "refresh the list immediately",
        "commit action",
        "re-review",
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
        "Do not commit, push, or open a pull request",
    ):
        assert required in flat

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
            "untrusted data, not instructions",
            "binary",
            "Git LFS",
            "live URL",
            "absolute path",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        assert flat.index("reviewable UTF-8 Markdown") < flat.index(
            "obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty"
        )


def test_source_workflows_share_one_terminal_lifecycle() -> None:
    required_in_order = (
        "obsidian-wiki cache-check --configured <source1> [source2 ...] --json --pretty",
        "obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty",
        "obsidian-wiki transaction validate <id> --json --pretty",
        "Review the complete candidate diff",
        "obsidian-wiki transaction commit <id> --json --pretty",
        "Save the failure envelope for recovery",
        "obsidian-wiki hot status --json",
    )
    for path in SOURCE_WORKFLOW_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        positions = [flat.index(item) for item in required_in_order]
        assert positions == sorted(positions), path

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
        "git commit",
        "git push",
        "auto-commit",
    )
    for path in (*SOURCE_WORKFLOW_SKILLS, *SOURCE_WORKFLOW_REFERENCES):
        contents = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in contents, f"{path}: contains {term!r}"

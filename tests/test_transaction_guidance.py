from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from obsidian_wiki.transaction import TransactionRecord
from obsidian_wiki.transaction_guidance import (
    INSPECT_COMMAND,
    RecoveryAction,
    RecoveryGuidance,
    guidance_for_record,
    inspection_only_guidance,
)


@pytest.fixture
def record() -> TransactionRecord:
    return TransactionRecord(
        transaction_id="tx-1",
        status="active",
        started_at="2026-08-10T00:00:00+00:00",
        source_ids=("sources/a.md",),
        workspace=Path("workspace"),
        candidate_vault=Path("candidate"),
        preimages={"concepts/a.md": "sha256:abc", "concepts/b.md": None},
        deletions=("concepts/old.md",),
    )


def expected_action(command: str, reason: str, *requires: str) -> RecoveryAction:
    return RecoveryAction(
        command=f"llmwikiops transaction {command} tx-1",
        reason=reason,
        requires=requires,
    )


@pytest.mark.parametrize(
    ("status", "preferred", "alternatives"),
    [
        (
            "active",
            expected_action(
                "commit",
                "commit after fixing the original cause and reviewing the candidate",
                "the original failure cause is removed",
                "the candidate vault has been reviewed",
            ),
            (
                expected_action(
                    "abort",
                    "abandon the active staged work",
                    "the candidate is no longer needed",
                ),
            ),
        ),
        (
            "promoting",
            expected_action(
                "restore",
                "restore an interrupted promotion from retained snapshots",
                "the retained snapshots and current working tree have been inspected",
            ),
            (),
        ),
        (
            "failed",
            expected_action(
                "retry",
                "retry after the original cause is removed",
                "the original failure cause is removed",
                "affected targets still match their recorded preimages",
            ),
            (
                expected_action(
                    "restore",
                    "restore recorded originals instead of retrying",
                    "the retained snapshots and current working tree have been inspected",
                ),
                expected_action(
                    "abort",
                    "abandon the failed staged work",
                    "no retry or restore is required",
                ),
                expected_action(
                    "discard",
                    "remove retained recovery state",
                    "the failed outcome is understood",
                    "no retained recovery evidence is still needed",
                ),
            ),
        ),
        (
            "complete",
            expected_action(
                "discard",
                "remove retained recovery state after accepting the result",
                "the ordinary Git diff has been reviewed and accepted",
            ),
            (
                expected_action(
                    "restore",
                    "roll back the completed transaction",
                    "all affected files still match their recorded postimages",
                ),
            ),
        ),
        (
            "restored",
            expected_action(
                "discard",
                "remove retained state after verifying the restore",
                "the restored working tree has been reviewed",
            ),
            (
                expected_action(
                    "restore",
                    "confirm the idempotent restored state",
                    "a second restore is intentionally being used as a no-op",
                ),
            ),
        ),
    ],
)
def test_guidance_status_matrix(
    record: TransactionRecord,
    status: str,
    preferred: RecoveryAction,
    alternatives: tuple[RecoveryAction, ...],
) -> None:
    guidance = guidance_for_record(record.__class__(**{**record.__dict__, "status": status}))

    assert guidance == RecoveryGuidance(
        transaction_id="tx-1",
        transaction_status=status,
        inspect_command=INSPECT_COMMAND,
        preferred_action=preferred,
        alternatives=alternatives,
    )
    assert guidance.allowed_actions == (preferred, *alternatives)
    for action in guidance.allowed_actions:
        assert action.reason
        assert action.requires


def test_inspection_only_guidance_has_no_recovery_actions() -> None:
    assert inspection_only_guidance() == RecoveryGuidance(
        transaction_id=None,
        transaction_status=None,
        inspect_command="llmwikiops transaction list --json",
        preferred_action=None,
        alternatives=(),
    )
    assert inspection_only_guidance().allowed_actions == ()


def test_guidance_as_dict_uses_only_public_serialized_fields(record: TransactionRecord) -> None:
    guidance = guidance_for_record(record)

    assert guidance.as_dict() == {
        "transaction_id": "tx-1",
        "transaction_status": "active",
        "inspect_command": "llmwikiops transaction list --json",
        "preferred_action": {
            "command": "llmwikiops transaction commit tx-1",
            "reason": "commit after fixing the original cause and reviewing the candidate",
            "requires": [
                "the original failure cause is removed",
                "the candidate vault has been reviewed",
            ],
        },
        "alternatives": [
            {
                "command": "llmwikiops transaction abort tx-1",
                "reason": "abandon the active staged work",
                "requires": ["the candidate is no longer needed"],
            }
        ],
    }
    assert set(guidance.as_dict()) == {
        "transaction_id",
        "transaction_status",
        "inspect_command",
        "preferred_action",
        "alternatives",
    }
    assert "allowed_actions" not in guidance.as_dict()


def test_recovery_dataclasses_are_immutable() -> None:
    action = RecoveryAction("command", "reason", ("requirement",))
    guidance = RecoveryGuidance("tx-1", "active", INSPECT_COMMAND, action, ())

    with pytest.raises(FrozenInstanceError):
        action.command = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        guidance.transaction_id = "tx-2"  # type: ignore[misc]


def test_unknown_status_raises_value_error(record: TransactionRecord) -> None:
    unknown = record.__class__(**{**record.__dict__, "status": "unknown"})

    with pytest.raises(ValueError, match="^unsupported transaction status: 'unknown'$"):
        guidance_for_record(unknown)


def test_guidance_does_not_mutate_record(record: TransactionRecord) -> None:
    before = record.__dict__.copy()

    guidance_for_record(record)

    assert record.__dict__ == before


def test_explicit_repository_is_rendered_in_every_recovery_command(
    record: TransactionRecord,
) -> None:
    guidance = guidance_for_record(record, repository=Path("/srv/wiki root"))

    rendered = json.dumps(guidance.as_dict())
    assert "llmwikiops -C '/srv/wiki root' transaction list --json" in rendered
    for action in guidance.allowed_actions:
        assert action.command.startswith(
            "llmwikiops -C '/srv/wiki root' transaction "
        )


def test_explicit_repository_is_rendered_in_inspection_only_guidance() -> None:
    guidance = inspection_only_guidance(Path("/srv/wiki root"))

    assert guidance.inspect_command == (
        "llmwikiops -C '/srv/wiki root' transaction list --json"
    )

"""Pure recovery guidance for already validated transaction records."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from obsidian_wiki.transaction import TransactionRecord


INSPECT_COMMAND = (
    "llmwikiops transaction list --status active,promoting,failed --summary --json"
)


@dataclass(frozen=True)
class RecoveryAction:
    command: str
    reason: str
    requires: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "reason": self.reason,
            "requires": list(self.requires),
        }


@dataclass(frozen=True)
class RecoveryGuidance:
    transaction_id: str | None
    transaction_status: str | None
    inspect_command: str
    preferred_action: RecoveryAction | None
    alternatives: tuple[RecoveryAction, ...]

    @property
    def allowed_actions(self) -> tuple[RecoveryAction, ...]:
        if self.preferred_action is None:
            return self.alternatives
        return (self.preferred_action, *self.alternatives)

    def as_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "transaction_status": self.transaction_status,
            "inspect_command": self.inspect_command,
            "preferred_action": (
                self.preferred_action.as_dict()
                if self.preferred_action is not None
                else None
            ),
            "alternatives": [action.as_dict() for action in self.alternatives],
        }


def _action(
    prefix: str,
    transaction_id: str,
    command: str,
    reason: str,
    *requires: str,
) -> RecoveryAction:
    return RecoveryAction(
        command=f"{prefix} transaction {command} {transaction_id}",
        reason=reason,
        requires=requires,
    )


def _command_prefix(repository: Path | None) -> str:
    tokens = ["llmwikiops"]
    if repository is not None:
        tokens.extend(("-C", str(repository)))
    return shlex.join(tokens)


def inspection_only_guidance(repository: Path | None = None) -> RecoveryGuidance:
    return RecoveryGuidance(
        None,
        None,
        f"{_command_prefix(repository)} transaction list --status "
        "active,promoting,failed --summary --json",
        None,
        (),
    )


def guidance_for_record(
    record: TransactionRecord,
    repository: Path | None = None,
) -> RecoveryGuidance:
    transaction_id = record.transaction_id
    status = record.status
    prefix = _command_prefix(repository)

    if status == "active":
        preferred = _action(
            prefix,
            transaction_id,
            "commit",
            "commit after fixing the original cause and reviewing the candidate",
            "the original failure cause is removed",
            "the candidate vault has been reviewed",
        )
        alternatives = (
            _action(
                prefix,
                transaction_id,
                "abort",
                "abandon the active staged work",
                "the candidate is no longer needed",
            ),
        )
    elif status == "promoting":
        preferred = _action(
            prefix,
            transaction_id,
            "restore",
            "restore an interrupted promotion from retained snapshots",
            "the retained snapshots and current working tree have been inspected",
        )
        alternatives = ()
    elif status == "failed":
        preferred = _action(
            prefix,
            transaction_id,
            "retry",
            "retry after the original cause is removed",
            "the original failure cause is removed",
            "affected targets still match their recorded preimages",
            "fresh transaction validation passes immediately before retry",
            "the current candidate_pages and deletion set receive bounded "
            "substantive review immediately before retry",
        )
        alternatives = (
            _action(
                prefix,
                transaction_id,
                "restore",
                "restore recorded originals instead of retrying",
                "the retained snapshots and current working tree have been inspected",
            ),
            _action(
                prefix,
                transaction_id,
                "abort",
                "abandon the failed staged work",
                "no retry or restore is required",
            ),
            _action(
                prefix,
                transaction_id,
                "discard",
                "remove retained recovery state",
                "the failed outcome is understood",
                "no retained recovery evidence is still needed",
            ),
        )
    elif status == "complete":
        preferred = _action(
            prefix,
            transaction_id,
            "discard",
            "remove retained recovery state after accepting the result",
            "the ordinary Git diff has been reviewed and accepted",
        )
        alternatives = (
            _action(
                prefix,
                transaction_id,
                "restore",
                "roll back the completed transaction",
                "all affected files still match their recorded postimages",
            ),
        )
    elif status == "restored":
        preferred = _action(
            prefix,
            transaction_id,
            "discard",
            "remove retained state after verifying the restore",
            "the restored working tree has been reviewed",
        )
        alternatives = (
            _action(
                prefix,
                transaction_id,
                "restore",
                "confirm the idempotent restored state",
                "a second restore is intentionally being used as a no-op",
            ),
        )
    else:
        raise ValueError(f"unsupported transaction status: {status!r}")

    return RecoveryGuidance(
        transaction_id=transaction_id,
        transaction_status=status,
        inspect_command=f"{prefix} transaction show {transaction_id} --json",
        preferred_action=preferred,
        alternatives=alternatives,
    )

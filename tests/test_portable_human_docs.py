from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _flat(relative: str) -> str:
    return " ".join(_text(relative).split())


def test_cli_documents_transaction_preflight_contract() -> None:
    text = _flat("docs/cli.md")
    for required in (
        "`transaction validate ID`",
        "obsidian-wiki transaction validate <id> --json --pretty",
        "`transaction_id`, `status`, `candidate_pages`, `deletions`, `issues`, and `warnings`",
        "Exit status is `0` for `pass` and `1` for `fail`",
        "prospective vault = (live knowledge pages - declared deletions) + candidate replacements",
        "non-empty subset of the transaction's `source_ids`",
        "before any recovery snapshot or live-vault promotion",
    ):
        assert required in text


def test_cli_documents_cache_structured_output_contract() -> None:
    text = _flat("docs/cli.md")
    for required in (
        "Cache output is JSON by default",
        "obsidian-wiki cache-check --configured sources/design.md --json --pretty",
        "obsidian-wiki cache-check /resolved/vault /resolved/source.md --json --pretty",
        "obsidian-wiki cache-update /resolved/vault /resolved/source.md --json --pretty",
        "obsidian-wiki cache-hash /resolved/source.md --json --pretty",
        "`context_warnings`",
        "structured JSON stdout remains parseable and stderr stays empty",
    ):
        assert required in text


def test_portable_manifest_docs_keep_cache_update_out_of_completion() -> None:
    configuration = _flat("docs/configuration.md")
    assert (
        "Use `obsidian-wiki cache-check` and `cache-update` for v2 state"
        not in configuration
    )
    for required in (
        "Use `obsidian-wiki cache-check --configured` for Portable v2 freshness",
        "compile or recompile candidate pages through a transaction",
        "transaction commit owns the affected manifest shards",
        "not a Portable transaction completion step",
    ):
        assert required in configuration

    cli = _flat("docs/cli.md")
    for required in (
        "For `source-new` or `source-stale`, compile or recompile the source "
        "through a transaction",
        "obsidian-wiki transaction begin --source <source> --json --pretty",
        "obsidian-wiki transaction validate <id> --json --pretty",
        "obsidian-wiki transaction commit <id> --json --pretty",
        "After commit, rerun `obsidian-wiki check`",
        "`cache-update` is a low-level compatibility interface",
        "It is not a Portable transaction completion step",
        "git rm <vault>/.manifest/sources/<relative>.json",
        "whole-file Git deletion",
    ):
        assert required in cli


def test_cli_documents_hot_inputs_contract() -> None:
    text = _flat("docs/cli.md")
    for required in (
        "`hot inputs`",
        "`--pages 50`",
        "`--operations 10`",
        "obsidian-wiki hot inputs --pages 50 --operations 10 --json --pretty",
        "read-only",
        "`fingerprint`, `pages`, and `operations`",
        "Each validated immutable operation record",
    ):
        assert required in text


def test_architecture_documents_prospective_validation_before_snapshots() -> None:
    text = _flat("docs/architecture.md")
    for required in (
        "prospective vault = (live knowledge pages - declared deletions) + candidate replacements",
        "candidate-to-candidate",
        "unchanged live pages",
        "before recovery snapshots or promotion",
        "reviewed candidate bytes are the bytes promoted",
    ):
        assert required in text


def test_configuration_documents_runtime_resolution_and_version_contracts() -> None:
    text = _flat("docs/configuration.md")
    for required in (
        "does not export `OBSIDIAN_VAULT_PATH` into the parent shell",
        "config-aware command such as `cache-check --configured`",
        "release-tag-based compatible PEP 440 range",
        "Exact development-build pins",
        "high-churn",
        "`setup-version-stale`",
        "independent from Portable `requires_cli` compatibility",
    ):
        assert required in text


def test_agents_document_root_cwd_timestamps_recovery_and_hot_gate() -> None:
    text = _flat("docs/agents.md")
    for required in (
        "Keep the repository root as the command working directory",
        "do not `cd` into it",
        "runtime-only absolute path",
        "`created = updated = started_at`",
        "preserve `created` and set `updated = started_at`",
        "explicit adjacent Portable Repository completion and Personal mode completion branches",
        "validate before commit",
        "hot status` → `hot inputs` → semantic rewrite → `hot mark-current",
    ):
        assert required in text


def test_skills_reference_documents_mode_local_completion() -> None:
    text = _flat("docs/skills.md")
    for required in (
        "explicit adjacent Portable Repository completion and Personal mode completion branches",
        "repository root as CWD",
        "runtime-only absolute `candidate_vault`",
        "transaction `started_at`",
        "status-aware recovery",
        "hot freshness gate",
    ):
        assert required in text

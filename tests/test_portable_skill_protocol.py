import hashlib
import itertools
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from obsidian_wiki.config import PortableConfig
from obsidian_wiki.frontmatter import parse_frontmatter
from obsidian_wiki.graph_analysis import analyse_vault
from obsidian_wiki.portable_manifest import ShardedManifest


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "obsidian_wiki/_data/skills/llm-wiki/SKILL.md"
SETUP = "obsidian_wiki/_data/skills/wiki-setup/SKILL.md"
TRANSACTION_REVIEW = "obsidian_wiki/_data/skills/wiki-transaction-review/SKILL.md"
SOURCE_WORKFLOW_SKILLS = (
    "obsidian_wiki/_data/skills/wiki-capture/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-ingest/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-import/SKILL.md",
    "obsidian_wiki/_data/skills/wiki-research/SKILL.md",
)
SOURCE_SNAPSHOT = (
    "obsidian_wiki/_data/skills/wiki-capture/references/source-snapshot.md"
)
HISTORY_SKILLS = (
    "claude-history-ingest",
    "codex-history-ingest",
    "copilot-history-ingest",
    "hermes-history-ingest",
    "openclaw-history-ingest",
    "pi-history-ingest",
    "wiki-agent",
)
HISTORY_ROUTER = "obsidian_wiki/_data/skills/wiki-history-ingest/SKILL.md"
RAW_FORMAT = "obsidian_wiki/_data/skills/wiki-capture/references/RAW-FORMAT.md"
BOOTSTRAPS = (
    "obsidian_wiki/_data/bootstrap/AGENTS.md",
    "obsidian_wiki/_data/bootstrap/agent/rules/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/agent/workflows/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/cursor/rules/obsidian-wiki.mdc",
    "obsidian_wiki/_data/bootstrap/github/copilot-instructions.md",
    "obsidian_wiki/_data/bootstrap/kiro/steering/obsidian-wiki.md",
    "obsidian_wiki/_data/bootstrap/windsurf/rules/obsidian-wiki.md",
)
FORBIDDEN_RUNTIME_TERMS = (
    "Personal mode",
    "Portable Repository mode",
    "@name",
    "~/.obsidian-wiki/config",
    "WIKI_STAGED_WRITES",
    "cache-update",
    "QMD_",
)
MAINTENANCE_SKILLS = (
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


def skill_text(name: str) -> str:
    return text(f"obsidian_wiki/_data/skills/{name}/SKILL.md")


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_canonical_runtime_has_no_mode_or_legacy_branches() -> None:
    canonical = text(CANONICAL)
    for forbidden in FORBIDDEN_RUNTIME_TERMS:
        assert forbidden not in canonical


def test_bootstraps_delegate_to_repository_authority() -> None:
    for relative in BOOTSTRAPS:
        bootstrap = text(relative)
        for required in ("config.toml", "transaction"):
            assert required in bootstrap, f"{relative}: missing {required!r}"
        assert ".skills/" in bootstrap or "AGENTS.md" in bootstrap, relative
        for forbidden in FORBIDDEN_RUNTIME_TERMS:
            assert forbidden not in bootstrap, f"{relative}: contains {forbidden!r}"


def test_setup_is_repository_only_and_describes_managed_assets() -> None:
    setup = text(SETUP)
    flat = " ".join(setup.split())
    for required in (
        "obsidian-wiki setup [DIR]",
        "clone",
        "doctor",
        "check",
        "`.skills/`",
        "managed mirrors",
        "obsidian-wiki repo sync-skills",
        "obsidian-wiki repo upgrade-skills",
        "--apply",
        "requires_cli",
        "Git",
    ):
        assert required in flat
    for forbidden in (
        "global install",
        "prompt publication",
        *FORBIDDEN_RUNTIME_TERMS,
    ):
        assert forbidden not in setup

    assert "sync-skills` is read-only by default" in flat
    assert "upgrade-skills` applies immediately" in flat
    assert "deliberately edit `.obsidian-wiki/config.toml`" in flat
    assert "does not bypass compatibility checks or rewrite `requires_cli`" in flat
    assert "upgrade-skills --dry-run" not in flat
    assert "review its proposed changes" not in flat


def test_transaction_review_resolves_repository_authority_before_listing() -> None:
    review = text(TRANSACTION_REVIEW)
    flat = " ".join(review.split())

    frontmatter = parse_frontmatter(review)

    assert "name: wiki-transaction-review" in review
    assert frontmatter.scalars["name"] == "wiki-transaction-review"
    assert frontmatter.scalars["description"].startswith("Use when ")
    for required in (
        "nearest ancestor `.obsidian-wiki/config.toml`",
        "repository root",
        "root `AGENTS.md`",
        "vault `AGENTS.md`",
        "canonical `llm-wiki`",
        "obsidian-wiki transaction list --json --pretty",
        "Do not infer",
    ):
        assert required in flat

    authority = [
        "root `AGENTS.md`",
        "canonical `llm-wiki`",
        "vault `AGENTS.md`",
        "task skill",
    ]
    assert [flat.index(item) for item in authority] == sorted(
        flat.index(item) for item in authority
    )
    assert "canonical protocol wins" in flat


def test_quick_capture_is_a_snapshot_only_terminal_section() -> None:
    capture = text(SOURCE_WORKFLOW_SKILLS[0])
    quick = capture.split("## Quick capture", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(quick.split())

    for required in (
        "sources/inbox/YYYY-MM-DD-<slug>.md",
        "origin",
        "captured_at",
        "content_hash",
        "format",
        "exact reviewed text",
        "pending ingest",
        "stop",
        "Do not run `obsidian-wiki transaction begin`",
        "write a knowledge page",
        "create a manifest entry",
        "create an operation page",
        "run a hot command",
    ):
        assert required in flat
    for forbidden in ("candidate_vault", "transaction validate", "transaction commit"):
        assert forbidden not in quick


def test_source_snapshot_reference_replaces_raw_format() -> None:
    assert not (ROOT / RAW_FORMAT).exists()
    snapshot = text(SOURCE_SNAPSHOT)
    flat = " ".join(snapshot.split())
    for required in (
        "origin",
        "captured_at",
        "content_hash",
        "format",
        "exact reviewed text",
        "ordinary tracked UTF-8 Markdown",
        "repository-relative Source ID",
        "Git review ownership",
        "untrusted data",
        "Do not commit, push, or open a pull request",
    ):
        assert required in flat


def test_history_skills_are_repository_native_analysis_protocols() -> None:
    for name in HISTORY_SKILLS:
        relative = f"obsidian_wiki/_data/skills/{name}/SKILL.md"
        skill = text(relative)
        flat = " ".join(skill.split())
        frontmatter = parse_frontmatter(skill)
        assert frontmatter.scalars["name"] == name
        assert frontmatter.scalars["description"].startswith("Use when ")
        for required in (
            "reviewable UTF-8 Markdown snapshot",
            "sources/history/",
            "transaction begin --source",
            "parent owns",
            "analysis-only",
        ):
            assert required in flat, f"{relative}: missing {required!r}"
        for forbidden in (
            "Personal mode",
            "Portable Repository mode",
            "cache-update",
            "QMD_",
            "_raw/",
        ):
            assert forbidden not in skill, f"{relative}: contains {forbidden!r}"


def test_history_router_only_selects_retained_tool_skill() -> None:
    router = text(HISTORY_ROUTER)
    flat = " ".join(router.split())
    routed = set(re.findall(r"`([a-z]+-history-ingest)`", router))
    assert routed == set(HISTORY_SKILLS[:-1])
    assert "wiki-agent" not in router
    for required in (
        "route",
        "retained tool-specific skill",
        "does not parse sessions",
        "does not create snapshots",
        "does not begin transactions",
        "does not mutate",
    ):
        assert required in flat
    for forbidden in ("memory-bridge", "generic mutation", "manifest", "index/log"):
        assert forbidden not in router


@pytest.mark.parametrize("name", MAINTENANCE_SKILLS)
def test_maintenance_skills_are_repository_native(name: str) -> None:
    contents = skill_text(name)
    flat = " ".join(contents.split())
    frontmatter = parse_frontmatter(contents)

    assert frontmatter.scalars["name"] == name
    assert frontmatter.scalars["description"].startswith("Use when ")
    for required in (
        "nearest ancestor `.obsidian-wiki/config.toml`",
        "repository root",
        "root `AGENTS.md`",
        "canonical `llm-wiki`",
        "vault `AGENTS.md` when present",
        "task skill",
        "read-only inventory",
        "intent confirmation",
        "complete source closure",
        "final candidates",
        "reviewed deletions",
        "obsidian-wiki transaction validate <id> --json --pretty",
        "obsidian-wiki transaction commit <id> --json --pretty",
        "recommended_action",
        "allowed_actions",
    ):
        assert required in flat, f"{name}: missing {required!r}"

    authority = (
        "root `AGENTS.md`",
        "canonical `llm-wiki`",
        "vault `AGENTS.md` when present",
        "task skill",
    )
    assert [flat.index(item) for item in authority] == sorted(
        flat.index(item) for item in authority
    )

    for forbidden in (
        "Personal mode",
        "Portable Repository mode",
        "@name",
        "@work",
        "~/.obsidian-wiki/config",
        "WIKI_STAGED_WRITES",
        "cache-update",
        "QMD_",
        "_raw/",
        "_staging/",
        "_readouts/",
    ):
        assert forbidden not in contents, f"{name}: contains {forbidden!r}"


def test_rebuild_is_page_scoped_and_has_no_archive_or_whole_vault_modes() -> None:
    contents = skill_text("wiki-rebuild")
    flat = " ".join(contents.split())
    for required in (
        "transaction-backed page rebuild",
        "explicit page set",
        "bounded transactions",
        "declared sources",
        "external Git history",
    ):
        assert required in flat
    for forbidden in (
        "Archive only",
        "Archive + Rebuild",
        "Restore",
        "nuke and repave",
        "_archives/",
        "whole vault",
    ):
        assert forbidden not in contents


def test_daily_update_is_manual_and_has_no_scheduler_infrastructure() -> None:
    contents = skill_text("daily-update")
    flat = " ".join(contents.split())
    for required in (
        "manual",
        "obsidian-wiki transaction list --json --pretty",
        "obsidian-wiki cache-check <source1> [source2 ...] --json --pretty",
        "obsidian-wiki hot status --json",
        "selected page repair",
        "does not change knowledge pages, sources, manifest shards, or transactions",
        "local derived-state housekeeping",
        "may invalidate and remove a stale ignored `hot.md` artifact",
        "`transaction list` and `cache-check` are read-only",
    ):
        assert required in flat
    for forbidden in (
        "launchctl",
        "LaunchAgents",
        "cron",
        "terminal-notifier",
        "notifier",
        "QMD",
        "ordinary path is read-only",
    ):
        assert forbidden not in contents


def test_status_inspects_repository_state_and_writes_only_one_insight_page() -> None:
    contents = skill_text("wiki-status")
    flat = " ".join(contents.split())
    for required in (
        "sharded manifest",
        "operation records",
        "retained transactions",
        "graph",
        "freshness",
        "obsidian-wiki cache-check <source1> [source2 ...] --json --pretty",
        "synthesis/wiki-insights.md",
        "does not change knowledge pages, sources, manifest shards, or transactions",
        "local derived-state housekeeping",
        "may invalidate and remove a stale ignored `hot.md` artifact",
        "`transaction list` and `cache-check` are read-only",
        "page count only from `stats.pages`",
        "<vault>/journal/operations/**/*.md",
        "<vault>/.manifest/sources/**/*.json",
        "Label the origin of every reported count",
    ):
        assert required in flat
    for forbidden in (
        "_insights.md",
        "direct-write",
        "staged writes",
        "always a read-only report",
        "without changing it",
    ):
        assert forbidden not in contents


def test_update_requires_owner_committed_snapshot_before_delta() -> None:
    contents = skill_text("wiki-update")
    flat = " ".join(contents.split())
    for required in (
        "owner review, stage, and commit externally, then rerun",
        "framework and agent must not run `git add`, `git commit`, or `git push`",
        "valid HEAD",
        "status output must be empty",
        "existing replacement",
        "owner commit",
        "rerun",
        "before delta planning",
    ):
        assert required in flat
    assert flat.index("owner review, stage, and commit externally, then rerun") < flat.index(
        "cache-check"
    )


@pytest.mark.parametrize("name", MAINTENANCE_SKILLS)
def test_maintenance_inventory_uses_safe_markdown_snapshots_before_reads(
    name: str,
) -> None:
    contents = skill_text(name)
    flat = " ".join(contents.split())
    first_work = {
        "cross-linker": "Build a registry",
        "daily-update": "inventory configured Source IDs",
        "tag-taxonomy": "Read `_meta/taxonomy.md`",
        "wiki-dedup": "Build a registry",
        "wiki-lint": "Inspect knowledge pages",
        "wiki-rebuild": "List every requested final",
        "wiki-status": "inspect the manifest-v2 marker",
        "wiki-synthesize": "Build a co-occurrence map",
        "wiki-update": "Inventory the project's current architecture",
    }[name]
    for required in (
        "framework safe Markdown scanner",
        "repository and vault containment",
        "ancestor components are real directories",
        "symlink",
        "reparse point",
        "special file",
        "terminal `.md` file is ordinary and single-link",
        "`O_NOFOLLOW`",
        "`fstat`",
        "device/inode identity",
        "bounded byte snapshots",
        "fail closed before decoding or analysis",
        "must not use `read_text`, `rglob`, shell globbing, or follow links",
        "`obsidian-wiki check` alone is not a sufficient scanner preflight",
    ):
        assert required in flat, f"{name}: missing {required!r}"
    assert flat.index("## Mandatory authority preflight") < flat.index(
        "## Safe Markdown inventory boundary"
    ) < flat.index(first_work)


@pytest.mark.parametrize("name", MAINTENANCE_SKILLS)
def test_maintenance_authority_preflight_has_exact_config_stop_and_precedence(
    name: str,
) -> None:
    flat = " ".join(skill_text(name).split())
    for required in (
        "If no nearest config exists, stop and recommend exactly",
        "`obsidian-wiki setup [DIR]`",
        "If the nearest config is invalid, fail closed",
        "authority or instruction conflict",
        "canonical `llm-wiki` wins",
    ):
        assert required in flat, f"{name}: missing {required!r}"
    preflight = flat.index("## Mandatory authority preflight")
    safe_boundary = flat.index("## Safe Markdown inventory boundary")
    assert preflight < flat.index("obsidian-wiki setup [DIR]") < safe_boundary
    assert preflight < flat.index("canonical `llm-wiki` wins") < safe_boundary


def test_update_links_canonical_source_snapshot_and_closes_both_topology_paths() -> None:
    relative = "../wiki-capture/references/source-snapshot.md"
    contents = skill_text("wiki-update")
    assert relative in contents
    assert (
        ROOT / "obsidian_wiki/_data/skills/wiki-update" / relative
    ).resolve().is_file()
    flat = " ".join(contents.split())
    for required in (
        "absent target",
        "existing target",
        "pre-write owner preservation gate",
        "ordinary single-link",
        "safe atomic replacement",
        "post-write owner review",
        "owner review, stage, and commit externally, then rerun",
        "Git-tracked symlink",
        "does not establish authority",
    ):
        assert required in flat


def test_dedup_restores_deterministic_similarity_contract() -> None:
    flat = " ".join(skill_text("wiki-dedup").split())
    for required in (
        "Jaccard",
        "normalized Levenshtein",
        "substring",
        "alias cross-match",
        "same category",
        "three or more shared tags",
        "two shared tags",
        "same dominant first tag",
        "max(0.65 * token_jaccard, 0.40 * edit_similarity, substring_signal)",
        "0.75",
        "0.90",
        "YAML block scalar",
    ):
        assert required in flat

    def score(
        token_jaccard: float,
        edit_similarity: float,
        substring_signal: float,
        alias_bonus: float,
        semantic: float,
    ) -> float:
        return min(
            1.0,
            max(0.65 * token_jaccard, 0.40 * edit_similarity, substring_signal)
            + alias_bonus
            + semantic,
        )

    assert score(0.2, 0.8, 0.0, 0.0, 0.15) == pytest.approx(0.47)
    assert score(0.0, 0.1, 0.0, 0.65, 0.10) == pytest.approx(0.79)
    assert score(0.6, 0.6, 0.5, 0.65, 0.20) == 1.0


def test_rebuild_batches_are_sequential_and_failure_bounded() -> None:
    flat = " ".join(skill_text("wiki-rebuild").split())
    ordered = (
        "current live state",
        "previous successful batch",
        "no forward references",
        "deletions last",
        "stop all subsequent batches",
        "previous successful commits remain retained",
        "partial completion",
        "remaining page set",
        "recovery state",
    )
    for required in ordered:
        assert required in flat
    assert [flat.index(item) for item in ordered] == sorted(
        flat.index(item) for item in ordered
    )


def test_taxonomy_control_vocabulary_stays_outside_transactions() -> None:
    flat = " ".join(skill_text("tag-taxonomy").split())
    for required in (
        "`_meta/taxonomy.md`",
        "authoritative vocabulary",
        "owner separately performs an explicit control-file edit",
        "safe backup",
        "Git diff",
        "re-read",
        "must not write `_meta/taxonomy.md` into `candidate_vault`",
        "validator rejects `_meta/` candidates",
        "existing canonical mappings only",
    ):
        assert required in flat


def test_lint_preserves_material_rules_and_thresholds() -> None:
    flat = " ".join(skill_text("wiki-lint").split())
    for required in (
        "zero incoming links",
        "unresolved wikilinks",
        "summary exceeds 200 characters",
        "AMBIGUOUS > 15%",
        "INFERRED > 40%",
        "top 10 by incoming links",
        "INFERRED > 20%",
        "more than 0.20",
        "cohesion < 0.15",
        "at least 5 pages",
        "later source modification time also produces a page-stale finding",
        "repository-relative Source ID",
        "timezone-aware",
        "invalid or ambiguous page timestamps",
        "supersession",
        "typed relationships",
    ):
        assert required in flat
    assert "updated more than 90 days ago" not in flat
    assert "synthesis gaps" not in flat
    assert "visibility inconsistencies" not in flat


def test_lint_source_relative_staleness_has_stable_old_and_newer_source_cases() -> None:
    def is_stale(page_updated: str, source_mtime: float) -> bool:
        parsed = datetime.fromisoformat(page_updated.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("invalid or ambiguous timestamp")
        return datetime.fromtimestamp(source_mtime, tz=timezone.utc) > parsed

    old_page = "2020-01-01T00:00:00Z"
    unchanged_old_source = datetime(2019, 12, 31, tzinfo=timezone.utc).timestamp()
    assert is_stale(old_page, unchanged_old_source) is False

    new_page = "2026-08-13T08:00:00+08:00"
    newer_source = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc).timestamp()
    assert is_stale(new_page, newer_source) is True


def test_lint_distinguishes_hash_drift_timestamp_stale_and_corrupt_shards() -> None:
    def findings(
        *,
        recorded_hash: str | None,
        source_bytes: bytes,
        source_mtime: float,
        page_updated: str,
    ) -> set[str]:
        if recorded_hash is None:
            raise ValueError("manifest-corrupt")
        current_hash = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
        parsed = datetime.fromisoformat(page_updated.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("invalid-clock-or-timestamp")
        result: set[str] = set()
        if current_hash != recorded_hash:
            result.update({"source-stale", "hash-drift", "page-stale"})
        if datetime.fromtimestamp(source_mtime, tz=timezone.utc) > parsed:
            result.add("page-stale")
            if current_hash == recorded_hash:
                result.add("timestamp-only-freshness")
        return result

    old_bytes = b"old\n"
    recorded = "sha256:" + hashlib.sha256(old_bytes).hexdigest()
    updated = "2026-08-13T00:00:00Z"
    later = datetime(2026, 8, 13, 1, tzinfo=timezone.utc).timestamp()
    earlier = datetime(2026, 8, 12, 23, tzinfo=timezone.utc).timestamp()

    changed = findings(
        recorded_hash=recorded,
        source_bytes=b"changed\n",
        source_mtime=later,
        page_updated=updated,
    )
    assert {"source-stale", "hash-drift", "page-stale"} <= changed

    preserved_mtime = findings(
        recorded_hash=recorded,
        source_bytes=b"changed\n",
        source_mtime=earlier,
        page_updated=updated,
    )
    assert {"hash-drift", "page-stale"} <= preserved_mtime

    touched = findings(
        recorded_hash=recorded,
        source_bytes=old_bytes,
        source_mtime=later,
        page_updated=updated,
    )
    assert touched == {"page-stale", "timestamp-only-freshness"}

    with pytest.raises(ValueError, match="manifest-corrupt"):
        findings(
            recorded_hash=None,
            source_bytes=old_bytes,
            source_mtime=earlier,
            page_updated=updated,
        )

    flat = " ".join(skill_text("wiki-lint").split())
    for required in (
        "safely snapshot the current source bytes",
        "recompute SHA-256",
        "`source-stale` / `hash-drift`",
        "strong page-stale evidence",
        "timestamp-only freshness",
        "clock or timestamp errors",
        "corrupt shard",
    ):
        assert required in flat


def test_status_contract_matches_real_graph_and_portable_manifest_layout(
    tmp_path: Path,
) -> None:
    root = tmp_path
    vault = root / "wiki"
    source_root = root / "sources"
    vault.mkdir()
    source_root.mkdir()
    (vault / "alpha.md").write_text("# Alpha\n\n[[beta]]\n", encoding="utf-8")
    (vault / "beta.md").write_text("# Beta\n", encoding="utf-8")
    graph = analyse_vault(vault)
    assert set(graph) == {
        "god_nodes",
        "communities",
        "surprising_connections",
        "dead_ends",
        "isolated",
        "stats",
    }
    assert set(graph["stats"]) == {"pages", "edges", "communities"}
    assert graph["stats"]["pages"] == 2

    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "storage": "sharded",
                "entries": ".manifest/sources",
            }
        ),
        encoding="utf-8",
    )
    source = source_root / "evidence.md"
    source.write_text("evidence\n", encoding="utf-8")
    config = PortableConfig(
        root=root,
        path=root / ".obsidian-wiki/config.toml",
        schema_version=1,
        implementation="obsidian-wiki",
        requires_cli=">=0",
        vault=vault,
        sources=(source_root,),
        skills=root / ".skills",
        local_state=root / ".obsidian-wiki/local",
        settings={},
    )
    manifest = ShardedManifest(config)
    manifest.upsert(source, pages=["alpha.md"])
    entries = manifest.iter_entries()
    assert [entry.source_id for entry in entries] == ["sources/evidence.md"]
    assert manifest.entries_root == vault / ".manifest" / "sources"

    flat = " ".join(skill_text("wiki-status").split())
    for required in (
        "parse its stdout as JSON",
        "`stats.pages`",
        "`god_nodes`",
        "`communities`",
        "`surprising_connections`",
        "`dead_ends`",
        "`isolated`",
        "<vault>/journal/operations/**/*.md",
        "<vault>/.manifest/sources/**/*.json",
        "validate each shard schema",
        "duplicate Source IDs",
        "does not return operation records or manifest shard payloads",
    ):
        assert required in flat
    for unsupported in (
        "bridge pages",
        "tag-cluster cohesion",
        "cross-category connections",
        "graph delta",
        "tier changes",
    ):
        assert unsupported not in flat


def test_dedup_bounds_candidate_generation_before_similarity_scoring() -> None:
    flat = " ".join(skill_text("wiki-dedup").split())
    for required in (
        "candidate blocks",
        "normalized titles and aliases",
        "shared title tokens",
        "shared tags",
        "explicit entity references",
        "before similarity scoring",
        "500 pairs per block",
        "10,000 candidate pairs total",
        "deferred",
        "deterministic order",
    ):
        assert required in flat
    assert "For every pair," not in flat
    assert flat.index("candidate blocks") < flat.index("For every candidate pair")


def test_dedup_cursor_resumes_exclusively_without_pair_loss_or_duplicates() -> None:
    def fingerprint(inventory: tuple[tuple[str, str], ...]) -> str:
        canonical = json.dumps(sorted(inventory), separators=(",", ":")).encode()
        return hashlib.sha256(canonical).hexdigest()

    def emit(
        inventory: tuple[tuple[str, str], ...],
        *,
        limit: int,
        block_key: str,
        cursor: dict[str, object] | None = None,
    ) -> tuple[list[tuple[str, str]], dict[str, object] | None]:
        stable_inventory = tuple(sorted(inventory))
        stable_ids = tuple(page_id for page_id, _blocking_fields in stable_inventory)
        inventory_fingerprint = fingerprint(stable_inventory)
        pairs = list(itertools.combinations(stable_ids, 2))
        start = 0
        if cursor is not None:
            if cursor["inventory_fingerprint"] != inventory_fingerprint:
                raise ValueError("inventory-changed")
            if cursor["block_key"] != block_key:
                raise ValueError("block-key-mismatch")
            start = pairs.index(cursor["last_emitted_pair"]) + 1
        batch = pairs[start : start + limit]
        if start + len(batch) == len(pairs):
            return batch, None
        return batch, {
            "inventory_fingerprint": inventory_fingerprint,
            "block_key": block_key,
            "last_emitted_pair": batch[-1],
        }

    inventory = tuple(
        (f"concepts/page-{number:02}.md", "tags=shared") for number in range(33)
    )
    page_ids = tuple(page_id for page_id, _blocking_fields in inventory)
    expected = list(itertools.combinations(page_ids, 2))
    first, cursor = emit(inventory, limit=500, block_key="tag:shared", cursor=None)
    assert len(expected) == 528
    assert cursor == {
        "inventory_fingerprint": fingerprint(inventory),
        "block_key": "tag:shared",
        "last_emitted_pair": first[-1],
    }
    second, final_cursor = emit(
        inventory, limit=500, block_key="tag:shared", cursor=cursor
    )
    assert second[0] == expected[500]
    assert first + second == expected
    assert len(set(first) & set(second)) == 0
    assert final_cursor is None

    total_limited, total_cursor = emit(
        inventory, limit=17, block_key="tag:shared", cursor=None
    )
    resumed, _ = emit(
        inventory, limit=17, block_key="tag:shared", cursor=total_cursor
    )
    assert total_limited + resumed == expected[:34]

    with pytest.raises(ValueError, match="inventory-changed"):
        emit(
            inventory[:-1] + ((inventory[-1][0], "tags=changed"),),
            limit=500,
            block_key="tag:shared",
            cursor=cursor,
        )

    flat = " ".join(skill_text("wiki-dedup").split())
    for required in (
        "`block_key`",
        "`last_emitted_pair`",
        "two stable page IDs",
        "resume exclusively after",
        "mid-block",
        "inventory fingerprint",
        "invalidates the cursor",
        "fail closed",
        "no duplicate or skipped pair",
    ):
        assert required in flat

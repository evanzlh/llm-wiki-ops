from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID, __version__
from obsidian_wiki import cli as cli_module
from obsidian_wiki import migration as migration_module
from obsidian_wiki.cli import main
from obsidian_wiki.config import load_portable_config
from obsidian_wiki.frontmatter import Provenance, Relationship, parse_frontmatter
from obsidian_wiki.migration import MigrationError, analyze_migration, apply_migration
from obsidian_wiki.portable import PROJECT_AGENT_DIRS
from obsidian_wiki.portable_check import check_portable_repo
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.skill_inventory import ManagedSkillsInventory, read_inventory
from obsidian_wiki.skill_trees import discover_skill_collection


def make_legacy_repo(tmp_path: Path):
    root = tmp_path / "knowledge"
    sources = root / "sources"
    vault = root / "wiki"
    sources.mkdir(parents=True)
    (vault / "concepts").mkdir(parents=True)
    source = sources / "a.md"
    source.write_text("source", encoding="utf-8")
    page = vault / "concepts" / "a.md"
    page.write_text(
        f"""---
title: A
category: concepts
tags: [example]
sources:
  - {source}
created: 2026-08-07
updated: 2026-08-07
---
# A
""",
        encoding="utf-8",
    )
    (vault / "index.md").write_text("# Legacy index\n", encoding="utf-8")
    (vault / "log.md").write_text("# Legacy log\n", encoding="utf-8")
    (vault / "hot.md").write_text("# Legacy hot\n", encoding="utf-8")
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {
                    str(source): {
                        "content_hash": "sha256:old",
                        "pages_produced": ["concepts/a.md"],
                        "last_ingested": "2026-08-01T00:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return root, sources, vault, source, page


def migration_skills(tmp_path: Path) -> Path:
    source = tmp_path / "framework-skills"
    skill = source / "wiki-ingest"
    if not skill.exists():
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: wiki-ingest\n"
            "description: Ingest authoritative sources into the portable wiki.\n"
            "---\n\n"
            "# Wiki ingest\n",
            encoding="utf-8",
        )
        reference = skill / "references/迁移说明.md"
        reference.parent.mkdir()
        reference.write_text("# 迁移说明\n", encoding="utf-8")
        (skill / "references/empty/nested").mkdir(parents=True)
    return source


def test_analyze_maps_contained_absolute_source_to_repo_id(tmp_path: Path) -> None:
    root, sources, vault, source, _page = make_legacy_repo(tmp_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    assert plan.blockers == ()
    assert plan.source_mappings == ((str(source.resolve()), "sources/a.md"),)
    assert plan.page_updates == ("concepts/a.md",)
    assert plan.manifest_entries == ("sources/a.md",)
    assert {
        path: path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == before


def test_analyze_blocks_external_and_url_sources(tmp_path: Path) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    external = tmp_path / "external.md"
    external.write_text("external", encoding="utf-8")
    manifest = json.loads((vault / ".manifest.json").read_text())
    manifest["sources"][str(external)] = {
        "content_hash": "sha256:x",
        "pages_produced": [],
    }
    manifest["sources"]["https://example.com/live"] = {
        "content_hash": "sha256:y",
        "pages_produced": [],
    }
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    assert {blocker.code for blocker in plan.blockers} == {
        "external-source",
        "live-url-source",
    }


def test_analyze_rejects_vault_or_sources_outside_root(tmp_path: Path) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root / "nested", vault=vault, source_root=sources)
    assert "outside-root" in {blocker.code for blocker in plan.blockers}


def test_plan_serialization_is_stable_and_repo_relative(tmp_path: Path) -> None:
    root, sources, vault, source, _page = make_legacy_repo(tmp_path)

    serialized = analyze_migration(
        root=root, vault=vault, source_root=sources
    ).to_dict()

    assert list(serialized) == [
        "root",
        "vault",
        "source_root",
        "source_mappings",
        "page_updates",
        "manifest_entries",
        "blockers",
        "warnings",
    ]
    assert serialized["root"] == "."
    assert serialized["vault"] == "wiki"
    assert serialized["source_root"] == "sources"
    assert serialized["source_mappings"] == [[str(source.resolve()), "sources/a.md"]]


def test_analyze_supports_list_manifest_and_relative_vault_paths(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    relative_source = "../sources/a.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(str(source), relative_source),
        encoding="utf-8",
    )
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "path": relative_source,
                        "content_hash": "sha256:old",
                        "pages": ["concepts/a.md"],
                        "ingested_at": "2026-08-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert plan.blockers == ()
    assert plan.source_mappings == ((relative_source, "sources/a.md"),)
    assert plan.page_updates == ("concepts/a.md",)
    assert plan.warnings == ()


def test_analyze_uses_v1_fallbacks_when_primary_fields_are_null(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    relative_source = "../sources/a.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(str(source), relative_source),
        encoding="utf-8",
    )
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "path": None,
                        "source_id": relative_source,
                        "content_hash": "sha256:old",
                        "pages_produced": None,
                        "pages": ["concepts/a.md"],
                        "last_ingested": None,
                        "ingested_at": "2026-08-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert plan.blockers == ()
    assert plan.source_mappings == ((relative_source, "sources/a.md"),)
    assert plan.page_updates == ("concepts/a.md",)


def test_analyze_blocks_malformed_manifest_entries(tmp_path: Path) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    42,
                    {"path": None, "source_id": None},
                    {"path": "../sources/a.md", "pages_produced": "bad"},
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert "manifest-invalid" in {blocker.code for blocker in plan.blockers}


def test_analyze_allows_identical_aliases_but_blocks_conflicting_collision(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, _page = make_legacy_repo(tmp_path)
    manifest_path = vault / ".manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"]["../sources/a.md"] = dict(manifest["sources"][str(source)])
    manifest["sources"]["../sources/a.md"]["content_hash"] = "old"
    manifest["sources"]["../sources/a.md"]["pages_produced"] = [
        "concepts/a.md",
        "concepts/a.md",
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    identical = analyze_migration(root=root, vault=vault, source_root=sources)
    assert "source-id-collision" not in {blocker.code for blocker in identical.blockers}

    manifest["sources"]["../sources/a.md"]["content_hash"] = "sha256:different"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    conflicting = analyze_migration(root=root, vault=vault, source_root=sources)
    assert "source-id-collision" in {blocker.code for blocker in conflicting.blockers}


def test_analyze_blocks_missing_pseudo_and_unsafe_manifest_paths(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    manifest_path = vault / ".manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"]["../sources/missing.md"] = {
        "content_hash": "sha256:missing",
        "pages_produced": ["../escape.md", "concepts/missing.md"],
    }
    manifest["sources"]["agent:session-1"] = {
        "content_hash": "sha256:pseudo",
        "pages_produced": [],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert {blocker.code for blocker in plan.blockers} >= {
        "missing-source",
        "pseudo-source",
    }
    assert {blocker.code for blocker in plan.blockers} >= {
        "unsafe-page",
        "missing-page",
    }

    source = sources / "missing.md"
    source.write_text("now present", encoding="utf-8")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    assert {blocker.code for blocker in plan.blockers} >= {
        "unsafe-page",
        "missing-page",
        "pseudo-source",
    }


def test_analyze_blocks_unmapped_absolute_page_source(tmp_path: Path) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    other = sources / "other.md"
    other.write_text("other", encoding="utf-8")
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "sources:\n", f"sources:\n  - {other}\n"
        ),
        encoding="utf-8",
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    blocker = next(
        item for item in plan.blockers if item.code == "unmapped-page-source"
    )
    assert blocker.source == f"concepts/a.md: {other}"
    serialized_blocker = next(
        item
        for item in plan.to_dict()["blockers"]
        if item["code"] == "unmapped-page-source"
    )
    assert list(serialized_blocker) == ["code", "source", "message"]


def test_analyze_records_exact_mapping_for_resolved_page_alias(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    page_alias = f"{sources}/../sources/a.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(str(source), page_alias),
        encoding="utf-8",
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert plan.blockers == ()
    assert (page_alias, "sources/a.md") in plan.source_mappings
    assert plan.page_updates == ("concepts/a.md",)


def test_analyze_records_scalar_frontmatter_source(tmp_path: Path) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            f"sources:\n  - {source}", f"sources: {source}"
        ),
        encoding="utf-8",
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert plan.blockers == ()
    assert plan.page_updates == ("concepts/a.md",)


def test_analyze_records_relative_page_alias_and_blocks_page_pseudo_source(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    relative_alias = "../sources/a.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            f"  - {source}", f"  - {relative_alias}\n  - agent:orphan"
        ),
        encoding="utf-8",
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert (relative_alias, "sources/a.md") in plan.source_mappings
    assert plan.page_updates == ("concepts/a.md",)
    assert any(
        blocker.code == "pseudo-source"
        and blocker.source == "concepts/a.md: agent:orphan"
        for blocker in plan.blockers
    )


def test_analyze_rejects_manifest_page_through_external_directory_symlink(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text(
        "---\ntitle: Leak\nsources: []\n---\nsecret\n", encoding="utf-8"
    )
    os.symlink(outside, vault / "references")
    manifest_path = vault / ".manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    only_entry = next(iter(manifest["sources"].values()))
    only_entry["pages_produced"].append("references/leak.md")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert any(
        blocker.code == "unsafe-page" and blocker.source == "references/leak.md"
        for blocker in plan.blockers
    )


def test_analyze_rejects_internal_source_and_page_symlink_components(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, _page = make_legacy_repo(tmp_path)
    source_alias = sources / "alias.md"
    os.symlink(source, source_alias)
    (vault / "entities").mkdir()
    (vault / "entities" / "linked.md").write_text(
        "---\ntitle: Linked\ncategory: entities\ntags: []\nsources: []\n"
        "created: 2026-08-07\nupdated: 2026-08-07\n---\n# Linked\n",
        encoding="utf-8",
    )
    os.symlink(vault / "entities", vault / "references")
    manifest_path = vault / ".manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][str(source_alias)] = {
        "content_hash": "sha256:old",
        "pages_produced": ["references/linked.md"],
        "last_ingested": "2026-08-01T00:00:00Z",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert {blocker.code for blocker in plan.blockers} >= {
        "unsafe-page",
        "unsafe-source",
    }


def test_analyze_marks_canonical_scalar_source_for_list_normalization(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            f"sources:\n  - {source}", "sources: sources/a.md"
        ),
        encoding="utf-8",
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert plan.blockers == ()
    assert plan.page_updates == ("concepts/a.md",)


def test_analyze_rejects_manifest_without_explicit_legacy_sources(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    (vault / ".manifest.json").write_text(
        json.dumps({"schema_version": 2, "kind": "sharded"}), encoding="utf-8"
    )

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert "manifest-invalid" in {blocker.code for blocker in plan.blockers}


def test_windows_drive_source_is_not_misclassified_as_live_url(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    manifest_path = vault / ".manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"]["C://docs/a.md"] = {
        "content_hash": "sha256:windows",
        "pages_produced": [],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    matching = [item for item in plan.blockers if item.source == "C://docs/a.md"]
    assert {item.code for item in matching} == {"external-source"}


@pytest.mark.parametrize(
    "managed", [".obsidian-wiki", ".skills", ".gitattributes"]
)
def test_analyze_rejects_source_root_overlapping_managed_paths(
    tmp_path: Path, managed: str
) -> None:
    root, _sources, vault, _source, _page = make_legacy_repo(tmp_path)
    managed_source = root / managed
    managed_source.mkdir()

    plan = analyze_migration(
        root=root, vault=vault, source_root=managed_source
    )

    assert "managed-path-overlap" in {blocker.code for blocker in plan.blockers}


def test_analyze_rejects_vault_below_portable_local_state(tmp_path: Path) -> None:
    root, sources, _vault, _source, _page = make_legacy_repo(tmp_path)
    managed_vault = root / ".obsidian-wiki/local/wiki"

    plan = analyze_migration(
        root=root, vault=managed_vault, source_root=sources
    )

    assert "managed-path-overlap" in {blocker.code for blocker in plan.blockers}


def test_analyze_rejects_preexisting_portable_manifest_artifacts(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    stale = vault / ".manifest/sources/stale.md.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale shard\n", encoding="utf-8")

    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    assert "portable-artifact-conflict" in {
        blocker.code for blocker in plan.blockers
    }


def test_apply_converts_manifest_frontmatter_and_derived_files(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    result = apply_migration(
        plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
    )

    config = load_portable_config(
        root / ".obsidian-wiki" / "config.toml",
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    assert "sources/a.md" in page.read_text(encoding="utf-8")
    entry = ShardedManifest(config).load("sources/a.md")
    assert entry is not None
    assert entry.compiled_at == "2026-08-01T00:00:00Z"
    assert json.loads((vault / ".manifest.json").read_text())["schema_version"] == 2
    assert "```query" in (vault / "index.md").read_text(encoding="utf-8")
    assert "journal/operations" in (vault / "log.md").read_text(encoding="utf-8")
    assert "* -text" in (root / ".gitattributes").read_text().splitlines()
    assert not (vault / "hot.md").exists()
    assert result.changed_files
    assert result.backup_dir.is_dir()
    assert len(list((vault / "journal/operations").rglob("*.md"))) == 1
    backup_manifest = json.loads(
        (result.backup_dir.parent / "manifest.json").read_text(encoding="utf-8")
    )
    assert backup_manifest["status"] == "committed"


def test_apply_refuses_blocked_plan(tmp_path: Path) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    external = tmp_path / "outside.md"
    manifest = json.loads((vault / ".manifest.json").read_text())
    manifest["sources"][str(external)] = {
        "content_hash": "x",
        "pages_produced": [],
    }
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    with pytest.raises(MigrationError, match="blocker"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert not (root / ".obsidian-wiki" / "config.toml").exists()


def test_apply_failure_restores_every_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    before_directories = {
        path.relative_to(root) for path in root.rglob("*") if path.is_dir()
    }
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    monkeypatch.setattr(
        ShardedManifest,
        "upsert",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(MigrationError, match="rolled back"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    after_directories = {
        path.relative_to(root) for path in root.rglob("*") if path.is_dir()
    }
    assert after == before
    assert after_directories == before_directories
    assert not (root / ".obsidian-wiki").exists()


def test_apply_mid_swap_failure_restores_preimages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    before_directories = {
        path.relative_to(root) for path in root.rglob("*") if path.is_dir()
    }
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_replace = migration_module._atomic_replace_bytes
    calls = 0

    def fail_once(path: Path, data: bytes, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 5:
            raise OSError("swap failed")
        original_replace(path, data, **kwargs)

    monkeypatch.setattr(migration_module, "_atomic_replace_bytes", fail_once)

    with pytest.raises(MigrationError, match="rolled back: swap failed"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    after_directories = {
        path.relative_to(root) for path in root.rglob("*") if path.is_dir()
    }
    assert after == before
    assert after_directories == before_directories
    assert not (root / ".obsidian-wiki").exists()


def test_apply_retains_snapshots_when_rollback_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_replace = migration_module._atomic_replace_bytes
    page_applied = False
    forward_failed = False

    def fail_forward_and_restore(path: Path, data: bytes, **kwargs) -> None:
        nonlocal page_applied, forward_failed
        if page_applied and not forward_failed:
            forward_failed = True
            raise OSError("forward swap failed")
        if forward_failed:
            raise OSError("restore failed")
        original_replace(path, data, **kwargs)
        if path == page:
            page_applied = True

    monkeypatch.setattr(
        migration_module, "_atomic_replace_bytes", fail_forward_and_restore
    )

    with pytest.raises(MigrationError, match="rollback was incomplete") as caught:
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert "forward swap failed" in str(caught.value)
    migration_roots = list((root / ".obsidian-wiki/local/migrations").iterdir())
    assert len(migration_roots) == 1
    payload = json.loads(
        (migration_roots[0] / "manifest.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "rollback-failed"
    assert (migration_roots[0] / "snapshots").is_dir()


def test_rollback_preserves_in_place_competitor_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_replace = migration_module._atomic_replace_bytes
    competitor = b"concurrent in-place edit\n"
    competed = False

    def compete_after_page(path: Path, data: bytes, **kwargs) -> None:
        nonlocal competed
        original_replace(path, data, **kwargs)
        if path == page and not competed:
            page.write_bytes(competitor)
            competed = True
            raise OSError("fail after competitor edit")

    monkeypatch.setattr(migration_module, "_atomic_replace_bytes", compete_after_page)

    with pytest.raises(MigrationError, match="rollback was incomplete"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert competed
    assert page.read_bytes() == competitor


def test_apply_refuses_manifest_or_page_preimage_drift(tmp_path: Path) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    page.write_text(page.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="changed since analysis"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert not (root / ".obsidian-wiki" / "config.toml").exists()


def test_apply_rejects_absent_target_below_symlinked_managed_parent(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    os.symlink(outside, root / ".skills")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    with pytest.raises(MigrationError, match="symbolic link"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert list(outside.iterdir()) == []
    assert not (root / ".obsidian-wiki").exists()


def test_apply_rechecks_managed_parent_at_each_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    outside = tmp_path / "race-outside"
    outside.mkdir()
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_replace = migration_module._atomic_replace_bytes
    swapped = False

    def swap_parent(path: Path, data: bytes, **kwargs) -> None:
        nonlocal swapped
        relative = path.relative_to(root)
        if not swapped and relative.parts[0] == ".skills":
            skills = root / ".skills"
            if skills.exists():
                skills.rename(root / ".skills-raced")
            os.symlink(outside, skills)
            swapped = True
        original_replace(path, data, **kwargs)

    monkeypatch.setattr(migration_module, "_atomic_replace_bytes", swap_parent)

    with pytest.raises(MigrationError, match="rollback was incomplete"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert swapped
    assert list(outside.iterdir()) == []


def test_apply_never_overwrites_competing_operation_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_exclusive = migration_module._write_exclusive_bytes
    competitor: bytes | None = None
    operation_target: Path | None = None

    def create_competitor(path: Path, data: bytes, *, root: Path, **kwargs) -> None:
        nonlocal competitor, operation_target
        operation_target = path
        competitor = data
        path.write_bytes(data)
        original_exclusive(path, data, root=root, **kwargs)

    monkeypatch.setattr(
        migration_module, "_write_exclusive_bytes", create_competitor
    )

    with pytest.raises(MigrationError, match="rollback was incomplete"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert operation_target is not None
    assert competitor is not None
    assert operation_target.read_bytes() == competitor


def test_apply_does_not_claim_exact_competitor_as_its_config_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_replace = migration_module._atomic_replace_bytes
    competitor_path = root / ".obsidian-wiki/config.toml"
    installed = False
    competitor_data: bytes | None = None

    def install_exact_competitor(path: Path, data: bytes, **kwargs) -> None:
        nonlocal competitor_data, installed
        if path == competitor_path and not installed:
            path.write_bytes(data)
            installed = True
            competitor_data = data
        original_replace(path, data, **kwargs)

    monkeypatch.setattr(
        migration_module, "_atomic_replace_bytes", install_exact_competitor
    )

    with pytest.raises(MigrationError, match="rolled back"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert installed
    assert competitor_data is not None
    assert competitor_path.read_bytes() == competitor_data


def test_apply_preserves_concurrent_hot_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_unlink = migration_module._unlink_expected
    hot = vault / "hot.md"
    deleted = False

    def delete_before_migration(path: Path, **kwargs) -> None:
        nonlocal deleted
        if path == hot and not deleted:
            path.unlink()
            deleted = True
        original_unlink(path, **kwargs)

    monkeypatch.setattr(migration_module, "_unlink_expected", delete_before_migration)

    with pytest.raises(MigrationError, match="rolled back"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert deleted
    assert not hot.exists()


def test_apply_preserves_markdown_body_bytes_and_normalizes_scalar_sources(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    body = b"# A\r\n\r\nBody with  spaces.\r\n"
    page.write_bytes(
        (
            "---\r\n"
            "title: A\r\n"
            "category: concepts\r\n"
            "tags: [example]\r\n"
            f"sources: {source}\r\n"
            "created: 2026-08-07\r\n"
            "updated: 2026-08-07\r\n"
            "---\r\n"
        ).encode()
        + body
    )
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    apply_migration(
        plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
    )

    migrated = page.read_bytes()
    assert migrated.split(b"---\r\n", 2)[2] == body
    assert b"sources:\r\n  - sources/a.md\r\n" in migrated


def test_apply_preserves_supported_nested_frontmatter_and_output_is_valid(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    nested = '''provenance:
  extracted: 0.72
  inferred: 0.25
  ambiguous: 0.03
relationships:
  - target: "[[concepts/a]]"
    type: related-to
'''
    page.write_text(
        page.read_text(encoding="utf-8").replace("---\n# A\n", nested + "---\n# A\n"),
        encoding="utf-8",
    )
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    apply_migration(
        plan, installed_version=__version__, source_skills=migration_skills(tmp_path)
    )

    migrated = page.read_bytes()
    assert migrated.count(nested.encode("utf-8")) == 1
    parsed = parse_frontmatter(migrated.decode("utf-8"))
    assert parsed.provenance == Provenance("0.72", "0.25", "0.03")
    assert parsed.relationships == (
        Relationship(target="[[concepts/a]]", type="related-to"),
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "add", "."], check=True, capture_output=True
    )
    config = load_portable_config(
        root / ".obsidian-wiki/config.toml",
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )
    report = check_portable_repo(config)
    assert report == {
        "status": "pass",
        "errors": 0,
        "warnings": 0,
        "issues": [],
    }


def test_apply_rebuilds_shard_edges_from_actual_page_frontmatter(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, _page = make_legacy_repo(tmp_path)
    stale = vault / "entities" / "stale.md"
    stale.parent.mkdir()
    stale.write_text(
        "---\ntitle: Stale\ncategory: entities\ntags: []\nsources: []\n"
        "created: 2026-08-07\nupdated: 2026-08-07\n---\n# Stale\n",
        encoding="utf-8",
    )
    manifest_path = vault / ".manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][str(source)]["pages_produced"] = ["entities/stale.md"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    apply_migration(
        plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
    )

    config = load_portable_config(
        root / ".obsidian-wiki/config.toml",
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    entry = ShardedManifest(config).load("sources/a.md")
    assert entry is not None
    assert entry.pages == ("concepts/a.md",)


def test_apply_refuses_knowledge_page_inventory_drift(tmp_path: Path) -> None:
    root, sources, vault, source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    added = vault / "entities" / "added.md"
    added.parent.mkdir()
    added.write_text(
        "---\ntitle: Added\ncategory: entities\ntags: []\n"
        f"sources: [{source}]\ncreated: 2026-08-07\nupdated: 2026-08-07\n"
        "---\n# Added\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="knowledge page set changed"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert not (root / ".obsidian-wiki").exists()


def test_apply_rolls_back_when_page_inventory_changes_during_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_verify = migration_module._verify_candidate_dependencies
    added = vault / "entities/concurrent.md"
    inserted = False

    def add_after_candidate_verification(
        root: Path, dependencies: dict[str, bytes | None]
    ) -> None:
        nonlocal inserted
        original_verify(root, dependencies)
        if not inserted:
            added.parent.mkdir()
            added.write_text(
                "---\ntitle: Concurrent\ncategory: entities\ntags: []\n"
                "sources: [sources/a.md]\ncreated: 2026-08-08\n"
                "updated: 2026-08-08\n---\n# Concurrent\n",
                encoding="utf-8",
            )
            inserted = True

    monkeypatch.setattr(
        migration_module,
        "_verify_candidate_dependencies",
        add_after_candidate_verification,
    )

    with pytest.raises(MigrationError, match="knowledge page set changed during apply"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert inserted
    assert added.is_file()


def test_apply_refuses_portable_manifest_artifact_drift(tmp_path: Path) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    stale = vault / ".manifest/sources/stale.md.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale shard\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="artifacts changed since analysis"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert not (root / ".obsidian-wiki").exists()


def test_apply_preserves_existing_obsidian_owner_settings(tmp_path: Path) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    settings = {
        vault / ".obsidian/app.json": b'{"owner": "app"}\n',
        vault / ".obsidian/appearance.json": b'{"owner": "appearance"}\n',
    }
    for path, data in settings.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    apply_migration(
        plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
    )

    assert {path: path.read_bytes() for path in settings} == settings


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable modes are not portable")
def test_apply_preserves_executable_mode_in_canonical_skill_assets(
    tmp_path: Path,
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    source_skills = tmp_path / "bundled-skills"
    script = source_skills / "demo" / "scripts" / "run.sh"
    script.parent.mkdir(parents=True)
    (source_skills / "demo" / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: Run the complete migration demonstration.\n"
        "---\n\n"
        "# Demo\n",
        encoding="utf-8",
    )
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    reference = source_skills / "demo/references/迁移说明.md"
    reference.parent.mkdir()
    reference.write_text("# 迁移说明\n", encoding="utf-8")
    (source_skills / "demo/assets").mkdir()
    (source_skills / "demo/assets/blob.bin").write_bytes(b"\x00\xffmigration\n")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    apply_migration(
        plan, installed_version="2026.8", source_skills=source_skills
    )

    installed = root / ".skills/demo/scripts/run.sh"
    assert installed.stat().st_mode & 0o777 == 0o755
    canonical = discover_skill_collection(root / ".skills")
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        assert discover_skill_collection(root / agent_relative) == canonical
    inventory = read_inventory(root)
    assert isinstance(inventory, ManagedSkillsInventory)
    assert inventory.managed_skills == canonical.names
    assert inventory.managed_skill_digests == {
        skill.name: skill.digest for skill in canonical.skills
    }


def test_apply_preserves_empty_directories_in_all_skill_trees(tmp_path: Path) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    source_skills = migration_skills(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    apply_migration(
        plan, installed_version="2026.8", source_skills=source_skills
    )

    empty_relative = Path("wiki-ingest/references/empty/nested")
    assert (root / ".skills" / empty_relative).is_dir()
    canonical = discover_skill_collection(root / ".skills")
    for agent_relative, _label in PROJECT_AGENT_DIRS:
        assert (root / agent_relative / empty_relative).is_dir()
        assert discover_skill_collection(root / agent_relative) == canonical
    inventory = read_inventory(root)
    assert isinstance(inventory, ManagedSkillsInventory)
    assert inventory.managed_skill_digests == {
        skill.name: skill.digest for skill in canonical.skills
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes are not portable")
def test_apply_empty_skill_directories_use_materializer_mode(tmp_path: Path) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    source_skills = migration_skills(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    original_umask = os.umask(0o077)
    try:
        apply_migration(
            plan, installed_version="2026.8", source_skills=source_skills
        )
    finally:
        os.umask(original_umask)

    empty_relative = Path("wiki-ingest/references/empty/nested")
    directories = (
        root / ".skills" / empty_relative,
        *(
            root / agent_relative / empty_relative
            for agent_relative, _label in PROJECT_AGENT_DIRS
        ),
    )
    assert all(
        stat.S_IMODE(directory.stat().st_mode) == 0o755
        for directory in directories
    )


def test_apply_rollback_removes_new_empty_skill_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    source_skills = migration_skills(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    empty_relative = Path("wiki-ingest/references/empty/nested")
    empty_directories = (
        root / ".skills" / empty_relative,
        *(
            root / agent_relative / empty_relative
            for agent_relative, _label in PROJECT_AGENT_DIRS
        ),
    )
    original_replace = migration_module._atomic_replace_bytes
    page_applied = False
    failed = False
    observed_empty_directories = False

    def fail_after_page(path: Path, data: bytes, **kwargs) -> None:
        nonlocal failed, observed_empty_directories, page_applied
        observed_empty_directories = observed_empty_directories or all(
            directory.is_dir() for directory in empty_directories
        )
        if page_applied and not failed:
            failed = True
            raise OSError("fail after empty skill directories")
        original_replace(path, data, **kwargs)
        if path == page:
            page_applied = True

    monkeypatch.setattr(migration_module, "_atomic_replace_bytes", fail_after_page)

    with pytest.raises(
        MigrationError, match="rolled back: fail after empty skill directories"
    ):
        apply_migration(
            plan, installed_version="2026.8", source_skills=source_skills
        )

    assert observed_empty_directories
    assert all(not directory.exists() for directory in empty_directories)


def test_apply_detects_empty_skill_directory_deleted_before_operation_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    source_skills = migration_skills(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    empty_directory = root / ".skills/wiki-ingest/references/empty/nested"
    canonical_skill = root / ".skills/wiki-ingest/SKILL.md"
    original_replace = migration_module._atomic_replace_bytes
    deleted = False

    def delete_empty_directory(path: Path, data: bytes, **kwargs) -> None:
        nonlocal deleted
        original_replace(path, data, **kwargs)
        if path == canonical_skill and not deleted:
            empty_directory.rmdir()
            deleted = True

    monkeypatch.setattr(
        migration_module, "_atomic_replace_bytes", delete_empty_directory
    )

    with pytest.raises(MigrationError, match="directory postimage"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=source_skills
        )

    assert deleted
    assert not (root / ".skills").exists()


def test_apply_preserves_recreated_empty_skill_directory_during_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    source_skills = migration_skills(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    empty_directory = root / ".skills/wiki-ingest/references/empty/nested"
    canonical_skill = root / ".skills/wiki-ingest/SKILL.md"
    original_replace = migration_module._atomic_replace_bytes
    replaced = False
    replacement_postimage: tuple[int, int, int] | None = None

    def replace_empty_directory(path: Path, data: bytes, **kwargs) -> None:
        nonlocal replaced, replacement_postimage
        original_replace(path, data, **kwargs)
        if path == canonical_skill and not replaced:
            replacement = empty_directory.parent / "replacement"
            replacement.mkdir(mode=0o755)
            replacement.chmod(0o755)
            empty_directory.rmdir()
            replacement.rename(empty_directory)
            metadata = empty_directory.stat()
            replacement_postimage = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_ctime_ns,
            )
            replaced = True

    monkeypatch.setattr(
        migration_module, "_atomic_replace_bytes", replace_empty_directory
    )

    with pytest.raises(MigrationError, match="directory postimage"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=source_skills
        )

    assert replaced
    assert empty_directory.is_dir()
    metadata = empty_directory.stat()
    assert (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_ctime_ns,
    ) == replacement_postimage


def test_apply_preserves_directory_replaced_after_rollback_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    source_skills = migration_skills(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    empty_directory = root / ".skills/wiki-ingest/references/empty/nested"
    original_replace = migration_module._atomic_replace_bytes
    original_read_directory = migration_module._read_directory_postimage
    original_rollback = migration_module._rollback_targets
    rollback_started = False
    page_applied = False
    failed = False
    replaced = False
    replacement_postimage: tuple[int, int, int] | None = None

    def fail_after_page(path: Path, data: bytes, **kwargs) -> None:
        nonlocal failed, page_applied
        if page_applied and not failed:
            failed = True
            raise OSError("post-validation rollback race")
        original_replace(path, data, **kwargs)
        if path == page:
            page_applied = True

    def mark_rollback(*args, **kwargs):
        nonlocal rollback_started
        rollback_started = True
        return original_rollback(*args, **kwargs)

    def replace_after_validation(repo: Path, directory: Path):
        nonlocal replaced, replacement_postimage
        postimage = original_read_directory(repo, directory)
        if rollback_started and directory == empty_directory and not replaced:
            replacement = empty_directory.parent / "concurrent-replacement"
            displaced = empty_directory.parent / "displaced-owned-directory"
            replacement.mkdir(mode=0o755)
            replacement.chmod(0o755)
            empty_directory.rename(displaced)
            replacement.rename(empty_directory)
            metadata = empty_directory.stat()
            replacement_postimage = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_ctime_ns,
            )
            replaced = True
        return postimage

    monkeypatch.setattr(migration_module, "_atomic_replace_bytes", fail_after_page)
    monkeypatch.setattr(migration_module, "_rollback_targets", mark_rollback)
    monkeypatch.setattr(
        migration_module, "_read_directory_postimage", replace_after_validation
    )

    with pytest.raises(MigrationError, match="post-validation rollback race"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=source_skills
        )

    assert replaced
    assert replacement_postimage is not None
    assert any(
        child.is_dir()
        and (
            child.stat().st_dev,
            child.stat().st_ino,
            child.stat().st_ctime_ns,
        )
        == replacement_postimage
        for child in empty_directory.parent.iterdir()
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable modes are not portable")
def test_apply_rollback_restores_original_file_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, page = make_legacy_repo(tmp_path)
    page.chmod(0o750)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_replace = migration_module._atomic_replace_bytes
    page_applied = False
    failed = False

    def fail_after_page(path: Path, data: bytes, **kwargs) -> None:
        nonlocal failed, page_applied
        if page_applied and not failed:
            failed = True
            raise OSError("fail after page")
        original_replace(path, data, **kwargs)
        if path == page:
            page_applied = True

    monkeypatch.setattr(migration_module, "_atomic_replace_bytes", fail_after_page)

    with pytest.raises(MigrationError, match="rolled back: fail after page"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert page.stat().st_mode & 0o777 == 0o750


def test_apply_preserves_sources_field_comments(tmp_path: Path) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    page.write_text(
        "---\n"
        "title: A\n"
        "category: concepts\n"
        "tags: [example]\n"
        "sources: # provenance header\n"
        "  # source note\n"
        f'  - "{source}" # source tail\n'
        "\n"
        "created: 2026-08-07\n"
        "updated: 2026-08-07\n"
        "---\n"
        "# A\n",
        encoding="utf-8",
    )
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    apply_migration(
        plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
    )

    migrated = page.read_text(encoding="utf-8")
    assert "# provenance header" in migrated
    assert "# source note" in migrated
    assert "# source tail" in migrated
    assert str(source) not in migrated


def test_apply_preserves_comment_after_unquoted_source_with_apostrophe(
    tmp_path: Path,
) -> None:
    root, sources, vault, source, page = make_legacy_repo(tmp_path)
    apostrophe_source = sources / "O'Brien.md"
    source.rename(apostrophe_source)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            str(source), f"{apostrophe_source} # keep me"
        ),
        encoding="utf-8",
    )
    manifest_path = vault / ".manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][str(apostrophe_source)] = manifest["sources"].pop(
        str(source)
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = analyze_migration(root=root, vault=vault, source_root=sources)

    apply_migration(
        plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
    )

    migrated = page.read_text(encoding="utf-8")
    assert '  - "sources/O\'Brien.md" # keep me' in migrated
    assert str(apostrophe_source) not in migrated


def test_apply_refuses_owner_file_change_while_building_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_build = migration_module._build_migration_candidates

    def change_owner_after_build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        (root / "AGENTS.md").write_text("owner edit\n", encoding="utf-8")
        return result

    monkeypatch.setattr(
        migration_module, "_build_migration_candidates", change_owner_after_build
    )

    with pytest.raises(MigrationError, match="changed while building candidates"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert (root / "AGENTS.md").read_bytes() == b"owner edit\n"
    assert not (root / ".obsidian-wiki").exists()


def test_apply_rolls_back_when_source_changes_after_candidate_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_verify = migration_module._verify_candidate_dependencies
    changed = False

    def change_after_full_verification(
        root: Path, dependencies: dict[str, bytes | None]
    ) -> None:
        nonlocal changed
        original_verify(root, dependencies)
        if not changed and "sources/a.md" in dependencies:
            source.write_text("concurrent source edit\n", encoding="utf-8")
            changed = True

    monkeypatch.setattr(
        migration_module,
        "_verify_candidate_dependencies",
        change_after_full_verification,
    )

    with pytest.raises(MigrationError, match="changed while building candidates"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert changed
    assert source.read_bytes() == b"concurrent source edit\n"
    assert not (root / ".obsidian-wiki").exists()


def test_apply_rolls_back_after_parent_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    before_directories = {
        path.relative_to(root) for path in root.rglob("*") if path.is_dir()
    }
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_fsync = migration_module._fsync_open_parent
    failed = False

    def fail_once(parent_fd: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("directory fsync failed")
        original_fsync(parent_fd)

    monkeypatch.setattr(migration_module, "_fsync_open_parent", fail_once)

    with pytest.raises(
        MigrationError, match="rolled back: directory fsync failed"
    ):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    after_directories = {
        path.relative_to(root) for path in root.rglob("*") if path.is_dir()
    }
    assert after == before
    assert after_directories == before_directories


@pytest.mark.skipif(os.name == "nt", reason="dir_fd identity check is POSIX-only")
def test_atomic_replace_rejects_parent_renamed_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    parent = root / ".skills"
    parent.mkdir(parents=True)
    target = parent / "managed.md"
    moved = root / ".skills-moved"
    original_open = migration_module._open_parent_fd
    swapped = False

    def rename_after_open(root: Path, path: Path) -> int:
        nonlocal swapped
        descriptor = original_open(root, path)
        if not swapped:
            parent.rename(moved)
            parent.mkdir()
            swapped = True
        return descriptor

    monkeypatch.setattr(migration_module, "_open_parent_fd", rename_after_open)

    with pytest.raises(MigrationError, match="parent changed during apply"):
        migration_module._atomic_replace_bytes(
            target, b"managed\n", root=root, expected=None
        )

    assert not target.exists()
    assert not (moved / target.name).exists()


@pytest.mark.skipif(os.name == "nt", reason="dir_fd identity check is POSIX-only")
def test_atomic_replace_preserves_competitor_in_renamed_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    parent = root / ".skills"
    parent.mkdir(parents=True)
    target = parent / "managed.md"
    moved = root / ".skills-moved"
    original_open = migration_module._open_parent_fd
    original_matches = migration_module._parent_fd_matches
    competitor = b"competitor\n"
    swapped = False
    competed = False

    def rename_after_open(root: Path, path: Path) -> int:
        nonlocal swapped
        descriptor = original_open(root, path)
        if not swapped:
            parent.rename(moved)
            parent.mkdir()
            swapped = True
        return descriptor

    def replace_before_identity_check(root: Path, path: Path, parent_fd: int) -> bool:
        nonlocal competed
        if not competed:
            (moved / path.name).write_bytes(competitor)
            competed = True
        return original_matches(root, path, parent_fd)

    monkeypatch.setattr(migration_module, "_open_parent_fd", rename_after_open)
    monkeypatch.setattr(
        migration_module, "_parent_fd_matches", replace_before_identity_check
    )

    with pytest.raises(MigrationError, match="local restoration failed"):
        migration_module._atomic_replace_bytes(
            target, b"managed\n", root=root, expected=None
        )

    assert competed
    assert (moved / target.name).read_bytes() == competitor


@pytest.mark.skipif(os.name == "nt", reason="dir_fd identity check is POSIX-only")
def test_apply_detects_parent_renamed_after_helper_identity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_matches = migration_module._parent_fd_matches
    moved = root / ".skills-raced"
    swapped = False

    def rename_after_check(root: Path, path: Path, parent_fd: int) -> bool:
        nonlocal swapped
        matches = original_matches(root, path, parent_fd)
        if matches and not swapped and path.relative_to(root).parts[0] == ".skills":
            skills = root / ".skills"
            skills.rename(moved)
            skills.mkdir()
            for directory in sorted(
                (entry for entry in moved.rglob("*") if entry.is_dir()),
                key=lambda entry: len(entry.parts),
            ):
                (skills / directory.relative_to(moved)).mkdir(exist_ok=True)
            swapped = True
        return matches

    monkeypatch.setattr(
        migration_module, "_parent_fd_matches", rename_after_check
    )

    with pytest.raises(MigrationError, match="rollback was incomplete") as caught:
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert swapped
    assert "migration postimage" in str(caught.value)
    assert any(path.is_file() for path in moved.rglob("*"))


def test_apply_writes_pages_before_manifest_shards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_replace = migration_module._atomic_replace_bytes
    writes: list[str] = []

    def record_write(path: Path, data: bytes, **kwargs) -> None:
        writes.append(path.relative_to(root).as_posix())
        original_replace(path, data, **kwargs)

    monkeypatch.setattr(migration_module, "_atomic_replace_bytes", record_write)

    apply_migration(
        plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
    )

    assert writes.index("wiki/concepts/a.md") < writes.index(
        "wiki/.manifest/sources/a.md.json"
    )


def test_apply_detects_stale_shard_appearing_during_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    original_verify = migration_module._verify_source_dependencies
    stale = vault / ".manifest/sources/stale.md.json"
    calls = 0

    def insert_before_marker(*args, **kwargs) -> None:
        nonlocal calls
        original_verify(*args, **kwargs)
        calls += 1
        if calls == 2:
            stale.write_text("stale shard\n", encoding="utf-8")

    monkeypatch.setattr(
        migration_module, "_verify_source_dependencies", insert_before_marker
    )

    with pytest.raises(MigrationError, match="shard tree changed during apply"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert stale.read_bytes() == b"stale shard\n"


def test_apply_never_overwrites_planned_shard_appearing_during_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sources, vault, _source, _page = make_legacy_repo(tmp_path)
    plan = analyze_migration(root=root, vault=vault, source_root=sources)
    planned = vault / ".manifest/sources/a.md.json"
    competitor = b"planned competitor\n"
    original_preimage = migration_module._target_preimage
    inserted = False

    def insert_planned(path: Path, *, root: Path) -> bytes | None:
        nonlocal inserted
        if path == planned and not inserted:
            path.parent.mkdir(parents=True)
            path.write_bytes(competitor)
            inserted = True
        return original_preimage(path, root=root)

    monkeypatch.setattr(migration_module, "_target_preimage", insert_planned)

    with pytest.raises(MigrationError, match="planned manifest shard appeared"):
        apply_migration(
            plan, installed_version="2026.8", source_skills=migration_skills(tmp_path)
        )

    assert inserted
    assert planned.read_bytes() == competitor


def _repository_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_cli_migrate_defaults_to_read_only_json_and_resolves_paths_from_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _sources, _vault, source, _page = make_legacy_repo(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    before = _repository_files(root)
    monkeypatch.chdir(elsewhere)

    result = main(
        [
            "repo",
            "migrate",
            "--root",
            str(root),
            "--vault",
            "wiki",
            "--sources",
            "sources",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["status"] == "ready"
    assert payload["mode"] == "dry-run"
    assert payload["root"] == "."
    assert payload["vault"] == "wiki"
    assert payload["source_root"] == "sources"
    assert payload["source_mappings"] == [[str(source.resolve()), "sources/a.md"]]
    assert _repository_files(root) == before


def test_cli_migrate_human_dry_run_has_sections_and_exact_apply_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _sources, _vault, _source, _page = make_legacy_repo(tmp_path)

    result = main(
        [
            "repo",
            "migrate",
            "--root",
            str(root),
            "--vault",
            "wiki",
            "--sources",
            "sources",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    for heading in (
        "Mappings:",
        "Page updates:",
        "Manifest shards:",
        "Warnings:",
        "Blockers:",
    ):
        assert heading in output
    assert (
        f"obsidian-wiki repo migrate --root {root} --vault wiki "
        "--sources sources --apply"
    ) in output


@pytest.mark.skipif(os.name == "nt", reason="shlex models the POSIX shell output")
def test_cli_migrate_apply_command_quotes_shell_metacharacters_exactly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unusual_parent = tmp_path / "$USER `do-not-run` spaces"
    unusual_parent.mkdir()
    root, _sources, _vault, _source, _page = make_legacy_repo(unusual_parent)

    result = main(
        [
            "repo",
            "migrate",
            "--root",
            str(root),
            "--vault",
            "wiki",
            "--sources",
            "sources",
        ]
    )

    output = capsys.readouterr().out
    command = output.split("Apply with:\n  ", 1)[1].strip()
    assert result == 0
    assert shlex.split(command) == [
        "obsidian-wiki",
        "repo",
        "migrate",
        "--root",
        str(root),
        "--vault",
        "wiki",
        "--sources",
        "sources",
        "--apply",
    ]


def test_cli_migrate_dry_run_reports_blockers_as_json_without_apply_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _sources, vault, _source, _page = make_legacy_repo(tmp_path)
    external = tmp_path / "external.md"
    external.write_text("external", encoding="utf-8")
    manifest = json.loads((vault / ".manifest.json").read_text())
    manifest["sources"][str(external)] = {
        "content_hash": "sha256:external",
        "pages_produced": [],
    }
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = main(
        [
            "repo",
            "migrate",
            "--root",
            str(root),
            "--vault",
            "wiki",
            "--sources",
            "sources",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 1
    assert payload["status"] == "blocked"
    assert "external-source" in {
        blocker["code"] for blocker in payload["blockers"]
    }
    assert "apply_command" not in payload


def test_cli_migrate_apply_refuses_blockers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _sources, vault, _source, _page = make_legacy_repo(tmp_path)
    external = tmp_path / "external.md"
    external.write_text("external", encoding="utf-8")
    manifest = json.loads((vault / ".manifest.json").read_text())
    only_entry = next(iter(manifest["sources"].values()))
    manifest["sources"] = {str(external): only_entry}
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = _repository_files(root)

    result = main(
        [
            "repo",
            "migrate",
            "--root",
            str(root),
            "--vault",
            "wiki",
            "--sources",
            "sources",
            "--apply",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 1
    assert payload["status"] == "blocked"
    assert payload["mode"] == "apply"
    assert _repository_files(root) == before


def test_cli_migrate_apply_reports_changes_and_never_commits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _sources, _vault, _source, _page = make_legacy_repo(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Migration Test",
            "-c",
            "user.email=migration@example.invalid",
            "commit",
            "-qm",
            "legacy baseline",
        ],
        check=True,
    )
    before_head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    )

    result = main(
        [
            "repo",
            "migrate",
            "--root",
            str(root),
            "--vault",
            "wiki",
            "--sources",
            "sources",
            "--apply",
            "--json",
            "--pretty",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    after_head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    )
    assert result == 0
    assert payload["status"] == "applied"
    assert "wiki/.manifest.json" in payload["changed_files"]
    assert payload["removed_files"] == ["wiki/hot.md"]
    assert payload["backup_dir"].startswith(".obsidian-wiki/local/migrations/")
    assert "\n  \"" in captured.out
    assert after_head == before_head


def test_cli_migrate_apply_refuses_preimage_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _sources, vault, _source, _page = make_legacy_repo(tmp_path)
    original_analyze = cli_module.analyze_migration

    def analyze_then_drift(**kwargs):
        plan = original_analyze(**kwargs)
        (vault / ".manifest.json").write_text("{}\n", encoding="utf-8")
        return plan

    monkeypatch.setattr(cli_module, "analyze_migration", analyze_then_drift)

    result = main(
        [
            "repo",
            "migrate",
            "--root",
            str(root),
            "--vault",
            "wiki",
            "--sources",
            "sources",
            "--apply",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 1
    assert payload["status"] == "error"
    assert "changed since analysis" in payload["error"]
    assert not (root / ".obsidian-wiki/config.toml").exists()


def test_cli_migrate_reports_already_portable_without_rewriting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _sources, _vault, _source, _page = make_legacy_repo(tmp_path)
    base_args = [
        "repo",
        "migrate",
        "--root",
        str(root),
        "--vault",
        "wiki",
        "--sources",
        "sources",
        "--apply",
        "--json",
    ]
    assert main(base_args) == 0
    capsys.readouterr()
    before = _repository_files(root)

    result = main(base_args)

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["status"] == "already-portable"
    assert _repository_files(root) == before

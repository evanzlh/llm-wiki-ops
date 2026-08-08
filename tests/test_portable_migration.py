from __future__ import annotations

import json
import os
from pathlib import Path

from obsidian_wiki.migration import analyze_migration


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

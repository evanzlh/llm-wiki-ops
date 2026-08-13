"""Tests for vault linting."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.lint import (
    ALLOWED_RELATIONSHIP_TYPES,
    TRUST_REQUIRED_FRONTMATTER,
    lint_vault,
)
from obsidian_wiki.page_graph import parse_page_text
from obsidian_wiki.trust import (
    ALLOWED_LIFECYCLES,
    TRUST_REQUIRED_FIELD_ALLOWLIST,
    build_trust_ledger,
    write_trust_ledger,
)


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    probe = link.parent / ".symlink-probe"
    try:
        probe.symlink_to(target, target_is_directory=directory)
        probe.unlink()
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        try:
            probe.unlink()
        except OSError:
            pass
        pytest.skip(f"symlinks are unavailable: {exc}")


def _portable_cli_context(
    vault: Path, settings: dict[str, str] | None = None
) -> Path:
    root = vault.parent
    vault.mkdir(parents=True, exist_ok=True)
    (root / ".obsidian-wiki").mkdir(exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    (root / ".skills").mkdir(exist_ok=True)
    nested = root / "work/nested"
    nested.mkdir(parents=True, exist_ok=True)
    setting_lines = "".join(
        f'{key} = "{value}"\n' for key, value in (settings or {}).items()
    )
    (root / ".obsidian-wiki/config.toml").write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "{vault.name}"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
[settings]
{setting_lines}''',
        encoding="utf-8",
    )
    return nested


def _page(
    vault: Path,
    relpath: str,
    *,
    title: str | None = None,
    summary: str | None = "Short summary.",
    tags: str = "[test]",
    sources: str = "[manual]",
    created: str = "2026-07-01",
    updated: str = "2026-07-01",
    links: list[str] | None = None,
    include_frontmatter: bool = True,
    include_trust_fields: bool = True,
) -> Path:
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if include_frontmatter:
        lines.extend(
            [
                "---",
                f"title: {title or path.stem}",
                "category: concepts",
                f"tags: {tags}",
                f"sources: {sources}",
                f"created: {created}",
                f"updated: {updated}",
            ]
        )
        if include_trust_fields:
            lines.extend(["base_confidence: 0.80", "lifecycle: reviewed"])
        if summary is not None:
            lines.append(f"summary: {summary}")
        lines.append("---")
    lines.append(f"# {title or path.stem}")
    for link in links or []:
        lines.append(f"[[{link}]]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(
    home: Path, *args: str, cwd: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        cwd=cwd,
    )


def _run_at(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(Path(__file__).parents[1])
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        cwd=cwd,
    )


def test_lint_vault_passes_clean_graph(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "index.md", links=["alpha"])
    _page(vault, "log.md", links=["alpha"])
    _page(vault, "hot.md", links=["alpha"])
    _page(vault, "concepts/alpha.md", links=["beta"])
    _page(vault, "concepts/beta.md", links=["alpha"])
    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    write_trust_ledger(vault / "_meta" / "trust-ledger.json", ledger, vault=vault)

    report = lint_vault(vault)

    assert report["status"] == "pass"
    assert report["findings"]["broken_links"] == []
    assert report["findings"]["missing_frontmatter"] == []


def test_lint_vault_fails_on_broken_links_and_missing_frontmatter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", links=["ghost"])
    _page(vault, "concepts/beta.md", include_frontmatter=False)

    report = lint_vault(vault)

    assert report["status"] == "fail"
    assert report["findings"]["broken_links"] == [{"page": "concepts/alpha.md", "target": "ghost"}]
    assert any(item["page"] == "concepts/beta.md" for item in report["findings"]["missing_frontmatter"])
    assert report["findings"]["typed_relationship_issues"] == []


@pytest.mark.parametrize("kind", ["ordinary", "symlink"])
def test_lint_vault_prunes_relative_subtree_without_hiding_other_findings(
    tmp_path: Path, kind: str
) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", links=["ghost"])
    legacy = vault / "journal" / "operations"
    legacy.parent.mkdir()
    if kind == "ordinary":
        legacy.mkdir()
        (legacy / "malformed.md").write_text(
            "# Missing frontmatter with [[legacy ghost]]\n", encoding="utf-8"
        )
    else:
        external = tmp_path / "external-legacy"
        external.mkdir()
        (external / "malformed.md").write_text("# External\n", encoding="utf-8")
        _symlink_or_skip(legacy, external, directory=True)

    report = lint_vault(
        vault,
        require_trust_ledger=False,
        skip_relative_subtrees={("journal", "operations")},
    )

    assert report["findings"]["broken_links"] == [
        {"page": "concepts/alpha.md", "target": "ghost"}
    ]
    assert all(
        not item.get("page", "").startswith("journal/operations/")
        for finding in report["findings"].values()
        if isinstance(finding, list)
        for item in finding
        if isinstance(item, dict)
    )


def test_lint_does_not_hide_unsupported_personal_artifact_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    for name in ("_archives", "_raw", "_readouts", "_staging"):
        _page(vault, f"{name}/legacy.md", include_frontmatter=False)

    report = lint_vault(vault)

    assert {
        item["page"] for item in report["findings"]["missing_frontmatter"]
    } == {
        "_archives/legacy.md",
        "_raw/legacy.md",
        "_readouts/legacy.md",
        "_staging/legacy.md",
    }


def test_lint_rejects_external_symlink_without_leaking_content(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    raw = vault / "_raw"
    raw.mkdir(parents=True)
    secret = tmp_path / "secret.md"
    secret.write_text("# SECRET-MARKER\n", encoding="utf-8")
    (raw / "leak.md").symlink_to(secret)

    with pytest.raises(RuntimeError, match="symlink") as raised:
        lint_vault(vault)

    assert "SECRET-MARKER" not in str(raised.value)


def test_lint_ignores_unrelated_non_markdown_symlink(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    secret = tmp_path / "image.png"
    secret.write_text("SECRET-MARKER\n", encoding="utf-8")
    (vault / "image.png").symlink_to(secret)

    report = lint_vault(vault, require_trust_ledger=False)

    assert "SECRET-MARKER" not in json.dumps(report)


def test_lint_preserves_strict_utf8_trust_validation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/invalid.md")
    page.write_bytes(page.read_bytes() + b"\xff")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["trust_metadata_errors"] == [
        {"page": "concepts/invalid.md", "issue": "page is not valid UTF-8: concepts/invalid.md"}
    ]


def test_parse_page_text_preserves_wikilink_and_markdown_targets() -> None:
    parsed = parse_page_text(
        "concepts/alpha.md",
        """---
title: Alpha
summary: An alpha page.
---
[[concepts/Beta|Beta]]
[Gamma](../references/gamma.md#details)
""",
    )

    assert parsed.path == "concepts/alpha.md"
    assert parsed.node_id == "concepts/alpha"
    assert parsed.slug == "alpha"
    assert parsed.title == "Alpha"
    assert parsed.links == ("beta", "gamma")
    assert tuple(field.name for field in fields(parsed)) == (
        "path",
        "node_id",
        "slug",
        "title",
        "summary",
        "fields",
        "links",
        "text",
    )


def test_lint_vault_reports_broken_links_after_page_graph_extraction(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", links=["missing"])

    report = lint_vault(vault)

    assert report["findings"]["broken_links"] == [
        {"page": "concepts/alpha.md", "target": "missing"}
    ]


def test_lint_vault_warns_on_duplicates_missing_summaries_and_orphans(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", title="Same Title", summary=None)
    _page(vault, "references/beta.md", title="Same Title")
    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    write_trust_ledger(vault / "_meta" / "trust-ledger.json", ledger, vault=vault)

    report = lint_vault(vault)

    assert report["status"] == "warn"
    assert report["findings"]["duplicate_titles"]
    assert "concepts/alpha.md" in report["findings"]["missing_summaries"]
    assert "references/beta.md" in report["findings"]["orphan_pages"]


def test_lint_cli_uses_configured_vault_and_strict_mode(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", summary=None)

    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    write_trust_ledger(vault / "_meta" / "trust-ledger.json", ledger)
    nested = _portable_cli_context(vault)

    proc = _run(home, "lint", "--json", "--strict", cwd=nested)

    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["status"] == "warn"
    assert "concepts/alpha.md" in data["findings"]["missing_summaries"]


def test_lint_cli_prefers_portable_vault_and_schema_from_nested_cwd(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root = tmp_path / "knowledge"
    portable_vault = root / "wiki"
    global_vault = tmp_path / "global-vault"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / ".skills").mkdir()
    portable_vault.mkdir()
    portable_page = _page(
        portable_vault, "concepts/portable.md", summary=None
    )
    portable_page.write_text(
        portable_page.read_text(encoding="utf-8").replace(
            "lifecycle: reviewed", "lifecycle: active"
        ),
        encoding="utf-8",
    )
    _page(global_vault, "concepts/global.md")
    portable_config = root / ".obsidian-wiki/config.toml"
    portable_config.write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
[settings]
OBSIDIAN_ALLOWED_LIFECYCLES = ["active"]
''',
        encoding="utf-8",
    )
    global_config = home / ".obsidian-wiki/config"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(
        f'OBSIDIAN_VAULT_PATH="{global_vault}"\n', encoding="utf-8"
    )
    nested = root / "work/nested"
    nested.mkdir(parents=True)

    proc = _run_at(home, nested, "lint", "--json")

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert "concepts/portable.md" in report["findings"]["missing_summaries"]
    assert "concepts/global.md" not in report["findings"]["missing_summaries"]
    assert "active" in report["schema"]["allowed_lifecycles"]
    assert report["schema"]["source"] == f"config:{portable_config.resolve()}"
    assert "context_warnings" not in report


def test_lint_cli_rejects_explicit_vault_in_portable_repository(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "knowledge"
    portable_vault = root / "wiki"
    explicit_vault = tmp_path / "explicit-vault"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / ".skills").mkdir()
    portable_vault.mkdir()
    _page(explicit_vault, "concepts/explicit.md")
    (root / ".obsidian-wiki/config.toml").write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    nested = root / "work/nested"
    nested.mkdir(parents=True)

    proc = _run_at(home, nested, "lint", str(explicit_vault), "--json")

    assert proc.returncode == 2
    assert "unrecognized arguments" in proc.stderr


def test_lint_vault_legacy_pages_without_trust_schema_warn_by_default(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", include_trust_fields=False)

    report = lint_vault(vault)

    assert report["status"] == "warn"
    assert report["findings"]["missing_frontmatter"] == []
    assert report["findings"]["confidence_missing_fields"] == [
        {"page": "concepts/alpha.md", "missing": ["base_confidence", "lifecycle"]}
    ]
    assert any(item["issue"] == "ledger_missing" for item in report["findings"]["confidence_ledger_errors"])


def test_lint_vault_missing_ledger_is_warning_not_failure_by_default(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")

    report = lint_vault(vault)

    assert report["status"] == "warn"
    assert report["findings"]["confidence_ledger_errors"]


def test_lint_vault_strict_trust_fails_on_missing_fields_and_ledger(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", include_trust_fields=False)

    report = lint_vault(vault, strict_trust=True)

    assert report["status"] == "fail"


def test_lint_vault_strict_trust_still_passes_clean_reviewed_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", links=["beta"])
    _page(vault, "concepts/beta.md", links=["alpha"])
    ledger = build_trust_ledger(vault, reviewed_at="2026-07-12T17:38:39+07:00")
    write_trust_ledger(vault / "_meta" / "trust-ledger.json", ledger, vault=vault)

    report = lint_vault(vault, strict_trust=True)

    assert report["status"] == "pass"


def test_lint_cli_strict_trust_flag_fails_portable_vault(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md", include_trust_fields=False)

    nested = _portable_cli_context(vault)

    default_proc = _run(home, "lint", "--json", cwd=nested)
    assert default_proc.returncode == 0
    assert json.loads(default_proc.stdout)["status"] == "warn"

    strict_proc = _run(home, "lint", "--json", "--strict-trust", cwd=nested)
    assert strict_proc.returncode == 1
    assert json.loads(strict_proc.stdout)["status"] == "fail"


def test_owner_schema_accepts_extensions_optional_trust_and_reports_source(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    extensions = [
        ("active", "synthesizes"),
        ("confirmed", "builds_on"),
        ("stub", "complements"),
        ("active", "refines"),
        ("confirmed", "contrasts_with"),
    ]
    names = [f"page-{index}" for index in range(len(extensions))]
    for index, ((lifecycle, relationship), name) in enumerate(zip(extensions, names)):
        target = names[(index + 1) % len(names)]
        page = _page(vault, f"concepts/{name}.md", links=[target])
        text = page.read_text()
        text = text.replace("base_confidence: 0.80\n", "")
        text = text.replace(
            "lifecycle: reviewed",
            f'lifecycle: {lifecycle}\nrelationships:\n  - type: {relationship}\n    target: "[[concepts/{target}]]"',
        )
        page.write_text(text)

    report = lint_vault(
        vault,
        require_trust_ledger=False,
        allowed_lifecycles={"active", "confirmed", "stub"},
        allowed_relationship_types={item[1] for item in extensions},
        required_trust_fields=("updated",),
        schema_source="wiki/AGENTS.md",
    )

    assert report["findings"]["confidence_missing_fields"] == []
    assert report["findings"]["typed_relationship_issues"] == []
    assert report["schema"] == {
        "source": "wiki/AGENTS.md",
        "allowed_lifecycles": ["active", "confirmed", "stub"],
        "allowed_relationship_types": sorted(item[1] for item in extensions),
        "required_trust_fields": ["updated"],
    }


def test_folded_summary_before_typed_relationships_is_parsed_compatibly(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    alpha = _page(vault, "concepts/alpha.md", links=["beta"])
    _page(vault, "concepts/beta.md", links=["alpha"])
    alpha.write_text(
        alpha.read_text().replace(
            "summary: Short summary.",
            "summary: >-\n"
            "  Folded summary text\n"
            "  remains visible to lint.\n"
            "relationships:\n"
            '  - target: "[[concepts/beta]]"\n'
            "    type: related_to",
        )
    )

    report = lint_vault(vault, require_trust_ledger=False)

    assert "concepts/alpha.md" not in report["findings"]["missing_summaries"]
    assert report["findings"]["typed_relationship_issues"] == []


def test_unknown_relationship_field_is_one_malformed_typed_issue(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    alpha = _page(vault, "concepts/alpha.md", links=["beta"])
    _page(vault, "concepts/beta.md", links=["alpha"])
    alpha.write_text(
        alpha.read_text().replace(
            "summary: Short summary.",
            "summary: Short summary.\n"
            "relationships:\n"
            '  - target: "[[concepts/beta]]"\n'
            "    type: related_to\n"
            "    weight: 5",
        )
    )

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["findings"]["typed_relationship_issues"] == [
        {
            "page": "concepts/alpha.md",
            "index": 0,
            "issue": "malformed_relationship_entry",
        }
    ]


def test_invalid_configured_required_trust_field_fails_closed_for_all_cli_paths(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    nested = _portable_cli_context(
        vault,
        {"OBSIDIAN_REQUIRED_TRUST_FIELDS": "updated,base_confidnce"},
    )
    expected = (
        "error: invalid OBSIDIAN_REQUIRED_TRUST_FIELDS value(s): base_confidnce; "
        "allowed values: base_confidence, lifecycle, lifecycle_changed, updated"
    )
    commands = (
        ("lint", "--json"),
        (
            "trust-record",
            "--all",
            "--reviewed-at",
            "2026-08-05T12:00:00+09:00",
            "--approved",
            "--json",
        ),
        ("trust-check", "--json"),
    )

    for command in commands:
        proc = _run_at(home, nested, *command)
        assert proc.returncode == 1, (command, proc.stdout, proc.stderr)
        assert proc.stdout == ""
        assert expected in proc.stderr
        assert "Traceback" not in proc.stderr

    assert not (vault / "_meta" / "trust-ledger.json").exists()


def test_empty_relationship_cli_extension_cannot_hide_missing_relation_type(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    alpha = _page(vault, "concepts/alpha.md", links=["beta"])
    _page(vault, "concepts/beta.md", links=["alpha"])
    alpha.write_text(
        alpha.read_text().replace(
            "summary: Short summary.",
            'summary: Short summary.\nrelationships:\n  - type:\n    target: "[[concepts/beta]]"',
        )
    )
    nested = _portable_cli_context(vault)

    baseline = _run(home, "lint", "--json", cwd=nested)
    assert baseline.returncode == 0
    assert json.loads(baseline.stdout)["findings"]["typed_relationship_issues"] == [
        {"page": "concepts/alpha.md", "index": 0, "issue": "malformed_relationship_entry"}
    ]

    for value in ("", "   "):
        invalid = _run(
            home,
            "lint",
            "--json",
            "--allow-relationship-type",
            value,
            cwd=nested,
        )
        assert invalid.returncode == 1
        assert invalid.stdout == ""
        assert "error: invalid --allow-relationship-type value: must not be empty" in invalid.stderr
        assert "Traceback" not in invalid.stderr


def test_empty_cli_lifecycle_and_required_field_overrides_fail_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    nested = _portable_cli_context(vault)

    for value in ("", "   "):
        lifecycle = _run(
            home,
            "lint",
            "--json",
            "--allow-lifecycle",
            value,
            cwd=nested,
        )
        assert lifecycle.returncode == 1
        assert lifecycle.stdout == ""
        assert "error: invalid --allow-lifecycle value: must not be empty" in lifecycle.stderr
        assert "Traceback" not in lifecycle.stderr

    required = _run(
        home,
        "lint",
        "--json",
        "--required-trust-field",
        "",
        cwd=nested,
    )
    assert required.returncode == 2
    assert required.stdout == ""
    assert "invalid choice" in required.stderr
    assert "Traceback" not in required.stderr

    for value in ("", "   "):
        source = _run(
            home,
            "lint",
            "--json",
            "--schema-source",
            value,
            cwd=nested,
        )
        assert source.returncode == 1
        assert source.stdout == ""
        assert "error: invalid --schema-source value: must not be empty" in source.stderr
        assert "Traceback" not in source.stderr


def test_empty_configured_schema_values_fail_closed_for_all_cli_paths(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    commands = (
        ("lint", "--json"),
        (
            "trust-record",
            "--all",
            "--reviewed-at",
            "2026-08-05T12:00:00+09:00",
            "--approved",
            "--json",
        ),
        ("trust-check", "--json"),
    )

    for key in (
        "OBSIDIAN_ALLOWED_LIFECYCLES",
        "OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES",
        "OBSIDIAN_REQUIRED_TRUST_FIELDS",
        "OBSIDIAN_SCHEMA_SOURCE",
    ):
        nested = _portable_cli_context(vault, {key: "   "})
        for command in commands:
            invalid = _run_at(home, nested, *command)
            assert invalid.returncode == 1, (key, command, invalid.stdout, invalid.stderr)
            if key == "OBSIDIAN_SCHEMA_SOURCE":
                assert invalid.stderr == ""
                message = json.loads(invalid.stdout)["error"]["message"]
                assert "unsupported portable setting: OBSIDIAN_SCHEMA_SOURCE" in message
                assert "Traceback" not in invalid.stdout
            else:
                assert invalid.stdout == ""
                assert f"invalid {key} value: entries must not be empty" in invalid.stderr
                assert "Traceback" not in invalid.stderr

    assert not (vault / "_meta" / "trust-ledger.json").exists()


def test_blank_config_schema_source_cannot_be_masked_by_valid_cli_source(tmp_path: Path) -> None:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    nested = _portable_cli_context(
        vault, {"OBSIDIAN_SCHEMA_SOURCE": "   "}
    )
    ledger = vault / "_meta" / "trust-ledger.json"
    before = {
        path.relative_to(vault): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }
    commands = (
        ("lint", "--json", "--schema-source", "owner/AGENTS.md"),
        (
            "trust-record",
            "--all",
            "--reviewed-at",
            "2026-08-05T12:00:00+09:00",
            "--approved",
            "--json",
            "--schema-source",
            "owner/AGENTS.md",
        ),
        ("trust-check", "--json", "--schema-source", "owner/AGENTS.md"),
    )

    for command in commands:
        invalid = _run_at(home, nested, *command)
        assert invalid.returncode == 1, (command, invalid.stdout, invalid.stderr)
        assert invalid.stderr == ""
        assert "unsupported portable setting: OBSIDIAN_SCHEMA_SOURCE" in json.loads(
            invalid.stdout
        )["error"]["message"]
        assert "Traceback" not in invalid.stdout
        after = {
            path.relative_to(vault): path.read_bytes()
            for path in vault.rglob("*")
            if path.is_file()
        }
        assert after == before
        assert not ledger.exists()


def test_distributed_schema_config_contract_names_all_four_variables(tmp_path: Path) -> None:
    assert tmp_path.is_dir()
    root = Path(__file__).parents[1]
    variables = (
        "OBSIDIAN_ALLOWED_LIFECYCLES",
        "OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES",
        "OBSIDIAN_REQUIRED_TRUST_FIELDS",
        "OBSIDIAN_SCHEMA_SOURCE",
    )
    configuration = (root / "docs" / "configuration.md").read_text(encoding="utf-8")
    skills = root / "obsidian_wiki" / "_data" / "skills"
    lint_skill = (skills / "wiki-lint" / "SKILL.md").read_text(encoding="utf-8")
    llm_skill = (skills / "llm-wiki" / "SKILL.md").read_text(encoding="utf-8")
    capture_skill = (skills / "wiki-capture" / "SKILL.md").read_text(encoding="utf-8")

    for variable in variables:
        assert variable in configuration
        assert variable in lint_skill
    assert "not a repository setting" in configuration
    portable_settings = configuration.split("## Settings", 1)[1].split(
        "## Tracked and ignored state", 1
    )[0]
    assert "OBSIDIAN_SCHEMA_SOURCE" not in portable_settings.split(
        "Schema command inputs", 1
    )[0]
    assert "CLI flags > resolved environment/config values > framework defaults" in (
        " ".join(configuration.split())
    )
    assert "does not read environment variables or `.env` directly" in configuration
    assert "external wrapper" in configuration
    for value in (
        *ALLOWED_LIFECYCLES,
        *ALLOWED_RELATIONSHIP_TYPES,
        *TRUST_REQUIRED_FRONTMATTER,
        *TRUST_REQUIRED_FIELD_ALLOWLIST,
    ):
        assert f"`{value}`" in configuration
    for skill in (llm_skill, capture_skill):
        assert "nearest" in skill
        assert ".obsidian-wiki/config.toml" in skill
        assert "canonical" in skill
    assert (
        "CLI flags > resolved environment/config values > framework defaults"
        in " ".join(lint_skill.split())
    )
    assert "fails closed" in lint_skill


def test_owner_schema_still_rejects_unknown_typos(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md", links=["beta"])
    _page(vault, "concepts/beta.md", links=["alpha"])
    page.write_text(
        page.read_text()
        .replace("lifecycle: reviewed", "lifecycle: activ")
        .replace(
            "---\n# alpha",
            'relationships:\n  - type: synthesizez\n    target: "[[concepts/beta]]"\n---\n# alpha',
        )
    )

    report = lint_vault(
        vault,
        require_trust_ledger=False,
        allowed_lifecycles={"active", "confirmed", "stub"},
        allowed_relationship_types={"synthesizes"},
        required_trust_fields=("updated",),
        schema_source="wiki/AGENTS.md",
    )

    assert report["findings"]["typed_relationship_issues"][0]["issue"] == "invalid_type"


def test_default_schema_remains_framework_compatible(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["schema"]["source"] == "framework-defaults"
    assert report["schema"]["required_trust_fields"] == ["base_confidence", "lifecycle"]
    assert "related_to" in report["schema"]["allowed_relationship_types"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_optional_present_fifo_ledger_is_rejected_without_opening(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _page(vault, "concepts/alpha.md")
    ledger_path = vault / "_meta/trust-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    os.mkfifo(ledger_path)

    report = lint_vault(
        vault,
        require_trust_ledger=False,
        strict_trust=True,
    )

    assert report["status"] == "fail"
    assert report["findings"]["confidence_ledger_errors"][0]["issue"] == (
        "ledger_unreadable"
    )


def test_lifecycle_typo_fails_without_ledger_when_ledger_is_not_required(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    page.write_text(page.read_text().replace("lifecycle: reviewed", "lifecycle: reveiwed"))

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["status"] == "fail"
    assert report["findings"]["trust_metadata_errors"] == [
        {"page": "concepts/alpha.md", "issue": "invalid lifecycle: reveiwed"}
    ]


def test_present_invalid_confidence_fails_without_ledger_when_not_required(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    page = _page(vault, "concepts/alpha.md")
    page.write_text(page.read_text().replace("base_confidence: 0.80", "base_confidence: 1.4"))

    report = lint_vault(vault, require_trust_ledger=False)

    assert report["status"] == "fail"
    assert report["findings"]["trust_metadata_errors"] == [
        {"page": "concepts/alpha.md", "issue": "base_confidence is outside [0.0, 1.0]"}
    ]


def test_schema_source_is_portable_config_and_positional_vaults_are_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    local_vault = tmp_path / "local-vault"
    explicit_vault = tmp_path / "explicit-vault"
    for vault in (local_vault, explicit_vault):
        page = _page(vault, "concepts/alpha.md")
        page.write_text(page.read_text().replace("lifecycle: reviewed", "lifecycle: active"))

    portable_config = tmp_path / ".obsidian-wiki/config.toml"
    nested = _portable_cli_context(
        local_vault, {"OBSIDIAN_ALLOWED_LIFECYCLES": "active"}
    )

    explicit = _run_at(home, nested, "lint", str(explicit_vault), "--json")
    assert explicit.returncode == 2
    assert "unrecognized arguments" in explicit.stderr

    local = _run_at(home, nested, "lint", "--json")
    assert local.returncode == 0, local.stderr
    local_source = json.loads(local.stdout)["schema"]["source"]
    assert Path(local_source.removeprefix("config:")).resolve() == portable_config.resolve()

    named = _run_at(home, nested, "lint", "@owner", "--json")
    assert named.returncode == 2
    assert "unrecognized arguments" in named.stderr


def test_correction_contract_requires_temporal_authority_and_immutable_hash_check(tmp_path: Path) -> None:
    skill = (
        Path(__file__).parents[1]
        / "obsidian_wiki/_data/skills/wiki-capture/SKILL.md"
    ).read_text()
    for field in (
        "authority_class:",
        "verification_state:",
        "asserted_at:",
        "effective_at:",
        "as_of:",
        "consumer_propagation:",
        "source_pre_sha256",
        "source_post_sha256",
    ):
        assert field in skill
    for phrase in (
        "Before any candidate write",
        "safe ordinary-file read",
        "source_pre_sha256",
        "source_post_sha256",
        "before `transaction begin`",
        "identity or hash changed",
        "stop and restart",
        "complete source closure",
        "affected page",
    ):
        assert phrase in skill

    source = tmp_path / "immutable.jsonl"
    source.write_text('{"role":"user","content":"tool result"}\n', encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    correction = {
        "source_text_sha256": before,
        "speaker_type": "tool_result",
        "authority_class": "runtime",
        "verification_state": "verified",
        "asserted_at": "2026-08-05T10:00:00+09:00",
        "effective_at": "2026-08-05T10:00:00+09:00",
        "as_of": "2026-08-05T11:00:00+09:00",
        "consumer_propagation": {"ob": "complete", "kw": "open"},
    }
    assert correction["speaker_type"] != "user"
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert before == after == correction["source_text_sha256"]

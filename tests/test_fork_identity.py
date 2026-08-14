from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

import pytest

from obsidian_wiki import FORK_BASE_COMMIT, IMPLEMENTATION_ID, UPSTREAM_URL

ROOT = Path(__file__).resolve().parents[1]
FORMER_EXTERNAL_PROTOCOL = re.compile(
    r"(?i)(?:\.obsidian-wiki|"
    r"(?<![A-Za-z0-9_])obsidian-wiki(?![A-Za-z0-9_])|"
    r"(?i:OBSIDIAN_WIKI_[A-Z0-9_]+)|obsidian\s+wiki)"
)
UPSTREAM_ATTRIBUTION = "https://github.com/Ar9av/obsidian-wiki"
UPSTREAM_REPOSITORY = "Ar9av/obsidian-wiki"
OTHER_BINARY_ALLOWLIST = frozenset(
    {
        Path("extensions/brain-capture/assets/icon-128.png"),
        Path("extensions/brain-capture/assets/icon-16.png"),
        Path("extensions/brain-capture/assets/icon-48.png"),
        Path("extensions/brain-capture/assets/obsidian-brain.png"),
        Path("extensions/brain-capture/assets/store-screenshot-1280x800.png"),
    }
)

TRACKED_CATEGORIES = frozenset(
    {"production", "docs", "tests", "package-resource", "other"}
)
HISTORICAL_DOCUMENTS = frozenset(
    {
        Path("docs/superpowers/plans/2026-08-07-fork-identity-and-source-install.md"),
        Path("docs/superpowers/plans/2026-08-07-portable-config-and-setup.md"),
        Path("docs/superpowers/plans/2026-08-07-portable-migration-and-e2e.md"),
        Path("docs/superpowers/plans/2026-08-07-portable-transactions-and-derived-state.md"),
        Path("docs/superpowers/plans/2026-08-07-sharded-manifest-and-check.md"),
        Path("docs/superpowers/plans/2026-08-10-cli-runtime-context-and-recovery-guidance.md"),
        Path("docs/superpowers/plans/2026-08-10-portable-setup-installation-compatibility.md"),
        Path("docs/superpowers/plans/2026-08-10-source-reinstall-cache-refresh.md"),
        Path("docs/superpowers/plans/2026-08-11-portable-agent-preflight-cli.md"),
        Path("docs/superpowers/plans/2026-08-11-portable-agent-skill-docs.md"),
        Path("docs/superpowers/plans/2026-08-12-agent-context-and-full-skill-mirrors.md"),
        Path("docs/superpowers/plans/2026-08-12-portable-only.md"),
        Path("docs/superpowers/plans/2026-08-13-single-operation-log-and-tracked-hot.md"),
        Path("docs/superpowers/plans/2026-08-14-llmwikiops-independence-and-rename.md"),
        Path("docs/superpowers/plans/2026-08-14-llmwikiops-protocol-rename.md"),
        Path("docs/superpowers/specs/2026-08-07-portable-repo-mode-design.md"),
        Path("docs/superpowers/specs/2026-08-10-cli-runtime-context-and-recovery-guidance-design.md"),
        Path("docs/superpowers/specs/2026-08-10-portable-setup-installation-compatibility-design.md"),
        Path("docs/superpowers/specs/2026-08-11-portable-agent-ergonomics-design.md"),
        Path("docs/superpowers/specs/2026-08-12-agent-context-and-full-skill-mirrors-design.md"),
        Path("docs/superpowers/specs/2026-08-12-portable-only-design.md"),
        Path("docs/superpowers/specs/2026-08-13-single-operation-log-and-tracked-hot-design.md"),
        Path("docs/superpowers/specs/2026-08-14-llmwikiops-independence-and-rename-design.md"),
        Path("docs/superpowers/specs/2026-08-14-llmwikiops-protocol-rename-design.md"),
    }
)
NEGATIVE_TEST_MODULE_CONSTANTS = {
    Path("tests/test_asset_artifact_parity.py"): frozenset(
        {
            "REMOVED_DISTRIBUTION_PATHS",
            "FORMER_PROTOCOL_RESOURCE",
            "FORMER_PROTOCOL_PATH",
        }
    ),
    Path("tests/test_installation_policy.py"): frozenset(
        {"FORMER_PROTOCOL_RESOURCE"}
    ),
    Path("tests/test_portable_human_docs.py"): frozenset(
        {"FORMER_EXTERNAL_PROTOCOL"}
    ),
    Path("tests/test_portable_setup.py"): frozenset({"LEGACY_REFERENCE_BOOTSTRAP"}),
    Path("tests/test_portable_skill_protocol.py"): frozenset(
        {"FORBIDDEN_RUNTIME_TERMS", "LEGACY_RUNTIME_IDENTITY"}
    ),
    Path("tests/test_scripts_packaging.py"): frozenset(
        {"REMOVED_SCHEDULER_ARTIFACTS"}
    ),
    Path("tests/test_fork_identity.py"): frozenset(
        {"FORMER_EXTERNAL_PROTOCOL", "HARD_CUTOVER_PARAGRAPHS"}
    ),
}
NEGATIVE_TEST_FUNCTIONS = {
    Path("tests/test_asset_artifact_parity.py"): frozenset(
        {
            "test_distribution_assets_exactly_match_canonical_package_data",
            "test_archive_path_audit_rejects_former_protocol_filenames",
        }
    ),
    Path("tests/test_context_pack_docs.py"): frozenset(
        {"test_context_skill_uses_repo_discovery_and_requires_installed_cli"}
    ),
    Path("tests/test_doctor.py"): frozenset(
        {"test_doctor_wrong_portable_implementation_fails_without_global_fallback"}
    ),
    Path("tests/test_fork_identity.py"): frozenset(
        {
            "test_only_llmwikiops_cli_and_protocol_names_remain_supported",
            "test_protocol_audit_rejects_mutated_allowed_contexts",
            "test_current_document_allowance_rejects_old_name_appended_to_attribution_line",
            "test_manifest_audit_rejects_old_name_appended_to_license",
            "test_tracked_path_audit_rejects_former_protocol_filenames",
            "test_former_protocol_detector_rejects_all_external_protocol_variants",
            "test_former_protocol_managed_assets_are_absent_from_source_tree",
        }
    ),
    Path("tests/test_installation_policy.py"): frozenset(
        {
            "test_build_metadata_is_retained_for_uv_source_install",
            "test_bilingual_readmes_disclose_the_fork_and_only_source_install",
            "test_agents_routes_repository_authority_without_global_source_variables",
            "test_factory_uses_safe_managed_validator_from_nearest_repository",
            "test_no_unsupported_install_guidance_remains",
            "test_no_unsupported_install_guidance_in_user_facing_tooling",
            "test_uv_tool_install_survives_source_move",
        }
    ),
    Path("tests/test_portable_config.py"): frozenset(
        {"test_wrong_implementation_is_rejected"}
    ),
    Path("tests/test_portable_human_docs.py"): frozenset(
        {
            "test_current_docs_name_the_llmwikiops_protocol_and_hard_cutover",
            "test_old_product_reference_detection_covers_public_boundaries",
            "test_old_product_reference_detection_preserves_allowed_contexts",
            "test_hard_cutover_explanation_rejects_other_former_protocol_identifiers",
            "test_chinese_hard_cutover_explanation_rejects_other_former_protocol_identifiers",
            "test_current_docs_use_llmwikiops_identity",
        }
    ),
    Path("tests/test_portable_only_contract.py"): frozenset(
        {
            "test_bare_cli_prints_help_without_writing_repository_state",
            "test_portable_info_ignores_residual_legacy_home_config",
            "test_setup_rejects_removed_arguments",
            "test_retired_current_surfaces_have_one_complete_removal_inventory",
            "test_factory_uses_fresh_repository_skill_validator",
        }
    ),
    Path("tests/test_portable_setup.py"): frozenset(
        {
            "test_setup_portable_creates_repo_without_global_side_effects",
            "test_setup_hard_cutover_preserves_legacy_protocol_owner_content",
            "test_owner_seed_collision_rolls_back_only_setup_created_paths",
            "test_owner_seed_late_owner_write_is_preserved_after_partial_install",
            "test_owner_seed_parent_swap_never_writes_through_external_symlink",
            "test_owner_seed_root_replacement_after_preflight_is_not_initialized",
            "test_owner_seed_opened_parent_swap_is_not_treated_as_success",
            "test_owner_seed_attachment_failure_does_not_leak_recursive_descriptors",
            "test_owner_seed_parent_namespace_swap_preserves_bound_staging_evidence",
            "test_owner_seed_grandparent_replacement_does_not_initialize_moved_tree",
            "test_owner_seed_link_rewrite_without_collision_is_never_reported_success",
            "test_owner_seed_prebind_failure_never_cleans_replacement_staging",
            "test_owner_seed_populate_failure_preserves_replacement_staging_evidence",
            "test_owner_seed_staging_preflight_failure_preserves_replacement_evidence",
            "test_owner_seed_root_fstat_failure_does_not_leak_open_descriptors",
            "test_owner_seed_close_failure_still_attempts_remaining_descriptors",
            "test_former_portable_bootstrap_marker_is_owner_content_not_migration_input",
            "test_portable_config_is_relative_minimal_and_loadable",
        }
    ),
    Path("tests/test_portable_skill_protocol.py"): frozenset(
        {
            "test_identity_matcher_detects_disallowed_contexts",
            "test_identity_allowlist_preserves_exact_compatibility_contexts",
            "test_popup_keeps_the_stable_raw_picker_id_once_in_picker_options",
            "test_identity_location_reports_path_line_and_snippet",
            "test_maintenance_skills_are_repository_native",
            "test_status_contract_matches_real_graph_and_portable_manifest_layout",
        }
    ),
    Path("tests/test_portable_write_protocol.py"): frozenset(
        {"test_runtime_exports_only_new_repository_variable"}
    ),
    Path("tests/test_protocol_identity.py"): frozenset(
        {
            "test_legacy_state_config_is_not_discovered_or_modified",
            "test_local_state_must_use_the_canonical_protocol_path",
        }
    ),
    Path("tests/test_query_cli.py"): frozenset(
        {"test_query_cli_invalid_portable_config_never_falls_back_global"}
    ),
    Path("tests/test_transaction.py"): frozenset(
        {"test_transaction_excludes_llmwikiops_protocol_directory"}
    ),
}
AUDIT_HELPERS = frozenset(
    {
        "_is_allowed_current_document_match",
        "_is_explicit_negative_test_match",
    }
)
NEGATIVE_TEST_HELPERS = {
    Path("tests/test_portable_human_docs.py"): frozenset(
        {"_is_upstream_attribution", "_is_hard_cutover_explanation"}
    ),
    Path("tests/test_portable_skill_protocol.py"): frozenset(
        {"_is_ar9av_attribution", "is_allowed_legacy_identity"}
    ),
}
NEGATIVE_TEST_PATH_FIXTURES = frozenset[Path]()

def _line_at(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return text[start:] if end == -1 else text[start:end]


def _is_exact_upstream_attribution(text: str, match: re.Match[str]) -> bool:
    for attribution in (UPSTREAM_ATTRIBUTION, UPSTREAM_REPOSITORY):
        start = text.find(attribution)
        while start >= 0:
            end = start + len(attribution)
            leading = text[start - 1 : start]
            trailing = text[end : end + 1]
            valid_trailing = trailing in {
                "", " ", "\n", "\t", "'", '"', ")", "]", ">", ","
            }
            if attribution == UPSTREAM_ATTRIBUTION and trailing == "/":
                valid_trailing = text[end + 1 : end + 2] not in {
                    "", "/", " ", "\n", "\t"
                }
            if (
                leading in {"", " ", "\n", "\t", "'", '"', "(", "[", "<"}
                and valid_trailing
                and start <= match.start() < end
            ):
                return True
            start = text.find(attribution, start + 1)
    return False


def _unattributed_protocol_matches(text: str) -> list[re.Match[str]]:
    return [
        match
        for match in FORMER_EXTERNAL_PROTOCOL.finditer(text)
        if not _is_exact_upstream_attribution(text, match)
    ]


def disallowed_protocol_matches(path: Path, text: str) -> list[str]:
    """Return former external protocol names outside the exact upstream URL."""
    return [
        f"{path}:{text.count(chr(10), 0, match.start()) + 1}: {match.group()}"
        for match in _unattributed_protocol_matches(text)
    ]


HARD_CUTOVER_PARAGRAPHS = {
    Path("README.md"): (
        "**Protocol incompatibility.** The former `.obsidian-wiki/` state is not detected,\n"
        "read, migrated, or deleted. A repository containing only it is uninitialized; when\n"
        "both directories exist, `.llmwikiops/` is the only authority. Explicitly run\n"
        "`llmwikiops setup` and review its new files; do not manually copy former state."
    ),
    Path("README_ZH.md"): (
        "**协议不兼容。** 旧的 `.obsidian-wiki/` 状态不会检测、读取、迁移或删除。仅有该目录的仓库视为未初始化；"
        "两个目录同时存在时，只有 `.llmwikiops/` 是权威。请显式运行 `llmwikiops setup` 并审查新文件；"
        "不要手工复制旧状态。"
    ),
}


def _paragraph_start(text: str, offset: int) -> int:
    return text.rfind("\n\n", 0, offset) + 2


def _paragraph_at(text: str, offset: int) -> str:
    start = _paragraph_start(text, offset)
    end = text.find("\n\n", offset)
    return text[start:] if end == -1 else text[start:end]


def _is_allowed_current_document_match(
    relative: Path, text: str, match: re.Match[str]
) -> bool:
    paragraph = HARD_CUTOVER_PARAGRAPHS.get(relative)
    if paragraph is None or match.group() != ".obsidian-wiki":
        return False
    if text[match.start() : match.end() + 1] != ".obsidian-wiki/":
        return False
    paragraph_start = _paragraph_start(text, match.start())
    return (
        _paragraph_at(text, match.start()) == paragraph
        and match.start() == paragraph_start + paragraph.index(".obsidian-wiki/")
    )


def _is_explicit_negative_test_match(
    relative: Path, text: str, match: re.Match[str]
) -> bool:
    raw_line = _line_at(text, match.start())
    if raw_line.strip() == '_portable_config().replace(".llmwikiops/local", ".obsidian-wiki/local"),':
        return True
    if raw_line.strip() in {
        '"scripts/com.obsidian-wiki.daily-update.plist",',
        '"com.obsidian-wiki.daily-update.plist",',
        'r"~[\\\\/]\\.obsidian-wiki[\\\\/]config\\b",',
    }:
        return True
    line_number = text.count("\n", 0, match.start()) + 1
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first_line = min(
            [node.lineno] + [decorator.lineno for decorator in node.decorator_list]
        )
        if (
            node.name in NEGATIVE_TEST_FUNCTIONS.get(relative, frozenset())
            and first_line <= line_number <= node.end_lineno
        ):
            return True
        if (
            relative == Path("tests/test_fork_identity.py")
            and node.name in AUDIT_HELPERS
            and first_line <= line_number <= node.end_lineno
        ):
            return True
        if (
            node.name in NEGATIVE_TEST_HELPERS.get(relative, frozenset())
            and first_line <= line_number <= node.end_lineno
        ):
            return True
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            node.targets if isinstance(node, ast.Assign) else (node.target,)
        )
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        if (
            names & NEGATIVE_TEST_MODULE_CONSTANTS.get(relative, frozenset())
            and node.lineno <= line_number <= node.end_lineno
        ):
            return True
        if (
            relative == Path("tests/test_fork_identity.py")
            and "FORMER_EXTERNAL_PROTOCOL" in names
            and node.lineno <= line_number <= node.end_lineno
        ):
            return True
    return False


def _tracked_manifest() -> frozenset[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    return frozenset(Path(value) for value in tracked.split("\0") if value)


def classify_tracked_path(relative: Path) -> str:
    if relative.parts[:1] == ("tests",):
        return "tests"
    if relative.parts[:1] == ("docs",) or relative.name in {"README.md", "README_ZH.md"}:
        return "docs"
    if relative.parts[:2] == ("obsidian_wiki", "_data"):
        return "package-resource"
    if relative in {
        Path(".gitattributes"),
        Path(".gitignore"),
        Path("AGENTS.md"),
        Path("CLAUDE.md"),
        Path("GEMINI.md"),
        Path(".hermes.md"),
        Path(".github/copilot-instructions.md"),
    }:
        return "production"
    if relative.parent == Path(".") and relative.suffix == ".toml":
        return "production"
    if relative.name == "uv.lock":
        return "production"
    if relative.parts[:1] in {("obsidian_wiki",), ("tools",), ("scripts",)}:
        return "production" if relative.suffix == ".py" else "other"
    if relative.parts[:1] == ("extensions",):
        return "production" if relative.suffix in {".css", ".html", ".js", ".json"} else "other"
    if relative.parts[:1] == (".github",):
        return "production"
    if relative.parts[:1] == (".cursor",):
        return "production" if relative.suffix == ".mdc" else "other"
    if relative.parts[:2] in {
        (".agent", "rules"),
        (".agent", "workflows"),
        (".windsurf", "rules"),
        (".kiro", "steering"),
    }:
        return "production"
    return "other"


def _tracked_protocol_violations(
    manifest: Iterable[Path], contents: Callable[[Path], str]
) -> list[str]:
    violations: list[str] = []
    for relative in manifest:
        category = classify_tracked_path(relative)
        if category == "docs":
            if relative in HISTORICAL_DOCUMENTS:
                continue
            text = contents(relative)
            violations.extend(
                f"{relative}:{text.count(chr(10), 0, match.start()) + 1}: {match.group()}"
                for match in _unattributed_protocol_matches(text)
                if not _is_allowed_current_document_match(relative, text, match)
            )
        elif category == "package-resource":
            text = contents(relative)
            violations.extend(disallowed_protocol_matches(relative, text))
        elif category == "tests":
            text = contents(relative)
            violations.extend(
                f"{relative}:{text.count(chr(10), 0, match.start()) + 1}: {match.group()}"
                for match in _unattributed_protocol_matches(text)
                if not _is_explicit_negative_test_match(relative, text, match)
            )
        elif category == "other" and relative not in OTHER_BINARY_ALLOWLIST:
            text = contents(relative)
            violations.extend(disallowed_protocol_matches(relative, text))
    return violations


def _tracked_path_protocol_violations(paths: Iterable[Path]) -> list[str]:
    violations: list[str] = []
    for relative in sorted(paths):
        if relative in HISTORICAL_DOCUMENTS or relative in NEGATIVE_TEST_PATH_FIXTURES:
            continue
        for match in FORMER_EXTERNAL_PROTOCOL.finditer(relative.as_posix()):
            violations.append(f"{relative}: {match.group()}")
    return violations


def _select_current_source_paths(tracked_relatives: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(
        sorted(
            ROOT / relative
            for relative in tracked_relatives
            if classify_tracked_path(relative) == "production"
        )
    )


def _current_source_paths() -> tuple[Path, ...]:
    return _select_current_source_paths(_tracked_manifest())


def test_llmwikiops_identity_constants_are_stable() -> None:
    assert IMPLEMENTATION_ID == "evanzlh/llm-wiki-ops"
    assert UPSTREAM_URL == "https://github.com/Ar9av/obsidian-wiki"
    assert FORK_BASE_COMMIT == "5ef66b6bec8b26bab6594ac37fb4d8371469fbab"


def test_version_output_identifies_llmwikiops() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.startswith("llmwikiops ")
    assert "evanzlh/llm-wiki-ops" in result.stdout


def test_package_metadata_preserves_upstream_and_points_users_to_fork() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = tomllib.loads(text)["project"]
    assert project["name"] == "llm-wiki-ops"
    assert (
        project["description"]
        == "LLM-oriented operational framework for durable Markdown knowledge bases"
    )
    assert 'authors = [{ name = "Ar9av" }]' in text
    assert 'maintainers = [{ name = "evanzlh" }]' in text
    assert project["urls"] == {
        "Homepage": "https://github.com/evanzlh/llm-wiki-ops",
        "Repository": "https://github.com/evanzlh/llm-wiki-ops",
        "Issues": "https://github.com/evanzlh/llm-wiki-ops/issues",
        "Changelog": "https://github.com/evanzlh/llm-wiki-ops/releases",
        "Upstream": "https://github.com/Ar9av/obsidian-wiki",
    }
    assert project["scripts"] == {"llmwikiops": "obsidian_wiki.cli:main"}


def test_only_llmwikiops_cli_and_protocol_names_remain_supported() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'llmwikiops = "obsidian_wiki.cli:main"' in pyproject
    assert 'obsidian-wiki = "obsidian_wiki.cli:main"' not in pyproject
    assert (ROOT / "obsidian_wiki").is_dir()
    assert ".llmwikiops/config.toml" in (
        ROOT / "docs/configuration.md"
    ).read_text(encoding="utf-8")


def test_current_product_source_and_config_have_no_former_protocol() -> None:
    """The tracked production/config surface has no compatibility aliases."""
    disallowed: list[str] = []
    for path in _current_source_paths():
        contents = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        disallowed.extend(disallowed_protocol_matches(relative, contents))
    assert not disallowed, disallowed


def test_current_source_surface_covers_tracked_development_configs() -> None:
    relative_paths = {
        path.relative_to(ROOT) for path in _current_source_paths()
    }

    assert {
        Path("CLAUDE.md"),
        Path("GEMINI.md"),
        Path(".hermes.md"),
        Path(".agent/rules/llmwikiops.md"),
        Path(".agent/workflows/llmwikiops.md"),
        Path(".windsurf/rules/llmwikiops.md"),
        Path(".kiro/steering/llmwikiops.md"),
        Path(".github/copilot-instructions.md"),
        Path(".cursor/rules/llmwikiops.mdc"),
        Path("obsidian_wiki/portable.py"),
        Path("tools/check_readme_sync.py"),
        Path("pyproject.toml"),
        Path(".gitignore"),
        Path("extensions/brain-capture/popup.js"),
        Path("extensions/brain-capture/popup.css"),
        Path(".gitattributes"),
        Path(".github/workflows/readme-sync.yml"),
        Path("uv.lock"),
    } <= relative_paths
    assert Path("docs/configuration.md") not in relative_paths
    assert Path("obsidian_wiki/_data/legacy-skill-digests-v1.json") not in relative_paths


def test_current_source_surface_uses_only_git_listed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed = Path("obsidian_wiki/portable.py")
    ambient = Path("obsidian_wiki/_identity_audit_scratch.py")
    calls = []

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=f"{listed}\0".encode("utf-8"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    selected = _current_source_paths()
    assert selected == (ROOT / listed,)
    assert ROOT / ambient not in selected
    assert calls == [
        ((["git", "ls-files", "-z"],), {"cwd": ROOT, "check": True, "capture_output": True}),
    ]


def test_tracked_manifest_has_one_exhaustive_path_category() -> None:
    manifest = _tracked_manifest()
    partitions = {
        category: {path for path in manifest if classify_tracked_path(path) == category}
        for category in TRACKED_CATEGORIES
    }

    assert manifest
    assert set().union(*partitions.values()) == manifest
    assert sum(len(paths) for paths in partitions.values()) == len(manifest)
    assert {
        "production": Path(".gitattributes"),
        "docs": Path("README.md"),
        "tests": Path("tests/test_fork_identity.py"),
        "package-resource": Path("obsidian_wiki/_data/legacy-skill-digests-v1.json"),
        "other": Path("LICENSE"),
    }.items() <= {
        (category, path)
        for category, paths in partitions.items()
        for path in paths
    }


def test_tracked_docs_tests_and_resources_have_dedicated_protocol_guards() -> None:
    manifest = _tracked_manifest()
    docs = {path for path in manifest if classify_tracked_path(path) == "docs"}
    tests = {path for path in manifest if classify_tracked_path(path) == "tests"}
    resources = {
        path for path in manifest if classify_tracked_path(path) == "package-resource"
    }

    assert HISTORICAL_DOCUMENTS <= docs
    assert Path("obsidian_wiki/_data/legacy-skill-digests-v1.json") in resources
    others = {path for path in manifest if classify_tracked_path(path) == "other"}
    assert others == OTHER_BINARY_ALLOWLIST | {Path("LICENSE")}

    violations = _tracked_protocol_violations(
        manifest, lambda relative: (ROOT / relative).read_text(encoding="utf-8")
    )

    assert not _tracked_path_protocol_violations(manifest), manifest
    assert not violations, violations


def test_protocol_audit_rejects_mutated_allowed_contexts() -> None:
    for relative, text in (
        (Path("README.md"), "# note\nobsidian-wiki command\n"),
        (Path("docs/fork.md"), "# note\nobsidian-wiki command\n"),
        (Path("tests/test_fixture.py"), "def test_regular():\n    value = 'obsidian-wiki'\n"),
        (Path("LICENSE"), "license note\nobsidian-wiki command\n"),
    ):
        matches = _unattributed_protocol_matches(text)
        assert matches
        if relative.parts[:1] == ("tests",):
            assert not _is_explicit_negative_test_match(relative, text, matches[0])
        elif relative == Path("LICENSE"):
            assert disallowed_protocol_matches(relative, text)
        else:
            assert not _is_allowed_current_document_match(relative, text, matches[0])


@pytest.mark.parametrize(
    "relative",
    (Path("README.md"), Path("README_ZH.md"), Path("docs/fork.md")),
)
def test_current_document_allowance_rejects_old_name_appended_to_attribution_line(
    relative: Path,
) -> None:
    text = (ROOT / relative).read_text(encoding="utf-8").replace(
        UPSTREAM_ATTRIBUTION,
        f"{UPSTREAM_ATTRIBUTION} obsidian-wiki command",
        1,
    )
    appended_start = text.index(" obsidian-wiki command") + 1
    match = next(
        match
        for match in _unattributed_protocol_matches(text)
        if match.start() == appended_start
    )

    assert not _is_allowed_current_document_match(relative, text, match)


def test_manifest_audit_rejects_old_name_appended_to_license() -> None:
    manifest = _tracked_manifest()

    def contents(relative: Path) -> str:
        text = (ROOT / relative).read_text(encoding="utf-8")
        if relative == Path("LICENSE"):
            return f"{text}\nobsidian-wiki command\n"
        return text

    violations = _tracked_protocol_violations(manifest, contents)

    assert any(violation.startswith("LICENSE:") for violation in violations)


def test_tracked_path_audit_rejects_former_protocol_filenames() -> None:
    paths = {
        Path("obsidian_wiki/portable.py"),
        Path("extensions/obsidian-wiki-probe.js"),
        Path(".agent/rules/obsidian-wiki.md.extra"),
        Path("LICENSE.obsidian-wiki"),
    }

    violations = _tracked_path_protocol_violations(paths)

    assert set(violations) == {
        "extensions/obsidian-wiki-probe.js: obsidian-wiki",
        ".agent/rules/obsidian-wiki.md.extra: obsidian-wiki",
        "LICENSE.obsidian-wiki: .obsidian-wiki",
    }


@pytest.mark.parametrize(
    ("contents", "violations"),
    (
        ("from obsidian_wiki import cli", []),
        ("https://github.com/Ar9av/obsidian-wiki", []),
        ('root / ".obsidian-wiki/config.toml"', [".obsidian-wiki"]),
        ("<!-- obsidian-wiki:managed:start -->", ["obsidian-wiki"]),
        ('id: "obsidian-wiki-raw",', ["obsidian-wiki"]),
        ('_SIDECAR = ".obsidian-wiki-manifest-mutation"', [".obsidian-wiki"]),
        ('marker = b"obsidian-wiki manifest capability probe\\n"', ["obsidian-wiki"]),
        ("OBSIDIAN_WIKI_REPO=/tmp/repository", ["OBSIDIAN_WIKI_REPO"]),
        ("Obsidian_Wiki_REPO=/tmp/repository", ["Obsidian_Wiki_REPO"]),
        ("# Obsidian Wiki Agent Instructions", ["Obsidian Wiki"]),
        ("https://github.com/evanzlh/obsidian-wiki", ["obsidian-wiki"]),
        ("https://github.com/Ar9av/obsidian-wiki.evil", ["obsidian-wiki"]),
        (
            "evilhttps://github.com/Ar9av/obsidian-wiki",
            ["obsidian-wiki"],
        ),
        ("ObSiDiAn-WiKi setup", ["ObSiDiAn-WiKi"]),
        (
            "https://github.com/Ar9av/obsidian-wiki\n"
            "https://github.com/Ar9av/obsidian-wiki",
            [],
        ),
    ),
)
def test_former_protocol_detector_rejects_all_external_protocol_variants(
    contents: str, violations: list[str]
) -> None:
    found = disallowed_protocol_matches(Path("fixture.txt"), contents)
    assert [entry.rsplit(": ", 1)[1] for entry in found] == violations


def test_former_protocol_managed_assets_are_absent_from_source_tree() -> None:
    former = (
        ".agent/rules/obsidian-wiki.md",
        ".agent/workflows/obsidian-wiki.md",
        ".cursor/rules/obsidian-wiki.mdc",
        ".windsurf/rules/obsidian-wiki.md",
        ".kiro/steering/obsidian-wiki.md",
        "obsidian_wiki/_data/bootstrap/agent/rules/obsidian-wiki.md",
        "obsidian_wiki/_data/bootstrap/agent/workflows/obsidian-wiki.md",
        "obsidian_wiki/_data/bootstrap/cursor/rules/obsidian-wiki.mdc",
        "obsidian_wiki/_data/bootstrap/windsurf/rules/obsidian-wiki.md",
        "obsidian_wiki/_data/bootstrap/kiro/steering/obsidian-wiki.md",
    )
    assert not set(former) & _tracked_manifest()


def test_gitignore_setup_hint_uses_the_supported_cli_syntax() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "llmwikiops setup [DIR]" in gitignore
    assert "--portable" not in gitignore
    assert "--project" not in gitignore
    assert (
        "source checkout does not track portable repository-local agent mirrors"
        in gitignore
    )
    assert "symlinks" not in gitignore
    assert "adapters" not in gitignore


def test_session_index_describes_its_stdlib_scope_without_misstating_dependencies() -> None:
    contents = (ROOT / "obsidian_wiki/session_index.py").read_text(encoding="utf-8")

    assert "`dependencies = []`" not in contents
    assert "Package dependencies are declared in `pyproject.toml`" in contents

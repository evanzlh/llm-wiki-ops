from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import PortableConfig
from obsidian_wiki.portable import render_portable_config
from obsidian_wiki.portable_manifest import ShardedManifest

ROOT = Path(__file__).resolve().parents[1]

CURRENT_DOCS = (
    "README.md",
    "README_ZH.md",
    "docs/README.md",
    "docs/agents.md",
    "docs/architecture.md",
    "docs/cli.md",
    "docs/cli.zh-TW.md",
    "docs/configuration.md",
    "docs/contributing.md",
    "docs/fork.md",
    "docs/installation.md",
    "docs/skills.md",
)

FORBIDDEN_CURRENT_DOC_TERMS = (
    "Personal mode",
    "setup --portable",
    "repo migrate",
    "sync-setup",
    "cache-update",
    "manifest v1",
    "@name",
    "~/.obsidian-wiki/config",
    "Dataview",
)

HISTORICAL_PLANS = (
    "docs/superpowers/plans/2026-08-07-fork-identity-and-source-install.md",
    "docs/superpowers/plans/2026-08-07-portable-config-and-setup.md",
    "docs/superpowers/plans/2026-08-07-portable-migration-and-e2e.md",
    "docs/superpowers/plans/2026-08-07-portable-transactions-and-derived-state.md",
    "docs/superpowers/plans/2026-08-07-sharded-manifest-and-check.md",
    "docs/superpowers/plans/2026-08-10-cli-runtime-context-and-recovery-guidance.md",
    "docs/superpowers/plans/2026-08-10-portable-setup-installation-compatibility.md",
    "docs/superpowers/plans/2026-08-11-portable-agent-preflight-cli.md",
    "docs/superpowers/plans/2026-08-11-portable-agent-skill-docs.md",
    "docs/superpowers/plans/2026-08-12-agent-context-and-full-skill-mirrors.md",
)

HISTORICAL_SPECS = (
    "docs/superpowers/specs/2026-08-07-portable-repo-mode-design.md",
    "docs/superpowers/specs/2026-08-10-cli-runtime-context-and-recovery-guidance-design.md",
    "docs/superpowers/specs/2026-08-10-portable-setup-installation-compatibility-design.md",
    "docs/superpowers/specs/2026-08-11-portable-agent-ergonomics-design.md",
)

PLAN_BANNER = (
    "> **Superseded (2026-08-12):** Current behavior is defined by the\n"
    "> [Portable-Only Repository Design](../specs/2026-08-12-portable-only-design.md).\n\n"
)
SPEC_BANNER = (
    "> **Superseded (2026-08-12):** Current behavior is defined by the\n"
    "> [Portable-Only Repository Design](2026-08-12-portable-only-design.md).\n\n"
)
HISTORICAL_BODY_BASE = "ba9990717e931bb5c78f6ec2d08b2e8a0c0c6b98"


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_current_docs_describe_only_the_current_repository_product() -> None:
    for relative in CURRENT_DOCS:
        text = _text(relative)
        for forbidden in FORBIDDEN_CURRENT_DOC_TERMS:
            assert forbidden not in text, (relative, forbidden)


def test_readmes_have_aligned_setup_and_upgrade_commands() -> None:
    commands = (
        "obsidian-wiki setup ./team-knowledge",
        "cd ./team-knowledge",
        "obsidian-wiki doctor",
        "obsidian-wiki check",
        "obsidian-wiki repo upgrade-skills",
    )
    for command in commands:
        assert command in _text("README.md")
        assert command in _text("README_ZH.md")


def test_current_docs_cover_the_portable_only_contract() -> None:
    combined = "\n".join(_text(relative) for relative in CURRENT_DOCS)
    for required in (
        "nearest ancestor",
        "manifest v2",
        "tracked source snapshots",
        "transaction review",
        "recovery",
        "stable `wiki/index.md` and `wiki/log.md`",
        "ignored `wiki/hot.md`",
        "Git publication",
        "Dashboard",
    ):
        assert required in combined, required


def test_configuration_example_uses_the_runtime_implementation_id() -> None:
    configuration = _text("docs/configuration.md")
    rendered = render_portable_config(version="2026.8")
    implementation_line = next(
        line for line in rendered.splitlines() if line.startswith("implementation = ")
    )
    assert implementation_line == f'implementation = "{IMPLEMENTATION_ID}"'
    assert implementation_line in configuration


def test_architecture_layout_and_source_shard_example_match_runtime(
    tmp_path: Path,
) -> None:
    architecture = _text("docs/architecture.md")
    assert "wiki/.ops" not in architecture
    assert "wiki/_sources" not in architecture
    for required in (
        "sources/",
        "wiki/.manifest.json",
        "wiki/.manifest/sources/",
        "sources/design/architecture.md",
        "wiki/.manifest/sources/design/architecture.md.json",
    ):
        assert required in architecture

    root = tmp_path / "repository"
    vault = root / "wiki"
    source_root = root / "sources"
    (vault / ".manifest/sources").mkdir(parents=True)
    source = source_root / "design/architecture.md"
    source.parent.mkdir(parents=True)
    source.write_text("authority\n", encoding="utf-8")
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
    config = PortableConfig(
        root=root,
        path=root / ".obsidian-wiki/config.toml",
        schema_version=1,
        implementation=IMPLEMENTATION_ID,
        requires_cli=">=0",
        vault=vault,
        sources=(source_root,),
        skills=root / ".skills",
        local_state=root / ".obsidian-wiki/local",
        settings={},
    )
    store = ShardedManifest(config)
    source_id = store.source_id(source)
    assert source_id == "sources/design/architecture.md"
    assert store.entry_path(source_id).relative_to(root).as_posix() == (
        "wiki/.manifest/sources/design/architecture.md.json"
    )


def test_installation_documents_owner_initialized_git_repository() -> None:
    installation = _text("docs/installation.md")
    assert "does not initialize Git" in installation
    initialize = installation.index("git -C ./team-knowledge init")
    setup = installation.index("obsidian-wiki setup ./team-knowledge", initialize)
    review = installation.index("git -C ./team-knowledge status", setup)
    add = installation.index("git -C ./team-knowledge add", review)
    commit = installation.index("git -C ./team-knowledge commit", add)
    assert initialize < setup < review < add < commit


def test_readmes_define_source_and_transaction_ownership() -> None:
    expectations = {
        "README.md": (
            "owner-reviewed",
            "tracked before `transaction begin`",
            "promotes candidate pages",
            "upserts manifest shards",
            "writes an operation record",
            "never modifies tracked source snapshots",
        ),
        "README_ZH.md": (
            "由所有者审查",
            "在 `transaction begin` 之前纳入版本管理",
            "提升候选页面",
            "更新 manifest 分片",
            "写入操作记录",
            "绝不会修改受版本管理的来源快照",
        ),
    }
    for relative, required_phrases in expectations.items():
        text = _text(relative)
        for required in required_phrases:
            assert required in text, (relative, required)


def test_current_documentation_links_resolve() -> None:
    link = re.compile(r"(?<!!)\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
    for relative in CURRENT_DOCS:
        source = ROOT / relative
        for target in link.findall(source.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            assert (source.parent / target).resolve().exists(), (relative, target)


def test_historical_documents_have_one_exact_banner_before_the_body() -> None:
    for relative, banner in (
        *((path, PLAN_BANNER) for path in HISTORICAL_PLANS),
        *((path, SPEC_BANNER) for path in HISTORICAL_SPECS),
    ):
        text = _text(relative)
        assert text.startswith(banner), relative
        assert text.count("> **Superseded (2026-08-12):**") == 1, relative
        original = subprocess.run(
            ["git", "show", f"{HISTORICAL_BODY_BASE}:{relative}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert text.removeprefix(banner) == original, relative

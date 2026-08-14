from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import PortableConfig
from obsidian_wiki.portable import render_portable_config
from obsidian_wiki.portable_manifest import ShardedManifest
from obsidian_wiki.safe_files import stable_directory_identity

ROOT = Path(__file__).resolve().parents[1]

CURRENT_DOCS = (
    "AGENTS.md",
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
    "docs/session-brain.md",
    "docs/skills.md",
)

OLD_PRODUCT_REFERENCE = re.compile(
    r"(?<![\w.-])obsidian-wiki(?![\w-]|\.(?:md|mdc)\b)", re.IGNORECASE
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

FORBIDDEN_CURRENT_DOC_PATTERNS = (
    r"personal[\s_-]+mode",
    r"setup\s+--?portable",
    r"repo[\s_-]+migrate",
    r"sync[\s_-]+setup",
    r"cache[\s_-]+update",
    r"manifest[\s_-]+v1",
    r"@\s*name",
    r"~[\\/]\.obsidian-wiki[\\/]config",
    r"dataview",
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


def _unexpected_old_product_reference(text: str) -> re.Match[str] | None:
    """Return a stale public brand reference, preserving protocol and attribution."""
    for match in OLD_PRODUCT_REFERENCE.finditer(text):
        prefix = text[max(0, match.start() - len("Ar9av/")) : match.start()]
        if prefix.casefold() == "ar9av/":
            continue
        return match
    return None


def test_current_docs_describe_only_the_current_repository_product() -> None:
    for relative in CURRENT_DOCS:
        text = _text(relative)
        for forbidden in FORBIDDEN_CURRENT_DOC_TERMS:
            assert forbidden not in text, (relative, forbidden)
        for pattern in FORBIDDEN_CURRENT_DOC_PATTERNS:
            assert re.search(pattern, text, re.IGNORECASE) is None, (
                relative,
                pattern,
            )
        for nonexistent in (".ops/", "_sources/"):
            assert nonexistent not in text, (relative, nonexistent)


def test_readmes_have_aligned_setup_and_upgrade_commands() -> None:
    commands = (
        "llmwikiops setup ./team-knowledge",
        "cd ./team-knowledge",
        "llmwikiops doctor",
        "llmwikiops check",
        "llmwikiops repo upgrade-skills",
    )
    for command in commands:
        assert command in _text("README.md")
        assert command in _text("README_ZH.md")


@pytest.mark.parametrize(
    "reference",
    (
        "obsidian-wiki,",
        "Obsidian-Wiki.",
        '"obsidian-wiki"',
        "[obsidian-wiki](https://example.invalid)",
        "https://github.com/evanzlh/obsidian-wiki",
    ),
)
def test_old_product_reference_detection_covers_public_boundaries(
    reference: str,
) -> None:
    assert _unexpected_old_product_reference(reference) is not None


@pytest.mark.parametrize(
    "reference",
    (
        ".obsidian-wiki/config.toml",
        "obsidian_wiki.cli",
        "obsidian-wiki.md",
        "obsidian-wiki.mdc",
        "Ar9av/obsidian-wiki",
        "Ar9av/Obsidian-Wiki",
    ),
)
def test_old_product_reference_detection_preserves_allowed_contexts(
    reference: str,
) -> None:
    assert _unexpected_old_product_reference(reference) is None


def test_current_docs_use_llmwikiops_identity() -> None:
    for relative in CURRENT_DOCS:
        text = _text(relative)
        assert "evanzlh/obsidian-wiki" not in text, relative
        assert _unexpected_old_product_reference(text) is None, relative
    for relative in ("README.md", "README_ZH.md", "docs/fork.md"):
        text = _text(relative)
        assert "LLMWikiOps" in text, relative
        assert "evanzlh/llm-wiki-ops" in text, relative


def test_current_docs_cover_the_portable_only_contract() -> None:
    combined = "\n".join(_text(relative) for relative in CURRENT_DOCS)
    for required in (
        "nearest ancestor",
        "manifest v2",
        "tracked source snapshots",
        "transaction review",
        "recovery",
        "tracked authoritative operation log",
        "tracked derived semantic view",
        "log_path",
        "must not remove",
        "Git publication",
        "Dashboard",
    ):
        assert required in combined, required


def test_active_docs_drop_operation_pages_and_ignored_hot_contract() -> None:
    combined = "\n".join(_text(relative) for relative in CURRENT_DOCS)
    for forbidden in ("journal/operations", "operation_path", "ignored `wiki/hot.md`"):
        assert forbidden not in combined, forbidden


def test_each_authoritative_page_documents_its_role() -> None:
    required_by_page = {
        "AGENTS.md": ("LLMWikiOps", "Framework Development", "obsidian_wiki"),
        "README.md": ("one repository layout", "transaction begin", "Git publication"),
        "README_ZH.md": ("一种仓库布局", "transaction begin", "Git 发布"),
        "docs/installation.md": ("does not initialize Git", "requires_cli", "doctor"),
        "docs/configuration.md": ("nearest ancestor", "schema_version", "Manifest v2"),
        "docs/architecture.md": ("wiki/.manifest.json", "ShardedManifest.entry_path", "recovery"),
        "docs/cli.md": ("llmwikiops --help", "Only commands", "transaction commit"),
        "docs/cli.zh-TW.md": ("llmwikiops --help", "目前支援", "transaction commit"),
        "docs/agents.md": ("canonical protocol", "candidate_vault", "Git publication"),
        "docs/skills.md": ("36 skills", "metadata-first", "never installs"),
        "docs/contributing.md": ("source checkout", "disposable", "check_readme_sync.py"),
        "docs/fork.md": ("independently", "does not track future upstream changes", "single repository product"),
        "docs/README.md": ("Installation", "Architecture", "CLI reference"),
        "docs/session-brain.md": ("session", "llmwikiops", "sessions-build"),
    }
    assert set(required_by_page) == set(CURRENT_DOCS)
    for relative, required_phrases in required_by_page.items():
        text = _text(relative)
        for phrase in required_phrases:
            assert phrase in text, (relative, phrase)


def test_traditional_chinese_cli_documents_tracked_log_and_hot_contract() -> None:
    text = _text("docs/cli.zh-TW.md")
    for required in (
        "受版本管理的權威操作日誌 `wiki/log.md`",
        "最後附加一個規範區塊",
        "`log_path`",
        "受版本管理的衍生語義檢視",
        "不得移除",
        "一般 Git 衝突",
    ):
        assert required in text, required
    assert "被忽略的衍生檢視" not in text
    assert "`wiki/log.md` 保持穩定" not in text


def test_agent_docs_define_the_only_live_hot_write_exception() -> None:
    text = _text("docs/agents.md")
    assert "sole live-vault write exception" in text
    paragraphs = text.split("\n\n")
    status = next(paragraph for paragraph in paragraphs if "`hot status`" in paragraph)
    refresh = next(paragraph for paragraph in paragraphs if "`hot inputs`" in paragraph)
    assert "may run at any time" in status
    assert "read-only" in status
    assert "must not remove" in status
    assert "successful `transaction commit`" not in status
    assert "successful `transaction commit` or `transaction retry`" in refresh
    assert "tracked derived semantic `wiki/hot.md`" in refresh
    assert "`hot mark-current`" in refresh


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
        root_identity=stable_directory_identity(root.stat()),
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
    setup = installation.index("llmwikiops setup ./team-knowledge", initialize)
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
            "appends one canonical block",
            "`log_path`",
            "never modifies tracked source snapshots",
        ),
        "README_ZH.md": (
            "由所有者审查",
            "在 `transaction begin` 之前纳入版本管理",
            "提升候选页面",
            "更新 manifest 分片",
            "追加一个规范区块",
            "`log_path`",
            "绝不会修改受版本管理的来源快照",
        ),
    }
    for relative, required_phrases in expectations.items():
        text = _text(relative)
        for required in required_phrases:
            assert required in text, (relative, required)


def test_current_documentation_links_resolve() -> None:
    link = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")

    def anchors(path: Path) -> set[str]:
        found: set[str] = set()
        counts: dict[str, int] = {}
        for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE):
            plain = re.sub(r"[`*_~]", "", heading).casefold()
            slug = re.sub(r"[^\w\- ]", "", plain, flags=re.UNICODE)
            slug = re.sub(r"\s+", "-", slug.strip())
            count = counts.get(slug, 0)
            counts[slug] = count + 1
            found.add(slug if count == 0 else f"{slug}-{count}")
        return found

    for relative in CURRENT_DOCS:
        source = ROOT / relative
        for target in link.findall(source.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            raw_path, separator, raw_anchor = target.partition("#")
            destination = source if not raw_path else (source.parent / unquote(raw_path)).resolve()
            assert destination.exists(), (relative, target)
            if separator and raw_anchor and destination.suffix.casefold() == ".md":
                assert unquote(raw_anchor).casefold() in anchors(destination), (
                    relative,
                    target,
                )


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

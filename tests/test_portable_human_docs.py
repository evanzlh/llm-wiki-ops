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
AR9AV_REPOSITORY_URL_PREFIX = "https://github.com/"

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

EXTERNAL_ADAPTER_ENGLISH_DOCS = (
    "README.md",
    "docs/agents.md",
    "docs/architecture.md",
    "docs/cli.md",
    "docs/configuration.md",
    "docs/installation.md",
    "docs/skills.md",
)

EXTERNAL_ADAPTER_LOCALIZED_DOCS = {
    "README_ZH.md": (
        "在 wiki 内部，仓库感知命令使用基于当前工作目录的最近祖先发现。",
        "在 wiki 外部，请使用显式安装的全局 Adapter，并在每条仓库感知命令上强制提供 `-C` / `--repo`。",
    ),
    "docs/cli.zh-TW.md": (
        "在 wiki 內部，儲存庫感知命令使用基於目前工作目錄的最近祖先探索。",
        "在 wiki 外部，請使用明確安裝的全域 Adapter，並在每一條儲存庫感知命令上強制提供 `-C` / `--repo`。",
    ),
}

FORMER_EXTERNAL_PROTOCOL = re.compile(
    r"(?i)(?:\.obsidian-wiki(?:[/:]|\b)|"
    r"(?<![A-Za-z0-9_])obsidian-wiki(?:-(?:raw)|\.(?:md|mdc)|:[A-Za-z0-9:-]+)?"
    r"(?![A-Za-z0-9_])|OBSIDIAN_WIKI_[A-Z0-9_]+)"
)

FORBIDDEN_CURRENT_DOC_TERMS = (
    "Personal mode",
    "setup --portable",
    "repo migrate",
    "sync-setup",
    "cache-update",
    "manifest v1",
    "@name",
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
HISTORICAL_BODY_BASE = "4e16b436ec358067d33df7f59ea30b046a6300ae"


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _paragraph_at(text: str, offset: int) -> str:
    start = text.rfind("\n\n", 0, offset) + 2
    end = text.find("\n\n", offset)
    return text[start:] if end == -1 else text[start:end]


def _is_upstream_attribution(text: str, match: re.Match[str]) -> bool:
    if match.group() != "obsidian-wiki":
        return False
    owner_start = match.start() - len("Ar9av/")
    if owner_start < 0 or text[owner_start : match.start()] != "Ar9av/":
        return False
    url_start = owner_start - len(AR9AV_REPOSITORY_URL_PREFIX)
    if (
        url_start >= 0
        and text[url_start:owner_start] == AR9AV_REPOSITORY_URL_PREFIX
    ):
        if url_start and text[url_start - 1] not in " \t\r\n`'\"([{:=" :
            return False
    elif owner_start and text[owner_start - 1] not in " \t\r\n`'\"([{:=,":
        return False
    end = match.end()
    if end == len(text) or text[end] in " \t\r\n`'\"),;:!?]}#>":
        return True
    return text[end] == "/" and (end + 1 == len(text) or text[end + 1] not in "/ \t\r\n")


def _is_hard_cutover_explanation(text: str, match: re.Match[str]) -> bool:
    if match.group() != ".obsidian-wiki/":
        return False
    paragraph = _paragraph_at(text, match.start())
    return all(
        phrase in paragraph
        for phrase in (
            ".obsidian-wiki/",
            ".llmwikiops/",
            "llmwikiops setup",
        )
    ) and ("not detected" in paragraph or "不会检测" in paragraph)


def _unexpected_former_protocol_reference(text: str) -> re.Match[str] | None:
    """Return a former external protocol reference outside narrow allowed prose."""
    for match in FORMER_EXTERNAL_PROTOCOL.finditer(text):
        if _is_upstream_attribution(text, match) or _is_hard_cutover_explanation(
            text, match
        ):
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


def test_human_docs_distinguish_local_and_external_wiki_contexts() -> None:
    local = (
        "Inside a wiki, repository-aware commands use nearest-ancestor CWD discovery."
    )
    external = (
        "Outside a wiki, use an explicitly installed global adapter and mandatory "
        "`-C` / `--repo` on every repository-aware command."
    )
    for relative in EXTERNAL_ADAPTER_ENGLISH_DOCS:
        text = _text(relative)
        assert local in text, relative
        assert external in text, relative
    for relative, phrases in EXTERNAL_ADAPTER_LOCALIZED_DOCS.items():
        text = _text(relative)
        for phrase in phrases:
            assert phrase in text, (relative, phrase)


def test_external_adapter_docs_define_closed_installation_contract() -> None:
    installation = _text("docs/installation.md")
    cli = _text("docs/cli.md")
    combined = installation + "\n" + cli
    for target in ("codex", "claude", "cursor", "windsurf", "opencode", "pi", "kiro"):
        assert f"`{target}`" in combined, target
    for required in (
        "llmwikiops agent install-adapter --agent codex",
        "one agent per command",
        "does not automatically install",
        "no `--force`",
        "no uninstall command",
    ):
        assert required in combined, required


def test_external_adapter_docs_publish_exact_target_destinations() -> None:
    installation = _text("docs/installation.md")
    rows = (
        "| `codex` | `$CODEX_HOME/skills/llm-wiki-ops/` or `~/.codex/skills/llm-wiki-ops/` when unset |",
        "| `claude` | `~/.claude/skills/llm-wiki-ops/` |",
        "| `cursor` | `~/.cursor/skills/llm-wiki-ops/` |",
        "| `windsurf` | `~/.codeium/windsurf/skills/llm-wiki-ops/` |",
        "| `opencode` | `~/.config/opencode/skills/llm-wiki-ops/` |",
        "| `pi` | `~/.pi/agent/skills/llm-wiki-ops/` |",
        "| `kiro` | `~/.kiro/skills/llm-wiki-ops/` |",
    )
    for row in rows:
        assert row in installation, row
    for required in (
        "`CODEX_HOME` override must be an absolute path",
        "When `CODEX_HOME` is unset, Codex uses the invoking user's `~/.codex`",
        "The CLI accepts no custom destination",
    ):
        assert required in installation, required


def test_external_adapter_docs_explain_retained_evidence_policy() -> None:
    english = "\n".join(
        _text(relative)
        for relative in (
            "README.md",
            "docs/agents.md",
            "docs/architecture.md",
            "docs/cli.md",
            "docs/installation.md",
            "docs/skills.md",
        )
    )
    for required in (
        "`<agent-config>/.llmwikiops-retained/.llmwikiops-retained-<token>`",
        "active namespace",
        "does not automatically call `unlink` or `rmdir`",
        "no automatic garbage collection",
        "accumulate and consume disk space",
        "evidence and recovery boundary",
        "only after user confirmation",
        "manual cleanup",
        "no cleanup command",
    ):
        assert required in english, required

    localized = {
        "README_ZH.md": (
            "`.llmwikiops-retained`",
            "不会自动调用 `unlink` 或 `rmdir`",
            "不会自动执行垃圾回收",
            "累积并占用磁盘空间",
            "证据与恢复边界",
            "用户确认后手工清理",
            "不提供清理命令",
        ),
        "docs/cli.zh-TW.md": (
            "`<agent-config>/.llmwikiops-retained/.llmwikiops-retained-<token>`",
            "不會自動呼叫 `unlink` 或 `rmdir`",
            "不會自動執行垃圾回收",
            "累積並占用磁碟空間",
            "證據與復原邊界",
            "使用者確認後手動清理",
            "不提供清理命令",
        ),
    }
    for relative, phrases in localized.items():
        text = _text(relative)
        for phrase in phrases:
            assert phrase in text, (relative, phrase)


def test_external_adapter_docs_show_canonical_explicit_root_commands() -> None:
    commands = (
        "llmwikiops agent install-adapter --agent codex",
        "llmwikiops -C /absolute/path/to/wiki info --json",
        'llmwikiops -C /absolute/path/to/wiki query --mode find --term "topic" --json',
        "llmwikiops -C /absolute/path/to/wiki transaction list --json",
    )
    for relative in ("README.md", "README_ZH.md", "docs/cli.md", "docs/cli.zh-TW.md"):
        text = _text(relative)
        for command in commands:
            assert command in text, (relative, command)
    for relative in ("README.md", "README_ZH.md"):
        assert "llmwikiops -C /absolute/path/to/wiki check" in _text(relative)


def test_external_adapter_docs_state_static_quiescent_repository_boundary() -> None:
    english_docs = (
        "README.md",
        "docs/agents.md",
        "docs/architecture.md",
        "docs/installation.md",
    )
    for relative in english_docs:
        text = _text(relative).lower()
        assert "user-controlled local" in text, relative
        assert "quiescent" in text, relative

    agents = _text("docs/agents.md")
    info = agents.index("llmwikiops -C <root> info --json")
    check = agents.index("llmwikiops -C <root> check")
    direct_reads = agents.lower().index("direct agent reads")
    assert info < check < direct_reads
    agents_lower = agents.lower()
    resolved = agents.index("`runtime.status` must equal `resolved`")
    returned_root = agents.index("`runtime.root` must exactly equal")
    assert info < resolved < returned_root < check
    assert "normalized user-supplied root" in agents[returned_root:check]
    for discovery_boundary in (
        "direct child skill directories",
        "direct `skill.md`",
        "do not recursively search or hunt",
        "repository or source tree",
        "limit each complete external file read to 1 mib",
        "routing frontmatter within 64 kib",
        "read at most 1,048,576 bytes",
        "require an explicit eof or completeness indication separate from delivered content",
        "if the tool cannot establish completeness within that limit, reject the read",
        "token or tool-output limit is not a byte bound or proof of eof",
        "do not treat truncated output as a complete read",
    ):
        assert discovery_boundary in agents_lower, discovery_boundary
    for forbidden in (
        "metadata before every full read",
        "same-process metadata",
        "os.lstat",
        "stat.s_isreg",
    ):
        assert forbidden not in agents_lower, forbidden
    enumerate_skills = agents_lower.index("direct child skill directories")
    bounded_frontmatter = agents_lower.index("routing frontmatter within 64 kib")
    merge_metadata = agents_lower.index("merge repository metadata")
    reroute = agents_lower.index("reroute")
    full_authorities = agents_lower.index("full ordinary utf-8 authorities")
    assert (
        enumerate_skills
        < bounded_frontmatter
        < merge_metadata
        < reroute
        < full_authorities
    )
    authority_order = (
        "`<root>/AGENTS.md`",
        "`<root>/.skills/llm-wiki/SKILL.md`",
        "optional `<vault>/AGENTS.md`",
        "selected task's direct `SKILL.md`",
    )
    cursor = full_authorities
    for authority in authority_order:
        cursor = agents.index(authority, cursor)

    for relative in ("docs/agents.md", "docs/installation.md"):
        text = _text(relative)
        assert (
            "may deterministically finish an already-recorded framework "
            "skill-maintenance recovery"
        ) in text, relative
        assert re.search(
            r"`?check`? (?:is|remains) (?:purely )?read-only",
            text,
            re.IGNORECASE,
        ) is None, relative

    for relative in ("docs/agents.md", "docs/architecture.md"):
        text = _text(relative)
        assert "does not claim adversarial TOCTOU protection" in text, relative
        assert re.search(
            r"(?:provides|guarantees|ensures) adversarial TOCTOU protection",
            text,
            re.IGNORECASE,
        ) is None, relative

    for relative in (
        "docs/agents.md",
        "docs/architecture.md",
        "docs/installation.md",
    ):
        text = _text(relative).lower()
        assert "owner guarantees" in text, relative
        assert "mechanically" in text, relative
        assert "static" in text, relative
        assert "unsupported" in text, relative
        for boundary in ("concurrent", "shared-writable", "network-sync"):
            assert boundary in text, (relative, boundary)

    readme_zh = _text("README_ZH.md")
    assert "用户控制的本地" in readme_zh
    assert "静止" in readme_zh
    assert "共享可写仓库绝不可使用" in readme_zh
    assert "无条件不支持" in readme_zh

    readme = _text("README.md")
    assert (
        "Shared-writable repositories must not be used and are categorically "
        "unsupported"
    ) in readme
    assert "Concurrent modification and network-sync activity" in readme


def test_agent_docs_fail_closed_on_external_command_and_catalog_results() -> None:
    agents = " ".join(_text("docs/agents.md").split())
    apostrophe_escape = "'\"'\"'"

    for required in (
        "`<exact-root>` is the once-normalized value",
        (
            "When available, a structured argv-array tool or API with shell execution "
            "disabled is required"
        ),
        (
            "When only POSIX shell text is available, single-quote every dynamic argv "
            f"value and encode each literal apostrophe with `{apostrophe_escape}`"
        ),
        (
            "Never use double quotes alone for a dynamic root because `$`, backticks, "
            "and `$()` can evaluate"
        ),
        (
            "Before execution, verify that decoding produces one argv element "
            "byte-for-byte equal to `<exact-root>`; stop if you cannot establish that"
        ),
        (
            "After each catalog or frontmatter command, inspect its exit status and "
            "parsed result before starting the next command"
        ),
        (
            "Any nonzero exit, missing or unterminated frontmatter, duplicate name, or "
            "malformed result stops immediately before any authority body"
        ),
        "Never continue from partial catalog output",
        (
            "Every required CLI response must be nonempty and parse successfully before "
            "the next action"
        ),
        "Empty output is malformed CLI output and triggers the same stop",
    ):
        assert required in agents

    assert apostrophe_escape.encode("ascii") == bytes((0x27, 0x22, 0x27, 0x22, 0x27))


def test_explicit_repository_docs_define_selection_and_routing_authority() -> None:
    cli = _text("docs/cli.md")
    configuration = _text("docs/configuration.md")
    agents = _text("docs/agents.md")
    skills = _text("docs/skills.md")
    assert "global option before the subcommand" in cli
    for required in (
        "exact root",
        "does not search ancestors",
        "no default repository",
        "profile",
        "environment variable",
        "recently used repository",
    ):
        assert required in configuration, required
    for required in (
        "repository-local skill metadata and body take precedence",
        "re-evaluate the route",
        "same immutable repository binding",
    ):
        assert required in agents + "\n" + skills, required


def test_current_docs_name_the_llmwikiops_protocol_and_hard_cutover() -> None:
    combined = "\n".join(_text(relative) for relative in CURRENT_DOCS)
    for required in (
        ".llmwikiops/config.toml",
        ".llmwikiops/local/",
        "llmwikiops setup",
    ):
        assert required in combined, required

    for relative in CURRENT_DOCS:
        assert _unexpected_former_protocol_reference(_text(relative)) is None, relative

    for relative in ("README.md", "README_ZH.md"):
        text = _text(relative)
        assert ".obsidian-wiki/" in text
        assert ".llmwikiops/" in text
        assert "llmwikiops setup" in text


@pytest.mark.parametrize(
    "reference",
    (
        "obsidian-wiki,",
        "Obsidian-Wiki.",
        '"obsidian-wiki"',
        "[obsidian-wiki](https://example.invalid)",
        "https://github.com/evanzlh/obsidian-wiki",
        "Ar9av/Obsidian-Wiki",
        "Ar9av/obsidian-wiki-extra",
        "Ar9av/obsidian-wiki.evil",
        "https://evil.example/Ar9av/obsidian-wiki",
        "https://github.com/Ar9av/obsidian-wiki.evil",
        "NotAr9av/obsidian-wiki",
        "obsidian-wiki.md",
        "obsidian-wiki.mdc",
        'id: "obsidian-wiki-raw",',
        "<!-- obsidian-wiki:managed:start -->",
        "OBSIDIAN_WIKI_REPO=/tmp/repository",
    ),
)
def test_old_product_reference_detection_covers_public_boundaries(
    reference: str,
) -> None:
    assert _unexpected_former_protocol_reference(reference) is not None


@pytest.mark.parametrize(
    "reference",
    (
        "Ar9av/obsidian-wiki",
        "https://github.com/Ar9av/obsidian-wiki",
        "https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6",
        "from obsidian_wiki import cli",
    ),
)
def test_old_product_reference_detection_preserves_allowed_contexts(
    reference: str,
) -> None:
    assert _unexpected_former_protocol_reference(reference) is None


@pytest.mark.parametrize(
    "injected",
    (
        "OBSIDIAN_WIKI_REPO=/tmp/repository",
        'id: "obsidian-wiki-raw",',
        "obsidian-wiki.md",
        "obsidian-wiki.mdc",
        "<!-- obsidian-wiki:managed:start -->",
    ),
)
def test_hard_cutover_explanation_rejects_other_former_protocol_identifiers(
    injected: str,
) -> None:
    paragraph = (
        "The former `.obsidian-wiki/` state is not detected, read, migrated, or "
        "deleted. When both directories exist, `.llmwikiops/` is the only "
        "authority. Explicitly run `llmwikiops setup`. "
        f"{injected}"
    )
    assert _unexpected_former_protocol_reference(paragraph) is not None


@pytest.mark.parametrize(
    "injected",
    (
        "OBSIDIAN_WIKI_REPO=/tmp/repository",
        'id: "obsidian-wiki-raw",',
        "obsidian-wiki.md",
        "obsidian-wiki.mdc",
        "<!-- obsidian-wiki:managed:start -->",
    ),
)
def test_chinese_hard_cutover_explanation_rejects_other_former_protocol_identifiers(
    injected: str,
) -> None:
    paragraph = (
        "旧的 `.obsidian-wiki/` 状态不会检测、读取、迁移或删除。两个目录同时存在时，"
        "只有 `.llmwikiops/` 是权威。请显式运行 `llmwikiops setup`。"
        f"{injected}"
    )
    assert _unexpected_former_protocol_reference(paragraph) is not None


def test_current_docs_use_llmwikiops_identity() -> None:
    for relative in CURRENT_DOCS:
        text = _text(relative)
        assert "evanzlh/obsidian-wiki" not in text, relative
        assert _unexpected_former_protocol_reference(text) is None, relative
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
        path=root / ".llmwikiops/config.toml",
        schema_version=1,
        implementation=IMPLEMENTATION_ID,
        requires_cli=">=0",
        vault=vault,
        sources=(source_root,),
        skills=root / ".skills",
        local_state=root / ".llmwikiops/local",
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

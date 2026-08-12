from __future__ import annotations

import re
import subprocess
from pathlib import Path


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

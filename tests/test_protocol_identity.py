from __future__ import annotations

from pathlib import Path

import pytest

import obsidian_wiki.protocol as protocol
from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.cli import _config_values
from obsidian_wiki.config import ConfigError, load_portable_config, resolve_config
from obsidian_wiki.protocol import (
    AGENT_RULE_BASENAME,
    CONFIG_RELATIVE,
    CURSOR_RULE_BASENAME,
    GITATTRIBUTES_END,
    GITATTRIBUTES_START,
    GLOBAL_CONFIG_RELATIVE,
    LLMWIKIOPS_REPO_ENV,
    LOCAL_STATE_RELATIVE,
    MANAGED_END,
    MANAGED_INVENTORY_RELATIVE,
    MANAGED_START,
    PORTABLE_BOOTSTRAP_MARKER,
    RAW_PICKER_ID,
    STATE_DIR_NAME,
    TEMP_PREFIX_TOKEN,
)


def _portable_config() -> str:
    return f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"

[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".llmwikiops/local"
'''


def test_protocol_constants_define_the_llmwikiops_identity() -> None:
    expected = {
        "STATE_DIR_NAME": ".llmwikiops",
        "CONFIG_RELATIVE": ".llmwikiops/config.toml",
        "LOCAL_STATE_RELATIVE": ".llmwikiops/local",
        "MANAGED_INVENTORY_RELATIVE": ".llmwikiops/managed-skills.json",
        "GLOBAL_CONFIG_RELATIVE": ".llmwikiops/config",
        "AGENT_RULE_BASENAME": "llmwikiops.md",
        "CURSOR_RULE_BASENAME": "llmwikiops.mdc",
        "MANAGED_START": "<!-- llmwikiops:managed:start -->",
        "MANAGED_END": "<!-- llmwikiops:managed:end -->",
        "GITATTRIBUTES_START": "# llmwikiops:gitattributes:start",
        "GITATTRIBUTES_END": "# llmwikiops:gitattributes:end",
        "PORTABLE_BOOTSTRAP_MARKER": "llmwikiops:portable-bootstrap",
        "RAW_PICKER_ID": "llmwikiops-raw",
        "LLMWIKIOPS_REPO_ENV": "LLMWIKIOPS_REPO",
        "TEMP_PREFIX_TOKEN": "llmwikiops",
    }
    assert {
        "STATE_DIR_NAME": STATE_DIR_NAME,
        "CONFIG_RELATIVE": CONFIG_RELATIVE,
        "LOCAL_STATE_RELATIVE": LOCAL_STATE_RELATIVE,
        "MANAGED_INVENTORY_RELATIVE": MANAGED_INVENTORY_RELATIVE,
        "GLOBAL_CONFIG_RELATIVE": GLOBAL_CONFIG_RELATIVE,
        "AGENT_RULE_BASENAME": AGENT_RULE_BASENAME,
        "CURSOR_RULE_BASENAME": CURSOR_RULE_BASENAME,
        "MANAGED_START": MANAGED_START,
        "MANAGED_END": MANAGED_END,
        "GITATTRIBUTES_START": GITATTRIBUTES_START,
        "GITATTRIBUTES_END": GITATTRIBUTES_END,
        "PORTABLE_BOOTSTRAP_MARKER": PORTABLE_BOOTSTRAP_MARKER,
        "RAW_PICKER_ID": RAW_PICKER_ID,
        "LLMWIKIOPS_REPO_ENV": LLMWIKIOPS_REPO_ENV,
        "TEMP_PREFIX_TOKEN": TEMP_PREFIX_TOKEN,
    } == expected
    assert set(protocol.__all__) == set(expected)


def test_legacy_state_config_is_not_discovered_or_modified(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    cwd = repository / "nested"
    cwd.mkdir(parents=True)
    legacy = repository / ".obsidian-wiki" / "config.toml"
    legacy.parent.mkdir()
    original = _portable_config().encode("utf-8")
    legacy.write_bytes(original)

    with pytest.raises(ConfigError, match="^repository not configured$"):
        resolve_config(
            cwd=cwd,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )
    assert legacy.read_bytes() == original

    current = repository / CONFIG_RELATIVE
    current.parent.mkdir()
    current.write_text(_portable_config(), encoding="utf-8")
    resolved = resolve_config(
        cwd=cwd,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )
    assert resolved.path == current.resolve()
    assert legacy.read_bytes() == original
    values = _config_values(resolved)
    assert values[LLMWIKIOPS_REPO_ENV] == str(repository)
    assert "OBSIDIAN_WIKI_REPO" not in values


def test_local_state_must_use_the_canonical_protocol_path(tmp_path: Path) -> None:
    config = tmp_path / CONFIG_RELATIVE
    config.parent.mkdir()
    config.write_text(
        _portable_config().replace(".llmwikiops/local", ".obsidian-wiki/local"),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError, match=r"paths\.local_state must be exactly \.llmwikiops/local"
    ):
        load_portable_config(
            config,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )

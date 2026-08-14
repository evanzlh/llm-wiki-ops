"""Canonical external protocol identifiers for LLMWikiOps repositories.

This module deliberately contains only dependency-free string constants.  It
defines repository-facing names shared by configuration, discovery, and setup.
"""

STATE_DIR_NAME = ".llmwikiops"
CONFIG_RELATIVE = ".llmwikiops/config.toml"
LOCAL_STATE_RELATIVE = ".llmwikiops/local"
MANAGED_INVENTORY_RELATIVE = ".llmwikiops/managed-skills.json"
GLOBAL_CONFIG_RELATIVE = ".llmwikiops/config"
AGENT_RULE_BASENAME = "llmwikiops.md"
CURSOR_RULE_BASENAME = "llmwikiops.mdc"
MANAGED_START = "<!-- llmwikiops:managed:start -->"
MANAGED_END = "<!-- llmwikiops:managed:end -->"
GITATTRIBUTES_START = "# llmwikiops:gitattributes:start"
GITATTRIBUTES_END = "# llmwikiops:gitattributes:end"
PORTABLE_BOOTSTRAP_MARKER = "llmwikiops:portable-bootstrap"
RAW_PICKER_ID = "llmwikiops-raw"
LLMWIKIOPS_REPO_ENV = "LLMWIKIOPS_REPO"
TEMP_PREFIX_TOKEN = "llmwikiops"

__all__ = [
    "STATE_DIR_NAME",
    "CONFIG_RELATIVE",
    "LOCAL_STATE_RELATIVE",
    "MANAGED_INVENTORY_RELATIVE",
    "GLOBAL_CONFIG_RELATIVE",
    "AGENT_RULE_BASENAME",
    "CURSOR_RULE_BASENAME",
    "MANAGED_START",
    "MANAGED_END",
    "GITATTRIBUTES_START",
    "GITATTRIBUTES_END",
    "PORTABLE_BOOTSTRAP_MARKER",
    "RAW_PICKER_ID",
    "LLMWIKIOPS_REPO_ENV",
    "TEMP_PREFIX_TOKEN",
]

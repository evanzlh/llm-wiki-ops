"""LLMWikiOps: deterministic, repository-native LLM Wiki implementation.

The Python package keeps its historic ``obsidian_wiki`` import path while the
LLMWikiOps CLI provides deterministic repository setup and maintenance.
"""

from importlib.metadata import PackageNotFoundError, version

IMPLEMENTATION_ID = "evanzlh/llm-wiki-ops"
UPSTREAM_URL = "https://github.com/Ar9av/obsidian-wiki"
FORK_BASE_COMMIT = "5ef66b6bec8b26bab6594ac37fb4d8371469fbab"
SOURCE_INSTALL_COMMAND = "uv tool install --link-mode copy ."
SOURCE_REINSTALL_COMMAND = "uv tool install --force --reinstall --link-mode copy ."

try:
    __version__ = version("llm-wiki-ops")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+dev"

__all__ = [
    "__version__",
    "FORK_BASE_COMMIT",
    "IMPLEMENTATION_ID",
    "SOURCE_INSTALL_COMMAND",
    "SOURCE_REINSTALL_COMMAND",
    "UPSTREAM_URL",
]

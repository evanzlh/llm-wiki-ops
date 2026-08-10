"""obsidian-wiki: install the LLM-Wiki agent skills into your AI coding agents.

The product is the markdown skill content under ``.skills/`` (bundled into this
package as data). This module is just the installer CLI — see ``cli.py``.
"""

from importlib.metadata import PackageNotFoundError, version

IMPLEMENTATION_ID = "evanzlh/obsidian-wiki"
UPSTREAM_URL = "https://github.com/Ar9av/obsidian-wiki"
FORK_BASE_COMMIT = "5ef66b6bec8b26bab6594ac37fb4d8371469fbab"
SOURCE_INSTALL_COMMAND = "uv tool install --link-mode copy ."
SOURCE_REINSTALL_COMMAND = "uv tool install --force --link-mode copy ."

try:
    __version__ = version("obsidian-wiki")
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

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import ConfigError, load_portable_config


def write_portable(root: Path, body: str | None = None) -> Path:
    config = root / ".obsidian-wiki" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        body
        or f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"

[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"

[settings]
OBSIDIAN_LINK_FORMAT = "wikilink"
''',
        encoding="utf-8",
    )
    return config


def test_load_resolves_paths_against_repository_root(tmp_path: Path) -> None:
    path = write_portable(tmp_path)
    config = load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
    assert config.root == tmp_path.resolve()
    assert config.vault == (tmp_path / "wiki").resolve()
    assert config.sources == ((tmp_path / "sources").resolve(),)
    assert config.settings["OBSIDIAN_LINK_FORMAT"] == "wikilink"


@pytest.mark.parametrize("value", ["/tmp/wiki", "C:/wiki", "../../wiki"])
def test_absolute_or_escaping_vault_is_rejected(tmp_path: Path, value: str) -> None:
    path = write_portable(
        tmp_path,
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "{value}"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
    )
    with pytest.raises(ConfigError, match="repository-relative"):
        load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)


def test_backslash_config_path_is_rejected_on_every_platform(tmp_path: Path) -> None:
    path = write_portable(
        tmp_path,
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = 'wiki\\nested'
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
    )
    with pytest.raises(ConfigError, match="forward-slash"):
        load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)


def test_vault_and_sources_must_not_overlap(tmp_path: Path) -> None:
    path = write_portable(
        tmp_path,
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "sources/wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
    )
    with pytest.raises(ConfigError, match="must not overlap"):
        load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)


def test_wrong_implementation_is_rejected(tmp_path: Path) -> None:
    path = write_portable(tmp_path).read_text(encoding="utf-8")
    config_path = tmp_path / ".obsidian-wiki" / "config.toml"
    config_path.write_text(path.replace(IMPLEMENTATION_ID, "Ar9av/obsidian-wiki"), encoding="utf-8")
    with pytest.raises(ConfigError, match="implementation"):
        load_portable_config(config_path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)


def test_incompatible_cli_version_is_rejected(tmp_path: Path) -> None:
    path = write_portable(tmp_path).read_text(encoding="utf-8")
    config_path = tmp_path / ".obsidian-wiki" / "config.toml"
    config_path.write_text(path.replace(">=0", ">=2027"), encoding="utf-8")
    with pytest.raises(ConfigError, match="requires CLI"):
        load_portable_config(config_path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)


def test_machine_specific_setting_is_rejected(tmp_path: Path) -> None:
    path = write_portable(
        tmp_path,
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
[settings]
CLAUDE_HISTORY_PATH = "/tmp/claude"
''',
    )
    with pytest.raises(ConfigError, match="portable setting"):
        load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)

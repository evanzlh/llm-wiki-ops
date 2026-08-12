from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import (
    ConfigError,
    PortableConfig,
    load_portable_config,
    resolve_config,
)


def write_portable(root: Path, body: str | None = None) -> Path:
    config = root / ".obsidian-wiki" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
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
    config = load_portable_config(
        path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
    )
    assert config.root == tmp_path.resolve()
    assert config.vault == (tmp_path / "wiki").resolve()
    assert config.sources == ((tmp_path / "sources").resolve(),)
    assert config.settings["OBSIDIAN_LINK_FORMAT"] == "wikilink"


@pytest.mark.parametrize("value", ["/tmp/wiki", "C:/wiki", "C:wiki", "../../wiki"])
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
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )


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
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )


@pytest.mark.parametrize("schema_version", [0, 2, "1", True])
def test_schema_version_must_be_integer_one(
    tmp_path: Path, schema_version: object
) -> None:
    rendered = (
        f'"{schema_version}"'
        if isinstance(schema_version, str)
        else str(schema_version).lower()
    )
    path = write_portable(
        tmp_path,
        f'''schema_version = {rendered}
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
    )
    with pytest.raises(ConfigError, match="schema_version must be the integer 1"):
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )


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
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )


def test_wrong_implementation_is_rejected(tmp_path: Path) -> None:
    path = write_portable(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            IMPLEMENTATION_ID, "Ar9av/obsidian-wiki"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="implementation"):
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )


def test_incompatible_cli_version_is_rejected(tmp_path: Path) -> None:
    path = write_portable(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(">=0", ">=2027"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="requires CLI"):
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )


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
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )


def test_composite_portable_setting_is_rejected_with_config_path(
    tmp_path: Path,
) -> None:
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
OBSIDIAN_ALLOWED_LIFECYCLES = [{{name = "active"}}]
''',
    )
    with pytest.raises(ConfigError, match="unsupported settings value") as exc_info:
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )
    assert str(path) in str(exc_info.value)


def test_boolean_setting_list_is_normalized_to_lowercase(tmp_path: Path) -> None:
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
OBSIDIAN_ALLOWED_LIFECYCLES = [true, false]
''',
    )
    config = load_portable_config(
        path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
    )
    assert config.settings["OBSIDIAN_ALLOWED_LIFECYCLES"] == "true,false"


def test_invalid_utf8_is_reported_as_path_qualified_config_error(
    tmp_path: Path,
) -> None:
    path = write_portable(tmp_path)
    path.write_bytes(b"\xff")

    with pytest.raises(ConfigError) as exc_info:
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )
    assert str(path) in str(exc_info.value)


def test_resolve_config_returns_nearest_portable_config(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    cwd = inner / "nested" / "work"
    cwd.mkdir(parents=True)
    write_portable(outer)
    inner_path = write_portable(inner)

    resolved = resolve_config(
        cwd=cwd,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert isinstance(resolved, PortableConfig)
    assert resolved.path == inner_path.resolve()
    assert resolved.root == inner.resolve()


def test_legacy_files_do_not_configure_a_repository(tmp_path: Path) -> None:
    cwd = tmp_path / "project" / "nested"
    cwd.mkdir(parents=True)
    (cwd / ".env").write_text(
        'OBSIDIAN_VAULT_PATH="legacy-vault"\n', encoding="utf-8"
    )
    home = tmp_path / "home"
    global_config = home / ".obsidian-wiki" / "config"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(
        'OBSIDIAN_VAULT_PATH="global-vault"\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="^repository not configured$"):
        resolve_config(
            cwd=cwd,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )


def test_invalid_nearest_config_fails_closed(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    cwd = inner / "nested"
    cwd.mkdir(parents=True)
    write_portable(outer)
    invalid = write_portable(inner, "this is not valid TOML = [")

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            cwd=cwd,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )

    assert str(invalid.resolve()) in str(exc_info.value)
    assert str(outer / ".obsidian-wiki" / "config.toml") not in str(exc_info.value)


def test_repository_discovery_error_is_a_path_qualified_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()
    candidate = cwd / ".obsidian-wiki" / "config.toml"
    original_exists = Path.exists

    def denied_exists(path: Path) -> bool:
        if path == candidate:
            raise PermissionError("inspection denied")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", denied_exists)

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            cwd=cwd,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )

    assert str(candidate) in str(exc_info.value)
    assert "inspection denied" in str(exc_info.value)

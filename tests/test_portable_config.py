from __future__ import annotations

from pathlib import Path
import os

import pytest

import obsidian_wiki.config as config_module

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


@pytest.mark.parametrize("value", ["./wiki", "wiki/../other", "wiki//nested", "wiki/"])
def test_non_normalized_repository_path_is_rejected(
    tmp_path: Path, value: str
) -> None:
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

    with pytest.raises(ConfigError, match="normalized repository-relative"):
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX link safety contract")
@pytest.mark.parametrize("link_kind", ["symbolic", "hard"])
def test_resolve_config_rejects_linked_config_files(
    tmp_path: Path, link_kind: str
) -> None:
    repository = tmp_path / "repository"
    real = tmp_path / "real.toml"
    write_portable(tmp_path / "template").replace(real)
    candidate = repository / ".obsidian-wiki" / "config.toml"
    candidate.parent.mkdir(parents=True)
    if link_kind == "symbolic":
        candidate.symlink_to(real)
    else:
        os.link(real, candidate)

    with pytest.raises(ConfigError, match="symlink|single-link ordinary file"):
        resolve_config(
            cwd=repository,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX link safety contract")
def test_resolve_config_rejects_linked_configuration_directory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    real_directory = tmp_path / "configuration"
    real_directory.mkdir()
    write_portable(real_directory.parent / "seed").replace(
        real_directory / "config.toml"
    )
    repository.mkdir()
    (repository / ".obsidian-wiki").symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(ConfigError, match="symlink"):
        resolve_config(
            cwd=repository,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX special-file safety contract")
def test_resolve_config_rejects_special_config_file(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    candidate = repository / ".obsidian-wiki" / "config.toml"
    candidate.parent.mkdir(parents=True)
    os.mkfifo(candidate)

    with pytest.raises(ConfigError, match="single-link ordinary file"):
        resolve_config(
            cwd=repository,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX root identity contract")
def test_load_config_rejects_repository_rebound_during_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    path = write_portable(repository)
    original_parse = config_module._parse_portable_config

    def parse_then_rebind(*args, **kwargs):
        parsed = original_parse(*args, **kwargs)
        repository.rename(tmp_path / "original-repository")
        replacement = write_portable(repository)
        assert replacement == path
        return parsed

    monkeypatch.setattr(config_module, "_parse_portable_config", parse_then_rebind)

    with pytest.raises(ConfigError, match="root changed since file was read"):
        load_portable_config(
            path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX ABA link race")
def test_configured_paths_never_bind_to_aba_replacement_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    path = write_portable(repository)
    replacement = tmp_path / "replacement"
    write_portable(replacement)
    original_parse = config_module._parse_portable_config

    def parse_during_aba(*args, **kwargs):
        original = tmp_path / "original"
        repository.rename(original)
        repository.symlink_to(replacement, target_is_directory=True)
        try:
            return original_parse(*args, **kwargs)
        finally:
            repository.unlink()
            original.rename(repository)

    monkeypatch.setattr(config_module, "_parse_portable_config", parse_during_aba)

    config = load_portable_config(
        path, installed_version="2026.8", implementation=IMPLEMENTATION_ID
    )

    assert config.root == repository
    assert config.vault == repository / "wiki"
    assert config.sources == (repository / "sources",)
    assert config.skills == repository / ".skills"
    assert config.local_state == repository / ".obsidian-wiki/local"


def test_repository_discovery_error_is_a_path_qualified_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()
    candidate = cwd / ".obsidian-wiki" / "config.toml"
    candidate.parent.mkdir()
    original_lstat = Path.lstat

    def denied_lstat(path: Path):
        if path == candidate:
            raise PermissionError("inspection denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied_lstat)

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            cwd=cwd,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )

    assert str(candidate) in str(exc_info.value)
    assert "inspection denied" in str(exc_info.value)

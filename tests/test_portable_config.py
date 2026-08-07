from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import ConfigError, load_portable_config, resolve_config


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


def write_legacy(path: Path, vault: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'OBSIDIAN_VAULT_PATH="{vault}"\n', encoding="utf-8")


def test_load_resolves_paths_against_repository_root(tmp_path: Path) -> None:
    path = write_portable(tmp_path)
    config = load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
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


def test_composite_portable_setting_is_rejected_with_config_path(tmp_path: Path) -> None:
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
        load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
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
    config = load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
    assert config.settings["OBSIDIAN_ALLOWED_LIFECYCLES"] == "true,false"


def test_invalid_utf8_is_reported_as_path_qualified_config_error(tmp_path: Path) -> None:
    path = write_portable(tmp_path)
    path.write_bytes(b"\xff")

    with pytest.raises(ConfigError) as exc_info:
        load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
    assert str(path) in str(exc_info.value)


def test_nearest_portable_config_beats_nested_env_and_global_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    cwd = root / "nested" / "work"
    cwd.mkdir(parents=True)
    write_portable(tmp_path)
    portable_path = write_portable(root)
    write_legacy(root / "nested" / ".env", tmp_path / "env-vault")
    write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-vault")

    resolved = resolve_config(
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "portable"
    assert resolved.source == str(portable_path.resolve())
    assert resolved.vault == (root / "wiki").resolve()


def test_nearest_env_beats_global_without_portable_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    cwd = root / "nested" / "work"
    cwd.mkdir(parents=True)
    write_legacy(root / ".env", Path("root-wiki"))
    env_path = root / "nested" / ".env"
    write_legacy(env_path, Path("local"))
    write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-vault")

    resolved = resolve_config(
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "env"
    assert resolved.source == str(env_path.resolve())
    assert resolved.vault == (env_path.parent / "local").resolve()


def test_named_vault_beats_portable_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    cwd = root / "nested"
    cwd.mkdir(parents=True)
    write_portable(root)
    named_path = home / ".obsidian-wiki" / "config.work"
    write_legacy(named_path, Path("named"))

    resolved = resolve_config(
        vault_arg="@work",
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "named"
    assert resolved.source == str(named_path.resolve())
    assert resolved.vault == (named_path.parent / "named").resolve()


def test_missing_named_vault_never_falls_back_to_portable_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    write_portable(root)

    with pytest.raises(ConfigError, match=r"config\.work"):
        resolve_config(
            vault_arg="@work",
            cwd=root,
            home=home,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )


def test_explicit_vault_path_beats_portable_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    cwd = root / "nested"
    cwd.mkdir(parents=True)
    write_portable(root)

    resolved = resolve_config(
        vault_arg="explicit-wiki",
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "explicit"
    assert resolved.source == "explicit-wiki"
    assert resolved.vault == (cwd / "explicit-wiki").resolve()


def test_explicit_tilde_path_uses_supplied_home(tmp_path: Path) -> None:
    home = tmp_path / "supplied-home"

    resolved = resolve_config(
        vault_arg="~/explicit-wiki",
        cwd=tmp_path / "project",
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "explicit"
    assert resolved.vault == (home / "explicit-wiki").resolve()


def test_env_without_vault_is_skipped_but_empty_vault_fails_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    cwd = root / "nested" / "work"
    cwd.mkdir(parents=True)
    (cwd / ".env").write_text("SOME_OTHER_KEY=value\n", encoding="utf-8")
    empty_path = root / "nested" / ".env"
    empty_path.write_text('export OBSIDIAN_VAULT_PATH="" # intentionally empty\n', encoding="utf-8")
    write_legacy(root / ".env", tmp_path / "farther-vault")
    write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-vault")

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            cwd=cwd,
            home=home,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )
    assert str(empty_path.resolve()) in str(exc_info.value)


def test_absent_global_config_reports_vault_not_configured(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()

    with pytest.raises(ConfigError, match="^vault not configured$"):
        resolve_config(
            cwd=cwd,
            home=tmp_path / "home",
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )


def test_empty_global_config_reports_its_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    global_path = home / ".obsidian-wiki" / "config"
    global_path.parent.mkdir(parents=True)
    global_path.write_text("OBSIDIAN_VAULT_PATH=  # missing value\n", encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            cwd=cwd,
            home=home,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )
    assert str(global_path.resolve()) in str(exc_info.value)


@pytest.mark.parametrize(
    "vault_arg",
    [
        "@",
        "@../outside",
        "@x/../../../outside",
        "@x\\outside",
        "@work profile",
        "@.",
        "@work%2Foutside",
    ],
)
def test_invalid_named_vault_cannot_read_outside_profile(
    tmp_path: Path, vault_arg: str
) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    write_legacy(outside, tmp_path / "escaped-vault")

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            vault_arg=vault_arg,
            cwd=tmp_path,
            home=home,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )
    message = str(exc_info.value)
    assert "[A-Za-z0-9_-]+" in message
    assert str(outside) not in message


def test_env_walk_stops_at_supplied_home_before_using_global_config(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    cwd = home / "projects" / "wiki"
    cwd.mkdir(parents=True)
    write_legacy(tmp_path / ".env", tmp_path / "parent-vault")
    global_path = home / ".obsidian-wiki" / "config"
    write_legacy(global_path, tmp_path / "global-vault")

    resolved = resolve_config(
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "global"
    assert resolved.source == str(global_path.resolve())
    assert resolved.vault == (tmp_path / "global-vault").resolve()


def test_env_walk_reaches_filesystem_root_for_cwd_outside_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside_root = tmp_path / "external"
    cwd = outside_root / "project" / "nested"
    cwd.mkdir(parents=True)
    env_path = outside_root / ".env"
    write_legacy(env_path, Path("outside-wiki"))

    resolved = resolve_config(
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "env"
    assert resolved.source == str(env_path.resolve())
    assert resolved.vault == (outside_root / "outside-wiki").resolve()


def test_irrelevant_malformed_env_is_skipped_for_valid_parent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    cwd = root / "nested"
    cwd.mkdir(parents=True)
    (cwd / ".env").write_text(
        '# OBSIDIAN_VAULT_PATH="commented-out"\nOTHER="unterminated\n',
        encoding="utf-8",
    )
    parent_env = root / ".env"
    write_legacy(parent_env, Path("valid-wiki"))

    resolved = resolve_config(
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "env"
    assert resolved.source == str(parent_env.resolve())
    assert resolved.vault == (root / "valid-wiki").resolve()


def test_malformed_target_vault_assignment_fails_with_path_and_line(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    env_path = cwd / ".env"
    env_path.write_text(
        '  export OBSIDIAN_VAULT_PATH="unterminated\n', encoding="utf-8"
    )
    write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-vault")

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            cwd=cwd,
            home=home,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )
    message = str(exc_info.value)
    assert f"{env_path.resolve()}:1" in message
    assert "unterminated" in message


@pytest.mark.parametrize("vault_arg", ["", " ", "\t\n"])
def test_empty_explicit_vault_is_rejected(tmp_path: Path, vault_arg: str) -> None:
    with pytest.raises(ConfigError, match="explicit vault path must be non-empty"):
        resolve_config(
            vault_arg=vault_arg,
            cwd=tmp_path,
            home=tmp_path / "home",
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )


def test_explicit_vault_path_may_contain_spaces(tmp_path: Path) -> None:
    resolved = resolve_config(
        vault_arg="team wiki",
        cwd=tmp_path,
        home=tmp_path / "home",
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.vault == (tmp_path / "team wiki").resolve()


@pytest.mark.parametrize(
    ("mode", "invalid_key"),
    [("env", "BAD-KEY"), ("named", "9BAD"), ("global", ".BAD")],
)
def test_selected_legacy_config_rejects_invalid_environment_keys(
    tmp_path: Path, mode: str, invalid_key: str
) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    if mode == "env":
        config_path = cwd / ".env"
        vault_arg = None
    elif mode == "named":
        config_path = home / ".obsidian-wiki" / "config.work"
        vault_arg = "@work"
    else:
        config_path = home / ".obsidian-wiki" / "config"
        vault_arg = None
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f'OBSIDIAN_VAULT_PATH="{tmp_path / "wiki"}"\n{invalid_key}=value\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            vault_arg=vault_arg,
            cwd=cwd,
            home=home,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )
    message = str(exc_info.value)
    assert f"{config_path.resolve()}:2" in message
    assert "[A-Za-z_][A-Za-z0-9_]*" in message


@pytest.mark.parametrize("vault_arg", [r"C:\vault", "C:/vault", "C:vault"])
def test_windows_vault_paths_are_not_relative_names_on_non_windows(
    tmp_path: Path, vault_arg: str
) -> None:
    if Path("C:/vault").is_absolute():
        pytest.skip("Windows drive paths have native semantics on this platform")

    with pytest.raises(ConfigError, match="Windows"):
        resolve_config(
            vault_arg=vault_arg,
            cwd=tmp_path,
            home=tmp_path / "home",
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )


@pytest.mark.parametrize("vault_value", [r"C:\vault", "C:/vault", "C:vault"])
def test_legacy_windows_vault_path_error_includes_config_path(
    tmp_path: Path, vault_value: str
) -> None:
    if Path("C:/vault").is_absolute():
        pytest.skip("Windows drive paths have native semantics on this platform")
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    global_path = home / ".obsidian-wiki" / "config"
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        f'OBSIDIAN_VAULT_PATH="{vault_value}"\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError) as exc_info:
        resolve_config(
            cwd=cwd,
            home=home,
            installed_version="2026.8",
            implementation=IMPLEMENTATION_ID,
        )
    message = str(exc_info.value)
    assert str(global_path.resolve()) in message
    assert "Windows" in message


def test_global_config_parses_legacy_syntax_and_preserves_other_values(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    global_path = home / ".obsidian-wiki" / "config"
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        """# legacy shell-style configuration
export OBSIDIAN_VAULT_PATH='~/vault' # use the supplied home
OBSIDIAN_LINK_FORMAT = markdown # inline comment
OBSIDIAN_RAW_DIR="incoming notes"
""",
        encoding="utf-8",
    )

    resolved = resolve_config(
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.mode == "global"
    assert resolved.source == str(global_path.resolve())
    assert resolved.vault == (home / "vault").resolve()
    assert resolved.values == {
        "OBSIDIAN_VAULT_PATH": str((home / "vault").resolve()),
        "OBSIDIAN_LINK_FORMAT": "markdown",
        "OBSIDIAN_RAW_DIR": "incoming notes",
    }


def test_portable_resolution_returns_runtime_values_without_modifying_config(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    path = write_portable(
        root,
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources", "imports"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
[settings]
OBSIDIAN_LINK_FORMAT = "markdown"
OBSIDIAN_MAX_PAGES_PER_INGEST = 12
''',
    )
    original = path.read_text(encoding="utf-8")

    resolved = resolve_config(
        cwd=root,
        home=tmp_path / "home",
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )

    assert resolved.values == {
        "OBSIDIAN_VAULT_PATH": str((root / "wiki").resolve()),
        "OBSIDIAN_SOURCES_DIR": ",".join(
            [str((root / "sources").resolve()), str((root / "imports").resolve())]
        ),
        "OBSIDIAN_WIKI_REPO": str(root.resolve()),
        "OBSIDIAN_LINK_FORMAT": "markdown",
        "OBSIDIAN_MAX_PAGES_PER_INGEST": "12",
    }
    assert resolved.portable is not None
    assert path.read_text(encoding="utf-8") == original

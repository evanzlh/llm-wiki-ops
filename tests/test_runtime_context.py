from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.runtime_context import (
    SETUP_GUIDANCE,
    inspect_runtime,
    nearest_portable_config,
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
''',
        encoding="utf-8",
    )
    return config


def write_legacy(path: Path, vault: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'OBSIDIAN_VAULT_PATH="{vault}"\n', encoding="utf-8")
    return path


def write_dangling_portable(root: Path) -> Path:
    config = root / ".obsidian-wiki" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.symlink_to(root / "missing.toml")
    return config


def inspect(cwd: Path, home: Path, vault_arg: str | None = None):
    return inspect_runtime(
        vault_arg,
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )


def test_portable_runtime_without_override_is_resolved(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = write_portable(root)

    result = inspect(root / "nested", tmp_path / "home")

    assert result.status == "resolved"
    assert result.runtime is not None
    assert result.runtime.mode == "portable"
    assert result.portable_config == config.resolve()
    assert result.warnings == ()


def test_explicit_same_vault_warns_about_portable_context(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = write_portable(root)
    vault = (root / "wiki").resolve()

    result = inspect(root, tmp_path / "home", str(vault))

    assert result.status == "resolved"
    assert len(result.warnings) == 1
    assert result.warnings[0].as_dict() == {
        "code": "portable-context-overridden",
        "message": f"explicit vault selection overrides portable context discovered at {config.resolve()}",
        "hint": "omit the explicit vault to retain portable repository semantics",
        "portable_config": str(config.resolve()),
        "selected_mode": "explicit",
        "selected_source": str(vault),
        "selected_vault": str(vault),
    }


def test_named_override_warns_without_mutating_named_config(tmp_path: Path) -> None:
    root = tmp_path / "project"
    home = tmp_path / "home"
    write_portable(root)
    named = write_legacy(home / ".obsidian-wiki" / "config.work", Path("work-wiki"))
    original = named.read_bytes()

    result = inspect(root, home, "@work")

    assert result.status == "resolved"
    assert result.runtime is not None
    assert result.runtime.mode == "named"
    assert len(result.warnings) == 1
    assert result.warnings[0].selected_source == str(named.resolve())
    assert named.read_bytes() == original


def test_invalid_shadowed_portable_config_does_not_block_explicit_override(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    config = write_portable(root, "this is not valid TOML = [")
    explicit = tmp_path / "explicit-wiki"

    result = inspect(root, tmp_path / "home", str(explicit))

    assert result.status == "resolved"
    assert result.runtime is not None
    assert result.runtime.mode == "explicit"
    assert result.portable_config == config.resolve()
    assert len(result.warnings) == 1


def test_invalid_authoritative_portable_config_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "project"
    write_portable(root, "this is not valid TOML = [")

    result = inspect(root, tmp_path / "home")

    assert result.status == "error"
    assert result.runtime is None
    assert result.error is not None
    assert result.warnings == ()


def test_no_config_is_unconfigured_with_setup_guidance(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = inspect(cwd, tmp_path / "home")

    assert result.status == "unconfigured"
    assert result.runtime is None
    assert result.guidance == SETUP_GUIDANCE


def test_dangling_portable_symlink_is_discovered_and_errors_without_override(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    config = write_dangling_portable(root)

    assert nearest_portable_config(root) == config.absolute()
    result = inspect(root, tmp_path / "home")
    assert result.status == "error"
    assert result.runtime is None
    assert result.error is not None


@pytest.mark.parametrize("fallback", ["env", "global"])
def test_dangling_portable_is_authoritative_over_legacy_fallbacks(
    tmp_path: Path, fallback: str
) -> None:
    root = tmp_path / "project"
    home = tmp_path / "home"
    config = write_dangling_portable(root)
    if fallback == "env":
        write_legacy(root / ".env", tmp_path / "env-wiki")
    else:
        write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-wiki")

    result = inspect(root, home)

    assert result.status == "error"
    assert result.portable_config == config.absolute()
    assert result.runtime is None
    assert result.error is not None
    assert result.warnings == ()


@pytest.mark.parametrize("portable_kind", ["invalid", "dangling"])
@pytest.mark.parametrize("vault_arg", ["explicit-wiki", "@work"])
def test_explicit_selection_overrides_invalid_or_dangling_portable_context(
    tmp_path: Path, portable_kind: str, vault_arg: str
) -> None:
    root = tmp_path / "project"
    home = tmp_path / "home"
    if portable_kind == "invalid":
        config = write_portable(root, "this is not valid TOML = [")
    else:
        config = write_dangling_portable(root)
    if vault_arg == "@work":
        write_legacy(home / ".obsidian-wiki" / "config.work", Path("work-wiki"))

    result = inspect(root, home, vault_arg)

    assert result.status == "resolved"
    assert result.runtime is not None
    assert result.runtime.mode == ("named" if vault_arg == "@work" else "explicit")
    assert result.portable_config == config.absolute()
    assert len(result.warnings) == 1
    assert result.error is None


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling", "none"])
def test_nearest_portable_config_discovers_files_and_symlinks(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "project"
    cwd = root / "nested"
    cwd.mkdir(parents=True)
    config = root / ".obsidian-wiki" / "config.toml"
    config.parent.mkdir()
    if kind == "file":
        config.write_text("", encoding="utf-8")
        expected: Path | None = config.resolve()
    elif kind == "symlink":
        target = root / "config-source.toml"
        target.write_text("", encoding="utf-8")
        config.symlink_to(target)
        expected = config.absolute()
    elif kind == "dangling":
        config.symlink_to(root / "missing.toml")
        expected = config.absolute()
    else:
        config.parent.rmdir()
        expected = None

    assert nearest_portable_config(cwd) == expected


def test_explicit_override_outside_portable_context_has_no_warning(tmp_path: Path) -> None:
    root = tmp_path / "project"
    write_portable(root)
    outside = tmp_path / "outside"
    outside.mkdir()

    result = inspect(outside, tmp_path / "home", "explicit-wiki")

    assert result.status == "resolved"
    assert result.warnings == ()

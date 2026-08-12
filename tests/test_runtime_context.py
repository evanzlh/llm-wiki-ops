from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import ConfigError, PortableConfig
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


def write_dangling_portable(root: Path) -> Path:
    config = root / ".obsidian-wiki" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.symlink_to(root / "missing.toml")
    return config


def inspect(cwd: Path):
    return inspect_runtime(
        cwd=cwd,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )


def test_portable_runtime_is_resolved(tmp_path: Path) -> None:
    root = tmp_path / "project"
    cwd = root / "nested"
    cwd.mkdir(parents=True)
    config_path = write_portable(root)

    result = inspect(cwd)

    assert result.status == "resolved"
    assert result.cwd == cwd.resolve()
    assert result.portable_config == config_path.resolve()
    assert isinstance(result.config, PortableConfig)
    assert result.config.root == root.resolve()
    assert result.error is None
    assert result.guidance is None


def test_no_config_is_unconfigured_with_exact_setup_guidance(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = inspect(cwd)

    assert result.status == "unconfigured"
    assert result.portable_config is None
    assert result.config is None
    assert result.error is not None
    assert result.error.args == ("repository not configured",)
    assert result.guidance == SETUP_GUIDANCE == "run: obsidian-wiki setup [DIR]"


@pytest.mark.parametrize("kind", ["invalid", "dangling"])
def test_invalid_nearest_config_is_an_error(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "project"
    if kind == "invalid":
        config_path = write_portable(root, "this is not valid TOML = [")
    else:
        config_path = write_dangling_portable(root)

    result = inspect(root)

    assert result.status == "error"
    assert result.portable_config == config_path.absolute()
    assert result.config is None
    assert result.error is not None
    assert result.guidance is None


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


def test_runtime_inspection_classifies_discovery_error(
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

    result = inspect(cwd)

    assert result.status == "error"
    assert result.portable_config is None
    assert result.config is None
    assert isinstance(result.error, ConfigError)
    assert str(candidate) in str(result.error)

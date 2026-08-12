from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import (
    ConfigError,
    PortableConfig,
    _is_portable_config_candidate,
    resolve_config,
)


RuntimeStatus = Literal["resolved", "unconfigured", "error"]
SETUP_GUIDANCE = "run: obsidian-wiki setup [DIR]"


@dataclass(frozen=True)
class RuntimeInspection:
    status: RuntimeStatus
    cwd: Path
    portable_config: Path | None
    config: PortableConfig | None
    error: ConfigError | None = None
    guidance: str | None = None


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve(strict=False)
    except (OSError, RuntimeError):
        return expanded.absolute()


def nearest_portable_config(cwd: Path) -> Path | None:
    current = _absolute(cwd)
    while True:
        candidate = current / ".obsidian-wiki" / "config.toml"
        if _is_portable_config_candidate(candidate):
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def inspect_runtime(
    *,
    cwd: Path,
    installed_version: str,
    implementation: str,
) -> RuntimeInspection:
    current = _absolute(cwd)
    portable_config = None
    try:
        portable_config = nearest_portable_config(current)
        config = resolve_config(
            cwd=current,
            installed_version=installed_version,
            implementation=implementation,
        )
    except ConfigError as exc:
        if portable_config is None and exc.args == ("repository not configured",):
            return RuntimeInspection(
                status="unconfigured",
                cwd=current,
                portable_config=portable_config,
                config=None,
                error=exc,
                guidance=SETUP_GUIDANCE,
            )
        return RuntimeInspection(
            status="error",
            cwd=current,
            portable_config=portable_config,
            config=None,
            error=exc,
        )

    return RuntimeInspection(
        status="resolved",
        cwd=current,
        portable_config=portable_config,
        config=config,
    )

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import ConfigError, ResolvedConfig, resolve_config


RuntimeStatus = Literal["resolved", "unconfigured", "error"]
SETUP_GUIDANCE = "run: obsidian-wiki setup --vault /path/to/your/vault"


@dataclass(frozen=True)
class ContextWarning:
    code: str
    message: str
    hint: str
    portable_config: str
    selected_mode: str
    selected_source: str
    selected_vault: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "portable_config": self.portable_config,
            "selected_mode": self.selected_mode,
            "selected_source": self.selected_source,
            "selected_vault": self.selected_vault,
        }


@dataclass(frozen=True)
class RuntimeInspection:
    status: RuntimeStatus
    cwd: Path
    portable_config: Path | None
    runtime: ResolvedConfig | None
    warnings: tuple[ContextWarning, ...]
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
        if candidate.exists() or candidate.is_symlink():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def inspect_runtime(
    vault_arg: str | None = None,
    *,
    cwd: Path,
    home: Path,
    installed_version: str,
    implementation: str,
) -> RuntimeInspection:
    current = _absolute(cwd)
    portable_config = nearest_portable_config(current)
    try:
        runtime = resolve_config(
            vault_arg,
            cwd=current,
            home=home,
            installed_version=installed_version,
            implementation=implementation,
        )
    except ConfigError as exc:
        if (
            vault_arg is None
            and portable_config is None
            and exc.args == ("vault not configured",)
        ):
            return RuntimeInspection(
                status="unconfigured",
                cwd=current,
                portable_config=portable_config,
                runtime=None,
                warnings=(),
                error=exc,
                guidance=SETUP_GUIDANCE,
            )
        return RuntimeInspection(
            status="error",
            cwd=current,
            portable_config=portable_config,
            runtime=None,
            warnings=(),
            error=exc,
        )

    warnings: tuple[ContextWarning, ...] = ()
    if vault_arg is not None and portable_config is not None:
        warnings = (
            ContextWarning(
                code="portable-context-overridden",
                message=(
                    "explicit vault selection overrides portable context discovered at "
                    f"{portable_config}"
                ),
                hint="omit the explicit vault to retain portable repository semantics",
                portable_config=str(portable_config),
                selected_mode=runtime.mode,
                selected_source=runtime.source,
                selected_vault=str(runtime.vault),
            ),
        )
    return RuntimeInspection(
        status="resolved",
        cwd=current,
        portable_config=portable_config,
        runtime=runtime,
        warnings=warnings,
    )

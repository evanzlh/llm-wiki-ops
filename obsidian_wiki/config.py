from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


class ConfigError(ValueError):
    pass


PORTABLE_SETTING_KEYS = frozenset(
    {
        "OBSIDIAN_CATEGORIES",
        "OBSIDIAN_MAX_PAGES_PER_INGEST",
        "OBSIDIAN_LINK_FORMAT",
        "OBSIDIAN_RAW_DIR",
        "OBSIDIAN_TRUST_STRICT",
        "OBSIDIAN_ALLOWED_LIFECYCLES",
        "OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES",
        "OBSIDIAN_REQUIRED_TRUST_FIELDS",
    }
)


@dataclass(frozen=True)
class PortableConfig:
    root: Path
    path: Path
    schema_version: int
    implementation: str
    requires_cli: str
    vault: Path
    sources: tuple[Path, ...]
    skills: Path
    local_state: Path
    settings: dict[str, str]


def _contained_path(root: Path, raw: str, label: str) -> Path:
    if "\\" in raw:
        raise ConfigError(f"{label} must use portable forward-slash separators")
    value = Path(raw)
    windows_value = PureWindowsPath(raw)
    if value.is_absolute() or windows_value.is_absolute() or windows_value.drive:
        raise ConfigError(f"{label} must be repository-relative")
    resolved_root = root.resolve()
    resolved = (resolved_root / value).resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ConfigError(
            f"{label} must be repository-relative and remain inside {resolved_root}"
        ) from exc
    return resolved


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _setting_scalar(value: Any, key: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ConfigError(f"unsupported settings value for {key}")


def _settings(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("[settings] must be a TOML table")
    values: dict[str, str] = {}
    for key, value in raw.items():
        if key not in PORTABLE_SETTING_KEYS:
            raise ConfigError(f"unsupported portable setting: {key}")
        if isinstance(value, list):
            values[key] = ",".join(_setting_scalar(item, key) for item in value)
        else:
            values[key] = _setting_scalar(value, key)
    return values


def _required_string(table: dict[str, Any], key: str, table_name: str = "") -> str:
    try:
        value = table[key]
    except KeyError as exc:
        label = f"{table_name}.{key}" if table_name else key
        raise ConfigError(f"missing required {label}") from exc
    if not isinstance(value, str) or not value:
        label = f"{table_name}.{key}" if table_name else key
        raise ConfigError(f"{label} must be a non-empty string")
    return value


def _parse_portable_config(
    path: Path, *, installed_version: str, implementation: str
) -> PortableConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ConfigError("schema_version must be the integer 1")

    configured_implementation = _required_string(data, "implementation")
    if configured_implementation != implementation:
        raise ConfigError(
            "portable configuration implementation "
            f"{configured_implementation!r} does not match {implementation!r}"
        )

    requires_cli = _required_string(data, "requires_cli")
    required_versions = SpecifierSet(requires_cli)
    current_version = Version(installed_version)
    if current_version not in required_versions:
        raise ConfigError(
            f"portable configuration requires CLI {requires_cli}; installed version is {installed_version}"
        )

    paths = data.get("paths")
    if not isinstance(paths, dict):
        raise ConfigError("[paths] must be a TOML table")
    vault_raw = _required_string(paths, "vault", "paths")
    skills_raw = _required_string(paths, "skills", "paths")
    local_state_raw = _required_string(paths, "local_state", "paths")
    sources_raw = paths.get("sources")
    if (
        not isinstance(sources_raw, list)
        or not sources_raw
        or any(not isinstance(source, str) or not source for source in sources_raw)
    ):
        raise ConfigError("paths.sources must be a non-empty list of non-empty strings")

    root = path.parent.parent.resolve()
    vault = _contained_path(root, vault_raw, "paths.vault")
    sources = tuple(
        _contained_path(root, source, f"paths.sources[{index}]")
        for index, source in enumerate(sources_raw)
    )
    skills = _contained_path(root, skills_raw, "paths.skills")
    local_state = _contained_path(root, local_state_raw, "paths.local_state")

    for source in sources:
        if _overlaps(vault, source):
            raise ConfigError("paths.vault and paths.sources must not overlap")

    return PortableConfig(
        root=root,
        path=path.resolve(strict=False),
        schema_version=schema_version,
        implementation=configured_implementation,
        requires_cli=requires_cli,
        vault=vault,
        sources=sources,
        skills=skills,
        local_state=local_state,
        settings=_settings(data.get("settings")),
    )


def load_portable_config(
    path: Path, *, installed_version: str, implementation: str
) -> PortableConfig:
    config_path = Path(path)
    try:
        return _parse_portable_config(
            config_path,
            installed_version=installed_version,
            implementation=implementation,
        )
    except ConfigError as exc:
        raise ConfigError(f"{config_path}: {exc}") from exc
    except (
        InvalidSpecifier,
        InvalidVersion,
        tomllib.TOMLDecodeError,
        OSError,
        TypeError,
        UnicodeDecodeError,
    ) as exc:
        raise ConfigError(f"{config_path}: invalid portable configuration: {exc}") from exc


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def _ancestors(path: Path) -> Iterator[Path]:
    current = _safe_resolve(Path(path))
    while True:
        yield current
        parent = current.parent
        if parent == current:
            return
        current = parent


def resolve_config(
    *,
    cwd: Path | None = None,
    installed_version: str,
    implementation: str,
) -> PortableConfig:
    current_dir = _safe_resolve(Path.cwd() if cwd is None else Path(cwd))

    for ancestor in _ancestors(current_dir):
        portable_path = ancestor / ".obsidian-wiki" / "config.toml"
        if portable_path.exists() or portable_path.is_symlink():
            return load_portable_config(
                portable_path,
                installed_version=installed_version,
                implementation=implementation,
            )
    raise ConfigError("repository not configured")

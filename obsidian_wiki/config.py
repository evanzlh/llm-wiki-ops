from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator, Literal

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


class ConfigError(ValueError):
    pass


_PROFILE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VAULT_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?OBSIDIAN_VAULT_PATH\s*=", re.MULTILINE
)


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


@dataclass(frozen=True)
class ResolvedConfig:
    mode: Literal["explicit", "named", "portable", "env", "global"]
    source: str
    vault: Path
    values: dict[str, str]
    portable: PortableConfig | None = None


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


def _read_legacy_text(path: Path) -> str:
    config_path = Path(path)
    try:
        return config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{config_path}: invalid legacy configuration: {exc}") from exc


def _read_env_file(path: Path, *, text: str | None = None) -> dict[str, str]:
    config_path = Path(path)
    if text is None:
        text = _read_legacy_text(config_path)

    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        assignment = line.strip()
        if not assignment or assignment.startswith("#"):
            continue
        if assignment.startswith("export") and assignment[6:7].isspace():
            assignment = assignment[6:].lstrip()
        if "=" not in assignment:
            continue

        key, raw_value = assignment.split("=", 1)
        key = key.strip()
        if _ENV_KEY_RE.fullmatch(key) is None:
            raise ConfigError(
                f"{config_path}:{line_number}: invalid environment key {key!r}; "
                "expected [A-Za-z_][A-Za-z0-9_]*"
            )

        raw_value = raw_value.strip()
        if raw_value.startswith(("'", '"')):
            quote = raw_value[0]
            closing_index: int | None = None
            escaped = False
            for index, character in enumerate(raw_value[1:], start=1):
                if character == quote and not escaped:
                    closing_index = index
                    break
                if character == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
            if closing_index is None:
                raise ConfigError(
                    f"{config_path}:{line_number}: unterminated quoted value"
                )
            trailing = raw_value[closing_index + 1 :].strip()
            if trailing and not trailing.startswith("#"):
                raise ConfigError(
                    f"{config_path}:{line_number}: unexpected text after quoted value"
                )
            value = raw_value[1:closing_index]
        else:
            comment_index = next(
                (
                    index
                    for index, character in enumerate(raw_value)
                    if character == "#" and (index == 0 or raw_value[index - 1].isspace())
                ),
                None,
            )
            value = (
                raw_value[:comment_index].rstrip()
                if comment_index is not None
                else raw_value
            )
        values[key] = value
    return values


def _ancestors(path: Path) -> Iterator[Path]:
    current = _safe_resolve(Path(path))
    while True:
        yield current
        parent = current.parent
        if parent == current:
            return
        current = parent


def _vault_path(raw: str, *, relative_to: Path, home: Path) -> Path:
    if "\x00" in raw:
        raise ConfigError("invalid vault path: embedded NUL character")
    native_value = Path(raw)
    windows_value = PureWindowsPath(raw)
    if windows_value.drive and not windows_value.is_absolute():
        raise ConfigError("Windows drive-relative vault paths are not supported")
    if (windows_value.drive or windows_value.root) and not (
        native_value.drive or native_value.root
    ):
        raise ConfigError(
            "Windows-style absolute vault paths are not supported on this platform"
        )

    if raw == "~":
        candidate = home
    elif raw.startswith("~/"):
        candidate = home / raw[2:]
    else:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = relative_to / candidate
    try:
        return _safe_resolve(candidate)
    except ValueError as exc:
        raise ConfigError(f"invalid vault path: {exc}") from exc


def _resolved_legacy(
    path: Path,
    mode: Literal["named", "env", "global"],
    *,
    home: Path,
    values: dict[str, str] | None = None,
) -> ResolvedConfig:
    config_path = _safe_resolve(Path(path))
    parsed = _read_env_file(config_path) if values is None else values
    if "OBSIDIAN_VAULT_PATH" not in parsed:
        raise ConfigError(f"{config_path}: OBSIDIAN_VAULT_PATH is missing")
    raw_vault = parsed["OBSIDIAN_VAULT_PATH"]
    if not raw_vault.strip():
        raise ConfigError(f"{config_path}: OBSIDIAN_VAULT_PATH must be non-empty")

    try:
        vault = _vault_path(raw_vault, relative_to=config_path.parent, home=home)
    except ConfigError as exc:
        raise ConfigError(f"{config_path}: {exc}") from exc
    runtime_values = dict(parsed)
    runtime_values["OBSIDIAN_VAULT_PATH"] = str(vault)
    return ResolvedConfig(
        mode=mode,
        source=str(config_path),
        vault=vault,
        values=runtime_values,
    )


def load_global_config(path: Path, *, home: Path) -> ResolvedConfig:
    """Load one global legacy config independently of CWD resolution."""
    return _resolved_legacy(path, "global", home=_safe_resolve(home))


def resolve_config(
    vault_arg: str | None = None,
    *,
    cwd: Path | None = None,
    home: Path | None = None,
    installed_version: str,
    implementation: str,
) -> ResolvedConfig:
    current_dir = _safe_resolve(Path.cwd() if cwd is None else Path(cwd))
    home_dir = _safe_resolve(Path.home() if home is None else Path(home))

    if vault_arg is not None:
        if vault_arg.startswith("@"):
            name = vault_arg[1:]
            if _PROFILE_NAME_RE.fullmatch(name) is None:
                raise ConfigError("named vault must match [A-Za-z0-9_-]+")
            named_path = home_dir / ".obsidian-wiki" / f"config.{name}"
            return _resolved_legacy(named_path, "named", home=home_dir)

        if not vault_arg.strip():
            raise ConfigError("explicit vault path must be non-empty")
        vault = _vault_path(vault_arg, relative_to=current_dir, home=home_dir)
        return ResolvedConfig(
            mode="explicit",
            source=vault_arg,
            vault=vault,
            values={"OBSIDIAN_VAULT_PATH": str(vault)},
        )

    for ancestor in _ancestors(current_dir):
        portable_path = ancestor / ".obsidian-wiki" / "config.toml"
        if portable_path.exists() or portable_path.is_symlink():
            portable = load_portable_config(
                portable_path,
                installed_version=installed_version,
                implementation=implementation,
            )
            values = {
                "OBSIDIAN_VAULT_PATH": str(portable.vault),
                "OBSIDIAN_SOURCES_DIR": ",".join(str(source) for source in portable.sources),
                "OBSIDIAN_WIKI_REPO": str(portable.root),
            }
            values.update(portable.settings)
            return ResolvedConfig(
                mode="portable",
                source=str(portable.path),
                vault=portable.vault,
                values=values,
                portable=portable,
            )

    try:
        current_dir.relative_to(home_dir)
        cwd_is_inside_home = True
    except ValueError:
        cwd_is_inside_home = False

    for ancestor in _ancestors(current_dir):
        env_path = ancestor / ".env"
        if env_path.exists():
            text = _read_legacy_text(env_path)
            if _VAULT_ASSIGNMENT_RE.search(text) is not None:
                values = _read_env_file(env_path, text=text)
                return _resolved_legacy(env_path, "env", home=home_dir, values=values)
        if cwd_is_inside_home and ancestor == home_dir:
            break

    global_path = home_dir / ".obsidian-wiki" / "config"
    if global_path.exists():
        return _resolved_legacy(global_path, "global", home=home_dir)
    raise ConfigError("vault not configured")

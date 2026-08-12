"""Source-built CLI for installing and operating obsidian-wiki.

The locally built artifact bundles the skill content under
``obsidian_wiki/_data/skills``. This CLI wires those skills into every supported
AI agent's skills directory and writes ``~/.obsidian-wiki/config`` so the skills
resolve the vault from any project.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

from obsidian_wiki import IMPLEMENTATION_ID, SOURCE_REINSTALL_COMMAND, __version__
from obsidian_wiki.config import (
    ConfigError,
    PortableConfig,
    ResolvedConfig,
    load_global_config,
    load_portable_config,
    resolve_config,
)
from obsidian_wiki.git_support import discover_git_root
from obsidian_wiki.migration import (
    MigrationError,
    MigrationPlan,
    analyze_migration,
    apply_migration,
)
from obsidian_wiki.portable import (
    _BOOTSTRAP_REFERENCES,
    MANIFEST_MARKER,
    PROJECT_AGENT_DIRS,
    _assert_directory,
    _assert_safe_managed_path,
    _assert_single_link_ordinary_file,
    recover_portable_skill_operations,
    setup_portable_repo,
    sync_portable_skill_mirrors,
    upgrade_portable_skills,
)
from obsidian_wiki.runtime_context import (
    RuntimeInspection,
    inspect_runtime,
    nearest_portable_config,
)

HOME = Path.home()
GLOBAL_CONFIG_DIR = HOME / ".obsidian-wiki"
GLOBAL_CONFIG = GLOBAL_CONFIG_DIR / "config"
SOURCE_REINSTALL_HINT = (
    "clone https://github.com/evanzlh/obsidian-wiki, then run "
    f"`{SOURCE_REINSTALL_COMMAND}` from the clone"
)

# Skills usable from any project (no vault context needed beyond the global
# config). These are also installed globally for agents that only scope skills
# per-project, so cross-project sync/query/context work everywhere.
PORTABLE_SKILLS = ("wiki-update", "wiki-query", "wiki-context-pack")


def version_label() -> str:
    return f"obsidian-wiki {__version__} ({IMPLEMENTATION_ID})"


class SchemaOptions(TypedDict):
    allowed_lifecycles: frozenset[str]
    allowed_relationship_types: frozenset[str]
    required_trust_fields: tuple[str, ...]
    schema_source: str


# ── Data resolution ──────────────────────────────────────────────────────────
# Runtime assets have one canonical location inside the Python package. Source
# checkouts and built wheels use the same package-relative paths.
def _pkg_dir() -> Path:
    return Path(__file__).resolve().parent


def _data_dir(name: str) -> Path:
    """Return one strict package-data directory or raise with recovery guidance."""
    bundled = _pkg_dir() / "_data" / name
    if bundled.is_dir():
        return bundled
    raise FileNotFoundError(
        f"Could not locate bundled {name}. Reinstall from a clone of "
        "https://github.com/evanzlh/obsidian-wiki with "
        f"`{SOURCE_REINSTALL_COMMAND}`."
    )


def skills_dir() -> Path:
    """Return the directory holding the bundled skill folders."""
    return _data_dir("skills")


def bootstrap_dir() -> Path:
    """Return the packaged agent bootstrap directory."""
    return _data_dir("bootstrap")


def list_skills() -> list[str]:
    return sorted(p.name for p in skills_dir().iterdir() if p.is_dir())


def _installed_skill_names(
    root: Path,
    *,
    warning_sink: list[dict[str, str]] | None = None,
) -> set[str]:
    """Return installed skills backed by a readable regular SKILL.md."""
    installed: set[str] = set()
    for entry in root.iterdir():
        skill_file = entry / "SKILL.md"
        try:
            if not entry.is_dir() or not skill_file.is_file():
                continue
            with skill_file.open("rb") as stream:
                stream.read(1)
        except OSError as exc:
            if warning_sink is not None:
                warning_sink.append(_agent_skill_inspection_warning(skill_file, exc))
            continue
        installed.add(entry.name)
    return installed


# ── Skill installation ───────────────────────────────────────────────────────
def install_skills(
    target_dir: Path,
    label: str,
    *,
    subset: tuple[str, ...] | None = None,
    mode: str = "symlink",
    quiet: bool = False,
) -> int:
    """Install bundled skills into *target_dir*. Returns the count installed."""
    src_root = skills_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    installed = 0
    for skill in sorted(p for p in src_root.iterdir() if p.is_dir()):
        name = skill.name
        if subset is not None and name not in subset:
            continue
        link_path = target_dir / name

        if link_path.is_symlink() or link_path.is_file():
            link_path.unlink()
        elif link_path.is_dir():
            # A real directory we previously copied here is safe to replace;
            # anything else is the user's and we leave it alone.
            if (link_path / "SKILL.md").exists():
                shutil.rmtree(link_path)
            else:
                print(f"   ⚠️  {link_path} is not a managed skill, skipping")
                continue

        if mode == "symlink":
            link_path.symlink_to(skill, target_is_directory=True)
        else:  # copy
            shutil.copytree(skill, link_path)

        if not (link_path / "SKILL.md").exists():
            raise RuntimeError(f"broken skill install: {link_path} -> {skill}")
        installed += 1

    if not quiet:
        print(f"✅  Installed {installed} skills → {label}")
    return installed


# Agents whose skills directory lives under $HOME. (path-under-home, label,
# subset). All get every skill so they remain globally discoverable from any
# project.
GLOBAL_AGENT_DIRS: list[tuple[str, str, tuple[str, ...] | None]] = [
    (".claude/skills", "~/.claude/skills/ (Claude Code)", None),
    (".gemini/skills", "~/.gemini/skills/ (Gemini CLI)", None),
    (
        ".gemini/antigravity/skills",
        "~/.gemini/antigravity/skills/ (Antigravity, legacy)",
        None,
    ),
    (".codex/skills", "~/.codex/skills/ (Codex)", None),
    (".hermes/skills", "~/.hermes/skills/ (Hermes default)", None),
    (".openclaw/skills", "~/.openclaw/skills/ (OpenClaw)", None),
    (".copilot/skills", "~/.copilot/skills/ (GitHub Copilot CLI)", None),
    (".trae/skills", "~/.trae/skills/ (Trae)", None),
    (".trae-cn/skills", "~/.trae-cn/skills/ (Trae CN)", None),
    (".kiro/skills", "~/.kiro/skills/ (Kiro CLI)", None),
    (".pi/agent/skills", "~/.pi/agent/skills/ (Pi)", None),
    (".agents/skills", "~/.agents/skills/ (OpenCode, Aider, Droid, generic)", None),
]


def install_global_skills(mode: str) -> None:
    for rel, label, subset in GLOBAL_AGENT_DIRS:
        install_skills(HOME / rel, label, subset=subset, mode=mode)
    _install_hermes_profiles(mode)


def _install_hermes_profiles(mode: str) -> None:
    """Install into the active and all named Hermes profiles."""
    hermes_home = os.environ.get("HERMES_HOME")
    handled: set[Path] = set()
    if hermes_home:
        hp = Path(hermes_home).expanduser()
        if hp != HOME / ".hermes":
            install_skills(
                hp / "skills", f"{hp}/skills/ (Hermes active profile)", mode=mode
            )
            handled.add(hp)
    profiles = HOME / ".hermes" / "profiles"
    if profiles.is_dir():
        for prof in sorted(p for p in profiles.iterdir() if p.is_dir()):
            if prof in handled:
                continue
            install_skills(
                prof / "skills",
                f"~/.hermes/profiles/{prof.name}/skills/ (Hermes profile: {prof.name})",
                mode=mode,
            )


# ── Project-local install (opt-in) ───────────────────────────────────────────
# (bootstrap-relative source path, destination relative to project dir).
# Source paths are always resolved against the packaged bootstrap directory.
BOOTSTRAP_FILES = [
    ("AGENTS.md", "AGENTS.md"),
    ("cursor/rules/obsidian-wiki.mdc", ".cursor/rules/obsidian-wiki.mdc"),
    ("windsurf/rules/obsidian-wiki.md", ".windsurf/rules/obsidian-wiki.md"),
    ("kiro/steering/obsidian-wiki.md", ".kiro/steering/obsidian-wiki.md"),
    ("agent/rules/obsidian-wiki.md", ".agent/rules/obsidian-wiki.md"),
    ("agent/workflows/obsidian-wiki.md", ".agent/workflows/obsidian-wiki.md"),
    ("github/copilot-instructions.md", ".github/copilot-instructions.md"),
]

# AGENTS.md aliases created as symlinks within the project (single source).
AGENTS_ALIASES = ("CLAUDE.md", "GEMINI.md", ".hermes.md")


def _resolve_bootstrap_src(boot_root: Path, rel: str) -> Path:
    """Resolve one required bootstrap file under the package-only layout."""
    source = boot_root / rel
    if source.is_file():
        return source
    raise FileNotFoundError(
        f"Could not locate bundled bootstrap file {rel}. "
        f"Reinstall with `{SOURCE_REINSTALL_COMMAND}`."
    )


def install_project(project_dir: Path, mode: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n📁  Installing project-local files → {project_dir}")
    for rel, _label in PROJECT_AGENT_DIRS:
        install_skills(project_dir / rel, f"{rel}/", mode=mode)

    boot_root = bootstrap_dir()

    for rel, dest in BOOTSTRAP_FILES:
        src = _resolve_bootstrap_src(boot_root, rel)
        dst = project_dir / dest
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink() or dst.exists():
            if dst.is_dir() and not dst.is_symlink():
                continue
            dst.unlink()
        shutil.copyfile(src, dst)
    print("✅  Installed bootstrap context files (AGENTS.md, rules, workflows)")

    # AGENTS.md aliases as relative symlinks (copy fallback for symlink-hostile FS).
    for alias in AGENTS_ALIASES:
        link = project_dir / alias
        if link.is_symlink() or link.exists():
            link.unlink()
        try:
            link.symlink_to("AGENTS.md")
        except OSError:
            shutil.copyfile(project_dir / "AGENTS.md", link)
    print(f"✅  Linked AGENTS.md aliases ({', '.join(AGENTS_ALIASES)})")


# ── Config ───────────────────────────────────────────────────────────────────
def _read_config_value(key: str) -> str:
    if not GLOBAL_CONFIG.is_file():
        return ""
    for line in GLOBAL_CONFIG.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _read_config() -> dict[str, str]:
    if not GLOBAL_CONFIG.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in GLOBAL_CONFIG.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def resolve_vault_path(cli_vault: str | None) -> str:
    if cli_vault:
        return os.path.expanduser(cli_vault)
    existing = _read_config_value("OBSIDIAN_VAULT_PATH")
    if existing and existing != "/path/to/your/vault":
        return existing
    if sys.stdin.isatty():
        try:
            entered = input("  Where is your Obsidian vault? (absolute path): ").strip()
        except EOFError:
            entered = ""
        if entered:
            return os.path.expanduser(entered)
    return existing


def _runtime_error_detail(error: ConfigError) -> str:
    detail = str(error)
    if "must be non-empty" in detail:
        return f"vault not configured: {detail}"
    return detail


def _resolve_runtime(
    vault_arg: str | None = None,
    *,
    error_sink: list[ConfigError] | None = None,
) -> ResolvedConfig | None:
    """Resolve one CLI runtime through the shared precedence protocol."""
    try:
        return resolve_config(
            vault_arg,
            cwd=Path.cwd(),
            home=HOME,
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
    except ConfigError as exc:
        if error_sink is not None:
            error_sink.append(exc)
        else:
            print(f"error: {_runtime_error_detail(exc)}", file=sys.stderr)
        return None


def _context_warning_payloads(inspection: RuntimeInspection) -> list[dict[str, str]]:
    return [warning.as_dict() for warning in inspection.warnings]


def _emit_context_warnings(inspection: RuntimeInspection) -> None:
    for warning in inspection.warnings:
        print(f"warning: {warning.message}", file=sys.stderr)
        print(f"  {warning.hint}", file=sys.stderr)


def _resolved_inspection(
    vault_arg: str | None,
) -> tuple[RuntimeInspection, ResolvedConfig] | None:
    inspection = _inspect_cli_runtime(vault_arg)
    if inspection.runtime is None:
        error = inspection.error or ConfigError("vault not configured")
        print(f"error: {_runtime_error_detail(error)}", file=sys.stderr)
        return None
    return inspection, inspection.runtime


def _attach_context_warnings(
    payload: dict[str, object], inspection: RuntimeInspection
) -> dict[str, object]:
    payload["context_warnings"] = _context_warning_payloads(inspection)
    return payload


def _portable_for_vault(vault: Path) -> PortableConfig | None:
    """Return the CWD portable config when it owns the supplied vault."""
    try:
        runtime = resolve_config(
            None,
            cwd=Path.cwd(),
            home=HOME,
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
    except ConfigError:
        current = Path.cwd().resolve(strict=False)
        if any(
            (ancestor / ".obsidian-wiki" / "config.toml").exists()
            for ancestor in (current, *current.parents)
        ):
            raise
        return None
    return (
        runtime.portable
        if runtime.mode == "portable" and runtime.vault == vault
        else None
    )


def _manifest_context_for_vault(vault: Path) -> PortableConfig | None:
    """Resolve cache context, refusing to treat an unresolved v2 marker as v1."""
    portable = _portable_for_vault(vault)
    if portable is not None:
        return portable
    try:
        marker = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        isinstance(marker, dict)
        and marker.get("schema_version") == 2
        and marker.get("storage") == "sharded"
    ):
        from obsidian_wiki.portable_manifest import ManifestError

        raise ManifestError(
            "manifest v2 is portable-only; "
            "run this command inside the portable repository"
        )
    return None


def _resolved_vault(runtime: ResolvedConfig) -> Path | None:
    if not runtime.vault.is_dir():
        print(f"error: vault not found: {runtime.vault}", file=sys.stderr)
        return None
    return runtime.vault


def _schema_config_source(runtime: ResolvedConfig) -> str:
    return "explicit-vault" if runtime.mode == "explicit" else runtime.source


def write_config(vault_path: str) -> None:
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # OBSIDIAN_WIKI_REPO points at the bundled data root so skills that reference
    # framework assets (templates, references) can find them post-install.
    repo_root = skills_dir().parent
    GLOBAL_CONFIG.write_text(
        f'OBSIDIAN_VAULT_PATH="{vault_path}"\n'
        f'OBSIDIAN_WIKI_REPO="{repo_root}"\n'
        f'OBSIDIAN_WIKI_VERSION="{__version__}"\n'
    )
    print(f"✅  Global config written to {GLOBAL_CONFIG}")


VAULT_SUBDIRS = (
    "concepts",
    "entities",
    "skills",
    "references",
    "synthesis",
    "journal",
    "projects",
    "_archives",
    "_raw",
    "_staging",
    ".obsidian",
)


def scaffold_vault(vault_path: Path) -> bool:
    """Create the vault directory structure and special files if they don't exist yet.

    Idempotent: existing files/dirs are left untouched. Returns True if the vault
    directory itself had to be created (i.e. this is a brand new vault).
    """
    created = not vault_path.is_dir()
    for name in VAULT_SUBDIRS:
        (vault_path / name).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    index_md = vault_path / "index.md"
    if not index_md.exists():
        index_md.write_text(
            "---\n"
            "title: Wiki Index\n"
            "---\n\n"
            "# Wiki Index\n\n"
            f"*This index is automatically maintained. Last updated: {timestamp}*\n\n"
            "## Concepts\n\n"
            "*No pages yet. Use `wiki-ingest` to add your first source.*\n\n"
            "## Entities\n\n"
            "## Skills\n\n"
            "## References\n\n"
            "## Synthesis\n\n"
            "## Journal\n"
        )

    log_md = vault_path / "log.md"
    if not log_md.exists():
        log_md.write_text(
            "---\n"
            "title: Wiki Log\n"
            "---\n\n"
            "# Wiki Log\n\n"
            f'- [{timestamp}] INIT vault_path="{vault_path}" '
            "categories=concepts,entities,skills,references,synthesis,journal\n"
        )

    hot_md = vault_path / "hot.md"
    if not hot_md.exists():
        hot_md.write_text(
            "---\n"
            "title: Hot Cache\n"
            f"updated: {timestamp}\n"
            "---\n\n"
            "# Hot Cache\n\n"
            "*A ~500-word semantic snapshot of recent activity. Updated after every major write operation.*\n\n"
            "## Recent Activity\n\n"
            f"- [{timestamp}] INIT — vault created at {vault_path}\n\n"
            "## Active Threads\n\n"
            "*None yet — start ingesting sources to populate.*\n\n"
            "## Key Takeaways\n\n"
            "*None yet.*\n\n"
            "## Flagged Contradictions\n\n"
            "*None yet.*\n"
        )

    manifest_json = vault_path / ".manifest.json"
    if not manifest_json.exists():
        manifest_json.write_text("{}\n")

    app_json = vault_path / ".obsidian" / "app.json"
    if not app_json.exists():
        app_json.write_text(
            json.dumps(
                {
                    "strictLineBreaks": False,
                    "showFrontmatter": False,
                    "defaultViewMode": "preview",
                    "livePreview": True,
                },
                indent=2,
            )
            + "\n"
        )

    appearance_json = vault_path / ".obsidian" / "appearance.json"
    if not appearance_json.exists():
        appearance_json.write_text(json.dumps({"baseFontSize": 16}, indent=2) + "\n")

    return created


_STALE_SETUP_VERSION_UNSET = object()


def _global_config_inspection_warning(error: BaseException) -> dict[str, str]:
    return {
        "code": "installation-global-config-invalid",
        "message": f"could not inspect global config {GLOBAL_CONFIG}: {error}",
        "hint": "fix the global config or run: obsidian-wiki setup",
    }


def _agent_skills_inspection_warning(root: Path, error: OSError) -> dict[str, str]:
    return {
        "code": "installation-agent-skills-unreadable",
        "message": f"could not inspect agent skills at {root}: {error}",
        "hint": "check permissions and re-run: obsidian-wiki setup",
    }


def _agent_skill_inspection_warning(
    skill_file: Path, error: OSError
) -> dict[str, str]:
    return {
        "code": "installation-agent-skill-unreadable",
        "message": f"could not inspect installed skill at {skill_file}: {error}",
        "hint": "check permissions and re-run: obsidian-wiki setup",
    }


def _bundled_skills_inspection_warning(error: OSError) -> dict[str, str]:
    return {
        "code": "installation-bundled-skills-unreadable",
        "message": f"could not inspect bundled skills: {error}",
        "hint": SOURCE_REINSTALL_HINT,
    }


def _deduplicate_warnings(
    warnings: list[dict[str, str]],
) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for warning in warnings:
        identity = (warning["code"], warning["message"], warning["hint"])
        if identity not in seen:
            seen.add(identity)
            unique.append(warning)
    return unique


def _stale_install_warnings(
    bundled: set[str] | None = None,
    *,
    setup_version: str | None | object = _STALE_SETUP_VERSION_UNSET,
) -> list[dict[str, str]]:
    """Collect at most one warning about an incomplete global installation."""
    try:
        config_present = GLOBAL_CONFIG.is_file()
    except OSError as exc:
        return [_global_config_inspection_warning(exc)]
    if not config_present:
        return [
            {
                "code": "setup-not-run",
                "message": f"{version_label()} is installed but setup has never been run.",
                "hint": "run: obsidian-wiki setup --vault /path/to/your/vault",
            }
        ]

    if setup_version is _STALE_SETUP_VERSION_UNSET:
        try:
            inspected_setup_version: str | None = _read_config_value(
                "OBSIDIAN_WIKI_VERSION"
            )
        except (OSError, UnicodeError) as exc:
            return [_global_config_inspection_warning(exc)]
    else:
        assert setup_version is None or isinstance(setup_version, str)
        inspected_setup_version = setup_version
    if inspected_setup_version and inspected_setup_version != __version__:
        return [
            {
                "code": "setup-version-stale",
                "message": (
                    f"obsidian-wiki upgraded {inspected_setup_version} → {version_label()} "
                    "but setup hasn't been re-run."
                ),
                "hint": "run: obsidian-wiki setup",
            }
        ]

    # Even if the version matches, check that ~/.claude/skills has the full set.
    claude_skills_dir = HOME / ".claude" / "skills"
    try:
        claude_skills_present = claude_skills_dir.is_dir()
    except OSError as exc:
        return [_agent_skills_inspection_warning(claude_skills_dir, exc)]
    if claude_skills_present:
        if bundled is None:
            try:
                bundled_set = set(list_skills())
            except OSError as exc:
                return [_bundled_skills_inspection_warning(exc)]
        else:
            bundled_set = bundled
        try:
            installed = _installed_skill_names(claude_skills_dir)
        except OSError as exc:
            return [_agent_skills_inspection_warning(claude_skills_dir, exc)]
        missing = bundled_set - installed
        if missing:
            return [
                {
                    "code": "agent-skills-missing",
                    "message": (
                        f"{len(missing)} skill(s) missing from ~/.claude/skills/ "
                        f"(e.g. {', '.join(sorted(missing)[:3])}"
                        f"{', ...' if len(missing) > 3 else ''})."
                    ),
                    "hint": "run: obsidian-wiki setup",
                }
            ]
    return []


def _check_stale() -> None:
    """Render stale-install warnings for commands without structured output."""
    for warning in _stale_install_warnings():
        print(f"warning: {warning['message']}", file=sys.stderr)
        print(f"  {warning['hint']}", file=sys.stderr)


def _doctor_add(
    checks: list[dict[str, str]],
    *,
    name: str,
    status: str,
    detail: str,
    hint: str = "",
) -> None:
    checks.append(
        {
            "name": name,
            "status": status,
            "detail": detail,
            "hint": hint,
        }
    )


def _doctor_status(checks: list[dict[str, str]]) -> str:
    statuses = {check["status"] for check in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _required_vault_paths(vault: Path) -> list[Path]:
    return [
        vault / "index.md",
        vault / "log.md",
        vault / "hot.md",
        vault / ".manifest.json",
    ]


def _doctor_project_check(project_dir: Path) -> dict[str, str]:
    required = [
        project_dir / "AGENTS.md",
        *[project_dir / dest for _src, dest in BOOTSTRAP_FILES[1:]],
    ]
    missing = [
        str(path.relative_to(project_dir)) for path in required if not path.exists()
    ]
    if missing:
        return {
            "status": "warn",
            "detail": f"missing {len(missing)} bootstrap file(s)",
            "hint": f"run: obsidian-wiki setup --project {project_dir}",
        }
    aliases_missing = [
        alias for alias in AGENTS_ALIASES if not (project_dir / alias).exists()
    ]
    if aliases_missing:
        return {
            "status": "warn",
            "detail": f"missing AGENTS aliases: {', '.join(aliases_missing)}",
            "hint": f"run: obsidian-wiki setup --project {project_dir}",
        }
    return {
        "status": "pass",
        "detail": "bootstrap files and aliases present",
        "hint": "",
    }


def _refuse_portable_git_workflow() -> bool:
    portable_config = nearest_portable_config(Path.cwd())
    if portable_config is None:
        return False
    print(
        "error: portable repositories use branch and pull-request workflows; "
        "configure remotes and publish changes with Git "
        f"(portable config: {portable_config})",
        file=sys.stderr,
    )
    return True


def _portable_doctor_error(config_path: Path, error: str) -> dict[str, object]:
    checks: list[dict[str, str]] = []
    _doctor_add(
        checks,
        name="portable-config",
        status="fail",
        detail=error,
        hint=f"repair {config_path}",
    )
    _doctor_add(
        checks,
        name="implementation",
        status="fail",
        detail=error,
        hint=f"expected {IMPLEMENTATION_ID} with compatible CLI {__version__}",
    )
    return {"status": "fail", "checks": checks}


def _doctor_resolution_error(error: ConfigError) -> dict[str, object]:
    checks: list[dict[str, str]] = []
    _doctor_add(
        checks,
        name="vault-config",
        status="fail",
        detail=_runtime_error_detail(error),
        hint="repair the selected vault configuration or pass a valid --vault",
    )
    return {"status": "fail", "checks": checks}


def _portable_lexical_paths(
    portable: PortableConfig,
) -> tuple[Path, Path, Path, tuple[Path, ...]]:
    """Re-read validated path strings without resolving away symlink evidence."""
    config_path = portable.root / ".obsidian-wiki/config.toml"
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        paths = data["paths"]
        vault = paths["vault"]
        skills = paths["skills"]
        local_state = paths["local_state"]
        sources = paths["sources"]
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
    ) as exc:
        raise ValueError(
            f"portable configuration paths could not be read safely: {config_path}: {exc}"
        ) from exc
    if (
        not isinstance(vault, str)
        or not isinstance(skills, str)
        or not isinstance(local_state, str)
        or not isinstance(sources, list)
        or any(not isinstance(source, str) for source in sources)
    ):
        raise ValueError(f"portable configuration paths are invalid: {config_path}")
    root = portable.root
    return (
        root / vault,
        root / skills,
        root / local_state,
        tuple(root / source for source in sources),
    )


def _assert_optional_portable_directory(root: Path, path: Path, label: str) -> None:
    """Allow a wholly absent lazy path while rejecting every unsafe existing entry."""
    _assert_safe_managed_path(root, path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f"portable {label} path is unreadable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"portable {label} path contains a symlink: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"portable {label} path must be an ordinary directory: {path}")


def _validate_portable_paths(portable: PortableConfig) -> str:
    root = portable.root
    expected_config = root / ".obsidian-wiki/config.toml"
    if portable.path != expected_config:
        raise ValueError(
            f"portable configuration path contains a symlink or escapes the repository: "
            f"{expected_config}"
        )
    _assert_single_link_ordinary_file(root, expected_config, "portable configuration")

    vault, skills, local_state, sources = _portable_lexical_paths(portable)
    _assert_directory(root, vault, "vault path")
    _assert_directory(root, skills, "skills path")
    _assert_optional_portable_directory(root, local_state, "local state")
    for source in sources:
        _assert_optional_portable_directory(root, source, "source")
    if portable.skills != root / ".skills":
        raise ValueError("portable canonical skills path must be .skills")

    bootstrap_files = (root / "AGENTS.md",) + tuple(
        root / relative for relative in _BOOTSTRAP_REFERENCES
    )
    for path in bootstrap_files:
        _assert_single_link_ordinary_file(root, path, "portable bootstrap file")

    core_files = (
        portable.vault / "index.md",
        portable.vault / "log.md",
        portable.vault / ".manifest.json",
    )
    for path in core_files:
        try:
            _assert_single_link_ordinary_file(root, path, "portable vault core file")
        except ValueError as exc:
            relative_detail = str(exc).replace(f"{root}{os.sep}", "")
            raise ValueError(relative_detail) from exc
    try:
        manifest = json.loads(core_files[-1].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"portable manifest is invalid: {exc}") from exc
    if manifest != MANIFEST_MARKER:
        raise ValueError("portable manifest must be the canonical v2 sharded marker")
    entries_root = portable.vault / ".manifest/sources"
    shard_count = 0
    if entries_root.exists() or entries_root.is_symlink():
        _assert_directory(portable.root, entries_root, "manifest shard root")
        for directory, dirnames, filenames in os.walk(entries_root, followlinks=False):
            current = Path(directory)
            for name in (*dirnames, *filenames):
                entry = current / name
                metadata = entry.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    relative = entry.relative_to(portable.root).as_posix()
                    raise ValueError(
                        f"portable manifest shard tree contains a symlink: {relative}"
                    )
            for name in filenames:
                path = current / name
                metadata = path.lstat()
                if (
                    not name.endswith(".json")
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                ):
                    relative = path.relative_to(portable.root).as_posix()
                    raise ValueError(
                        "portable manifest shard must be an ordinary JSON file with "
                        f"one link (hard link detected): {relative}"
                    )
                shard_count += 1
    return (
        f"vault={portable.vault}; sources={len(portable.sources)}; "
        f"manifest shards={shard_count}; core files and fixed bootstrap files are valid"
    )


def _validate_portable_project_skills(
    portable: PortableConfig,
) -> dict[str, object]:
    from obsidian_wiki.portable_check import check_portable_skills

    return check_portable_skills(portable)


def _portable_skill_report_detail(report: dict[str, object]) -> str:
    issues = report["issues"]
    assert isinstance(issues, list)
    if not issues:
        return "canonical skills, managed ownership, and all agent mirrors are valid"
    return "; ".join(
        f"{issue['code']} ({issue['path']}): {issue['message']}" for issue in issues
    )


def _run_portable_doctor(portable: PortableConfig) -> dict[str, object]:
    checks: list[dict[str, str]] = []
    try:
        loaded = load_portable_config(
            portable.root / ".obsidian-wiki/config.toml",
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
        _assert_single_link_ordinary_file(
            portable.root,
            portable.root / ".obsidian-wiki/config.toml",
            "portable configuration",
        )
    except (ConfigError, ValueError, OSError) as exc:
        return _portable_doctor_error(portable.path, str(exc))
    _doctor_add(
        checks,
        name="portable-config",
        status="pass",
        detail=str(loaded.path),
    )
    _doctor_add(
        checks,
        name="implementation",
        status="pass",
        detail=f"{loaded.implementation}; CLI {__version__} satisfies {loaded.requires_cli}",
    )
    git_root = discover_git_root(loaded.vault)
    if git_root is not None and git_root != loaded.root:
        _doctor_add(
            checks,
            name="portable-git",
            status="fail",
            detail="vault is enclosed by a different Git worktree",
            hint="move the portable config to the enclosing repository root",
        )
    else:
        _doctor_add(
            checks,
            name="portable-git",
            status="pass",
            detail="enclosing Git root matches the portable repository"
            if git_root is not None
            else "no enclosing Git worktree detected",
        )
    try:
        path_detail = _validate_portable_paths(loaded)
    except (ValueError, OSError) as exc:
        _doctor_add(
            checks,
            name="portable-paths",
            status="fail",
            detail=str(exc),
        )
    else:
        _doctor_add(
            checks,
            name="portable-paths",
            status="pass",
            detail=path_detail,
        )
    skill_report = _validate_portable_project_skills(loaded)
    _doctor_add(
        checks,
        name="project-skills",
        status=str(skill_report["status"]),
        detail=_portable_skill_report_detail(skill_report),
    )
    return {"status": _doctor_status(checks), "checks": checks}


def _doctor_with_context(
    report: dict[str, object], inspection: RuntimeInspection
) -> dict[str, object]:
    return _attach_context_warnings(report, inspection)


def run_doctor(
    *,
    vault_override: str | None = None,
    project_dir: str | None = None,
    inspection: RuntimeInspection | None = None,
) -> dict[str, object]:
    inspected = inspection or _inspect_cli_runtime(vault_override)
    runtime = inspected.runtime
    portable_candidate = inspected.portable_config
    if (
        runtime is not None
        and runtime.mode == "portable"
        and runtime.portable is not None
    ):
        return _doctor_with_context(_run_portable_doctor(runtime.portable), inspected)
    if portable_candidate is not None and vault_override is None:
        try:
            _assert_single_link_ordinary_file(
                portable_candidate.parent.parent,
                portable_candidate,
                "portable configuration",
            )
            load_portable_config(
                portable_candidate,
                installed_version=__version__,
                implementation=IMPLEMENTATION_ID,
            )
        except (ConfigError, ValueError) as exc:
            return _doctor_with_context(
                _portable_doctor_error(portable_candidate, str(exc)), inspected
            )
        return _doctor_with_context(
            _portable_doctor_error(
                portable_candidate,
                "portable configuration was discovered but did not resolve",
            ),
            inspected,
        )
    if inspected.status == "error" and inspected.error is not None:
        return _doctor_with_context(_doctor_resolution_error(inspected.error), inspected)

    checks: list[dict[str, str]] = []

    try:
        bundled = list_skills()
        _doctor_add(
            checks,
            name="bundled-skills",
            status="pass" if bundled else "fail",
            detail=f"{len(bundled)} bundled skill(s) available",
            hint="" if bundled else SOURCE_REINSTALL_HINT,
        )
    except FileNotFoundError as exc:
        _doctor_add(
            checks,
            name="bundled-skills",
            status="fail",
            detail=str(exc),
            hint=SOURCE_REINSTALL_HINT,
        )
        bundled = []

    try:
        boot = bootstrap_dir()
    except FileNotFoundError as exc:
        _doctor_add(
            checks,
            name="bootstrap-assets",
            status="fail",
            detail=str(exc),
            hint=SOURCE_REINSTALL_HINT,
        )
    else:
        _doctor_add(
            checks,
            name="bootstrap-assets",
            status="pass",
            detail=str(boot),
            hint="",
        )

    config = _read_config()
    config_present = GLOBAL_CONFIG.is_file()
    _doctor_add(
        checks,
        name="global-config",
        status="pass" if config_present else "fail",
        detail=str(GLOBAL_CONFIG) if config_present else "global config not written",
        hint=""
        if config_present
        else "run: obsidian-wiki setup --vault /path/to/your/vault",
    )

    vault_path = ""
    if runtime is not None:
        vault_path = str(runtime.vault)
    elif config_present:
        vault_path = config.get("OBSIDIAN_VAULT_PATH", "")

    if not vault_path:
        _doctor_add(
            checks,
            name="vault-config",
            status="fail",
            detail="OBSIDIAN_VAULT_PATH is not set",
            hint="run: obsidian-wiki setup --vault /path/to/your/vault",
        )
        vault = None
    else:
        vault = Path(vault_path).expanduser().resolve()
        _doctor_add(
            checks,
            name="vault-config",
            status="pass",
            detail=str(vault),
            hint="",
        )

    setup_version = config.get("OBSIDIAN_WIKI_VERSION", "") if config_present else ""
    if setup_version and setup_version != __version__:
        _doctor_add(
            checks,
            name="setup-version",
            status="warn",
            detail=f"setup ran with {setup_version}; installed package is {version_label()}",
            hint="run: obsidian-wiki setup",
        )
    elif config_present:
        _doctor_add(
            checks,
            name="setup-version",
            status="pass",
            detail=f"setup version matches installed package ({version_label()})"
            if setup_version
            else "setup version not recorded",
            hint="" if setup_version else "re-run setup to record install metadata",
        )

    if vault is not None:
        if vault.is_dir():
            _doctor_add(
                checks,
                name="vault-path",
                status="pass",
                detail="vault directory exists",
                hint="",
            )
            missing_core = [
                str(path.relative_to(vault))
                for path in _required_vault_paths(vault)
                if not path.exists()
            ]
            if missing_core:
                _doctor_add(
                    checks,
                    name="vault-core-files",
                    status="warn",
                    detail=f"missing {len(missing_core)} core file(s): {', '.join(missing_core)}",
                    hint="run the wiki setup skill or create the missing files",
                )
            else:
                _doctor_add(
                    checks,
                    name="vault-core-files",
                    status="pass",
                    detail="core vault files present",
                    hint="",
                )

            manifest_path = vault / ".manifest.json"
            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    sources = data.get("sources", {})
                    _doctor_add(
                        checks,
                        name="manifest-json",
                        status="pass",
                        detail=f"valid JSON with {len(sources)} tracked source(s)",
                        hint="",
                    )
                except (json.JSONDecodeError, OSError) as exc:
                    _doctor_add(
                        checks,
                        name="manifest-json",
                        status="fail",
                        detail=f"invalid manifest: {exc}",
                        hint="repair or regenerate .manifest.json",
                    )
        else:
            _doctor_add(
                checks,
                name="vault-path",
                status="fail",
                detail=f"vault directory not found: {vault}",
                hint="fix OBSIDIAN_VAULT_PATH or re-run setup",
            )

    agent_summaries: list[str] = []
    partial_agents: list[str] = []
    full_agents = 0
    bundled_set = set(bundled)
    for rel, label, _subset in GLOBAL_AGENT_DIRS:
        agent_dir = HOME / rel
        if not agent_dir.is_dir():
            continue
        installed = {
            p.name for p in agent_dir.iterdir() if (p.is_dir() or p.is_symlink())
        }
        missing = bundled_set - installed
        count = len(installed & bundled_set)
        agent_summaries.append(f"{label}: {count}/{len(bundled_set)}")
        if missing:
            partial_agents.append(label)
        else:
            full_agents += 1

    if not agent_summaries:
        _doctor_add(
            checks,
            name="agent-installs",
            status="warn",
            detail="no global agent skill installs found",
            hint="run: obsidian-wiki setup",
        )
    elif partial_agents:
        _doctor_add(
            checks,
            name="agent-installs",
            status="warn",
            detail="; ".join(agent_summaries),
            hint="re-run obsidian-wiki setup to fill missing skills",
        )
    else:
        _doctor_add(
            checks,
            name="agent-installs",
            status="pass",
            detail=f"{full_agents} agent install(s) fully provisioned",
            hint="",
        )

    if project_dir:
        project = Path(project_dir).expanduser().resolve()
        if project.is_dir():
            project_check = _doctor_project_check(project)
            _doctor_add(
                checks,
                name="project-bootstrap",
                status=project_check["status"],
                detail=project_check["detail"],
                hint=project_check["hint"],
            )
        else:
            _doctor_add(
                checks,
                name="project-bootstrap",
                status="fail",
                detail=f"project directory not found: {project}",
                hint="pass an existing directory",
            )

    return _doctor_with_context(
        {
            "status": _doctor_status(checks),
            "checks": checks,
        },
        inspected,
    )


def _print_doctor(report: dict[str, object]) -> None:
    icon = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}
    print(f"obsidian-wiki doctor: {report['status']}")
    for check in report["checks"]:
        name = check["name"]
        status = check["status"]
        detail = check["detail"]
        hint = check["hint"]
        print(f"{icon.get(status, '•')} {name}: {detail}")
        if hint:
            print(f"   hint: {hint}")


# ── Commands ─────────────────────────────────────────────────────────────────
def _maybe_configure_sync(vault_path: Path, remote_arg: str | None) -> bool:
    """Offer (or apply) GitHub sync setup for the vault.

    Non-interactive (`--remote` passed, or no TTY and no remote given): only
    acts when a remote was explicitly supplied. The interactive
    `obsidian-wiki setup` flow prompts for the remote when sync is not already
    configured.
    """
    from obsidian_wiki.sync import configure_sync, get_remote

    if get_remote(vault_path):
        return True  # already configured — nothing to do

    remote = remote_arg
    if not remote:
        if not sys.stdin.isatty():
            return False
        print()
        try:
            answer = input("  Set up GitHub sync for your vault? [y/N]: ").strip()
        except EOFError:
            answer = ""
        if answer.lower() != "y":
            return False
        try:
            remote = input(
                "  GitHub repo URL (e.g. https://github.com/you/my-wiki.git): "
            ).strip()
        except EOFError:
            remote = ""
        if not remote:
            return False

    try:
        messages = configure_sync(vault_path, remote)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"⚠️  GitHub sync setup skipped: {exc}", file=sys.stderr)
        return False
    for m in messages:
        print(f"✅  {m}")
    print("✅  Run `obsidian-wiki sync` any time to commit and push vault changes.")
    return True


def cmd_setup(args: argparse.Namespace) -> int:
    if args.portable is not None:
        conflicts = (
            args.vault is not None
            or args.project is not None
            or args.project_only
            or args.copy
            or args.remote is not None
        )
        if conflicts:
            print(
                "error: --portable cannot be combined with --vault, --project, "
                "--project-only, --copy, or --remote",
                file=sys.stderr,
            )
            return 2
        target = Path(args.portable).expanduser().absolute()
        try:
            target = setup_portable_repo(
                target,
                version=__version__,
                source_skills=skills_dir(),
            )
        except (ValueError, OSError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Portable repository scaffolded at {target}")
        print(f"Open {target / 'wiki'} in Obsidian")
        return 0

    mode = "copy" if args.copy else "symlink"
    print("\n╔══════════════════════════════════════════════════╗")
    print("║         obsidian-wiki — Agent Setup              ║")
    print("╚══════════════════════════════════════════════════╝\n")

    vault_path = resolve_vault_path(args.vault)
    write_config(vault_path)
    if not vault_path:
        print("    → Vault path not set yet. Re-run with `--vault /path/to/vault`")
        print("      or edit OBSIDIAN_VAULT_PATH in ~/.obsidian-wiki/config.")
    else:
        vault_dir = Path(vault_path).expanduser()
        vault_created = scaffold_vault(vault_dir)
        if vault_created:
            print(f"✅  Vault created at {vault_dir}")
        else:
            print(f"✅  Vault verified at {vault_dir}")

    if not args.project_only:
        print()
        install_global_skills(mode)

    if args.project is not None:
        project_dir = Path(args.project or os.getcwd()).expanduser().resolve()
        install_project(project_dir, mode)

    sync_configured = False
    if vault_path and Path(vault_path).expanduser().is_dir():
        sync_configured = _maybe_configure_sync(
            Path(vault_path).expanduser(), args.remote
        )

    n = len(list_skills())
    print("\n───────────────────────────────────────────────────")
    print(" Setup complete!\n")
    print(f" Skills installed: {n}  (mode: {mode})")
    if vault_path:
        print(f" Vault:            {vault_path}")
    if sync_configured:
        print(" GitHub sync:      obsidian-wiki sync")
    print("\n Next steps:")
    print("   1. Open a project in your agent")
    print('   2. Say: "set up my wiki"\n')
    print(" From any project:")
    print("   /wiki-update    → sync knowledge into your vault")
    print("   /wiki-query     → ask questions against your wiki")
    print("   /wiki-context-pack → compile bounded context for another agent")
    print("───────────────────────────────────────────────────\n")
    return 0


def cmd_sync_setup(args: argparse.Namespace) -> int:
    if _refuse_portable_git_workflow():
        return 1
    runtime = _resolve_runtime(args.vault)
    if runtime is None:
        return 1
    if runtime.mode == "portable":
        print(
            "error: portable repositories use branch and pull-request workflows; "
            "configure remotes and publish changes with Git",
            file=sys.stderr,
        )
        return 1
    from obsidian_wiki.sync import configure_sync

    vault_path = runtime.vault
    try:
        messages = configure_sync(vault_path, args.remote)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for m in messages:
        print(f"✅  {m}")
    print("✅  Run `obsidian-wiki sync` any time to commit and push vault changes.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    if _refuse_portable_git_workflow():
        return 1
    runtime = _resolve_runtime(args.vault)
    if runtime is None:
        return 1
    if runtime.mode == "portable":
        print(
            "error: portable repositories use branch and pull-request workflows; "
            "commit on a branch and open a pull request with Git",
            file=sys.stderr,
        )
        return 1
    from obsidian_wiki.sync import run_sync

    code, message = run_sync(runtime.vault)
    print(message)
    return code


def cmd_graph_query(args: argparse.Namespace) -> int:
    from obsidian_wiki.graphrag import query

    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        print(f"error: vault not found: {vault}", file=sys.stderr)
        return 1
    result = query(vault, args.question, top_n=args.top, max_should_read=args.max_read)
    if args.pretty:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))
    return 0


def cmd_batch_plan(args: argparse.Namespace) -> int:
    from obsidian_wiki.batch import plan_batches
    from obsidian_wiki.portable_manifest import ManifestError

    source_dir = Path(args.source_dir).expanduser().resolve()
    vault = Path(args.vault).expanduser().resolve()
    try:
        portable = _manifest_context_for_vault(vault)
    except ConfigError as exc:
        print(f"error: {_runtime_error_detail(exc)}", file=sys.stderr)
        return 1
    if not source_dir.is_dir():
        print(f"error: source directory not found: {source_dir}", file=sys.stderr)
        return 1
    try:
        result = plan_batches(
            source_dir,
            vault,
            max_batch_mb=args.max_mb,
            max_batch_files=args.max_files,
            skip_unchanged=not args.no_cache,
            include_code=args.include_code,
            portable=portable,
        )
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.pretty:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))
    return 0


def cmd_graph_analyse(args: argparse.Namespace) -> int:
    from obsidian_wiki.graph_analysis import analyse_vault

    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        print(f"error: vault not found: {vault}", file=sys.stderr)
        return 1
    result = analyse_vault(vault, top_n=args.top)
    if args.pretty:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))
    return 0


DEFAULT_CLAUDE_DIR = "~/.claude"
DEFAULT_BRAIN_DIR = "~/.claude/session-brain"


def _brain_dir(args: argparse.Namespace) -> Path:
    return Path(
        args.out or os.environ.get("WIKI_SESSION_BRAIN_DIR") or DEFAULT_BRAIN_DIR
    ).expanduser()


def _skip_list(args: argparse.Namespace) -> list[str]:
    raw = args.skip or os.environ.get("WIKI_SKIP_PROJECTS", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def cmd_sessions_build(args: argparse.Namespace) -> int:
    from obsidian_wiki.session_graph import build

    claude_dir = Path(args.claude_dir).expanduser()
    bookmarks = (
        Path(args.bookmarks).expanduser()
        if args.bookmarks
        else Path("~/.bookmark-agent/bookmarks.json").expanduser()
    )

    def progress(message: str) -> None:
        if args.verbose:
            print(f"… {message}", file=sys.stderr)

    result = build(
        claude_dir,
        _brain_dir(args),
        k=args.k,
        min_sim=args.min_sim,
        mutual=args.mutual,
        half_life_days=args.half_life,
        full=args.full,
        since=args.since,
        skip=_skip_list(args),
        bookmarks_path=bookmarks,
        write_html=not args.no_html,
        progress=progress,
    )
    if args.json:
        print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
        return 0

    stats = result["stats"]
    print(
        f"{stats['sessions']} sessions ({stats['full']} with transcripts, "
        f"{stats['thin']} history-only) · {stats['edges']} links · "
        f"{stats['clusters']} topics · {stats['unclustered']} unclustered"
    )
    print(f"read {stats['read_this_run']} this run, reused {stats['reused']} cached")
    for cluster in result["clusters"][:15]:
        flag = (
            " [dormant]"
            if cluster["dormant"]
            else (" [hot]" if cluster["momentum"] >= 2 else "")
        )
        print(f"  {cluster['size']:4}  {cluster['name'] or cluster['label']}{flag}")
    if result["unnamed"]:
        print(
            f"{result['unnamed']} unnamed topic(s) — run the session-brain skill to name them"
        )
    print(f"-> {result['out_dir']}")
    return 0


def cmd_sessions_query(args: argparse.Namespace) -> int:
    from obsidian_wiki.session_query import query

    try:
        result = query(
            _brain_dir(args),
            args.question,
            top_n=args.top,
            max_load=args.max_load,
            half_life_days=args.half_life,
            project=args.project,
            cluster=args.cluster,
            since=args.since,
            min_score=args.min_score,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
        return 0
    if not result["candidates"]:
        print("no matching sessions")
        return 0
    for c in result["candidates"]:
        loadable = "" if c["loadable"] else "  (no transcript)"
        print(
            f"{c['score']:.2f}  {c['end_ts'][:10]}  {c['project'][:18]:18}  "
            f"{(c['title'] or '(untitled)')[:52]:52}{loadable}"
        )
        print(f"      {c['why']}")
    if result["should_load"]:
        print(f"\nload: {result['load_command']}")
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    from obsidian_wiki.session_query import show

    try:
        result = show(_brain_dir(args), args.session_id, neighbors=args.neighbors)
    except (FileNotFoundError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
    return 0


def cmd_sessions_clusters(args: argparse.Namespace) -> int:
    from obsidian_wiki.session_graph import load_graph

    try:
        _, clusters_doc = load_graph(_brain_dir(args))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    clusters = clusters_doc.get("clusters", [])
    if args.unnamed:
        clusters = [c for c in clusters if not c.get("name")]
    clusters = clusters[: args.top]
    if args.json:
        payload = {"clusters": clusters}
        print(json.dumps(payload, indent=2) if args.pretty else json.dumps(payload))
        return 0
    for c in clusters:
        flag = (
            " [dormant]"
            if c.get("dormant")
            else (" [hot]" if c.get("momentum", 0) >= 2 else "")
        )
        print(f"{c['id']:3}  {c['size']:4}  {c.get('name') or c['label']}{flag}")
        print(f"      terms: {', '.join(t for t, _ in c['top_terms'][:8])}")
    return 0


def cmd_sessions_name(args: argparse.Namespace) -> int:
    from obsidian_wiki.session_graph import set_cluster_names

    raw = (
        sys.stdin.read()
        if args.from_file == "-"
        else Path(args.from_file).expanduser().read_text(encoding="utf-8")
    )
    try:
        updates = json.loads(raw)
    except ValueError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(updates, list):
        print(
            'error: expected a JSON array of {"id": N, "name": "...", "summary": "..."}',
            file=sys.stderr,
        )
        return 1
    try:
        result = set_cluster_names(_brain_dir(args), updates)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


def _cache_source_path(raw: str) -> Path:
    """Return an absolute lexical source path without following symlinks."""
    return Path(os.path.abspath(os.fspath(Path(raw).expanduser())))


def _cache_vault_path(raw: str) -> Path:
    """Return a canonical absolute vault path for runtime ownership checks."""
    return _cache_source_path(raw).resolve(strict=False)


def cmd_cache_check(args: argparse.Namespace) -> int:
    from obsidian_wiki.cache import check_sources

    if not args.configured and not args.paths:
        print(
            "error: cache-check requires VAULT SOURCE... or --configured SOURCE...",
            file=sys.stderr,
        )
        return 2

    if args.configured:
        vault_arg = None
    else:
        vault = _cache_vault_path(args.path)
        vault_arg = str(vault)
    resolved = _resolved_inspection(vault_arg)
    if resolved is None:
        return 1
    inspection, runtime = resolved
    if args.configured:
        vault = runtime.vault
        sources_raw = [args.path, *args.paths]
        portable = runtime.portable
    else:
        sources_raw = args.paths
        try:
            portable = _manifest_context_for_vault(vault)
        except ConfigError as exc:
            print(f"error: {_runtime_error_detail(exc)}", file=sys.stderr)
            return 1

    sources = [_cache_source_path(path) for path in sources_raw]
    result = check_sources(vault, sources, portable=portable)
    _attach_context_warnings(result, inspection)
    _json_print(result, pretty=args.pretty)
    return 0


def cmd_cache_update(args: argparse.Namespace) -> int:
    from obsidian_wiki.cache import update_source

    vault = _cache_vault_path(args.vault)
    source = _cache_source_path(args.source)
    pages = args.pages or []
    try:
        portable = _manifest_context_for_vault(vault)
    except ConfigError as exc:
        print(f"error: {_runtime_error_detail(exc)}", file=sys.stderr)
        return 1
    h = update_source(vault, source, pages_produced=pages, portable=portable)
    _json_print(
        {"path": str(source), "content_hash": h},
        pretty=args.pretty,
    )
    return 0


def cmd_cache_hash(args: argparse.Namespace) -> int:
    from obsidian_wiki.cache import hash_file

    path = _cache_source_path(args.path)
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 1
    _json_print(
        {"path": str(path), "sha256": hash_file(path)},
        pretty=args.pretty,
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    portable_candidate = nearest_portable_config(Path.cwd())
    resolution_errors: list[ConfigError] = []
    runtime = _resolve_runtime(error_sink=resolution_errors)
    if runtime is None:
        if portable_candidate is not None and resolution_errors:
            print(
                f"error: {_runtime_error_detail(resolution_errors[0])}",
                file=sys.stderr,
            )
        else:
            print("error: check requires a portable repository", file=sys.stderr)
        return 1
    if runtime.portable is None:
        print("error: check requires a portable repository", file=sys.stderr)
        return 1
    from obsidian_wiki.portable_check import check_portable_repo

    try:
        recover_portable_skill_operations(
            runtime.portable.root,
            version=__version__,
            source_skills=skills_dir(),
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = check_portable_repo(runtime.portable)
    if args.json:
        print(json.dumps(report, indent=2 if args.pretty else None, ensure_ascii=False))
    else:
        print(
            f"portable check: {report['status']} ({report['errors']} errors, {report['warnings']} warnings)"
        )
        for issue in report["issues"]:
            print(
                f"{issue['severity']}: {issue['code']}: {issue['path']}: {issue['message']}"
            )
    return (
        1
        if report["status"] == "fail"
        or (args.strict and report["status"] == "warn")
        else 0
    )


def _portable_command_config(command: str) -> PortableConfig:
    try:
        cwd = Path.cwd()
    except OSError as exc:
        raise ConfigError(
            f"{command} requires a portable repository: "
            f"cannot resolve current working directory: {exc}"
        ) from exc
    try:
        runtime = resolve_config(
            cwd=cwd,
            home=HOME,
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
    except ConfigError as exc:
        raise ConfigError(f"{command} requires a portable repository: {exc}") from exc
    except OSError as exc:
        raise ConfigError(
            f"{command} requires a portable repository: "
            f"cannot resolve portable configuration from {cwd}: {exc}"
        ) from exc
    if runtime.mode != "portable" or runtime.portable is None:
        raise ConfigError(f"{command} requires a portable repository")
    return runtime.portable


def _json_print(
    payload: object, *, pretty: bool = False, ensure_ascii: bool = False
) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=ensure_ascii,
            indent=2 if pretty else None,
        )
    )


def _transaction_manager():
    from obsidian_wiki.transaction import TransactionManager

    return TransactionManager(_portable_command_config("transaction"))


def _transaction_error_code(error: Exception) -> str:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError

    if isinstance(error, ConfigError):
        return "config-error"
    if isinstance(error, ManifestError):
        return "manifest-error"
    if isinstance(error, TransactionError):
        seen = {id(error)}
        cause = error.__cause__
        while cause is not None and id(cause) not in seen:
            if isinstance(cause, ManifestError):
                return "manifest-error"
            seen.add(id(cause))
            cause = cause.__cause__
        return "transaction-error"
    raise TypeError(f"unsupported transaction error: {type(error).__name__}")


def _trusted_recovery_guidance(manager, transaction_id: str | None):
    from obsidian_wiki.transaction import TransactionError
    from obsidian_wiki.transaction_guidance import (
        guidance_for_record,
        inspection_only_guidance,
    )

    if manager is None or transaction_id is None:
        return inspection_only_guidance()
    try:
        record = manager.load(transaction_id)
    except (TransactionError, OSError, UnicodeError):
        return inspection_only_guidance()
    return guidance_for_record(record)


def _terminal_safe_text(value: object) -> str:
    return "".join(
        character
        if character.isprintable()
        else character.encode("unicode_escape").decode("ascii")
        for character in str(value)
    )


def _render_transaction_failure(
    args: argparse.Namespace,
    error: Exception,
    *,
    manager=None,
    transaction_id: str | None = None,
) -> int:
    guidance = _trusted_recovery_guidance(manager, transaction_id)
    payload = {
        "status": "error",
        "error": {
            "code": _transaction_error_code(error),
            "message": str(error),
        },
        "recovery": guidance.as_dict(),
    }
    if args.json:
        _json_print(payload, pretty=args.pretty, ensure_ascii=True)
        return 1

    print(f"error: {_terminal_safe_text(error)}", file=sys.stderr)
    if guidance.transaction_status is not None:
        print(
            f"transaction status: {guidance.transaction_status}",
            file=sys.stderr,
        )
    print(f"inspect: {guidance.inspect_command}", file=sys.stderr)
    if guidance.preferred_action is not None:
        action = guidance.preferred_action
        print(f"preferred: {action.command} — {action.reason}", file=sys.stderr)
        for requirement in action.requires:
            print(f"  requires: {requirement}", file=sys.stderr)
    for action in guidance.alternatives:
        print(f"alternative: {action.command} — {action.reason}", file=sys.stderr)
        for requirement in action.requires:
            print(f"  requires: {requirement}", file=sys.stderr)
    return 1


def _transaction_source(config: PortableConfig, raw: str) -> Path:
    from obsidian_wiki.transaction import TransactionError

    try:
        source = Path(raw).expanduser()
        if source.is_absolute():
            return source
        cwd_candidate = (Path.cwd() / source).absolute()
        if cwd_candidate.exists() or cwd_candidate.is_symlink():
            return cwd_candidate
        return (config.root / source).absolute()
    except (OSError, RuntimeError) as exc:
        raise TransactionError(
            f"cannot resolve transaction source path {raw!r}: {exc}"
        ) from exc


def _record_payload(record) -> dict[str, object]:
    return {
        "transaction_id": record.transaction_id,
        "status": record.status,
        "started_at": record.started_at,
        "source_ids": list(record.source_ids),
        "workspace": str(record.workspace),
        "candidate_vault": str(record.candidate_vault),
        "snapshots": str(record.workspace / "snapshots"),
        "deletions": list(record.deletions),
    }


def _list_record_payload(record, guidance) -> dict[str, object]:
    payload = _record_payload(record)
    payload.update(
        {
            "recommended_action": (
                guidance.preferred_action.as_dict()
                if guidance.preferred_action is not None
                else None
            ),
            "allowed_actions": [
                action.as_dict() for action in guidance.allowed_actions
            ],
        }
    )
    return payload


def _commit_payload(result) -> dict[str, object]:
    return {
        "transaction_id": result.transaction_id,
        "created": list(result.created),
        "updated": list(result.updated),
        "removed": list(result.removed),
        "operation_path": result.operation_path,
    }


def cmd_transaction_begin(args: argparse.Namespace) -> int:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError, TransactionManager

    manager = None
    try:
        config = _portable_command_config("transaction begin")
        manager = TransactionManager(config)
        record = manager.begin(
            [_transaction_source(config, raw) for raw in args.sources]
        )
    except (ConfigError, ManifestError, TransactionError) as exc:
        return _render_transaction_failure(args, exc, manager=manager)
    payload = _record_payload(record)
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(f"transaction {record.transaction_id}: {record.candidate_vault}")
    return 0


def cmd_transaction_list(args: argparse.Namespace) -> int:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError
    from obsidian_wiki.transaction_guidance import guidance_for_record

    manager = None
    try:
        manager = _transaction_manager()
        records = manager.list_transactions()
    except (ConfigError, ManifestError, TransactionError) as exc:
        return _render_transaction_failure(args, exc, manager=manager)
    guided_records = [
        (record, guidance_for_record(record)) for record in records
    ]
    payload = [
        _list_record_payload(record, guidance)
        for record, guidance in guided_records
    ]
    if args.json:
        _json_print(payload, pretty=args.pretty)
    elif not records:
        print("No retained transactions.")
    else:
        for record, guidance in guided_records:
            recommended = (
                guidance.preferred_action.command
                if guidance.preferred_action is not None
                else "-"
            )
            print(
                f"{record.transaction_id}\t{record.status}\t"
                f"{recommended}\t{record.workspace}"
            )
    return 0


def cmd_transaction_delete(args: argparse.Namespace) -> int:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError

    manager = None
    try:
        manager = _transaction_manager()
        manager.mark_delete(args.transaction_id, args.path)
    except (ConfigError, ManifestError, TransactionError) as exc:
        return _render_transaction_failure(
            args,
            exc,
            manager=manager,
            transaction_id=args.transaction_id,
        )
    payload = {
        "transaction_id": args.transaction_id,
        "deleted": args.path,
    }
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(f"transaction {args.transaction_id}: delete {args.path}")
    return 0


def cmd_transaction_validate(args: argparse.Namespace) -> int:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError

    manager = None
    try:
        manager = _transaction_manager()
        report = manager.validate(args.transaction_id)
    except (ConfigError, ManifestError, TransactionError) as exc:
        return _render_transaction_failure(
            args,
            exc,
            manager=manager,
            transaction_id=args.transaction_id,
        )
    payload = report.as_dict()
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(
            f"transaction {args.transaction_id}: {report.status} "
            f"({len(report.issues)} issues)"
        )
        for issue in report.issues:
            print(
                f"{_terminal_safe_text(issue.code)}: "
                f"{_terminal_safe_text(issue.path)}: "
                f"{_terminal_safe_text(issue.message)}"
            )
    return 1 if report.status == "fail" else 0


def _run_transaction_commit(args: argparse.Namespace, *, retry: bool) -> int:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError

    manager = None
    try:
        manager = _transaction_manager()
        action = manager.retry if retry else manager.commit
        result = action(args.transaction_id)
    except (ConfigError, ManifestError, TransactionError) as exc:
        return _render_transaction_failure(
            args,
            exc,
            manager=manager,
            transaction_id=args.transaction_id,
        )
    payload = _commit_payload(result)
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(
            f"transaction {result.transaction_id}: "
            f"{len(result.created)} created, {len(result.updated)} updated, "
            f"{len(result.removed)} removed; {result.operation_path}"
        )
    return 0


def cmd_transaction_commit(args: argparse.Namespace) -> int:
    return _run_transaction_commit(args, retry=False)


def cmd_transaction_retry(args: argparse.Namespace) -> int:
    return _run_transaction_commit(args, retry=True)


def _transaction_state_action(args: argparse.Namespace, action_name: str) -> int:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError

    manager = None
    try:
        manager = _transaction_manager()
        action = getattr(manager, action_name)
        action(args.transaction_id)
    except (ConfigError, ManifestError, TransactionError) as exc:
        return _render_transaction_failure(
            args,
            exc,
            manager=manager,
            transaction_id=args.transaction_id,
        )
    payload = {
        "transaction_id": args.transaction_id,
        "status": {
            "restore": "restored",
            "discard": "discarded",
            "abort": "aborted",
        }[action_name],
    }
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(f"transaction {args.transaction_id}: {payload['status']}")
    return 0


def cmd_transaction_restore(args: argparse.Namespace) -> int:
    return _transaction_state_action(args, "restore")


def cmd_transaction_discard(args: argparse.Namespace) -> int:
    return _transaction_state_action(args, "discard")


def cmd_transaction_abort(args: argparse.Namespace) -> int:
    return _transaction_state_action(args, "abort")


def cmd_hot_status(args: argparse.Namespace) -> int:
    from obsidian_wiki.local_state import hot_status

    status = hot_status(
        _portable_command_config("hot status"),
        invalidate=True,
    )
    if args.json:
        _json_print(status, pretty=args.pretty)
    else:
        print("stale" if status["stale"] else "current")
    return 0


def cmd_hot_mark_current(args: argparse.Namespace) -> int:
    from obsidian_wiki.local_state import mark_hot_current

    mark_hot_current(_portable_command_config("hot mark-current"))
    payload = {"stale": False, "status": "current"}
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print("hot.md marked current")
    return 0


def cmd_hot_inputs(args: argparse.Namespace) -> int:
    from obsidian_wiki.local_state import hot_inputs

    payload = hot_inputs(
        _portable_command_config("hot inputs"),
        page_limit=args.pages,
        operation_limit=args.operations,
    )
    _json_print(payload, pretty=args.pretty)
    return 0


def cmd_ast_extract(args: argparse.Namespace) -> int:
    from pathlib import Path

    from obsidian_wiki.ast_extractor import extract

    path = Path(args.path).expanduser().resolve()
    try:
        result = extract(path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.pretty:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    inspection = _inspect_cli_runtime(args.vault)
    report = run_doctor(
        vault_override=args.vault,
        project_dir=args.project,
        inspection=inspection,
    )
    if args.json:
        if args.pretty:
            print(json.dumps(report, indent=2))
        else:
            print(json.dumps(report))
    else:
        _emit_context_warnings(inspection)
        _print_doctor(report)
    statuses = {check["status"] for check in report["checks"]}
    if "fail" in statuses or (args.strict and "warn" in statuses):
        return 1
    return 0


def _print_lint(report: dict[str, object]) -> None:
    print(f"obsidian-wiki lint: {report['status']}")
    stats = report["stats"]
    print(f"pages: {stats['pages']}  links: {stats['link_count']}")
    for name, count in stats["findings"].items():
        print(f"{name}: {count}")


def _schema_csv(config: dict[str, str], key: str) -> list[str]:
    if key not in config:
        return []
    values = [item.strip() for item in config[key].split(",")]
    if any(not item for item in values):
        raise ValueError(f"invalid {key} value: entries must not be empty")
    return values


def _schema_cli_values(values: list[str] | None, flag: str) -> list[str]:
    normalised = [item.strip() for item in values or []]
    if any(not item for item in normalised):
        raise ValueError(f"invalid {flag} value: must not be empty")
    return normalised


def _schema_source_value(
    args: argparse.Namespace,
    config: dict[str, str],
) -> str | None:
    configured_value: str | None = None
    if "OBSIDIAN_SCHEMA_SOURCE" in config:
        configured_value = config["OBSIDIAN_SCHEMA_SOURCE"].strip()
        if not configured_value:
            raise ValueError("invalid OBSIDIAN_SCHEMA_SOURCE value: must not be empty")

    cli_value = getattr(args, "schema_source", None)
    if cli_value is not None:
        value = cli_value.strip()
        if not value:
            raise ValueError("invalid --schema-source value: must not be empty")
        return value
    return configured_value


def _schema_options(
    args: argparse.Namespace,
    config: dict[str, str],
    config_source: str,
    *,
    default_required_trust_fields: tuple[str, ...] | None = None,
) -> SchemaOptions:
    from obsidian_wiki.lint import (
        ALLOWED_RELATIONSHIP_TYPES,
        TRUST_REQUIRED_FRONTMATTER,
    )
    from obsidian_wiki.trust import (
        ALLOWED_LIFECYCLES,
        TRUST_REQUIRED_FIELD_ALLOWLIST,
    )

    cli_lifecycles = _schema_cli_values(
        getattr(args, "allow_lifecycle", None), "--allow-lifecycle"
    )
    cli_relationships = _schema_cli_values(
        getattr(args, "allow_relationship_type", None), "--allow-relationship-type"
    )
    raw_cli_required = getattr(args, "required_trust_field", None)
    cli_required = (
        _schema_cli_values(raw_cli_required, "--required-trust-field")
        if raw_cli_required is not None
        else None
    )
    configured_lifecycles = _schema_csv(config, "OBSIDIAN_ALLOWED_LIFECYCLES")
    configured_relationships = _schema_csv(
        config, "OBSIDIAN_ALLOWED_RELATIONSHIP_TYPES"
    )
    configured_required = _schema_csv(config, "OBSIDIAN_REQUIRED_TRUST_FIELDS")
    unknown_required = sorted(
        set(configured_required).union(cli_required or ())
        - TRUST_REQUIRED_FIELD_ALLOWLIST
    )
    if unknown_required:
        allowed = ", ".join(sorted(TRUST_REQUIRED_FIELD_ALLOWLIST))
        unknown = ", ".join(unknown_required)
        raise ValueError(
            "invalid OBSIDIAN_REQUIRED_TRUST_FIELDS value(s): "
            f"{unknown}; allowed values: {allowed}"
        )
    required = tuple(
        cli_required
        if cli_required is not None
        else configured_required
        or list(default_required_trust_fields or TRUST_REQUIRED_FRONTMATTER)
    )
    cli_overrides = bool(
        cli_lifecycles or cli_relationships or cli_required is not None
    )
    configured_overrides = bool(
        configured_lifecycles or configured_relationships or configured_required
    )
    source = _schema_source_value(args, config)
    if not source:
        if cli_overrides and configured_overrides:
            source = f"cli+config:{config_source}"
        elif cli_overrides:
            source = f"cli:{config_source}"
        elif configured_overrides:
            source = f"config:{config_source}"
        else:
            source = "framework-defaults"
    return {
        "allowed_lifecycles": ALLOWED_LIFECYCLES.union(
            configured_lifecycles, cli_lifecycles
        ),
        "allowed_relationship_types": ALLOWED_RELATIONSHIP_TYPES.union(
            configured_relationships, cli_relationships
        ),
        "required_trust_fields": required,
        "schema_source": source,
    }


def cmd_lint(args: argparse.Namespace) -> int:
    from obsidian_wiki.lint import lint_vault

    resolved = _resolved_inspection(args.vault)
    if resolved is None:
        return 1
    inspection, runtime = resolved
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1
    config = runtime.values
    config_source = _schema_config_source(runtime)

    strict_trust = args.strict_trust or config.get(
        "OBSIDIAN_TRUST_STRICT", ""
    ).strip().lower() in (
        "1",
        "true",
        "yes",
    )
    try:
        schema = _schema_options(args, config, config_source)
        report = lint_vault(
            vault,
            require_trust_ledger=True,
            strict_trust=strict_trust,
            **schema,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = _attach_context_warnings(report, inspection)
    if args.json:
        if args.pretty:
            print(json.dumps(report, indent=2))
        else:
            print(json.dumps(report))
    else:
        _emit_context_warnings(inspection)
        _print_lint(report)
    if report["status"] == "fail" or (args.strict and report["status"] == "warn"):
        return 1
    return 0


def cmd_trust_record(args: argparse.Namespace) -> int:
    from obsidian_wiki.trust import (
        TRUST_LEDGER_RELATIVE_PATH,
        build_trust_ledger,
        check_trust_ledger,
        update_trust_ledger,
        write_trust_ledger,
    )

    resolved = _resolved_inspection(args.vault)
    if resolved is None:
        return 1
    inspection, runtime = resolved
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1
    config = runtime.values
    config_source = _schema_config_source(runtime)
    try:
        schema = _schema_options(
            args,
            config,
            config_source,
            default_required_trust_fields=("base_confidence", "lifecycle", "updated"),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    path = vault / TRUST_LEDGER_RELATIVE_PATH
    try:
        if args.all:
            removed_not_applicable: list[str] = []
            if path.is_file():
                previous = check_trust_ledger(
                    vault,
                    path,
                    allowed_lifecycles=schema["allowed_lifecycles"],
                    required_trust_keys=schema["required_trust_fields"],
                    schema_source=schema["schema_source"],
                )
                removed_not_applicable = sorted(
                    item["page"]
                    for item in previous["stale"]
                    if item.get("reason")
                    == "confidence_not_applicable_but_ledger_entry_exists"
                )
            ledger = build_trust_ledger(
                vault,
                reviewed_at=args.reviewed_at,
                allowed_lifecycles=schema["allowed_lifecycles"],
                required_trust_keys=schema["required_trust_fields"],
            )
            ledger["removed_not_applicable"] = removed_not_applicable
            recorded_pages = len(ledger["pages"])
        else:
            ledger = update_trust_ledger(
                vault,
                path,
                reviewed_at=args.reviewed_at,
                page_paths=args.page,
                allowed_lifecycles=schema["allowed_lifecycles"],
                required_trust_keys=schema["required_trust_fields"],
            )
            requested = {Path(raw).as_posix().removeprefix("./") for raw in args.page}
            recorded_pages = len(requested.intersection(ledger["pages"]))
        write_trust_ledger(path, ledger, vault=vault)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    result = {
        "status": "recorded",
        "ledger_path": str(path),
        "recorded_pages": recorded_pages,
        "not_applicable_pages": list(ledger.get("not_applicable", [])),
        "removed_not_applicable": list(ledger.get("removed_not_applicable", [])),
        "reviewed_at": args.reviewed_at,
        "method": ledger["method"],
        "schema": {
            "source": schema["schema_source"],
            "allowed_lifecycles": sorted(schema["allowed_lifecycles"]),
            "required_trust_fields": list(schema["required_trust_fields"]),
        },
    }
    result = _attach_context_warnings(result, inspection)
    if args.json:
        print(json.dumps(result, indent=2 if args.pretty else None))
    else:
        _emit_context_warnings(inspection)
        print(f"recorded {result['recorded_pages']} reviewed page(s) in {path}")
        print(
            "not applicable (excluded from trust review): "
            f"{len(result['not_applicable_pages'])} page(s)"
        )
        for page in result["not_applicable_pages"]:
            print(f"  - {page}")
        print(
            "obsolete ledger entries removed: "
            f"{len(result['removed_not_applicable'])} page(s)"
        )
        for page in result["removed_not_applicable"]:
            print(f"  - {page}")
        if result["removed_not_applicable"]:
            removed = ", ".join(result["removed_not_applicable"])
            print(
                "warning: removed obsolete trust ledger entries because "
                f"base_confidence is not applicable: {removed}",
                file=sys.stderr,
            )
    return 0


def cmd_trust_check(args: argparse.Namespace) -> int:
    from obsidian_wiki.trust import check_trust_ledger

    resolved = _resolved_inspection(args.vault)
    if resolved is None:
        return 1
    inspection, runtime = resolved
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1
    config = runtime.values
    config_source = _schema_config_source(runtime)
    try:
        schema = _schema_options(
            args,
            config,
            config_source,
            default_required_trust_fields=("base_confidence", "lifecycle", "updated"),
        )
        report = check_trust_ledger(
            vault,
            allowed_lifecycles=schema["allowed_lifecycles"],
            required_trust_keys=schema["required_trust_fields"],
            schema_source=schema["schema_source"],
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = _attach_context_warnings(report, inspection)
    if args.json:
        print(json.dumps(report, indent=2 if args.pretty else None))
    else:
        _emit_context_warnings(inspection)
        print(f"obsidian-wiki trust-check: {report['status']}")
        for name, count in report["counts"].items():
            print(f"{name}: {count}")
    if report["status"] == "fail" or (args.strict and report["status"] == "warn"):
        return 1
    return 0


def _print_query(result: dict[str, object]) -> None:
    print(f"answer_type: {result['answer_type']}")
    candidates = result.get("candidates", [])
    if candidates:
        print("candidates:")
        for item in candidates:
            print(f"- {item['title']} ({item['page']}) score={item['score']}")
    path = result.get("path") or []
    if path:
        print("path:")
        print(" -> ".join(path))
    should_read = result.get("should_read") or []
    if should_read:
        print("should_read:")
        for page in should_read:
            print(f"- {page}")


def cmd_query(args: argparse.Namespace) -> int:
    from obsidian_wiki.graphrag import query

    resolved = _resolved_inspection(args.vault)
    if resolved is None:
        return 1
    inspection, runtime = resolved
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1

    result = _attach_context_warnings(
        query(vault, args.question, top_n=args.top, max_should_read=args.max_read),
        inspection,
    )
    if args.json:
        if args.pretty:
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps(result))
    else:
        _emit_context_warnings(inspection)
        _print_query(result)
    return 0


def cmd_context_pack(args: argparse.Namespace) -> int:
    from obsidian_wiki.context_pack import (
        ContextError,
        build_context_pack,
        render_markdown,
    )

    resolved = _resolved_inspection(args.vault)
    if resolved is None:
        return 1
    inspection, runtime = resolved
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1
    try:
        pack = build_context_pack(
            vault,
            args.topic or "",
            budget=args.budget,
            recent=args.recent,
            public_only=args.public_only,
            metadata_only=args.metadata_only,
        )
    except ContextError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    pack = _attach_context_warnings(pack, inspection)
    if args.json:
        print(json.dumps(pack, indent=2 if args.pretty else None))
    else:
        _emit_context_warnings(inspection)
        print(render_markdown(pack), end="")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for name in list_skills():
        print(name)
    return 0


def _inspect_cli_runtime(vault_arg: str | None = None) -> RuntimeInspection:
    return inspect_runtime(
        vault_arg,
        cwd=Path.cwd(),
        home=HOME,
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )


def _runtime_payload(inspection: RuntimeInspection) -> dict[str, object]:
    runtime = inspection.runtime
    if runtime is None:
        payload: dict[str, object] = {"status": inspection.status}
        if inspection.status == "error":
            assert inspection.error is not None
            payload["error"] = _runtime_error_detail(inspection.error)
        if inspection.guidance is not None:
            payload["guidance"] = inspection.guidance
        return payload

    payload = {
        "status": "resolved",
        "mode": runtime.mode,
        "source": runtime.source,
        "vault": str(runtime.vault),
        "portable": None,
    }
    if runtime.portable is not None:
        portable = runtime.portable
        payload["portable"] = {
            "root": str(portable.root),
            "sources": [str(source) for source in portable.sources],
            "skills": str(portable.skills),
            "local_state": str(portable.local_state),
        }
    return payload


def _agent_install_payload(
    bundled: set[str] | None,
    *,
    warning_sink: list[dict[str, str]] | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for rel, label, _subset in GLOBAL_AGENT_DIRS:
        root = HOME / rel
        root_metadata_failed = False
        try:
            root_is_dir = root.is_dir()
        except OSError as exc:
            root_is_dir = True
            root_metadata_failed = True
            if warning_sink is not None:
                warning_sink.append(_agent_skills_inspection_warning(root, exc))
        installed: set[str] = set()
        if root_is_dir and not root_metadata_failed:
            try:
                installed = _installed_skill_names(
                    root, warning_sink=warning_sink
                )
            except OSError as exc:
                if warning_sink is not None:
                    warning_sink.append(_agent_skills_inspection_warning(root, exc))
        if bundled is None:
            installed_count: int | None = None
            bundled_count: int | None = None
            missing: list[str] | None = None
            status = "not-installed" if not root_is_dir else "unknown"
        else:
            present = installed & bundled
            missing = sorted(bundled - installed)
            installed_count = len(present)
            bundled_count = len(bundled)
            status = (
                "not-installed"
                if not root_is_dir
                else "complete"
                if not missing
                else "partial"
            )
        records.append(
            {
                "label": label,
                "path": str(root),
                "status": status,
                "installed": installed_count,
                "bundled": bundled_count,
                "missing": missing,
            }
        )
    return records


def _installation_payload() -> tuple[dict[str, object], list[dict[str, str]]]:
    from obsidian_wiki.sync import get_remote

    warnings: list[dict[str, str]] = []
    try:
        bundled: list[str] | None = list_skills()
        skill_root: str | None = str(skills_dir())
    except OSError as exc:
        bundled = None
        skill_root = None
        warnings.append(_bundled_skills_inspection_warning(exc))
    bundled_set = set(bundled) if bundled is not None else None
    try:
        boot = bootstrap_dir()
    except OSError as exc:
        boot = None
        warnings.append(
            {
                "code": "installation-bootstrap-unreadable",
                "message": f"could not inspect bootstrap files: {exc}",
                "hint": SOURCE_REINSTALL_HINT,
            }
        )

    try:
        config_present = GLOBAL_CONFIG.is_file()
    except OSError as exc:
        config_present = False
        warnings.append(_global_config_inspection_warning(exc))
    vault: str | None = None
    setup_version: str | None = None
    remote: str | None = None
    if config_present:
        try:
            global_config = load_global_config(GLOBAL_CONFIG, home=HOME)
        except (ConfigError, OSError, UnicodeError) as exc:
            warnings.append(_global_config_inspection_warning(exc))
        else:
            vault = str(global_config.vault)
            setup_version = global_config.values.get("OBSIDIAN_WIKI_VERSION")
            try:
                remote = get_remote(global_config.vault)
            except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
                warnings.append(
                    {
                        "code": "installation-sync-inspection-failed",
                        "message": (
                            f"could not inspect Git remote for {global_config.vault}: "
                            f"{exc}"
                        ),
                        "hint": "check Git availability and vault permissions",
                    }
                )

    payload: dict[str, object] = {
        "version": version_label(),
        "skills": skill_root,
        "bootstrap": str(boot) if boot is not None else None,
        "global_config": str(GLOBAL_CONFIG),
        "global_default": {
            "configured": config_present,
            "vault": vault,
            "setup_version": setup_version,
            "sync_remote": remote,
        },
        "bundled_skills": len(bundled) if bundled is not None else None,
        "agent_installs": _agent_install_payload(
            bundled_set, warning_sink=warnings
        ),
    }
    warnings.extend(
        _stale_install_warnings(
            bundled_set if bundled_set is not None else set(),
            setup_version=setup_version,
        )
    )
    return payload, _deduplicate_warnings(warnings)


def _print_info(payload: dict[str, object]) -> None:
    runtime = payload["runtime"]
    installation = payload["installation"]
    assert isinstance(runtime, dict)
    assert isinstance(installation, dict)

    print("Runtime context")
    for key in ("status", "mode", "source", "vault", "guidance", "error"):
        if key in runtime:
            print(f"  {key}: {runtime[key]}")
    portable = runtime.get("portable")
    if portable is not None:
        assert isinstance(portable, dict)
        print(f"  repository: {portable['root']}")
        sources = portable["sources"]
        assert isinstance(sources, list)
        for source in sources:
            print(f"  source: {source}")
        print(f"  skills: {portable['skills']}")
        print(f"  local state: {portable['local_state']}")

    print()
    print("CLI installation")
    print(f"  version: {installation['version']}")
    bundled_skills = installation["bundled_skills"]
    print(
        "  bundled skills: "
        f"{bundled_skills if bundled_skills is not None else '(unknown)'}"
    )
    print(f"  skills root: {installation['skills']}")
    print(f"  bootstrap: {installation['bootstrap'] or '(not found)'}")
    print(f"  global config: {installation['global_config']}")
    global_default = installation["global_default"]
    assert isinstance(global_default, dict)
    print(f"  global vault: {global_default['vault'] or '(unset)'}")
    agent_installs = installation["agent_installs"]
    assert isinstance(agent_installs, list)
    print("  agent installs:")
    for record in agent_installs:
        assert isinstance(record, dict)
        installed = record["installed"]
        bundled = record["bundled"]
        counts = (
            "?/?"
            if installed is None or bundled is None
            else f"{installed}/{bundled}"
        )
        print(
            f"    {record['label']}: {counts} ({record['status']})"
        )


def cmd_info(args: argparse.Namespace) -> int:
    inspection = _inspect_cli_runtime(args.vault)
    installation, install_warnings = _installation_payload()
    warnings = [warning.as_dict() for warning in inspection.warnings] + install_warnings
    payload: dict[str, object] = {
        "runtime": _runtime_payload(inspection),
        "installation": installation,
        "warnings": warnings,
    }
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        _print_info(payload)
        if inspection.status == "error":
            assert inspection.error is not None
            print(f"error: {_runtime_error_detail(inspection.error)}", file=sys.stderr)
        for warning in warnings:
            print(f"warning: {warning['message']}", file=sys.stderr)
            print(f"  {warning['hint']}", file=sys.stderr)
    return 1 if inspection.status == "error" else 0


def cmd_repo_upgrade_skills(args: argparse.Namespace) -> int:
    warnings: list[dict[str, str]] = []
    try:
        resolved = resolve_config(
            cwd=Path.cwd(),
            home=Path.home(),
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
        if resolved.mode != "portable" or resolved.portable is None:
            raise ConfigError(
                "repo upgrade-skills must run inside a portable repository"
            )
        root = resolved.portable.root
        names = upgrade_portable_skills(
            root,
            version=__version__,
            source_skills=skills_dir(),
            warning_sink=warnings,
        )
    except (ConfigError, ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Upgraded {len(names)} managed repository skills and rebuilt six full "
        f"mirrors at {root} to {__version__}"
    )
    for warning in warnings:
        print(f"warning [{warning['code']}]: {warning['message']}", file=sys.stderr)
    return 0


def cmd_repo_sync_skills(args: argparse.Namespace) -> int:
    root: Path | None = None
    try:
        resolved = resolve_config(
            cwd=Path.cwd(),
            home=Path.home(),
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
        if resolved.mode != "portable" or resolved.portable is None:
            raise ConfigError(
                "repo sync-skills must run inside a portable repository"
            )
        root = resolved.portable.root
        report = sync_portable_skill_mirrors(root, apply=args.apply)
    except (ConfigError, ValueError, OSError, RuntimeError) as exc:
        error = str(exc)
        if root is not None:
            error = error.replace(str(root), ".")
        if args.json:
            _json_print(
                {"status": "error", "error": error, "warnings": []},
                pretty=args.pretty,
            )
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1

    if args.json:
        _json_print(report.as_dict(), pretty=args.pretty)
    elif report.status == "applied":
        print(
            "Rebuilt six derived agent skill roots from .skills/; canonical "
            ".skills/ and the managed-skills inventory are unchanged."
        )
    elif report.status == "clean":
        print("All six derived agent skill roots match canonical .skills/.")
    else:
        print("Portable agent skill mirror drift detected:")
        for target in report.targets:
            changes = len(target.added + target.changed + target.removed + target.unsafe)
            if changes:
                print(f"  - {target.path}: {changes} change(s)")
        print("Run `obsidian-wiki repo sync-skills --apply` to rebuild all mirrors.")
    if not args.json:
        for warning in report.warnings:
            print(f"warning: {warning['path']}: {warning['message']}", file=sys.stderr)
    return 1 if report.status == "drift" else 0


def _migration_path(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def _already_portable(root: Path, vault: Path, source_root: Path) -> bool:
    config_path = root / ".obsidian-wiki/config.toml"
    marker_path = vault / ".manifest.json"
    try:
        config = load_portable_config(
            config_path,
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (ConfigError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        config.root == root
        and config.vault == vault
        and source_root in config.sources
        and marker == MANIFEST_MARKER
    )


def _migration_apply_command(args: argparse.Namespace) -> str:
    return shlex.join(
        [
            "obsidian-wiki",
            "repo",
            "migrate",
            "--root",
            args.root,
            "--vault",
            args.vault,
            "--sources",
            args.sources,
            "--apply",
        ]
    )


def _migration_payload(
    plan: MigrationPlan, *, status: str, mode: str
) -> dict[str, object]:
    payload = plan.to_dict()
    payload["status"] = status
    payload["mode"] = mode
    return payload


def _print_migration_plan(plan: MigrationPlan, args: argparse.Namespace) -> None:
    sections = (
        (
            "Mappings",
            [f"{old} -> {source_id}" for old, source_id in plan.source_mappings],
        ),
        ("Page updates", list(plan.page_updates)),
        ("Manifest shards", list(plan.manifest_entries)),
        ("Warnings", list(plan.warnings)),
        (
            "Blockers",
            [
                f"[{blocker.code}] {blocker.source}: {blocker.message}"
                for blocker in plan.blockers
            ],
        ),
    )
    for heading, lines in sections:
        print(f"{heading}:")
        if lines:
            for line in lines:
                print(f"  - {line}")
        else:
            print("  (none)")
    if not plan.blockers:
        print()
        print("Apply with:")
        print(f"  {_migration_apply_command(args)}")


def cmd_repo_migrate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve(strict=False)
    vault = _migration_path(root, args.vault)
    source_root = _migration_path(root, args.sources)
    mode = "apply" if args.apply else "dry-run"
    plan = analyze_migration(root=root, vault=vault, source_root=source_root)

    if _already_portable(root, vault, source_root):
        if args.json:
            payload = _migration_payload(plan, status="already-portable", mode=mode)
            payload["source_mappings"] = []
            payload["page_updates"] = []
            payload["manifest_entries"] = []
            payload["blockers"] = []
            payload["warnings"] = []
            print(json.dumps(payload, indent=2 if args.pretty else None))
        else:
            print(f"Repository at {root} is already portable; no files changed.")
        return 0

    if plan.blockers:
        if args.json:
            payload = _migration_payload(plan, status="blocked", mode=mode)
            print(json.dumps(payload, indent=2 if args.pretty else None))
        else:
            _print_migration_plan(plan, args)
        return 1

    if not args.apply:
        if args.json:
            payload = _migration_payload(plan, status="ready", mode=mode)
            payload["apply_command"] = _migration_apply_command(args)
            print(json.dumps(payload, indent=2 if args.pretty else None))
        else:
            _print_migration_plan(plan, args)
        return 0

    try:
        result = apply_migration(
            plan,
            installed_version=__version__,
            source_skills=skills_dir(),
        )
    except MigrationError as exc:
        if args.json:
            payload = _migration_payload(plan, status="error", mode=mode)
            payload["error"] = str(exc)
            print(json.dumps(payload, indent=2 if args.pretty else None))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        backup_dir = result.backup_dir.relative_to(root).as_posix()
    except ValueError:
        backup_dir = str(result.backup_dir)
    if args.json:
        payload = _migration_payload(plan, status="applied", mode=mode)
        payload["changed_files"] = list(result.changed_files)
        payload["removed_files"] = list(result.removed_files)
        payload["backup_dir"] = backup_dir
        print(json.dumps(payload, indent=2 if args.pretty else None))
    else:
        print("Migration applied. No commit or push was performed.")
        print("Changed files:")
        for path in result.changed_files:
            print(f"  - {path}")
        print("Removed files:")
        if result.removed_files:
            for path in result.removed_files:
                print(f"  - {path}")
        else:
            print("  (none)")
        print(f"Backup: {backup_dir}")
    return 0


# ── Argument parsing ─────────────────────────────────────────────────────────
class _ArgumentParseError(Exception):
    def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
        super().__init__(message)
        self.parser = parser
        self.message = message


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentParseError(self, message)


def _add_json_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--pretty", action="store_true", help="pretty-print JSON output"
    )


_TRANSACTION_SUBCOMMANDS = frozenset(
    {
        "begin",
        "list",
        "delete",
        "validate",
        "commit",
        "retry",
        "restore",
        "discard",
        "abort",
    }
)


def _transaction_option_intent(argv: list[str]) -> tuple[bool, bool, bool]:
    if not argv or argv[0] != "transaction":
        return False, False, False

    parent_tokens: list[str] = []
    leaf_index: int | None = None
    parent_separator: int | None = None
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in _TRANSACTION_SUBCOMMANDS:
            leaf_index = index
            break
        if token == "--":
            parent_separator = index
            index += 1
            if index < len(argv) and argv[index] in _TRANSACTION_SUBCOMMANDS:
                leaf_index = index
            break
        if not token.startswith("-"):
            break
        parent_tokens.append(token)
        index += 1

    leaf_tokens: list[str] = []
    if leaf_index is not None:
        for token in argv[leaf_index + 1 :]:
            if token == "--":
                break
            leaf_tokens.append(token)
        option_tokens = (*parent_tokens, *leaf_tokens)
    else:
        if parent_separator is None:
            error_tokens = argv[1:]
        else:
            error_tokens = [
                *argv[1:parent_separator],
                *argv[parent_separator + 1 :],
            ]
        try:
            separator = error_tokens.index("--")
        except ValueError:
            pass
        else:
            error_tokens = error_tokens[:separator]
        option_tokens = tuple(error_tokens)
    return (
        "--json" in option_tokens,
        "--pretty" in option_tokens,
        "-h" in option_tokens or "--help" in option_tokens,
    )


def _normalize_cache_check_argv(argv: list[str]) -> list[str]:
    """Move known zero-argument options ahead of cache-check PATH values."""
    if not argv or argv[0] != "cache-check":
        return argv
    try:
        separator = argv.index("--", 1)
    except ValueError:
        before_separator = argv[1:]
        after_separator: list[str] = []
    else:
        before_separator = argv[1:separator]
        after_separator = argv[separator:]
    option_names = frozenset({"--configured", "--json", "--pretty"})
    options = [token for token in before_separator if token in option_names]
    paths = [token for token in before_separator if token not in option_names]
    return [argv[0], *options, *paths, *after_separator]


def _normalize_transaction_parent_separator(argv: list[str]) -> list[str]:
    """Let ``transaction -- <known-command>`` select its nested parser."""
    if (
        len(argv) >= 3
        and argv[0] == "transaction"
        and argv[1] == "--"
        and argv[2] in _TRANSACTION_SUBCOMMANDS
    ):
        return [argv[0], *argv[2:]]
    return argv


def build_parser() -> argparse.ArgumentParser:
    p = _ArgumentParser(
        prog="obsidian-wiki",
        description="Install the LLM-Wiki agent skills into your AI coding agents.",
    )
    p.add_argument("-V", "--version", action="version", version=version_label())
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser(
        "setup", help="install skills into your agents and write config (default)"
    )
    _add_setup_args(sp)
    sp.set_defaults(func=cmd_setup)

    ssp = sub.add_parser(
        "sync-setup",
        help="configure GitHub sync for your vault (git init, .gitignore, remote)",
    )
    ssp.add_argument(
        "remote",
        help="GitHub (or any git host) repo URL, e.g. https://github.com/you/my-wiki.git",
    )
    ssp.add_argument(
        "--vault", metavar="PATH", help="absolute path to your Obsidian vault"
    )
    ssp.set_defaults(func=cmd_sync_setup)

    syp = sub.add_parser(
        "sync", help="commit and push pending vault changes (git add -A, commit, push)"
    )
    syp.add_argument(
        "--vault", metavar="PATH", help="absolute path to your Obsidian vault"
    )
    syp.set_defaults(func=cmd_sync)

    lp = sub.add_parser("list", help="list bundled skills")
    lp.set_defaults(func=cmd_list)

    ip = sub.add_parser("info", help="show install paths, version, and config")
    ip.add_argument(
        "--vault", metavar="PATH", help="preview PATH or @name for this invocation"
    )
    _add_json_args(ip)
    ip.set_defaults(func=cmd_info)

    rp = sub.add_parser("repo", help="maintain a portable repository")
    repo_sub = rp.add_subparsers(dest="repo_command", required=True)
    rus = repo_sub.add_parser(
        "upgrade-skills",
        help="upgrade managed skills and rebuild full agent mirrors from this CLI",
    )
    rus.set_defaults(func=cmd_repo_upgrade_skills)

    rss = repo_sub.add_parser(
        "sync-skills",
        help="check or rebuild agent skill mirrors from repository-canonical .skills",
    )
    rss.add_argument(
        "--apply",
        action="store_true",
        help="replace all derived mirrors from .skills",
    )
    _add_json_args(rss)
    rss.set_defaults(func=cmd_repo_sync_skills)

    migrate = repo_sub.add_parser(
        "migrate",
        help="analyze or apply a legacy-to-portable repository migration",
    )
    migrate.add_argument("--root", required=True, help="migration repository root")
    migrate.add_argument(
        "--vault", required=True, help="vault path, resolved against --root"
    )
    migrate.add_argument(
        "--sources", required=True, help="source root, resolved against --root"
    )
    migrate.add_argument(
        "--apply", action="store_true", help="apply the analyzed migration plan"
    )
    _add_json_args(migrate)
    migrate.set_defaults(func=cmd_repo_migrate)

    transaction = sub.add_parser(
        "transaction",
        help="stage, promote, and recover portable repository writes",
        allow_abbrev=False,
    )
    transaction_sub = transaction.add_subparsers(
        dest="transaction_command", required=True
    )
    transaction_begin = transaction_sub.add_parser(
        "begin",
        help="begin one local portable write transaction",
        allow_abbrev=False,
    )
    transaction_begin.add_argument(
        "--source",
        dest="sources",
        nargs="+",
        required=True,
        metavar="PATH",
        help="one or more authoritative source paths",
    )
    _add_json_args(transaction_begin)
    transaction_begin.set_defaults(func=cmd_transaction_begin)

    transaction_list = transaction_sub.add_parser(
        "list",
        help="list active and retained recovery transactions",
        allow_abbrev=False,
    )
    _add_json_args(transaction_list)
    transaction_list.set_defaults(func=cmd_transaction_list)

    transaction_delete = transaction_sub.add_parser(
        "delete",
        help="declare one vault-relative page removal",
        allow_abbrev=False,
    )
    transaction_delete.add_argument("transaction_id")
    transaction_delete.add_argument("path", help="vault-relative knowledge page path")
    _add_json_args(transaction_delete)
    transaction_delete.set_defaults(func=cmd_transaction_delete)

    transaction_validate = transaction_sub.add_parser(
        "validate",
        help="validate a staged transaction without modifying it",
        allow_abbrev=False,
    )
    transaction_validate.add_argument("transaction_id")
    _add_json_args(transaction_validate)
    transaction_validate.set_defaults(func=cmd_transaction_validate)

    for name, help_text, function in (
        ("commit", "promote an active transaction", cmd_transaction_commit),
        ("retry", "retry a retained failed transaction", cmd_transaction_retry),
        (
            "restore",
            "restore a failed or completed transaction",
            cmd_transaction_restore,
        ),
        ("discard", "discard retained recovery state", cmd_transaction_discard),
        ("abort", "abort active or failed staged work", cmd_transaction_abort),
    ):
        command = transaction_sub.add_parser(
            name, help=help_text, allow_abbrev=False
        )
        command.add_argument("transaction_id")
        _add_json_args(command)
        command.set_defaults(func=function)

    hot = sub.add_parser("hot", help="inspect local derived hot.md state")
    hot_sub = hot.add_subparsers(dest="hot_command", required=True)
    hot_status_parser = hot_sub.add_parser(
        "status", help="report hot.md freshness and remove it when stale"
    )
    _add_json_args(hot_status_parser)
    hot_status_parser.set_defaults(func=cmd_hot_status)
    hot_mark = hot_sub.add_parser(
        "mark-current", help="record an Agent-written hot.md as current"
    )
    _add_json_args(hot_mark)
    hot_mark.set_defaults(func=cmd_hot_mark_current)
    hot_inputs_parser = hot_sub.add_parser(
        "inputs", help="emit deterministic inputs for an Agent-written hot.md"
    )
    hot_inputs_parser.add_argument(
        "--pages",
        type=int,
        default=50,
        help="maximum page summaries to emit (default: 50)",
    )
    hot_inputs_parser.add_argument(
        "--operations",
        type=int,
        default=10,
        help="maximum operation records to emit (default: 10)",
    )
    _add_json_args(hot_inputs_parser)
    hot_inputs_parser.set_defaults(func=cmd_hot_inputs, json=True)

    gq = sub.add_parser(
        "graph-query",
        help="answer a question from the vault's wikilink index without reading page bodies",
    )
    gq.add_argument("vault", help="path to the Obsidian vault")
    gq.add_argument("question", help="question to answer")
    gq.add_argument(
        "--top",
        type=int,
        default=8,
        help="number of candidate pages to rank (default: 8)",
    )
    gq.add_argument(
        "--max-read",
        type=int,
        default=3,
        help="max pages to return in should_read (default: 3)",
    )
    gq.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    gq.set_defaults(func=cmd_graph_query)

    bp = sub.add_parser(
        "batch-plan",
        help="split a source directory into parallel-ingest batches, skipping unchanged files",
    )
    bp.add_argument("vault", help="path to the Obsidian vault")
    bp.add_argument("source_dir", help="directory of source documents to ingest")
    bp.add_argument(
        "--max-mb", type=float, default=2.0, help="max MB per batch (default: 2)"
    )
    bp.add_argument(
        "--max-files", type=int, default=20, help="max files per batch (default: 20)"
    )
    bp.add_argument(
        "--no-cache",
        action="store_true",
        help="disable manifest-based skip of unchanged files",
    )
    bp.add_argument(
        "--include-code",
        action="store_true",
        help="include code files (default: excluded; use ast-extract instead)",
    )
    bp.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    bp.set_defaults(func=cmd_batch_plan)

    ga = sub.add_parser(
        "graph-analyse",
        help="analyse the vault's wikilink graph: god nodes, communities, surprising connections",
    )
    ga.add_argument("vault", help="path to the Obsidian vault")
    ga.add_argument(
        "--top",
        type=int,
        default=20,
        help="number of top results to return (default: 20)",
    )
    ga.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    ga.set_defaults(func=cmd_graph_analyse)

    sb = sub.add_parser(
        "sessions-build",
        help="build a topic graph over your agent session history (writes a sidecar, not the vault)",
    )
    sb.add_argument(
        "--claude-dir",
        default=DEFAULT_CLAUDE_DIR,
        help=f"agent session cache to read (default: {DEFAULT_CLAUDE_DIR})",
    )
    sb.add_argument(
        "--out",
        default=None,
        help=f"output directory (default: $WIKI_SESSION_BRAIN_DIR or {DEFAULT_BRAIN_DIR})",
    )
    sb.add_argument(
        "--k", type=int, default=8, help="neighbours per session (default: 8)"
    )
    sb.add_argument(
        "--min-sim",
        type=float,
        default=0.08,
        help="minimum cosine similarity for an edge (default: 0.08)",
    )
    sb.add_argument(
        "--mutual",
        action="store_true",
        help="keep only mutual kNN edges — tighter, smaller clusters",
    )
    sb.add_argument(
        "--half-life",
        type=float,
        default=90.0,
        help="recency half-life in days (default: 90)",
    )
    sb.add_argument(
        "--since", help="only read sessions modified on or after this ISO date"
    )
    sb.add_argument(
        "--skip",
        help="comma-separated substrings of project dirs to skip (or $WIKI_SKIP_PROJECTS). "
        "Cache dir names begin with '-', which argparse reads as a flag — pass the "
        "bare name ('game') or use --skip=-w-game",
    )
    sb.add_argument(
        "--full", action="store_true", help="ignore caches and re-read every session"
    )
    sb.add_argument("--no-html", action="store_true", help="skip writing graph.html")
    sb.add_argument(
        "--bookmarks",
        help="path to bookmarks.json (default: ~/.bookmark-agent/bookmarks.json)",
    )
    sb.add_argument(
        "--json", action="store_true", help="emit JSON instead of a human summary"
    )
    sb.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    sb.add_argument(
        "-v", "--verbose", action="store_true", help="report progress to stderr"
    )
    sb.set_defaults(func=cmd_sessions_build)

    sq = sub.add_parser(
        "sessions-query",
        help="find the sessions most relevant to a topic, ranked by similarity and recency",
    )
    sq.add_argument("question", help="topic or question to search for")
    sq.add_argument("--out", default=None, help="session-brain directory")
    sq.add_argument(
        "--top", type=int, default=10, help="candidates to return (default: 10)"
    )
    sq.add_argument(
        "--max-load",
        type=int,
        default=3,
        help="max sessions to recommend loading (default: 3)",
    )
    sq.add_argument(
        "--half-life",
        type=float,
        default=None,
        help="override the recency half-life used at build time",
    )
    sq.add_argument("--project", help="restrict to one project")
    sq.add_argument("--cluster", type=int, help="restrict to one topic cluster id")
    sq.add_argument(
        "--since", help="only consider sessions ending on or after this ISO date"
    )
    sq.add_argument(
        "--min-score", type=float, default=0.05, help="drop candidates below this score"
    )
    sq.add_argument(
        "--json", action="store_true", help="emit JSON instead of a human summary"
    )
    sq.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    sq.set_defaults(func=cmd_sessions_query)

    ssh = sub.add_parser(
        "sessions-show",
        help="show one session's graph node and its nearest neighbours",
    )
    ssh.add_argument("session_id", help="session id (full or unique prefix)")
    ssh.add_argument("--out", default=None, help="session-brain directory")
    ssh.add_argument(
        "--neighbors", type=int, default=8, help="neighbours to include (default: 8)"
    )
    ssh.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    ssh.set_defaults(func=cmd_sessions_show)

    scl = sub.add_parser("sessions-clusters", help="list the discovered topic clusters")
    scl.add_argument("--out", default=None, help="session-brain directory")
    scl.add_argument(
        "--unnamed", action="store_true", help="only clusters that still need a name"
    )
    scl.add_argument(
        "--top", type=int, default=20, help="max clusters to list (default: 20)"
    )
    scl.add_argument(
        "--json", action="store_true", help="emit JSON instead of a human summary"
    )
    scl.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    scl.set_defaults(func=cmd_sessions_clusters)

    snm = sub.add_parser(
        "sessions-name", help="assign names to topic clusters (durable across rebuilds)"
    )
    snm.add_argument("--out", default=None, help="session-brain directory")
    snm.add_argument(
        "--from",
        dest="from_file",
        required=True,
        metavar="FILE",
        help='JSON array of {"id": N, "name": "...", "summary": "..."}; use - for stdin',
    )
    snm.set_defaults(func=cmd_sessions_name)

    cc = sub.add_parser(
        "cache-check",
        help="check which sources are new/modified/unchanged vs. .manifest.json",
    )
    cc.add_argument("path", metavar="PATH")
    cc.add_argument("paths", nargs="*", metavar="PATH")
    cc.add_argument(
        "--configured",
        action="store_true",
        help="resolve the vault from config and treat every PATH as a source",
    )
    _add_json_args(cc)
    cc.set_defaults(func=cmd_cache_check, json=True)

    cu = sub.add_parser(
        "cache-update",
        help="record a source's current SHA-256 hash in .manifest.json after ingestion",
    )
    cu.add_argument("vault", help="path to the Obsidian vault")
    cu.add_argument("source", help="source file or directory that was just ingested")
    cu.add_argument(
        "--pages",
        nargs="*",
        metavar="PAGE",
        help="vault-relative paths of pages produced",
    )
    _add_json_args(cu)
    cu.set_defaults(func=cmd_cache_update, json=True)

    ch = sub.add_parser(
        "cache-hash",
        help="compute the SHA-256 hash of a file or directory (no manifest I/O)",
    )
    ch.add_argument("path", help="file or directory to hash")
    _add_json_args(ch)
    ch.set_defaults(func=cmd_cache_hash, json=True)

    ck = sub.add_parser("check", help="validate a portable repository without an LLM")
    ck.add_argument("--json", action="store_true", help="emit a JSON report")
    ck.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    ck.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on warnings as well as failures",
    )
    ck.set_defaults(func=cmd_check)

    ap = sub.add_parser(
        "ast-extract",
        help="extract code structure (classes, functions, imports) from a file or directory — no LLM, no API calls",
    )
    ap.add_argument("path", help="file or directory to extract from")
    ap.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    ap.set_defaults(func=cmd_ast_extract)

    dr = sub.add_parser(
        "doctor",
        help="check config, vault shape, bootstrap assets, and installed skills",
    )
    dr.add_argument(
        "--vault", help="override OBSIDIAN_VAULT_PATH for this health check"
    )
    dr.add_argument(
        "--project", help="also check project-local bootstrap files in this directory"
    )
    dr.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    dr.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    dr.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on warnings as well as failures",
    )
    dr.set_defaults(func=cmd_doctor)

    lt = sub.add_parser(
        "lint",
        help="lint a vault for missing frontmatter, broken links, duplicates, and orphans",
    )
    lt.add_argument(
        "vault",
        nargs="?",
        help="vault path or @name (defaults via CWD .env, then global config)",
    )
    lt.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    lt.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    lt.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on warnings as well as failures",
    )
    lt.add_argument(
        "--strict-trust",
        action="store_true",
        help=(
            "fail lint on missing trust fields, ledger errors, stale reviews, and "
            "score mismatches (default: legacy mode, these are warnings only). "
            "Also settable per-vault via OBSIDIAN_TRUST_STRICT=1 in the config."
        ),
    )
    lt.add_argument(
        "--allow-lifecycle",
        action="append",
        metavar="VALUE",
        help="extend the framework lifecycle allowlist (repeatable)",
    )
    lt.add_argument(
        "--allow-relationship-type",
        action="append",
        metavar="VALUE",
        help="extend the framework relationship-type allowlist (repeatable)",
    )
    lt.add_argument(
        "--required-trust-field",
        action="append",
        choices=("base_confidence", "lifecycle", "lifecycle_changed", "updated"),
        help="replace default trust-field requiredness (repeatable)",
    )
    lt.add_argument(
        "--schema-source",
        help="authority locator recorded in the lint report (for example, vault/AGENTS.md)",
    )
    lt.set_defaults(func=cmd_lint)

    tr = sub.add_parser(
        "trust-record",
        help="record explicitly approved manual confidence reviews in the vault trust ledger",
    )
    tr.add_argument(
        "vault",
        nargs="?",
        help="vault path or @name (defaults via CWD .env, then global config)",
    )
    selection = tr.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--all", action="store_true", help="record every current trust-schema page"
    )
    selection.add_argument(
        "--page",
        action="append",
        metavar="VAULT_RELATIVE_PATH",
        help="record only this explicitly reviewed page (repeatable)",
    )
    tr.add_argument(
        "--reviewed-at", required=True, help="ISO timestamp for the approved review"
    )
    tr.add_argument(
        "--approved",
        action="store_true",
        required=True,
        help="confirm a human approved every confidence value being recorded",
    )
    tr.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    tr.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    tr.add_argument(
        "--allow-lifecycle",
        action="append",
        metavar="VALUE",
        help="extend the resolved vault lifecycle allowlist (repeatable)",
    )
    tr.add_argument(
        "--required-trust-field",
        action="append",
        choices=("base_confidence", "lifecycle", "lifecycle_changed", "updated"),
        help="replace resolved vault trust-field requiredness (repeatable)",
    )
    tr.add_argument("--schema-source", help="authority locator recorded in the result")
    tr.set_defaults(func=cmd_trust_record)

    tc = sub.add_parser(
        "trust-check",
        help="validate confidence values and material fingerprints against the manual trust ledger",
    )
    tc.add_argument(
        "vault",
        nargs="?",
        help="vault path or @name (defaults via CWD .env, then global config)",
    )
    tc.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    tc.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    tc.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on warnings as well as failures",
    )
    tc.add_argument(
        "--allow-lifecycle",
        action="append",
        metavar="VALUE",
        help="extend the framework lifecycle allowlist (repeatable)",
    )
    tc.add_argument(
        "--required-trust-field",
        action="append",
        choices=("base_confidence", "lifecycle", "lifecycle_changed", "updated"),
        help="replace default trust-field requiredness (repeatable)",
    )
    tc.add_argument(
        "--schema-source",
        help="authority locator recorded in the trust report",
    )
    tc.set_defaults(func=cmd_trust_check)

    qq = sub.add_parser(
        "query",
        help="query the configured vault without passing the raw path each time",
    )
    qq.add_argument("question", help="question to ask against the vault index")
    qq.add_argument("--vault", help="override OBSIDIAN_VAULT_PATH for this query")
    qq.add_argument(
        "--top",
        type=int,
        default=8,
        help="number of candidate pages to rank (default: 8)",
    )
    qq.add_argument(
        "--max-read",
        type=int,
        default=3,
        help="max pages to return in should_read (default: 3)",
    )
    qq.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    qq.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    qq.set_defaults(func=cmd_query)

    cp = sub.add_parser(
        "context-pack",
        aliases=["context"],
        help="compile a token-bounded vault slice for a downstream agent",
    )
    cp.add_argument(
        "topic", nargs="?", help="topic to retrieve; omit only with --recent"
    )
    cp.add_argument("--vault", help="override OBSIDIAN_VAULT_PATH")
    cp.add_argument(
        "--budget",
        type=int,
        default=8_000,
        help="maximum estimated output tokens, 256..100000 (default: 8000)",
    )
    cp.add_argument(
        "--recent", action="store_true", help="select recently updated notes"
    )
    cp.add_argument(
        "--public-only",
        action="store_true",
        help="exclude visibility/internal and visibility/pii notes",
    )
    cp.add_argument(
        "--metadata-only",
        action="store_true",
        help="emit titles, provenance, and summaries without body excerpts",
    )
    cp.add_argument("--json", action="store_true", help="emit structured JSON")
    cp.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    cp.set_defaults(func=cmd_context_pack)

    return p


def _add_setup_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--portable",
        nargs="?",
        const=".",
        default=None,
        metavar="DIR",
        help="create a clone-ready portable knowledge repository in DIR",
    )
    sp.add_argument(
        "--vault", metavar="PATH", help="absolute path to your Obsidian vault"
    )
    sp.add_argument(
        "--project",
        nargs="?",
        const="",
        default=None,
        metavar="DIR",
        help="also install project-local skills + bootstrap files into DIR "
        "(defaults to the current directory if no DIR given)",
    )
    sp.add_argument(
        "--project-only",
        action="store_true",
        help="skip the global agent install (use with --project)",
    )
    sp.add_argument(
        "--copy",
        action="store_true",
        help="copy skill files instead of symlinking to the installed package",
    )
    sp.add_argument(
        "--remote",
        metavar="URL",
        help="GitHub (or any git host) repo URL for vault sync — skips the interactive "
        "prompt and configures it non-interactively (see also: obsidian-wiki sync-setup)",
    )


def main(argv: list[str] | None = None) -> int:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError

    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # No subcommand → default to `setup` (the common case).
    if not argv or (
        argv[0].startswith("-") and argv[0] not in ("-h", "--help", "-V", "--version")
    ):
        argv = ["setup", *argv]
    json_intent, pretty_intent, help_intent = _transaction_option_intent(argv)
    transaction_json_parse = json_intent and not help_intent
    argv = _normalize_cache_check_argv(argv)
    argv = _normalize_transaction_parent_separator(argv)
    try:
        args = parser.parse_args(argv)
    except _ArgumentParseError as exc:
        if transaction_json_parse:
            parse_args = argparse.Namespace(
                json=True,
                pretty=pretty_intent,
            )
            return _render_transaction_failure(
                parse_args,
                TransactionError(
                    f"invalid transaction arguments: {exc.message}"
                ),
            )
        exc.parser.print_usage(sys.stderr)
        exc.parser.exit(
            2,
            f"{exc.parser.prog}: error: {exc.message}\n",
        )
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    # Warn about stale installs on every command except `setup` (which fixes it)
    # and `info` (which calls _check_stale itself with richer output).
    if (
        getattr(args, "command", None)
        not in ("setup", "repo", "info", "doctor", "check", None)
        and not getattr(args, "json", False)
    ):
        _check_stale()
    try:
        return args.func(args)
    except (ConfigError, ManifestError, TransactionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

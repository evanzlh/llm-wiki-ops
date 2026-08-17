"""LLMWikiOps CLI for deterministic, repository-native LLM Wiki operations.

The locally built artifact bundles the canonical skill and bootstrap resources
used to scaffold and maintain clone-ready repositories.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
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
    load_portable_config,
    resolve_config,
)
from obsidian_wiki.git_support import discover_git_root
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
from obsidian_wiki.protocol import CONFIG_RELATIVE, LLMWIKIOPS_REPO_ENV
SOURCE_REINSTALL_HINT = (
    "clone https://github.com/evanzlh/llm-wiki-ops, then run "
    f"`{SOURCE_REINSTALL_COMMAND}` from the clone"
)

def version_label() -> str:
    return f"llmwikiops {__version__} ({IMPLEMENTATION_ID})"


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
        "https://github.com/evanzlh/llm-wiki-ops with "
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


# ── Config ───────────────────────────────────────────────────────────────────
def _runtime_error_detail(error: ConfigError) -> str:
    detail = str(error)
    if "must be non-empty" in detail:
        return f"vault not configured: {detail}"
    return detail


def _resolve_runtime(
    *,
    error_sink: list[ConfigError] | None = None,
) -> PortableConfig | None:
    """Resolve one CLI runtime through the shared precedence protocol."""
    try:
        cwd = Path.cwd()
    except OSError as exc:
        error = ConfigError(f"current working directory is unavailable: {exc}")
        error.__cause__ = exc
    else:
        try:
            return resolve_config(
                cwd=cwd,
                installed_version=__version__,
                implementation=IMPLEMENTATION_ID,
            )
        except ConfigError as exc:
            error = exc
        except OSError as exc:
            error = ConfigError(f"repository resolution failed: {exc}")
            error.__cause__ = exc
        error._llmwikiops_cwd = cwd  # type: ignore[attr-defined]

    if error_sink is not None:
        error_sink.append(error)
        return None
    raise error


def _config_values(config: PortableConfig) -> dict[str, str]:
    values = {
        "OBSIDIAN_VAULT_PATH": str(config.vault),
        "OBSIDIAN_SOURCES_DIR": ",".join(str(path) for path in config.sources),
        LLMWIKIOPS_REPO_ENV: str(config.root),
    }
    values.update(config.settings)
    return values


def _resolved_vault(runtime: PortableConfig) -> Path | None:
    if not runtime.vault.is_dir():
        print(f"error: vault not found: {runtime.vault}", file=sys.stderr)
        return None
    return runtime.vault


def _schema_config_source(runtime: PortableConfig) -> str:
    return str(runtime.path)


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


def _portable_lexical_paths(
    portable: PortableConfig,
) -> tuple[Path, Path, Path, tuple[Path, ...]]:
    """Re-read validated path strings without resolving away symlink evidence."""
    config_path = portable.root / CONFIG_RELATIVE
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
    expected_config = root / CONFIG_RELATIVE
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
            portable.root / CONFIG_RELATIVE,
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
        _assert_single_link_ordinary_file(
            portable.root,
            portable.root / CONFIG_RELATIVE,
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


def run_doctor(config: PortableConfig | None = None) -> dict[str, object]:
    if config is not None:
        return _run_portable_doctor(config)

    errors: list[ConfigError] = []
    runtime = _resolve_runtime(error_sink=errors)
    if runtime is not None:
        return _run_portable_doctor(runtime)

    error = errors[0] if errors else ConfigError("repository not configured")
    current = getattr(error, "_llmwikiops_cwd", None)
    if current is None:
        checks: list[dict[str, str]] = []
        _doctor_add(
            checks,
            name="portable-config",
            status="fail",
            detail=_runtime_error_detail(error),
            hint="run: llmwikiops setup [DIR]",
        )
        return {"status": "fail", "checks": checks}

    current = Path(current)
    candidate = None
    try:
        for ancestor in (current, *current.parents):
            path = ancestor / CONFIG_RELATIVE
            if path.exists() or path.is_symlink():
                candidate = path
                break
    except OSError as exc:
        return _portable_doctor_error(
            current / CONFIG_RELATIVE,
            f"portable configuration inspection failed: {exc}",
        )
    if candidate is not None:
        try:
            _assert_single_link_ordinary_file(
                candidate.parent.parent,
                candidate,
                "portable configuration",
            )
        except (ValueError, OSError) as exc:
            return _portable_doctor_error(candidate, str(exc))
        return _portable_doctor_error(candidate, str(error))

    checks: list[dict[str, str]] = []
    _doctor_add(
        checks,
        name="portable-config",
        status="fail",
        detail=_runtime_error_detail(error),
        hint="run: llmwikiops setup [DIR]",
    )
    return {"status": "fail", "checks": checks}


def _print_doctor(report: dict[str, object]) -> None:
    icon = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}
    print(f"llmwikiops doctor: {report['status']}")
    for check in report["checks"]:
        name = check["name"]
        status = check["status"]
        detail = check["detail"]
        hint = check["hint"]
        print(f"{icon.get(status, '•')} {name}: {detail}")
        if hint:
            print(f"   hint: {hint}")


# ── Commands ─────────────────────────────────────────────────────────────────
def cmd_setup(args: argparse.Namespace) -> int:
    target = Path(args.directory).expanduser().absolute()
    try:
        target = setup_portable_repo(
            target,
            version=__version__,
            source_skills=skills_dir(),
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Repository scaffolded at {target}")
    print(f"Open {target / 'wiki'} in Obsidian")
    return 0


def cmd_batch_plan(args: argparse.Namespace) -> int:
    from obsidian_wiki.batch import plan_batches
    from obsidian_wiki.portable_manifest import ManifestError

    config = _resolve_runtime()
    if config is None:
        return 1
    source_dir = config.sources[0]
    if not source_dir.is_dir():
        print(f"error: source directory not found: {source_dir}", file=sys.stderr)
        return 1
    try:
        result = plan_batches(
            source_dir,
            config,
            max_batch_mb=args.max_mb,
            max_batch_files=args.max_files,
            skip_unchanged=not args.no_cache,
            include_code=args.include_code,
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

    runtime = _resolve_runtime()
    if runtime is None:
        return 1
    vault = _resolved_vault(runtime)
    if vault is None:
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


def _cache_source_path(raw: str, *, root: Path | None = None) -> Path:
    """Return an absolute lexical source path without following symlinks."""
    raw_path = Path(raw).expanduser()
    if root is None:
        return Path(os.path.abspath(os.fspath(raw_path)))

    from obsidian_wiki.portable_manifest import ManifestError

    root_path = Path(os.path.abspath(os.fspath(root)))
    if raw_path.is_absolute():
        raise ManifestError("source is outside the repository root")
    source_path = Path(os.path.abspath(os.fspath(root_path / raw_path)))
    try:
        source_path.relative_to(root_path)
    except ValueError as exc:
        raise ManifestError("source is outside the repository root") from exc
    return source_path


def cmd_cache_check(args: argparse.Namespace) -> int:
    from obsidian_wiki.cache import check_sources

    config = _resolve_runtime()
    if config is None:
        return 1
    sources = [
        _cache_source_path(raw, root=config.root)
        for raw in args.sources
    ]
    result = check_sources(config, sources)
    _json_print(result, pretty=args.pretty)
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
    resolution_errors: list[ConfigError] = []
    runtime = _resolve_runtime(error_sink=resolution_errors)
    if runtime is None:
        error = resolution_errors[0] if resolution_errors else ConfigError(
            "check requires a portable repository"
        )
        if getattr(args, "json", False):
            raise error
        print(f"error: {_runtime_error_detail(error)}", file=sys.stderr)
        return 1
    from obsidian_wiki.portable_check import check_portable_repo

    try:
        recover_portable_skill_operations(
            runtime.root,
            version=__version__,
            source_skills=skills_dir(),
            expected_root_identity=runtime.root_identity,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = check_portable_repo(runtime)
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
        return resolve_config(
            cwd=Path.cwd(),
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
    except (ConfigError, OSError) as exc:
        raise ConfigError(f"{command} requires a repository: {exc}") from exc


def _repository_error_message(error: Exception) -> str:
    message = str(error)
    if not isinstance(error, ConfigError) or "requires a repository:" not in message:
        return message
    if isinstance(error.__cause__, OSError):
        message = f"{message}; current working directory is unavailable"
    return f"{message}; portable repository required"


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
            "message": _repository_error_message(error),
        },
        "recovery": guidance.as_dict(),
    }
    if args.json:
        _json_print(payload, pretty=args.pretty, ensure_ascii=True)
        return 1

    print(
        f"error: {_terminal_safe_text(_repository_error_message(error))}",
        file=sys.stderr,
    )
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
        "log_path": result.log_path,
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


def cmd_manifest_resolve_conflict(args: argparse.Namespace) -> int:
    from obsidian_wiki.portable_manifest import ShardedManifest

    config = _portable_command_config("manifest resolve-conflict")
    result = ShardedManifest(config).resolve_conflict_keep_live()
    payload = {"status": "resolved", **result}
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(
            f"manifest conflict resolved for {result['source_id']}: kept live shard"
        )
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
            f"{len(result.removed)} removed; {result.log_path}"
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

    status = hot_status(_portable_command_config("hot status"))
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
    report = run_doctor()
    if args.json:
        if args.pretty:
            print(json.dumps(report, indent=2))
        else:
            print(json.dumps(report))
    else:
        _print_doctor(report)
    statuses = {check["status"] for check in report["checks"]}
    if "fail" in statuses or (args.strict and "warn" in statuses):
        return 1
    return 0


def _print_lint(report: dict[str, object]) -> None:
    print(f"llmwikiops lint: {report['status']}")
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

    runtime = _resolve_runtime()
    if runtime is None:
        return 1
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1
    config = _config_values(runtime)
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
    if args.json:
        if args.pretty:
            print(json.dumps(report, indent=2))
        else:
            print(json.dumps(report))
    else:
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

    runtime = _resolve_runtime()
    if runtime is None:
        return 1
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1
    config = _config_values(runtime)
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
        write_trust_ledger(
            path,
            ledger,
            vault=vault,
            repository_root=runtime.root,
            root_identity=runtime.root_identity,
        )
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
    if args.json:
        print(json.dumps(result, indent=2 if args.pretty else None))
    else:
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

    runtime = _resolve_runtime()
    if runtime is None:
        return 1
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1
    config = _config_values(runtime)
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
    if args.json:
        print(json.dumps(report, indent=2 if args.pretty else None))
    else:
        print(f"llmwikiops trust-check: {report['status']}")
        for name, count in report["counts"].items():
            print(f"{name}: {count}")
    if report["status"] == "fail" or (args.strict and report["status"] == "warn"):
        return 1
    return 0


def _print_query(result: dict[str, object]) -> None:
    print(f"mode: {result['mode']}")
    print(f"status: {result['status']}")
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


def _query_error_payload(error: Exception) -> dict[str, object]:
    from obsidian_wiki.query_language import GRAMMAR_VERSION, describe_query_language

    error_payload: dict[str, object] = {
        "code": error.code,
        "message": str(error),
        "grammar_version": GRAMMAR_VERSION,
    }
    if error.code == "unsupported_query_structure":
        error_payload["templates"] = [
            item["template"]
            for item in describe_query_language()["natural_templates"]
        ]
    details = getattr(error, "details", None)
    if details:
        error_payload["details"] = details
    return {"status": "error", "error": error_payload}


def _render_query_error(args: argparse.Namespace, error: Exception) -> int:
    payload = _query_error_payload(error)
    error_payload = payload["error"]
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        print(f"error: {error_payload['message']}", file=sys.stderr)
        for template in error_payload.get("templates", []):
            print(f"  {template}", file=sys.stderr)
    return 2


def cmd_query(args: argparse.Namespace) -> int:
    from obsidian_wiki.graphrag import QueryExecutionError, query
    from obsidian_wiki.query_language import (
        QueryLanguageError,
        build_explicit_query,
        describe_query_language,
        parse_natural_query,
    )

    explicit_values = (args.mode, args.term, args.source, args.target)
    try:
        if args.describe:
            has_query_option = (
                args.question is not None
                or any(value is not None for value in explicit_values)
                or args.top is not None
                or args.max_read is not None
                or args.public_only
            )
            if has_query_option:
                raise QueryLanguageError(
                    "invalid_query_arguments",
                    "--describe cannot be combined with a query",
                )
            _json_print(describe_query_language(), pretty=args.pretty)
            return 0
        if args.question is not None and any(
            value is not None for value in explicit_values
        ):
            raise QueryLanguageError(
                "invalid_query_arguments",
                "natural and explicit query forms cannot be mixed",
            )
        if args.question is not None:
            spec = parse_natural_query(args.question)
        else:
            if args.mode is None:
                raise QueryLanguageError(
                    "invalid_query_arguments",
                    "provide one natural template or --mode with its operands",
                )
            spec = build_explicit_query(
                mode=args.mode,
                term=args.term,
                source=args.source,
                target=args.target,
            )
        effective_top = 8 if args.top is None else args.top
        effective_max_read = 3 if args.max_read is None else args.max_read
        if (
            isinstance(effective_top, bool)
            or not isinstance(effective_top, int)
            or effective_top < 1
        ):
            raise QueryLanguageError(
                "invalid_query_arguments",
                "top must be an integer greater than or equal to 1",
            )
        if (
            isinstance(effective_max_read, bool)
            or not isinstance(effective_max_read, int)
            or effective_max_read < 0
        ):
            raise QueryLanguageError(
                "invalid_query_arguments",
                "max-read must be an integer greater than or equal to 0",
            )
    except QueryLanguageError as exc:
        return _render_query_error(args, exc)

    runtime = _resolve_runtime()
    if runtime is None:
        return 1
    vault = _resolved_vault(runtime)
    if vault is None:
        return 1

    try:
        result = query(
            vault,
            spec,
            top_n=effective_top,
            max_should_read=effective_max_read,
            public_only=args.public_only,
        )
    except QueryExecutionError as exc:
        return _render_query_error(args, exc)
    if args.json:
        _json_print(result, pretty=args.pretty)
    else:
        _print_query(result)
    return 0


def cmd_context_pack(args: argparse.Namespace) -> int:
    from obsidian_wiki.context_pack import (
        ContextError,
        build_context_pack,
        render_markdown,
    )

    runtime = _resolve_runtime()
    if runtime is None:
        return 1
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
    if args.json:
        print(json.dumps(pack, indent=2 if args.pretty else None))
    else:
        print(render_markdown(pack), end="")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for name in list_skills():
        print(name)
    return 0


def _runtime_payload(
    runtime: PortableConfig | None, error: ConfigError | None = None
) -> dict[str, object]:
    if runtime is None:
        if error is not None and str(error) != "repository not configured":
            return {"status": "error", "error": _runtime_error_detail(error)}
        return {
            "status": "unconfigured",
            "guidance": "run: llmwikiops setup [DIR]",
        }

    return {
        "status": "resolved",
        "config": str(runtime.path),
        "root": str(runtime.root),
        "vault": str(runtime.vault),
        "sources": [str(source) for source in runtime.sources],
        "skills": str(runtime.skills),
        "local_state": str(runtime.local_state),
    }


def _installation_payload() -> tuple[dict[str, object], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    try:
        bundled: list[str] | None = list_skills()
        skill_root: str | None = str(skills_dir())
    except OSError as exc:
        bundled = None
        skill_root = None
        warnings.append(_bundled_skills_inspection_warning(exc))
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

    payload: dict[str, object] = {
        "implementation": IMPLEMENTATION_ID,
        "version": version_label(),
        "install_path": str(_pkg_dir()),
        "reinstall_command": SOURCE_REINSTALL_COMMAND,
        "skills": skill_root,
        "bootstrap": str(boot) if boot is not None else None,
        "bundled_skills": len(bundled) if bundled is not None else None,
    }
    return payload, _deduplicate_warnings(warnings)


def _print_info(payload: dict[str, object]) -> None:
    runtime = payload["runtime"]
    installation = payload["installation"]
    assert isinstance(runtime, dict)
    assert isinstance(installation, dict)

    print("Runtime context")
    for key in ("status", "config", "guidance", "error"):
        if key in runtime:
            print(f"  {key}: {runtime[key]}")
    if runtime.get("status") == "resolved":
        print(f"  repository: {runtime['root']}")
        print(f"  vault: {runtime['vault']}")
        sources = runtime["sources"]
        assert isinstance(sources, list)
        for source in sources:
            print(f"  source: {source}")
        print(f"  skills: {runtime['skills']}")
        print(f"  local state: {runtime['local_state']}")

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


def cmd_info(args: argparse.Namespace) -> int:
    errors: list[ConfigError] = []
    runtime = _resolve_runtime(error_sink=errors)
    resolution_error = errors[0] if errors else None
    installation, install_warnings = _installation_payload()
    warnings = install_warnings
    payload: dict[str, object] = {
        "runtime": _runtime_payload(runtime, resolution_error),
        "installation": installation,
        "warnings": warnings,
    }
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        _print_info(payload)
        if resolution_error is not None and str(resolution_error) != "repository not configured":
            print(f"error: {_runtime_error_detail(resolution_error)}", file=sys.stderr)
        for warning in warnings:
            print(f"warning: {warning['message']}", file=sys.stderr)
            print(f"  {warning['hint']}", file=sys.stderr)
    return (
        1
        if resolution_error is not None
        and str(resolution_error) != "repository not configured"
        else 0
    )


def cmd_repo_upgrade_skills(args: argparse.Namespace) -> int:
    warnings: list[dict[str, str]] = []
    try:
        resolved = resolve_config(
            cwd=Path.cwd(),
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
        root = resolved.root
        names = upgrade_portable_skills(
            root,
            version=__version__,
            source_skills=skills_dir(),
            warning_sink=warnings,
            expected_root_identity=resolved.root_identity,
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
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
        root = resolved.root
        report = sync_portable_skill_mirrors(
            root,
            apply=args.apply,
            expected_root_identity=resolved.root_identity,
        )
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
        print("Run `llmwikiops repo sync-skills --apply` to rebuild all mirrors.")
    if not args.json:
        for warning in report.warnings:
            print(f"warning: {warning['path']}: {warning['message']}", file=sys.stderr)
    return 1 if report.status == "drift" else 0


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


def _query_option_intent(argv: list[str]) -> tuple[bool, bool, bool]:
    if not argv or argv[0] != "query":
        return False, False, False
    try:
        separator = argv.index("--", 1)
    except ValueError:
        option_tokens = argv[1:]
    else:
        option_tokens = argv[1:separator]
    return (
        "--json" in option_tokens,
        "--pretty" in option_tokens,
        "-h" in option_tokens or "--help" in option_tokens,
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
    option_names = frozenset({"--json", "--pretty"})
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
        prog="llmwikiops",
        description=(
            "LLMWikiOps: deterministic, repository-native LLM Wiki operations."
        ),
    )
    p.add_argument("-V", "--version", action="version", version=version_label())
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("setup", help="create a portable knowledge repository")
    _add_setup_args(sp)
    sp.set_defaults(func=cmd_setup)

    lp = sub.add_parser("list", help="list bundled skills")
    lp.set_defaults(func=cmd_list)

    ip = sub.add_parser("info", help="show install paths, version, and config")
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

    manifest = sub.add_parser(
        "manifest", help="inspect and reconcile manifest recovery state"
    )
    manifest_sub = manifest.add_subparsers(dest="manifest_command", required=True)
    manifest_resolve = manifest_sub.add_parser(
        "resolve-conflict",
        help="keep the current live shard and remove only verified recovery artifacts",
    )
    manifest_resolve.add_argument(
        "--keep-live", action="store_true", required=True,
        help="confirm that the current live shard is the owner-selected version",
    )
    _add_json_args(manifest_resolve)
    manifest_resolve.set_defaults(func=cmd_manifest_resolve_conflict)

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
        "status", help="report hot.md freshness"
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

    bp = sub.add_parser(
        "batch-plan",
        help="split a source directory into parallel-ingest batches, skipping unchanged files",
    )
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
    cc.add_argument("sources", nargs="+", metavar="SOURCE")
    _add_json_args(cc)
    cc.set_defaults(func=cmd_cache_check, json=True)

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
        help="run a query-language/v1 operation against the configured vault",
    )
    qq.add_argument(
        "question",
        nargs="?",
        help="one exact query-language/v1 natural template",
    )
    qq.add_argument(
        "--describe", action="store_true", help="describe query-language/v1"
    )
    qq.add_argument("--mode", help="explicit mode: find, list, or path")
    qq.add_argument("--term", help="opaque Unicode operand for find or list")
    qq.add_argument(
        "--from", dest="source", help="opaque Unicode path source operand"
    )
    qq.add_argument("--to", dest="target", help="opaque Unicode path target operand")
    qq.add_argument(
        "--top",
        type=int,
        default=None,
        help="maximum returned candidates",
    )
    qq.add_argument(
        "--max-read",
        type=int,
        default=None,
        help="maximum suggested page reads",
    )
    qq.add_argument(
        "--public-only",
        action="store_true",
        help="exclude visibility/internal and visibility/pii before body reads",
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
        "directory",
        nargs="?",
        default=".",
        metavar="DIR",
        help="create a clone-ready portable knowledge repository in DIR",
    )


def main(argv: list[str] | None = None) -> int:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError

    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        parser.print_help()
        return 0
    json_intent, pretty_intent, help_intent = _transaction_option_intent(argv)
    transaction_json_parse = json_intent and not help_intent
    query_json_intent, query_pretty_intent, query_help_intent = (
        _query_option_intent(argv)
    )
    query_json_parse = query_json_intent and not query_help_intent
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
        if query_json_parse:
            from obsidian_wiki.query_language import QueryLanguageError

            parse_args = argparse.Namespace(
                json=True,
                pretty=query_pretty_intent,
            )
            return _render_query_error(
                parse_args,
                QueryLanguageError(
                    "invalid_query_arguments",
                    f"invalid query arguments: {exc.message}",
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
    try:
        return args.func(args)
    except (ConfigError, ManifestError, TransactionError) as exc:
        if getattr(args, "json", False):
            code = (
                "config-error"
                if isinstance(exc, ConfigError)
                else "manifest-error"
                if isinstance(exc, ManifestError)
                else "transaction-error"
            )
            _json_print(
                {
                    "status": "error",
                    "error": {
                        "code": code,
                        "message": _repository_error_message(exc),
                    },
                },
                pretty=getattr(args, "pretty", False),
            )
            return 1
        print(f"error: {_repository_error_message(exc)}", file=sys.stderr)
        return 1
    except (FileNotFoundError, RuntimeError) as exc:
        if getattr(args, "json", False):
            _json_print(
                {
                    "status": "error",
                    "error": {"code": "runtime-error", "message": str(exc)},
                },
                pretty=getattr(args, "pretty", False),
            )
            return 1
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

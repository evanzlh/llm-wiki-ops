> **Superseded (2026-08-12):** Current behavior is defined by the
> [Portable-Only Repository Design](../specs/2026-08-12-portable-only-design.md).

# Portable Configuration and Repository Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add repository-relative TOML configuration, automatic portable-repository discovery, clone-ready repository scaffolding, repository-local skills, and portable-aware doctor/config consumers without changing legacy personal mode.

**Architecture:** Put parsing and precedence in a focused `obsidian_wiki.config` module and portable scaffolding/skill adapters in `obsidian_wiki.portable`. CLI commands consume one `ResolvedConfig` instead of implementing their own `.env` walks. `setup --portable` is an early, mutually exclusive branch of setup that never writes global config or global agent directories. Agent adapters and bootstrap files are ordinary Markdown files containing repository-relative references rather than symlinks, which keeps the tracked representation platform-neutral and avoids link-permission assumptions; the approved first-release support boundary remains Linux and macOS.

**Tech Stack:** Python dataclasses/pathlib, `tomllib` with `tomli` fallback, `packaging` version specifiers, argparse, managed Markdown adapters, pytest

**Depends on:** `2026-08-07-fork-identity-and-source-install.md`

---

## File map

- `obsidian_wiki/config.py`: parse portable TOML, validate containment and versions, resolve explicit/named/portable/env/global precedence.
- `obsidian_wiki/portable.py`: scaffold repositories, write stable templates, copy canonical skills, maintain relative Markdown adapters and managed bootstrap blocks.
- `obsidian_wiki/cli.py`: route `setup --portable`, add `repo upgrade-skills`, and use unified config in doctor/query/context/lint/trust commands.
- `pyproject.toml`, `uv.lock`: add TOML fallback and PEP 440 specifier support.
- `tests/test_portable_config.py`, `tests/test_portable_setup.py`, `tests/test_portable_skill_protocol.py`: focused TDD coverage.
- `tests/test_doctor.py`, `tests/test_query_cli.py`, `tests/test_context_pack_cli.py`, `tests/test_lint.py`: integration and legacy-regression coverage.
- `AGENTS.md`, `.skills/llm-wiki/SKILL.md`, core wiki skills, agent bootstrap files: teach agents the portable resolution branch.
- `docs/configuration.md`, `docs/installation.md`, `docs/agents.md`, `docs/cli.md`, `docs/architecture.md`: document the finished behavior.

### Task 1: Parse and validate portable TOML configuration

**Files:**
- Create: `obsidian_wiki/config.py`
- Create: `tests/test_portable_config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Write failing parser and containment tests**

```python
# tests/test_portable_config.py
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.config import ConfigError, load_portable_config


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


def test_load_resolves_paths_against_repository_root(tmp_path: Path) -> None:
    path = write_portable(tmp_path)
    config = load_portable_config(path, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
    assert config.root == tmp_path.resolve()
    assert config.vault == (tmp_path / "wiki").resolve()
    assert config.sources == ((tmp_path / "sources").resolve(),)
    assert config.settings["OBSIDIAN_LINK_FORMAT"] == "wikilink"


@pytest.mark.parametrize("value", ["/tmp/wiki", "C:/wiki", "../../wiki"])
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
```

- [ ] **Step 2: Run parser tests and verify failure**

Run: `uv run pytest tests/test_portable_config.py -q`

Expected: FAIL because `obsidian_wiki.config` does not exist.

- [ ] **Step 3: Add the minimal dependencies**

Set:

```toml
dependencies = [
  "packaging>=24",
  "tomli>=2; python_version < '3.11'",
]
```

Run: `uv lock`

Expected: `uv.lock` records `packaging` and the conditional `tomli` dependency.

- [ ] **Step 4: Implement portable configuration types and validation**

Start `obsidian_wiki/config.py` with these public contracts:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

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
    if value.is_absolute() or PureWindowsPath(raw).is_absolute():
        raise ConfigError(f"{label} must be repository-relative")
    resolved_root = root.resolve()
    resolved = (resolved_root / value).resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ConfigError(f"{label} must be repository-relative and remain inside {resolved_root}") from exc
    return resolved


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


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
            values[str(key)] = ",".join(str(item) for item in value)
        elif isinstance(value, bool):
            values[str(key)] = "true" if value else "false"
        elif isinstance(value, (str, int, float, bool)):
            values[str(key)] = str(value)
        else:
            raise ConfigError(f"unsupported settings value for {key}")
    return values
```

Implement `load_portable_config(path, *, installed_version, implementation)` to:

1. read TOML with `tomllib.loads`;
2. require schema version `1`, the expected implementation, and a valid `SpecifierSet` containing `Version(installed_version)`;
3. derive root as `path.parent.parent`;
4. require non-empty `paths.vault`, `paths.sources`, `paths.skills`, and `paths.local_state`;
5. resolve each through `_contained_path`;
6. reject vault/source overlap and source roots outside the repository;
7. return `PortableConfig` with flattened settings.

Convert `InvalidSpecifier`, `InvalidVersion`, TOML decode errors, missing keys, and wrong types into a `ConfigError` that includes the config path.

- [ ] **Step 5: Run parser tests**

Run: `uv run pytest tests/test_portable_config.py -q`

Expected: PASS.

- [ ] **Step 6: Commit portable parsing**

```bash
git add pyproject.toml uv.lock obsidian_wiki/config.py tests/test_portable_config.py
git commit -m "feat: parse portable repository configuration"
```

### Task 2: Centralize configuration precedence

**Files:**
- Modify: `obsidian_wiki/config.py`
- Modify: `tests/test_portable_config.py`

- [ ] **Step 1: Add failing precedence tests**

Append:

```python
from obsidian_wiki.config import resolve_config


def write_legacy(path: Path, vault: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'OBSIDIAN_VAULT_PATH="{vault}"\n', encoding="utf-8")


def test_nearest_portable_config_beats_env_and_global(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / "knowledge"
    nested = root / "sources" / "nested"
    nested.mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "sources").mkdir(exist_ok=True)
    write_portable(root)
    write_legacy(nested / ".env", tmp_path / "env-vault")
    write_legacy(home / ".obsidian-wiki" / "config", tmp_path / "global-vault")
    resolved = resolve_config(cwd=nested, home=home, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
    assert resolved.mode == "portable"
    assert resolved.vault == (root / "wiki").resolve()


def test_nearest_env_beats_global_without_portable_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = home / "project"
    nested = project / "nested"
    nested.mkdir(parents=True)
    local_vault = tmp_path / "local"
    global_vault = tmp_path / "global"
    write_legacy(project / ".env", local_vault)
    write_legacy(home / ".obsidian-wiki" / "config", global_vault)
    resolved = resolve_config(cwd=nested, home=home, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
    assert resolved.mode == "env"
    assert resolved.vault == local_vault.resolve()


def test_named_override_beats_portable_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / "knowledge"
    root.mkdir(parents=True)
    write_portable(root)
    named = tmp_path / "named"
    write_legacy(home / ".obsidian-wiki" / "config.work", named)
    resolved = resolve_config(vault_arg="@work", cwd=root, home=home, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
    assert resolved.mode == "named"
    assert resolved.vault == named.resolve()


def test_missing_named_override_never_falls_back(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / "knowledge"
    root.mkdir(parents=True)
    write_portable(root)
    with pytest.raises(ConfigError, match="config.work"):
        resolve_config(vault_arg="@work", cwd=root, home=home, installed_version="2026.8", implementation=IMPLEMENTATION_ID)
```

- [ ] **Step 2: Run precedence tests and verify failure**

Run: `uv run pytest tests/test_portable_config.py -q`

Expected: FAIL because `resolve_config` is missing.

- [ ] **Step 3: Implement one resolver**

Add private helpers `_read_env_file`, `_ancestors`, and `_resolved_legacy`, then implement `resolve_config(vault_arg: str | None = None, *, cwd: Path | None = None, home: Path | None = None, installed_version: str, implementation: str) -> ResolvedConfig`.

Use this exact order:

1. `@name` reads `<home>/.obsidian-wiki/config.<name>` and fails closed if absent or empty.
2. A non-`None` non-`@` `vault_arg` is an explicit path and returns mode `explicit`.
3. Walk ancestors from `cwd` and use the first `<ancestor>/.obsidian-wiki/config.toml`.
4. Walk ancestors again for the first `.env` containing `OBSIDIAN_VAULT_PATH`; an empty value fails closed.
5. Read `<home>/.obsidian-wiki/config`.
6. Raise `ConfigError("vault not configured")` instead of falling through silently.

For portable mode populate runtime-only `values` with absolute strings for `OBSIDIAN_VAULT_PATH`, `OBSIDIAN_SOURCES_DIR`, `OBSIDIAN_WIKI_REPO`, and the flattened `[settings]`. These values are returned in memory only and are never written to tracked files.

- [ ] **Step 4: Run parser and precedence tests**

Run: `uv run pytest tests/test_portable_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit unified resolution**

```bash
git add obsidian_wiki/config.py tests/test_portable_config.py
git commit -m "feat: resolve portable config before legacy config"
```

### Task 3: Scaffold clone-ready portable repositories

**Files:**
- Create: `obsidian_wiki/portable.py`
- Create: `tests/test_portable_setup.py`
- Modify: `obsidian_wiki/cli.py`

- [ ] **Step 1: Write failing scaffold tests**

```python
# tests/test_portable_setup.py
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.portable import MANAGED_END, MANAGED_START


def run_cli(home: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


def test_setup_portable_creates_repo_without_global_side_effects(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    target = work / "knowledge"
    work.mkdir()
    result = run_cli(home, work, "setup", "--portable", str(target))
    assert result.returncode == 0, result.stderr
    assert not (home / ".obsidian-wiki").exists()
    config = (target / ".obsidian-wiki" / "config.toml").read_text(encoding="utf-8")
    assert f'implementation = "{IMPLEMENTATION_ID}"' in config
    assert (target / "sources").is_dir()
    assert (target / "wiki" / "concepts").is_dir()
    assert json.loads((target / "wiki" / ".manifest.json").read_text())["schema_version"] == 2
    assert not (target / "wiki" / "hot.md").exists()
    assert not (target / ".venv").exists()
    assert not (target / "obsidian_wiki").exists()
    assert not (target / ".git").exists()
    assert "wiki/hot.md" in (target / ".gitignore").read_text(encoding="utf-8")
    assert (target / ".skills" / "wiki-ingest" / "SKILL.md").is_file()
    agents = (target / "AGENTS.md").read_text(encoding="utf-8")
    assert MANAGED_START in agents and MANAGED_END in agents
    assert "## Team conventions" in agents
    assert "README Translation Parity" not in agents


def test_portable_adapters_are_regular_relative_files_and_survive_repo_move(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "knowledge"
    result = run_cli(home, tmp_path, "setup", "--portable", str(target))
    assert result.returncode == 0
    adapter = target / ".claude" / "skills" / "wiki-ingest" / "SKILL.md"
    assert adapter.is_file()
    assert not adapter.is_symlink()
    assert "../../../.skills/wiki-ingest/SKILL.md" in adapter.read_text(encoding="utf-8")
    moved = tmp_path / "moved-knowledge"
    target.rename(moved)
    assert (moved / ".claude" / "skills" / "wiki-ingest" / "SKILL.md").is_file()


def test_setup_portable_rejects_legacy_setup_flags(tmp_path: Path) -> None:
    result = run_cli(
        tmp_path / "home",
        tmp_path,
        "setup",
        "--portable",
        str(tmp_path / "knowledge"),
        "--vault",
        str(tmp_path / "vault"),
    )
    assert result.returncode != 0
    assert "cannot be combined" in result.stderr
```

- [ ] **Step 2: Run setup tests and verify failure**

Run: `uv run pytest tests/test_portable_setup.py -q`

Expected: FAIL because `--portable` is not registered.

- [ ] **Step 3: Implement portable templates and managed adapters**

In `obsidian_wiki/portable.py` define:

```python
MANAGED_START = "<!-- obsidian-wiki:managed:start -->"
MANAGED_END = "<!-- obsidian-wiki:managed:end -->"

MANIFEST_MARKER = {
    "schema_version": 2,
    "storage": "sharded",
    "entries": ".manifest/sources",
}

PORTABLE_VAULT_DIRS = (
    "concepts",
    "entities",
    "skills",
    "references",
    "synthesis",
    "journal/operations",
    "projects",
    "_meta",
    "_raw",
    "_readouts",
    ".obsidian",
)

PORTABLE_ROOT_IGNORE = (
    ".obsidian-wiki/local/",
)
```

`ensure_portable_gitignore` appends `PORTABLE_ROOT_IGNORE` plus `<vault-relative>/hot.md`, `<vault-relative>/.obsidian/workspace.json`, `<vault-relative>/.obsidian/workspace-mobile.json`, and `<vault-relative>/.trash/`. Derive `<vault-relative>` from the config/setup target; never hard-code an absolute path. For the default scaffold it is `wiki`.

Provide narrowly scoped functions named `compatible_cli_spec`, `merge_managed_block`, `write_portable_config`, `scaffold_portable_vault`, `copy_canonical_skills`, `write_agent_adapters`, `install_portable_bootstrap`, `ensure_portable_gitignore`, and `setup_portable_repo`. Keep the argument and return contracts demonstrated by the tests above; `setup_portable_repo` is the orchestrator and the other functions each own only the artifact named by the function.

`compatible_cli_spec` must turn a CalVer release such as `2026.8.3` into `>=2026.8,<2026.9`; for development/local versions, use exact `==<public-version>`.

The generated `[settings]` table pins these output defaults: `OBSIDIAN_CATEGORIES = "concepts,entities,skills,references,synthesis,journal,projects"`, `OBSIDIAN_MAX_PAGES_PER_INGEST = 15`, `OBSIDIAN_LINK_FORMAT = "wikilink"`, `OBSIDIAN_RAW_DIR = "_raw"`, and `OBSIDIAN_TRUST_STRICT = false`. Optional schema keys may be added later by the repository owner from `PORTABLE_SETTING_KEYS`. History locations, semantic-search commands, API credentials, external tool paths, and `OBSIDIAN_SCHEMA_SOURCE` are not portable settings; keep them in the agent/OS environment or ignored local state. Reports derive schema authority as `.obsidian-wiki/config.toml` or `wiki/AGENTS.md`, never as a committed absolute path.

`scaffold_portable_vault` creates exactly `PORTABLE_VAULT_DIRS`, stable built-in-query `index.md` and `log.md`, `.manifest.json` marker v2, `.manifest/sources/`, and Obsidian app/appearance JSON. Portable mode does not create personal-mode `_staging/`; transaction candidates live below local state. It deliberately does not create `hot.md`.

Render `index.md` with these exact stable bytes:

````markdown
---
title: Wiki Index
---

# Wiki Index

```query
path:"concepts" OR path:"entities" OR path:"skills" OR path:"references" OR path:"synthesis" OR path:"projects"
```
````

Render `log.md` with these exact stable bytes:

````markdown
---
title: Wiki Operation Log
---

# Wiki Operation Log

```query
path:"journal/operations"
```
````

Both files end with one newline and are left unchanged by ordinary portable writes.

`copy_canonical_skills` copies real directories into `<root>/.skills`. For every existing `PROJECT_AGENT_DIRS` location, `write_agent_adapters` writes an ordinary `<agent-dir>/<skill>/SKILL.md` file whose managed body tells the agent to read and follow `../../../.skills/<skill>/SKILL.md`. The adapter path is computed with `PurePosixPath`; it contains no absolute path and no symlink. Root-level `CLAUDE.md`, `GEMINI.md`, and `.hermes.md` are ordinary managed Markdown files that reference `AGENTS.md`. Rule files below `.agent`, `.cursor`, `.windsurf`, `.kiro`, and `.github` likewise use repository-relative Markdown references at the correct depth.

Do not copy this framework repository's development `AGENTS.md` into a knowledge repository: it contains fork-maintenance and documentation rules that do not apply there. `portable.py` owns a dedicated portable bootstrap renderer. Its root managed block states config discovery, `.skills/<name>/SKILL.md` intent routing, required reading of `wiki/AGENTS.md` when present, the transaction-only write boundary, and the prohibition on automatic commit/push/PR actions. After the managed block, initialize a `## Team conventions` region with one sentence explaining that maintainers may add terminology, writing style, scope, and review rules there. Subsequent setup/upgrade calls replace only the managed block.

Use this exact adapter body (substituting the skill name only):

```markdown
---
name: wiki-ingest
description: Portable adapter for the repository-canonical wiki-ingest skill.
---

# Portable skill adapter

Read and follow `../../../.skills/wiki-ingest/SKILL.md` from this repository. Resolve that path from this adapter file, never from the process working directory.
```

`merge_managed_block` replaces only content between its markers and preserves all text outside them. If no markers exist, prepend the managed block and retain the existing file as the team-maintained region.

- [ ] **Step 4: Register `setup --portable [DIR]`**

Add to `_add_setup_args`:

```python
sp.add_argument(
    "--portable",
    nargs="?",
    const=".",
    default=None,
    metavar="DIR",
    help="create a clone-ready portable knowledge repository in DIR",
)
```

At the top of `cmd_setup`, if `args.portable is not None`, reject `--vault`, `--project`, `--project-only`, `--copy`, and `--remote`, resolve the target, call `setup_portable_repo(target, version=__version__, source_skills=skills_dir())`, print the target plus `Open <target>/wiki in Obsidian`, and return before `write_config` or global skill installation. Portable bootstrap text is rendered by `portable.py`; do not pass or copy the framework repository's development `AGENTS.md`.

- [ ] **Step 5: Run setup tests**

Run: `uv run pytest tests/test_portable_setup.py -q`

Expected: PASS.

- [ ] **Step 6: Commit portable scaffolding**

```bash
git add obsidian_wiki/portable.py obsidian_wiki/cli.py tests/test_portable_setup.py
git commit -m "feat: scaffold portable knowledge repositories"
```

### Task 4: Upgrade repository skills without overwriting owners

**Files:**
- Modify: `obsidian_wiki/portable.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_portable_setup.py`

- [ ] **Step 1: Add failing idempotence and upgrade tests**

Append tests that:

```python
def test_portable_setup_is_idempotent_and_preserves_team_text(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "knowledge"
    assert run_cli(home, tmp_path, "setup", "--portable", str(root)).returncode == 0
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n## Team policy\nUse our glossary.\n", encoding="utf-8")
    assert run_cli(home, root, "setup", "--portable", str(root)).returncode == 0
    assert "Use our glossary." in agents.read_text(encoding="utf-8")


def test_repo_upgrade_skills_repairs_adapter_and_preserves_team_text(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "knowledge"
    assert run_cli(home, tmp_path, "setup", "--portable", str(root)).returncode == 0
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\nTeam-owned sentence.\n", encoding="utf-8")
    (root / ".claude" / "skills" / "wiki-ingest" / "SKILL.md").unlink()
    result = run_cli(home, root, "repo", "upgrade-skills")
    assert result.returncode == 0, result.stderr
    assert "Team-owned sentence." in agents.read_text(encoding="utf-8")
    assert (root / ".claude" / "skills" / "wiki-ingest" / "SKILL.md").is_file()
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run pytest tests/test_portable_setup.py -q`

Expected: FAIL because `repo upgrade-skills` is missing and setup overwrites bootstrap files.

- [ ] **Step 3: Implement managed skill inventory and upgrade**

Store a tracked `.obsidian-wiki/managed-skills.json` containing exactly three keys: `implementation` with value `evanzlh/obsidian-wiki`, `skills_version` with the installed version string, and `skills` with the lexicographically sorted names returned by `copy_canonical_skills`. Serialize it with sorted keys, two-space indentation, and one trailing newline.

On upgrade, copy current bundled skills, remove only obsolete skill directories listed by the previous inventory, rebuild relative Markdown adapters, update only managed bootstrap blocks, and rewrite the inventory canonically. Never remove an unlisted directory or text outside managed markers.

- [ ] **Step 4: Register the nested repo command**

In `build_parser` create a `repo` parser with required nested subcommands and register `upgrade-skills`. `cmd_repo_upgrade_skills` must resolve portable config from CWD, fail outside portable mode, and call the upgrade function. Do not accept an arbitrary external repository root.

- [ ] **Step 5: Run portable setup tests**

Run: `uv run pytest tests/test_portable_setup.py -q`

Expected: PASS.

- [ ] **Step 6: Commit safe skill upgrades**

```bash
git add obsidian_wiki/portable.py obsidian_wiki/cli.py tests/test_portable_setup.py
git commit -m "feat: upgrade portable skills without overwriting owners"
```

### Task 5: Make CLI consumers and doctor portable-aware

**Files:**
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_doctor.py`
- Modify: `tests/test_query_cli.py`
- Modify: `tests/test_context_pack_cli.py`
- Modify: `tests/test_lint.py`

- [ ] **Step 1: Add portable integration tests**

Add one test per consumer that creates a portable repository, runs from a nested directory with an intentionally different global vault, and asserts the portable vault wins. For doctor, assert checks named `portable-config`, `implementation`, `portable-paths`, and `project-skills` pass and that absence of global config/skills is not a warning or failure. Add a second doctor case whose TOML says `implementation = "Ar9av/obsidian-wiki"`; assert nonzero status, a failed `implementation` check, and no fallback to the global vault.

Use this shared fixture shape in each test module:

```python
root = tmp_path / "knowledge"
(root / ".obsidian-wiki").mkdir(parents=True)
(root / "sources").mkdir()
(root / "wiki").mkdir()
(root / ".skills").mkdir()
(root / ".obsidian-wiki" / "config.toml").write_text(
    f'''schema_version = 1
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
```

- [ ] **Step 2: Run consumer tests and verify failure**

Run: `uv run pytest tests/test_doctor.py tests/test_query_cli.py tests/test_context_pack_cli.py tests/test_lint.py -q`

Expected: new portable cases FAIL because commands still have separate legacy resolvers.

- [ ] **Step 3: Replace command-local config walkers**

Add a small CLI adapter:

```python
def _resolve_runtime(vault_arg: str | None = None) -> ResolvedConfig | None:
    try:
        return resolve_config(
            vault_arg,
            cwd=Path.cwd(),
            home=HOME,
            installed_version=__version__,
            implementation=IMPLEMENTATION_ID,
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
```

Use it in query, context-pack, lint, trust-record, trust-check, doctor, sync, and sync-setup. Preserve explicit `--vault` and `@name` behavior. Pass `ResolvedConfig.values` to schema option parsing. Delete `_resolve_schema_command_context`, `_resolve_context_pack_vault`, `_resolve_command_vault`, and duplicate `.env` helpers only after all callers migrate.

In portable doctor mode:

- do not require `~/.obsidian-wiki/config`;
- do not scan global agent directories;
- check implementation/version through `load_portable_config`;
- require portable paths, canonical `.skills`, managed inventory, bootstrap files, and regular relative Markdown adapters;
- require `index.md`, `log.md`, and `.manifest.json`, but not ignored `hot.md`.

Keep the existing personal doctor result names and behavior unchanged.

- [ ] **Step 4: Run focused and legacy config tests**

Run: `uv run pytest tests/test_portable_config.py tests/test_portable_setup.py tests/test_doctor.py tests/test_query_cli.py tests/test_context_pack_cli.py tests/test_lint.py -q`

Expected: PASS for portable and legacy cases.

- [ ] **Step 5: Commit consumer integration**

```bash
git add obsidian_wiki/cli.py tests/test_doctor.py tests/test_query_cli.py tests/test_context_pack_cli.py tests/test_lint.py
git commit -m "feat: resolve portable config across CLI commands"
```

### Task 6: Update agent protocols and user documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `.skills/llm-wiki/SKILL.md`
- Modify: `.skills/wiki-ingest/SKILL.md`
- Modify: `.skills/wiki-update/SKILL.md`
- Modify: `.skills/wiki-status/SKILL.md`
- Modify: `.skills/wiki-query/SKILL.md`
- Modify: `.skills/wiki-context-pack/SKILL.md`
- Modify: `.skills/wiki-setup/SKILL.md`
- Modify: `.agent/rules/obsidian-wiki.md`
- Modify: `.cursor/rules/obsidian-wiki.mdc`
- Modify: `.windsurf/rules/obsidian-wiki.md`
- Modify: `.kiro/steering/obsidian-wiki.md`
- Modify: `.github/copilot-instructions.md`
- Modify: `docs/configuration.md`
- Modify: `docs/installation.md`
- Modify: `docs/agents.md`
- Modify: `docs/cli.md`
- Modify: `docs/architecture.md`
- Create: `tests/test_portable_skill_protocol.py`

- [ ] **Step 1: Write a failing protocol-consistency test**

```python
# tests/test_portable_skill_protocol.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = (
    "AGENTS.md",
    ".skills/llm-wiki/SKILL.md",
    ".skills/wiki-ingest/SKILL.md",
    ".skills/wiki-update/SKILL.md",
    ".skills/wiki-status/SKILL.md",
    ".skills/wiki-query/SKILL.md",
    ".skills/wiki-context-pack/SKILL.md",
)


def test_core_protocol_documents_portable_precedence() -> None:
    for relative in CORE:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert ".obsidian-wiki/config.toml" in text, relative
        assert "@name" in text, relative
        assert "README_TW.md" not in text, relative


def test_portable_setup_never_writes_global_config() -> None:
    text = (ROOT / ".skills/wiki-setup/SKILL.md").read_text(encoding="utf-8")
    assert "obsidian-wiki setup --portable" in text
    assert "does not write `~/.obsidian-wiki/config`" in text
```

- [ ] **Step 2: Run the protocol test and verify failure**

Run: `uv run pytest tests/test_portable_skill_protocol.py -q`

Expected: FAIL because the shared protocol only documents `.env` and global config.

- [ ] **Step 3: Add one canonical portable resolution block**

In `AGENTS.md` and `.skills/llm-wiki/SKILL.md`, state this exact precedence:

```text
explicit @name
-> nearest ancestor .obsidian-wiki/config.toml
-> nearest ancestor .env containing OBSIDIAN_VAULT_PATH
-> ~/.obsidian-wiki/config
-> setup guidance
```

For portable mode, paths are resolved from the repository root, all authoritative sources must be below configured `sources`, and `<vault>/AGENTS.md` is read after resolution. Skills must not synthesize an absolute `OBSIDIAN_WIKI_REPO` into committed files.

Update the other core skills and bootstrap files to link to that canonical block instead of restating a different order. `wiki-setup` must explain the distinction between personal setup and `obsidian-wiki setup --portable DIR` and explicitly state that portable setup does not write global config.

- [ ] **Step 4: Document commands and configuration**

Add the TOML schema, relative-path rules, implementation check, discovery precedence, setup command, clone workflow, `repo upgrade-skills`, and agent-adapter behavior to the listed human docs. State that each contributor installs the CLI as a user/system uv tool from the framework clone; the knowledge repository never contains `.venv`, a vendored CLI package, or another runtime copy. State explicitly that adapters are regular Markdown files rather than symlinks and therefore require no link privileges. Document Linux and macOS as the first-release support boundary without putting OS-specific paths or shell behavior into committed repository state. Keep `README.md` and `README_ZH.md` aligned when adding the portable command.

- [ ] **Step 5: Run protocol and documentation tests**

Run: `uv run pytest tests/test_portable_skill_protocol.py tests/test_inline_vault_targeting_docs.py tests/test_context_pack_docs.py tests/test_readme_sync.py -q`

Expected: PASS.

- [ ] **Step 6: Run the complete second-plan verification**

Run: `uv run pytest tests/test_portable_config.py tests/test_portable_setup.py tests/test_portable_skill_protocol.py tests/test_doctor.py tests/test_query_cli.py tests/test_context_pack_cli.py tests/test_lint.py tests/test_inline_vault_targeting_docs.py -q`

Expected: PASS.

- [ ] **Step 7: Commit protocol and docs**

```bash
git add AGENTS.md .skills .agent .cursor .windsurf .kiro .github/copilot-instructions.md docs README.md README_ZH.md tests/test_portable_skill_protocol.py
git commit -m "docs: define portable repository resolution protocol"
```

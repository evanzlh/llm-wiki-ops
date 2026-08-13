> **Superseded (2026-08-12):** Current behavior is defined by the
> [Portable-Only Repository Design](../specs/2026-08-12-portable-only-design.md).

# CLI Runtime Context and Recovery Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CLI runtime resolution observable, warn when explicit vault targeting shadows a portable repository, block Personal-mode Git automation from bypassing portable context, and provide safe structured transaction recovery guidance.

**Architecture:** Add two dependency-free pure modules: one inspects config resolution and produces immutable context warnings, and one maps trusted transaction records to immutable recovery actions. Keep `resolve_config()` and transaction state transitions unchanged; `cli.py` only resolves, renders, attaches warnings, and catches transaction errors before the global human-text handler.

**Tech Stack:** Python 3.8+, frozen dataclasses, pathlib, argparse, JSON, pytest, existing config/doctor/transaction APIs, and subprocess CLI tests.

---

## Scope and file map

The approved design is
`docs/superpowers/specs/2026-08-10-cli-runtime-context-and-recovery-guidance-design.md`.

- Create `obsidian_wiki/runtime_context.py`: immutable runtime inspection and
  portable-shadow warning generation. It reads configuration but never writes.
- Create `obsidian_wiki/transaction_guidance.py`: immutable recovery actions
  derived only from a validated `TransactionRecord`.
- Modify `obsidian_wiki/cli.py`: render `info`, attach context warnings, protect
  sync commands, and serialize transaction failures.
- Create `tests/test_runtime_context.py`: pure resolver and lexical discovery
  tests.
- Create `tests/test_info_cli.py`: human/JSON `info` contract and no-mutation
  tests.
- Create `tests/test_transaction_guidance.py`: complete status/action matrix.
- Modify `tests/test_doctor.py`, `tests/test_query_cli.py`,
  `tests/test_context_pack_cli.py`, `tests/test_lint.py`, and
  `tests/test_trust.py`: command-level warning integration.
- Modify `tests/test_sync.py`: explicit override and unsafe config-entry bypass
  prevention.
- Modify `tests/test_transaction.py`: structured failures and annotated list
  output.
- Modify `tests/test_installation_policy.py`: retain source-install independence
  coverage under the new human `info` labels.
- Modify `tests/test_portable_write_protocol.py`: pin documentation and recovery
  command ownership.
- Modify `docs/cli.md` and `docs/configuration.md`: document the new output and
  safety contract.

No README change is planned. No raw-path graph/cache/batch command changes, no
new config variable, no interactive prompt, and no automatic recovery are in
scope.

### Task 1: Add the pure runtime inspection model

**Files:**
- Create: `obsidian_wiki/runtime_context.py`
- Create: `tests/test_runtime_context.py`

- [ ] **Step 1: Write failing runtime inspection tests**

Create `tests/test_runtime_context.py` with helpers and the approved boundary
matrix:

```python
from __future__ import annotations

from pathlib import Path

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.runtime_context import (
    inspect_runtime,
    nearest_portable_config,
)


def _portable(root: Path, *, text: str | None = None) -> Path:
    path = root / ".obsidian-wiki/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        text
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
    return path


def _legacy(path: Path, vault: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'OBSIDIAN_VAULT_PATH="{vault}"\n', encoding="utf-8")


def _inspect(cwd: Path, home: Path, vault: str | None = None):
    return inspect_runtime(
        vault,
        cwd=cwd,
        home=home,
        installed_version="2026.8",
        implementation=IMPLEMENTATION_ID,
    )


def test_portable_runtime_without_override_has_no_warning(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    nested = root / "work/nested"
    nested.mkdir(parents=True)
    path = _portable(root)

    inspected = _inspect(nested, tmp_path / "home")

    assert inspected.status == "resolved"
    assert inspected.runtime is not None
    assert inspected.runtime.mode == "portable"
    assert inspected.portable_config == path.absolute()
    assert inspected.warnings == ()


def test_explicit_same_vault_still_warns(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    path = _portable(root)

    inspected = _inspect(root, tmp_path / "home", str(root / "wiki"))

    assert inspected.status == "resolved"
    assert inspected.runtime is not None
    assert inspected.runtime.mode == "explicit"
    assert len(inspected.warnings) == 1
    warning = inspected.warnings[0].as_dict()
    assert warning["code"] == "portable-context-overridden"
    assert warning["portable_config"] == str(path.absolute())
    assert warning["selected_vault"] == str((root / "wiki").resolve())


def test_named_override_warns_without_switching_default(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _portable(root)
    home = tmp_path / "home"
    named = home / ".obsidian-wiki/config.work"
    _legacy(named, tmp_path / "work-vault")
    before = named.read_bytes()

    inspected = _inspect(root, home, "@work")

    assert inspected.runtime is not None
    assert inspected.runtime.mode == "named"
    assert inspected.warnings[0].selected_source == str(named.resolve())
    assert named.read_bytes() == before


def test_invalid_shadowed_portable_does_not_block_explicit_override(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    path = _portable(root, text="not valid toml = [")

    inspected = _inspect(root, tmp_path / "home", "other-vault")

    assert inspected.status == "resolved"
    assert inspected.runtime is not None
    assert inspected.runtime.mode == "explicit"
    assert inspected.portable_config == path.absolute()
    assert len(inspected.warnings) == 1


def test_invalid_authoritative_portable_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _portable(root, text="not valid toml = [")

    inspected = _inspect(root, tmp_path / "home")

    assert inspected.status == "error"
    assert inspected.runtime is None
    assert inspected.error is not None
    assert inspected.warnings == ()


def test_no_config_is_unconfigured_not_invalid(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()

    inspected = _inspect(cwd, tmp_path / "home")

    assert inspected.status == "unconfigured"
    assert inspected.runtime is None
    assert inspected.guidance == "run: obsidian-wiki setup --vault /path/to/your/vault"


def test_dangling_portable_symlink_is_discovered_lexically(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    config = root / ".obsidian-wiki/config.toml"
    config.parent.mkdir(parents=True)
    config.symlink_to(tmp_path / "missing.toml")

    assert nearest_portable_config(root) == config.absolute()
    assert _inspect(root, tmp_path / "home").status == "error"
```

Also parameterize `nearest_portable_config` for an ordinary file, a valid
symlink, a dangling symlink, and no entry. Add an explicit override outside a
portable CWD and assert `warnings == ()`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest tests/test_runtime_context.py -q
```

Expected: collection fails because `obsidian_wiki.runtime_context` does not
exist.

- [ ] **Step 3: Implement the immutable inspection component**

Create `obsidian_wiki/runtime_context.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from obsidian_wiki.config import ConfigError, ResolvedConfig, resolve_config

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
    try:
        return path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return path.expanduser().absolute()


def nearest_portable_config(cwd: Path) -> Path | None:
    current = _absolute(Path(cwd))
    while True:
        candidate = current / ".obsidian-wiki/config.toml"
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
    current = _absolute(Path(cwd))
    candidate = nearest_portable_config(current)
    try:
        runtime = resolve_config(
            vault_arg,
            cwd=current,
            home=home,
            installed_version=installed_version,
            implementation=implementation,
        )
    except ConfigError as exc:
        status: RuntimeStatus = (
            "unconfigured"
            if vault_arg is None
            and candidate is None
            and exc.args == ("vault not configured",)
            else "error"
        )
        return RuntimeInspection(
            status=status,
            cwd=current,
            portable_config=candidate,
            runtime=None,
            warnings=(),
            error=exc,
            guidance=SETUP_GUIDANCE if status == "unconfigured" else None,
        )

    warnings: tuple[ContextWarning, ...] = ()
    if vault_arg is not None and candidate is not None:
        warning = ContextWarning(
            code="portable-context-overridden",
            message=(
                "explicit vault selection overrides portable context discovered "
                f"at {candidate}"
            ),
            hint="omit the explicit vault to retain portable repository semantics",
            portable_config=str(candidate),
            selected_mode=runtime.mode,
            selected_source=runtime.source,
            selected_vault=str(runtime.vault),
        )
        warnings = (warning,)
    return RuntimeInspection(
        status="resolved",
        cwd=current,
        portable_config=candidate,
        runtime=runtime,
        warnings=warnings,
    )
```

Do not import CLI globals or read installation state from this module.

- [ ] **Step 4: Run focused and config regression tests**

Run:

```bash
uv run pytest tests/test_runtime_context.py tests/test_portable_config.py -q
```

Expected: all pass; existing config precedence remains unchanged.

- [ ] **Step 5: Commit the runtime inspection slice**

```bash
git add obsidian_wiki/runtime_context.py tests/test_runtime_context.py
git commit -m "feat: inspect resolved CLI runtime context"
```

### Task 2: Make `info` resolution-aware and machine-readable

**Files:**
- Modify: `obsidian_wiki/cli.py:60-190,300-360,531-560,2274-2315,2546-2551`
- Create: `tests/test_info_cli.py`
- Modify: `tests/test_installation_policy.py:325-355`

- [ ] **Step 1: Write failing `info` contract tests**

Create `tests/test_info_cli.py` using a subprocess helper that sets `HOME` and
runs `python -m obsidian_wiki.cli`. Cover these exact assertions:

```python
def test_info_json_separates_portable_runtime_from_global_default(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root = _portable_repo(tmp_path)
    global_vault = tmp_path / "global-vault"
    _global_config(home, global_vault)

    result = _run(home, root / "nested", "info", "--json", "--pretty")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["runtime"]["status"] == "resolved"
    assert payload["runtime"]["mode"] == "portable"
    assert payload["runtime"]["vault"] == str((root / "wiki").resolve())
    assert payload["runtime"]["portable"] == {
        "root": str(root.resolve()),
        "sources": [str((root / "sources").resolve())],
        "skills": str((root / ".skills").resolve()),
        "local_state": str((root / ".obsidian-wiki/local").resolve()),
    }
    assert payload["installation"]["global_default"]["vault"] == str(global_vault)
    assert payload["warnings"] == []
    assert result.stderr == ""


def test_info_explicit_same_vault_reports_one_structured_warning(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root = _portable_repo(tmp_path)

    result = _run(
        home,
        root,
        "info",
        "--vault",
        str(root / "wiki"),
        "--json",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["runtime"]["mode"] == "explicit"
    assert [item["code"] for item in payload["warnings"]].count(
        "portable-context-overridden"
    ) == 1
    assert result.stderr == ""


def test_info_without_config_remains_available(tmp_path: Path) -> None:
    result = _run(tmp_path / "home", tmp_path, "info", "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["runtime"]["status"] == "unconfigured"
    assert "obsidian-wiki setup" in payload["runtime"]["guidance"]
    assert payload["installation"]["version"]


def test_info_invalid_portable_is_one_json_error_document(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / ".obsidian-wiki/config.toml").write_text("bad = [", encoding="utf-8")

    result = _run(tmp_path / "home", root, "info", "--json")

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["runtime"]["status"] == "error"
    assert "invalid portable configuration" in payload["runtime"]["error"]
    assert payload["installation"]["skills"]


def test_info_human_output_has_two_sections_and_one_stderr_warning(
    tmp_path: Path,
) -> None:
    root = _portable_repo(tmp_path)

    result = _run(
        tmp_path / "home", root, "info", "--vault", str(root / "wiki")
    )

    assert "Runtime context" in result.stdout
    assert "CLI installation" in result.stdout
    assert "skills root:" in result.stdout
    assert result.stderr.count("portable context") == 1
```

Add named, env, global, and explicit-path cases. Snapshot the selected config
files before and after `info --vault` and assert their bytes and symlink targets
are unchanged. Assert JSON contains no `⚠️`, `✅`, or human section headings.

Update the source-install regression to parse the new line:

```python
if line.strip().startswith("skills root:")
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest tests/test_info_cli.py tests/test_installation_policy.py -q
```

Expected: new `info` flags are unknown and the current output is not JSON.

- [ ] **Step 3: Add data collectors and renderers without printing during collection**

In `obsidian_wiki/cli.py`, import `RuntimeInspection` and
`inspect_runtime`. Add these helpers with the exact payload keys from the
design:

```python
def _inspect_cli_runtime(vault_arg: str | None = None) -> RuntimeInspection:
    return inspect_runtime(
        vault_arg,
        cwd=Path.cwd(),
        home=HOME,
        installed_version=__version__,
        implementation=IMPLEMENTATION_ID,
    )


def _runtime_payload(inspection: RuntimeInspection) -> dict[str, object]:
    if inspection.runtime is None:
        payload: dict[str, object] = {"status": inspection.status}
        if inspection.error is not None and inspection.status == "error":
            payload["error"] = _runtime_error_detail(inspection.error)
        if inspection.guidance is not None:
            payload["guidance"] = inspection.guidance
        return payload
    runtime = inspection.runtime
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
            "sources": [str(path) for path in portable.sources],
            "skills": str(portable.skills),
            "local_state": str(portable.local_state),
        }
    return payload


def _agent_install_payload(bundled: set[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative, label, _subset in GLOBAL_AGENT_DIRS:
        root = HOME / relative
        installed = (
            {path.name for path in root.iterdir() if path.is_dir() or path.is_symlink()}
            if root.is_dir()
            else set()
        )
        present = installed & bundled
        missing = bundled - installed
        records.append(
            {
                "label": label,
                "path": str(root),
                "status": (
                    "not-installed"
                    if not root.is_dir()
                    else "complete" if not missing else "partial"
                ),
                "installed": len(present),
                "bundled": len(bundled),
                "missing": sorted(missing),
            }
        )
    return records
```

Replace `_check_stale` internals with this collector returning at most one
object; keep `_check_stale()` as the human renderer so other commands retain
their behavior:

```python
def _stale_install_warnings(
    bundled: set[str] | None = None,
) -> list[dict[str, str]]:
    if not GLOBAL_CONFIG.is_file():
        return [
            {
                "code": "setup-not-run",
                "message": f"{version_label()} is installed but setup has never been run",
                "hint": "run: obsidian-wiki setup --vault /path/to/your/vault",
            }
        ]
    setup_version = _read_config_value("OBSIDIAN_WIKI_VERSION")
    if setup_version and setup_version != __version__:
        return [
            {
                "code": "setup-version-stale",
                "message": (
                    f"obsidian-wiki upgraded {setup_version} -> {version_label()} "
                    "but setup has not been re-run"
                ),
                "hint": "run: obsidian-wiki setup",
            }
        ]
    claude_skills = HOME / ".claude/skills"
    if claude_skills.is_dir():
        expected = set(list_skills()) if bundled is None else bundled
        installed = {
            path.name for path in claude_skills.iterdir() if path.is_dir()
        }
        missing = sorted(expected - installed)
        if missing:
            examples = ", ".join(missing[:3])
            if len(missing) > 3:
                examples += ", ..."
            return [
                {
                    "code": "agent-skills-missing",
                    "message": (
                        f"{len(missing)} skill(s) missing from ~/.claude/skills/ "
                        f"(e.g. {examples})"
                    ),
                    "hint": "run: obsidian-wiki setup",
                }
            ]
    return []


def _check_stale() -> None:
    for warning in _stale_install_warnings():
        print(f"warning: {warning['message']}", file=sys.stderr)
        print(f"  {warning['hint']}", file=sys.stderr)
```

Use codes `setup-not-run`, `setup-version-stale`, and
`agent-skills-missing`; do not change the order of these checks.

Add `_installation_payload()` with the exact return type and keys below. Reuse
the existing `_read_config()` and import `get_remote` inside the function so a
non-Git global vault returns `None` without changing runtime resolution:

```python
def _installation_payload() -> tuple[dict[str, object], list[dict[str, str]]]:
    from obsidian_wiki.sync import get_remote

    bundled = list_skills()
    bundled_set = set(bundled)
    config = _read_config()
    vault = config.get("OBSIDIAN_VAULT_PATH")
    remote = get_remote(Path(vault).expanduser()) if vault else None
    boot = bootstrap_dir()
    payload: dict[str, object] = {
        "version": version_label(),
        "skills": str(skills_dir()),
        "bootstrap": str(boot) if boot is not None else None,
        "global_config": str(GLOBAL_CONFIG),
        "global_default": {
            "configured": GLOBAL_CONFIG.is_file(),
            "vault": vault,
            "setup_version": config.get("OBSIDIAN_WIKI_VERSION"),
            "sync_remote": remote,
        },
        "bundled_skills": len(bundled),
        "agent_installs": _agent_install_payload(bundled_set),
    }
    return payload, _stale_install_warnings(bundled_set)
```

The collector may call `get_remote` only for the global default vault; a
missing or non-Git path produces `None` and is not a runtime error.

- [ ] **Step 4: Replace `cmd_info` and register flags**

Implement `cmd_info` as one collect-then-render flow:

```python
def cmd_info(args: argparse.Namespace) -> int:
    inspection = _inspect_cli_runtime(args.vault)
    installation, install_warnings = _installation_payload()
    warnings = [warning.as_dict() for warning in inspection.warnings]
    warnings.extend(install_warnings)
    payload = {
        "runtime": _runtime_payload(inspection),
        "installation": installation,
        "warnings": warnings,
    }
    if args.json:
        _json_print(payload, pretty=args.pretty)
    else:
        _print_info(payload)
        if inspection.status == "error" and inspection.error is not None:
            print(f"error: {_runtime_error_detail(inspection.error)}", file=sys.stderr)
        for warning in warnings:
            print(f"warning: {warning['message']}", file=sys.stderr)
            print(f"  {warning['hint']}", file=sys.stderr)
    return 1 if inspection.status == "error" else 0
```

Register:

```python
ip.add_argument("--vault", help="preview PATH or @name for this invocation")
_add_json_args(ip)
```

Make `_print_info` print exactly the two approved headings and `skills root:`
label. Use this renderer and do not call `_check_stale()` from `cmd_info`:

```python
def _print_info(payload: dict[str, object]) -> None:
    runtime = payload["runtime"]
    installation = payload["installation"]
    assert isinstance(runtime, dict)
    assert isinstance(installation, dict)
    print("Runtime context")
    for key in ("status", "mode", "source", "vault", "guidance", "error"):
        if runtime.get(key) is not None:
            print(f"  {key.replace('_', ' ')}: {runtime[key]}")
    portable = runtime.get("portable")
    if isinstance(portable, dict):
        print(f"  repository: {portable['root']}")
        for source in portable["sources"]:
            print(f"  source: {source}")
        print(f"  skills: {portable['skills']}")
        print(f"  local state: {portable['local_state']}")
    print("\nCLI installation")
    print(f"  version: {installation['version']}")
    print(f"  bundled skills: {installation['bundled_skills']}")
    print(f"  skills root: {installation['skills']}")
    print(f"  bootstrap: {installation['bootstrap'] or '(not found)'}")
    print(f"  global config: {installation['global_config']}")
    global_default = installation["global_default"]
    assert isinstance(global_default, dict)
    print(f"  global vault: {global_default.get('vault') or '(unset)'}")
    print("  agent installs:")
    for agent in installation["agent_installs"]:
        print(
            f"    {agent['label']}: {agent['installed']}/{agent['bundled']} "
            f"({agent['status']})"
        )
```

- [ ] **Step 5: Run focused and installation-policy tests**

Run:

```bash
uv run pytest tests/test_info_cli.py tests/test_installation_policy.py -q
```

Expected: all pass; source-installed CLI still resolves skills below the uv tool
directory after its source clone moves.

- [ ] **Step 6: Commit the `info` slice**

```bash
git add obsidian_wiki/cli.py tests/test_info_cli.py tests/test_installation_policy.py
git commit -m "feat: report resolved CLI runtime info"
```

### Task 3: Attach context warnings to configuration-aware commands

**Files:**
- Modify: `obsidian_wiki/cli.py:330-360,928-1025,1873-2280`
- Modify: `tests/test_doctor.py`
- Modify: `tests/test_query_cli.py`
- Modify: `tests/test_context_pack_cli.py`
- Modify: `tests/test_lint.py`
- Modify: `tests/test_trust.py`

- [ ] **Step 1: Add command-level warning tests**

Extend existing explicit-vault tests so their CWD contains a portable config
and assert:

```python
warning = payload["context_warnings"]
assert len(warning) == 1
assert warning[0]["code"] == "portable-context-overridden"
assert warning[0]["selected_mode"] in {"explicit", "named"}
```

Cover `doctor`, `lint`, `trust-record`, `trust-check`, `query`, and
`context-pack` JSON outputs. For one human `query` case assert:

```python
assert result.returncode == 0
assert result.stderr.count("warning: explicit vault selection overrides") == 1
assert "portable-context-overridden" not in result.stdout
```

For a no-override portable case in every JSON-producing command assert:

```python
assert payload["context_warnings"] == []
```

For `doctor --strict`, compare the business status and exit code with the same
explicit vault invocation outside the portable CWD; the informational context
warning must not change either. Assert any existing numeric `warnings` value is
still an integer.

- [ ] **Step 2: Run the participating command modules and verify RED**

Run:

```bash
uv run pytest \
  tests/test_doctor.py \
  tests/test_query_cli.py \
  tests/test_context_pack_cli.py \
  tests/test_lint.py \
  tests/test_trust.py \
  -q
```

Expected: the new tests fail because `context_warnings` is missing and the
human context-warning assertion sees no matching stderr line. Existing tests
continue to pass.

- [ ] **Step 3: Add shared CLI warning helpers**

In `obsidian_wiki/cli.py` add:

```python
def _context_warning_payloads(
    inspection: RuntimeInspection,
) -> list[dict[str, str]]:
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
```

Do not make `_resolve_runtime()` emit warnings; commands outside this approved
surface keep their current behavior.

- [ ] **Step 4: Wire each participating command once**

For lint/trust/query/context, replace the initial `_resolve_runtime` call with
`_resolved_inspection`. Before human ordinary output call
`_emit_context_warnings(inspection)`. Before JSON serialization call
`_attach_context_warnings(report, inspection)`.

Refactor `run_doctor` to accept an optional precomputed inspection. Add one
helper so every early and final report carries the same warning array:

```python
def _doctor_with_context(
    report: dict[str, object], inspection: RuntimeInspection
) -> dict[str, object]:
    report["context_warnings"] = _context_warning_payloads(inspection)
    return report


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
        return _doctor_with_context(
            _doctor_resolution_error(inspected.error), inspected
        )
```

After this replacement prologue, keep the existing Personal-mode check
construction. Replace its current `runtime` and config-present inputs with the
variables above, and wrap its one final report in `_doctor_with_context`. In
`cmd_doctor`, compute inspection once, pass it to `run_doctor`, and emit its
human warning before `_print_doctor`.

For every JSON branch mutate only the fresh command result dictionary; never
write context warnings into vault files or persistent ledgers.

- [ ] **Step 5: Run participating command suites**

Run:

```bash
uv run pytest \
  tests/test_doctor.py \
  tests/test_query_cli.py \
  tests/test_context_pack_cli.py \
  tests/test_lint.py \
  tests/test_trust.py \
  -q
```

Expected: all pass. Context warnings are additive and existing strict/business
status behavior is unchanged.

- [ ] **Step 6: Commit the command integration slice**

```bash
git add \
  obsidian_wiki/cli.py \
  tests/test_doctor.py \
  tests/test_query_cli.py \
  tests/test_context_pack_cli.py \
  tests/test_lint.py \
  tests/test_trust.py
git commit -m "feat: warn on portable context overrides"
```

### Task 4: Close explicit-vault sync bypasses

**Files:**
- Modify: `obsidian_wiki/cli.py:1319-1360`
- Modify: `tests/test_sync.py:140-205`

- [ ] **Step 1: Add failing mutation-boundary tests**

Extend the portable sync parameterization with explicit vault arguments that
point both to the portable vault and to an unrelated Personal vault:

```python
@pytest.mark.parametrize(
    "arguments",
    [
        ("sync", "--vault", "wiki"),
        (
            "sync-setup",
            "https://example.invalid/knowledge.git",
            "--vault",
            "../personal-vault",
        ),
    ],
)
def test_explicit_vault_cannot_bypass_portable_git_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
) -> None:
    root = _portable_repository(tmp_path)
    before_head = _git(root, "rev-parse", "HEAD").stdout
    before_status = _git(root, "status", "--porcelain=v1").stdout
    before_remotes = _git(root, "remote", "-v").stdout
    monkeypatch.chdir(root)

    result = main(list(arguments))

    captured = capsys.readouterr()
    assert result == 1
    assert "portable repositories use branch and pull-request workflows" in captured.err
    assert _git(root, "rev-parse", "HEAD").stdout == before_head
    assert _git(root, "status", "--porcelain=v1").stdout == before_status
    assert _git(root, "remote", "-v").stdout == before_remotes
    assert not (root / "wiki/.git").exists()
```

Monkeypatch `obsidian_wiki.sync.run_sync` and `configure_sync` to raise
`AssertionError` if called. Add a dangling `.obsidian-wiki/config.toml` symlink
case and assert the helpers are still not called. Retain the existing Personal
mode sync tests.

- [ ] **Step 2: Run the sync tests and verify RED**

Run:

```bash
uv run pytest tests/test_sync.py -q
```

Expected: explicit `--vault` bypasses the current `runtime.mode == "portable"`
guard and reaches a forbidden sync helper.

- [ ] **Step 3: Add a pre-resolution portable Git guard**

In `obsidian_wiki/cli.py` import `nearest_portable_config` and add:

```python
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
```

Make this the first statement in both commands:

```python
if _refuse_portable_git_workflow():
    return 1
```

Keep the existing post-resolution `runtime.mode == "portable"` checks as
defense in depth for portable vaults selected from outside their repository.

- [ ] **Step 4: Run sync and portable Git regression tests**

Run:

```bash
uv run pytest tests/test_sync.py tests/test_portable_git.py -q
```

Expected: all pass; no Git index, remote, nested `.git`, or portable file is
modified in refusal cases.

- [ ] **Step 5: Commit the Git safety slice**

```bash
git add obsidian_wiki/cli.py tests/test_sync.py
git commit -m "fix: prevent portable sync override bypasses"
```

### Task 5: Add the pure transaction recovery guidance matrix

**Files:**
- Create: `obsidian_wiki/transaction_guidance.py`
- Create: `tests/test_transaction_guidance.py`

- [ ] **Step 1: Write the complete status/action tests**

Create a trusted record helper and assert complete objects, not substrings:

```python
from pathlib import Path

import pytest

from obsidian_wiki.transaction import TransactionRecord
from obsidian_wiki.transaction_guidance import (
    guidance_for_record,
    inspection_only_guidance,
)


def _record(tmp_path: Path, status: str) -> TransactionRecord:
    workspace = tmp_path / "transactions/tx-1"
    return TransactionRecord(
        transaction_id="tx-1",
        status=status,
        started_at="2026-08-10T00:00:00Z",
        source_ids=("sources/a.md",),
        workspace=workspace,
        candidate_vault=workspace / "wiki",
        preimages={},
        deletions=(),
    )


@pytest.mark.parametrize(
    ("status", "preferred", "allowed"),
    [
        (
            "active",
            "obsidian-wiki transaction commit tx-1",
            {
                "obsidian-wiki transaction commit tx-1",
                "obsidian-wiki transaction abort tx-1",
            },
        ),
        (
            "promoting",
            "obsidian-wiki transaction restore tx-1",
            {"obsidian-wiki transaction restore tx-1"},
        ),
        (
            "failed",
            "obsidian-wiki transaction retry tx-1",
            {
                "obsidian-wiki transaction retry tx-1",
                "obsidian-wiki transaction restore tx-1",
                "obsidian-wiki transaction abort tx-1",
                "obsidian-wiki transaction discard tx-1",
            },
        ),
        (
            "complete",
            "obsidian-wiki transaction discard tx-1",
            {
                "obsidian-wiki transaction discard tx-1",
                "obsidian-wiki transaction restore tx-1",
            },
        ),
        (
            "restored",
            "obsidian-wiki transaction discard tx-1",
            {
                "obsidian-wiki transaction discard tx-1",
                "obsidian-wiki transaction restore tx-1",
            },
        ),
    ],
)
def test_guidance_matrix(
    tmp_path: Path, status: str, preferred: str, allowed: set[str]
) -> None:
    guidance = guidance_for_record(_record(tmp_path, status))

    assert guidance.preferred_action is not None
    assert guidance.preferred_action.command == preferred
    assert {action.command for action in guidance.allowed_actions} == allowed
    assert all(action.reason for action in guidance.allowed_actions)
    assert all(action.requires for action in guidance.allowed_actions)


def test_untrusted_state_only_recommends_inspection() -> None:
    guidance = inspection_only_guidance()

    assert guidance.transaction_id is None
    assert guidance.transaction_status is None
    assert guidance.preferred_action is None
    assert guidance.allowed_actions == ()
    assert guidance.inspect_command == "obsidian-wiki transaction list --json"
```

Add exact prerequisite assertions: retry mentions fixed cause and matching
preimages; complete restore mentions matching postimages; failed discard says
the outcome must be understood; promoting exposes no action other than restore.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest tests/test_transaction_guidance.py -q
```

Expected: module import fails.

- [ ] **Step 3: Implement the pure guidance module**

Create `obsidian_wiki/transaction_guidance.py` with frozen values:

```python
from __future__ import annotations

from dataclasses import dataclass

from obsidian_wiki.transaction import TransactionRecord

INSPECT_COMMAND = "obsidian-wiki transaction list --json"


@dataclass(frozen=True)
class RecoveryAction:
    command: str
    reason: str
    requires: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "reason": self.reason,
            "requires": list(self.requires),
        }


@dataclass(frozen=True)
class RecoveryGuidance:
    transaction_id: str | None
    transaction_status: str | None
    inspect_command: str
    preferred_action: RecoveryAction | None
    alternatives: tuple[RecoveryAction, ...]

    @property
    def allowed_actions(self) -> tuple[RecoveryAction, ...]:
        if self.preferred_action is None:
            return self.alternatives
        return (self.preferred_action, *self.alternatives)

    def as_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "transaction_status": self.transaction_status,
            "inspect_command": self.inspect_command,
            "preferred_action": (
                self.preferred_action.as_dict()
                if self.preferred_action is not None
                else None
            ),
            "alternatives": [action.as_dict() for action in self.alternatives],
        }


def _action(
    transaction_id: str,
    command: str,
    reason: str,
    *requires: str,
) -> RecoveryAction:
    return RecoveryAction(
        command=f"obsidian-wiki transaction {command} {transaction_id}",
        reason=reason,
        requires=tuple(requires),
    )


def inspection_only_guidance() -> RecoveryGuidance:
    return RecoveryGuidance(None, None, INSPECT_COMMAND, None, ())
```

Implement `guidance_for_record(record)` with explicit `if` branches for the
five statuses:

```python
def guidance_for_record(record: TransactionRecord) -> RecoveryGuidance:
    transaction_id = record.transaction_id
    status = record.status
    if status == "active":
        preferred = _action(
            transaction_id,
            "commit",
            "commit after fixing the original cause and reviewing the candidate",
            "the original failure cause is removed",
            "the candidate vault has been reviewed",
        )
        alternatives = (
            _action(
                transaction_id,
                "abort",
                "abandon the active staged work",
                "the candidate is no longer needed",
            ),
        )
    elif status == "promoting":
        preferred = _action(
            transaction_id,
            "restore",
            "restore an interrupted promotion from retained snapshots",
            "the retained snapshots and current working tree have been inspected",
        )
        alternatives = ()
    elif status == "failed":
        preferred = _action(
            transaction_id,
            "retry",
            "retry after the original cause is removed",
            "the original failure cause is removed",
            "affected targets still match their recorded preimages",
        )
        alternatives = (
            _action(
                transaction_id,
                "restore",
                "restore recorded originals instead of retrying",
                "the retained snapshots and current working tree have been inspected",
            ),
            _action(
                transaction_id,
                "abort",
                "abandon the failed staged work",
                "no retry or restore is required",
            ),
            _action(
                transaction_id,
                "discard",
                "remove retained recovery state",
                "the failed outcome is understood",
                "no retained recovery evidence is still needed",
            ),
        )
    elif status == "complete":
        preferred = _action(
            transaction_id,
            "discard",
            "remove retained recovery state after accepting the result",
            "the ordinary Git diff has been reviewed and accepted",
        )
        alternatives = (
            _action(
                transaction_id,
                "restore",
                "roll back the completed transaction",
                "all affected files still match their recorded postimages",
            ),
        )
    elif status == "restored":
        preferred = _action(
            transaction_id,
            "discard",
            "remove retained state after verifying the restore",
            "the restored working tree has been reviewed",
        )
        alternatives = (
            _action(
                transaction_id,
                "restore",
                "confirm the idempotent restored state",
                "a second restore is intentionally being used as a no-op",
            ),
        )
    else:
        raise ValueError(f"unsupported transaction status: {status!r}")
    return RecoveryGuidance(
        transaction_id=transaction_id,
        transaction_status=status,
        inspect_command=INSPECT_COMMAND,
        preferred_action=preferred,
        alternatives=alternatives,
    )
```

Raise `ValueError` for an unknown status; callers only pass records returned by
`TransactionManager.load`.
Do not import subprocess, Git helpers, CLI globals, or filesystem mutation APIs.

- [ ] **Step 4: Run focused tests and transaction import regression**

Run:

```bash
uv run pytest \
  tests/test_transaction_guidance.py \
  tests/test_transaction.py::test_transaction_module_imports_when_fcntl_is_unavailable \
  -q
```

Expected: all pass on POSIX and the simulated Windows import path.

- [ ] **Step 5: Commit the pure guidance slice**

```bash
git add obsidian_wiki/transaction_guidance.py tests/test_transaction_guidance.py
git commit -m "feat: derive safe transaction recovery guidance"
```

### Task 6: Serialize transaction failures and annotate retained state

**Files:**
- Modify: `obsidian_wiki/cli.py:1675-1828,3125-3155`
- Modify: `tests/test_transaction.py:2910-3070`

- [ ] **Step 1: Write failing CLI failure-envelope tests**

Add these behaviors to `tests/test_transaction.py`:

```python
def test_transaction_json_failure_is_structured_outside_portable_mode(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "ordinary"
    cwd.mkdir()

    result = run_cli(
        tmp_path / "home", cwd, "transaction", "commit", "tx-1", "--json"
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "config-error"
    assert payload["recovery"] == {
        "transaction_id": None,
        "transaction_status": None,
        "inspect_command": "obsidian-wiki transaction list --json",
        "preferred_action": None,
        "alternatives": [],
    }


def test_active_commit_failure_reports_review_then_commit_guidance(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    source = add_source(root)
    manager = TransactionManager(config)
    manager.begin([source], transaction_id="tx-1")

    result = run_cli(
        tmp_path / "home", root, "transaction", "commit", "tx-1", "--json"
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert result.stderr == ""
    assert payload["error"]["code"] == "transaction-error"
    assert payload["recovery"]["transaction_status"] == "active"
    assert payload["recovery"]["preferred_action"]["command"] == (
        "obsidian-wiki transaction commit tx-1"
    )
    assert TransactionManager(config).load("tx-1").status == "active"


def test_corrupt_record_never_generates_a_mutating_recovery_command(
    tmp_path: Path,
) -> None:
    root, config = make_config(tmp_path)
    record = TransactionManager(config).begin(
        [add_source(root)], transaction_id="tx-1"
    )
    (record.workspace / "metadata.json").write_text("{}\n", encoding="utf-8")

    result = run_cli(
        tmp_path / "home", root, "transaction", "commit", "tx-1", "--json"
    )

    recovery = json.loads(result.stdout)["recovery"]
    assert recovery["transaction_id"] is None
    assert recovery["preferred_action"] is None
    assert recovery["alternatives"] == []


def test_human_transaction_failure_uses_stderr_only(tmp_path: Path) -> None:
    root, _config = make_config(tmp_path)

    result = run_cli(tmp_path / "home", root, "transaction", "commit", "missing")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "error:" in result.stderr
    assert "obsidian-wiki transaction list --json" in result.stderr
```

Update the retained-list assertions:

```python
record = json.loads(listed.stdout)[0]
assert record["recommended_action"]["command"].endswith(
    "transaction commit " + transaction_id
)
assert {item["command"] for item in record["allowed_actions"]} == {
    f"obsidian-wiki transaction commit {transaction_id}",
    f"obsidian-wiki transaction abort {transaction_id}",
}
```

Parameterize JSON failures for begin, list, delete, commit, retry, restore,
discard, and abort outside portable mode. Preserve human-mode assertions for
commands without `--json`.

- [ ] **Step 2: Run the new transaction CLI tests and verify RED**

Run:

```bash
uv run pytest \
  tests/test_transaction.py::test_transaction_json_failure_is_structured_outside_portable_mode \
  tests/test_transaction.py::test_active_commit_failure_reports_review_then_commit_guidance \
  tests/test_transaction.py::test_corrupt_record_never_generates_a_mutating_recovery_command \
  tests/test_transaction.py::test_human_transaction_failure_uses_stderr_only \
  tests/test_transaction.py::test_transaction_cli_complete_lifecycle_and_git_is_read_only \
  -q
```

Expected: the global handler writes plain stderr, JSON stdout is empty, and
list records lack action fields.

- [ ] **Step 3: Add one trusted failure renderer**

In `obsidian_wiki/cli.py`, add:

```python
def _transaction_error_code(error: Exception) -> str:
    from obsidian_wiki.portable_manifest import ManifestError
    from obsidian_wiki.transaction import TransactionError

    if isinstance(error, ConfigError):
        return "config-error"
    if isinstance(error, ManifestError):
        return "manifest-error"
    if isinstance(error, TransactionError):
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
    except (TransactionError, OSError, UnicodeError, ValueError):
        return inspection_only_guidance()
    return guidance_for_record(record)


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
        _json_print(payload, pretty=args.pretty)
        return 1
    print(f"error: {error}", file=sys.stderr)
    if guidance.transaction_status is not None:
        print(f"transaction status: {guidance.transaction_status}", file=sys.stderr)
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
```

Import `TransactionError` in `_trusted_recovery_guidance`. The concrete catch
is deliberately limited to untrusted filesystem/decoding/validation state; a
programmer error must not be silently converted into guidance.

- [ ] **Step 4: Catch errors inside every transaction handler**

For each handler, retain a nullable manager and catch exactly
`ConfigError`, `ManifestError`, and `TransactionError`:

```python
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
```

Apply the same pattern to begin, list, delete, and `_transaction_state_action`.
For begin and list pass no transaction ID. Do not catch `KeyboardInterrupt`,
`SystemExit`, or unexpected programmer errors.

In `main`, keep stale-install human warnings out of every JSON invocation so a
transaction error can honor its stdout/stderr contract:

```python
if (
    getattr(args, "command", None)
    not in ("setup", "repo", "info", "doctor", "check", None)
    and not getattr(args, "json", False)
):
    _check_stale()
```

`info --json` still exposes collected installation warnings in its `warnings`
array; this change only prevents the global human renderer from contaminating
machine-oriented invocations.

The ellipsis in the example is the existing unchanged success output block;
copy it exactly rather than inventing a new success schema.

- [ ] **Step 5: Annotate list payloads from the same mapper**

Extend `_record_payload`:

```python
guidance = guidance_for_record(record)
return {
    # existing fields unchanged
    "recommended_action": (
        guidance.preferred_action.as_dict()
        if guidance.preferred_action is not None
        else None
    ),
    "allowed_actions": [
        action.as_dict() for action in guidance.allowed_actions
    ],
}
```

Human list rows become:

```python
recommended = (
    guidance_for_record(record).preferred_action.command
    if guidance_for_record(record).preferred_action is not None
    else "-"
)
print(f"{record.transaction_id}\t{record.status}\t{recommended}\t{record.workspace}")
```

Compute guidance once per record. Do not change the top-level JSON list.

- [ ] **Step 6: Run transaction and write-protocol suites**

Run:

```bash
uv run pytest \
  tests/test_transaction_guidance.py \
  tests/test_transaction.py \
  tests/test_portable_write_protocol.py \
  -q
```

Expected: all pass. Existing success payloads and Git-read-only assertions
remain unchanged.

- [ ] **Step 7: Commit transaction CLI integration**

```bash
git add obsidian_wiki/cli.py tests/test_transaction.py
git commit -m "feat: explain portable transaction recovery"
```

### Task 7: Document and verify the complete CLI UX

**Files:**
- Modify: `docs/cli.md:20-45,150-210`
- Modify: `docs/configuration.md:15-110`
- Modify: `tests/test_portable_write_protocol.py`

- [ ] **Step 1: Add documentation contract tests**

Extend `tests/test_portable_write_protocol.py` to require the active docs to
name the stable interfaces:

```python
def test_cli_docs_name_runtime_context_and_recovery_contracts() -> None:
    cli = (ROOT / "docs/cli.md").read_text(encoding="utf-8")
    configuration = (ROOT / "docs/configuration.md").read_text(encoding="utf-8")

    for required in (
        "obsidian-wiki info --json --pretty",
        "portable-context-overridden",
        "context_warnings",
        '"recovery"',
        "recommended_action",
        "allowed_actions",
        "obsidian-wiki transaction list --json",
    ):
        assert required in cli
    assert "same vault" in configuration
    assert "sync" in configuration
    assert "sync-setup" in configuration
    assert "branch and pull request" in configuration
```

- [ ] **Step 2: Run the documentation test and verify RED**

Run:

```bash
uv run pytest tests/test_portable_write_protocol.py -q
```

Expected: the new stable names are absent.

- [ ] **Step 3: Update `docs/cli.md`**

Document:

- `info`, `info --vault`, `--json`, and `--pretty`;
- the `runtime`, `installation`, and `warnings` sections;
- participating commands' additive `context_warnings` field;
- the one-line human stderr warning and unchanged exit status;
- the explicit-vault sync/sync-setup refusal;
- transaction JSON error envelope and human stderr ordering;
- preferred/allowed action fields on `transaction list`;
- the complete five-status recovery matrix and the inspection-only fallback;
- the fact that guidance never executes recovery or Git.

Use the exact warning code and JSON field names from the design. Keep Personal
mode sync documentation intact outside portable context.

- [ ] **Step 4: Update `docs/configuration.md`**

Add the explicit rule:

```markdown
An explicit PATH or `@name` still wins resolution. When the current directory
also discovers a Portable Repository config, configuration-aware CLI commands
report `portable-context-overridden` because the selected invocation no longer
has portable semantics. This warning also applies when the explicit path names
the same vault. It is informational and does not change the command exit code.
`sync` and `sync-setup` are stricter: they refuse before mutation whenever the
current directory discovers a portable config, so `--vault` cannot bypass the
branch and pull request boundary.
```

State that lexical discovery includes symlink and dangling-symlink entries and
that an explicit override does not load the shadowed TOML solely for warning
generation.

- [ ] **Step 5: Run the complete affected feature set**

Run:

```bash
uv run pytest \
  tests/test_runtime_context.py \
  tests/test_info_cli.py \
  tests/test_portable_config.py \
  tests/test_doctor.py \
  tests/test_query_cli.py \
  tests/test_context_pack_cli.py \
  tests/test_lint.py \
  tests/test_trust.py \
  tests/test_sync.py \
  tests/test_portable_git.py \
  tests/test_transaction_guidance.py \
  tests/test_transaction.py \
  tests/test_installation_policy.py \
  tests/test_portable_write_protocol.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Run repository hygiene and commit docs**

Run:

```bash
git diff --check
uv run python tools/check_readme_sync.py
git status --short
```

Expected: diff check is silent; README_ZH is current because neither README
changed; status contains only the planned documentation/test edits.

Commit:

```bash
git add docs/cli.md docs/configuration.md tests/test_portable_write_protocol.py
git commit -m "docs: explain CLI context and recovery guidance"
```

### Task 8: Final security and regression verification

**Files:**
- Verify only; no planned file changes.

- [ ] **Step 1: Re-run context and recovery security slices**

Run:

```bash
uv run pytest \
  tests/test_runtime_context.py \
  tests/test_sync.py \
  tests/test_transaction_guidance.py \
  tests/test_transaction.py \
  -q
```

Expected: explicit overrides cannot trigger Git mutations, invalid trusted
config does not fall back, untrusted records never produce mutating guidance,
and no recovery action is called implicitly.

- [ ] **Step 2: Run the full suite**

Run:

```bash
uv run pytest -q
```

Expected: the complete suite and all subprocess subtests pass.

- [ ] **Step 3: Run final repository checks**

Run:

```bash
git diff --check
uv run python tools/check_readme_sync.py
git status --short
git log --oneline 7efe2b1..HEAD
```

Expected: no whitespace error, no README translation drift, a clean worktree,
and only the approved context/recovery implementation commits after the design
baseline.

- [ ] **Step 4: Inspect the exact implementation range**

Run:

```bash
git diff 7efe2b1..HEAD -- \
  obsidian_wiki/runtime_context.py \
  obsidian_wiki/transaction_guidance.py \
  obsidian_wiki/cli.py \
  tests/test_runtime_context.py \
  tests/test_info_cli.py \
  tests/test_doctor.py \
  tests/test_query_cli.py \
  tests/test_context_pack_cli.py \
  tests/test_lint.py \
  tests/test_trust.py \
  tests/test_sync.py \
  tests/test_transaction_guidance.py \
  tests/test_transaction.py \
  tests/test_installation_policy.py \
  tests/test_portable_write_protocol.py \
  docs/cli.md \
  docs/configuration.md
```

Confirm all of the following before approval:

- config precedence and `resolve_config()` are unchanged;
- an overridden invalid portable file is not loaded merely for a warning;
- authoritative invalid portable state still fails closed;
- context warnings occur at most once and never alter strict/business status;
- sync refusal happens before any mutation helper;
- transaction guidance uses only a validated record;
- JSON failure output is one document and human failure stdout is empty;
- no recovery or Git action is invoked by inspection or rendering;
- no absolute runtime path is persisted into portable files.

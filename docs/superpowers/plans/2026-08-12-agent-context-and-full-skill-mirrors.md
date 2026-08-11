# Agent Context Isolation and Full Skill Mirrors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate framework development context from wiki runtime behavior, package built-in skills outside agent discovery paths, replace portable skill adapters with complete deterministic mirrors, and remove automatic Claude Stop capture.

**Architecture:** Add two small pure modules for skill-tree validation/snapshots and managed-inventory parsing, then keep repository mutation and recovery orchestration in `portable.py`. The installed package owns built-in skills and runtime bootstrap templates under `obsidian_wiki/_data/`; a portable repository owns canonical `.skills/`, while six ordinary-file agent trees are derived, checked, and explicitly synchronized from it. Existing replacement journals are generalized so skill upgrades and mirror synchronization share the same containment, rollback, and recovery guarantees.

**Tech Stack:** Python 3.8+, frozen dataclasses, `pathlib`, `hashlib`, restricted frontmatter parsing, `argparse`, JSON, filesystem-local rename/rollback journals, Hatchling, uv, Git, pytest, Markdown, Simplified Chinese README parity.

---

## Delivery and File Structure

At execution time, use `superpowers:using-git-worktrees` before changing files.
Create `feat/agent-context-full-skill-mirrors` from design commit `474bf2f` for
Python, package resources, runtime skills, tests, and source bootstrap changes. After
the development branch is green, create `docs/agent-context-full-skill-mirrors` from
its tip for human documentation and README changes. Keep documentation commits on the
docs branch, then run final integration from the docs branch tip.

The implementation uses these boundaries:

- Create `obsidian_wiki/skill_trees.py`: validate, snapshot, hash, compare, and
  materialize ordinary skill collections; this module performs no repository mutation
  outside an explicitly supplied destination.
- Create `obsidian_wiki/skill_inventory.py`: parse legacy and schema-v2 inventories
  and render schema-v2 inventory bytes.
- Create `tools/capture_legacy_skill_digests.py`: deterministically record recognized
  schema-v1 canonical baselines before bundled skill content changes.
- Move `.skills/**` to `obsidian_wiki/_data/skills/**`: the only framework built-in
  skill source and installed package resource.
- Move runtime bootstrap templates to `obsidian_wiki/_data/bootstrap/**`; root
  bootstrap files become development-only pointers.
- Modify `obsidian_wiki/portable.py`: setup, full-mirror generation, dry-run planning,
  synchronization, upgrade, legacy migration, and journal recovery.
- Modify `obsidian_wiki/portable_check.py`: canonical/inventory/mirror validation and
  stable issue reporting.
- Modify `obsidian_wiki/migration.py`: create full mirrors and schema-v2 inventory in
  migrated repositories.
- Modify `obsidian_wiki/cli.py`: package-only data resolution and
  `repo sync-skills [--apply] [--json] [--pretty]`.
- Modify `pyproject.toml`: package tracked `_data` resources directly and remove Hook
  and root-skill force-includes.
- Create `tests/test_skill_trees.py`, `tests/test_skill_inventory.py`, and
  `tests/test_agent_context_boundary.py`; extend portable setup/check/migration/E2E
  tests and replace Hook packaging tests with negative guards.
- Delete `.claude/hooks/wiki-stop-capture.sh`, `.claude/settings.json`,
  `tests/test_stop_hook_behavior.py`, and `tests/test_stop_hook_packaging.py`.
- Update runtime skill Markdown under `obsidian_wiki/_data/skills/`, then update
  `README.md`, `README_ZH.md`, and the relevant `docs/*.md` pages.

### Task 1: Add Deterministic Skill-Tree Primitives

**Files:**
- Create: `obsidian_wiki/skill_trees.py`
- Create: `tests/test_skill_trees.py`

- [ ] **Step 1: Write failing tests for safe discovery, frontmatter, binary bytes, Unicode, modes, and digests**

Add tests with a helper that always writes valid discovery metadata:

```python
def write_skill(root: Path, name: str, description: str = "Use this skill.") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill


def test_discovery_preserves_unicode_binary_and_executable_mode(tmp_path: Path) -> None:
    source = tmp_path / "skills"
    skill = write_skill(source, "wiki-demo", "Use for demo requests.")
    resource = skill / "references/中文.bin"
    resource.parent.mkdir()
    resource.write_bytes(b"\x00\xffwiki\r\n")
    script = skill / "scripts/run.sh"
    script.parent.mkdir()
    script.write_bytes(b"#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    collection = discover_skill_collection(source)

    assert collection.names == ("wiki-demo",)
    assert collection.skills[0].name == "wiki-demo"
    assert collection.skills[0].description == "Use for demo requests."
    assert any(entry.path == "references/中文.bin" and entry.content == b"\x00\xffwiki\r\n" for entry in collection.skills[0].entries)
    assert any(entry.path == "scripts/run.sh" and entry.executable for entry in collection.skills[0].entries)
    assert collection.skills[0].digest.startswith("sha256:")


@pytest.mark.parametrize(
    ("frontmatter", "match"),
    [
        ("# no metadata\n", "frontmatter"),
        ("---\nname: wiki-demo\n---\n", "description"),
        ("---\nname: other\ndescription: Demo\n---\n", "directory name"),
    ],
)
def test_discovery_rejects_invalid_skill_metadata(
    tmp_path: Path, frontmatter: str, match: str
) -> None:
    skill = tmp_path / "skills/wiki-demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(frontmatter, encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        discover_skill_collection(tmp_path / "skills")
```

Also test a source symlink, a multiply linked regular file, a special file where the
platform supports it, an unsafe top-level skill name, byte-sensitive digest changes,
and identical materialization into two destinations.

- [ ] **Step 2: Run the focused tests and verify the new module is missing**

Run: `uv run pytest tests/test_skill_trees.py -q`

Expected: collection fails with `ModuleNotFoundError: obsidian_wiki.skill_trees`.

- [ ] **Step 3: Implement the immutable snapshot API**

Implement these public types and functions in `skill_trees.py`:

```python
@dataclass(frozen=True)
class SkillEntry:
    path: str
    kind: Literal["directory", "file"]
    executable: bool
    content: bytes


@dataclass(frozen=True)
class SkillTree:
    name: str
    description: str
    entries: tuple[SkillEntry, ...]
    digest: str


@dataclass(frozen=True)
class SkillCollection:
    skills: tuple[SkillTree, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self.skills)

    def by_name(self) -> dict[str, SkillTree]:
        return {skill.name: skill for skill in self.skills}


def discover_skill_collection(
    root: Path, *, ignore_source_artifacts: bool = False
) -> SkillCollection:
    """Return a sorted, fully validated ordinary-file snapshot of root."""


def materialize_skill_collection(
    collection: SkillCollection, destination: Path
) -> None:
    """Create destination from a snapshot; destination must not already exist."""


def compare_skill_collections(
    canonical: SkillCollection, mirror: SkillCollection
) -> tuple[dict[str, tuple[str, ...]], ...]:
    """Return deterministic added, changed, and removed path records."""
```

Use `frontmatter.parse_frontmatter()` for `name` and `description`. Reject decoding
errors, missing/empty scalars, name-directory mismatches, symlinks, hard links, and
special files. Sort names and relative paths by Unicode code point. Hash an
unambiguous stream containing the skill name plus each entry's UTF-8 path, kind,
executable bit, content length, and exact bytes; render it as `sha256:` followed by
exactly 64 lowercase hexadecimal characters.
Materialization writes exact bytes and applies `0o755` to executable files and
`0o644` to non-executable files. It creates ordinary `0o755` directories and never
normalizes line endings.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_skill_trees.py -q`

Expected: all skill-tree tests pass.

```bash
git add obsidian_wiki/skill_trees.py tests/test_skill_trees.py
git commit -m "feat: add deterministic skill tree snapshots"
```

### Task 2: Move Product Assets and Isolate Framework Agent Context

**Files:**
- Create: `tools/capture_legacy_skill_digests.py`
- Create: `obsidian_wiki/_data/legacy-skill-digests-v1.json`
- Move: `.skills/**` → `obsidian_wiki/_data/skills/**`
- Move: root runtime bootstrap sources → `obsidian_wiki/_data/bootstrap/**`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `GEMINI.md`
- Modify: `.hermes.md`
- Modify: `.agent/rules/obsidian-wiki.md`
- Modify: `.agent/workflows/obsidian-wiki.md`
- Modify: `.cursor/rules/obsidian-wiki.mdc`
- Modify: `.windsurf/rules/obsidian-wiki.md`
- Modify: `.kiro/steering/obsidian-wiki.md`
- Modify: `.github/copilot-instructions.md`
- Delete: `.claude/skills/**`
- Delete: `.cursor/skills/**`
- Delete: `.windsurf/skills/**`
- Delete: `.agents/skills/**`
- Delete: `.pi/skills/**`
- Delete: `.kiro/skills/**`
- Modify: `obsidian_wiki/cli.py`
- Modify: `obsidian_wiki/__init__.py`
- Modify: `pyproject.toml`
- Create: `tests/test_agent_context_boundary.py`
- Modify: filesystem-path references in `tests/test_installation_policy.py`,
  `tests/test_context_pack_docs.py`, `tests/test_doctor.py`,
  `tests/test_inline_vault_targeting_docs.py`, `tests/test_portable_manifest_docs.py`,
  `tests/test_portable_migration.py`, `tests/test_portable_setup.py`,
  `tests/test_portable_skill_protocol.py`, `tests/test_portable_write_protocol.py`,
  `tests/test_pre_write_snapshot_docs.py`, `tests/test_session_brain_docs.py`, and
  `tests/test_wiki_narrate_docs.py`

- [ ] **Step 1: Write boundary and package-only resolution tests**

Add assertions that the framework root has no local skill discovery tree, every root
bootstrap is an ordinary file whose content points to development `AGENTS.md`, runtime
assets exist only under `_data`, and `skills_dir()`/`bootstrap_dir()` never fall back
to the checkout root:

```python
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "obsidian_wiki/_data"
DISCOVERY_DIRS = tuple(relative for relative, _label in cli.PROJECT_AGENT_DIRS)


def test_framework_root_has_no_wiki_skill_discovery_tree() -> None:
    assert not (ROOT / ".skills").exists()
    assert [relative for relative in DISCOVERY_DIRS if (ROOT / relative).exists()] == []


def test_package_data_is_the_only_runtime_asset_source() -> None:
    assert cli.skills_dir() == DATA / "skills"
    assert cli.bootstrap_dir() == DATA / "bootstrap"
    assert (DATA / "skills/llm-wiki/SKILL.md").is_file()
    assert (DATA / "bootstrap/AGENTS.md").is_file()


def test_framework_bootstraps_are_ordinary_development_pointers() -> None:
    for relative in (
        "CLAUDE.md",
        "GEMINI.md",
        ".hermes.md",
        ".agent/rules/obsidian-wiki.md",
        ".agent/workflows/obsidian-wiki.md",
        ".cursor/rules/obsidian-wiki.mdc",
        ".windsurf/rules/obsidian-wiki.md",
        ".kiro/steering/obsidian-wiki.md",
        ".github/copilot-instructions.md",
    ):
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink(), relative
        text = path.read_text(encoding="utf-8")
        assert "AGENTS.md" in text
        assert "Config Resolution Protocol" not in text
```

- [ ] **Step 2: Run the boundary tests and verify they fail on current layout**

Run: `uv run pytest tests/test_agent_context_boundary.py -q`

Expected: failures report root `.skills`, symlink bootstraps, and missing `_data`.

- [ ] **Step 3: Capture the recognized schema-v1 skill baseline before editing assets**

Implement the tool with required `--source`, `--label`, and `--output` arguments. It
uses `discover_skill_collection(source, ignore_source_artifacts=True)` and writes:

```json
{
  "schema_version": 1,
  "collections": [
    {
      "label": "portable-adapter-baseline-7596215",
      "skills": {
        "llm-wiki": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
      }
    }
  ]
}
```

Run the deterministic generator before moving or editing `.skills/`:

```bash
uv run python tools/capture_legacy_skill_digests.py \
  --source .skills \
  --label portable-adapter-baseline-7596215 \
  --output obsidian_wiki/_data/legacy-skill-digests-v1.json
```

Rerun it and verify `git diff --exit-code -- obsidian_wiki/_data/legacy-skill-digests-v1.json`
returns zero after the first generated file is staged.

- [ ] **Step 4: Relocate tracked runtime assets and remove root discovery mirrors**

Move the canonical skill tree into `obsidian_wiki/_data/skills/`. Move the existing
runtime `AGENTS.md` and agent rule templates into the matching
`obsidian_wiki/_data/bootstrap/` paths used by `BOOTSTRAP_FILES`. Remove all six root
skill mirror trees.

Replace root `AGENTS.md` with development-only guidance containing these sections and
statements:

```markdown
# Obsidian Wiki — Framework Development

This checkout is the source of the `obsidian-wiki` framework. It is not an initialized
wiki repository. Do not resolve a vault or invoke wiki runtime skills unless a test or
the user explicitly asks for an end-to-end wiki operation.

## Product boundaries

- Python code performs deterministic setup, validation, transactions, and repository maintenance.
- Built-in runtime skills live under `obsidian_wiki/_data/skills/` as package resources.
- Runtime bootstrap templates live under `obsidian_wiki/_data/bootstrap/`.
- Project-local agent discovery directories must not contain wiki skills in this source checkout.

## Development commands

- Run focused tests with `uv run pytest tests/test_portable_setup.py -q`.
- Run the full suite with `PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider`.
- Install the CLI from this clone with `uv tool install --force --reinstall --link-mode copy .`.

## Documentation

`README.md` and `README_ZH.md` are one documentation surface. Keep their headings,
examples, links, and behavior aligned. Run `uv run python tools/check_readme_sync.py`.

Human-facing details belong in `docs/`; `README.md` remains a landing page.

## Safety

Preserve owner changes, use repository-relative portable data, reject unsafe links and
special files, and add regression tests before changing behavior.
```

Make every root agent bootstrap an ordinary short pointer to this file; use required
format metadata where applicable, but do not duplicate wiki runtime rules.

- [ ] **Step 5: Make CLI data lookup package-only and simplify Hatch packaging**

Replace fallback lookup with strict package resource lookup:

```python
def _data_dir(name: str) -> Path:
    path = _pkg_dir() / "_data" / name
    if not path.is_dir():
        raise FileNotFoundError(
            f"Could not locate bundled {name}. Reinstall from a clone of "
            "https://github.com/evanzlh/obsidian-wiki with "
            f"`{SOURCE_REINSTALL_COMMAND}`."
        )
    return path


def skills_dir() -> Path:
    return _data_dir("skills")


def bootstrap_dir() -> Path:
    return _data_dir("bootstrap")
```

Remove `_resolve_bootstrap_src()`'s repo-layout fallback and resolve all bootstrap
sources directly under `_data/bootstrap`. Update `obsidian_wiki/__init__.py` to call
the product skill tree a package resource, not root `.skills/` content.

Remove `.skills` and bootstrap force-include mappings from `pyproject.toml`; tracked
files already below the `obsidian_wiki` package are included normally. Retain the
source-root `scripts` and `.env.example` mappings until separately redesigned.

- [ ] **Step 6: Update test asset locators, run focused tests, and commit**

Change framework-source test constants from `ROOT / ".skills"` to
`ROOT / "obsidian_wiki/_data/skills"`. Do not rewrite portable fixture assertions;
portable repositories still use `.skills/`.

Run:

```bash
uv run pytest \
  tests/test_skill_trees.py \
  tests/test_agent_context_boundary.py \
  tests/test_installation_policy.py \
  tests/test_scripts_packaging.py -q
```

Expected: all selected tests pass and `git status --short` shows no root skill
discovery trees.

```bash
git add -A
git commit -m "refactor: isolate framework runtime assets"
```

### Task 3: Remove Automatic Claude Stop Capture

**Files:**
- Delete: `.claude/hooks/wiki-stop-capture.sh`
- Delete: `.claude/settings.json`
- Delete: `tests/test_stop_hook_behavior.py`
- Delete: `tests/test_stop_hook_packaging.py`
- Modify: `pyproject.toml`
- Modify: `obsidian_wiki/_data/skills/wiki-setup/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-capture/SKILL.md`
- Modify: `tests/test_agent_context_boundary.py`
- Modify: `tests/test_scripts_packaging.py`

- [ ] **Step 1: Add negative regression tests before deleting the feature**

```python
def test_automatic_stop_capture_surface_is_absent() -> None:
    assert not (ROOT / ".claude/settings.json").exists()
    assert not (ROOT / ".claude/hooks/wiki-stop-capture.sh").exists()
    assert not (DATA / "hooks/wiki-stop-capture.sh").exists()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    setup = (DATA / "skills/wiki-setup/SKILL.md").read_text(encoding="utf-8")
    capture = (DATA / "skills/wiki-capture/SKILL.md").read_text(encoding="utf-8")
    for text in (pyproject, setup, capture):
        assert "wiki-stop-capture" not in text
        assert "session-end Stop hook" not in text


def test_explicit_quick_capture_remains_documented() -> None:
    capture = (DATA / "skills/wiki-capture/SKILL.md").read_text(encoding="utf-8")
    assert "/wiki-capture --quick" in capture
    assert "Quick mode" in capture
```

- [ ] **Step 2: Run tests and verify the current Hook surface is detected**

Run: `uv run pytest tests/test_agent_context_boundary.py -q -k 'capture or hook'`

Expected: the negative test fails because Hook files and instructions still exist.

- [ ] **Step 3: Delete Hook code and rewrite capture skills for explicit invocation only**

Delete Hook files and their behavior/packaging tests. Remove the wheel/sdist Hook
mapping and related comments. Delete the entire optional automatic-Hook section from
`wiki-setup`. In `wiki-capture`, retain quick mode but describe it only as an explicit
low-friction capture path. Remove automatic-trigger classification advice and all Stop
event wording. Add one short legacy note to installation documentation later; do not
write code that edits `~/.claude/settings.json`.

- [ ] **Step 4: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_agent_context_boundary.py tests/test_scripts_packaging.py -q
rg -n "wiki-stop-capture|session-end Stop hook" \
  pyproject.toml obsidian_wiki tests .claude || test $? -eq 1
```

Expected: tests pass and `rg` finds no product/test reference.

```bash
git add -A
git commit -m "refactor: remove automatic Claude stop capture"
```

### Task 4: Add Schema-v2 Managed Inventory

**Files:**
- Create: `obsidian_wiki/skill_inventory.py`
- Create: `tests/test_skill_inventory.py`
- Modify: `obsidian_wiki/portable.py`

- [ ] **Step 1: Write failing tests for v2 rendering, strict parsing, and legacy parsing**

Test this exact logical payload and sorted canonical JSON:

```python
EXPECTED = {
    "implementation": IMPLEMENTATION_ID,
    "managed_skill_digests": {
        "wiki-ingest": "sha256:" + "1" * 64,
        "wiki-query": "sha256:" + "2" * 64,
    },
    "managed_skills": ["wiki-ingest", "wiki-query"],
    "mirror_format": "full-copy-v1",
    "schema_version": 2,
    "skills_version": "2026.8.3",
}


def test_v2_inventory_round_trip() -> None:
    inventory = ManagedSkillsInventory(
        skills_version="2026.8.3",
        managed_skills=("wiki-ingest", "wiki-query"),
        managed_skill_digests={
            "wiki-ingest": "sha256:" + "1" * 64,
            "wiki-query": "sha256:" + "2" * 64,
        },
    )
    rendered = render_inventory(inventory)
    assert json.loads(rendered) == EXPECTED
    assert parse_inventory_text(rendered) == inventory


def test_legacy_inventory_is_explicitly_typed() -> None:
    legacy = parse_inventory_text(
        json.dumps(
            {
                "implementation": IMPLEMENTATION_ID,
                "skills": ["wiki-ingest"],
                "skills_version": "2026.8.3",
            }
        ),
        allow_legacy=True,
    )
    assert isinstance(legacy, LegacyManagedSkillsInventory)
```

Also reject extra/missing fields, unsorted or duplicate names, digest-key mismatch,
malformed digest values, wrong implementation, wrong mirror format, unsafe names,
and legacy input when `allow_legacy=False`.

- [ ] **Step 2: Run tests and verify the inventory module is missing**

Run: `uv run pytest tests/test_skill_inventory.py -q`

Expected: import failure for `obsidian_wiki.skill_inventory`.

- [ ] **Step 3: Implement strict inventory dataclasses and parser**

Provide:

```python
SCHEMA_VERSION = 2
MIRROR_FORMAT = "full-copy-v1"


@dataclass(frozen=True)
class LegacyManagedSkillsInventory:
    skills_version: str
    managed_skills: tuple[str, ...]


@dataclass(frozen=True)
class ManagedSkillsInventory:
    skills_version: str
    managed_skills: tuple[str, ...]
    managed_skill_digests: dict[str, str]
    schema_version: int = SCHEMA_VERSION
    mirror_format: str = MIRROR_FORMAT


def parse_inventory_text(
    text: str, *, allow_legacy: bool = False
) -> ManagedSkillsInventory | LegacyManagedSkillsInventory:
    """Parse exact known schemas and reject every unknown field or format."""


def read_inventory(
    root: Path, *, allow_legacy: bool = False
) -> ManagedSkillsInventory | LegacyManagedSkillsInventory:
    """Read one contained single-link ordinary inventory file."""


def render_inventory(inventory: ManagedSkillsInventory) -> str:
    """Render sorted UTF-8 JSON with two-space indentation and one final newline."""
```

Keep portable path containment in `portable.py`; pass a validated inventory path or
root into this module without resolving through symlinks. Replace the old loose tuple
return only after setup/upgrade call sites are migrated in later tasks.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_skill_inventory.py -q`

Expected: all inventory tests pass.

```bash
git add obsidian_wiki/skill_inventory.py tests/test_skill_inventory.py
git commit -m "feat: define managed skill inventory v2"
```

### Task 5: Generate Full Mirrors During Setup and Repository Migration

**Files:**
- Modify: `obsidian_wiki/portable.py`
- Modify: `obsidian_wiki/migration.py`
- Modify: `tests/test_portable_setup.py`
- Modify: `tests/test_portable_migration.py`
- Modify: `tests/test_portable_collaboration_e2e.py`

- [ ] **Step 1: Replace adapter expectations with failing complete-mirror tests**

Update `tiny_skills` and all portable skill fixtures to contain valid `name` and
`description` frontmatter. Replace `WIKI_INGEST_ADAPTER` assertions with exact tree
snapshots. Add a nested reference, executable script, binary file, and CJK filename:

```python
def assert_all_agent_mirrors_match(root: Path) -> None:
    canonical = discover_skill_collection(root / ".skills")
    for agent_relative, _label in portable.PROJECT_AGENT_DIRS:
        mirror = discover_skill_collection(root / agent_relative)
        assert mirror == canonical
        assert not any(path.is_symlink() for path in (root / agent_relative).rglob("*"))


def test_setup_writes_complete_mirrors_and_v2_inventory(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    assert_all_agent_mirrors_match(root)
    inventory = read_inventory(root)
    assert isinstance(inventory, ManagedSkillsInventory)
    canonical = discover_skill_collection(root / ".skills")
    assert inventory.managed_skills == canonical.names
    assert inventory.managed_skill_digests == {
        skill.name: skill.digest for skill in canonical.skills
    }
```

Add an E2E assertion that a mirrored `SKILL.md` exposes the original useful
description directly and contains no “Portable adapter” text or relative redirect.

- [ ] **Step 2: Run setup/migration tests and verify adapter behavior fails**

Run:

```bash
uv run pytest tests/test_portable_setup.py tests/test_portable_migration.py \
  tests/test_portable_collaboration_e2e.py -q -k 'skill or mirror or inventory'
```

Expected: failures show one-file adapters and schema-v1 inventory.

- [ ] **Step 3: Replace adapter writing with full collection materialization**

Rename `_adapter_text` to `_legacy_adapter_text` and retain it only for schema-v1
upgrade validation. Replace `write_agent_adapters` with:

```python
def write_agent_skill_mirrors(
    root: Path,
    collection: SkillCollection,
    *,
    agent_dirs: Iterable[tuple[str, str]] = PROJECT_AGENT_DIRS,
) -> None:
    for relative, _label in agent_dirs:
        target = root / relative
        _assert_safe_managed_path(root, target)
        if target.exists() or target.is_symlink():
            raise ValueError(f"portable agent skills path already exists: {target}")
        materialize_skill_collection(collection, target)
```

Initial setup snapshots the bundled source once with source-artifact ignores,
materializes `.skills/`, snapshots the resulting canonical tree without ignores,
materializes all mirrors, verifies equality, and renders inventory v2 using the
canonical digests. Migration candidates use the same sequence.

For an existing schema-v2 repository, `setup --portable` validates canonical and
mirror state but does not silently regenerate drift; its error points to
`obsidian-wiki repo sync-skills --apply`. For an existing schema-v1 repository, setup
does not migrate adapters and points to `repo upgrade-skills`.

- [ ] **Step 4: Update recovery fixtures and run focused tests**

Update setup and migration helpers that hand-build inventory or staged adapter trees.
Do not weaken existing containment, Git-only target, rollback, or owner-bootstrap
tests.

Run:

```bash
uv run pytest tests/test_portable_setup.py tests/test_portable_migration.py \
  tests/test_portable_collaboration_e2e.py -q
```

Expected: all three files pass with complete mirrors and schema-v2 inventory.

- [ ] **Step 5: Commit**

```bash
git add obsidian_wiki/portable.py obsidian_wiki/migration.py \
  tests/test_portable_setup.py tests/test_portable_migration.py \
  tests/test_portable_collaboration_e2e.py
git commit -m "feat: generate complete portable skill mirrors"
```

### Task 6: Validate Canonical Skills, Inventory Ownership, and Mirror Drift

**Files:**
- Modify: `obsidian_wiki/portable_check.py`
- Modify: `tests/test_portable_check.py`
- Modify: `tests/test_doctor.py`

- [ ] **Step 1: Write failing check tests for every drift class**

Cover exact issue codes and severity:

```python
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("change", "skill-mirror-changed"),
        ("delete", "skill-mirror-missing"),
        ("extra", "skill-mirror-extra"),
        ("symlink", "skill-mirror-unsafe"),
        ("hardlink", "skill-mirror-unsafe"),
    ],
)
def test_check_reports_complete_mirror_drift(
    mutation: str, code: str, portable_repo: tuple[Path, PortableConfig]
) -> None:
    root, config = portable_repo
    mutate_claude_mirror(root, mutation)
    report = check_portable_repo(config)
    assert report["status"] == "fail"
    assert code in issue_codes(report)


def test_managed_canonical_edit_is_warning_when_mirrors_match(
    portable_repo: tuple[Path, PortableConfig]
) -> None:
    root, config = portable_repo
    for relative, _label in PROJECT_AGENT_DIRS:
        path = root / relative / "wiki-ingest/SKILL.md"
        path.write_text(path.read_text() + "\nOwner extension.\n", encoding="utf-8")
    canonical = root / ".skills/wiki-ingest/SKILL.md"
    canonical.write_text(canonical.read_text() + "\nOwner extension.\n", encoding="utf-8")
    report = check_portable_repo(config)
    assert report["status"] == "warn"
    assert "managed-canonical-modified" in warning_codes(report)
```

Also test malformed canonical frontmatter, directory-name mismatch, custom canonical
skill missing from one/all mirrors, binary and executable equality, inventory digest
key mismatch, legacy inventory guidance, and CJK resource paths.

- [ ] **Step 2: Run tests and verify current adapter checker fails expectations**

Run: `uv run pytest tests/test_portable_check.py -q -k 'skill or mirror or adapter'`

Expected: failures refer to `managed-adapter-invalid` and do not distinguish drift.

- [ ] **Step 3: Replace adapter checks with collection and ownership validation**

Use `discover_skill_collection()` for canonical and every mirror. Parse inventory v2
strictly. Emit:

- `canonical-skill-invalid` for unsafe trees or invalid frontmatter;
- `managed-skills-invalid` for inventory schema/ownership failures;
- warning `managed-canonical-modified` when a managed canonical digest differs;
- `skill-mirror-missing`, `skill-mirror-extra`, `skill-mirror-changed`, and
  `skill-mirror-unsafe` for derived-tree drift;
- `managed-skills-legacy` for schema-v1 repositories that require upgrade.

Do not compare inventory managed names to the complete canonical name list: unlisted
canonical names are custom skills. Compare every mirror to the complete canonical
collection. Scrub repository absolute paths from messages exactly as other portable
check findings do.

- [ ] **Step 4: Run check and doctor tests and commit**

Run: `uv run pytest tests/test_portable_check.py tests/test_doctor.py -q`

Expected: all tests pass; warnings do not make normal `check` exit non-zero unless the
existing strict-warning option is selected.

```bash
git add obsidian_wiki/portable_check.py tests/test_portable_check.py tests/test_doctor.py
git commit -m "feat: detect portable skill mirror drift"
```

### Task 7: Add a Read-Only Mirror Synchronization Planner

**Files:**
- Modify: `obsidian_wiki/portable.py`
- Modify: `tests/test_portable_setup.py`

- [ ] **Step 1: Write failing deterministic plan tests**

```python
def test_skill_sync_plan_reports_all_agent_changes_without_writing(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    custom = root / ".skills/team-note"
    write_valid_skill(custom.parent, "team-note", "Use for team notes.")
    before = snapshot_tree(root)

    report = plan_portable_skill_sync(root)

    assert report.status == "drift"
    assert all(change.added == ("team-note",) for change in report.targets)
    assert snapshot_tree(root) == before


def test_clean_skill_sync_plan_is_stably_ordered(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)
    report = plan_portable_skill_sync(root)
    assert report.status == "clean"
    assert tuple(target.path for target in report.targets) == tuple(
        relative for relative, _label in PROJECT_AGENT_DIRS
    )
```

Test added, removed, changed, unsafe, and invalid-canonical cases. Compare the complete
pre/post repository snapshot to prove the planner is read-only.

- [ ] **Step 2: Run tests and verify planner imports fail**

Run: `uv run pytest tests/test_portable_setup.py -q -k 'skill_sync_plan'`

Expected: import/name failure for `plan_portable_skill_sync`.

- [ ] **Step 3: Implement immutable report types and planning**

Add:

```python
@dataclass(frozen=True)
class SkillMirrorChange:
    path: str
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unsafe: tuple[str, ...]


@dataclass(frozen=True)
class SkillSyncReport:
    status: Literal["clean", "drift", "applied"]
    canonical_skills: tuple[str, ...]
    targets: tuple[SkillMirrorChange, ...]
    warnings: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "canonical_skills": list(self.canonical_skills),
            "targets": [asdict(target) for target in self.targets],
            "warnings": list(self.warnings),
        }


def plan_portable_skill_sync(root: Path) -> SkillSyncReport:
    """Validate canonical skills and report every mirror difference without writes."""
```

Path lists are repository-relative and sorted. Unsafe entries are reported without
following them. A malformed canonical tree raises before planning mirrors. Managed
canonical digest divergence is a warning copied from inventory comparison, not a
reason to reject explicit mirror synchronization.

- [ ] **Step 4: Run planner tests and commit**

Run: `uv run pytest tests/test_portable_setup.py -q -k 'skill_sync_plan'`

Expected: all planner tests pass.

```bash
git add obsidian_wiki/portable.py tests/test_portable_setup.py
git commit -m "feat: plan portable skill mirror synchronization"
```

### Task 8: Add Recoverable `repo sync-skills`

**Files:**
- Modify: `obsidian_wiki/portable.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_portable_setup.py`

- [ ] **Step 1: Preserve upgrade recovery behavior with characterization tests**

Before refactoring journals, run and extend the existing prepared/committed/recovery
tests. Add a parameterized assertion that an interruption after each target swap is
rolled back or completed on the next operation and never reported clean while only a
subset of agent roots matches.

Run: `uv run pytest tests/test_portable_setup.py -q -k 'upgrade and recover'`

Expected: existing recovery tests pass before refactoring.

- [ ] **Step 2: Write failing CLI and synchronization transaction tests**

Assert the interface and output contract:

```python
def test_repo_sync_skills_json_dry_run_and_apply(tmp_path: Path) -> None:
    root = setup_real_portable_repo(tmp_path)
    add_custom_skill(root, "team-note")

    dry = run_cli(tmp_path / "home", root, "repo", "sync-skills", "--json", "--pretty")
    assert dry.returncode == 1
    assert json.loads(dry.stdout)["status"] == "drift"
    assert not (root / ".claude/skills/team-note").exists()

    applied = run_cli(
        tmp_path / "home", root, "repo", "sync-skills", "--apply", "--json"
    )
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["status"] == "applied"
    assert_all_agent_mirrors_match(root)
```

Also test human output, clean dry-run exit 0, invalid portable context, malformed
canonical JSON error output, owner-only mirror files removed only with `--apply`, no
inventory changes, lock exclusion, rollback failure evidence, and recovery from each
journal status.

- [ ] **Step 3: Generalize replacement journals without weakening authorization**

Refactor the current upgrade journal into internal operation-aware helpers while
preserving existing on-disk schema-3 recovery:

```python
@dataclass(frozen=True)
class ReplacementOperation:
    name: Literal["upgrade", "sync"]
    transactions_relative: str
    inventory_must_be_last: bool


UPGRADE_OPERATION = ReplacementOperation(
    "upgrade", ".obsidian-wiki/local/skill-upgrades", True
)
SYNC_OPERATION = ReplacementOperation(
    "sync", ".obsidian-wiki/local/skill-syncs", False
)
```

New journals use schema 4 and include `operation`. Loading validates that every target
is authorized for that operation: sync may replace exactly the six `skills/` roots;
upgrade may replace managed canonical skill roots, six mirror roots, managed
bootstraps, and inventory last. Keep repository-relative journal paths, single-link
checks, staged snapshots, backups, created-parent rollback, and committed cleanup.
Continue loading schema-3 upgrade journals for interruption compatibility.

- [ ] **Step 4: Implement staged synchronization and recovery**

Add `sync_portable_skill_mirrors(root, apply)`:

```python
def sync_portable_skill_mirrors(root: Path, *, apply: bool) -> SkillSyncReport:
    root = _safe_root(root)
    with _portable_skills_lock(root):
        _recover_skill_operations(root)
        report = plan_portable_skill_sync(root)
        if not apply or report.status == "clean":
            return report
        canonical = discover_skill_collection(root / ".skills")
        transaction = _create_replacement_transaction(root, SYNC_OPERATION)
        replacements = _stage_complete_agent_mirrors(root, transaction, canonical)
        payload, records = _prepare_replacement_journal(
            root, transaction, SYNC_OPERATION, replacements
        )
        _apply_journaled_replacements(root, transaction, payload, records)
        verified = plan_portable_skill_sync(root)
        if verified.status != "clean":
            raise RuntimeError("portable skill synchronization verification failed")
        return replace(verified, status="applied")
```

Setup, sync, upgrade, and CLI `check` recover any pending skill operation before
continuing. Direct `check_portable_repo()` remains deterministic over the supplied
filesystem; the CLI wrapper performs recovery first.

- [ ] **Step 5: Add the CLI parser and output handling**

Register under `repo`:

```python
rss = repo_sub.add_parser(
    "sync-skills",
    help="check or rebuild agent skill mirrors from repository-canonical .skills",
)
rss.add_argument(
    "--apply", action="store_true", help="replace all derived mirrors from .skills"
)
_add_json_args(rss)
rss.set_defaults(func=cmd_repo_sync_skills)
```

Dry-run drift exits 1; clean and successfully applied states exit 0; operational
errors exit 1. JSON stdout contains only the report/error object, with warnings in its
`warnings` list. Human errors go to stderr, and human apply output states that six
derived roots were rebuilt while `.skills/` and inventory were unchanged.

- [ ] **Step 6: Run sync and regression tests, then commit**

Run:

```bash
uv run pytest tests/test_portable_setup.py tests/test_portable_check.py -q \
  -k 'sync_skills or skill_sync or recover or upgrade'
```

Expected: all new synchronization and existing upgrade recovery tests pass.

```bash
git add obsidian_wiki/portable.py obsidian_wiki/cli.py \
  tests/test_portable_setup.py tests/test_portable_check.py
git commit -m "feat: add recoverable portable skill synchronization"
```

### Task 9: Upgrade v2 Repositories and Safely Migrate Legacy Adapters

**Files:**
- Modify: `obsidian_wiki/portable.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_portable_setup.py`
- Modify: `tests/test_agent_context_boundary.py`

- [ ] **Step 1: Write failing v2 ownership and legacy migration tests**

Cover these cases:

- unchanged managed v2 skills upgrade and receive new digests;
- custom canonical skills remain byte-for-byte and appear in every rebuilt mirror;
- a managed canonical digest mismatch refuses before staging;
- mirror drift refuses and points to `repo sync-skills`;
- added/retired managed built-ins update only inventory-owned canonical directories;
- exact schema-v1 adapters and a recognized legacy canonical digest migrate to full
  copies atomically;
- modified/missing/extra/mixed adapters fail closed;
- an unknown legacy canonical digest fails with preservation guidance;
- inventory is the final replacement and recovery works before/after its swap.

Use the captured baseline catalog to create a recognized legacy fixture. One core
assertion is:

```python
def test_legacy_adapter_upgrade_requires_known_canonical_baseline(
    legacy_portable_repo: Path, new_bundled_skills: Path
) -> None:
    canonical = legacy_portable_repo / ".skills/wiki-ingest/SKILL.md"
    canonical.write_text(canonical.read_text() + "\nOwner modification.\n", encoding="utf-8")
    before = snapshot_tree(legacy_portable_repo)

    with pytest.raises(ValueError, match="legacy canonical.*not recognized"):
        upgrade_portable_skills(
            legacy_portable_repo,
            version="2026.8.4",
            source_skills=new_bundled_skills,
        )

    assert snapshot_tree(legacy_portable_repo) == before
```

- [ ] **Step 2: Run upgrade tests and verify current upgrade overwrites/refuses incorrectly**

Run: `uv run pytest tests/test_portable_setup.py -q -k 'upgrade or legacy_adapter'`

Expected: new ownership, digest, and full-copy migration assertions fail.

- [ ] **Step 3: Implement known-legacy baseline validation**

Load `obsidian_wiki/_data/legacy-skill-digests-v1.json` as strict package data. A
schema-v1 repository is trusted only when:

1. its managed names are safe and match one catalog collection's names/digests;
2. every managed adapter is the exact `_legacy_adapter_text()` for its location;
3. every extra per-agent skill is either absent or an exact full copy of a custom
   canonical skill across all agents; and
4. all involved files pass ordinary contained-tree validation.

If no catalog collection matches, fail without staging. The error explains that the
legacy format cannot prove owner edits and asks the owner to preserve/reconcile the
canonical skill rather than using a force flag.

- [ ] **Step 4: Rebuild upgrade staging around full mirrors and v2 inventory**

For schema v2, compare every managed canonical tree to its inventory digest before
staging. Build the prospective canonical collection from new package-managed skills
plus untouched custom canonical trees. Stage replacements for added/changed/removed
managed canonical directories, then stage all six complete mirror roots, bootstrap
updates, and inventory v2 last. Validate staged collection equality and digest mapping
before writing the journal.

Do not replace custom canonical directories. Do not accept mirror drift as upgrade
authority. Rename help/output from “skills and adapters” to “managed skills and full
mirrors.” Return the managed names and emit structured warnings for a recognized
legacy migration.

- [ ] **Step 5: Run complete setup/upgrade/check tests and commit**

Run:

```bash
uv run pytest tests/test_portable_setup.py tests/test_portable_check.py \
  tests/test_agent_context_boundary.py -q
```

Expected: all setup, synchronization, upgrade, migration, containment, and recovery
tests pass.

```bash
git add obsidian_wiki/portable.py obsidian_wiki/cli.py \
  tests/test_portable_setup.py tests/test_portable_check.py \
  tests/test_agent_context_boundary.py
git commit -m "feat: migrate portable adapters to managed full mirrors"
```

### Task 10: Align Portable Repository Migration and End-to-End Collaboration

**Files:**
- Modify: `obsidian_wiki/migration.py`
- Modify: `tests/test_portable_migration.py`
- Modify: `tests/test_portable_collaboration_e2e.py`
- Modify: `tests/test_portable_git.py`

- [ ] **Step 1: Add failing migration and moved-clone acceptance assertions**

Extend migration acceptance to require schema-v2 inventory, full custom-inclusive
mirrors, no adapters, and no absolute paths. Extend collaboration E2E to:

1. initialize a repository;
2. add a CJK-named custom skill with a nested resource;
3. observe dry-run drift;
4. apply synchronization;
5. commit, clone/move the repository;
6. run `check` and a portable transaction validation in the moved clone; and
7. verify no tracked file contains either old or new absolute clone root.

- [ ] **Step 2: Run E2E tests and verify missing behavior**

Run:

```bash
uv run pytest tests/test_portable_migration.py \
  tests/test_portable_collaboration_e2e.py tests/test_portable_git.py -q
```

Expected: failures identify any remaining adapter assumptions or absolute-path leak.

- [ ] **Step 3: Route every repository creation path through the same mirror helpers**

Remove remaining direct adapter calls/imports from `migration.py`. Ensure migration
candidate validation calls `discover_skill_collection()` on canonical and all mirrors
before promotion. Keep source mappings, operation journal, page rewrite, Git metadata,
and rollback behavior unchanged.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
uv run pytest tests/test_portable_migration.py \
  tests/test_portable_collaboration_e2e.py tests/test_portable_git.py -q
```

Expected: all selected tests pass.

```bash
git add obsidian_wiki/migration.py tests/test_portable_migration.py \
  tests/test_portable_collaboration_e2e.py tests/test_portable_git.py
git commit -m "test: verify portable full mirrors across repository moves"
```

### Task 11: Update Runtime and Human Documentation on a Separate Branch

**Files:**
- Modify: `obsidian_wiki/_data/skills/llm-wiki/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-setup/SKILL.md`
- Modify: other bundled skills that refer to adapters, root `.skills`, or Stop capture
- Modify: `docs/installation.md`
- Modify: `docs/agents.md`
- Modify: `docs/skills.md`
- Modify: `docs/cli.md`
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`
- Modify: `docs/contributing.md`
- Modify: `README.md`
- Modify: `README_ZH.md`
- Modify: documentation assertions in `tests/test_installation_policy.py`,
  `tests/test_portable_human_docs.py`, and `tests/test_portable_skill_protocol.py`

- [ ] **Step 1: Create the docs branch after the development branch is green**

Use the worktree skill and create `docs/agent-context-full-skill-mirrors` from the
development branch tip. Confirm `git status --short` is empty before documentation
edits.

- [ ] **Step 2: Write failing documentation contract tests**

Require all relevant documentation to state:

- framework built-ins live in `obsidian_wiki/_data/skills/`;
- portable `.skills/` is the only editable canonical skill tree;
- six agent directories are complete derived ordinary-file mirrors;
- `repo sync-skills` is read-only and `--apply` explicitly rebuilds mirrors;
- `upgrade-skills` upgrades managed built-ins, preserves custom skills, and refuses
  drift/unknown legacy changes;
- automatic Stop capture is absent and quick capture is explicit;
- old global Hook users receive manual removal guidance;
- source installation remains clone + uv only and the installed CLI survives source
  removal.

Also assert no user-facing page calls portable mirrors “adapters,” no framework doc
links to root `../.skills/`, and no Hook installation instructions remain.

- [ ] **Step 3: Run documentation tests and verify stale terminology fails**

Run:

```bash
uv run pytest tests/test_installation_policy.py tests/test_portable_human_docs.py \
  tests/test_portable_skill_protocol.py -q
```

Expected: failures point to adapter/root-skill/Hook wording and missing sync command.

- [ ] **Step 4: Rewrite runtime skill guidance and human documentation**

In runtime skills, describe `.skills/` as canonical only inside a portable wiki and
tell agents never to edit their native mirror directory. Add the explicit workflow:

```bash
obsidian-wiki repo sync-skills --json --pretty
obsidian-wiki repo sync-skills --apply --json --pretty
obsidian-wiki check --json --pretty
git diff -- .skills .claude/skills .cursor/skills .windsurf/skills .agents/skills .pi/skills .kiro/skills
```

In contributing documentation, tell framework contributors to edit
`obsidian_wiki/_data/skills/wiki-ingest/SKILL.md` (or the corresponding named skill),
reinstall with the supported uv command,
and test a disposable portable setup. Explain that the framework source has no local
wiki skills by design.

Update both README languages with matching command order, links, headings, and
behavior. Keep detailed explanation in `docs/`; README remains a compact landing page.
Document manual removal of the legacy global Claude settings entry without adding an
automatic cleanup command.

- [ ] **Step 5: Run documentation tests and parity checker, then commit**

Run:

```bash
uv run pytest tests/test_installation_policy.py tests/test_portable_human_docs.py \
  tests/test_portable_skill_protocol.py tests/test_readme_sync.py -q
uv run python tools/check_readme_sync.py
```

Expected: pytest passes; the advisory checker reports no English-only drift introduced
by these commits.

```bash
git add obsidian_wiki/_data/skills README.md README_ZH.md docs tests
git commit -m "docs: explain canonical skills and full agent mirrors"
```

### Task 12: Verify Distribution Independence and Final Integration

**Files:**
- Modify: `tests/test_installation_policy.py`
- Modify: `tests/test_agent_context_boundary.py`
- Modify: `tests/test_portable_collaboration_e2e.py`
- Modify: `pyproject.toml` only if artifact inspection exposes a packaging omission

- [ ] **Step 1: Add artifact-content and source-removal acceptance tests**

Build wheel and sdist into a temporary directory and assert both contain:

- the complete `obsidian_wiki/_data/skills/**` tree;
- `obsidian_wiki/_data/bootstrap/**`;
- `obsidian_wiki/_data/legacy-skill-digests-v1.json`;
- no root `.skills/**`, per-agent mirror trees, Hook script, or Hook settings.

Extend the existing uv isolation helper to clone/copy the source into a disposable
directory, run the supported command exactly, move the source directory aside, then
run:

```bash
obsidian-wiki list
obsidian-wiki setup --portable "$acceptance_root/wiki-repo"
obsidian-wiki repo sync-skills --json
obsidian-wiki check --json --pretty
```

Assert success, useful original skill descriptions in all six mirrors, and no tracked
absolute source/installation path.

- [ ] **Step 2: Run artifact and install acceptance tests**

Run:

```bash
uv run pytest tests/test_installation_policy.py \
  tests/test_agent_context_boundary.py tests/test_portable_collaboration_e2e.py -q
```

Expected: all artifact and source-removal tests pass.

- [ ] **Step 3: Run the focused integration suite**

Run:

```bash
uv run pytest \
  tests/test_skill_trees.py \
  tests/test_skill_inventory.py \
  tests/test_agent_context_boundary.py \
  tests/test_portable_setup.py \
  tests/test_portable_check.py \
  tests/test_portable_migration.py \
  tests/test_portable_collaboration_e2e.py \
  tests/test_portable_git.py \
  tests/test_doctor.py \
  tests/test_installation_policy.py \
  tests/test_scripts_packaging.py \
  tests/test_portable_human_docs.py \
  tests/test_portable_skill_protocol.py \
  tests/test_readme_sync.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Run full verification and inspect repository boundaries**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider
uv run python tools/check_readme_sync.py
git diff --check
git status --short
find . -maxdepth 3 \( -path './.git' -o -path './.venv' \) -prune -o \
  \( -path './.skills' -o -path './.claude/skills' -o -path './.agents/skills' \
     -o -path './.cursor/skills' -o -path './.windsurf/skills' \
     -o -path './.pi/skills' -o -path './.kiro/skills' \) -print
```

Expected: full pytest passes; README parity has no new drift; diff check is clean;
status contains only intentional final changes before commit; `find` prints no
framework-root skill discovery path.

- [ ] **Step 5: Commit final acceptance changes**

```bash
git add tests pyproject.toml
git commit -m "test: verify packaged skills survive source removal"
```

- [ ] **Step 6: Review final history and hand off for integration**

Run:

```bash
git log --oneline --decorate design/agent-context-skill-mirrors..HEAD
git status --short --branch
```

Expected: development commits precede the documentation branch commit, the worktree
is clean, no Hook feature remains, and every acceptance criterion in the design maps
to a passing test above. Use `superpowers:requesting-code-review` before final merge,
then `superpowers:finishing-a-development-branch` for the user's integration choice.

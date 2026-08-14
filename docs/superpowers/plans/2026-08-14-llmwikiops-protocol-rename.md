# LLMWikiOps Protocol Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every current external repository-protocol identifier with the LLMWikiOps identity, using `.llmwikiops/` as the sole state directory and providing no compatibility or migration behavior.

**Architecture:** Introduce one dependency-free protocol constants module, then migrate discovery/configuration, setup/bootstrap, local state/transactions, packaged resources, and documentation in bounded TDD stages. Former protocol files are ordinary owner content: the implementation never detects, reads, migrates, aliases, or deletes them. The internal `obsidian_wiki` Python package and `Ar9av/obsidian-wiki` attribution remain unchanged.

**Tech Stack:** Python 3.9+, pathlib, argparse, TOML, pytest, Hatch/Hatch-VCS, uv, Markdown package resources, JavaScript browser extension assets, Git.

---

## File and Protocol Map

Create `obsidian_wiki/protocol.py` as the single source of current machine-readable
protocol names. Keep package/distribution identity in `obsidian_wiki/__init__.py`.

Use these exact mappings:

```text
.obsidian-wiki/                         -> .llmwikiops/
.obsidian-wiki/config.toml              -> .llmwikiops/config.toml
.obsidian-wiki/local                    -> .llmwikiops/local
.obsidian-wiki/managed-skills.json      -> .llmwikiops/managed-skills.json
~/.obsidian-wiki/config                 -> ~/.llmwikiops/config
obsidian-wiki.md                        -> llmwikiops.md
obsidian-wiki.mdc                       -> llmwikiops.mdc
obsidian-wiki:managed:*                 -> llmwikiops:managed:*
obsidian-wiki:gitattributes:*           -> llmwikiops:gitattributes:*
obsidian-wiki:portable-bootstrap        -> llmwikiops:portable-bootstrap
obsidian-wiki-raw                       -> llmwikiops-raw
.obsidian-wiki-manifest-mutation        -> .llmwikiops-manifest-mutation
obsidian-wiki manifest capability probe -> llmwikiops manifest capability probe
OBSIDIAN_WIKI_REPO                      -> LLMWIKIOPS_REPO
```

Apply the same prefix change to local locks, skill replacement journals, temporary
directory prefixes, sidecars, and error labels. Do not rename generic Obsidian
vault settings such as `OBSIDIAN_VAULT_PATH` or `OBSIDIAN_CATEGORIES`; those refer
to Obsidian, not the former product protocol.

Retain only:

```text
obsidian_wiki                 # Python import/package resource path
Ar9av/obsidian-wiki           # upstream attribution and fork history
```

Historical specs/plans and explicit negative-test fixtures may describe former
protocol names. Current implementation and generated resources may not.

## Task 1: Centralize protocol identity and cut over discovery

**Files:**

- Create: `obsidian_wiki/protocol.py`
- Create: `tests/test_protocol_identity.py`
- Modify: `obsidian_wiki/config.py`
- Modify: `obsidian_wiki/runtime_context.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `tests/test_portable_config.py`
- Modify: `tests/test_runtime_context.py`
- Modify: `tests/test_info_cli.py`
- Modify: `tests/test_query_cli.py`
- Modify: `tests/test_safe_files.py`

- [ ] **Step 1: Add failing canonical identity tests**

Create `tests/test_protocol_identity.py` with the exact public internal contract:

```python
from obsidian_wiki.protocol import (
    AGENT_RULE_BASENAME,
    CONFIG_RELATIVE,
    CURSOR_RULE_BASENAME,
    GITATTRIBUTES_END,
    GITATTRIBUTES_START,
    GLOBAL_CONFIG_RELATIVE,
    LLMWIKIOPS_REPO_ENV,
    LOCAL_STATE_RELATIVE,
    MANAGED_END,
    MANAGED_INVENTORY_RELATIVE,
    MANAGED_START,
    PORTABLE_BOOTSTRAP_MARKER,
    RAW_PICKER_ID,
    STATE_DIR_NAME,
)


def test_canonical_protocol_identity() -> None:
    assert STATE_DIR_NAME == ".llmwikiops"
    assert CONFIG_RELATIVE == ".llmwikiops/config.toml"
    assert LOCAL_STATE_RELATIVE == ".llmwikiops/local"
    assert MANAGED_INVENTORY_RELATIVE == ".llmwikiops/managed-skills.json"
    assert GLOBAL_CONFIG_RELATIVE == ".llmwikiops/config"
    assert AGENT_RULE_BASENAME == "llmwikiops.md"
    assert CURSOR_RULE_BASENAME == "llmwikiops.mdc"
    assert MANAGED_START == "<!-- llmwikiops:managed:start -->"
    assert MANAGED_END == "<!-- llmwikiops:managed:end -->"
    assert GITATTRIBUTES_START == "# llmwikiops:gitattributes:start"
    assert GITATTRIBUTES_END == "# llmwikiops:gitattributes:end"
    assert PORTABLE_BOOTSTRAP_MARKER == "llmwikiops:portable-bootstrap"
    assert RAW_PICKER_ID == "llmwikiops-raw"
    assert LLMWIKIOPS_REPO_ENV == "LLMWIKIOPS_REPO"
```

Add discovery tests using two sibling fixtures:

```python
def test_only_new_repository_config_is_discovered(tmp_path: Path) -> None:
    old_only = tmp_path / "old-only"
    (old_only / ".obsidian-wiki").mkdir(parents=True)
    (old_only / ".obsidian-wiki/config.toml").write_text("old", encoding="utf-8")
    assert nearest_portable_config(old_only) is None

    configured = tmp_path / "configured"
    (configured / ".llmwikiops").mkdir(parents=True)
    (configured / "nested").mkdir()
    expected = configured / ".llmwikiops/config.toml"
    expected.write_text("new", encoding="utf-8")
    assert nearest_portable_config(configured / "nested") == expected
```

Add a configuration test that supplies `local_state = ".obsidian-wiki/local"` to
`load_portable_config()` and expects `ConfigError` mentioning the canonical
`.llmwikiops/local` value.

- [ ] **Step 2: Run the new tests and record red**

```bash
uv run --with pytest python -m pytest tests/test_protocol_identity.py \
  tests/test_portable_config.py tests/test_runtime_context.py -q
```

Expected: collection fails because `obsidian_wiki.protocol` does not exist, and
existing discovery/config tests still expect the former paths.

- [ ] **Step 3: Create the dependency-free protocol module**

Add exactly these constants to `obsidian_wiki/protocol.py`:

```python
"""Stable machine-readable names for the LLMWikiOps repository protocol."""

STATE_DIR_NAME = ".llmwikiops"
CONFIG_RELATIVE = f"{STATE_DIR_NAME}/config.toml"
LOCAL_STATE_RELATIVE = f"{STATE_DIR_NAME}/local"
MANAGED_INVENTORY_RELATIVE = f"{STATE_DIR_NAME}/managed-skills.json"
GLOBAL_CONFIG_RELATIVE = f"{STATE_DIR_NAME}/config"

AGENT_RULE_BASENAME = "llmwikiops.md"
CURSOR_RULE_BASENAME = "llmwikiops.mdc"
MANAGED_START = "<!-- llmwikiops:managed:start -->"
MANAGED_END = "<!-- llmwikiops:managed:end -->"
GITATTRIBUTES_START = "# llmwikiops:gitattributes:start"
GITATTRIBUTES_END = "# llmwikiops:gitattributes:end"
PORTABLE_BOOTSTRAP_MARKER = "llmwikiops:portable-bootstrap"
RAW_PICKER_ID = "llmwikiops-raw"
LLMWIKIOPS_REPO_ENV = "LLMWIKIOPS_REPO"
TEMP_PREFIX_TOKEN = "llmwikiops"
```

Export them with an explicit `__all__` containing the same names.

- [ ] **Step 4: Cut discovery and configuration over to constants**

In `config.py` and `runtime_context.py`, build candidates with:

```python
from .protocol import CONFIG_RELATIVE, LOCAL_STATE_RELATIVE

candidate = ancestor / CONFIG_RELATIVE
```

After reading `paths.local_state`, enforce the exact canonical value before
constructing `PortableConfig`:

```python
if local_state_raw != LOCAL_STATE_RELATIVE:
    raise ConfigError(
        f"paths.local_state must be exactly {LOCAL_STATE_RELATIVE!r}"
    )
```

In `cli.py`, replace every repository config candidate with `CONFIG_RELATIVE` and
expose only the new repository environment name:

```python
from obsidian_wiki.protocol import CONFIG_RELATIVE, LLMWIKIOPS_REPO_ENV

values = {
    "OBSIDIAN_VAULT_PATH": str(config.vault),
    "OBSIDIAN_SOURCES_DIR": ",".join(str(path) for path in config.sources),
    LLMWIKIOPS_REPO_ENV: str(config.root),
}
```

Do not emit `OBSIDIAN_WIKI_REPO`.

- [ ] **Step 5: Update directly affected fixtures and verify green**

Change fixture configuration paths and canonical local-state values in the Task 1
test files to `.llmwikiops`. Keep one explicit former-path negative fixture in
`tests/test_protocol_identity.py` only.

```bash
uv run --with pytest python -m pytest tests/test_protocol_identity.py \
  tests/test_portable_config.py tests/test_runtime_context.py \
  tests/test_info_cli.py tests/test_query_cli.py tests/test_safe_files.py -q
git diff --check
```

Expected: all selected tests pass; repository discovery never reads the former
directory.

- [ ] **Step 6: Commit the discovery cutover**

```bash
git add obsidian_wiki/protocol.py obsidian_wiki/config.py \
  obsidian_wiki/runtime_context.py obsidian_wiki/cli.py \
  tests/test_protocol_identity.py tests/test_portable_config.py \
  tests/test_runtime_context.py tests/test_info_cli.py tests/test_query_cli.py \
  tests/test_safe_files.py
git commit -m "feat: cut repository discovery over to llmwikiops"
```

## Task 2: Rename setup, markers, inventory, and bootstrap assets

**Files:**

- Modify: `obsidian_wiki/portable.py`
- Modify: `obsidian_wiki/portable_check.py`
- Modify: `obsidian_wiki/skill_inventory.py`
- Rename: `.agent/rules/obsidian-wiki.md` -> `.agent/rules/llmwikiops.md`
- Rename: `.agent/workflows/obsidian-wiki.md` -> `.agent/workflows/llmwikiops.md`
- Rename: `.cursor/rules/obsidian-wiki.mdc` -> `.cursor/rules/llmwikiops.mdc`
- Rename: `.windsurf/rules/obsidian-wiki.md` -> `.windsurf/rules/llmwikiops.md`
- Rename: `.kiro/steering/obsidian-wiki.md` -> `.kiro/steering/llmwikiops.md`
- Rename: `obsidian_wiki/_data/bootstrap/agent/rules/obsidian-wiki.md` -> `obsidian_wiki/_data/bootstrap/agent/rules/llmwikiops.md`
- Rename: `obsidian_wiki/_data/bootstrap/agent/workflows/obsidian-wiki.md` -> `obsidian_wiki/_data/bootstrap/agent/workflows/llmwikiops.md`
- Rename: `obsidian_wiki/_data/bootstrap/cursor/rules/obsidian-wiki.mdc` -> `obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc`
- Rename: `obsidian_wiki/_data/bootstrap/windsurf/rules/obsidian-wiki.md` -> `obsidian_wiki/_data/bootstrap/windsurf/rules/llmwikiops.md`
- Rename: `obsidian_wiki/_data/bootstrap/kiro/steering/obsidian-wiki.md` -> `obsidian_wiki/_data/bootstrap/kiro/steering/llmwikiops.md`
- Modify: `tests/test_portable_setup.py`
- Modify: `tests/test_portable_check.py`
- Modify: `tests/test_skill_inventory.py`
- Modify: `tests/test_portable_skill_protocol.py`
- Modify: `tests/test_agent_context_boundary.py`
- Modify: `tests/test_context_pack_docs.py`
- Modify: `tests/test_asset_artifact_parity.py`
- Modify: `tests/test_installation_policy.py`

- [ ] **Step 1: Write failing fresh-setup and hard-cutover tests**

Add a fresh setup assertion for the exact tree:

```python
expected = {
    ".llmwikiops/config.toml",
    ".llmwikiops/managed-skills.json",
    ".agent/rules/llmwikiops.md",
    ".agent/workflows/llmwikiops.md",
    ".cursor/rules/llmwikiops.mdc",
    ".windsurf/rules/llmwikiops.md",
    ".kiro/steering/llmwikiops.md",
}
for relative in expected:
    assert (root / relative).is_file(), relative
```

Add a fixture containing a former directory and former managed filenames, snapshot
their bytes, run setup, and assert they remain byte-for-byte unchanged while the
new files are created:

```python
former = {
    ".obsidian-wiki/config.toml": b"former config\n",
    ".agent/rules/obsidian-wiki.md": b"former rule\n",
    ".cursor/rules/obsidian-wiki.mdc": b"former cursor rule\n",
}
for relative, content in former.items():
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

run_setup(root)

for relative, content in former.items():
    assert (root / relative).read_bytes() == content
assert (root / ".llmwikiops/config.toml").is_file()
assert (root / ".agent/rules/llmwikiops.md").is_file()
```

Add marker tests requiring only the new `MANAGED_*` and `GITATTRIBUTES_*` values.
Delete tests that require `_LEGACY_BOOTSTRAP_HEADING` or automatic conversion of a
former portable-bootstrap marker; replace them with a test that treats the former
marker as ordinary text and never rewrites it as migration input.

- [ ] **Step 2: Run setup tests and record red**

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_setup.py tests/test_portable_check.py \
  tests/test_skill_inventory.py tests/test_portable_skill_protocol.py -q
```

Expected: failures show the former state directory, marker values, inventory path,
and managed basenames.

- [ ] **Step 3: Move setup constants to the protocol module**

Import the canonical constants in `portable.py`, `portable_check.py`, and
`skill_inventory.py`:

```python
from obsidian_wiki.protocol import (
    AGENT_RULE_BASENAME,
    CONFIG_RELATIVE,
    CURSOR_RULE_BASENAME,
    GITATTRIBUTES_END,
    GITATTRIBUTES_START,
    LOCAL_STATE_RELATIVE,
    MANAGED_END,
    MANAGED_INVENTORY_RELATIVE,
    MANAGED_START,
    PORTABLE_BOOTSTRAP_MARKER,
    STATE_DIR_NAME,
    TEMP_PREFIX_TOKEN,
)
```

Make `skill_inventory.MANAGED_SKILLS_INVENTORY` a compatibility import name for
internal callers only:

```python
MANAGED_SKILLS_INVENTORY = MANAGED_INVENTORY_RELATIVE
```

This alias does not preserve a former value; it preserves only the Python symbol.

Update the root ignore, skill lock, upgrade/sync transaction roots, config renderer,
config validator, temporary prefixes, and check issue paths from the mapping table.
Remove `_LEGACY_BOOTSTRAP_HEADING`, `_legacy_bootstrap_text()`, and the branch that
recognizes the former bootstrap marker.

- [ ] **Step 4: Rename bootstrap files and mappings**

Use Git-aware moves for the ten exact files:

```bash
git mv .agent/rules/obsidian-wiki.md .agent/rules/llmwikiops.md
git mv .agent/workflows/obsidian-wiki.md .agent/workflows/llmwikiops.md
git mv .cursor/rules/obsidian-wiki.mdc .cursor/rules/llmwikiops.mdc
git mv .windsurf/rules/obsidian-wiki.md .windsurf/rules/llmwikiops.md
git mv .kiro/steering/obsidian-wiki.md .kiro/steering/llmwikiops.md
git mv obsidian_wiki/_data/bootstrap/agent/rules/obsidian-wiki.md obsidian_wiki/_data/bootstrap/agent/rules/llmwikiops.md
git mv obsidian_wiki/_data/bootstrap/agent/workflows/obsidian-wiki.md obsidian_wiki/_data/bootstrap/agent/workflows/llmwikiops.md
git mv obsidian_wiki/_data/bootstrap/cursor/rules/obsidian-wiki.mdc obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc
git mv obsidian_wiki/_data/bootstrap/windsurf/rules/obsidian-wiki.md obsidian_wiki/_data/bootstrap/windsurf/rules/llmwikiops.md
git mv obsidian_wiki/_data/bootstrap/kiro/steering/obsidian-wiki.md obsidian_wiki/_data/bootstrap/kiro/steering/llmwikiops.md
```

Update `_BOOTSTRAP_REFERENCES` and `_BOOTSTRAP_ASSET_TARGETS` to use only the new
filenames. Change workflow frontmatter to `name: llmwikiops`. Do not retain former
asset names in package data or parity allowlists.

- [ ] **Step 5: Update asset and rendered-resource assertions**

Change the Task 2 tests to require the renamed files in source, sdist, wheel, and
fresh setup output. Add explicit absence assertions:

```python
assert not (root / ".agent/rules/obsidian-wiki.md").exists()
assert not (root / ".cursor/rules/obsidian-wiki.mdc").exists()
assert "obsidian-wiki:managed:" not in rendered_agents
assert "llmwikiops:managed:" in rendered_agents
```

- [ ] **Step 6: Verify setup and packaged assets**

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_setup.py tests/test_portable_check.py \
  tests/test_skill_inventory.py tests/test_portable_skill_protocol.py \
  tests/test_agent_context_boundary.py tests/test_context_pack_docs.py \
  tests/test_asset_artifact_parity.py tests/test_installation_policy.py -q
git diff --check
```

Expected: all tests pass; source and built resources expose only new managed names.

- [ ] **Step 7: Commit setup and asset changes**

```bash
git add obsidian_wiki .agent .cursor .windsurf .kiro tests
git commit -m "feat: rename the repository protocol to llmwikiops"
```

## Task 3: Move local state, transactions, sidecars, and environment names

**Files:**

- Modify: `obsidian_wiki/transaction.py`
- Modify: `obsidian_wiki/portable_manifest.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `obsidian_wiki/portable.py`
- Modify: `obsidian_wiki/portable_check.py`
- Modify: `tests/test_transaction.py`
- Modify: `tests/test_portable_manifest.py`
- Modify: `tests/test_portable_write_protocol.py`
- Modify: `tests/test_local_state.py`
- Modify: `tests/test_batch.py`
- Modify: `tests/test_cache.py`
- Modify: `tests/test_trust.py`
- Modify: `tests/test_doctor.py`
- Modify: `tests/test_graph_analysis.py`
- Modify: `tests/test_lint.py`
- Modify: `tests/test_context_pack_cli.py`
- Modify: `tests/test_portable_collaboration_e2e.py`
- Modify: `tests/test_portable_git.py`
- Modify: `tests/test_scripts_packaging.py`

- [ ] **Step 1: Add failing machine-identifier tests**

Add exact assertions:

```python
def test_manifest_protocol_names_are_llmwikiops() -> None:
    assert portable_manifest._SIDECAR == ".llmwikiops-manifest-mutation"
    assert portable_manifest._CAPABILITY_MARKER == (
        b"llmwikiops manifest capability probe\n"
    )


def test_runtime_exports_only_new_repository_variable(config: PortableConfig) -> None:
    values = _config_values(config)
    assert values["LLMWIKIOPS_REPO"] == str(config.root)
    assert "OBSIDIAN_WIKI_REPO" not in values
```

If the capability marker is currently inline, first name it
`_CAPABILITY_MARKER` in `portable_manifest.py` so the test and writer share one
value.

Add transaction assertions that the protocol directory excluded from ordinary
vault writes is `.llmwikiops`, and that all locks/journals resolve beneath
`.llmwikiops/local`.

- [ ] **Step 2: Run the local-state suite and record red**

```bash
uv run --with pytest python -m pytest tests/test_portable_manifest.py \
  tests/test_transaction.py tests/test_portable_write_protocol.py \
  tests/test_local_state.py tests/test_batch.py -q
```

Expected: failures identify former sidecars, markers, exclusions, local-state paths,
and environment names.

- [ ] **Step 3: Implement the machine-name cutover**

Use protocol constants rather than new raw strings:

```python
from obsidian_wiki.protocol import (
    LLMWIKIOPS_REPO_ENV,
    LOCAL_STATE_RELATIVE,
    STATE_DIR_NAME,
    TEMP_PREFIX_TOKEN,
)
```

In `portable_manifest.py`:

```python
_SIDECAR = ".llmwikiops-manifest-mutation"
_CAPABILITY_MARKER = b"llmwikiops manifest capability probe\n"
```

In `transaction.py`, replace the excluded protocol component with
`STATE_DIR_NAME`. In setup and transaction helpers, derive all lock, journal,
recovery, and temporary paths from `LOCAL_STATE_RELATIVE` or the loaded canonical
`config.local_state`. Replace filename prefixes with `TEMP_PREFIX_TOKEN`.

Delete every production lookup or emission of `OBSIDIAN_WIKI_REPO`; do not add a
fallback to it.

- [ ] **Step 4: Migrate all direct fixtures in the listed tests**

Use `.llmwikiops/config.toml` and `.llmwikiops/local` for valid fixtures. Keep old
paths only in dedicated hard-cutover negative tests, including:

```python
before = snapshot_tree(root / ".obsidian-wiki")
result = run_repository_command(root)
assert result.returncode != 0
assert "repository not configured" in result.stderr
assert snapshot_tree(root / ".obsidian-wiki") == before
```

Do not make generic fixture helpers create both protocols.

- [ ] **Step 5: Verify transaction and runtime behavior**

```bash
uv run --with pytest python -m pytest \
  tests/test_transaction.py tests/test_portable_manifest.py \
  tests/test_portable_write_protocol.py tests/test_local_state.py \
  tests/test_batch.py tests/test_cache.py tests/test_trust.py \
  tests/test_doctor.py tests/test_graph_analysis.py tests/test_lint.py \
  tests/test_context_pack_cli.py tests/test_portable_collaboration_e2e.py \
  tests/test_portable_git.py tests/test_scripts_packaging.py -q
git diff --check
```

Expected: all selected tests pass and former machine identifiers have no runtime
effect.

- [ ] **Step 6: Commit machine-state changes**

```bash
git add obsidian_wiki tests
git commit -m "feat: move runtime state to the llmwikiops protocol"
```

## Task 4: Migrate human docs, packaged guidance, and extension identity

**Files:**

- Modify: `README.md`, `README_ZH.md`, `AGENTS.md`
- Modify: `docs/agents.md`, `docs/architecture.md`, `docs/cli.md`
- Modify: `docs/cli.zh-TW.md`, `docs/configuration.md`, `docs/contributing.md`
- Modify: `docs/installation.md`, `docs/skills.md`
- Modify: `extensions/brain-capture/popup.js`
- Modify: `obsidian_wiki/_data/bootstrap/AGENTS.md`
- Modify: `obsidian_wiki/_data/bootstrap/github/copilot-instructions.md`
- Modify: `obsidian_wiki/_data/skills/claude-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/codex-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/copilot-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/cross-linker/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/daily-update/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/graph-colorize/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/hermes-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/llm-wiki/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/obsidian-layout-adjustment/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/obsidian-layout-adjustment/evals/evals.json`
- Modify: `obsidian_wiki/_data/skills/obsidian-layout-adjustment/references/workflow-reference.md`
- Modify: `obsidian_wiki/_data/skills/openclaw-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/pi-history-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/tag-taxonomy/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/vault-skill-factory/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-agent/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-capture/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-context-pack/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-dedup/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-digest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-export/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-import/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-ingest/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-lint/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-narrate/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-query/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-rebuild/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-research/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-setup/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-status/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-synthesize/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-transaction-review/SKILL.md`
- Modify: `obsidian_wiki/_data/skills/wiki-update/SKILL.md`
- Modify: `tests/test_portable_human_docs.py`
- Modify: `tests/test_readme_sync.py`
- Modify: `tests/test_portable_skill_protocol.py`
- Modify: `tests/test_context_pack_docs.py`
- Modify: `tests/test_inline_vault_targeting_docs.py`
- Modify: `tests/test_pre_write_snapshot_docs.py`

- [ ] **Step 1: Add failing current-doc and packaged-guidance tests**

Update the current-doc contract to require:

```python
required = (
    ".llmwikiops/config.toml",
    ".llmwikiops/local/",
    "llmwikiops setup",
)
for relative in CURRENT_DOCS:
    text = _text(relative)
    for value in required:
        assert value in combined_current_docs, value
```

Add a semantic former-protocol detector for current docs and packaged resources.
It must reject `.obsidian-wiki`, former managed basenames/markers/IDs, and
`OBSIDIAN_WIKI_*`, while allowing only the exact `Ar9av/obsidian-wiki`
attribution and explicit hard-cutover explanation paragraphs.

Lock the extension ID directly:

```python
assert popup_js.count('id: "llmwikiops-raw",') == 1
assert "obsidian-wiki-raw" not in popup_js
```

- [ ] **Step 2: Run documentation tests and record red**

```bash
uv run --with pytest python -m pytest tests/test_portable_human_docs.py \
  tests/test_readme_sync.py tests/test_portable_skill_protocol.py \
  tests/test_context_pack_docs.py tests/test_inline_vault_targeting_docs.py \
  tests/test_pre_write_snapshot_docs.py -q
```

Expected: failures enumerate former state paths, filenames, variables, and the
extension picker ID.

- [ ] **Step 3: Rewrite the paired landing pages**

Keep README headings, code-fence roles, links, and behavior aligned. Replace all
current repository protocol examples with:

```bash
llmwikiops setup ./team-knowledge
${EDITOR:?} .llmwikiops/config.toml
llmwikiops doctor
llmwikiops check
```

Add the same concise incompatibility note in both languages: the protocol rename
does not migrate former state; users explicitly run setup and review new files.
Do not instruct users to copy local transaction state.

- [ ] **Step 4: Rewrite current docs and packaged resources by exact semantics**

Apply these replacements only to current protocol prose:

```text
.obsidian-wiki/       -> .llmwikiops/
obsidian-wiki.md      -> llmwikiops.md
obsidian-wiki.mdc     -> llmwikiops.mdc
obsidian-wiki-raw     -> llmwikiops-raw
OBSIDIAN_WIKI_REPO    -> LLMWIKIOPS_REPO
```

Retain `Ar9av/obsidian-wiki` attribution and `obsidian_wiki` Python imports.
Historical specs/plans are excluded from mechanical rewriting. In packaged
bootstrap and skill Markdown, update paths, commands, marker explanations, and
environment examples together.

- [ ] **Step 5: Change the extension picker identity**

In `extensions/brain-capture/popup.js`, change only the stable picker ID:

```javascript
const handle = await window.showDirectoryPicker({
  id: "llmwikiops-raw",
  mode: "readwrite",
  startIn: "documents",
});
```

Do not change permission, storage, or capture behavior.

- [ ] **Step 6: Verify docs and runtime prose**

```bash
uv run --with pytest python -m pytest tests/test_portable_human_docs.py \
  tests/test_readme_sync.py tests/test_portable_skill_protocol.py \
  tests/test_context_pack_docs.py tests/test_inline_vault_targeting_docs.py \
  tests/test_pre_write_snapshot_docs.py -q
uv run python tools/check_readme_sync.py
git diff --check
```

Expected: all selected tests pass and the README synchronization checker exits 0.

- [ ] **Step 7: Commit documentation and extension changes**

```bash
git add README.md README_ZH.md AGENTS.md docs extensions \
  obsidian_wiki/_data tests
git commit -m "docs: document the llmwikiops repository protocol"
```

## Task 5: Enforce the hard-cutover audit and artifact contract

**Files:**

- Modify: `tests/test_fork_identity.py`
- Modify: `tests/test_protocol_identity.py`
- Modify: `tests/test_installation_policy.py`
- Modify: `tests/test_asset_artifact_parity.py`
- Modify: no production file is expected in this task; if the audit reports a
  current source file, return it to its owning Task 1-4 scope before editing.

- [ ] **Step 1: Strengthen the tracked-file identity audit**

Make the existing `git ls-files -z` audit reject every former external protocol
identifier in current implementation/config/resources. Scan production/config
surfaces here; human docs and packaged resources remain covered by their Task 4
guards, and explicit negative fixtures remain covered by detector unit tests.

Use this detector and exact attribution check:

```python
FORMER_EXTERNAL_PROTOCOL = re.compile(
    r"(?i)(?:\.obsidian-wiki|"
    r"(?<![A-Za-z0-9_])obsidian-wiki(?![A-Za-z0-9_])|"
    r"OBSIDIAN_WIKI_[A-Z0-9_]+|obsidian\s+wiki)"
)
UPSTREAM_ATTRIBUTION = "https://github.com/Ar9av/obsidian-wiki"


def disallowed_protocol_matches(path: Path, text: str) -> list[str]:
    violations: list[str] = []
    for match in FORMER_EXTERNAL_PROTOCOL.finditer(text):
        start = match.start()
        upstream_start = text.rfind(UPSTREAM_ATTRIBUTION, 0, start + 1)
        inside_upstream = (
            upstream_start >= 0
            and start < upstream_start + len(UPSTREAM_ATTRIBUTION)
        )
        if not inside_upstream:
            line = text.count("\n", 0, start) + 1
            violations.append(f"{path}:{line}: {match.group()}")
    return violations
```

Build the production/config path set from tracked files and exclude these exact
specialized surfaces:

```python
def is_specialized_surface(path: Path) -> bool:
    return (
        path.parts[:1] == ("tests",)
        or path.parts[:1] == ("docs",)
        or path.parts[:3] == ("obsidian_wiki", "_data", "skills")
        or path.parts[:3] == ("obsidian_wiki", "_data", "bootstrap")
    )
```

Add detector unit cases that reject former directories, markers, managed
basenames, IDs, environment variables, arbitrary owner URLs, and mixed-case
variants, while accepting `obsidian_wiki` imports and the canonical upstream URL.

- [ ] **Step 2: Run the audit and classify every failure**

```bash
uv run --with pytest python -m pytest tests/test_fork_identity.py \
  tests/test_protocol_identity.py -q
rg -n --hidden --glob '!.git/**' --glob '!dist/**' \
  --glob '!docs/superpowers/specs/**' --glob '!docs/superpowers/plans/**' \
  '\.obsidian-wiki|obsidian-wiki|OBSIDIAN_WIKI'
```

Expected: `rg` output is limited to `obsidian_wiki` package/import substrings,
exact upstream attribution, and explicit negative-test fixtures. Change every
unclassified current match.

- [ ] **Step 3: Build and inspect exact artifacts**

```bash
artifact_dir=$(mktemp -d /tmp/llmwikiops-protocol-build.XXXXXX)
uv build --out-dir "$artifact_dir"
```

Use the existing structured artifact tests to assert:

```python
assert console_scripts == {
    "llmwikiops": "obsidian_wiki.cli:main",
}
assert "obsidian_wiki/__init__.py" in wheel_names
assert any(
    name.endswith("/obsidian_wiki/__init__.py") for name in sdist_names
)
assert not any("obsidian-wiki.md" in name for name in archive_names)
assert any("llmwikiops.md" in name for name in archive_names)
```

The wheel/sdist distribution names remain `llm_wiki_ops-*`; only repository
protocol resources change.

- [ ] **Step 4: Run the focused final contract**

```bash
uv run --with pytest python -m pytest tests/test_fork_identity.py \
  tests/test_protocol_identity.py tests/test_installation_policy.py \
  tests/test_asset_artifact_parity.py tests/test_portable_human_docs.py \
  tests/test_portable_skill_protocol.py -q
git diff --check
git status --short
```

Expected: tests pass and no generated artifact appears as a tracked or non-ignored
worktree change.

- [ ] **Step 5: Commit audit fixes if the audit changed tracked files**

```bash
git add obsidian_wiki tests README.md README_ZH.md AGENTS.md docs extensions \
  .agent .cursor .windsurf .kiro .github
git commit -m "test: lock the llmwikiops protocol boundary"
```

Skip this commit when the audit produces no tracked change.

## Task 6: Run full verification, recoverability, and publication

**Files:** Git refs, bundle under `/tmp`, and remote `main`; no source edits unless
a demonstrated regression requires a reviewed fix.

- [ ] **Step 1: Run README and complete test verification**

```bash
uv run python tools/check_readme_sync.py
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
```

Expected: README checker exits 0 and pytest reports no failures. Persist the full
pytest summary and exit status under `/tmp` if the tool session may outlive one
terminal yield.

- [ ] **Step 2: Verify source state and build**

```bash
uv build --out-dir "$(mktemp -d /tmp/llmwikiops-protocol-release.XXXXXX)"
git diff --check
git status --short --branch
```

Expected: build exits 0; worktree and index are clean on
`feat/llmwikiops-protocol-rename`.

- [ ] **Step 3: Create a fresh recovery bundle**

```bash
bundle_tmp=$(mktemp /tmp/llmwikiops-protocol-migration.XXXXXX.bundle)
git bundle create "$bundle_tmp" --branches
git bundle verify "$bundle_tmp"
chmod 600 "$bundle_tmp"
mv "$bundle_tmp" /tmp/llmwikiops-before-protocol-publish.bundle
git bundle verify /tmp/llmwikiops-before-protocol-publish.bundle
```

Expected: bundle contains every local branch and records complete history.

- [ ] **Step 4: Reconfirm fast-forward and remote target**

```bash
git merge-base --is-ancestor main feat/llmwikiops-protocol-rename
git rev-list --left-right --count main...feat/llmwikiops-protocol-rename
git remote -v
git ls-remote --heads origin
git ls-remote --tags origin
```

Expected: left-only count is zero; the sole remote is
`git@github.com:evanzlh/llm-wiki-ops.git`; remote has only the existing `main` and
no tags.

- [ ] **Step 5: Fast-forward and push only main**

```bash
verified_tip=$(git rev-parse feat/llmwikiops-protocol-rename)
git switch main
git merge --ff-only feat/llmwikiops-protocol-rename
test "$(git rev-parse HEAD)" = "$verified_tip"
git push origin main
```

Expected: no merge commit; only `main` advances. Do not use `--all`, `--mirror`, or
`--tags`.

- [ ] **Step 6: Verify exact remote refs and clean-clone installation**

```bash
git ls-remote --heads origin
git ls-remote --tags origin
verify_dir=$(mktemp -d /tmp/llmwikiops-protocol-clone.XXXXXX)
git clone --depth 1 --single-branch --branch main \
  https://github.com/evanzlh/llm-wiki-ops.git "$verify_dir/repo"
UV_TOOL_DIR="$verify_dir/tools" UV_TOOL_BIN_DIR="$verify_dir/bin" \
UV_CACHE_DIR="$verify_dir/cache" \
  uv tool install --link-mode copy "$verify_dir/repo"
"$verify_dir/bin/llmwikiops" --version
test ! -e "$verify_dir/bin/obsidian-wiki"
```

Create a temporary repository with the installed CLI and verify fresh setup creates
`.llmwikiops/config.toml`, never creates `.obsidian-wiki`, and emits only renamed
managed assets.

- [ ] **Step 7: Report final evidence**

Report the final SHA, full-suite summary, build artifacts, exact remote refs,
clean-clone installed version, fresh setup tree, and recovery-bundle path/checksum.
Do not create a release tag.

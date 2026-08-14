# Bootstrap Skill Name Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every generated agent bootstrap route runtime skills by skill name instead of the `.skills/<name>/SKILL.md` storage path.

**Architecture:** Keep bootstrap installation and managed-block behavior unchanged. Update only packaged bootstrap content and its existing resource/setup contract tests so each agent resolves `llm-wiki` and task skills through its native skill discovery directory.

**Tech Stack:** Markdown package resources, Python 3, pytest, uv

---

## File Map

- `tests/test_context_pack_docs.py`: contract across all seven bootstrap resources.
- `tests/test_inline_vault_targeting_docs.py`: canonical-before-task routing contract for rule bootstraps.
- `tests/test_portable_setup.py`: installed `AGENTS.md` and bundled bootstrap rendering contract.
- `obsidian_wiki/_data/bootstrap/AGENTS.md`: repository authority template.
- `obsidian_wiki/_data/bootstrap/agent/rules/llmwikiops.md`: Agent rule adapter.
- `obsidian_wiki/_data/bootstrap/agent/workflows/llmwikiops.md`: Agent command registry and workflow instructions.
- `obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc`: Cursor rule adapter.
- `obsidian_wiki/_data/bootstrap/github/copilot-instructions.md`: GitHub Copilot adapter.
- `obsidian_wiki/_data/bootstrap/kiro/steering/llmwikiops.md`: Kiro adapter.
- `obsidian_wiki/_data/bootstrap/windsurf/rules/llmwikiops.md`: Windsurf adapter.

### Task 1: Define The Name-Based Bootstrap Contract

**Files:**
- Modify: `tests/test_context_pack_docs.py`
- Modify: `tests/test_inline_vault_targeting_docs.py`
- Modify: `tests/test_portable_setup.py`

- [ ] **Step 1: Replace resource assertions with the desired contract**

In `test_bootstraps_route_generic_tasks_and_context_pack_is_discoverable`, assert
that every bootstrap has ordered `llm-wiki` and task-skill names and no `.skills/`
path. Add exact registry checks for the workflow file:

```python
for relative in files:
    bootstrap = read(relative)
    canonical = bootstrap.index("`llm-wiki` skill")
    task = bootstrap.index("task skill")
    assert canonical < task, relative
    assert ".skills/" not in bootstrap, relative
    assert "SKILL.md" not in bootstrap, relative

workflow = read(
    "obsidian_wiki/_data/bootstrap/agent/workflows/llmwikiops.md"
)
for name in ("wiki-query", "wiki-update", "wiki-ingest", "wiki-status"):
    assert f"skill: {name}" in workflow
```

In `test_runtime_bootstraps_route_nearest_repository_canonical_then_task`, replace
path indexes with:

```python
canonical = text.index("`llm-wiki` skill")
task = text.index("task skill")
self.assertLess(canonical, task)
self.assertNotIn(".skills/", text)
self.assertNotIn("SKILL.md", text)
```

In `test_fresh_cli_setup_renders_bundled_bootstrap_assets_with_frontmatter` and
`test_root_agents_is_portable_dedicated_and_preserves_team_conventions`, assert
the installed `AGENTS.md` contains ordered `` `llm-wiki` skill `` and `task skill`
references and does not contain `.skills/` or `SKILL.md`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_context_pack_docs.py::test_bootstraps_route_generic_tasks_and_context_pack_is_discoverable \
  tests/test_inline_vault_targeting_docs.py::InlineVaultTargetingDocsTest::test_runtime_bootstraps_route_nearest_repository_canonical_then_task \
  tests/test_portable_setup.py::test_fresh_cli_setup_renders_bundled_bootstrap_assets_with_frontmatter \
  tests/test_portable_setup.py::test_root_agents_is_portable_dedicated_and_preserves_team_conventions -q
```

Expected: FAIL because the current resources contain `.skills/.../SKILL.md` and
the workflow registry uses path values.

- [ ] **Step 3: Commit the failing contract tests**

```bash
git add tests/test_context_pack_docs.py tests/test_inline_vault_targeting_docs.py tests/test_portable_setup.py
git commit -m "test: require agent-native skill routing"
```

### Task 2: Route Every Bootstrap By Skill Name

**Files:**
- Modify: `obsidian_wiki/_data/bootstrap/AGENTS.md`
- Modify: `obsidian_wiki/_data/bootstrap/agent/rules/llmwikiops.md`
- Modify: `obsidian_wiki/_data/bootstrap/agent/workflows/llmwikiops.md`
- Modify: `obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc`
- Modify: `obsidian_wiki/_data/bootstrap/github/copilot-instructions.md`
- Modify: `obsidian_wiki/_data/bootstrap/kiro/steering/llmwikiops.md`
- Modify: `obsidian_wiki/_data/bootstrap/windsurf/rules/llmwikiops.md`

- [ ] **Step 1: Replace path-based prose in all bootstrap resources**

Use this semantic wording, preserving each file's existing line wrapping and
surrounding configuration/config-resolution language:

```markdown
First load the `llm-wiki` skill as the canonical transaction protocol, then load
the applicable task skill. The canonical protocol takes precedence over conflicts.
```

The root `AGENTS.md` may say "canonical protocol" instead of "canonical
transaction protocol" to preserve its current terminology. Remove its statement
that `.skills/` is the canonical skill tree.

- [ ] **Step 2: Replace Agent workflow registry path values**

Use bare skill names:

```yaml
commands:
  - name: wiki-query
    skill: wiki-query
  - name: wiki-update
    skill: wiki-update
  - name: wiki-ingest
    skill: wiki-ingest
  - name: wiki-status
    skill: wiki-status
```

- [ ] **Step 3: Run the focused tests and verify GREEN**

Run the Task 1 focused pytest command again.

Expected: `4 passed`.

- [ ] **Step 4: Run all bootstrap-adjacent tests**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_context_pack_docs.py \
  tests/test_inline_vault_targeting_docs.py \
  tests/test_portable_setup.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the bootstrap implementation**

```bash
git add obsidian_wiki/_data/bootstrap
git commit -m "fix: route bootstrap skills by name"
```

### Task 3: Verify Repository Contracts

**Files:**
- Verify only; no planned file modifications.

- [ ] **Step 1: Check formatting and stale bootstrap paths**

Run:

```bash
git diff --check
rg -n "\.skills/|SKILL\.md" obsidian_wiki/_data/bootstrap
```

Expected: `git diff --check` succeeds and `rg` returns no matches.

- [ ] **Step 2: Run the full suite**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
```

Expected: all tests pass.

- [ ] **Step 3: Check README parity**

```bash
uv run python tools/check_readme_sync.py
```

Expected: command exits successfully.

- [ ] **Step 4: Inspect the final change set**

```bash
git status --short
git log -3 --oneline
```

Expected: only intended commits and no uncommitted implementation changes.

# Source Reinstall Cache Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the documented source upgrade command refresh uv's cached local build so an updated clone actually installs its new CLI and bundled skills.

**Architecture:** Add `--reinstall` to the existing canonical `SOURCE_REINSTALL_COMMAND`, leaving the fresh copy-mode command unchanged. Prove the behavior in the real isolated uv integration test by changing and committing bundled source content between the first install and reinstall, then retain the source-move and portable workflow assertions.

**Tech Stack:** Python 3.9+, pytest, uv tool installation, Git, Markdown, Simplified Chinese translation parity.

**Design:** `docs/superpowers/specs/2026-08-10-source-reinstall-cache-refresh-design.md`

---

## File map

- `obsidian_wiki/__init__.py` — canonical refreshed source-reinstall command.
- `tests/test_installation_policy.py` — command policy plus real cached-build refresh and installed-wheel portability regression.
- `tests/test_portable_setup.py` — existing remediation assertion follows the canonical constant without duplication.
- `README.md`, `README_ZH.md` — translation-aligned upgrade command.
- `docs/installation.md`, `docs/contributing.md` — active user and contributor upgrade commands.

### Task 1: Refresh cached local builds during source reinstall

**Files:**
- Modify: `tests/test_installation_policy.py:57-88,148-280`
- Modify: `obsidian_wiki/__init__.py:9-15`
- Modify: `README.md:24-34`
- Modify: `README_ZH.md:24-34`
- Modify: `docs/installation.md:27-38`
- Modify: `docs/contributing.md:5-14`

- [ ] **Step 1: Write the failing command-policy and real refresh assertions**

Require the canonical upgrade command to include `--reinstall`:

```python
def test_source_reinstall_command_refreshes_cached_builds() -> None:
    assert SOURCE_REINSTALL_COMMAND == (
        "uv tool install --force --reinstall --link-mode copy ."
    )
```

In `test_uv_tool_install_survives_source_move`, after the first install, append
a unique marker to `.skills/wiki-ingest/SKILL.md`, commit only that file in the
temporary copied repository using per-command Git author configuration, run
`SOURCE_REINSTALL_COMMAND`, and assert the installed
`wiki-ingest/SKILL.md` contains the marker before moving the source directory.
Keep the existing source-move, installed-location, single-link, setup, doctor,
Git initialization, check, and isolated-HOME assertions.

- [ ] **Step 2: Run the policy test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_installation_policy.py::test_source_reinstall_command_refreshes_cached_builds -q
```

Expected: FAIL because `SOURCE_REINSTALL_COMMAND` lacks `--reinstall`.

- [ ] **Step 3: Add cache refresh to the canonical reinstall command**

Change the production constant to:

```python
SOURCE_REINSTALL_COMMAND = (
    "uv tool install --force --reinstall --link-mode copy ."
)
```

Do not change `SOURCE_INSTALL_COMMAND`. CLI and portable diagnostics already
consume the reinstall constant.

- [ ] **Step 4: Update active upgrade documentation**

Replace the force-only copy-mode upgrade command in `README.md`,
`README_ZH.md`, `docs/installation.md`, and `docs/contributing.md` with the
canonical force-plus-reinstall copy-mode command. Preserve README structural
and semantic translation parity.

- [ ] **Step 5: Run focused GREEN verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_installation_policy.py::test_source_reinstall_command_refreshes_cached_builds -q
.venv/bin/python -m pytest tests/test_portable_setup.py::test_source_skill_hard_links_are_rejected_before_target_creation -q
```

Then run the isolated refresh/install test with network access:

```bash
.venv/bin/python -m pytest tests/test_installation_policy.py::test_uv_tool_install_survives_source_move -q -p no:cacheprovider
```

Expected: all selected tests pass; the second install exposes the committed
marker from the refreshed build.

- [ ] **Step 6: Run policy, documentation, and stale-command checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_installation_policy.py tests/test_portable_skill_protocol.py tests/test_context_pack_docs.py -q -k 'not test_uv_tool_install_survives_source_move'
.venv/bin/python tools/check_readme_sync.py
rg -n --glob '!docs/superpowers/**' 'uv tool install --force( --reinstall)? --link-mode copy \\.' README.md README_ZH.md AGENTS.md docs .skills obsidian_wiki tools .github tests
git diff --check
```

Expected: local tests pass; README translations are current; every active
upgrade instruction uses `--reinstall`; matches without it are limited to
explicit banned-policy test literals if any.

- [ ] **Step 7: Commit the cache-refresh fix with the installed-wheel test**

```bash
git add obsidian_wiki/__init__.py tests/test_installation_policy.py README.md README_ZH.md docs/installation.md docs/contributing.md
git commit -m "fix: refresh cached source reinstalls"
```

- [ ] **Step 8: Run final distribution and workflow verification**

Run the focused compatibility set, the full pytest suite without repository
cache writes, `uv build`, and then:

```bash
uv tool install --force --reinstall --link-mode copy .
obsidian-wiki --version
```

Expected: all tests and builds pass, and the installed version matches the
current Git source. Re-run the missing-target and `.git`-only `/tmp` portable
setup/doctor/check workflows from the installed executable before handoff.

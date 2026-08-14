# LLMWikiOps Independence and Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the public product to LLMWikiOps, preserve established repository and Python compatibility protocols, promote the independent development line to `main`, and publish only that branch to `git@github.com:evanzlh/llm-wiki-ops.git`.

**Architecture:** Separate public identity, compatibility protocols, and Git publication. Regression tests define the new identity first; current docs and packaged runtime prose then move together; publication happens only after fresh build and full-suite evidence, using a fast-forward and staged remote rename rather than history rewriting or mirror-pushing.

**Tech Stack:** Python 3.9+, argparse, Hatch/Hatch-VCS, uv, pytest, Git, Markdown package resources, GitHub SSH.

---

## Change Boundaries

Public identity changes now:

- `obsidian_wiki/__init__.py`: distribution lookup and implementation identity.
- `obsidian_wiki/cli.py`: program/version labels, guidance, repository URLs.
- `pyproject.toml`, `uv.lock`: distribution, URLs, dual console scripts.
- `README.md`, `README_ZH.md`, `AGENTS.md`, current `docs/*.md`: product prose.
- `obsidian_wiki/_data/bootstrap/AGENTS.md`, `obsidian_wiki/_data/skills/**/*.md`: packaged runtime prose.
- `extensions/brain-capture/popup.js`, `extensions/brain-capture/README.md`: extension guidance.
- Related tests under `tests/`: new identity and rendered-text contracts.

Compatibility surfaces remain:

- `obsidian_wiki/` Python imports and package resources.
- `.obsidian-wiki/` repository protocol paths.
- `obsidian-wiki` console-script alias.
- Managed bootstrap filenames ending in `obsidian-wiki.md` or `obsidian-wiki.mdc`.
- `Ar9av/obsidian-wiki`, fork base, license, historical specs/plans/commits.

## Task 1: Define and implement package identity

**Files:**

- Modify: `tests/test_fork_identity.py`
- Modify: `tests/test_installation_policy.py`
- Modify: `obsidian_wiki/__init__.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Write failing identity tests**

Replace the current identity assertions in `tests/test_fork_identity.py` with:

```python
def test_project_identity_constants_are_stable() -> None:
    assert IMPLEMENTATION_ID == "evanzlh/llm-wiki-ops"
    assert UPSTREAM_URL == "https://github.com/Ar9av/obsidian-wiki"
    assert FORK_BASE_COMMIT == "5ef66b6bec8b26bab6594ac37fb4d8371469fbab"


def test_version_output_identifies_llmwikiops() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.startswith("llmwikiops ")
    assert "evanzlh/llm-wiki-ops" in result.stdout
```

Make the metadata test require:

```python
assert 'name = "llm-wiki-ops"' in text
assert 'Repository = "https://github.com/evanzlh/llm-wiki-ops"' in text
assert 'Issues = "https://github.com/evanzlh/llm-wiki-ops/issues"' in text
assert 'llmwikiops = "obsidian_wiki.cli:main"' in text
assert 'obsidian-wiki = "obsidian_wiki.cli:main"' in text
```

In `tests/test_installation_policy.py`, require both scripts and make the isolated
installation test resolve and run both executables.

- [ ] **Step 2: Verify red**

```bash
uv run --with pytest python -m pytest tests/test_fork_identity.py \
  tests/test_installation_policy.py::test_build_metadata_is_retained_for_uv_source_install -q
```

Expected: failures show the old ID, distribution, repository, version prefix, and
missing primary script.

- [ ] **Step 3: Implement the identity**

Use this contract in `obsidian_wiki/__init__.py`:

```python
"""LLMWikiOps: a deterministic, repository-native LLM Wiki implementation."""

IMPLEMENTATION_ID = "evanzlh/llm-wiki-ops"
UPSTREAM_URL = "https://github.com/Ar9av/obsidian-wiki"
FORK_BASE_COMMIT = "5ef66b6bec8b26bab6594ac37fb4d8371469fbab"
SOURCE_INSTALL_COMMAND = "uv tool install --link-mode copy ."
SOURCE_REINSTALL_COMMAND = "uv tool install --force --reinstall --link-mode copy ."

try:
    __version__ = version("llm-wiki-ops")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
```

Use this CLI label in `obsidian_wiki/cli.py` and change current runtime guidance to
the new URL and command without changing `.obsidian-wiki/` paths:

```python
def version_label() -> str:
    return f"llmwikiops {__version__} ({IMPLEMENTATION_ID})"
```

Set these `pyproject.toml` values:

```toml
[project]
name = "llm-wiki-ops"
description = "A deterministic, repository-native implementation of the LLM Wiki pattern."

[project.urls]
Homepage = "https://github.com/evanzlh/llm-wiki-ops"
Repository = "https://github.com/evanzlh/llm-wiki-ops"
Issues = "https://github.com/evanzlh/llm-wiki-ops/issues"
Upstream = "https://github.com/Ar9av/obsidian-wiki"

[project.scripts]
llmwikiops = "obsidian_wiki.cli:main"
obsidian-wiki = "obsidian_wiki.cli:main"
```

Regenerate metadata with `uv lock`.

- [ ] **Step 4: Verify green and commit**

Run the Step 2 test command, then:

```bash
git add obsidian_wiki/__init__.py obsidian_wiki/cli.py pyproject.toml uv.lock \
  tests/test_fork_identity.py tests/test_installation_policy.py
git commit -m "feat: establish LLMWikiOps identity"
```

## Task 2: Move current human docs to LLMWikiOps

**Files:**

- Modify: `README.md`, `README_ZH.md`, `AGENTS.md`
- Modify: `docs/README.md`, `docs/agents.md`, `docs/architecture.md`, `docs/cli.md`
- Modify: `docs/cli.zh-TW.md`, `docs/configuration.md`, `docs/contributing.md`
- Modify: `docs/fork.md`, `docs/installation.md`, `docs/session-brain.md`, `docs/skills.md`
- Modify: `tests/test_readme_sync.py`, `tests/test_portable_human_docs.py`

- [ ] **Step 1: Write failing doc tests**

Change the aligned command tuple in `tests/test_portable_human_docs.py` to:

```python
commands = (
    "llmwikiops setup ./team-knowledge",
    "cd ./team-knowledge",
    "llmwikiops doctor",
    "llmwikiops check",
    "llmwikiops repo upgrade-skills",
)
```

Add:

```python
def test_current_docs_use_llmwikiops_identity() -> None:
    for relative in CURRENT_DOCS:
        text = _text(relative)
        assert "evanzlh/obsidian-wiki" not in text, relative
        assert re.search(r"(?<![./\w-])obsidian-wiki(?=[\s`])", text) is None, relative
    for relative in ("README.md", "README_ZH.md", "docs/fork.md"):
        text = _text(relative)
        assert "LLMWikiOps" in text, relative
        assert "evanzlh/llm-wiki-ops" in text, relative
```

Change exact current CLI-page requirements from `obsidian-wiki --help` to
`llmwikiops --help`; retain `.obsidian-wiki` assertions.

- [ ] **Step 2: Verify red**

```bash
uv run --with pytest python -m pytest tests/test_portable_human_docs.py \
  tests/test_readme_sync.py -q
```

Expected: failures identify the old title, clone URL, and primary commands.

- [ ] **Step 3: Rewrite paired landing pages**

Use these titles and summaries:

```markdown
# LLMWikiOps

> A deterministic, repository-native implementation of the LLM Wiki pattern.
```

```markdown
# LLMWikiOps

> 一种确定性、仓库原生的 LLM Wiki 实现。
```

Use this identical install block in both:

```bash
git clone https://github.com/evanzlh/llm-wiki-ops.git
cd llm-wiki-ops
uv tool install --link-mode copy .
```

Change current commands to `llmwikiops`, the upgrade branch to
`upgrade-llmwikiops`, and the commit message to `Upgrade LLMWikiOps`. Keep headings,
links, fences, and behavior aligned and retain every `.obsidian-wiki/` path.

- [ ] **Step 4: Rewrite only current docs**

In the exact `docs/*.md` files listed above, use LLMWikiOps, `llmwikiops`, and the
new repository URL. Retain `Ar9av/obsidian-wiki` only for attribution and preserve
`.obsidian-wiki/`. Do not mechanically rewrite older files in
`docs/superpowers/specs/` or `docs/superpowers/plans/`.

- [ ] **Step 5: Verify, commit, and check README history alignment**

```bash
uv run --with pytest python -m pytest tests/test_portable_human_docs.py \
  tests/test_readme_sync.py -q
git add README.md README_ZH.md AGENTS.md docs tests/test_readme_sync.py \
  tests/test_portable_human_docs.py
git commit -m "docs: present the project as LLMWikiOps"
uv run python tools/check_readme_sync.py
```

Expected: pytest passes and the post-commit checker reports the Chinese README is up
to date.

## Task 3: Rename packaged runtime prose

**Files:**

- Modify: `obsidian_wiki/_data/bootstrap/AGENTS.md`
- Modify: Markdown files selected by
  `rg -l '(?<!\.)\bobsidian-wiki\b|evanzlh/obsidian-wiki' --pcre2 obsidian_wiki/_data/skills`
- Modify: `extensions/brain-capture/popup.js`, `extensions/brain-capture/README.md`
- Modify: `tests/test_agent_context_boundary.py`, `tests/test_context_pack_docs.py`
- Modify: `tests/test_doctor.py`, `tests/test_graphrag.py`, `tests/test_info_cli.py`
- Modify: `tests/test_inline_vault_targeting_docs.py`, `tests/test_installation_policy.py`
- Modify: `tests/test_portable_config.py`, `tests/test_portable_only_contract.py`
- Modify: `tests/test_portable_setup.py`, `tests/test_portable_skill_protocol.py`
- Modify: `tests/test_portable_write_protocol.py`, `tests/test_pre_write_snapshot_docs.py`
- Modify: `tests/test_query_cli.py`, `tests/test_runtime_context.py`
- Modify: `tests/test_transaction.py`, `tests/test_transaction_guidance.py`

- [ ] **Step 1: Write failing packaged-guidance test**

Add to `tests/test_portable_skill_protocol.py`:

```python
def test_current_packaged_guidance_uses_llmwikiops_command() -> None:
    roots = (
        ROOT / "obsidian_wiki/_data/skills",
        ROOT / "obsidian_wiki/_data/bootstrap",
    )
    for root in roots:
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            assert re.search(
                r"(?<![./\w-])obsidian-wiki(?=[\s`])", text
            ) is None, path
```

Update rendered-guidance expectations to `llmwikiops`, but preserve expectations for
`.obsidian-wiki`, `obsidian_wiki`, and managed filenames such as
`agent/workflows/obsidian-wiki.md`.

- [ ] **Step 2: Verify red**

```bash
uv run --with pytest python -m pytest tests/test_portable_skill_protocol.py \
  tests/test_installation_policy.py tests/test_context_pack_docs.py \
  tests/test_agent_context_boundary.py -q
```

Expected: failures list old commands in packaged prose.

- [ ] **Step 3: Perform bounded prose replacement**

For packaged skill Markdown, bootstrap `AGENTS.md`, and extension-facing prose only:

```text
https://github.com/evanzlh/obsidian-wiki -> https://github.com/evanzlh/llm-wiki-ops
evanzlh/obsidian-wiki                    -> evanzlh/llm-wiki-ops
obsidian-wiki <command>                  -> llmwikiops <command>
```

Do not rename files or directories. Do not change `.obsidian-wiki`, `obsidian_wiki`,
or `Ar9av/obsidian-wiki`.

- [ ] **Step 4: Run the runtime-text suite**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_context_boundary.py tests/test_context_pack_docs.py \
  tests/test_doctor.py tests/test_graphrag.py tests/test_info_cli.py \
  tests/test_inline_vault_targeting_docs.py tests/test_installation_policy.py \
  tests/test_portable_config.py tests/test_portable_only_contract.py \
  tests/test_portable_setup.py tests/test_portable_skill_protocol.py \
  tests/test_portable_write_protocol.py tests/test_pre_write_snapshot_docs.py \
  tests/test_query_cli.py tests/test_runtime_context.py tests/test_transaction.py \
  tests/test_transaction_guidance.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit packaged runtime prose**

```bash
git add obsidian_wiki/_data extensions tests
git commit -m "docs: migrate runtime guidance to llmwikiops"
```

## Task 4: Audit compatibility names and artifacts

**Files:**

- Modify: `tests/test_fork_identity.py`, `tests/test_installation_policy.py`
- Modify: only incorrectly classified current files found by the audit.

- [ ] **Step 1: Add compatibility assertions**

```python
def test_legacy_cli_alias_and_protocol_names_remain_supported() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'obsidian-wiki = "obsidian_wiki.cli:main"' in pyproject
    assert (ROOT / "obsidian_wiki").is_dir()
    assert '.obsidian-wiki/config.toml' in (
        ROOT / "docs/configuration.md"
    ).read_text(encoding="utf-8")
```

Update artifact discovery from `obsidian_wiki-*` to `llm_wiki_ops-*` while still
requiring members under `obsidian_wiki/`.

- [ ] **Step 2: Verify and classify remaining matches**

```bash
uv run --with pytest python -m pytest tests/test_fork_identity.py \
  tests/test_installation_policy.py -q
rg -n 'obsidian-wiki|obsidian_wiki|\.obsidian-wiki' --glob '!.git/**' \
  --glob '!docs/superpowers/specs/**' --glob '!docs/superpowers/plans/**'
```

Every match must be attribution, Python import/package, repository protocol path,
console alias/test, managed compatibility filename, or a negative identity assertion.
Change every other match.

- [ ] **Step 3: Build and verify artifacts**

```bash
uv build
```

Expected: `llm_wiki_ops-*.whl` and `llm_wiki_ops-*.tar.gz`, both containing the
`obsidian_wiki` package and `_data` resources.

- [ ] **Step 4: Run focused policy tests and commit any fixes**

```bash
uv run --with pytest python -m pytest tests/test_fork_identity.py \
  tests/test_installation_policy.py tests/test_portable_human_docs.py \
  tests/test_portable_skill_protocol.py -q
```

Commit only if the audit produced tracked fixes:

```bash
git add obsidian_wiki pyproject.toml uv.lock README.md README_ZH.md AGENTS.md \
  docs extensions tests
git commit -m "test: lock LLMWikiOps compatibility boundaries"
```

## Task 5: Complete pre-publication verification

**Files:** Modify only files required by a demonstrated regression.

- [ ] **Step 1: Verify README alignment and full suite**

```bash
uv run python tools/check_readme_sync.py
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
```

Expected: README checker exits 0 and pytest reports no failures.

- [ ] **Step 2: Verify clean source state**

```bash
git diff --check
git status --short
```

Expected: no source diff, untracked cache, bytecode, or test artifact. Ignored `dist/`
build output is allowed.

- [ ] **Step 3: Create and verify recovery bundle**

```bash
git bundle create /tmp/llm-wiki-ops-before-remote-migration.bundle --branches
git bundle verify /tmp/llm-wiki-ops-before-remote-migration.bundle
```

Expected: all eight local branches and complete history.

## Task 6: Promote main and publish the independent repository

**Files:** Git refs and remote configuration only.

- [ ] **Step 1: Reconfirm empty destination and ancestry**

```bash
git ls-remote git@github.com:evanzlh/llm-wiki-ops.git
git merge-base --is-ancestor main feat/portable-repo-mode
git rev-list --left-right --count main...feat/portable-repo-mode
```

Expected: empty destination output, ancestry exit 0, and `0` left-only commits.

- [ ] **Step 2: Fast-forward main**

```bash
git switch main
git merge --ff-only feat/portable-repo-mode
```

Expected: no merge commit; `main` equals the verified feature tip.

- [ ] **Step 3: Stage remotes and push only main**

```bash
git remote rename origin old-fork
git remote add origin git@github.com:evanzlh/llm-wiki-ops.git
git remote remove upstream
git remote -v
git push -u origin main
```

Expected: the new GitHub repository gets only `main`.

- [ ] **Step 4: Verify exact remote refs**

```bash
git ls-remote --heads origin
git ls-remote --tags origin
```

Expected: exactly `refs/heads/main` at local `main`; no tags.

- [ ] **Step 5: Verify clean clone and dual commands**

```bash
migration_tmp=$(mktemp -d)
git clone git@github.com:evanzlh/llm-wiki-ops.git "$migration_tmp/repo"
UV_TOOL_DIR="$migration_tmp/tools" UV_TOOL_BIN_DIR="$migration_tmp/bin" \
UV_CACHE_DIR="$migration_tmp/cache" \
  uv tool install --link-mode copy "$migration_tmp/repo"
PATH="$migration_tmp/bin:$PATH" llmwikiops --version
PATH="$migration_tmp/bin:$PATH" obsidian-wiki --version
```

Expected: both commands report `evanzlh/llm-wiki-ops`; output starts with
`llmwikiops`.

- [ ] **Step 6: Remove temporary old remote and report**

```bash
git remote remove old-fork
git remote -v
git status --short --branch
```

Expected: only new `origin`; clean `main` tracking `origin/main`. Report repository
URL, commit, test count, clean-clone result, and bundle path. Do not create `v0.1.0`
until separately approved; leave the old GitHub fork untouched for user archival.

> **Superseded (2026-08-12):** Current behavior is defined by the
> [Portable-Only Repository Design](../specs/2026-08-12-portable-only-design.md).

# Fork Identity and Source-only Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `evanzlh/obsidian-wiki` an explicitly attributed, independently maintained fork with one supported installation path (`git clone` followed by non-editable `uv tool install .`) and English/Simplified-Chinese landing pages.

**Architecture:** Keep the `obsidian-wiki` distribution and console-command names, but add a compiled-in implementation identifier that distinguishes the fork from upstream. Remove publication and alternate installer entry points while retaining wheel build metadata for local `uv` builds. Treat `README.md` and `README_ZH.md` as one landing-page surface and put the detailed fork relationship in `docs/fork.md`.

**Tech Stack:** Python 3.9+, importlib metadata, Hatchling/Hatch-VCS local builds, uv tool installation, Markdown, GitHub Actions advisory checks, pytest

**Approved design:** `design/portable-repo-mode` commit `f7e3183`, `docs/superpowers/specs/2026-08-07-portable-repo-mode-design.md`

---

## File map

- `obsidian_wiki/__init__.py`: expose version plus immutable fork identity constants.
- `obsidian_wiki/cli.py`: display fork identity in version/info output and replace obsolete reinstall guidance.
- `pyproject.toml`: preserve upstream authorship, add fork maintainer and fork/upstream project URLs.
- `README.md`, `README_ZH.md`, `docs/fork.md`: concise bilingual landing pages and the detailed attribution/motivation policy.
- `tools/check_readme_sync.py`, `.github/workflows/readme-sync.yml`, `tests/test_readme_sync.py`: retarget advisory translation drift from Traditional to Simplified Chinese.
- `docs/installation.md`, `docs/cli.md`, `docs/README.md`, `docs/agents.md`, `docs/architecture.md`, `docs/configuration.md`, `docs/contributing.md`, `docs/skills.md`: remove obsolete installation routes and link the fork policy.
- `.skills/wiki-context-pack/SKILL.md`, `.skills/wiki-setup/SKILL.md`, `.skills/vault-skill-factory/SKILL.md`: remove pip, remote-fetch, and `setup.sh` fallback instructions.
- `setup.sh`, `SETUP.md`, `.github/workflows/publish.yml`, `.github/workflows/setup.yml`: delete unsupported entry points.
- `tests/test_fork_identity.py`, `tests/test_installation_policy.py`: pin identity, metadata, documentation, and install independence.

### Task 1: Add a machine-readable fork identity

**Files:**
- Create: `tests/test_fork_identity.py`
- Modify: `obsidian_wiki/__init__.py`
- Modify: `obsidian_wiki/cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing identity and metadata tests**

```python
# tests/test_fork_identity.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from obsidian_wiki import FORK_BASE_COMMIT, IMPLEMENTATION_ID, UPSTREAM_URL


ROOT = Path(__file__).resolve().parents[1]


def test_fork_identity_constants_are_stable() -> None:
    assert IMPLEMENTATION_ID == "evanzlh/obsidian-wiki"
    assert UPSTREAM_URL == "https://github.com/Ar9av/obsidian-wiki"
    assert FORK_BASE_COMMIT == "5ef66b6bec8b26bab6594ac37fb4d8371469fbab"


def test_version_output_identifies_the_fork() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "evanzlh/obsidian-wiki" in result.stdout


def test_package_metadata_preserves_upstream_and_points_users_to_fork() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'authors = [{ name = "Ar9av" }]' in text
    assert 'maintainers = [{ name = "evanzlh" }]' in text
    assert 'Repository = "https://github.com/evanzlh/obsidian-wiki"' in text
    assert 'Issues = "https://github.com/evanzlh/obsidian-wiki/issues"' in text
    assert 'Upstream = "https://github.com/Ar9av/obsidian-wiki"' in text
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run pytest tests/test_fork_identity.py -q`

Expected: FAIL because the identity constants and fork metadata do not exist and `--version` lacks the implementation ID.

- [ ] **Step 3: Add constants and export them**

Replace the export section of `obsidian_wiki/__init__.py` with:

```python
IMPLEMENTATION_ID = "evanzlh/obsidian-wiki"
UPSTREAM_URL = "https://github.com/Ar9av/obsidian-wiki"
FORK_BASE_COMMIT = "5ef66b6bec8b26bab6594ac37fb4d8371469fbab"

__all__ = [
    "__version__",
    "FORK_BASE_COMMIT",
    "IMPLEMENTATION_ID",
    "UPSTREAM_URL",
]
```

Keep the existing `importlib.metadata` version lookup above these constants.

- [ ] **Step 4: Display identity in CLI output and update reinstall guidance**

Import `IMPLEMENTATION_ID` beside `__version__` in `obsidian_wiki/cli.py`. Define one formatter and use it for `--version`, `cmd_info`, doctor details, and stale-install messages:

```python
def version_label() -> str:
    return f"obsidian-wiki {__version__} ({IMPLEMENTATION_ID})"
```

Change the missing-bundled-skills hint to:

```python
raise FileNotFoundError(
    "Could not locate bundled skills. Reinstall from a clone of "
    "https://github.com/evanzlh/obsidian-wiki with `uv tool install --force .`."
)
```

- [ ] **Step 5: Update package metadata without changing attribution or license**

Use this metadata in `pyproject.toml`:

```toml
authors = [{ name = "Ar9av" }]
maintainers = [{ name = "evanzlh" }]

[project.urls]
Homepage = "https://github.com/evanzlh/obsidian-wiki"
Repository = "https://github.com/evanzlh/obsidian-wiki"
Issues = "https://github.com/evanzlh/obsidian-wiki/issues"
Upstream = "https://github.com/Ar9av/obsidian-wiki"
```

Leave `name = "obsidian-wiki"`, `license = "MIT"`, `license-files`, the original author, and the console script unchanged.

- [ ] **Step 6: Run the focused tests**

Run: `uv run pytest tests/test_fork_identity.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the identity layer**

```bash
git add obsidian_wiki/__init__.py obsidian_wiki/cli.py pyproject.toml tests/test_fork_identity.py
git commit -m "chore: identify the independently maintained fork"
```

### Task 2: Remove unsupported installer and publication entry points

**Files:**
- Create: `tests/test_installation_policy.py`
- Delete: `setup.sh`
- Delete: `SETUP.md`
- Delete: `.github/workflows/publish.yml`
- Delete: `.github/workflows/setup.yml`
- Delete: `tests/test_sync_setup_parity.py`
- Modify: `.gitignore`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `obsidian_wiki/cli.py`
- Modify: `obsidian_wiki/sync.py`
- Modify: `obsidian_wiki/ast_extractor.py`
- Modify: `obsidian_wiki/graph_analysis.py`
- Modify: `tests/test_sync.py`
- Modify: `tests/test_context_pack_docs.py`
- Modify: `tests/test_scripts_packaging.py`

- [ ] **Step 1: Write a failing repository-policy test**

```python
# tests/test_installation_policy.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_unsupported_install_entrypoints_are_absent() -> None:
    absent = (
        "setup.sh",
        "SETUP.md",
        ".github/workflows/publish.yml",
        ".github/workflows/setup.yml",
    )
    assert [path for path in absent if (ROOT / path).exists()] == []


def test_build_metadata_is_retained_for_uv_source_install() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'build-backend = "hatchling.build"' in pyproject
    assert 'obsidian-wiki = "obsidian_wiki.cli:main"' in pyproject
    assert '".skills" = "obsidian_wiki/_data/skills"' in pyproject
```

- [ ] **Step 2: Run the policy test and verify failure**

Run: `uv run pytest tests/test_installation_policy.py -q`

Expected: FAIL listing `setup.sh`, `SETUP.md`, and both obsolete workflows.

- [ ] **Step 3: Delete unsupported entry points and their parity test**

Delete the four policy files above and `tests/test_sync_setup_parity.py`. Do not delete `pyproject.toml` build configuration; `uv tool install .` needs the wheel and bundled data.

- [ ] **Step 4: Remove stale setup-script assumptions from code and tests**

Apply these exact semantic replacements:

- `obsidian_wiki/cli.py` module docstring: describe a source-built CLI, not a Python port of `setup.sh` or a pip-installed package.
- `_install_hermes_profiles`: remove the “Mirror setup.sh” wording.
- `_maybe_configure_sync`: describe the single CLI flow; remove shell/curl and pip comparisons.
- `obsidian_wiki/sync.py`: describe `obsidian-wiki setup`, `sync-setup`, and `sync` as the only entry points.
- `obsidian_wiki/ast_extractor.py` and `obsidian_wiki/graph_analysis.py`: describe automatic use of optional libraries when present, but remove `install obsidian-wiki[...]` guidance; the fork documents no alternate installer command.
- `tests/test_sync.py`: describe CLI sync regression coverage without comparing pip and `setup.sh`.
- `tests/test_context_pack_docs.py`: delete `test_setup_sh_installs_context_pack_as_portable`; retain the bootstrap-file coverage.
- `.gitignore`: replace the `setup.sh` recovery comment with “relative adapters are created by `obsidian-wiki setup --portable` or `obsidian-wiki setup --project`.”
- `.env.example`: describe personal CLI sync without comparing installer families or naming the removed script.
- `pyproject.toml` and `tests/test_scripts_packaging.py`: call the wheel a locally built/source-install artifact, not a published wheel or package.
- all doctor/bootstrap reinstall hints: point to a clone of `evanzlh/obsidian-wiki` and `uv tool install --force .`; retain no generic package-index wording.

- [ ] **Step 5: Run the policy and affected tests**

Run: `uv run pytest tests/test_installation_policy.py tests/test_sync.py tests/test_context_pack_docs.py -q`

Expected: PASS.

- [ ] **Step 6: Commit entry-point removal**

```bash
git add -A setup.sh SETUP.md .github/workflows/publish.yml .github/workflows/setup.yml tests/test_sync_setup_parity.py .gitignore .env.example pyproject.toml obsidian_wiki/cli.py obsidian_wiki/sync.py obsidian_wiki/ast_extractor.py obsidian_wiki/graph_analysis.py tests/test_sync.py tests/test_context_pack_docs.py tests/test_scripts_packaging.py tests/test_installation_policy.py
git commit -m "build: keep uv source install as the only installer"
```

### Task 3: Replace the landing pages and document the fork

**Files:**
- Modify: `README.md`
- Create: `README_ZH.md`
- Delete: `README_TW.md`
- Create: `docs/fork.md`
- Modify: `tests/test_installation_policy.py`

- [ ] **Step 1: Add failing fork-documentation assertions**

Append to `tests/test_installation_policy.py`:

```python
def test_bilingual_readmes_disclose_the_fork_and_only_source_install() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README_ZH.md").read_text(encoding="utf-8")
    assert not (ROOT / "README_TW.md").exists()
    for text in (english, chinese):
        assert "Ar9av/obsidian-wiki" in text
        assert "5ef66b6bec8b26bab6594ac37fb4d8371469fbab" in text
        assert "git clone https://github.com/evanzlh/obsidian-wiki.git" in text
        assert "uv tool install ." in text
        assert "docs/fork.md" in text
        assert "pip install obsidian-wiki" not in text
        assert "setup.sh" not in text


def test_fork_policy_is_explicit() -> None:
    policy = (ROOT / "docs/fork.md").read_text(encoding="utf-8")
    assert "independently" in policy
    assert "does not track future upstream changes" in policy
    assert "Portable Repository mode" in policy
```

- [ ] **Step 2: Run the documentation-policy tests and verify failure**

Run: `uv run pytest tests/test_installation_policy.py -q`

Expected: FAIL because `README_ZH.md` and `docs/fork.md` do not exist and `README_TW.md` still exists.

- [ ] **Step 3: Rewrite `README.md` as the English landing page**

Keep the existing project title and reusable images, remove upstream social/PyPI badges, and use these exact sections and claims:

```markdown
# obsidian-wiki

> An independently maintained fork of [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki), based on commit [`5ef66b6bec8b26bab6594ac37fb4d8371469fbab`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab). This is not an official upstream release and does not track future upstream changes. See [Fork relationship and rationale](docs/fork.md).

[English](README.md) | [简体中文](README_ZH.md)

A skill-based framework for compiling source material into an AI-maintained Obsidian knowledge graph.

## Why this fork

This fork focuses on Git-native, multi-contributor knowledge bases: sources and the compiled vault live in one repository, contributors work on branches, and generated changes go through pull-request review.

## Fork features

- Portable Repository mode with repository-relative configuration
- Tracked repository-local skills and multi-agent bootstrap files
- Stable repository-relative Source IDs and sharded manifest state
- Transactional writes, merge-friendly operation logs, and rebuildable hot state
- Deterministic, LLM-free validation for any CI platform

## Install

The only supported installation is a non-editable build from a local clone:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
```

The installed CLI does not depend on the clone remaining in place. To upgrade, pull the clone and run `uv tool install --force .`.

## Start a portable team wiki

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
```

Open `team-knowledge/wiki/` as the Obsidian vault. Contributors clone the knowledge repository, run `obsidian-wiki doctor`, and use the repository-local skills from their preferred agent.

## Personal mode

The existing personal workflow remains available through the source-installed CLI:

```bash
obsidian-wiki setup --vault ~/brain
```

## Documentation

- [Installation](docs/installation.md)
- [Portable configuration](docs/configuration.md)
- [Agent compatibility](docs/agents.md)
- [CLI reference](docs/cli.md)
- [Architecture](docs/architecture.md)
- [Skills](docs/skills.md)
- [Fork relationship and rationale](docs/fork.md)

## Upstream and license

The original work is by Ar9av and contributors. This fork preserves the upstream Git history and MIT license. See [docs/fork.md](docs/fork.md) for attribution and compatibility details.
```

- [ ] **Step 4: Create the structurally equivalent Simplified Chinese landing page**

Create `README_ZH.md` with this complete content. It has the same section order, links, command blocks, and claims as the English page; repository names, commit hashes, paths, and shell commands remain byte-for-byte identical.

````markdown
# obsidian-wiki

> 这是 [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) 的独立维护 Fork，基于提交 [`5ef66b6bec8b26bab6594ac37fb4d8371469fbab`](https://github.com/Ar9av/obsidian-wiki/commit/5ef66b6bec8b26bab6594ac37fb4d8371469fbab)。本项目不是上游官方版本，也不会持续跟踪上游后续变更。详见 [Fork 关系与动机](docs/fork.md)。

[English](README.md) | [简体中文](README_ZH.md)

一个基于 Skills 的框架，用于将来源资料编译为由 AI 维护的 Obsidian 知识图谱。

## 为什么维护这个 Fork

这个 Fork 聚焦于采用 Git 原生工作流的多人知识库：来源资料和编译后的 Vault 位于同一个仓库中；协作者在分支上工作；生成的变更通过 Pull Request 接受审查。

## Fork 新特性

- 采用仓库相对配置的便携式仓库模式
- 仓库内受版本管理的 Skills 与多 Agent 引导文件
- 稳定的仓库相对 Source ID 与分片 Manifest 状态
- 事务化写入、便于合并的操作日志以及可重建的 hot 状态
- 可在任意 CI 平台运行、无需 LLM 的确定性校验

## 安装

唯一受支持的安装方式，是从本地 clone 以非 editable 方式构建安装：

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
```

安装后的 CLI 不依赖 clone 目录继续存在。升级时，在 clone 目录中拉取更新，然后运行 `uv tool install --force .`。

## 创建便携式团队知识库

```bash
obsidian-wiki setup --portable ./team-knowledge
cd ./team-knowledge
obsidian-wiki doctor
```

在 Obsidian 中将 `team-knowledge/wiki/` 作为 Vault 打开。协作者 clone 知识库仓库、运行 `obsidian-wiki doctor`，然后通过自己偏好的 Agent 使用仓库内 Skills。

## 个人模式

通过源码安装的 CLI 仍保留既有的个人工作流：

```bash
obsidian-wiki setup --vault ~/brain
```

## 文档

- [安装](docs/installation.md)
- [便携式配置](docs/configuration.md)
- [Agent 兼容性](docs/agents.md)
- [CLI 参考](docs/cli.md)
- [架构](docs/architecture.md)
- [Skills](docs/skills.md)
- [Fork 关系与动机](docs/fork.md)

## 上游项目与许可证

原始工作由 Ar9av 及其贡献者完成。这个 Fork 保留了上游 Git 历史与 MIT 许可证。归属与兼容性详情见 [docs/fork.md](docs/fork.md)。
````

- [ ] **Step 5: Create the detailed fork policy**

Create `docs/fork.md` with:

```markdown
# Fork Relationship and Rationale

## Attribution

This repository derives from [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) at commit `5ef66b6bec8b26bab6594ac37fb4d8371469fbab`. The upstream author and MIT license remain credited in `pyproject.toml`, Git history, and `LICENSE`.

## Independent evolution

`evanzlh/obsidian-wiki` evolves independently from that baseline and does not track future upstream changes. It is not an official upstream distribution. Similar names and compatible commands describe ancestry, not release equivalence.

## Motivation

The fork targets knowledge bases maintained like software: authoritative sources and the compiled Obsidian vault share one Git repository; any contributor can compile changes on a branch; and humans review the resulting knowledge diff in a pull request.

## Fork-specific capabilities

- Portable Repository mode and repository-relative TOML configuration
- Repository-local canonical skills and agent adapters
- Stable Source IDs with sharded manifest v2
- Transactional page promotion and merge-friendly operation journals
- Stable index/log views and local rebuildable `hot.md`
- Deterministic `obsidian-wiki check` validation without LLM calls

## Compatibility

The fork keeps the `obsidian-wiki` Python distribution and CLI command names. Portable repositories additionally require the implementation identifier `evanzlh/obsidian-wiki`, so an upstream binary with a coincidentally matching version is rejected.

## Installation policy

The only supported installation is `git clone` followed by non-editable `uv tool install .`. The fork is not published to PyPI, does not support remote-URL or skills-registry installation, and does not retain `setup.sh`.
```

- [ ] **Step 6: Delete `README_TW.md` and run the focused tests**

Run: `uv run pytest tests/test_installation_policy.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the fork landing pages**

```bash
git add README.md README_ZH.md README_TW.md docs/fork.md tests/test_installation_policy.py
git commit -m "docs: explain the fork and add simplified Chinese README"
```

### Task 4: Retarget translation parity and remove alternate install guidance

**Files:**
- Modify: `tools/check_readme_sync.py`
- Modify: `tests/test_readme_sync.py`
- Modify: `.github/workflows/readme-sync.yml`
- Modify: `AGENTS.md`
- Modify: `docs/installation.md`
- Modify: `docs/cli.md`
- Modify: `docs/README.md`
- Modify: `docs/agents.md`
- Modify: `docs/architecture.md`
- Modify: `docs/configuration.md`
- Modify: `docs/contributing.md`
- Modify: `docs/skills.md`
- Modify: `.skills/wiki-context-pack/SKILL.md`
- Modify: `.skills/wiki-setup/SKILL.md`
- Modify: `.skills/vault-skill-factory/SKILL.md`
- Modify: `tests/test_context_pack_docs.py`
- Modify: `tests/test_stop_hook_packaging.py`
- Modify: `tests/test_installation_policy.py`

- [ ] **Step 1: Change readme-drift tests to Simplified Chinese**

Replace every `README_TW.md` fixture path in `tests/test_readme_sync.py` with `README_ZH.md`. Keep the four behaviors unchanged: in-sync passes, English-only commits report a diff, a later Chinese commit clears drift, and unrelated commits do not trigger drift.

- [ ] **Step 2: Run the drift tests and verify failure**

Run: `uv run pytest tests/test_readme_sync.py -q`

Expected: FAIL because `tools/check_readme_sync.py` still watches `README_TW.md`.

- [ ] **Step 3: Retarget the checker, workflow, and owner instructions**

Set `TRANSLATION = "README_ZH.md"` in `tools/check_readme_sync.py`, rename local variables such as `last_tw` to `last_translation`, and describe Simplified Chinese in the module docstring. Change the advisory workflow warning target to `README_ZH.md`. Update `AGENTS.md` and `docs/contributing.md` so their parity rule names `README.md` and `README_ZH.md`; keep the job advisory and non-blocking.

- [ ] **Step 4: Replace installation documentation with the single supported flow**

Make `docs/installation.md` contain these sections in order: `Prerequisites` (Git and uv), `Install from a clone`, `Verify`, `Upgrade`, `Create a portable repository`, `Use an existing portable repository`, `Personal mode`. The only install/upgrade commands are:

```bash
git clone https://github.com/evanzlh/obsidian-wiki.git
cd obsidian-wiki
uv tool install .
obsidian-wiki --version

git pull
uv tool install --force .
```

Update `docs/cli.md` and `docs/README.md` to link this flow. In `docs/agents.md`, replace `setup.sh` instructions with the source-installed CLI plus `obsidian-wiki setup` or `obsidian-wiki setup --portable`. In `docs/architecture.md`, remove `setup.sh` from the tree and describe the source-built wheel as the bundled-data carrier. In `docs/configuration.md`, remove pip/setup-script parity wording. In `docs/skills.md`, remove the Skills CLI installation route for this project; do not describe `npx skills add` as an installation method.

- [ ] **Step 5: Remove obsolete fallback routes from skills and their tests**

Use these replacements:

- `.skills/wiki-context-pack/SKILL.md`: replace `pip install obsidian-wiki` with “clone `evanzlh/obsidian-wiki` and run `uv tool install .`”; remove the fallback that treats an arbitrary clone as `OBSIDIAN_WIKI_REPO`.
- `.skills/wiki-setup/SKILL.md`: locate packaged hook assets through the installed CLI data only; remove curl from upstream and `PYTHONPATH` source fallbacks.
- `.skills/vault-skill-factory/SKILL.md`: replace “never run `setup.sh`” with “never run a global `obsidian-wiki setup` without explicit agreement.”
- `tests/test_context_pack_docs.py`: assert the clone-plus-uv guidance and absence of `pip install`.
- `tests/test_stop_hook_packaging.py`: assert the bundled hook path, remove the upstream raw-URL assertion, and describe the hook as part of the locally built wheel rather than a published distribution.

- [ ] **Step 6: Add a repository-wide banned-guidance assertion**

Append to `tests/test_installation_policy.py`:

```python
def test_no_unsupported_install_guidance_remains() -> None:
    checked_roots = (ROOT / "docs", ROOT / ".skills")
    files = [ROOT / "README.md", ROOT / "README_ZH.md", ROOT / "AGENTS.md"]
    for base in checked_roots:
        files.extend(
            path
            for path in base.rglob("*")
            if path.is_file() and "superpowers" not in path.parts
        )
    banned = (
        "pip install obsidian-wiki",
        "pipx install obsidian-wiki",
        "npx skills add Ar9av/obsidian-wiki",
        "bash setup.sh",
        "uv tool install git+",
    )
    offenders = {
        path.relative_to(ROOT).as_posix(): token
        for path in files
        for token in banned
        if token in path.read_text(encoding="utf-8", errors="ignore")
    }
    assert offenders == {}
```

- [ ] **Step 7: Run documentation and policy tests**

Run: `uv run pytest tests/test_readme_sync.py tests/test_installation_policy.py tests/test_context_pack_docs.py tests/test_stop_hook_packaging.py -q`

Expected: PASS.

- [ ] **Step 8: Run a final textual audit**

Run: `rg -n --glob '!docs/superpowers/**' "README_TW|pip install obsidian-wiki|pipx install obsidian-wiki|bash setup\.sh|npx skills add Ar9av/obsidian-wiki|raw\.githubusercontent\.com/Ar9av/obsidian-wiki" README.md README_ZH.md AGENTS.md docs .skills tools .github obsidian_wiki`

Expected: no output except deliberate historical text in `docs/fork.md` that names the removed channels without presenting commands.

Run: `rg -n "published wheel|published package|pip-installed|pip/uv installs|setup\.sh|install .?obsidian-wiki\[" pyproject.toml .env.example obsidian_wiki tests/test_scripts_packaging.py tests/test_stop_hook_packaging.py tests/test_sync.py`

Expected: no output.

- [ ] **Step 9: Commit the documentation-policy migration**

```bash
git add tools/check_readme_sync.py tests/test_readme_sync.py .github/workflows/readme-sync.yml AGENTS.md docs .skills tests/test_context_pack_docs.py tests/test_stop_hook_packaging.py tests/test_installation_policy.py
git commit -m "docs: enforce source-only installation and Chinese parity"
```

### Task 5: Prove the installed CLI is independent of the clone

**Files:**
- Modify: `tests/test_installation_policy.py`

- [ ] **Step 1: Add the isolated uv installation smoke test**

Append:

```python
import os
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required by the supported installer")
def test_uv_tool_install_survives_source_move(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(".venv", "dist", "build", "__pycache__"),
        symlinks=True,
    )
    env = os.environ.copy()
    env.update(
        HOME=str(tmp_path / "home"),
        UV_TOOL_DIR=str(tmp_path / "tools"),
        UV_TOOL_BIN_DIR=str(tmp_path / "bin"),
        UV_CACHE_DIR=str(tmp_path / "cache"),
    )
    subprocess.run(
        ["uv", "tool", "install", "."],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    source.rename(tmp_path / "source-moved")
    result = subprocess.run(
        [str(tmp_path / "bin" / "obsidian-wiki"), "--version"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "evanzlh/obsidian-wiki" in result.stdout
    bundled = subprocess.run(
        [str(tmp_path / "bin" / "obsidian-wiki"), "list"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "wiki-ingest" in bundled.stdout.splitlines()
```

- [ ] **Step 2: Run the isolated smoke test**

Run: `uv run pytest tests/test_installation_policy.py::test_uv_tool_install_survives_source_move -q`

Expected: PASS; the executable still starts after the copied source tree is renamed.

- [ ] **Step 3: Run the complete first-plan verification**

Run: `uv run pytest tests/test_fork_identity.py tests/test_installation_policy.py tests/test_readme_sync.py tests/test_context_pack_docs.py tests/test_stop_hook_packaging.py tests/test_sync.py -q`

Expected: PASS.

Run: `uv build`

Expected: wheel and source distribution build successfully from the fork source.

- [ ] **Step 4: Commit install-independence coverage**

```bash
git add tests/test_installation_policy.py
git commit -m "test: verify uv tool install is clone independent"
```

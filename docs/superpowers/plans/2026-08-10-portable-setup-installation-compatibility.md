# Portable Setup Installation Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the documented uv source installation compatible with strict canonical-skill validation and let Portable setup safely initialize a target whose only existing entry is an ordinary `.git` directory.

**Architecture:** Keep the hard-link security invariant and centralize the two supported copy-mode uv commands in `obsidian_wiki.__init__`. Extend `setup_portable_repo` with a narrowly classified `.git`-only path that stages the complete repository first, preserves `.git` in place, and rolls back moved top-level entries on handled failure. Keep arbitrary non-empty target rejection unchanged and prove the complete installed-CLI workflow in an isolated uv tool environment.

**Tech Stack:** Python 3.9+, pathlib/os/stat/shutil, pytest, uv tool installation, Git, Markdown, Simplified Chinese translation parity.

**Design:** `docs/superpowers/specs/2026-08-10-portable-setup-installation-compatibility-design.md`

---

## File map

- `obsidian_wiki/__init__.py` — canonical fresh-install and reinstall command constants.
- `obsidian_wiki/portable.py` — hard-link remediation text, `.git`-only target classification, staged top-level commit and rollback.
- `obsidian_wiki/cli.py` — all installed-CLI reinstall hints use the canonical command constant.
- `tests/test_portable_setup.py` — source hard-link diagnostic and `.git`-only success/refusal/rollback behavior.
- `tests/test_installation_policy.py` — active installation command policy and installed-wheel setup/doctor integration.
- `tests/test_portable_skill_protocol.py` — human Portable documentation contract.
- `tests/test_context_pack_docs.py` — bundled context skill installation guidance.
- `README.md`, `README_ZH.md` — translation-aligned landing-page install and upgrade commands.
- `docs/installation.md` — canonical installation commands and supported setup target states.
- `docs/cli.md`, `docs/agents.md`, `docs/architecture.md`, `docs/contributing.md`, `docs/fork.md` — active user/developer references to the one installation flow.
- `.skills/wiki-context-pack/SKILL.md`, `.skills/wiki-setup/SKILL.md` — agent-facing installation and setup rules bundled into the wheel.

### Task 1: Make incompatible installed skill bundles self-diagnosing

**Files:**
- Modify: `tests/test_portable_setup.py:13-28,2546-2567`
- Modify: `obsidian_wiki/__init__.py:7-17`
- Modify: `obsidian_wiki/portable.py:22-24,278-299`
- Modify: `obsidian_wiki/cli.py:24-25,57-63,89-97`

- [ ] **Step 1: Write the failing hard-link remediation assertion**

Import the canonical command and strengthen the existing source-hard-link test:

```python
from obsidian_wiki import IMPLEMENTATION_ID, SOURCE_REINSTALL_COMMAND, cli, portable


@pytest.mark.parametrize("hard_link_location", ["nested", "skill-file"])
def test_source_skill_hard_links_are_rejected_before_target_creation(
    hard_link_location: str, tmp_path: Path
) -> None:
    source = make_skill_source(tmp_path)
    external = tmp_path / "external-source"
    external.write_text("external source bytes\n", encoding="utf-8")
    external_bytes = external.read_bytes()
    if hard_link_location == "nested":
        os.link(external, source / "wiki-ingest/external.txt")
    else:
        skill_file = source / "wiki-ingest/SKILL.md"
        skill_file.unlink()
        os.link(external, skill_file)
    target = tmp_path / "repo"

    with pytest.raises(ValueError, match="hard link|multiple links") as exc_info:
        setup_portable_repo(target, version="2026.8.3", source_skills=source)

    assert SOURCE_REINSTALL_COMMAND in str(exc_info.value)
    assert external.read_bytes() == external_bytes
    assert not target.exists()
    assert not any(
        path.name.startswith(".repo.obsidian-wiki-") for path in tmp_path.iterdir()
    )
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
uv run pytest tests/test_portable_setup.py::test_source_skill_hard_links_are_rejected_before_target_creation -q
```

Expected: collection or assertion failure because `SOURCE_REINSTALL_COMMAND` does not exist or the error lacks the copy-mode command.

- [ ] **Step 3: Add canonical commands and use the reinstall command in diagnostics**

Add these public constants in `obsidian_wiki/__init__.py` and export them:

```python
SOURCE_INSTALL_COMMAND = "uv tool install --link-mode copy ."
SOURCE_REINSTALL_COMMAND = "uv tool install --force --link-mode copy ."

__all__ = [
    "__version__",
    "FORK_BASE_COMMIT",
    "IMPLEMENTATION_ID",
    "SOURCE_INSTALL_COMMAND",
    "SOURCE_REINSTALL_COMMAND",
    "UPSTREAM_URL",
]
```

Import `SOURCE_REINSTALL_COMMAND` in `portable.py` and make the hard-link branch actionable without relaxing it:

```python
from obsidian_wiki import IMPLEMENTATION_ID, SOURCE_REINSTALL_COMMAND

# inside _source_entry_kind
if metadata.st_nlink > 1:
    raise ValueError(
        "canonical skill source regular file has multiple links (hard link): "
        f"{path}; reinstall the CLI from its framework clone with "
        f"`{SOURCE_REINSTALL_COMMAND}`"
    )
```

Import the same constant in `cli.py` and replace both hard-coded reinstall commands:

```python
from obsidian_wiki import IMPLEMENTATION_ID, SOURCE_REINSTALL_COMMAND, __version__

SOURCE_REINSTALL_HINT = (
    "clone https://github.com/evanzlh/obsidian-wiki, then run "
    f"`{SOURCE_REINSTALL_COMMAND}` from the clone"
)

# skills_dir FileNotFoundError body
raise FileNotFoundError(
    "Could not locate bundled skills. Reinstall from a clone of "
    "https://github.com/evanzlh/obsidian-wiki with "
    f"`{SOURCE_REINSTALL_COMMAND}`."
)
```

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run:

```bash
uv run pytest tests/test_portable_setup.py::test_source_skill_hard_links_are_rejected_before_target_creation -q
```

Expected: `2 passed`.

- [ ] **Step 5: Run adjacent CLI and setup tests**

Run:

```bash
uv run pytest tests/test_portable_setup.py tests/test_doctor.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the diagnostic fix**

```bash
git add obsidian_wiki/__init__.py obsidian_wiki/portable.py obsidian_wiki/cli.py tests/test_portable_setup.py
git commit -m "fix: diagnose incompatible uv skill bundles"
```

### Task 2: Initialize targets containing only ordinary Git metadata

**Files:**
- Modify: `tests/test_portable_setup.py:857-870,2379-2402`
- Modify: `obsidian_wiki/portable.py:2334-2407`

- [ ] **Step 1: Write all failing `.git`-only behavior tests**

Add these tests near the existing non-empty target test:

```python
def test_setup_scaffolds_git_only_target_without_mutating_git_metadata(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    git_dir = root / ".git"
    objects = git_dir / "objects"
    objects.mkdir(parents=True)
    config = git_dir / "config"
    config.write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    before = snapshot_tree(git_dir)
    git_inode = git_dir.stat().st_ino
    config_inode = config.stat().st_ino
    git_mode = stat.S_IMODE(git_dir.stat().st_mode)
    config_mode = stat.S_IMODE(config.stat().st_mode)

    result = setup_portable_repo(
        root, version="2026.8.3", source_skills=tiny_skills
    )

    assert result == root.resolve()
    assert (root / ".obsidian-wiki/config.toml").is_file()
    assert (root / "wiki/index.md").is_file()
    assert snapshot_tree(git_dir) == before
    assert git_dir.stat().st_ino == git_inode
    assert config.stat().st_ino == config_inode
    assert stat.S_IMODE(git_dir.stat().st_mode) == git_mode
    assert stat.S_IMODE(config.stat().st_mode) == config_mode


def test_setup_rejects_git_plus_owner_content_without_changes(
    tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "README.md").write_text("owner repository\n", encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="not a portable|missing|empty"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


@pytest.mark.parametrize("git_kind", ["file", "symlink"])
def test_setup_rejects_non_directory_git_only_target(
    git_kind: str, tmp_path: Path, tiny_skills: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git_entry = root / ".git"
    if git_kind == "file":
        git_entry.write_text("gitdir: elsewhere\n", encoding="utf-8")
    else:
        external = tmp_path / "external-git"
        external.mkdir()
        git_entry.symlink_to(external, target_is_directory=True)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match=r"\.git.*ordinary directory"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before


def test_git_only_target_merge_failure_restores_exact_original(
    tmp_path: Path, tiny_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    git_dir = root / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text("owner git metadata\n", encoding="utf-8")
    before = snapshot_tree(root)
    original_replace = Path.replace
    committed = 0

    def fail_third_top_level_move(source: Path, target: Path) -> Path:
        nonlocal committed
        target_path = Path(target)
        if target_path.parent == root and source.parent != root:
            committed += 1
            if committed == 3:
                raise OSError("simulated staged merge failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_third_top_level_move)

    with pytest.raises(OSError, match="simulated staged merge failure"):
        setup_portable_repo(root, version="2026.8.3", source_skills=tiny_skills)

    assert snapshot_tree(root) == before
    assert not any(
        path.name.startswith(".repo.obsidian-wiki-") for path in tmp_path.iterdir()
    )


def test_setup_cli_scaffolds_git_only_target_and_validators_pass(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)

    setup = run_cli(home, tmp_path, "setup", "--portable", str(root))
    doctor = run_cli(home, root, "doctor")
    check = run_cli(home, root, "check")

    assert setup.returncode == 0, setup.stderr
    assert "Portable repository scaffolded" in setup.stdout
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "obsidian-wiki doctor: pass" in doctor.stdout
    assert check.returncode == 0, check.stdout + check.stderr
    assert "portable check: pass" in check.stdout
    assert (root / ".git").is_dir()
    assert not (home / ".obsidian-wiki").exists()
```

- [ ] **Step 2: Run the new target tests and confirm RED**

Run:

```bash
uv run pytest tests/test_portable_setup.py -q -k 'git_only or git_plus_owner'
```

Expected: success, rollback, and CLI cases fail because `.git`-only setup is not implemented; non-directory cases fail with the generic message instead of the required boundary.

- [ ] **Step 3: Add narrow target classification and staged merge helpers**

Add these helpers immediately before `setup_portable_repo`:

```python
def _target_contains_only_git_metadata(root: Path) -> bool:
    entries = tuple(root.iterdir())
    if len(entries) != 1 or entries[0].name != ".git":
        return False
    git_entry = entries[0]
    metadata = git_entry.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(
            "portable repository .git-only target requires .git to be an "
            f"ordinary directory: {git_entry}"
        )
    return True


def _commit_staged_repo_into_git_root(staging: Path, root: Path) -> None:
    if not _target_contains_only_git_metadata(root):
        raise ValueError(
            f"portable repository target changed while setup was staged: {root}"
        )
    moved: list[Path] = []
    try:
        for entry in sorted(staging.iterdir(), key=lambda path: path.name):
            target = root / entry.name
            if target.exists() or target.is_symlink():
                raise ValueError(f"portable setup target collision: {target}")
            entry.replace(target)
            moved.append(target)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for target in reversed(moved):
            try:
                target.replace(staging / target.name)
            except OSError as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        if rollback_errors:
            raise OSError(
                "portable .git-only setup rollback is incomplete: "
                + "; ".join(rollback_errors)
            ) from exc
        raise
```

Classify the target before existing-portable repair and use the helper after the staged tree passes preflight:

```python
target_existed = root.is_dir()
target_is_empty = target_existed and not any(root.iterdir())
target_is_git_only = (
    target_existed
    and not target_is_empty
    and _target_contains_only_git_metadata(root)
)
if target_existed and not target_is_empty and not target_is_git_only:
    config_path = root / ".obsidian-wiki/config.toml"
    if not config_path.exists() and not config_path.is_symlink():
        raise ValueError(
            "existing target is not a portable repository; use a missing or "
            "empty directory, or a directory containing only an ordinary "
            f".git directory: {root}"
        )
    _assert_ordinary_file(root, config_path, "configuration")
    _assert_single_link_ordinary_file(root, config_path, "configuration")
    _load_canonical_portable_config(root, version=version)
    with _portable_skills_lock(root):
        _recover_upgrade_transactions(
            root,
            version=version,
            source=source,
            current_names=skill_names,
        )
        inventory_path = root / MANAGED_SKILLS_INVENTORY
        if inventory_path.exists() or inventory_path.is_symlink():
            _inventory_version, managed_names = _read_managed_skills_inventory(root)
            _preflight_existing_portable(
                root, version=version, skill_names=managed_names
            )
            _repair_existing_portable_repo(
                root,
                source_skills=source,
                skill_names=managed_names,
            )
        else:
            _preflight_existing_portable(
                root, version=version, skill_names=skill_names
            )
            _validate_pre_inventory_migration(root, source, skill_names)
            _write_managed_skills_inventory(
                root, version=version, skill_names=skill_names
            )
    return root

# after staging preflight
if target_is_git_only:
    _commit_staged_repo_into_git_root(staging, root)
    staging.rmdir()
else:
    if target_is_empty:
        root.rmdir()
        removed_empty_target = True
    staging.replace(root)
```

Change the generic refusal text to list accepted states without suggesting that arbitrary non-empty repositories are adopted:

```python
raise ValueError(
    "existing target is not a portable repository; use a missing or empty "
    f"directory, or a directory containing only an ordinary .git directory: {root}"
)
```

- [ ] **Step 4: Run the new target tests and confirm GREEN**

Run:

```bash
uv run pytest tests/test_portable_setup.py -q -k 'git_only or git_plus_owner'
```

Expected: all selected tests pass, including rollback, doctor, and check.

- [ ] **Step 5: Run the complete portable setup module**

Run:

```bash
uv run pytest tests/test_portable_setup.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the `.git`-only implementation**

```bash
git add obsidian_wiki/portable.py tests/test_portable_setup.py
git commit -m "fix: scaffold portable repos around git metadata"
```

### Task 3: Align the supported installation and setup documentation

**Files:**
- Modify: `tests/test_installation_policy.py:57-68,78-85,102-145`
- Modify: `tests/test_portable_skill_protocol.py:75-95`
- Modify: `tests/test_context_pack_docs.py:20-28`
- Modify: `README.md:24-40`
- Modify: `README_ZH.md:24-40`
- Modify: `docs/installation.md:1-53,122-150`
- Modify: `docs/cli.md:24-47`
- Modify: `docs/agents.md:45-55`
- Modify: `docs/architecture.md:65-75,338-350`
- Modify: `docs/contributing.md:5-14`
- Modify: `docs/fork.md:25-36`
- Modify: `.skills/wiki-context-pack/SKILL.md:42-58`
- Modify: `.skills/wiki-setup/SKILL.md:20-45,375-382`

- [ ] **Step 1: Write failing policy assertions for the copy-mode contract**

Import the production command constants in `tests/test_installation_policy.py`:

```python
from obsidian_wiki import SOURCE_INSTALL_COMMAND, SOURCE_REINSTALL_COMMAND
```

Replace bare-command assertions with these constants, and add both old commands to the banned tuples used for active docs and user-facing tooling:

```python
for text in (english, chinese):
    assert SOURCE_INSTALL_COMMAND in text
    assert SOURCE_REINSTALL_COMMAND in text

banned = (
    "pip install obsidian-wiki",
    "pipx install obsidian-wiki",
    "npx skills add Ar9av/obsidian-wiki",
    "bash setup.sh",
    "uv tool install git+",
    "uv tool install .",
    "uv tool install --force .",
)
```

Update `test_contributor_skill_flow_rebuilds_installed_cli_before_setup`, `test_human_docs_cover_the_portable_repository_contract`, and `test_context_skill_canonicalizes_vault_and_requires_installed_cli` to require the copy-mode commands. Add a human-doc assertion for the `.git`-only boundary:

```python
assert "only an ordinary `.git` directory" in combined
assert "does not run `git init`" in combined
```

- [ ] **Step 2: Run documentation policy tests and confirm RED**

Run:

```bash
uv run pytest tests/test_installation_policy.py tests/test_portable_skill_protocol.py tests/test_context_pack_docs.py -q
```

Expected: failures list active files that still contain the old commands and missing `.git`-only setup language.

- [ ] **Step 3: Update active English documentation and agent instructions**

Use `uv tool install --link-mode copy .` for fresh installs and `uv tool install --force --link-mode copy .` for upgrades everywhere outside historical `docs/superpowers/**` documents.

Add this target-state guidance to `docs/installation.md` and a compact equivalent to `docs/cli.md`:

```markdown
Portable setup accepts a target that does not exist, an empty directory, or a
directory whose only entry is an ordinary `.git` directory. The `.git` metadata
is preserved byte-for-byte. Any other non-portable content is rejected without
mutation; use explicit migration for a legacy knowledge repository.

Portable setup does not run `git init`, create a commit, or configure a remote.
The simplest new-repository sequence is setup first, then `git init`. A freshly
initialized repository containing only `.git` is also accepted.
```

Add the same operational rule to the Portable Repository section of `.skills/wiki-setup/SKILL.md`. Keep the README limited to the command change.

- [ ] **Step 4: Update Simplified Chinese README in parity**

Change the two commands in `README_ZH.md` in the same positions as `README.md`:

```markdown
uv tool install --link-mode copy .
```

and:

```markdown
uv tool install --force --link-mode copy .
```

Retain the existing explanation that the installed CLI is independent of the clone.

- [ ] **Step 5: Run policy and translation checks and confirm GREEN**

Run:

```bash
uv run pytest tests/test_installation_policy.py tests/test_portable_skill_protocol.py tests/test_context_pack_docs.py -q
python tools/check_readme_sync.py
```

Expected: pytest passes; the advisory README checker reports no pending English-only translation drift introduced by this change.

- [ ] **Step 6: Scan active surfaces for stale bare commands**

Run:

```bash
rg -n --glob '!docs/superpowers/**' 'uv tool install( --force)? \.' README.md README_ZH.md AGENTS.md docs .skills obsidian_wiki tools .github tests
```

Expected: no active user instruction or assertion uses either bare command. Matches inside banned-token tests are acceptable and must be reviewed explicitly.

- [ ] **Step 7: Commit the aligned documentation**

```bash
git add README.md README_ZH.md docs/installation.md docs/cli.md docs/agents.md docs/architecture.md docs/contributing.md docs/fork.md .skills/wiki-context-pack/SKILL.md .skills/wiki-setup/SKILL.md tests/test_installation_policy.py tests/test_portable_skill_protocol.py tests/test_context_pack_docs.py
git commit -m "docs: require copy-mode source installation"
```

### Task 4: Prove installed CLI independence and run full regression

**Files:**
- Modify: `tests/test_installation_policy.py:148-212`

- [ ] **Step 1: Extend the isolated uv integration test**

Change its install command and add bundled-link, setup, and doctor assertions after the source directory is renamed:

```python
subprocess.run(
    ["uv", "tool", "install", "--link-mode", "copy", "."],
    cwd=source,
    env=env,
    text=True,
    capture_output=True,
    check=True,
    timeout=180,
)

# after resolving skills_path
multiply_linked = [
    path
    for path in skills_path.rglob("*")
    if path.is_file() and not path.is_symlink() and path.stat().st_nlink != 1
]
assert multiply_linked == []

portable = tmp_path / "portable"
setup = subprocess.run(
    [executable, "setup", "--portable", str(portable)],
    cwd=tmp_path,
    env=env,
    text=True,
    capture_output=True,
    check=True,
    timeout=60,
)
assert "Portable repository scaffolded" in setup.stdout
doctor = subprocess.run(
    [executable, "doctor"],
    cwd=portable,
    env=env,
    text=True,
    capture_output=True,
    check=True,
    timeout=60,
)
assert "obsidian-wiki doctor: pass" in doctor.stdout
isolated_home = Path(env["HOME"])
assert not (isolated_home / ".obsidian-wiki").exists()
assert not any(
    (isolated_home / relative).exists()
    for relative in (".claude/skills", ".codex/skills", ".agents/skills")
), "portable setup must not install global skills"
```

- [ ] **Step 2: Run the isolated install test**

Run:

```bash
uv run pytest tests/test_installation_policy.py::test_uv_tool_install_survives_source_move -q
```

Expected: `1 passed`; the installed CLI runs setup and doctor after the source clone has moved.

- [ ] **Step 3: Run all focused compatibility tests**

Run:

```bash
uv run pytest tests/test_portable_setup.py tests/test_installation_policy.py tests/test_portable_skill_protocol.py tests/test_context_pack_docs.py tests/test_doctor.py tests/test_portable_check.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Run the full test suite without repository-local bytecode/cache writes**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider
```

Expected: zero failures.

- [ ] **Step 5: Build the distributable artifacts**

Run:

```bash
uv build
```

Expected: wheel and source distribution build successfully in `dist/`.

- [ ] **Step 6: Reinstall from the finished source and reproduce both user workflows**

Run from the framework clone:

```bash
uv tool install --force --link-mode copy .
```

Then use two fresh temporary targets outside the source tree: one missing and one initialized with `git init`:

```bash
framework_root=$(git rev-parse --show-toplevel)
probe_root=$(mktemp -d /tmp/obsidian-wiki-portable-probe.XXXXXX)
missing_target="$probe_root/missing-target"
git_target="$probe_root/git-target"
git init "$git_target"
git_inode_before=$(stat -c '%i' "$git_target/.git")
obsidian-wiki setup --portable "$missing_target"
cd "$missing_target"
obsidian-wiki doctor
obsidian-wiki check
cd "$framework_root"
obsidian-wiki setup --portable "$git_target"
cd "$git_target"
obsidian-wiki doctor
obsidian-wiki check
git_inode_after=$(stat -c '%i' "$git_target/.git")
test "$git_inode_before" = "$git_inode_after"
test ! -e "$missing_target/.venv"
test ! -e "$git_target/.venv"
if rg -nF "$framework_root" --glob '!.git/**' "$missing_target" "$git_target"; then
    exit 1
fi
```

Expect every setup, doctor, check, inode, `.venv`, and absolute-path assertion to succeed.

- [ ] **Step 7: Verify diffs, generated artifacts, and repository status**

Run:

```bash
git diff --check
git status --short --branch
git log --oneline -6
```

Expected: only intentional source/test/doc commits are present; `dist/` remains ignored; no temporary setup repository or cache is inside the framework checkout.

- [ ] **Step 8: Commit the integration regression test**

```bash
git add tests/test_installation_policy.py
git commit -m "test: exercise portable setup from installed wheel"
```

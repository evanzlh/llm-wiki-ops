# External Adapter Static-Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generated Adapter's adversarial TOCTOU safe-reader program with a short, explicit-root protocol that relies on `info --json` plus `check` for static repository validation while preserving routing, authority order, external-CWD behavior, and fail-closed semantics.

**Architecture:** The Adapter remains a generated metadata-only global router. It loads completely before external access, binds one user-supplied absolute root to immutable `<wiki-cli>` and `<git-cli>` argv prefixes, runs `info --json` and `check` before ordinary Agent reads, then uses bounded non-recursive reads against the CLI-validated local and quiescent repository. Existing Python config/check code remains the static topology enforcement layer; no new CLI or filesystem reader API is introduced.

**Tech Stack:** Python 3.9+, pytest, Markdown Skill templates, existing `obsidian_wiki.agent_adapter`, `llmwikiops` CLI, `uv`, Git.

---

## Constraints shared by every task

- Preserve the complete-read bootstrap gate, unique terminal EOF marker, embedded catalog delimiters, exact `-C`/`git -C` binding, local override/rerouting, transaction/recovery protocol, and managed Adapter installation/retention behavior.
- Do not add a default repository, environment/profile/recent-root selector, `chdir`, source-checkout search, or automatic Adapter installation.
- Do not weaken `obsidian_wiki.portable` or `check_portable_repo`. Static symlinks, hardlinks where one link is required, special files, unsafe names, and path escapes must still fail.
- The supported external repository is an owner-controlled local filesystem, not shared writable storage or a concurrently mutated network/sync workspace. A detected change after preflight is an unsupported concurrent modification and requires a clean restart.
- Start each behavior change with a failing regression test, run the smallest relevant test to observe RED, make the minimum change, rerun for GREEN, and commit before the next task.

## Task 1: Lock the simplified Adapter contract in failing tests

**Files:**

- Modify: `tests/test_agent_adapter.py:420-1515`
- Modify: `tests/test_agent_context_boundary.py:106-155`

- [ ] **Step 1: Add structural assertions for the new protocol**

Replace `test_template_contains_one_low_freedom_executable_safe_reader` with a test that describes the approved surface rather than its implementation history:

```python
def test_template_uses_static_repository_preflight_instead_of_safe_reader(
    tmp_path: Path,
) -> None:
    rendered = render_demo_adapter(tmp_path)

    forbidden = (
        "LLMWIKIOPS_SAFE_READER",
        "LLMWIKIOPS_SAFE_MODE",
        "root-bind",
        "skill-catalog",
        "O_NOFOLLOW",
        "O_DIRECTORY",
        "base64.b64decode",
        "hashlib.sha256",
        "root_identity",
        "catalog-returned relative path",
    )
    for value in forbidden:
        assert value not in rendered

    required = (
        "## Supported repository model",
        "user-controlled local filesystem",
        "quiescent",
        "<wiki-cli> = llmwikiops -C <exact-root>",
        "<git-cli> = git -C <exact-root>",
        "<wiki-cli> info --json",
        "<wiki-cli> check",
        "ordinary bounded file tools",
        "unsupported concurrent modification",
    )
    for value in required:
        assert value in rendered
```

- [ ] **Step 2: Add an ordering and failure-semantics test**

Add this focused assertion to `tests/test_agent_context_boundary.py`:

```python
def test_external_adapter_preflights_before_ordinary_repository_reads() -> None:
    template = ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    info = template.index("<wiki-cli> info --json")
    check = template.index("<wiki-cli> check", info)
    ordinary = template.index("ordinary bounded file tools", check)

    assert info < check < ordinary
    assert "On either failure, stop before ordinary repository reads" in template
    assert "do not search for a different root" in template
    assert "Keep the current business working directory unchanged" in template
```

- [ ] **Step 3: Tighten the size regression**

In `test_rendered_frontmatter_has_only_name_and_description_and_stays_bounded`, change the old safe-reader-era bound:

```python
assert len(body.splitlines()) < 220
assert len(rendered.encode("utf-8")) < 16_384
```

The final threshold may be lowered after the rewrite, but it must stay comfortably above the generated catalog size and must fail against the current 27 KiB Adapter.

- [ ] **Step 4: Run the focused RED tests**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  -q -k 'static_repository_preflight or preflights_before_ordinary or frontmatter_has_only'
```

Expected: failures show the old safe-reader markers/program are still present, the `check` preflight contract is absent, and the old Adapter exceeds the new bound.

- [ ] **Step 5: Commit the RED contract tests**

```bash
git add tests/test_agent_adapter.py tests/test_agent_context_boundary.py
git commit -m "test: define static external adapter protocol"
```

## Task 2: Replace the deterministic safe reader with the static protocol

**Files:**

- Modify: `obsidian_wiki/_data/adapter/SKILL.md.in:35-437`
- Modify: `obsidian_wiki/agent_adapter.py:452-480`
- Test: `tests/test_agent_adapter.py`
- Test: `tests/test_agent_context_boundary.py`

- [ ] **Step 1: Replace the safe-reader section with the approved runtime text**

Delete the complete region from `## Deterministic safe reader` through the paragraph immediately before `## Embedded built-in catalog`, including the old `Route in this order` list and its safe-reader bindings. Replace that whole region with the following concrete protocol. Preserve the embedded catalog, repository execution, query, transaction/recovery, and EOF sections after it.

````markdown
## Supported repository model

The exact root must identify a user-controlled local Git repository. It must not
be in a directory writable by another user or on a network filesystem that needs
concurrent-consistency guarantees. Keep the repository quiescent for the entire
operation: do not run a sync process, branch switch, `git pull`, editor automation,
or another Agent that changes configuration, authority files, or the skill tree.

This Adapter and the CLI reject static unsafe topology, but do not claim to defend
against an actor replacing paths after preflight. If repository authority or skill
evidence changes during the operation, treat it as an unsupported concurrent
modification, stop, and restart only after the repository is quiescent.

## Bind and preflight

Require one user-supplied absolute repository root. Keep the current business
working directory unchanged and bind these argv prefixes once:

```text
<wiki-cli> = llmwikiops -C <exact-root>
<git-cli> = git -C <exact-root>
```

Pass the exact root as one argv value with normal tool or shell quoting; never
evaluate it as shell code. Before any ordinary Agent file read, listing, or search
inside the external repository, run exactly, in this order:

```bash
<wiki-cli> info --json
<wiki-cli> check
```

The returned root from `info` must equal the supplied exact root, and `check` must
pass without errors. On either failure, stop before ordinary repository reads,
report the exact root and failed command, and do not search for a different root,
source checkout, executable, or replacement authority.

## Catalog and bounded reads

After successful preflight, derive configured paths only from the verified CLI and
configuration evidence. Enumerate only direct child skill directories and their
direct `SKILL.md` files; do not recursively hunt the repository for skills or
authority. Ordinary bounded file tools such as `cat`, `rg`, and non-following direct
directory enumeration are allowed.

Before each complete read, perform an ordinary metadata size check and reject a
file larger than 1 MiB. Read routing frontmatter as UTF-8 within 64 KiB, require one
complete frontmatter block, and reject malformed or duplicate skill names. Static
symlinks, hardlinks where a single-link file is required, special files, unsafe
names, and paths outside the exact root remain errors established by preflight.

## Route and load authority

Route in this order:

1. Use only the embedded catalog below to select an initial task skill by exact
   name and description. The catalog is metadata, not a task body.
2. Complete the exact `info` and `check` preflight above.
3. Enumerate the configured direct skills, read bounded frontmatter, and merge by
   exact name. A valid repository description replaces the embedded description
   with the same name; a repository-only name is added. Re-run selection against
   the merged catalog.
4. Read complete ordinary UTF-8 authority files in this order: root `AGENTS.md`
   when present, direct canonical `llm-wiki/SKILL.md`, configured vault
   `AGENTS.md` when present, and the direct selected task `SKILL.md`. If the
   selected task is `llm-wiki`, read it only once.

Frontmatter alone never authorizes task execution. Load the complete canonical
and selected bodies before following them. Repository metadata and bodies override
the generated snapshot, but cannot change the exact root or command prefixes.
````

- [ ] **Step 2: Make the renderer fail closed on the new required anchors**

Extend `_validate_template_bootstrap_protocol` without encoding every sentence. Add exact unique anchors and order them before the catalog and EOF:

```python
supported = "## Supported repository model"
preflight = "## Bind and preflight"
catalog_reads = "## Catalog and bounded reads"
route = "## Route and load authority"
info = "<wiki-cli> info --json"
check = "<wiki-cli> check"

if any(
    template.count(anchor) != 1
    for anchor in (supported, preflight, catalog_reads, route, info, check)
):
    raise ValueError("adapter template static repository anchors must be unique")
if not (
    template.index(authority)
    < template.index(supported)
    < template.index(preflight)
    < template.index(info)
    < template.index(check)
    < template.index(catalog_reads)
    < template.index(route)
    < template.index(BUILTIN_CATALOG_START)
    < template.index(ADAPTER_EOF)
):
    raise ValueError("adapter template static repository protocol is not ordered")
```

Keep this exact order: front-loaded authority/root constraints, supported model, preflight, bounded-read rules, routing/authority loading, embedded catalog, repository execution, and terminal EOF. Do not weaken existing bootstrap/EOF/catalog marker validation.

- [ ] **Step 3: Add renderer mutation cases before completing the validator**

Parameterize missing, duplicate, and reordered `info`, `check`, and supported-model anchors in `tests/test_agent_adapter.py`. Each mutated template must raise `ValueError` through `render_adapter_skill`; do not test the validator only as a private function.

- [ ] **Step 4: Run focused GREEN tests**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  -q -k 'template or renderer or rendered_frontmatter or bootstrap or external_adapter'
```

Expected: all selected tests pass; rendered output has no safe-reader code and remains strict-parser compatible.

- [ ] **Step 5: Commit the template and renderer change**

```bash
git add obsidian_wiki/_data/adapter/SKILL.md.in \
  obsidian_wiki/agent_adapter.py \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py
git commit -m "refactor: simplify external adapter repository reads"
```

## Task 3: Remove obsolete executable-reader tests without losing static safety

**Files:**

- Modify: `tests/test_agent_adapter.py:1-1300`
- Modify if a coverage gap is demonstrated: `tests/test_portable_config.py`
- Modify if a coverage gap is demonstrated: `tests/test_portable_check.py`

- [ ] **Step 1: Delete only reader-specific scaffolding**

Remove `SAFE_READER_START`, `SAFE_READER_END`, `SAFE_READER_HEREDOC`, `embedded_safe_reader_script`, and `execute_safe_reader`. Remove `base64` and `is_safe_skill_name` after `rg` confirms their remaining references belong only to the deleted group. Keep `stat`, `os`, `sha256`, and other imports still used by installer and renderer tests.

- [ ] **Step 2: Delete tests whose only subject is the embedded program**

Delete the `test_embedded_safe_reader_*` and `test_embedded_skill_catalog_*` group, including injected read/list/ancestor swaps, root identities, open flags, Base64 transport, catalog SHA binding, and safe-reader mode validation. Do not delete renderer source-topology tests or managed Adapter installation race tests; those protect different production code.

- [ ] **Step 3: Prove existing CLI coverage for each retained static invariant**

Run the named/static selectors and inspect collection output:

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_config.py \
  tests/test_portable_check.py \
  -q -k 'symlink or hardlink or special or fifo or unsafe'
```

Confirm coverage exists for all of these cases:

- explicit root symlink;
- `.llmwikiops` directory and config-file symlink/special file;
- configured source/vault/skills directory escape;
- canonical skill directory or `SKILL.md` symlink;
- agent skill-mirror ancestor/file symlink;
- hardlinked managed bootstrap/skill/vault-control file;
- special entry in a managed/configured tree.

- [ ] **Step 4: Add a missing static case only if the preceding audit proves a gap**

Use the existing repository fixtures and assert `ConfigError` for `info` resolution failures or the precise issue code from `check_portable_repo`. Do not recreate FD races. A representative missing-case test should have this shape:

```python
def test_check_rejects_static_symlinked_canonical_skill_file(tmp_path: Path) -> None:
    root, config = initialized_repository(tmp_path)
    skill = root / ".skills/wiki-query/SKILL.md"
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    skill.unlink()
    skill.symlink_to(outside)

    assert "canonical-skill-unsafe" in issue_codes(check_portable_repo(config))
```

Use actual local fixture/helper names and issue codes discovered in the file; do not invent parallel setup utilities if equivalent ones already exist.

- [ ] **Step 5: Run the complete affected suites**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_portable_config.py \
  tests/test_portable_check.py \
  -q
```

- [ ] **Step 6: Commit test cleanup and any demonstrated coverage addition**

```bash
git add tests/test_agent_adapter.py tests/test_portable_config.py tests/test_portable_check.py
git commit -m "test: retain static adapter topology coverage"
```

Omit unchanged files from `git add`.

## Task 4: Document the reduced threat model and preflight

**Files:**

- Modify: `docs/agents.md:20-35`
- Modify: `docs/architecture.md:30-50`
- Modify: `docs/installation.md:25-75`
- Modify: `README.md:20-45`
- Modify: `README_ZH.md:20-45`
- Modify: `tests/test_portable_human_docs.py`

- [ ] **Step 1: Add failing human-documentation contract tests**

Extend the existing human-documentation contract suite with one test that reads the five documentation surfaces and requires the substantive concepts, allowing translated wording in `README_ZH.md`:

```python
def test_external_adapter_docs_state_static_quiescent_repository_boundary() -> None:
    agents = _text("docs/agents.md")
    architecture = _text("docs/architecture.md")
    installation = _text("docs/installation.md")
    readme = _text("README.md")
    readme_zh = _text("README_ZH.md")

    for text in (agents, architecture, installation, readme):
        assert "user-controlled local" in text
        assert "quiescent" in text
    assert "llmwikiops -C <root> info --json" in agents
    assert "llmwikiops -C <root> check" in agents
    assert "用户控制的本地" in readme_zh
    assert "静止" in readme_zh
```

Use the existing `_text` helper in `tests/test_portable_human_docs.py`. Run the new test and record RED:

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_human_docs.py \
  -q -k static_quiescent_repository_boundary
```

- [ ] **Step 2: Update `docs/agents.md` with the executable Agent protocol**

State that external access supports only a user-controlled local and quiescent repository. Require the exact sequence:

```bash
llmwikiops -C <root> info --json
llmwikiops -C <root> check
```

before direct Agent reads, then describe direct-only skill enumeration, bounded UTF-8 reads, metadata override, and stop/restart on concurrent change.

- [ ] **Step 3: Update architecture and installation boundaries**

In `docs/architecture.md`, distinguish static CLI validation from adversarial TOCTOU protection and explicitly exclude shared-writable/network/concurrently synchronized repositories. In `docs/installation.md`, put the support requirement adjacent to the Adapter install workflow and show `check` immediately after `info`.

- [ ] **Step 4: Keep README landing pages aligned**

Add one compact paragraph to both README files after the external Adapter example. Avoid moving low-level implementation details into the landing page. Keep headings, links, command examples, and semantics aligned.

- [ ] **Step 5: Run documentation tests and synchronization**

```bash
uv run --with pytest python -m pytest \
  tests/test_portable_human_docs.py \
  tests/test_agent_context_boundary.py \
  -q
uv run python tools/check_readme_sync.py
```

- [ ] **Step 6: Commit the human documentation**

```bash
git add README.md README_ZH.md docs/agents.md docs/architecture.md \
  docs/installation.md tests/test_portable_human_docs.py
git commit -m "docs: state external adapter static safety boundary"
```

## Task 5: Verify generated artifacts and managed upgrades

**Files:**

- Test: `tests/test_agent_adapter.py`
- Test: `tests/test_asset_artifact_parity.py`
- Test: `tests/test_agent_context_boundary.py`
- No production file is expected unless a test reveals an actual packaging defect.

- [ ] **Step 1: Run renderer, installation, and source/wheel/sdist parity tests**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_asset_artifact_parity.py \
  -q
```

- [ ] **Step 2: Build and install into fresh isolated Agent homes**

Use temporary directories, never the user's live Agent homes:

```bash
eval_root="$(mktemp -d /tmp/llmwikiops-static-adapter.XXXXXX)"
HOME="$eval_root/home" CODEX_HOME="$eval_root/codex" \
  uv run python -m obsidian_wiki agent install-adapter --agent codex
HOME="$eval_root/home" \
  uv run python -m obsidian_wiki agent install-adapter --agent claude
```

Compare the two installed `SKILL.md` byte streams, parse both `.llmwikiops-managed.json` records with `parse_managed_record`, require each record's target to match its destination, and require its `SKILL.md` digest to match the installed bytes.

- [ ] **Step 3: Validate both installed Skills**

Run the bundled strict Skill validator against each fresh installation. It must report `Skill is valid!`; additionally verify the installed file contains no `LLMWIKIOPS_SAFE_` token and has exactly one terminal EOF marker.

- [ ] **Step 4: Exercise an idempotent install and a managed upgrade**

Re-run each install and require `unchanged`. To cover the real old-to-new transition, install the parent-commit Adapter in another isolated home, then invoke the current command and require `upgraded`, a current target-bound ownership record, and retained old evidence outside the active skills namespace. Do not delete the retained evidence.

- [ ] **Step 5: Commit only if verification exposed and fixed a packaging defect**

If no code changes were necessary, make no empty commit. If a defect was found, add a RED regression first, fix it, rerun this task, and use:

```bash
git commit -m "fix: package simplified external adapter"
```

## Task 6: Run fresh external-wiki behavioral acceptance

**Files:**

- Modify if needed: `tests/test_external_wiki_e2e.py`
- Evidence only: a fresh directory below `/tmp/llmwikiops-static-adapter-eval.*`

- [ ] **Step 1: Preserve automated external lifecycle coverage**

Run:

```bash
uv run --with pytest python -m pytest \
  tests/test_external_wiki_e2e.py \
  tests/test_explicit_repository_cli.py \
  -q
```

If the external E2E test encodes safe-reader commands, first add a RED expectation for `info` then `check`, update only the protocol-dependent fixture, and retain all business-CWD, exact-root, recovery-command, and no-outside-write assertions.

- [ ] **Step 2: Create three fresh, isolated evaluation fixtures**

Use separate exact roots and unrelated business CWDs. Install the current Adapter into a new Codex home and pin the tested CLI launcher to this worktree/build. Record commit SHA, CLI version, Adapter SHA-256, fixture SHA-256, runtime/model, start/end UTC, and every tool command in each transcript.

- [ ] **Step 3: Evaluate an external query**

Request a sentinel available only in the external wiki. Require this order:

1. complete Adapter read through the unique EOF marker;
2. exact `llmwikiops -C <root> info --json`;
3. exact `llmwikiops -C <root> check`;
4. only then direct catalog/authority reads;
5. exact-root query returning the sentinel.

Require no `root-bind`, `skill-catalog`, `LLMWIKIOPS_SAFE_*`, source hunt, `cd`, alternate-root command, or write outside the selected wiki.

- [ ] **Step 4: Evaluate transaction recovery**

Create a controlled retained transaction failure, execute the recovery argv returned by the CLI with the same literal `-C <root>`, complete the transaction, and refresh hot state. Require the business CWD and alternate repository to remain byte/identity unchanged.

- [ ] **Step 5: Evaluate repository-local override and reroute**

Provide a same-name repository-local skill whose description changes routing and whose body contains a sentinel. Require metadata merge after preflight, route re-evaluation, complete canonical and selected body reads, sentinel output, and exact `-C` on every repository-aware CLI call.

- [ ] **Step 6: Treat protocol violations as failures**

Do not call a run GREEN if it reads/lists/searches the external root before both preflight commands, uses a removed reader token, switches roots, changes CWD, reconstructs a recovery command, or relies on an unpinned executable. Archive raw transcripts and a machine-checkable command audit.

- [ ] **Step 7: Commit only automated-test changes**

If `tests/test_external_wiki_e2e.py` changed through RED/GREEN, run its owner suites and commit:

```bash
git add tests/test_external_wiki_e2e.py
git commit -m "test: verify static external adapter lifecycle"
```

Do not commit temporary evaluation repositories or transcripts.

## Task 7: Final verification, review, and release handoff

**Files:**

- Review all files changed since `c188a18`

- [ ] **Step 1: Run focused correctness suites**

```bash
uv run --with pytest python -m pytest \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_asset_artifact_parity.py \
  tests/test_portable_config.py \
  tests/test_portable_check.py \
  tests/test_external_wiki_e2e.py \
  tests/test_explicit_repository_cli.py \
  -q
```

- [ ] **Step 2: Run repository validation tools**

```bash
uv run python tools/check_readme_sync.py
uv run ruff check obsidian_wiki/agent_adapter.py \
  tests/test_agent_adapter.py \
  tests/test_agent_context_boundary.py \
  tests/test_external_wiki_e2e.py
git diff --check c188a18..HEAD
```

- [ ] **Step 3: Run the full exact suite from `AGENTS.md`**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest \
  -q -p no:cacheprovider
```

- [ ] **Step 4: Perform a spec-focused review**

Review the final diff against `docs/superpowers/specs/2026-08-19-external-adapter-static-repository-design.md`. Explicitly check:

- no embedded safe-reader implementation or obsolete tests remain;
- no static topology validation was weakened;
- renderer fails closed on bootstrap, EOF, catalog, and preflight anchors;
- ordinary reads are authorized only after both preflight commands;
- docs do not claim adversarial TOCTOU protection;
- source/wheel/sdist/installed bytes agree;
- fresh behavior preserves exact root, business CWD, routing, overrides, recovery, and no-outside-write boundaries.

- [ ] **Step 5: Resolve every review finding with its own RED/GREEN loop**

For each substantive finding, reproduce it with the smallest failing test, implement the minimum correction, rerun the owner suite and final affected suites, then commit with a focused message. Do not waive a finding because the full suite was previously green.

- [ ] **Step 6: Confirm a clean handoff**

```bash
git status --short
git log --oneline c188a18..HEAD
```

Expected: clean worktree, all implementation commits visible, full suite green, and behavioral evidence paths reported. Do not reinstall the user's live CLI/Adapters, update an external wiki, push, or publish unless the user separately authorizes those operational steps.

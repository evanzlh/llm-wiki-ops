# Final Fix Report

Date: 2026-09-03
Base: `ff9b22255488fd9af07ad4a030cea88cb9ff4ba9`
Implementation commit: `ee5554b0eb1d5a487b0bd6c6293f18734b646a59`
Status: complete

## Finding-to-change map

1. **Pre-mutation overlap guards**

   - The canonical guard now precedes the initial transaction promotion and checks
     each candidate/deletion target, derived manifest-v2 shard, and canonical log
     target with literal-path porcelain status
     (`obsidian_wiki/_data/skills/llm-wiki/SKILL.md:117`). The hot-path guard likewise
     precedes every hot write (`llm-wiki/SKILL.md:177`).
   - The same ordering and target coverage is present in all nine maintenance
     workflows: `cross-linker`, `daily-update`, `tag-taxonomy`, `wiki-dedup`,
     `wiki-lint`, `wiki-rebuild`, `wiki-status`, `wiki-synthesize`, and `wiki-update`
     (their steps 5-7; for representative anchors see
     `cross-linker/SKILL.md:107` and `wiki-update/SKILL.md:152`).
   - Regressions:
     `tests/test_portable_write_protocol.py:597` verifies guard-before-mutation
     ordering, and `:629` uses a real Git repository to prove staged and unstaged
     overlap blocks affected-page, shard, log, and hot mutations.

2. **Absent Source deletion hole**

   - The shared Source snapshot contract now requires safe topology, filesystem
     absence, a valid `HEAD`, no index entry, and empty literal-path status before
     creation, then requires exactly `?? <Source ID>` afterward
     (`wiki-capture/references/source-snapshot.md:56`).
   - The gate is required directly by the four Source workflows
     (`wiki-capture/SKILL.md:126`, `wiki-ingest/SKILL.md:63`,
     `wiki-import/SKILL.md:199`, `wiki-research/SKILL.md:69`) and by
     `wiki-update/SKILL.md:66`. The seven history state tables now permit creation
     only for a filesystem-absent target that passes that gate
     (`claude-history-ingest/SKILL.md:93`, `codex-history-ingest/SKILL.md:82`,
     `copilot-history-ingest/SKILL.md:84`, `hermes-history-ingest/SKILL.md:82`,
     `openclaw-history-ingest/SKILL.md:86`, `pi-history-ingest/SKILL.md:103`, and
     `wiki-agent/SKILL.md:86`).
   - Regressions: `tests/test_portable_write_protocol.py:1125` covers the exact
     contract and post-write porcelain, `:1159` proves staged and unstaged tracked
     deletions never authorize recreation, and the logical-state helper/table now
     models `HEAD`, index, and status rather than treating every absent filesystem
     path as creatable.

3. **Canonical stale owner gate**

   - Canonical prerequisite text now requires Agent substantive review plus the
     exact-path local authority checkpoint
     (`obsidian_wiki/_data/skills/llm-wiki/SKILL.md:79`).
   - Regressions reject the stale phrase in source, built wheel, sdist, rebuilt
     wheel, and installed artifacts
     (`tests/test_portable_write_protocol.py:712`,
     `tests/test_asset_artifact_parity.py:402`, and
     `tests/test_portable_setup.py:1558`).

4. **Transaction-review autonomy by request scope**

   - `wiki-transaction-review` now branches explicitly: inspection-only requests
     remain read-only, while explicit completion/recovery requests authorize Agent
     review, fresh validation, bounded candidate/deletion review, guarded
     commit/retry, guarded hot refresh, final check, and one exact-path local result
     commit (`obsidian_wiki/_data/skills/wiki-transaction-review/SKILL.md:75-151`).
     Confirmation remains limited to lossy, semantic, external, or overlap choices;
     push/remotes/history/reset/checkout/clean/force are not authorized.
   - Regressions are in `tests/test_portable_write_protocol.py:847` and the
     packaged-skill protocol checks around `tests/test_portable_skill_protocol.py:570`.

5. **Retry freshness**

   - Canonical retry instructions now require fresh validation, bounded review of
     current `candidate_pages` and deletions, and a repeated overlap guard
     immediately before every promotion-capable retry; failed-state checks alone are
     explicitly insufficient (`llm-wiki/SKILL.md:161`). The same contract is in all
     nine maintenance copies (for example `cross-linker/SKILL.md:131` and
     `wiki-update/SKILL.md:176`) and in transaction review
     (`wiki-transaction-review/SKILL.md:121`).
   - Runtime-produced guidance carries the same deterministic preconditions
     (`obsidian_wiki/transaction_guidance.py:131`). Regressions:
     `tests/test_portable_write_protocol.py:662` and
     `tests/test_transaction_guidance.py:244`.

6. **Final lifecycle closure**

   - Canonical completion now requires final `check`, exact transaction-result
     `created`/`updated`/`removed` paths, vault-relative `log_path`, derived affected
     shards, changed hot only when applicable, staged-patch inspection/diff check,
     and one exact-path local result commit (`llm-wiki/SKILL.md:196`).
   - All four Source workflows carry the same terminal lifecycle
     (`wiki-capture/SKILL.md:181`, `wiki-ingest/SKILL.md:118`,
     `wiki-import/SKILL.md:255`, `wiki-research/SKILL.md:124`). All seven history
     parents carry it in step 8 (anchors: `claude-history-ingest/SKILL.md:106`,
     `codex-history-ingest/SKILL.md:95`, `copilot-history-ingest/SKILL.md:97`,
     `hermes-history-ingest/SKILL.md:95`, `openclaw-history-ingest/SKILL.md:99`,
     `pi-history-ingest/SKILL.md:116`, `wiki-agent/SKILL.md:99`).
   - Regressions: `tests/test_portable_write_protocol.py:965` plus package/install
     lifecycle checks at `tests/test_asset_artifact_parity.py:418` and
     `tests/test_portable_setup.py:1565`.

7. **Autonomy-aligned dedup wording (Minor)**

   - The `wiki-dedup` descriptor now says `evidence-supported duplicate pair`
     (`obsidian_wiki/_data/skills/wiki-dedup/SKILL.md:3`), covered by
     `tests/test_portable_skill_protocol.py:1292`.

The two unrelated test-only style Minors were not expanded into this wave, as
requested. Ponytail full kept the implementation to existing instruction-contract
patterns and the existing deterministic guidance data; no new abstraction,
dependency, or Python execution path was introduced.

## TDD evidence

### RED

- Initial contract command:
  `uv run --with pytest pytest -q tests/test_portable_write_protocol.py tests/test_transaction_guidance.py tests/test_portable_skill_protocol.py::test_dedup_bounds_candidate_generation_before_similarity_scoring tests/test_portable_setup.py::test_bundled_setup_installs_the_exact_current_skill_inventory`
  -> `11 failed, 84 passed in 3.18s`.
- Focused absent-Source/state-table command:
  `uv run --with pytest pytest -q tests/test_portable_write_protocol.py::test_absent_source_contract_checks_index_and_status_before_write tests/test_portable_write_protocol.py::test_history_snapshot_logical_identity_state_table`
  -> `1 failed, 1 passed`; the new HEAD/index/status requirement was missing.

### GREEN

- The initial focused set reached `95 passed in 2.22s`.
- The absent-Source contract, state table, staged/unstaged deletion, and topology
  subset reached `5 passed in 0.12s`.
- Final focused contract/artifact sweep:
  `uv run --with pytest python -m pytest -q tests/test_portable_write_protocol.py tests/test_portable_skill_protocol.py tests/test_pre_write_snapshot_docs.py tests/test_transaction_guidance.py tests/test_asset_artifact_parity.py`
  -> `191 passed in 4.47s`.

## Final verification

- Distribution asset parity:
  `uv run --with pytest pytest -q tests/test_asset_artifact_parity.py::test_distribution_assets_exactly_match_canonical_package_data`
  -> `1 passed in 3.17s`.
- README synchronization: `uv run python tools/check_readme_sync.py`
  -> `README_ZH.md is up to date with README.md.`
- Stale-phrase scan across packaged data found no instances of
  `owner review described above`, `owner-approved duplicate pair`, the old blanket
  local-commit prohibition, unconditional explicit-user-approval wording, or the
  old bare absent-target create table.
- `git diff --check` passed before the implementation commit.
- Required full suite:
  `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider`
  -> `3231 passed, 20 subtests passed in 629.52s (0:10:29)`.

## Commits and concerns

- Fix commit: `ee5554b0eb1d5a487b0bd6c6293f18734b646a59`
  (`fix: close autonomous transaction safety gaps`).
- This report is committed separately as closeout metadata; its generated commit
  hash is reported in the final handoff.
- No push, remote mutation, checkout/reset/clean/force operation, or history rewrite
  was performed. No known implementation or verification concerns remain.

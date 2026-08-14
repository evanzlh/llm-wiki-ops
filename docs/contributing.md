# Contributing

This checkout is framework source, not an initialized knowledge repository. Do not resolve a vault or run repository skills unless a test or an explicit end-to-end task requires it.

## Source boundaries

- Python under `obsidian_wiki/` owns deterministic setup, validation, transactions, and maintenance.
- Built-in skill sources live under `obsidian_wiki/_data/skills/`.
- Bootstrap templates live under `obsidian_wiki/_data/bootstrap/`.
- The framework checkout intentionally has no project-local wiki skill mirrors.

Preserve owner changes, reject unsafe links and special files, and keep persisted paths repository-relative.

## Tests

Run a focused test while iterating, then the full suite:

```bash
uv run --with pytest python -m pytest tests/test_portable_setup.py -q
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider
```

Behavior changes require a regression test that fails before the implementation changes. Documentation tests scan only current surfaces; Superseded decision records retain their historical bodies.

## Documentation

`README.md` and `README_ZH.md` are one landing-page surface. Keep headings, examples, links, and behavior aligned. Put detailed reference material under `docs/`, verify local links, and run:

```bash
uv run python tools/check_readme_sync.py
```

Current documentation describes only the single repository product. Describe interfaces by their current names and verify CLI examples against `llmwikiops --help`.

## Skills

Add a managed built-in at `obsidian_wiki/_data/skills/<name>/SKILL.md`, update inventory and parity tests, and ensure every knowledge-writing path uses transaction validation and review. Do not add generated mirrors to this source checkout.

## Test a skill change

Rebuild the installed CLI before testing. Create a disposable Git repository, scaffold it with the installed command, validate the generated mirrors, and run asset parity tests from the framework checkout:

```bash
uv tool install --force --reinstall --link-mode copy .
disposable="$(mktemp -d)"
mkdir "$disposable/knowledge"
git -C "$disposable/knowledge" init
llmwikiops setup "$disposable/knowledge"
(cd "$disposable/knowledge" && llmwikiops check)
(cd "$disposable/knowledge" && llmwikiops repo sync-skills --json --pretty)
uv run --with pytest python -m pytest tests/test_asset_artifact_parity.py tests/test_skill_trees.py -q
```

The disposable repository is the runtime fixture. Never use the framework source checkout as a runtime fallback; it intentionally has no generated repository skill tree.

## Upgrade protocol for maintainers

Use the same two-step CLI and repository upgrade protocol as users. On an owner branch, install the new CLI from the framework clone, then read the knowledge repository's tracked `requires_cli`. Resolution fails closed if its PEP 440 constraint excludes the new version, so explicitly review and edit the constraint before maintenance:

```bash
git switch -c upgrade-llmwikiops
cd /path/to/llm-wiki-ops
uv tool install --force --reinstall --link-mode copy .
cd /path/to/team-knowledge
${EDITOR:?} .llmwikiops/config.toml
llmwikiops repo upgrade-skills
llmwikiops check
git diff
git commit -m "Upgrade LLMWikiOps"
```

The command does not rewrite `requires_cli`. Collaborators review the complete diff before the owner commits or publishes it.

## Commits

Keep changes scoped, run `git diff --check`, and include the tests that establish the contract. Publishing a branch or pull request remains an explicit maintainer action.

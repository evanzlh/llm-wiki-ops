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

- Run focused tests with `uv run --with pytest python -m pytest tests/test_portable_setup.py -q`.
- Run the full suite with `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider`.
- Install the CLI from this clone with `uv tool install --force --reinstall --link-mode copy .`.

## Documentation

`README.md` and `README_ZH.md` are one documentation surface. Keep their headings,
examples, links, and behavior aligned. Run `uv run python tools/check_readme_sync.py`.

Human-facing details belong in `docs/`; `README.md` remains a landing page.

## Safety

Preserve owner changes, use repository-relative portable data, reject unsafe links and
special files, and add regression tests before changing behavior.

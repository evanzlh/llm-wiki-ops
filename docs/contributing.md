# Contributing

This is early. The skills work, but there's room to make the brain smarter: better cross-referencing, sharper deduplication, bigger vaults, new ingest sources. If you've been chewing on this problem or have a workflow that could be a skill, PRs are welcome.

## Adding a new skill

1. Create a folder in `obsidian_wiki/_data/skills/your-skill-name/`
2. Add a `SKILL.md` with YAML frontmatter (`name`, `description`) and markdown instructions
3. From the framework clone, rebuild the non-editable installed CLI so the updated bundled tree is installed:
   ```bash
   uv tool install --force --reinstall --link-mode copy .
   ```
4. Run `obsidian-wiki setup` to refresh personal-mode agent directories, or create a disposable portable repository with `obsidian-wiki setup --portable <path>`
5. Test by saying something to your agent that matches the description

The `description` is load-bearing — it's the only thing an agent sees when deciding whether your skill is relevant. Write it as a list of the phrases a user would actually say, and state what the skill is *not* for when it's easily confused with a neighbour.

Use the corresponding bundled skill at
`obsidian_wiki/_data/skills/skill-creator/SKILL.md` for the full guide, or just
ask your agent to run `/skill-creator`.

The framework source has no local wiki skills by design: opening this source
repository in a coding agent must not inject the wiki-maintenance skill set.
Edit `obsidian_wiki/_data/skills/<name>/SKILL.md` (for example,
`obsidian_wiki/_data/skills/wiki-ingest/SKILL.md`), reinstall with the command
above, and exercise setup in a disposable portable repository. Inside an actual
portable knowledge repository, `.skills/` is owner-editable canonical content;
never copy those repository-specific edits back into the framework bundle.

When you add a skill, also add it to the [skills reference](skills.md) and the routing table in `AGENTS.md`.

## Keeping both READMEs in sync

`README.md` (English) and `README_ZH.md` (Simplified Chinese) are **one documentation surface**. Keep headings, examples, links, and user-facing behavior structurally and semantically aligned.

Syncing is advisory, not a merge gate — the `readme-translation-drift` CI job only reports when the translation falls behind. To catch up:

```bash
python tools/check_readme_sync.py
```

It lists the commits that changed `README.md` without a later `README_ZH.md` update, plus the pending English diff. Translate and backfill those into `README_ZH.md`. Reviewers assess translation quality.

The `docs/` pages are English-only for now.

## Repo conventions

- `obsidian_wiki/_data/skills/` is the framework source of truth. Portable repositories have their own `.skills/` canonical tree and six generated agent mirrors; never edit a generated mirror.
- `CLAUDE.md`, `GEMINI.md`, and `.hermes.md` are symlinks to `AGENTS.md`. Edit `AGENTS.md`.
- New config variables belong in three places: `.env.example`, [`docs/configuration.md`](configuration.md), and the skill that reads them.
- New CLI subcommands belong in [`docs/cli.md`](cli.md).

## Tests

```bash
pytest
```

Tests live in `tests/`. Skill behavior that can be asserted deterministically (config resolution, manifest handling, graph math, session indexing) has coverage there; the LLM-driven parts don't.

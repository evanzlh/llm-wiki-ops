# Fork Relationship and Rationale

## Attribution

This repository derives from [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) at commit `5ef66b6bec8b26bab6594ac37fb4d8371469fbab`. The upstream author and MIT license remain credited in `pyproject.toml`, Git history, and `LICENSE`.

## Independent evolution

`evanzlh/obsidian-wiki` evolves independently from that baseline and does not track future upstream changes. It is not an official upstream distribution. Similar names and compatible commands describe ancestry, not release equivalence.

## Motivation

The fork targets knowledge bases maintained like software: authoritative sources and the compiled Obsidian vault share one Git repository; any contributor can compile changes on a branch; and humans review the resulting knowledge diff in a pull request.

## Fork-specific capabilities

- Portable Repository mode and repository-relative TOML configuration
- Repository-local canonical skills with complete ordinary-file mirrors for six agent discovery trees
- Stable Source IDs with sharded manifest v2
- Transactional page promotion and merge-friendly operation journals
- Stable index/log views and local rebuildable `hot.md`
- Deterministic `obsidian-wiki check` validation without LLM calls
- Dry-run-first legacy migration with byte-for-byte rollback snapshots
- Clone-stable source bytes and conflict-resistant multi-branch collaboration

## Compatibility

The fork keeps the `obsidian-wiki` Python distribution and CLI command names. Portable repositories additionally require the implementation identifier `evanzlh/obsidian-wiki`, so an upstream binary with a coincidentally matching version is rejected.

## Installation policy

The only supported installation is `git clone` followed by non-editable `uv tool install --link-mode copy .`. The fork is not published to PyPI, does not support remote-URL or skills-registry installation, and does not retain `setup.sh`.

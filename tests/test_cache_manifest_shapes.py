"""The cache module exposes only the sharded manifest API."""
from __future__ import annotations

import obsidian_wiki.cache as cache


def test_personal_manifest_helpers_are_not_exposed() -> None:
    removed = {
        "_manifest_path",
        "_load_raw",
        "_load_manifest",
        "_save_manifest",
        "_iter_entries",
        "_strip_algo",
        "_format_hash",
        "_is_file_key",
        "_same_source",
        "_missing_on_disk",
        "SourceEntry",
        "update_source",
    }

    assert removed.isdisjoint(vars(cache))

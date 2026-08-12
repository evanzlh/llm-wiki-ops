"""Content hashing and manifest-v2 source status."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TypedDict

from obsidian_wiki.config import PortableConfig


class CheckResult(TypedDict):
    new: list[str]
    modified: list[str]
    unchanged: list[str]
    missing: list[str]


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Return the hex SHA-256 digest of *path* without loading it all into RAM."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_dir(path: Path) -> str:
    """Stable SHA-256 over all files in a directory tree (sorted by relative path)."""
    h = hashlib.sha256()
    for fp in sorted(path.rglob("*")):
        if fp.is_file():
            rel = str(fp.relative_to(path))
            h.update(rel.encode())
            h.update(sha256_file(fp).encode())
    return h.hexdigest()


def compute_hash(path: Path) -> str:
    if path.is_dir():
        return sha256_dir(path)
    return sha256_file(path)


def check_sources(
    config: PortableConfig,
    source_paths: list[Path],
) -> CheckResult:
    """Classify sources against the configured sharded manifest."""
    from obsidian_wiki.portable_manifest import ShardedManifest

    return ShardedManifest(config).status_for(source_paths)


def hash_file(path: Path) -> str:
    """Compute and return the hash without manifest I/O."""
    return compute_hash(path)

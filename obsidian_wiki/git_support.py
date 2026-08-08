"""Read-only Git facts for portable repositories."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_GIT_ENV_OVERRIDES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)


def _git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in _GIT_ENV_OVERRIDES
        and not key.upper().startswith("GIT_TRACE")
    }
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _git_bytes(path: Path, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            timeout=10,
            check=False,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _output_line(output: bytes) -> bytes:
    if output.endswith(b"\n"):
        output = output[:-1]
    return output


def discover_git_root(path: Path) -> Path | None:
    """Return the enclosing worktree root without creating or changing a repo."""

    result = _git_bytes(Path(path), "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return None
    output = _output_line(result.stdout)
    if not output:
        return None
    return Path(os.fsdecode(output)).resolve(strict=False)


def git_branch_id(root: Path) -> str:
    """Return the current branch, detached commit, or ``no-git``."""

    branch = _git_bytes(
        Path(root), "symbolic-ref", "--quiet", "--short", "HEAD"
    )
    if branch is not None and branch.returncode == 0:
        output = _output_line(branch.stdout)
        if output:
            return os.fsdecode(output)
    head = _git_bytes(Path(root), "rev-parse", "--verify", "HEAD")
    if head is not None and head.returncode == 0:
        output = _output_line(head.stdout)
        if output:
            return os.fsdecode(output)
    return "no-git"


def tracked_paths(root: Path) -> tuple[str, ...]:
    """Return deterministic repository-relative paths known to the Git index."""

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            timeout=10,
            check=False,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    return tuple(
        sorted(os.fsdecode(part) for part in result.stdout.split(b"\0") if part)
    )

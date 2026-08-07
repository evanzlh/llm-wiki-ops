"""Report Simplified Chinese README translation drift.

Lists commits that changed README.md after the last commit that touched
README_ZH.md, plus the combined English diff that still needs to be
translated and backfilled into README_ZH.md. Advisory only — exits 1 on
drift so callers can detect it, but CI never uses it to block a merge.
"""
from __future__ import annotations

import subprocess


ENGLISH = "README.md"
TRANSLATION = "README_ZH.md"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def main() -> int:
    # A translation-only commit marks everything before it as synced;
    # per-commit pairing if that ever misleads
    last_translation = git("log", "-1", "--format=%H", "--", TRANSLATION).strip()
    log_range = f"{last_translation}..HEAD" if last_translation else "HEAD"
    pending = git(
        "log", "--format=%h %s", log_range, "--", ENGLISH
    ).strip()

    if not pending:
        print(f"{TRANSLATION} is up to date with {ENGLISH}.")
        return 0

    print(f"Commits that changed {ENGLISH} without a later {TRANSLATION} update:")
    print(pending)
    print()
    print(f"English changes not yet reflected in {TRANSLATION}:")
    if last_translation:
        print(git("diff", log_range, "--", ENGLISH))
    else:
        print(f"{TRANSLATION} has no history — the entire {ENGLISH} is untranslated.")
    print(f"Translate the changes above and backfill them into {TRANSLATION}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

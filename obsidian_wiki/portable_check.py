"""Read-only deterministic validation for portable repositories."""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .frontmatter import FrontmatterError, parse_frontmatter
from .portable_manifest import ManifestError, ShardedManifest


@dataclass(frozen=True)
class CheckIssue:
    code: str
    path: str
    message: str
    severity: Literal["warning", "error"] = "error"


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "."


def check_portable_repo(config) -> dict[str, object]:
    root = config.root
    issues: list[CheckIssue] = []
    try:
        store = ShardedManifest(config)
        entries = store.iter_entries()
    except ManifestError as exc:
        issues.append(CheckIssue("manifest-invalid", ".manifest.json", str(exc)))
        entries = []
        store = None
    if store:
        status = store.status()
        for code, paths in (("source-new", status["new"]), ("source-stale", status["modified"]), ("source-orphaned", status["missing"])):
            for path in paths:
                issues.append(CheckIssue(code, path, f"manifest source is {code[7:]}"))
        for entry in entries:
            for page_name in entry.pages:
                page = config.vault / page_name
                if not page.is_file() or page.is_symlink():
                    issues.append(CheckIssue("manifest-page-missing", page_name, "manifest page does not exist"))
        for source in config.sources:
            if source.exists():
                for path in source.rglob("*"):
                    if path.is_file() and path.read_text(encoding="utf-8", errors="ignore").startswith("version https://git-lfs.github.com/spec/v1"):
                        issues.append(CheckIssue("unsupported-git-lfs-pointer", _rel(root, path), "Git LFS pointer is not source content"))
    required = {"title", "category", "tags", "sources", "created", "updated"}
    categories = {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
    for page in config.vault.rglob("*.md"):
        rel = page.relative_to(config.vault)
        if not rel.parts or rel.parts[0] not in categories or (rel.parts[0] == "journal" and len(rel.parts) > 1 and rel.parts[1] == "operations"):
            continue
        try:
            fm = parse_frontmatter(page.read_text(encoding="utf-8"))
        except (OSError, FrontmatterError) as exc:
            issues.append(CheckIssue("frontmatter-invalid", _rel(root, page), str(exc)))
            continue
        missing = sorted(required - set(fm.scalars) - set(fm.lists))
        if missing:
            issues.append(CheckIssue("frontmatter-missing", _rel(root, page), "missing: " + ", ".join(missing)))
        for source in fm.lists.get("sources", ()):
            if Path(source).is_absolute():
                issues.append(CheckIssue("absolute-page-source", _rel(root, page), "page source must be repository-relative"))
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=False, timeout=10)
        if proc.returncode == 0:
            tracked = proc.stdout.decode().split("\0")
            for path in tracked:
                if path == "wiki/hot.md" or path.startswith(".obsidian-wiki/local/") or "/.locks/" in path or "/.snapshots/" in path or "/.transactions/" in path:
                    issues.append(CheckIssue("tracked-local-state", path, "mutable local state must not be tracked"))
        else:
            issues.append(CheckIssue("git-unavailable", ".", "Git worktree is unavailable", "warning"))
    except OSError:
        issues.append(CheckIssue("git-unavailable", ".", "Git is unavailable", "warning"))
    issues.sort(key=lambda x: (x.severity, x.code, x.path, x.message))
    errors = sum(i.severity == "error" for i in issues)
    warnings = sum(i.severity == "warning" for i in issues)
    return {"status": "fail" if errors else ("warn" if warnings else "pass"), "errors": errors, "warnings": warnings, "issues": [asdict(i) for i in issues]}

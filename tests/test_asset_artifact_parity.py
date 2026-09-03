from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "obsidian_wiki" / "_data"
ASSET_NAMES = ("skills", "bootstrap", "adapter")
REMOVED_DISTRIBUTION_PATHS = {
    ".claude/hooks/wiki-stop-capture.sh",
    ".claude/settings.json",
    ".github/workflows/publish.yml",
    ".github/workflows/setup.yml",
    "README_TW.md",
    "SETUP.md",
    ".env.example",
    "obsidian_wiki/_data/skills/wiki-capture/references/RAW-FORMAT.md",
    "obsidian_wiki/migration.py",
    "obsidian_wiki/sync.py",
    "scripts/com.obsidian-wiki.daily-update.plist",
    "scripts/daily-update.sh",
    "scripts/manifest.py",
    "scripts/wiki-notify.sh",
    "setup.sh",
    "tests/test_portable_migration.py",
    "tests/test_manifest_delta.py",
    "tests/test_stop_hook_behavior.py",
    "tests/test_stop_hook_packaging.py",
    "tests/test_sync.py",
    "tests/test_sync_setup_parity.py",
}
REMOVED_DISTRIBUTION_PREFIXES = (
    ".agents/skills/",
    ".claude/skills/",
    ".cursor/skills/",
    ".kiro/skills/",
    ".pi/skills/",
    ".windsurf/skills/",
    "obsidian_wiki/_data/skills/memory-bridge/",
    "obsidian_wiki/_data/skills/wiki-dashboard/",
    "obsidian_wiki/_data/skills/wiki-stage-commit/",
    "obsidian_wiki/_data/skills/wiki-switch/",
)
PROTOCOL_BOOTSTRAP_PATHS = {
    "bootstrap/agent/rules/llmwikiops.md",
    "bootstrap/agent/workflows/llmwikiops.md",
    "bootstrap/cursor/rules/llmwikiops.mdc",
    "bootstrap/windsurf/rules/llmwikiops.md",
    "bootstrap/kiro/steering/llmwikiops.md",
}
FORMER_PROTOCOL_RESOURCE = re.compile(
    rb"(?i)(?:\.obsidian-wiki|"
    rb"(?<![A-Za-z0-9_])obsidian-wiki(?![A-Za-z0-9_])|"
    rb"(?i:OBSIDIAN_WIKI_[A-Z0-9_]+)|obsidian\s+wiki)"
)
FORMER_PROTOCOL_PATH = re.compile(
    r"(?i)(?:\.obsidian-wiki|"
    r"(?<![A-Za-z0-9_])obsidian-wiki(?![A-Za-z0-9_])|"
    r"(?i:OBSIDIAN_WIKI_[A-Z0-9_]+)|obsidian\s+wiki)"
)


def test_normalized_wheel_inventory_ignores_zip_timestamps(tmp_path: Path) -> None:
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    for path, timestamp in (
        (first, (2020, 1, 1, 0, 0, 0)),
        (second, (2026, 8, 13, 12, 0, 0)),
    ):
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo("package/module.py", timestamp)
            info.external_attr = 0o644 << 16
            archive.writestr(info, b"VALUE = 1\n")

    assert _normalized_wheel_inventory(first) == _normalized_wheel_inventory(second)


def test_packaged_runtime_text_has_explicit_lf_policy_and_lf_only_bytes() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "obsidian_wiki/_data/**/*.md text eol=lf" in attributes.splitlines()
    assert "obsidian_wiki/_data/**/*.mdc text eol=lf" in attributes.splitlines()
    assert (
        "obsidian_wiki/_data/adapter/SKILL.md.in text eol=lf"
        in attributes.splitlines()
    )
    scoped = subprocess.run(
        [
            "git",
            "check-attr",
            "text",
            "eol",
            "--",
            "obsidian_wiki/_data/skills/wiki-ingest/references/pageindex.md",
            "obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc",
            "obsidian_wiki/_data/adapter/SKILL.md.in",
            "README.md",
            "docs/cli.md",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert scoped == [
        "obsidian_wiki/_data/skills/wiki-ingest/references/pageindex.md: text: set",
        "obsidian_wiki/_data/skills/wiki-ingest/references/pageindex.md: eol: lf",
        "obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc: text: set",
        "obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc: eol: lf",
        "obsidian_wiki/_data/adapter/SKILL.md.in: text: set",
        "obsidian_wiki/_data/adapter/SKILL.md.in: eol: lf",
        "README.md: text: unspecified",
        "README.md: eol: unspecified",
        "docs/cli.md: text: unspecified",
        "docs/cli.md: eol: unspecified",
    ]
    paths = tuple(
        path
        for path in (ROOT / "obsidian_wiki/_data").rglob("*")
        if path.is_file() and path.suffix in {".md", ".mdc"}
    )
    assert paths
    for path in paths:
        assert b"\r" not in path.read_bytes(), path
        relative = path.relative_to(ROOT).as_posix()
        blob = subprocess.run(
            ["git", "show", f":{relative}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        assert b"\r" not in blob, relative
    assert b"\r" not in (ROOT / "obsidian_wiki/_data/adapter/SKILL.md.in").read_bytes()


def test_archive_path_audit_rejects_former_protocol_filenames() -> None:
    paths = {
        "llm_wiki_ops-2026.9/obsidian_wiki/portable.py",
        "llm_wiki_ops-2026.9.dist-info/METADATA",
        "llm_wiki_ops-2026.9/extensions/obsidian-wiki-probe.js",
        "llm_wiki_ops-obsidian-wiki/PKG-INFO",
        "llm_wiki_ops-2026.9-obsidian-wiki.dist-info/METADATA",
        ".agent/rules/obsidian-wiki.md.extra",
        "LICENSE.obsidian-wiki",
    }

    violations = _archive_path_protocol_violations(paths)

    assert violations == {
        "llm_wiki_ops-2026.9/extensions/obsidian-wiki-probe.js",
        "llm_wiki_ops-obsidian-wiki/PKG-INFO",
        "llm_wiki_ops-2026.9-obsidian-wiki.dist-info/METADATA",
        ".agent/rules/obsidian-wiki.md.extra",
        "LICENSE.obsidian-wiki",
    }


def _archive_path_protocol_violations(paths: set[str]) -> set[str]:
    violations: set[str] = set()
    for path in paths:
        if FORMER_PROTOCOL_PATH.search(path):
            violations.add(path)
    return violations


def test_source_inventory_rejects_tracked_missing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["git", "ls-files"], returncode=0, stdout=b"obsidian_wiki/missing.py\0"
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)

    with pytest.raises(AssertionError, match="obsidian_wiki/missing.py"):
        _source_package_inventory()


def _entry(content: bytes, mode: int) -> tuple[int, str, int]:
    return (len(content), hashlib.sha256(content).hexdigest(), mode & 0o777)


def _normalized_wheel_inventory(wheel: Path) -> dict[str, tuple[int, str, int]]:
    inventory: dict[str, tuple[int, str, int]] = {}
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            assert info.filename not in inventory, info.filename
            content = archive.read(info)
            inventory[info.filename] = _entry(
                content, (info.external_attr >> 16) & 0o777
            )
    return inventory


def _normalized_sdist_inventory(sdist: Path) -> dict[str, tuple[int, str, int]]:
    inventory: dict[str, tuple[int, str, int]] = {}
    with tarfile.open(sdist) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = Path(member.name).parts
            assert len(parts) > 1, member.name
            relative = "/".join(parts[1:])
            assert relative not in inventory, relative
            stream = archive.extractfile(member)
            assert stream is not None
            inventory[relative] = _entry(stream.read(), member.mode)
    return inventory


def _source_package_inventory() -> dict[str, tuple[int, str, int]]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", "obsidian_wiki"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=30,
    )
    inventory: dict[str, tuple[int, str, int]] = {}
    for encoded_path in completed.stdout.split(b"\0"):
        if not encoded_path:
            continue
        relative = encoded_path.decode("utf-8")
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink(), relative
        inventory[relative] = _entry(path.read_bytes(), path.stat().st_mode)
    return inventory


def _source_distribution_inventory() -> dict[str, tuple[int, str, int]]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=30,
    )
    inventory: dict[str, tuple[int, str, int]] = {}
    for encoded_path in completed.stdout.split(b"\0"):
        if not encoded_path:
            continue
        relative = encoded_path.decode("utf-8")
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink(), relative
        inventory[relative] = _entry(path.read_bytes(), path.stat().st_mode)
    return inventory


def _source_inventory() -> dict[str, tuple[bytes, int]]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--",
            "obsidian_wiki/_data/skills",
            "obsidian_wiki/_data/bootstrap",
            "obsidian_wiki/_data/adapter",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=30,
    )
    inventory: dict[str, tuple[bytes, int]] = {}
    for encoded_path in completed.stdout.split(b"\0"):
        if not encoded_path:
            continue
        path = ROOT / encoded_path.decode("utf-8")
        assert path.is_file() and not path.is_symlink(), path
        relative = path.relative_to(DATA).as_posix()
        inventory[relative] = (path.read_bytes(), path.stat().st_mode & 0o777)
    return inventory


def _wheel_inventory(wheel: Path) -> dict[str, tuple[bytes, int]]:
    inventory: dict[str, tuple[bytes, int]] = {}
    prefix = "obsidian_wiki/_data/"
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            relative = info.filename[len(prefix) :]
            if relative.split("/", 1)[0] not in ASSET_NAMES:
                continue
            assert relative not in inventory, relative
            inventory[relative] = (
                archive.read(info),
                (info.external_attr >> 16) & 0o777,
            )
    return inventory


def _sdist_inventory(sdist: Path) -> dict[str, tuple[bytes, int]]:
    inventory: dict[str, tuple[bytes, int]] = {}
    marker = "/obsidian_wiki/_data/"
    with tarfile.open(sdist) as archive:
        for member in archive.getmembers():
            if not member.isfile() or marker not in member.name:
                continue
            relative = member.name.split(marker, 1)[1]
            if relative.split("/", 1)[0] not in ASSET_NAMES:
                continue
            assert relative not in inventory, relative
            stream = archive.extractfile(member)
            assert stream is not None
            inventory[relative] = (stream.read(), member.mode & 0o777)
    return inventory


def _wheel_paths(wheel: Path) -> set[str]:
    with zipfile.ZipFile(wheel) as archive:
        return {info.filename for info in archive.infolist() if not info.is_dir()}


def _sdist_paths(sdist: Path) -> set[str]:
    with tarfile.open(sdist) as archive:
        return {
            "/".join(Path(member.name).parts[1:])
            for member in archive.getmembers()
            if member.isfile() and len(Path(member.name).parts) > 1
        }


def _build(*arguments: str, cwd: Path) -> None:
    subprocess.run(
        ["uv", "build", "--quiet", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required for builds")
def test_distribution_assets_exactly_match_canonical_package_data(
    tmp_path: Path,
) -> None:
    direct_dir = tmp_path / "direct"
    repeated_dir = tmp_path / "repeated"
    sdist_dir = tmp_path / "sdist"
    rebuilt_dir = tmp_path / "rebuilt"
    _build("--wheel", "--out-dir", str(direct_dir), str(ROOT), cwd=ROOT)
    _build("--wheel", "--out-dir", str(repeated_dir), str(ROOT), cwd=ROOT)
    _build("--sdist", "--out-dir", str(sdist_dir), str(ROOT), cwd=ROOT)
    sdist_files = tuple(sdist_dir.glob("*.tar.gz"))
    assert len(sdist_files) == 1
    _build(
        "--wheel",
        "--out-dir",
        str(rebuilt_dir),
        str(sdist_files[0]),
        cwd=ROOT,
    )

    direct_wheels = tuple(direct_dir.glob("*.whl"))
    repeated_wheels = tuple(repeated_dir.glob("*.whl"))
    rebuilt_wheels = tuple(rebuilt_dir.glob("*.whl"))
    assert len(direct_wheels) == 1
    assert len(repeated_wheels) == 1
    assert len(rebuilt_wheels) == 1
    direct_inventory = _normalized_wheel_inventory(direct_wheels[0])
    repeated_inventory = _normalized_wheel_inventory(repeated_wheels[0])
    rebuilt_inventory = _normalized_wheel_inventory(rebuilt_wheels[0])
    sdist_inventory = _normalized_sdist_inventory(sdist_files[0])
    assert direct_inventory == repeated_inventory
    assert rebuilt_inventory == direct_inventory
    source_package = _source_package_inventory()
    source_distribution = _source_distribution_inventory()
    assert source_package
    assert set(sdist_inventory) - set(source_distribution) == {"PKG-INFO"}
    assert {
        path: sdist_inventory[path] for path in source_distribution
    } == source_distribution
    assert {
        path: direct_inventory[path] for path in source_package
    } == source_package
    assert {path: sdist_inventory[path] for path in source_package} == source_package
    expected_python = {
        path for path in source_package if path.endswith(".py")
    }
    assert expected_python == {
        path
        for path in direct_inventory
        if path.startswith("obsidian_wiki/") and path.endswith(".py")
    }
    for metadata in ("METADATA", "WHEEL", "entry_points.txt", "RECORD"):
        assert any(
            path.endswith(f".dist-info/{metadata}") for path in direct_inventory
        ), metadata
    expected = _source_inventory()
    assert expected
    artifact_inventories = {
        "direct wheel": _wheel_inventory(direct_wheels[0]),
        "sdist": _sdist_inventory(sdist_files[0]),
        "rebuilt wheel": _wheel_inventory(rebuilt_wheels[0]),
    }
    for artifact, inventory in artifact_inventories.items():
        assert inventory == expected
        canonical = inventory["skills/llm-wiki/SKILL.md"][0].decode("utf-8")
        canonical_flat = " ".join(canonical.split())
        assert "owner review described above" not in canonical, artifact
        for required in (
            "Agent substantive review",
            "exact-path local authority checkpoint",
            "one exact-path local result commit",
        ):
            assert required in canonical_flat, (artifact, required)
        lifecycle_paths = (
            "skills/wiki-capture/SKILL.md",
            "skills/wiki-ingest/SKILL.md",
            "skills/wiki-import/SKILL.md",
            "skills/wiki-research/SKILL.md",
            "skills/claude-history-ingest/SKILL.md",
            "skills/codex-history-ingest/SKILL.md",
            "skills/copilot-history-ingest/SKILL.md",
            "skills/hermes-history-ingest/SKILL.md",
            "skills/openclaw-history-ingest/SKILL.md",
            "skills/pi-history-ingest/SKILL.md",
            "skills/wiki-agent/SKILL.md",
        )
        for relative in lifecycle_paths:
            contract = inventory[relative][0].decode("utf-8")
            for required in (
                "<wiki-cli> check --json --pretty",
                "vault-relative `log_path`",
                "one exact-path local result commit",
            ):
                assert required in contract, (artifact, relative, required)
        for relative, (contents, _) in inventory.items():
            if Path(relative).suffix in {".md", ".mdc"}:
                assert b"\r" not in contents, f"{artifact}:{relative}"
    assert PROTOCOL_BOOTSTRAP_PATHS <= set(expected)
    for relative, (contents, _) in expected.items():
        assert not FORMER_PROTOCOL_RESOURCE.search(contents), relative
    artifact_paths = (
        (f"direct:{direct_wheels[0].name}", _wheel_paths(direct_wheels[0])),
        (f"sdist:{sdist_files[0].name}", _sdist_paths(sdist_files[0])),
        (f"rebuilt:{rebuilt_wheels[0].name}", _wheel_paths(rebuilt_wheels[0])),
    )
    for artifact, paths in artifact_paths:
        assert not _archive_path_protocol_violations(paths), artifact
        assert {
            f"obsidian_wiki/_data/{relative}" for relative in PROTOCOL_BOOTSTRAP_PATHS
        } <= paths, artifact
        assert not any(
            path.endswith(("obsidian-wiki.md", "obsidian-wiki.mdc"))
            for path in paths
        ), artifact
        assert paths.isdisjoint(REMOVED_DISTRIBUTION_PATHS), artifact
        assert not {
            path
            for path in paths
            if path.startswith(REMOVED_DISTRIBUTION_PREFIXES)
        }, artifact

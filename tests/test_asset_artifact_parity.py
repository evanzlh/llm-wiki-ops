from __future__ import annotations

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "obsidian_wiki" / "_data"
ASSET_NAMES = ("skills", "bootstrap")
REMOVED_DISTRIBUTION_PATHS = {
    ".claude/hooks/wiki-stop-capture.sh",
    ".claude/settings.json",
    ".github/workflows/publish.yml",
    ".github/workflows/setup.yml",
    "README_TW.md",
    "SETUP.md",
    "obsidian_wiki/_data/skills/wiki-capture/references/RAW-FORMAT.md",
    "obsidian_wiki/migration.py",
    "obsidian_wiki/sync.py",
    "scripts/com.obsidian-wiki.daily-update.plist",
    "scripts/daily-update.sh",
    "scripts/wiki-notify.sh",
    "setup.sh",
    "tests/test_portable_migration.py",
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


def _source_inventory() -> dict[str, tuple[bytes, int]]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--",
            "obsidian_wiki/_data/skills",
            "obsidian_wiki/_data/bootstrap",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
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
        timeout=180,
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required for builds")
def test_distribution_assets_exactly_match_canonical_package_data(
    tmp_path: Path,
) -> None:
    direct_dir = tmp_path / "direct"
    sdist_dir = tmp_path / "sdist"
    rebuilt_dir = tmp_path / "rebuilt"
    _build("--wheel", "--out-dir", str(direct_dir), str(ROOT), cwd=ROOT)
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
    rebuilt_wheels = tuple(rebuilt_dir.glob("*.whl"))
    assert len(direct_wheels) == 1
    assert len(rebuilt_wheels) == 1
    expected = _source_inventory()
    assert expected
    assert _wheel_inventory(direct_wheels[0]) == expected
    assert _sdist_inventory(sdist_files[0]) == expected
    assert _wheel_inventory(rebuilt_wheels[0]) == expected
    artifact_paths = (
        (f"direct:{direct_wheels[0].name}", _wheel_paths(direct_wheels[0])),
        (f"sdist:{sdist_files[0].name}", _sdist_paths(sdist_files[0])),
        (f"rebuilt:{rebuilt_wheels[0].name}", _wheel_paths(rebuilt_wheels[0])),
    )
    for artifact, paths in artifact_paths:
        assert paths.isdisjoint(REMOVED_DISTRIBUTION_PATHS), artifact
        assert not {
            path
            for path in paths
            if path.startswith(REMOVED_DISTRIBUTION_PREFIXES)
        }, artifact

from __future__ import annotations

import os
from pathlib import Path

import pytest

import obsidian_wiki.safe_files as safe_files


def test_scan_markdown_files_reads_an_ordinary_nested_tree(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    page = vault / "concepts/page.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Page\n", encoding="utf-8")

    snapshots = safe_files.scan_markdown_files(vault)

    assert [item.relative for item in snapshots] == ["concepts/page.md"]
    assert snapshots[0].text() == "# Page\n"


def test_scan_markdown_files_rejects_symlink_in_vault_root_ancestry(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    vault = outside / "wiki"
    vault.mkdir(parents=True)
    (vault / "secret.md").write_text("SECRET-MARKER\n", encoding="utf-8")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink") as raised:
        safe_files.scan_markdown_files(linked_parent / "wiki")

    assert "SECRET-MARKER" not in str(raised.value)


def test_scan_markdown_files_rejects_platform_without_bound_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    monkeypatch.setattr(safe_files, "_SUPPORTS_BOUND_SCAN", False)

    with pytest.raises(RuntimeError, match="not supported"):
        safe_files.scan_markdown_files(vault)


def test_scan_markdown_files_ignores_non_markdown_special_and_hardlinked_files(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    ordinary = vault / "page.md"
    ordinary.write_text("# Page\n", encoding="utf-8")
    external = tmp_path / "image.png"
    external.write_bytes(b"image")
    try:
        os.link(external, vault / "image.png")
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    if hasattr(os, "mkfifo"):
        os.mkfifo(vault / "ignored.txt")

    snapshots = safe_files.scan_markdown_files(vault)

    assert [item.relative for item in snapshots] == ["page.md"]


@pytest.mark.parametrize("kind", ["external", "dangling", "intermediate"])
def test_scan_markdown_files_rejects_symlinks_without_reading_targets(
    tmp_path: Path, kind: str
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    secret = external / "secret.md"
    secret.write_text("SECRET-MARKER\n", encoding="utf-8")
    if kind == "intermediate":
        (vault / "_raw").symlink_to(external, target_is_directory=True)
    else:
        raw = vault / "_raw"
        raw.mkdir()
        target = secret if kind == "external" else external / "missing.md"
        (raw / "leak.md").symlink_to(target)

    with pytest.raises(RuntimeError, match="symlink") as raised:
        safe_files.scan_markdown_files(vault)

    assert "SECRET-MARKER" not in str(raised.value)


def test_scan_markdown_files_rejects_hardlinks(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    raw = vault / "_raw"
    raw.mkdir(parents=True)
    external = tmp_path / "secret.md"
    external.write_text("SECRET-MARKER\n", encoding="utf-8")
    try:
        os.link(external, raw / "leak.md")
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")

    with pytest.raises(RuntimeError, match="hard-link") as raised:
        safe_files.scan_markdown_files(vault)

    assert "SECRET-MARKER" not in str(raised.value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
@pytest.mark.parametrize("name", ["blocked.md", "hot.md"])
def test_scan_markdown_files_rejects_fifo_before_open(
    tmp_path: Path, name: str
) -> None:
    vault = tmp_path / "wiki"
    raw = vault / "_raw"
    raw.mkdir(parents=True)
    os.mkfifo(raw / name)

    with pytest.raises(RuntimeError, match="special"):
        safe_files.scan_markdown_files(vault, skip_files={"hot.md"})

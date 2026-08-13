from __future__ import annotations

import os
from pathlib import Path

import pytest

from obsidian_wiki.safe_files import (
    read_markdown_snapshot,
    read_safe_file_snapshot,
    scan_markdown_headers,
    verify_safe_file_snapshot,
)


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    probe = link.parent / ".symlink-probe"
    try:
        probe.symlink_to(target, target_is_directory=directory)
        probe.unlink()
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        try:
            probe.unlink()
        except OSError:
            pass
        pytest.skip(f"symlinks are unavailable: {exc}")


@pytest.mark.skipif(os.name != "posix", reason="POSIX bound-read capability")
def test_safe_file_snapshot_rejects_repository_root_rebinding(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    configuration = repository / ".obsidian-wiki" / "config.toml"
    configuration.parent.mkdir(parents=True)
    configuration.write_text("original", encoding="utf-8")
    snapshot = read_safe_file_snapshot(repository, configuration)
    assert snapshot is not None

    moved = tmp_path / "original-repository"
    repository.rename(moved)
    replacement = repository / ".obsidian-wiki" / "config.toml"
    replacement.parent.mkdir(parents=True)
    replacement.write_text("replacement", encoding="utf-8")

    with pytest.raises(RuntimeError, match="root changed since file was read"):
        verify_safe_file_snapshot(snapshot)


def test_header_scan_reads_only_frontmatter_until_eligible_full_read(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    page = vault / "private.md"
    page.write_text(
        "---\ntags: [visibility/internal]\nsummary: Metadata\n---\n"
        "PRIVATE-BODY-SENTINEL\n",
        encoding="utf-8",
    )

    header = scan_markdown_headers(vault)[0]

    assert b"visibility/internal" in header.content
    assert b"PRIVATE-BODY-SENTINEL" not in header.content
    assert b"PRIVATE-BODY-SENTINEL" in read_markdown_snapshot(header).content


def test_full_read_rejects_identity_change_after_metadata_scan(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    page = vault / "page.md"
    page.write_text("---\ntags: [public]\n---\nOriginal\n", encoding="utf-8")
    header = scan_markdown_headers(vault)[0]
    page.write_text("---\ntags: [public]\n---\nChanged body and size\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed since metadata"):
        read_markdown_snapshot(header)


def test_header_scan_rejects_unbounded_frontmatter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "page.md").write_text("---\nsummary: " + "x" * 100 + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="header exceeds"):
        scan_markdown_headers(vault, max_header_bytes=32)


def test_header_scan_does_not_treat_indented_block_content_as_delimiter(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "page.md").write_bytes(
        b"---\nsummary: |-\n  first line\n  ---\n  final line\ntags: [public]\n---\n"
        b"PRIVATE-BODY-SENTINEL\n"
    )

    header = scan_markdown_headers(vault)[0]

    assert b"  ---\n  final line\ntags: [public]\n---\n" in header.content
    assert b"PRIVATE-BODY-SENTINEL" not in header.content


def test_header_scan_accepts_exact_column_zero_crlf_delimiters(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "page.md").write_bytes(
        b"---\r\ntags:\r\n  - public\r\n---\r\nPRIVATE-BODY-SENTINEL\r\n"
    )

    header = scan_markdown_headers(vault)[0]

    # The scanner stops on CR so CR-only metadata cannot consume a body byte;
    # a following LF is deliberately left unread with the closing descriptor.
    assert header.content.endswith(b"---\r")
    assert b"PRIVATE-BODY-SENTINEL" not in header.content


def test_header_scan_stops_at_cr_only_column_zero_delimiter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "page.md").write_bytes(
        b"---\rsummary: |-\r  first line\r  ---\r  final line\r"
        b"tags: [visibility/internal]\r---\rPRIVATE-BODY-SENTINEL\r"
    )

    header = scan_markdown_headers(vault)[0]

    assert header.content.endswith(b"---\r")
    assert b"  ---\r  final line\r" in header.content
    assert b"PRIVATE-BODY-SENTINEL" not in header.content

import obsidian_wiki.safe_files as safe_files


def test_scan_markdown_files_reads_an_ordinary_nested_tree(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    page = vault / "concepts/page.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Page\n", encoding="utf-8")

    snapshots = safe_files.scan_markdown_files(vault)

    assert [item.relative for item in snapshots] == ["concepts/page.md"]
    assert snapshots[0].text() == "# Page\n"


@pytest.mark.parametrize("kind", ["ordinary", "symlink", "fifo"])
def test_scan_markdown_files_prunes_exact_relative_subtree_before_inspection(
    tmp_path: Path, kind: str
) -> None:
    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable")
    vault = tmp_path / "wiki"
    sibling = vault / "archive" / "operations" / "page.md"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("# Sibling\n", encoding="utf-8")
    legacy = vault / "journal" / "operations"
    legacy.parent.mkdir()
    if kind == "ordinary":
        legacy.mkdir()
        (legacy / "malformed.md").write_text("# Legacy\n", encoding="utf-8")
    elif kind == "symlink":
        external = tmp_path / "external"
        external.mkdir()
        _symlink_or_skip(legacy, external, directory=True)
    else:
        os.mkfifo(legacy)

    snapshots = safe_files.scan_markdown_files(
        vault, skip_relative_subtrees={"journal/operations"}
    )

    assert [snapshot.relative for snapshot in snapshots] == [
        "archive/operations/page.md"
    ]


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "/journal/operations",
        "journal/./operations",
        "../operations",
        "journal\\operations",
        "journal/\x00operations",
    ],
)
def test_scan_markdown_files_rejects_invalid_relative_subtree(
    tmp_path: Path, relative: str
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()

    with pytest.raises(ValueError, match="skip subtree"):
        safe_files.scan_markdown_files(vault, skip_relative_subtrees={relative})


def test_scan_markdown_files_default_rejects_unsafe_unskipped_subtree(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "wiki"
    legacy = vault / "journal" / "operations"
    legacy.parent.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    _symlink_or_skip(legacy, external, directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        safe_files.scan_markdown_files(vault)


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


@pytest.mark.parametrize("name", ["unrelated.json", "image.png"])
def test_scan_markdown_files_ignores_terminal_non_markdown_symlinks(
    tmp_path: Path, name: str
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    (vault / "page.md").write_text("# Page\n", encoding="utf-8")
    secret = tmp_path / name
    secret.write_text("SECRET-MARKER\n", encoding="utf-8")
    (vault / name).symlink_to(secret)

    snapshots = safe_files.scan_markdown_files(vault)

    assert [item.relative for item in snapshots] == ["page.md"]
    assert "SECRET-MARKER" not in snapshots[0].text()


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


def test_read_safe_file_reads_an_ordinary_file(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    target = vault / "_meta/trust-ledger.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"schema_version": 1}\n')

    assert safe_files.read_safe_file(vault, target) == b'{"schema_version": 1}\n'


@pytest.mark.parametrize("kind", ["terminal_symlink", "intermediate_symlink", "hardlink"])
def test_read_safe_file_rejects_links_before_reading(
    tmp_path: Path, kind: str
) -> None:
    vault = tmp_path / "wiki"
    meta = vault / "_meta"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.json"
    secret.write_text("SECRET-MARKER\n", encoding="utf-8")
    if kind == "intermediate_symlink":
        meta.symlink_to(outside, target_is_directory=True)
        target = meta / "secret.json"
    else:
        meta.mkdir()
        target = meta / "trust-ledger.json"
        if kind == "terminal_symlink":
            target.symlink_to(secret)
        else:
            try:
                os.link(secret, target)
            except OSError as exc:
                pytest.skip(f"hardlinks unavailable: {exc}")

    with pytest.raises(safe_files.UnsafeVaultError) as raised:
        safe_files.read_safe_file(vault, target)

    assert "SECRET-MARKER" not in str(raised.value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_read_safe_file_rejects_fifo_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "wiki"
    target = vault / "_meta/trust-ledger.json"
    target.parent.mkdir(parents=True)
    os.mkfifo(target)
    real_open = safe_files.os.open

    def guarded_open(path: object, *args: object, **kwargs: object) -> int:
        if path == target.name:
            raise AssertionError("FIFO was opened")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(safe_files.os, "open", guarded_open)
    with pytest.raises(safe_files.UnsafeVaultError, match="ordinary file"):
        safe_files.read_safe_file(vault, target)


def test_scan_wraps_read_oserror_and_closes_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    (vault / "page.md").write_text("# Page\n", encoding="utf-8")
    opened: list[int] = []
    closed: list[int] = []
    real_open = safe_files.os.open
    real_close = safe_files.os.close

    def tracking_open(*args: object, **kwargs: object) -> int:
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def tracking_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    def denied_read(descriptor: int, size: int) -> bytes:
        raise PermissionError("denied by test")

    monkeypatch.setattr(safe_files.os, "open", tracking_open)
    monkeypatch.setattr(safe_files.os, "close", tracking_close)
    monkeypatch.setattr(safe_files.os, "read", denied_read)

    with pytest.raises(safe_files.UnsafeVaultError, match=r"page\.md.*read") as raised:
        safe_files.scan_markdown_files(vault)

    assert isinstance(raised.value.__cause__, PermissionError)
    assert set(opened) <= set(closed)


def test_scan_wraps_attachment_stat_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    (vault / "page.md").write_text("# Page\n", encoding="utf-8")
    real_stat = safe_files.os.stat
    page_stats = 0

    def denied_attachment(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal page_stats
        if path == "page.md":
            page_stats += 1
            if page_stats == 2:
                raise PermissionError("denied by test")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(safe_files.os, "stat", denied_attachment)

    with pytest.raises(safe_files.UnsafeVaultError, match=r"page\.md.*stat") as raised:
        safe_files.scan_markdown_files(vault)

    assert isinstance(raised.value.__cause__, PermissionError)


def test_scan_wraps_file_fstat_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    (vault / "page.md").write_text("# Page\n", encoding="utf-8")
    real_fstat = safe_files.os.fstat

    def denied_file_fstat(descriptor: int) -> os.stat_result:
        metadata = real_fstat(descriptor)
        if os.path.isfile(f"/proc/self/fd/{descriptor}"):
            raise PermissionError("denied by test")
        return metadata

    monkeypatch.setattr(safe_files.os, "fstat", denied_file_fstat)

    with pytest.raises(safe_files.UnsafeVaultError, match=r"page\.md.*fstat") as raised:
        safe_files.scan_markdown_files(vault)

    assert isinstance(raised.value.__cause__, PermissionError)


def test_scan_wraps_file_open_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    (vault / "page.md").write_text("# Page\n", encoding="utf-8")
    real_open = safe_files.os.open

    def denied_file_open(path: object, *args: object, **kwargs: object) -> int:
        if path == "page.md":
            raise PermissionError("denied by test")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(safe_files.os, "open", denied_file_open)

    with pytest.raises(safe_files.UnsafeVaultError, match=r"page\.md.*open") as raised:
        safe_files.scan_markdown_files(vault)

    assert isinstance(raised.value.__cause__, PermissionError)


def test_scan_close_failure_still_closes_every_opened_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    opened: list[int] = []
    closed: list[int] = []
    real_open = safe_files.os.open
    real_close = safe_files.os.close
    close_attempts = 0

    def tracking_open(*args: object, **kwargs: object) -> int:
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def fail_first_close(descriptor: int) -> None:
        nonlocal close_attempts
        close_attempts += 1
        if close_attempts == 1:
            raise PermissionError("denied by test")
        real_close(descriptor)
        closed.append(descriptor)

    monkeypatch.setattr(safe_files.os, "open", tracking_open)
    monkeypatch.setattr(safe_files.os, "close", fail_first_close)

    with pytest.raises(safe_files.UnsafeVaultError, match="close failed"):
        safe_files.scan_markdown_files(vault)

    assert set(opened) <= set(closed)

from pathlib import Path

import os

import pytest


def write_skill(root: Path, name: str, description: str = "Use this skill.") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: " + name + "\ndescription: " + description + "\n---\n",
        encoding="utf-8",
    )
    return skill


def test_discovers_metadata_and_preserves_unicode_binary_and_executable(tmp_path: Path) -> None:
    from obsidian_wiki.skill_trees import discover_skill_collection

    skill = write_skill(tmp_path, "example", "An example skill.")
    resource = skill / "references" / "中文.bin"
    resource.parent.mkdir()
    resource.write_bytes(b"\x00\xffwiki\r\n")
    script = skill / "scripts" / "run.sh"
    script.parent.mkdir()
    script.write_bytes(b"#!/bin/sh\r\necho hi\r\n")
    script.chmod(0o755)

    collection = discover_skill_collection(tmp_path)

    assert collection.names == ("example",)
    tree = collection.skills[0]
    assert tree.name == "example"
    assert tree.description == "An example skill."
    assert tree.digest.startswith("sha256:")
    assert len(tree.digest) == len("sha256:") + 64
    assert [(entry.path, entry.kind, entry.executable, entry.content) for entry in tree.entries] == [
        ("SKILL.md", "file", False, b"---\nname: example\ndescription: An example skill.\n---\n"),
        ("references", "directory", False, b""),
        ("references/中文.bin", "file", False, b"\x00\xffwiki\r\n"),
        ("scripts", "directory", False, b""),
        ("scripts/run.sh", "file", True, b"#!/bin/sh\r\necho hi\r\n"),
    ]


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("not frontmatter\n", "frontmatter"),
        ("---\nname: example\n---\n", "required"),
        ("---\nname: other\ndescription: Use this skill.\n---\n", "equal"),
    ],
)
def test_rejects_invalid_skill_metadata(tmp_path: Path, contents: str, message: str) -> None:
    skill = write_skill(tmp_path, "example")
    (skill / "SKILL.md").write_text(contents, encoding="utf-8")

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match=message):
        discover_skill_collection(tmp_path)


def test_rejects_source_and_nested_symlinks(tmp_path: Path) -> None:
    from obsidian_wiki.skill_trees import discover_skill_collection

    target = tmp_path / "target"
    write_skill(target, "target")
    source_link = tmp_path / "linked"
    try:
        source_link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))
    with pytest.raises(ValueError):
        discover_skill_collection(tmp_path)

    source_link.unlink()
    skill = write_skill(tmp_path, "example")
    (skill / "resource-link").symlink_to(target / "target" / "SKILL.md")
    with pytest.raises(ValueError, match="symbolic"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize("path", ["SKILL.md", "nested.txt"])
def test_rejects_multiply_linked_regular_files(tmp_path: Path, path: str) -> None:
    from obsidian_wiki.skill_trees import discover_skill_collection

    skill = write_skill(tmp_path, "example")
    target = skill / path
    if path != "SKILL.md":
        target.write_bytes(b"nested")
    try:
        os.link(target, skill / (path + ".linked"))
    except OSError as exc:
        pytest.skip("hard links unavailable: {}".format(exc))
    with pytest.raises(ValueError, match="multiply-linked"):
        discover_skill_collection(tmp_path)


def test_rejects_unsafe_top_level_skill_name(tmp_path: Path) -> None:
    write_skill(tmp_path, "bad name")

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="unsafe"):
        discover_skill_collection(tmp_path)


def test_ignore_mode_excludes_only_declared_source_artifacts(tmp_path: Path) -> None:
    skill = write_skill(tmp_path, "example")
    (skill / ".git").mkdir()
    (skill / ".git" / "config").write_bytes(b"ignored")
    (skill / "__pycache__").mkdir()
    (skill / "__pycache__" / "cache.pyc").write_bytes(b"ignored")
    (skill / ".DS_Store").write_bytes(b"ignored")
    (skill / ".env.local").write_bytes(b"ignored")
    (skill / "._resource").write_bytes(b"ignored")
    (skill / "bytecode.pyc").write_bytes(b"ignored")
    (skill / "legitimate-resource").mkdir()
    (skill / "legitimate-resource" / "data").write_bytes(b"kept")

    from obsidian_wiki.skill_trees import discover_skill_collection

    collection = discover_skill_collection(tmp_path, ignore_source_artifacts=True)
    assert collection.names == ("example",)
    assert [entry.path for entry in collection.skills[0].entries] == [
        "SKILL.md",
        "legitimate-resource",
        "legitimate-resource/data",
    ]


def test_rejects_special_files_where_supported(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes unavailable")
    skill = write_skill(tmp_path, "example")
    special = skill / "pipe"
    try:
        os.mkfifo(special)
    except OSError as exc:
        pytest.skip("named pipes unavailable: {}".format(exc))

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="special"):
        discover_skill_collection(tmp_path)


def test_digest_changes_for_exact_bytes_and_executable_bit(tmp_path: Path) -> None:
    skill = write_skill(tmp_path, "example")
    resource = skill / "resource"
    resource.write_bytes(b"one")

    from obsidian_wiki.skill_trees import discover_skill_collection

    original = discover_skill_collection(tmp_path).skills[0].digest
    resource.write_bytes(b"two")
    bytes_changed = discover_skill_collection(tmp_path).skills[0].digest
    resource.chmod(0o755)
    executable_changed = discover_skill_collection(tmp_path).skills[0].digest

    assert original != bytes_changed
    assert bytes_changed != executable_changed


def test_materializes_identical_snapshots_and_rejects_existing_destination(tmp_path: Path) -> None:
    skill = write_skill(tmp_path / "source", "example")
    script = skill / "scripts" / "run.sh"
    script.parent.mkdir()
    script.write_bytes(b"#!/bin/sh\r\n")
    script.chmod(0o755)

    from obsidian_wiki.skill_trees import (
        discover_skill_collection,
        materialize_skill_collection,
    )

    collection = discover_skill_collection(tmp_path / "source")
    first = tmp_path / "first"
    second = tmp_path / "second"
    materialize_skill_collection(collection, first)
    materialize_skill_collection(collection, second)

    assert discover_skill_collection(first) == collection
    assert discover_skill_collection(second) == collection
    assert (first / "example" / "scripts" / "run.sh").stat().st_mode & 0o111
    with pytest.raises(ValueError, match="already exists"):
        materialize_skill_collection(collection, first)


def test_compare_reports_deterministic_added_changed_and_removed_paths(tmp_path: Path) -> None:
    canonical_root = tmp_path / "canonical"
    mirror_root = tmp_path / "mirror"
    canonical = write_skill(canonical_root, "alpha")
    mirror = write_skill(mirror_root, "alpha")
    (canonical / "added").write_bytes(b"canonical")
    (canonical / "changed").write_bytes(b"canonical")
    (mirror / "changed").write_bytes(b"mirror")
    (mirror / "removed").write_bytes(b"mirror")
    write_skill(canonical_root, "beta")
    write_skill(mirror_root, "gamma")

    from obsidian_wiki.skill_trees import (
        compare_skill_collections,
        discover_skill_collection,
    )

    result = compare_skill_collections(
        discover_skill_collection(canonical_root),
        discover_skill_collection(mirror_root),
    )

    assert result == (
        {"alpha": ("added",), "beta": ("SKILL.md",)},
        {"alpha": ("changed",)},
        {"alpha": ("removed",), "gamma": ("SKILL.md",)},
    )

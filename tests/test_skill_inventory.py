from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID
from obsidian_wiki.skill_inventory import (
    MANAGED_SKILLS_INVENTORY,
    MIRROR_FORMAT,
    SCHEMA_VERSION,
    LegacyManagedSkillsInventory,
    ManagedSkillsInventory,
    parse_inventory_text,
    read_inventory,
    render_inventory,
)

DIGEST_1 = "sha256:" + "1" * 64
DIGEST_2 = "sha256:" + "2" * 64
EXPECTED = {
    "implementation": IMPLEMENTATION_ID,
    "managed_skill_digests": {
        "wiki-ingest": DIGEST_1,
        "wiki-query": DIGEST_2,
    },
    "managed_skills": ["wiki-ingest", "wiki-query"],
    "mirror_format": "full-copy-v1",
    "schema_version": 2,
    "skills_version": "2026.8.3",
}


def make_inventory() -> ManagedSkillsInventory:
    return ManagedSkillsInventory(
        skills_version="2026.8.3",
        managed_skills=("wiki-ingest", "wiki-query"),
        managed_skill_digests={
            "wiki-ingest": DIGEST_1,
            "wiki-query": DIGEST_2,
        },
    )


def test_v2_inventory_round_trip_uses_canonical_json() -> None:
    inventory = make_inventory()

    rendered = render_inventory(inventory)

    assert json.loads(rendered) == EXPECTED
    assert (
        rendered
        == json.dumps(EXPECTED, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    assert rendered.endswith("\n") and not rendered.endswith("\n\n")
    assert parse_inventory_text(rendered) == inventory


def test_inventory_values_are_immutable() -> None:
    inventory = make_inventory()

    with pytest.raises(FrozenInstanceError):
        inventory.skills_version = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        inventory.managed_skill_digests["wiki-ingest"] = DIGEST_2  # type: ignore[index]


def test_legacy_inventory_requires_explicit_opt_in_and_is_typed() -> None:
    text = json.dumps(
        {
            "implementation": IMPLEMENTATION_ID,
            "skills": ["wiki-ingest"],
            "skills_version": "2026.8.3",
        }
    )

    with pytest.raises(ValueError, match="legacy"):
        parse_inventory_text(text)

    legacy = parse_inventory_text(text, allow_legacy=True)
    assert legacy == LegacyManagedSkillsInventory(
        skills_version="2026.8.3", managed_skills=("wiki-ingest",)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("mirror_format"),
        lambda payload: payload.__setitem__("unexpected", True),
        lambda payload: payload.__setitem__("implementation", "other/wiki"),
        lambda payload: payload.__setitem__("mirror_format", "adapter-v1"),
        lambda payload: payload.__setitem__("schema_version", 3),
        lambda payload: payload.__setitem__("schema_version", True),
        lambda payload: payload.__setitem__("schema_version", 2.0),
        lambda payload: payload.__setitem__("skills_version", ""),
        lambda payload: payload.__setitem__("skills_version", 2026),
    ],
)
def test_v2_rejects_unknown_missing_or_wrong_scalar_fields(mutation) -> None:
    payload = dict(EXPECTED)
    mutation(payload)

    with pytest.raises(ValueError):
        parse_inventory_text(json.dumps(payload))


@pytest.mark.parametrize(
    "managed_skills",
    [
        "wiki-ingest",
        ["wiki-query", "wiki-ingest"],
        ["wiki-ingest", "wiki-ingest"],
        ["../wiki-ingest"],
        ["wiki/ingest"],
        [r"wiki\ingest"],
        [".hidden"],
        ["技能"],
        [True],
    ],
)
def test_v2_rejects_invalid_managed_skill_names(managed_skills: object) -> None:
    payload = dict(EXPECTED)
    payload["managed_skills"] = managed_skills

    with pytest.raises(ValueError, match="managed_skills|skill name"):
        parse_inventory_text(json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize(
    "digests",
    [
        [],
        {"wiki-ingest": DIGEST_1},
        {"wiki-ingest": DIGEST_1, "wiki-query": DIGEST_2, "extra": DIGEST_1},
        {"wiki-ingest": DIGEST_1, "wiki-query": True},
        {"wiki-ingest": DIGEST_1, "wiki-query": "sha256:" + "A" * 64},
        {"wiki-ingest": DIGEST_1, "wiki-query": "sha256:" + "2" * 63},
        {"wiki-ingest": DIGEST_1, "wiki-query": "md5:" + "2" * 64},
    ],
)
def test_v2_rejects_invalid_digest_mapping(digests: object) -> None:
    payload = dict(EXPECTED)
    payload["managed_skill_digests"] = digests

    with pytest.raises(ValueError, match="digest"):
        parse_inventory_text(json.dumps(payload))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        "null",
        '{"schema_version": 2,}',
        '{"schema_version": 2, "schema_version": 2}',
    ],
)
def test_parser_rejects_malformed_json_types_and_duplicate_keys(text: str) -> None:
    with pytest.raises(ValueError):
        parse_inventory_text(text)


def test_parser_rejects_duplicate_top_level_key_in_otherwise_valid_v2() -> None:
    members = [
        '"implementation": ' + json.dumps(IMPLEMENTATION_ID),
        '"implementation": ' + json.dumps(IMPLEMENTATION_ID),
        '"managed_skill_digests": ' + json.dumps(EXPECTED["managed_skill_digests"]),
        '"managed_skills": ' + json.dumps(EXPECTED["managed_skills"]),
        '"mirror_format": ' + json.dumps(EXPECTED["mirror_format"]),
        '"schema_version": 2',
        '"skills_version": ' + json.dumps(EXPECTED["skills_version"]),
    ]

    with pytest.raises(ValueError, match="duplicate"):
        parse_inventory_text("{" + ",".join(members) + "}")


def test_parser_rejects_duplicate_digest_key_in_otherwise_valid_v2() -> None:
    digests = (
        '{"wiki-ingest": '
        + json.dumps(DIGEST_1)
        + ', "wiki-ingest": '
        + json.dumps(DIGEST_1)
        + ', "wiki-query": '
        + json.dumps(DIGEST_2)
        + "}"
    )
    members = [
        '"implementation": ' + json.dumps(IMPLEMENTATION_ID),
        '"managed_skill_digests": ' + digests,
        '"managed_skills": ' + json.dumps(EXPECTED["managed_skills"]),
        '"mirror_format": ' + json.dumps(EXPECTED["mirror_format"]),
        '"schema_version": 2',
        '"skills_version": ' + json.dumps(EXPECTED["skills_version"]),
    ]

    with pytest.raises(ValueError, match="duplicate"):
        parse_inventory_text("{" + ",".join(members) + "}")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("skills"),
        lambda payload: payload.__setitem__("unexpected", False),
        lambda payload: payload.__setitem__("implementation", "other/wiki"),
        lambda payload: payload.__setitem__("skills_version", ""),
        lambda payload: payload.__setitem__("skills_version", 2026),
        lambda payload: payload.__setitem__("skills", "wiki-ingest"),
        lambda payload: payload.__setitem__("skills", ["wiki-query", "wiki-ingest"]),
        lambda payload: payload.__setitem__("skills", ["wiki-ingest", "wiki-ingest"]),
        lambda payload: payload.__setitem__("skills", ["../wiki-ingest"]),
        lambda payload: payload.__setitem__("skills", ["技能"]),
        lambda payload: payload.__setitem__("skills", [True]),
    ],
)
def test_legacy_rejects_every_noncanonical_shape(mutation) -> None:
    payload = {
        "implementation": IMPLEMENTATION_ID,
        "skills": ["wiki-ingest", "wiki-query"],
        "skills_version": "2026.8.3",
    }
    mutation(payload)

    with pytest.raises(ValueError):
        parse_inventory_text(json.dumps(payload, ensure_ascii=False), allow_legacy=True)


def test_constructor_and_renderer_do_not_coerce_invalid_types() -> None:
    with pytest.raises(ValueError, match="managed_skills"):
        ManagedSkillsInventory(
            skills_version="2026.8.3",
            managed_skills=["wiki-ingest"],  # type: ignore[arg-type]
            managed_skill_digests={"wiki-ingest": DIGEST_1},
        )
    with pytest.raises(ValueError, match="schema_version"):
        ManagedSkillsInventory(
            skills_version="2026.8.3",
            managed_skills=("wiki-ingest",),
            managed_skill_digests={"wiki-ingest": DIGEST_1},
            schema_version=True,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        render_inventory(  # type: ignore[arg-type]
            LegacyManagedSkillsInventory("2026.8.3", ("wiki-ingest",))
        )


def test_constants_match_the_portable_contract() -> None:
    assert SCHEMA_VERSION == 2
    assert MIRROR_FORMAT == "full-copy-v1"
    assert MANAGED_SKILLS_INVENTORY == ".obsidian-wiki/managed-skills.json"


def write_inventory(root: Path, text: str | None = None) -> Path:
    path = root / MANAGED_SKILLS_INVENTORY
    path.parent.mkdir(parents=True)
    path.write_text(text if text is not None else render_inventory(make_inventory()))
    return path


def test_read_inventory_reads_the_contained_single_link_ordinary_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    write_inventory(root)

    assert read_inventory(root) == make_inventory()


def test_read_inventory_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing|ordinary"):
        read_inventory(tmp_path / "repo")


def test_read_inventory_rejects_directory(tmp_path: Path) -> None:
    path = tmp_path / "repo" / MANAGED_SKILLS_INVENTORY
    path.mkdir(parents=True)

    with pytest.raises(ValueError, match="ordinary"):
        read_inventory(tmp_path / "repo")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_read_inventory_rejects_special_file_without_opening_it(tmp_path: Path) -> None:
    path = tmp_path / "repo" / MANAGED_SKILLS_INVENTORY
    path.parent.mkdir(parents=True)
    os.mkfifo(path)

    with pytest.raises(ValueError, match="ordinary"):
        read_inventory(tmp_path / "repo")


def test_read_inventory_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "repo" / MANAGED_SKILLS_INVENTORY
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff")

    with pytest.raises(ValueError, match="UTF-8|invalid"):
        read_inventory(tmp_path / "repo")


def test_read_inventory_rejects_inventory_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside.json"
    outside.write_text(render_inventory(make_inventory()), encoding="utf-8")
    path = root / MANAGED_SKILLS_INVENTORY
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink|ordinary"):
        read_inventory(root)


def test_read_inventory_rejects_parent_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "managed-skills.json").write_text(
        render_inventory(make_inventory()), encoding="utf-8"
    )
    root.mkdir()
    (root / ".obsidian-wiki").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        read_inventory(root)


def test_read_inventory_rejects_hard_link(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    path = write_inventory(root)
    other = tmp_path / "other.json"
    os.link(path, other)

    with pytest.raises(ValueError, match="multiple links|hard link"):
        read_inventory(root)


def test_read_inventory_rejects_repository_root_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_inventory(root)
    alias = tmp_path / "repo-link"
    alias.symlink_to(root, target_is_directory=True)

    with pytest.raises(ValueError, match="root.*symlink"):
        read_inventory(alias)


def test_read_inventory_rejects_replacement_between_lstat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    path = write_inventory(root)
    original = path.read_bytes()
    replacement = render_inventory(
        ManagedSkillsInventory(
            skills_version="changed",
            managed_skills=("wiki-ingest", "wiki-query"),
            managed_skill_digests={
                "wiki-ingest": DIGEST_1,
                "wiki-query": DIGEST_2,
            },
        )
    ).encode("utf-8")
    real_open = os.open
    replaced = False

    def replace_before_open(candidate, flags, *args, **kwargs):
        nonlocal replaced
        if not replaced and os.fspath(candidate) == os.fspath(path):
            replaced = True
            path.replace(tmp_path / "old-inventory.json")
            path.write_bytes(replacement)
        return real_open(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(portable.os, "open", replace_before_open)

    with pytest.raises(ValueError, match="changed"):
        read_inventory(root)
    assert (tmp_path / "old-inventory.json").read_bytes() == original


def test_read_inventory_keeps_the_first_validated_file_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    path = write_inventory(root)
    real_validate = portable._assert_single_link_ordinary_file

    def validate_then_replace(validated_root: Path, candidate: Path, label: str):
        observed = real_validate(validated_root, candidate, label)
        path.replace(tmp_path / "first-validated-inventory.json")
        path.write_text(render_inventory(make_inventory()), encoding="utf-8")
        return observed

    monkeypatch.setattr(
        portable, "_assert_single_link_ordinary_file", validate_then_replace
    )

    with pytest.raises(ValueError, match="changed"):
        read_inventory(root)


def test_read_inventory_rejects_path_replacement_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    path = write_inventory(root)
    real_read = os.read
    replaced = False

    def replace_during_read(descriptor: int, count: int) -> bytes:
        nonlocal replaced
        if not replaced:
            replaced = True
            path.replace(tmp_path / "opened-inventory.json")
            path.write_text(render_inventory(make_inventory()), encoding="utf-8")
        return real_read(descriptor, count)

    monkeypatch.setattr(portable.os, "read", replace_during_read)

    with pytest.raises(ValueError, match="changed"):
        read_inventory(root)


def test_read_inventory_revalidates_path_after_successful_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    path = write_inventory(root)
    real_close = os.close
    replaced = False

    def replace_during_close(descriptor: int) -> None:
        nonlocal replaced
        real_close(descriptor)
        if not replaced:
            replaced = True
            path.replace(tmp_path / "closed-inventory.json")
            path.write_text(render_inventory(make_inventory()), encoding="utf-8")

    monkeypatch.setattr(portable.os, "close", replace_during_close)

    with pytest.raises(ValueError, match="changed"):
        read_inventory(root)


def test_read_inventory_rejects_same_inode_rewrite_during_first_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    path = write_inventory(root)
    before = path.stat()
    changed = render_inventory(
        ManagedSkillsInventory(
            skills_version="2026.8.4",
            managed_skills=("wiki-ingest", "wiki-query"),
            managed_skill_digests={
                "wiki-ingest": DIGEST_1,
                "wiki-query": DIGEST_2,
            },
        )
    ).encode("utf-8")
    assert len(changed) == before.st_size
    real_read = os.read
    rewritten = False

    def rewrite_before_first_read(descriptor: int, count: int) -> bytes:
        nonlocal rewritten
        if not rewritten:
            rewritten = True
            path.write_bytes(changed)
            os.utime(
                path,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
            assert path.stat().st_ino == before.st_ino
        return real_read(descriptor, count)

    monkeypatch.setattr(portable.os, "read", rewrite_before_first_read)

    with pytest.raises(ValueError, match="changed"):
        read_inventory(root)


def test_read_inventory_rejects_same_inode_rewrite_during_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    path = write_inventory(root)
    before = path.stat()
    changed = render_inventory(
        ManagedSkillsInventory(
            skills_version="2026.8.4",
            managed_skills=("wiki-ingest", "wiki-query"),
            managed_skill_digests={
                "wiki-ingest": DIGEST_1,
                "wiki-query": DIGEST_2,
            },
        )
    ).encode("utf-8")
    assert len(changed) == before.st_size
    real_close = os.close
    rewritten = False

    def rewrite_during_close(descriptor: int) -> None:
        nonlocal rewritten
        real_close(descriptor)
        if not rewritten:
            rewritten = True
            path.write_bytes(changed)
            os.utime(
                path,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
            assert path.stat().st_ino == before.st_ino

    monkeypatch.setattr(portable.os, "close", rewrite_during_close)

    with pytest.raises(ValueError, match="changed"):
        read_inventory(root)


def test_read_inventory_rejects_short_read_even_when_json_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    padding = b" " * 64
    path = root / MANAGED_SKILLS_INVENTORY
    path.parent.mkdir(parents=True)
    path.write_bytes(render_inventory(make_inventory()).encode("utf-8") + padding)
    real_read = os.read
    first_read = True

    def omit_trailing_bytes(descriptor: int, count: int) -> bytes:
        nonlocal first_read
        data = real_read(descriptor, count)
        if first_read:
            first_read = False
            assert data.endswith(padding)
            return data[: -len(padding)]
        return data

    monkeypatch.setattr(portable.os, "read", omit_trailing_bytes)

    with pytest.raises(ValueError, match="changed"):
        read_inventory(root)


def test_read_inventory_preserves_primary_failure_when_close_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    write_inventory(root)
    real_close = os.close

    def fail_read(_descriptor: int, _count: int) -> bytes:
        raise RuntimeError("primary read failure")

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("secondary close failure")

    monkeypatch.setattr(portable.os, "read", fail_read)
    monkeypatch.setattr(portable.os, "close", close_then_fail)

    with pytest.raises(RuntimeError, match="primary read failure"):
        read_inventory(root)


def test_read_inventory_normalizes_close_only_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from obsidian_wiki import portable

    root = tmp_path / "repo"
    write_inventory(root)
    real_close = os.close

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("close failure")

    monkeypatch.setattr(portable.os, "close", close_then_fail)

    with pytest.raises(ValueError, match="safely read|close failure"):
        read_inventory(root)

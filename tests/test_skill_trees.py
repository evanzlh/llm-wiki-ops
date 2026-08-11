from pathlib import Path

import os
import stat

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


def test_rejects_file_replaced_by_symlink_before_descriptor_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = write_skill(tmp_path, "example")
    resource = skill / "resource"
    resource.write_bytes(b"original")
    replacement = skill / "replacement"
    replacement.write_bytes(b"replacement")

    from obsidian_wiki import skill_trees

    real_open = os.open
    swapped = False

    def replace_before_open(path: object, flags: int) -> int:
        nonlocal swapped
        if not swapped and os.fspath(path) == os.fspath(resource):
            resource.unlink()
            resource.symlink_to(replacement.name)
            swapped = True
        return real_open(path, flags)

    monkeypatch.setattr(skill_trees.os, "open", replace_before_open)

    with pytest.raises(ValueError, match="changed|ordinary|symbolic"):
        skill_trees.discover_skill_collection(tmp_path)


def test_skill_metadata_comes_from_the_captured_skill_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = write_skill(tmp_path, "example", "Before save.")
    skill_file = skill / "SKILL.md"

    from obsidian_wiki import skill_trees
    from obsidian_wiki.frontmatter import parse_frontmatter

    real_read = skill_trees._read_ordinary_file
    skill_reads = 0

    def save_after_first_read(path: Path, observed: os.stat_result) -> bytes:
        nonlocal skill_reads
        content = real_read(path, observed)
        if path == skill_file and skill_reads == 0:
            skill_reads += 1
            skill_file.write_text(
                "---\nname: example\ndescription: After save.\n---\n",
                encoding="utf-8",
            )
        return content

    monkeypatch.setattr(skill_trees, "_read_ordinary_file", save_after_first_read)

    tree = skill_trees.discover_skill_collection(tmp_path).skills[0]
    captured = next(entry for entry in tree.entries if entry.path == "SKILL.md")
    captured_frontmatter = parse_frontmatter(captured.content.decode("utf-8"))

    assert tree.name == captured_frontmatter.scalars["name"]
    assert tree.description == captured_frontmatter.scalars["description"]
    assert skill_reads == 1


def test_folded_description_preserves_captured_bytes_and_digest(tmp_path: Path) -> None:
    from obsidian_wiki import skill_trees

    original = (
        b"---\r\n"
        b"name: example\r\n"
        b"description: >-\r\n"
        b"  It's: a # literal, with \"quotes\", 'apostrophes', and \xe4\xb8\xad\xe6\x96\x87.\r\n"
        b"  Continue the selection here.\r\n"
        b"---\r\n"
        b"\r\n# Example\r\n"
    )
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(original)

    tree = skill_trees.discover_skill_collection(tmp_path).skills[0]
    captured = next(entry for entry in tree.entries if entry.path == "SKILL.md")

    assert tree.description == (
        "It's: a # literal, with \"quotes\", 'apostrophes', and 中文. "
        "Continue the selection here."
    )
    assert captured.content == original
    assert tree.digest == skill_trees._digest("example", tree.entries)


@pytest.mark.parametrize("indicator", [">", ">-", ">+"])
def test_accepts_supported_folded_description_indicators(
    tmp_path: Path, indicator: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: "
        + indicator
        + "\n  First line.\n  Second line.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    tree = discover_skill_collection(tmp_path).skills[0]
    assert tree.description == "First line. Second line."


@pytest.mark.parametrize(
    "frontmatter",
    [
        "name: example\ndescription: >\n---",
        "name: example\ndescription: >\n\tTabbed content.\n---",
        "name: example\ndescription: >2\n  Explicit indentation.\n---",
        "name: example\ndescription: |\n  Literal style.\n---",
        (
            "name: example\ndescription: >\n  First description.\n"
            "description: Second description.\n---"
        ),
        "name: >\n  example\ndescription: Use this skill.\n---",
        "metadata:\n  description: >\n    Nested value.\nname: example\n---",
    ],
)
def test_rejects_ambiguous_or_unsupported_folded_metadata(
    tmp_path: Path, frontmatter: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\n" + frontmatter + "\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="frontmatter|required|description"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize(
    "metadata",
    [
        "'other:key': >\ndescription: Use this skill.",
        '"other:key": >\ndescription: Use this skill.',
        "!tag other: >\ndescription: Use this skill.",
        "&anchor other: >\ndescription: Use this skill.",
        "'unterminated: >\ndescription: Use this skill.",
        "description:\t>\n  Block content.",
        "description: >\t\n  Block content.",
        "'other':\t>\ndescription: Use this skill.",
    ],
)
def test_rejects_non_description_block_keys_and_structural_tabs(
    tmp_path: Path, metadata: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n"
        + metadata
        + "\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="frontmatter|block|tab|description"):
        discover_skill_collection(tmp_path)


def test_block_header_near_misses_remain_plain_scalars(tmp_path: Path) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: example\n"
        "description: Use a > comparison: literal.\n"
        "quoted: '>'\n"
        'double_quoted: "|"\n'
        "other: 'key: >'\n"
        "---\n\n"
        "# Body\n\n'other:key': >\n  Not frontmatter.\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    tree = discover_skill_collection(tmp_path).skills[0]
    assert tree.description == "Use a > comparison: literal."


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Compare syntax: > is literal.", "Compare syntax: > is literal."),
        ("'Compare syntax: > is literal.'", "Compare syntax: > is literal."),
    ],
)
def test_literal_colon_indicator_text_is_not_a_block_header(
    tmp_path: Path, description: str, expected: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: " + description + "\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == expected


@pytest.mark.parametrize(
    "metadata",
    [
        "other:key: >",
        "a:b:c: >",
        "other:key: > # inline comment",
        "other:'quoted:key': >",
        'other:"quoted:key": > # inline comment',
    ],
)
def test_rejects_nested_mapping_suffix_with_block_metadata(
    tmp_path: Path, metadata: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n"
        + metadata
        + "\ndescription: Use this skill.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="ambiguous|block field"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "Compare mappings a:b: > is literal text.",
            "Compare mappings a:b: > is literal text.",
        ),
        (
            "'Compare mappings a:b: >'",
            "Compare mappings a:b: >",
        ),
        (
            '"Compare mappings a:b: >"',
            "Compare mappings a:b: >",
        ),
    ],
)
def test_nested_colons_in_literal_description_remain_scalar(
    tmp_path: Path, description: str, expected: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: " + description + "\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == expected


@pytest.mark.parametrize("comment", ["", " # inline comment"])
@pytest.mark.parametrize(
    "metadata",
    [
        "other:key: > extra",
        "other:key: >- extra",
        "other:key: | text",
        "a:b:c: > extra",
    ],
)
def test_rejects_nested_block_like_values_with_extra_tokens(
    tmp_path: Path, metadata: str, comment: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n"
        + metadata
        + comment
        + "\ndescription: Use this skill.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="ambiguous|block field"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize(
    "metadata",
    [
        "other:key: '> extra'",
        'other:key: ">- extra"',
        "other:key: '| text'",
        "other: key > extra",
    ],
)
def test_nested_quoted_or_non_indicator_values_remain_scalars(
    tmp_path: Path, metadata: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n"
        + metadata
        + "\ndescription: Use this skill.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == (
        "Use this skill."
    )


@pytest.mark.parametrize("comment", ["", " # inline comment"])
@pytest.mark.parametrize(
    "metadata",
    [
        "description:foo: >",
        "description:foo: > extra",
        "description:x:y: | text",
    ],
)
def test_rejects_colon_bearing_description_key_with_block_suffix(
    tmp_path: Path, metadata: str, comment: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n" + metadata + comment + "\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="colon-bearing|block field"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Compare syntax: > is literal.", "Compare syntax: > is literal."),
        ("'Compare syntax: >'", "Compare syntax: >"),
        ('"Compare syntax: >"', "Compare syntax: >"),
    ],
)
def test_space_delimited_description_values_keep_literal_colons(
    tmp_path: Path, description: str, expected: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: " + description + "\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == expected


def test_empty_description_mapping_still_fails_required_scalar_validation(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription:\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="required"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize(
    "description",
    ["'Compare syntax: >'", '"Compare syntax: >"'],
)
def test_quoted_literal_ending_in_indicator_is_not_a_block_header(
    tmp_path: Path, description: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: " + description + "\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == (
        "Compare syntax: >"
    )


def test_folded_description_header_accepts_inline_comment(tmp_path: Path) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n"
        "description: > # comment: > is comment text\n"
        "  Fold this line.\n  And this line.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == (
        "Fold this line. And this line."
    )


def test_folded_description_header_comment_does_not_make_empty_content_valid(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: > # comment\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="empty"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("description: >2 # comment", "style"),
        ("other: > # comment", "field"),
        ("description: | # comment", "style"),
        ("description: >\t# comment", "whitespace"),
        ("description: >\u00a0# comment", "whitespace"),
        ("description: >\u2003# comment", "whitespace"),
    ],
)
def test_rejects_unsupported_or_malformed_commented_block_headers(
    tmp_path: Path, header: str, message: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n" + header + "\n  Content.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match=message):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize("comment", ["", " # inline comment"])
@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ("description: > 2", "style"),
        ("description: >- extra", "style"),
        ("description: | text", "style"),
        ("other: > extra", "field"),
    ],
)
def test_rejects_every_unquoted_block_like_mapping_value(
    tmp_path: Path, mapping: str, message: str, comment: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n"
        + mapping
        + comment
        + "\ndescription: Use this skill.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match=message):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize("comment", ["", " # inline comment"])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("'> 2'", "> 2"),
        ('">- extra"', ">- extra"),
        ("'| text'", "| text"),
    ],
)
def test_quoted_block_like_description_values_remain_scalars(
    tmp_path: Path, value: str, expected: str, comment: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: "
        + value
        + comment
        + "\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == expected


def test_quoted_block_like_other_field_remains_scalar(tmp_path: Path) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\nother: '> extra'\n"
        "description: Use this skill.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == (
        "Use this skill."
    )


@pytest.mark.parametrize(
    "delimiter",
    ["\t---", "---\t", "\u00a0---", "---\u00a0", "\u2003---", "---\u2003"],
)
@pytest.mark.parametrize("position", ["opening", "closing"])
@pytest.mark.parametrize(
    "metadata",
    [
        "name: example\ndescription: >",
        "name: example\ndescription: |",
        "name: example\nother: >\ndescription: Use this skill.",
    ],
)
def test_rejects_non_ascii_skill_frontmatter_delimiter_structure(
    tmp_path: Path, delimiter: str, position: str, metadata: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    opening = delimiter if position == "opening" else "---"
    closing = delimiter if position == "closing" else "---"
    (skill / "SKILL.md").write_text(
        opening + "\n" + metadata + "\n" + closing + "\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="delimiter|whitespace|frontmatter"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("---", "---"), (" ---", "--- "), ("  ---  ", " --- ")],
)
def test_exact_and_ascii_space_skill_frontmatter_delimiters_are_supported(
    tmp_path: Path, opening: str, closing: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        opening
        + "\nname: example\ndescription: >\n"
        + "  Folded description.\n"
        + closing
        + "\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == (
        "Folded description."
    )


@pytest.mark.parametrize("separator", ["\v", "\f", "\x85", "\u2028", "\u2029"])
def test_non_newline_separator_cannot_terminate_opening_delimiter(
    tmp_path: Path, separator: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---"
        + separator
        + "\nname: example\ndescription: >\n  Content.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="delimiter|whitespace"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize("separator", ["\v", "\f", "\x85", "\u2028", "\u2029"])
def test_non_newline_separator_is_rejected_in_block_header_structure(
    tmp_path: Path, separator: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: >"
        + separator
        + "\n  Content.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="header|whitespace"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize("separator", ["\v", "\f", "\x85", "\u2028", "\u2029"])
def test_non_newline_separator_is_rejected_in_folded_indentation(
    tmp_path: Path, separator: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: >\n  "
        + separator
        + "Content.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="indentation|whitespace"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_skill_metadata_accepts_only_real_newlines_and_preserves_bytes(
    tmp_path: Path, newline: str
) -> None:
    original = newline.join(
        [
            "---",
            "name: example",
            "description: >",
            "  Folded description.",
            "---",
            "",
        ]
    ).encode("utf-8")
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(original)

    from obsidian_wiki.skill_trees import discover_skill_collection

    tree = discover_skill_collection(tmp_path).skills[0]
    captured = next(entry for entry in tree.entries if entry.path == "SKILL.md")
    assert tree.description == "Folded description."
    assert captured.content == original


@pytest.mark.parametrize("separator", ["\x85", "\u2028", "\u2029"])
def test_unicode_separator_inside_folded_content_remains_content(
    tmp_path: Path, separator: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: >\n  Keep"
        + separator
        + "inside.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == (
        "Keep" + separator + "inside."
    )


@pytest.mark.parametrize(
    "header",
    [
        "description:\u00a0>",
        "description:\u2003>",
        "description: >\u00a0",
        "description: >\u2003",
        "description\u00a0: >",
        "description\u2003: >",
    ],
)
def test_rejects_non_ascii_structural_whitespace_in_block_header(
    tmp_path: Path, header: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\n" + header + "\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="frontmatter|block|whitespace"):
        discover_skill_collection(tmp_path)


@pytest.mark.parametrize("indent", ["\u00a0", "\u2003", " \u00a0", " \u2003"])
def test_rejects_non_ascii_folded_description_indentation(
    tmp_path: Path, indent: str
) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: >\n"
        + indent
        + "Content.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="frontmatter|indentation|whitespace"):
        discover_skill_collection(tmp_path)


def test_non_ascii_whitespace_inside_folded_content_is_preserved(tmp_path: Path) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: >\n"
        "  Keep\u00a0this content.\n---\n",
        encoding="utf-8",
    )

    from obsidian_wiki.skill_trees import discover_skill_collection

    assert discover_skill_collection(tmp_path).skills[0].description == (
        "Keep\u00a0this content."
    )


def test_discovers_every_current_bundled_skill_with_nonempty_description() -> None:
    from obsidian_wiki.skill_trees import discover_skill_collection

    root = Path(__file__).resolve().parents[1] / ".skills"
    collection = discover_skill_collection(root, ignore_source_artifacts=True)

    assert collection.names == tuple(
        sorted(path.name for path in root.iterdir() if path.is_dir())
    )
    assert all(skill.description for skill in collection.skills)


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


def test_ignore_mode_rejects_symlink_named_as_ignored_file(tmp_path: Path) -> None:
    skill = write_skill(tmp_path, "example")
    target = skill / "target"
    target.write_bytes(b"target")
    ignored_link = skill / ".env"
    try:
        ignored_link.symlink_to(target)
    except OSError as exc:
        pytest.skip("symbolic links unavailable: {}".format(exc))

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="symbolic"):
        discover_skill_collection(tmp_path, ignore_source_artifacts=True)


def test_ignore_mode_rejects_special_file_named_as_ignored_file(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes unavailable")
    skill = write_skill(tmp_path, "example")
    try:
        os.mkfifo(skill / ".env")
    except OSError as exc:
        pytest.skip("named pipes unavailable: {}".format(exc))

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="special"):
        discover_skill_collection(tmp_path, ignore_source_artifacts=True)


def test_ignore_mode_rejects_hardlinked_ignored_file(tmp_path: Path) -> None:
    skill = write_skill(tmp_path, "example")
    ignored = skill / ".env"
    ignored.write_bytes(b"secret")
    try:
        os.link(ignored, skill / ".env.local")
    except OSError as exc:
        pytest.skip("hard links unavailable: {}".format(exc))

    from obsidian_wiki.skill_trees import discover_skill_collection

    with pytest.raises(ValueError, match="multiply-linked"):
        discover_skill_collection(tmp_path, ignore_source_artifacts=True)


@pytest.mark.parametrize(
    "name",
    [
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".AppleDouble",
        ".LSOverride",
        ".Spotlight-V100",
        ".Trashes",
    ],
)
def test_ignore_mode_matches_each_declared_directory(tmp_path: Path, name: str) -> None:
    skill = write_skill(tmp_path, "example")
    directory = skill / name
    directory.mkdir()
    (directory / "ignored").write_bytes(b"ignored")

    from obsidian_wiki.skill_trees import discover_skill_collection

    entries = discover_skill_collection(tmp_path, ignore_source_artifacts=True).skills[0].entries
    assert [entry.path for entry in entries] == ["SKILL.md"]


@pytest.mark.parametrize(
    "name",
    [
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
        "Icon\r",
        ".env",
        ".env.local",
        "._resource",
        "bytecode.pyc",
        "bytecode.pyo",
    ],
)
def test_ignore_mode_matches_each_declared_file(tmp_path: Path, name: str) -> None:
    skill = write_skill(tmp_path, "example")
    (skill / name).write_bytes(b"ignored")

    from obsidian_wiki.skill_trees import discover_skill_collection

    entries = discover_skill_collection(tmp_path, ignore_source_artifacts=True).skills[0].entries
    assert [entry.path for entry in entries] == ["SKILL.md"]


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("directory", ".git-cache"),
        ("directory", ".hg-cache"),
        ("directory", ".svn-cache"),
        ("directory", "__pycache__x"),
        ("directory", ".pytest_cachex"),
        ("directory", ".mypy_cachex"),
        ("directory", ".ruff_cachex"),
        ("directory", ".AppleDoublex"),
        ("directory", ".LSOverridex"),
        ("directory", ".Spotlight-V100x"),
        ("directory", ".Trashesx"),
        ("file", ".DS_Storex"),
        ("file", "Thumbs.dbx"),
        ("file", "desktop.inix"),
        ("file", "Icon"),
        ("file", ".environment"),
        ("file", ".envx"),
        ("file", ".-resource"),
        ("file", "bytecode.pycx"),
        ("file", "bytecode.pyox"),
    ],
)
def test_ignore_mode_retains_near_matches(tmp_path: Path, kind: str, name: str) -> None:
    skill = write_skill(tmp_path, "example")
    path = skill / name
    if kind == "directory":
        directory = path
        directory.mkdir()
        (directory / "retained").write_bytes(b"retained")
    else:
        path.write_bytes(b"retained")

    from obsidian_wiki.skill_trees import discover_skill_collection

    entries = discover_skill_collection(tmp_path, ignore_source_artifacts=True).skills[0].entries
    paths = {entry.path for entry in entries}
    assert name in paths


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


def test_entries_are_globally_sorted_by_relative_path(tmp_path: Path) -> None:
    skill = write_skill(tmp_path, "example")
    (skill / "a").mkdir()
    (skill / "a" / "z").write_bytes(b"z")
    (skill / "a-foo").write_bytes(b"sibling")

    from obsidian_wiki.skill_trees import discover_skill_collection

    entries = discover_skill_collection(tmp_path).skills[0].entries
    assert [entry.path for entry in entries] == ["SKILL.md", "a", "a-foo", "a/z"]


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
    assert stat.S_IMODE(first.stat().st_mode) == 0o755
    assert stat.S_IMODE((first / "example").stat().st_mode) == 0o755
    assert stat.S_IMODE((first / "example" / "scripts").stat().st_mode) == 0o755
    assert stat.S_IMODE((first / "example" / "scripts" / "run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((first / "example" / "SKILL.md").stat().st_mode) == 0o644
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

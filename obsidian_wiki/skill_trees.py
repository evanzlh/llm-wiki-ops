"""Deterministic, link-free snapshots of skill directory trees."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import unicodedata
from typing import Literal

from .frontmatter import (
    FrontmatterError,
    _strip_comment,
    _top_level_mapping,
    parse_frontmatter,
)


_SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SOURCE_IGNORED_DIRECTORIES = frozenset(
    {
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
    }
)
_SOURCE_IGNORED_FILES = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}
)
_SUPPORTED_DESCRIPTION_BLOCKS = frozenset({">", ">-", ">+"})


@dataclass(frozen=True)
class SkillEntry:
    path: str
    kind: Literal["directory", "file"]
    executable: bool
    content: bytes


@dataclass(frozen=True)
class SkillTree:
    name: str
    description: str
    entries: tuple[SkillEntry, ...]
    digest: str


@dataclass(frozen=True)
class SkillCollection:
    skills: tuple[SkillTree, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self.skills)

    def by_name(self) -> dict[str, SkillTree]:
        return {skill.name: skill for skill in self.skills}


def _error(path: Path, message: str) -> ValueError:
    return ValueError("{}: {}".format(path, message))


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _read_ordinary_file(path: Path, observed: os.stat_result) -> bytes:
    if not stat.S_ISREG(observed.st_mode):
        raise _error(path, "file must be an ordinary regular file")
    if observed.st_nlink != 1:
        raise _error(path, "multiply-linked regular files are not allowed")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _error(path, "file changed or is not an ordinary file") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _error(path, "file must be an ordinary regular file")
        if opened.st_nlink != 1:
            raise _error(path, "multiply-linked regular files are not allowed")
        if not _same_identity(observed, opened):
            raise _error(path, "file changed while being read")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)

        final = os.fstat(descriptor)
        if (
            not stat.S_ISREG(final.st_mode)
            or final.st_nlink != 1
            or not _same_identity(opened, final)
        ):
            raise _error(path, "file changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_directory_unchanged(path: Path, observed: os.stat_result) -> None:
    try:
        current = path.lstat()
    except OSError as exc:
        raise _error(path, "directory changed during scan") from exc
    if not stat.S_ISDIR(current.st_mode) or not _same_identity(observed, current):
        raise _error(path, "directory changed during scan")


def _is_ignored_source_artifact(name: str, mode: int) -> bool:
    if stat.S_ISDIR(mode):
        return name in _SOURCE_IGNORED_DIRECTORIES
    return (
        name in _SOURCE_IGNORED_FILES
        or name == ".env"
        or name.startswith(".env.")
        or name.startswith("._")
        or name.endswith(".pyc")
        or name.endswith(".pyo")
    )


def _snapshot_entry(
    path: Path,
    relative: str,
    entries: list[SkillEntry],
    *,
    ignore_source_artifacts: bool,
) -> None:
    metadata = path.lstat()
    mode = metadata.st_mode
    if stat.S_ISLNK(mode):
        raise _error(path, "symbolic links are not allowed")
    if stat.S_ISDIR(mode):
        if ignore_source_artifacts and _is_ignored_source_artifact(path.name, mode):
            return
        entries.append(SkillEntry(relative, "directory", False, b""))
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            _snapshot_entry(
                child,
                relative + "/" + child.name,
                entries,
                ignore_source_artifacts=ignore_source_artifacts,
            )
        _validate_directory_unchanged(path, metadata)
        return
    if not stat.S_ISREG(mode):
        raise _error(path, "special files are not allowed")
    if metadata.st_nlink != 1:
        raise _error(path, "multiply-linked regular files are not allowed")
    if ignore_source_artifacts and _is_ignored_source_artifact(path.name, mode):
        return
    entries.append(
        SkillEntry(
            relative,
            "file",
            bool(mode & 0o111),
            _read_ordinary_file(path, metadata),
        )
    )


def _digest(name: str, entries: tuple[SkillEntry, ...]) -> str:
    digest = sha256()

    def add(value: bytes) -> None:
        digest.update(str(len(value)).encode("ascii"))
        digest.update(b":")
        digest.update(value)

    add(name.encode("utf-8"))
    for entry in entries:
        add(entry.path.encode("utf-8"))
        add(entry.kind.encode("ascii"))
        add(b"1" if entry.executable else b"0")
        add(str(len(entry.content)).encode("ascii"))
        add(entry.content)
    return "sha256:" + digest.hexdigest()


def _structural_whitespace(character: str) -> bool:
    return character in " \t\v\f\x85" or unicodedata.category(character) in {
        "Zs",
        "Zl",
        "Zp",
    }


def _skill_block_value(raw_region: str) -> tuple[str, bool] | None:
    """Return an uncommented block token and its ASCII-structure validity."""
    uncommented = _strip_comment(raw_region)
    value_start = 0
    while value_start < len(uncommented) and _structural_whitespace(
        uncommented[value_start]
    ):
        value_start += 1
    value_end = len(uncommented)
    while value_end > value_start and _structural_whitespace(
        uncommented[value_end - 1]
    ):
        value_end -= 1
    value = uncommented[value_start:value_end]
    if not value.startswith((">", "|")):
        return None
    start = raw_region.find(uncommented)
    if start < 0:
        return None
    tail = raw_region[start + len(uncommented) :]
    comment = tail.find("#")
    trailing = tail if comment < 0 else tail[:comment]
    structural = (
        raw_region[:start]
        + uncommented[:value_start]
        + uncommented[value_end:]
        + trailing
    )
    return value, all(character == " " for character in structural)


def _skill_frontmatter_delimiter(line: str) -> bool:
    if line.strip() != "---":
        return False
    if line.strip(" ") != "---":
        raise FrontmatterError(
            "skill frontmatter delimiter has non-ASCII structural whitespace"
        )
    return True


def _has_nested_skill_block_metadata(raw_region: str) -> bool:
    nested_region = _strip_comment(raw_region).lstrip(" ")
    while True:
        mapping = _top_level_mapping(nested_region)
        if mapping is None:
            return False
        _key_region, nested_raw_region = mapping
        block_value = _skill_block_value(nested_raw_region)
        if block_value is not None:
            return True
        nested_region = _strip_comment(nested_raw_region).lstrip(" ")


def _normalized_skill_frontmatter(text: str) -> str:
    """Adapt the bundled skill description subset for the strict parser."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or not _skill_frontmatter_delimiter(lines[0]):
        return text
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if _skill_frontmatter_delimiter(line)
        ),
        None,
    )
    if closing is None:
        return text

    description_lines: list[int] = []
    folded_line: int | None = None
    for index in range(1, closing):
        line = lines[index]
        if not line or line.startswith((" ", "\t")):
            continue
        mapping = _top_level_mapping(line)
        if mapping is None:
            continue
        key_region, raw_region = mapping
        key_end = len(key_region)
        while key_end and _structural_whitespace(key_region[key_end - 1]):
            key_end -= 1
        key = key_region[:key_end]
        key_structure = key_region[key_end:]
        block_value = _skill_block_value(raw_region)
        if block_value is not None:
            value, value_structure_is_ascii = block_value
            if not value_structure_is_ascii or any(
                character != " " for character in key_structure
            ):
                raise FrontmatterError(
                    "skill metadata block header has non-ASCII structural whitespace"
                )
            if key != "description":
                raise FrontmatterError("unsupported skill metadata block field")
            description_lines.append(index)
            if value in _SUPPORTED_DESCRIPTION_BLOCKS:
                folded_line = index
            else:
                raise FrontmatterError("unsupported folded skill description style")
            continue
        if key != "description" and _has_nested_skill_block_metadata(raw_region):
            raise FrontmatterError("ambiguous unsupported skill metadata block field")
        if key == "description" and all(
            character == " " for character in key_structure
        ):
            description_lines.append(index)

    if len(description_lines) > 1:
        raise FrontmatterError("duplicate skill description")
    if folded_line is None:
        return text

    content: list[str] = []
    indentation: int | None = None
    end = folded_line + 1
    while end < closing:
        line = lines[end]
        if not line or all(character == " " for character in line):
            content.append("")
            end += 1
            continue
        leading = 0
        while leading < len(line) and _structural_whitespace(line[leading]):
            leading += 1
        if leading == 0:
            break
        if any(character != " " for character in line[:leading]):
            raise FrontmatterError(
                "folded skill description has non-ASCII indentation whitespace"
            )
        if indentation is None:
            indentation = leading
        if leading != indentation:
            raise FrontmatterError("folded skill description has bad indentation")
        content.append(line[leading:])
        end += 1

    if indentation is None:
        raise FrontmatterError("folded skill description is empty")
    folded = " ".join(
        part.strip(" ") for part in content if part.strip(" ")
    ).strip(" ")
    if not folded:
        raise FrontmatterError("folded skill description is empty")
    quoted = folded.replace("'", "''")
    normalized = lines[:folded_line]
    normalized.append("description: '" + quoted + "'")
    normalized.extend(lines[end:])
    return "\n".join(normalized) + "\n"


def discover_skill_collection(
    root: Path, *, ignore_source_artifacts: bool = False
) -> SkillCollection:
    """Return a sorted, fully validated ordinary-file snapshot of root."""
    root_metadata = root.lstat()
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise _error(root, "skill root must be an ordinary directory")

    skills: list[SkillTree] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        metadata = directory.lstat()
        mode = metadata.st_mode
        if stat.S_ISLNK(mode):
            raise _error(directory, "symbolic links are not allowed")
        if stat.S_ISDIR(mode):
            if ignore_source_artifacts and _is_ignored_source_artifact(
                directory.name, mode
            ):
                continue
        elif stat.S_ISREG(mode):
            if metadata.st_nlink != 1:
                raise _error(directory, "multiply-linked regular files are not allowed")
            if ignore_source_artifacts and _is_ignored_source_artifact(
                directory.name, mode
            ):
                continue
            raise _error(directory, "each skill must be an ordinary directory")
        else:
            raise _error(directory, "special files are not allowed")
        if not _SKILL_NAME.fullmatch(directory.name):
            raise _error(directory, "unsafe skill directory name")
        entries: list[SkillEntry] = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            _snapshot_entry(
                child,
                child.name,
                entries,
                ignore_source_artifacts=ignore_source_artifacts,
            )
        _validate_directory_unchanged(directory, metadata)
        frozen_entries = tuple(sorted(entries, key=lambda entry: entry.path))
        skill_file = directory / "SKILL.md"
        skill_entry = next(
            (entry for entry in frozen_entries if entry.path == "SKILL.md"), None
        )
        if skill_entry is None:
            raise _error(skill_file, "SKILL.md is missing")
        if skill_entry.kind != "file":
            raise _error(skill_file, "SKILL.md must be an ordinary file")
        try:
            skill_text = skill_entry.content.decode("utf-8")
            frontmatter = parse_frontmatter(_normalized_skill_frontmatter(skill_text))
        except (FrontmatterError, UnicodeDecodeError) as exc:
            raise _error(skill_file, "invalid UTF-8 frontmatter: {}".format(exc)) from exc
        name = frontmatter.scalars.get("name", "")
        description = frontmatter.scalars.get("description", "")
        if not name or not description:
            raise _error(skill_file, "frontmatter name and description are required")
        if name != directory.name:
            raise _error(skill_file, "frontmatter name must equal directory name")
        skills.append(SkillTree(name, description, frozen_entries, _digest(name, frozen_entries)))
    _validate_directory_unchanged(root, root_metadata)
    if not skills:
        raise _error(root, "skill collection must not be empty")
    return SkillCollection(tuple(skills))


def _materialize_path(root: Path, relative: str) -> Path:
    parts = relative.split("/")
    if not relative or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("unsafe skill entry path: {}".format(relative))
    return root.joinpath(*parts)


def materialize_skill_collection(
    collection: SkillCollection, destination: Path
) -> None:
    """Create destination from a snapshot; destination must not already exist."""
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination already exists: {}".format(destination))
    destination.mkdir(parents=True)
    os.chmod(destination, 0o755)
    for skill in collection.skills:
        skill_root = _materialize_path(destination, skill.name)
        skill_root.mkdir()
        os.chmod(skill_root, 0o755)
        for entry in skill.entries:
            path = _materialize_path(skill_root, entry.path)
            if entry.kind == "directory":
                path.mkdir()
                os.chmod(path, 0o755)
            else:
                path.write_bytes(entry.content)
                os.chmod(path, 0o755 if entry.executable else 0o644)


def compare_skill_collections(
    canonical: SkillCollection, mirror: SkillCollection
) -> tuple[dict[str, tuple[str, ...]], ...]:
    """Return deterministic added, changed, and removed path records.

    The three mappings respectively describe paths to add to, change in, and
    remove from ``mirror`` to make it match ``canonical``.
    """
    canonical_by_name = canonical.by_name()
    mirror_by_name = mirror.by_name()
    added: dict[str, tuple[str, ...]] = {}
    changed: dict[str, tuple[str, ...]] = {}
    removed: dict[str, tuple[str, ...]] = {}
    for name in sorted(set(canonical_by_name) | set(mirror_by_name)):
        canonical_tree = canonical_by_name.get(name)
        mirror_tree = mirror_by_name.get(name)
        canonical_entries = {
            entry.path: entry
            for entry in (() if canonical_tree is None else canonical_tree.entries)
        }
        mirror_entries = {
            entry.path: entry
            for entry in (() if mirror_tree is None else mirror_tree.entries)
        }
        added_paths = tuple(sorted(set(canonical_entries) - set(mirror_entries)))
        changed_paths = tuple(
            path
            for path in sorted(set(canonical_entries) & set(mirror_entries))
            if canonical_entries[path] != mirror_entries[path]
        )
        removed_paths = tuple(sorted(set(mirror_entries) - set(canonical_entries)))
        if added_paths:
            added[name] = added_paths
        if changed_paths:
            changed[name] = changed_paths
        if removed_paths:
            removed[name] = removed_paths
    return added, changed, removed

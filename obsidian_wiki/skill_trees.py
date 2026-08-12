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
_SUPPORTS_BOUND_TREE_WALK = (
    os.name == "posix"
    and bool(getattr(os, "O_NOFOLLOW", 0))
    and bool(getattr(os, "O_DIRECTORY", 0))
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.listdir in os.supports_fd
)


@dataclass(frozen=True)
class SkillEntry:
    path: str
    kind: Literal["directory", "file"]
    executable: bool
    content: bytes


@dataclass(frozen=True)
class UnsafeSkillEntry:
    path: str
    reason: Literal["symlink", "hard-link", "special", "changed", "read-error"]


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


def _is_reparse_point(metadata: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & flag)


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
    unsafe_entries: list[UnsafeSkillEntry] | None = None,
    windows_anchor: Path | None = None,
    windows_handles: list[int] | None = None,
) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        if unsafe_entries is None:
            raise
        unsafe_entries.append(UnsafeSkillEntry(relative, "read-error"))
        return
    mode = metadata.st_mode
    if stat.S_ISLNK(mode) or _is_reparse_point(metadata):
        if unsafe_entries is not None:
            unsafe_entries.append(UnsafeSkillEntry(relative, "symlink"))
            return
        raise _error(path, "symbolic links are not allowed")
    if stat.S_ISDIR(mode):
        if ignore_source_artifacts and _is_ignored_source_artifact(path.name, mode):
            return
        if windows_anchor is not None:
            assert windows_handles is not None
            try:
                _hold_windows_directory_guard(
                    windows_anchor, path, windows_handles
                )
            except (OSError, RuntimeError, ValueError):
                if unsafe_entries is None:
                    raise
                unsafe_entries.append(UnsafeSkillEntry(relative, "changed"))
                return
        entries.append(SkillEntry(relative, "directory", False, b""))
        try:
            children = sorted(path.iterdir(), key=lambda item: item.name)
        except OSError:
            if unsafe_entries is None:
                raise
            unsafe_entries.append(UnsafeSkillEntry(relative, "read-error"))
            entries[:] = [
                entry
                for entry in entries
                if entry.path != relative
                and not entry.path.startswith(relative + "/")
            ]
            return
        for child in children:
            _snapshot_entry(
                child,
                relative + "/" + child.name,
                entries,
                ignore_source_artifacts=ignore_source_artifacts,
                unsafe_entries=unsafe_entries,
                windows_anchor=windows_anchor,
                windows_handles=windows_handles,
            )
        try:
            _validate_directory_unchanged(path, metadata)
        except (OSError, ValueError):
            if unsafe_entries is None:
                raise
            unsafe_entries.append(UnsafeSkillEntry(relative, "changed"))
            entries[:] = [
                entry
                for entry in entries
                if entry.path != relative
                and not entry.path.startswith(relative + "/")
            ]
        return
    if not stat.S_ISREG(mode):
        if unsafe_entries is not None:
            unsafe_entries.append(UnsafeSkillEntry(relative, "special"))
            return
        raise _error(path, "special files are not allowed")
    if metadata.st_nlink != 1:
        if unsafe_entries is not None:
            unsafe_entries.append(UnsafeSkillEntry(relative, "hard-link"))
            return
        raise _error(path, "multiply-linked regular files are not allowed")
    if ignore_source_artifacts and _is_ignored_source_artifact(path.name, mode):
        return
    try:
        content = _read_ordinary_file(path, metadata)
    except OSError:
        if unsafe_entries is None:
            raise
        unsafe_entries.append(UnsafeSkillEntry(relative, "read-error"))
        return
    except ValueError:
        if unsafe_entries is None:
            raise
        unsafe_entries.append(UnsafeSkillEntry(relative, "changed"))
        return
    entries.append(
        SkillEntry(
            relative,
            "file",
            bool(mode & 0o111),
            content,
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


def _fold_skill_description(content: list[str], indicator: str) -> str:
    """Decode the supported YAML folded-scalar subset."""
    trailing_blanks = 0
    for line in reversed(content):
        if line:
            break
        trailing_blanks += 1

    significant = (
        content[: len(content) - trailing_blanks] if trailing_blanks else content
    )
    folded: list[str] = []
    blank_run = 0
    for line in significant:
        if not line:
            blank_run += 1
            continue
        if folded:
            folded.append("\n" * blank_run if blank_run else " ")
        elif blank_run:
            folded.append("\n" * blank_run)
        folded.append(line)
        blank_run = 0
    body = "".join(folded)
    if not body:
        raise FrontmatterError("folded skill description is empty")
    if indicator == ">-":
        return body
    if indicator == ">+":
        return body + "\n" * (trailing_blanks + 1)
    return body + "\n"


def _normalized_skill_frontmatter(text: str) -> tuple[str, str | None]:
    """Return strict-parser input and any decoded folded description."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or not _skill_frontmatter_delimiter(lines[0]):
        return text, None
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if _skill_frontmatter_delimiter(line)
        ),
        None,
    )
    if closing is None:
        return text, None

    description_lines: list[int] = []
    folded_line: int | None = None
    folded_indicator: str | None = None
    for index in range(1, closing):
        line = lines[index]
        if not line or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            continue
        mapping = _top_level_mapping(line)
        if mapping is None:
            continue
        key_region, raw_region = mapping
        if raw_region and not raw_region.startswith(" "):
            block_value = _skill_block_value(raw_region)
            if block_value is not None:
                _value, value_structure_is_ascii = block_value
                if not value_structure_is_ascii:
                    raise FrontmatterError(
                        "skill metadata block header has non-ASCII structural whitespace"
                    )
                raise FrontmatterError(
                    "unsupported colon-bearing skill metadata block field"
                )
            if _has_nested_skill_block_metadata(raw_region):
                raise FrontmatterError(
                    "unsupported colon-bearing skill metadata block field"
                )
            continue
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
                folded_indicator = value
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
        return text, None
    assert folded_indicator is not None

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
        elif leading > indentation:
            raise FrontmatterError(
                "folded skill description has unsupported more-indented content"
            )
        elif leading < indentation:
            raise FrontmatterError("folded skill description has bad indentation")
        content.append(line[leading:])
        end += 1

    if indentation is None:
        raise FrontmatterError("folded skill description is empty")
    folded = _fold_skill_description(content, folded_indicator)
    normalized = lines[:folded_line]
    normalized.append("description: '__folded_skill_description__'")
    normalized.extend(lines[end:])
    return "\n".join(normalized) + "\n", folded


def _skill_tree_from_entries(
    root: Path, name: str, entries: tuple[SkillEntry, ...]
) -> SkillTree:
    skill_file = root / name / "SKILL.md"
    skill_entry = next((entry for entry in entries if entry.path == "SKILL.md"), None)
    if skill_entry is None:
        raise _error(skill_file, "SKILL.md is missing")
    if skill_entry.kind != "file":
        raise _error(skill_file, "SKILL.md must be an ordinary file")
    try:
        skill_text = skill_entry.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(skill_file, "invalid UTF-8 SKILL.md: {}".format(exc)) from exc
    try:
        normalized, folded_description = _normalized_skill_frontmatter(skill_text)
        frontmatter = parse_frontmatter(normalized)
    except FrontmatterError as exc:
        raise _error(skill_file, "invalid skill frontmatter: {}".format(exc)) from exc
    discovered_name = frontmatter.scalars.get("name", "")
    description = (
        folded_description
        if folded_description is not None
        else frontmatter.scalars.get("description", "")
    )
    if not discovered_name or not description:
        raise _error(skill_file, "frontmatter name and description are required")
    if discovered_name != name:
        raise _error(skill_file, "frontmatter name must equal directory name")
    return SkillTree(name, description, entries, _digest(name, entries))


def _collection_from_root_entries(
    root: Path, entries: tuple[SkillEntry, ...]
) -> SkillCollection:
    top_level = tuple(entry for entry in entries if "/" not in entry.path)
    skills: list[SkillTree] = []
    for directory in top_level:
        if directory.kind != "directory":
            raise _error(root / directory.path, "each skill must be an ordinary directory")
        if not _SKILL_NAME.fullmatch(directory.path):
            raise _error(root / directory.path, "unsafe skill directory name")
        prefix = directory.path + "/"
        skill_entries = tuple(
            SkillEntry(
                entry.path[len(prefix) :],
                entry.kind,
                entry.executable,
                entry.content,
            )
            for entry in entries
            if entry.path.startswith(prefix)
        )
        skills.append(_skill_tree_from_entries(root, directory.path, skill_entries))
    if not skills:
        raise _error(root, "skill collection must not be empty")
    return SkillCollection(tuple(skills))


def discover_skill_collection(
    root: Path, *, ignore_source_artifacts: bool = False
) -> SkillCollection:
    """Return a sorted, fully validated ordinary-file snapshot of root."""
    root_metadata = root.lstat()
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise _error(root, "skill root must be an ordinary directory")

    root_entries: list[SkillEntry] = []
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
        root_entries.append(SkillEntry(directory.name, "directory", False, b""))
        entries: list[SkillEntry] = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            _snapshot_entry(
                child,
                child.name,
                entries,
                ignore_source_artifacts=ignore_source_artifacts,
            )
        _validate_directory_unchanged(directory, metadata)
        root_entries.extend(
            SkillEntry(
                f"{directory.name}/{entry.path}",
                entry.kind,
                entry.executable,
                entry.content,
            )
            for entry in entries
        )
    _validate_directory_unchanged(root, root_metadata)
    return _collection_from_root_entries(
        root, tuple(sorted(root_entries, key=lambda entry: entry.path))
    )


def snapshot_ordinary_tree(root: Path) -> tuple[SkillEntry, ...]:
    """Snapshot a raw ordinary tree without imposing skill metadata semantics."""
    root_metadata = root.lstat()
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        raise _error(root, "tree root must be an ordinary directory")
    entries: list[SkillEntry] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        _snapshot_entry(
            child,
            child.name,
            entries,
            ignore_source_artifacts=False,
        )
    _validate_directory_unchanged(root, root_metadata)
    return tuple(sorted(entries, key=lambda entry: entry.path))


def _same_bound_snapshot(
    observed: os.stat_result, current: os.stat_result, *, directory: bool
) -> bool:
    fields = ("st_dev", "st_ino", "st_mode", "st_ctime_ns")
    if not directory:
        fields += ("st_size", "st_mtime_ns")
    return all(getattr(observed, field) == getattr(current, field) for field in fields)


def _bound_directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _bound_file_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _remove_entry_subtree(entries: list[SkillEntry], relative: str) -> None:
    if not relative:
        entries.clear()
        return
    entries[:] = [
        entry
        for entry in entries
        if entry.path != relative and not entry.path.startswith(relative + "/")
    ]


def _read_bound_file(
    parent_descriptor: int, name: str, observed: os.stat_result
) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(name, _bound_file_flags(), dir_fd=parent_descriptor)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not _same_bound_snapshot(observed, opened, directory=False)
        ):
            raise ValueError("file changed while being opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if not _same_bound_snapshot(opened, final, directory=False):
            raise ValueError("file changed while being read")
        content = b"".join(chunks)
        if len(content) != final.st_size:
            raise ValueError("file size changed while being read")
        return content, opened
    finally:
        os.close(descriptor)


def _snapshot_bound_directory(
    descriptor: int,
    relative: str,
    entries: list[SkillEntry],
    unsafe_entries: list[UnsafeSkillEntry],
) -> None:
    before = os.fstat(descriptor)
    try:
        names = sorted(os.listdir(descriptor))
    except (OSError, NotImplementedError):
        unsafe_entries.append(UnsafeSkillEntry(relative or ".", "read-error"))
        _remove_entry_subtree(entries, relative)
        return
    for name in names:
        child_relative = f"{relative}/{name}" if relative else name
        try:
            observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError:
            unsafe_entries.append(UnsafeSkillEntry(child_relative, "read-error"))
            continue
        mode = observed.st_mode
        if stat.S_ISLNK(mode):
            unsafe_entries.append(UnsafeSkillEntry(child_relative, "symlink"))
            continue
        if stat.S_ISDIR(mode):
            child_descriptor = None
            try:
                child_descriptor = os.open(
                    name, _bound_directory_flags(), dir_fd=descriptor
                )
                opened = os.fstat(child_descriptor)
                if not _same_bound_snapshot(observed, opened, directory=True):
                    raise ValueError("directory changed while being opened")
                entries.append(SkillEntry(child_relative, "directory", False, b""))
                _snapshot_bound_directory(
                    child_descriptor, child_relative, entries, unsafe_entries
                )
                final = os.fstat(child_descriptor)
                attached = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not _same_bound_snapshot(opened, final, directory=True) or not (
                    _same_bound_snapshot(opened, attached, directory=True)
                ):
                    raise ValueError("directory changed during scan")
            except OSError:
                unsafe_entries.append(UnsafeSkillEntry(child_relative, "read-error"))
                _remove_entry_subtree(entries, child_relative)
            except ValueError:
                unsafe_entries.append(UnsafeSkillEntry(child_relative, "changed"))
                _remove_entry_subtree(entries, child_relative)
            finally:
                if child_descriptor is not None:
                    os.close(child_descriptor)
            continue
        if not stat.S_ISREG(mode):
            unsafe_entries.append(UnsafeSkillEntry(child_relative, "special"))
            continue
        if observed.st_nlink != 1:
            unsafe_entries.append(UnsafeSkillEntry(child_relative, "hard-link"))
            continue
        try:
            content, opened = _read_bound_file(descriptor, name, observed)
            attached = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not _same_bound_snapshot(opened, attached, directory=False):
                raise ValueError("file changed after being read")
        except OSError:
            unsafe_entries.append(UnsafeSkillEntry(child_relative, "read-error"))
            continue
        except ValueError:
            unsafe_entries.append(UnsafeSkillEntry(child_relative, "changed"))
            continue
        entries.append(
            SkillEntry(
                child_relative,
                "file",
                bool(opened.st_mode & 0o111),
                content,
            )
        )
    final = os.fstat(descriptor)
    if not _same_bound_snapshot(before, final, directory=True):
        unsafe_entries.append(UnsafeSkillEntry(relative or ".", "changed"))
        _remove_entry_subtree(entries, relative)


def _snapshot_posix_bound_tree(
    anchor: Path, root: Path
) -> tuple[tuple[SkillEntry, ...], tuple[UnsafeSkillEntry, ...]]:
    anchor = Path(os.path.abspath(os.fspath(anchor)))
    root = Path(os.path.abspath(os.fspath(root)))
    try:
        relative = root.relative_to(anchor)
    except ValueError:
        return (), (UnsafeSkillEntry(".", "changed"),)
    try:
        anchor_observed = anchor.lstat()
    except FileNotFoundError:
        return (), ()
    except (OSError, NotImplementedError):
        return (), (UnsafeSkillEntry(".", "read-error"),)
    if stat.S_ISLNK(anchor_observed.st_mode):
        return (), (UnsafeSkillEntry(".", "symlink"),)
    if not stat.S_ISDIR(anchor_observed.st_mode):
        return (), (UnsafeSkillEntry(".", "special"),)

    descriptors: list[int] = []
    bindings: list[tuple[int, str, int, os.stat_result]] = []
    try:
        current = os.open(anchor, _bound_directory_flags())
        descriptors.append(current)
        anchor_opened = os.fstat(current)
        if not _same_bound_snapshot(anchor_observed, anchor_opened, directory=True):
            return (), (UnsafeSkillEntry(".", "changed"),)
        for part in relative.parts:
            try:
                observed = os.stat(part, dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                return (), ()
            except OSError:
                return (), (UnsafeSkillEntry(".", "read-error"),)
            if stat.S_ISLNK(observed.st_mode):
                return (), (UnsafeSkillEntry(".", "symlink"),)
            if not stat.S_ISDIR(observed.st_mode):
                return (), (UnsafeSkillEntry(".", "special"),)
            try:
                child = os.open(part, _bound_directory_flags(), dir_fd=current)
            except OSError:
                return (), (UnsafeSkillEntry(".", "read-error"),)
            descriptors.append(child)
            opened = os.fstat(child)
            if not _same_bound_snapshot(observed, opened, directory=True):
                return (), (UnsafeSkillEntry(".", "changed"),)
            bindings.append((current, part, child, opened))
            current = child

        entries: list[SkillEntry] = []
        unsafe_entries: list[UnsafeSkillEntry] = []
        _snapshot_bound_directory(current, "", entries, unsafe_entries)
        try:
            anchor_attached = anchor.lstat()
        except OSError:
            return (), (UnsafeSkillEntry(".", "read-error"),)
        if not _same_bound_snapshot(anchor_opened, anchor_attached, directory=True):
            return (), (UnsafeSkillEntry(".", "changed"),)
        for parent, name, child, opened in bindings:
            try:
                attached = os.stat(name, dir_fd=parent, follow_symlinks=False)
                final = os.fstat(child)
            except OSError:
                return (), (UnsafeSkillEntry(".", "read-error"),)
            if not _same_bound_snapshot(opened, attached, directory=True) or not (
                _same_bound_snapshot(opened, final, directory=True)
            ):
                return (), (UnsafeSkillEntry(".", "changed"),)
        return (
            tuple(sorted(entries, key=lambda entry: entry.path)),
            tuple(
                sorted(set(unsafe_entries), key=lambda item: (item.path, item.reason))
            ),
        )
    except (OSError, NotImplementedError):
        return (), (UnsafeSkillEntry(".", "read-error"),)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _deepest_existing_ordinary_directory(anchor: Path, root: Path) -> Path:
    """Return the deepest existing root ancestor, rejecting links and reparses."""
    anchor = Path(os.path.abspath(os.fspath(anchor)))
    root = Path(os.path.abspath(os.fspath(root)))
    try:
        relative = root.relative_to(anchor)
    except ValueError as exc:
        raise ValueError("tree root escapes its anchor") from exc

    current = anchor
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return current.parent
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or _is_reparse_point(metadata)
        ):
            raise ValueError(
                f"tree anchor path must contain only ordinary directories: {current}"
            )
    return current


def _open_windows_directory_guards(anchor: Path, path: Path) -> list[int]:
    from .local_state import _windows_directory_guard

    return _windows_directory_guard(anchor, (path,))


def _close_windows_directory_guards(handles: list[int]) -> None:
    from .local_state import _close_windows_handles

    _close_windows_handles(handles)


def _hold_windows_directory_guard(
    anchor: Path, path: Path, handles: list[int]
) -> None:
    observed = path.lstat()
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or _is_reparse_point(observed)
    ):
        raise ValueError(f"Windows guarded path is not an ordinary directory: {path}")
    acquired = _open_windows_directory_guards(anchor, path)
    try:
        attached = path.lstat()
        if not _same_bound_snapshot(observed, attached, directory=True):
            raise ValueError(f"Windows guarded directory changed while opening: {path}")
    except BaseException:
        _close_windows_directory_guards(acquired)
        raise
    handles.extend(acquired)


def _snapshot_windows_guarded_tree(
    anchor: Path, root: Path
) -> tuple[tuple[SkillEntry, ...], tuple[UnsafeSkillEntry, ...]]:
    try:
        deepest = _deepest_existing_ordinary_directory(anchor, root)
    except (OSError, ValueError):
        return (), (UnsafeSkillEntry(".", "changed"),)
    if deepest != Path(os.path.abspath(os.fspath(root))):
        handles: list[int] = []
        try:
            _hold_windows_directory_guard(anchor, deepest, handles)
        except (OSError, RuntimeError, ValueError):
            return (), (UnsafeSkillEntry(".", "read-error"),)
        try:
            if not root.exists() and not root.is_symlink():
                return (), ()
            return (), (UnsafeSkillEntry(".", "changed"),)
        finally:
            _close_windows_directory_guards(handles)

    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return (), ()
    except OSError:
        return (), (UnsafeSkillEntry(".", "read-error"),)
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
        return (), (UnsafeSkillEntry(".", "symlink"),)
    if not stat.S_ISDIR(metadata.st_mode):
        return (), (UnsafeSkillEntry(".", "special"),)

    handles: list[int] = []
    try:
        _hold_windows_directory_guard(anchor, root, handles)
        entries: list[SkillEntry] = []
        unsafe_entries: list[UnsafeSkillEntry] = []
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError:
            return (), (UnsafeSkillEntry(".", "read-error"),)
        for child in children:
            _snapshot_entry(
                child,
                child.name,
                entries,
                ignore_source_artifacts=False,
                unsafe_entries=unsafe_entries,
                windows_anchor=anchor,
                windows_handles=handles,
            )
        try:
            _validate_directory_unchanged(root, metadata)
        except (OSError, ValueError):
            return (), (UnsafeSkillEntry(".", "changed"),)
        return (
            tuple(sorted(entries, key=lambda entry: entry.path)),
            tuple(
                sorted(set(unsafe_entries), key=lambda item: (item.path, item.reason))
            ),
        )
    except (OSError, RuntimeError, ValueError):
        return (), (UnsafeSkillEntry(".", "read-error"),)
    finally:
        _close_windows_directory_guards(handles)


def snapshot_ordinary_tree_with_unsafe(
    root: Path, *, anchor: Path | None = None
) -> tuple[tuple[SkillEntry, ...], tuple[UnsafeSkillEntry, ...]]:
    """Snapshot ordinary entries and report unsafe paths without following them."""
    if anchor is not None:
        if _SUPPORTS_BOUND_TREE_WALK:
            return _snapshot_posix_bound_tree(anchor, root)
        if os.name != "nt":
            return (), (UnsafeSkillEntry(".", "read-error"),)
        return _snapshot_windows_guarded_tree(anchor, root)
    try:
        root_metadata = root.lstat()
    except FileNotFoundError:
        return (), ()
    except OSError:
        return (), (UnsafeSkillEntry(".", "read-error"),)
    if stat.S_ISLNK(root_metadata.st_mode):
        return (), (UnsafeSkillEntry(".", "symlink"),)
    if not stat.S_ISDIR(root_metadata.st_mode):
        return (), (UnsafeSkillEntry(".", "special"),)

    entries: list[SkillEntry] = []
    unsafe_entries: list[UnsafeSkillEntry] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return (), (UnsafeSkillEntry(".", "read-error"),)
    for child in children:
        _snapshot_entry(
            child,
            child.name,
            entries,
            ignore_source_artifacts=False,
            unsafe_entries=unsafe_entries,
        )
    try:
        _validate_directory_unchanged(root, root_metadata)
    except (OSError, ValueError):
        return (), (UnsafeSkillEntry(".", "changed"),)
    return (
        tuple(sorted(entries, key=lambda entry: entry.path)),
        tuple(sorted(set(unsafe_entries), key=lambda item: (item.path, item.reason))),
    )


_UNSAFE_DISCOVERY_MESSAGES = {
    "symlink": "symbolic links are not allowed",
    "hard-link": "multiply-linked regular files are not allowed",
    "special": "special files are not allowed",
    "changed": "skill tree changed during scan",
    "read-error": "skill tree could not be read safely",
}


def discover_anchored_skill_collection(root: Path, *, anchor: Path) -> SkillCollection:
    """Discover skills from one anchor-bound raw snapshot and no path re-reads."""
    entries, unsafe = snapshot_ordinary_tree_with_unsafe(root, anchor=anchor)
    if unsafe:
        finding = unsafe[0]
        path = root if finding.path == "." else root.joinpath(*finding.path.split("/"))
        raise _error(path, _UNSAFE_DISCOVERY_MESSAGES[finding.reason])
    return _collection_from_root_entries(root, entries)


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

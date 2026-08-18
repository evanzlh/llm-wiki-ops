"""Render the packaged external-repository adapter from captured skill metadata."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import secrets
import stat
import sys
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

from . import IMPLEMENTATION_ID, skill_trees
from .frontmatter import FrontmatterError, parse_frontmatter
from .skill_names import is_safe_skill_name
from .skill_trees import SkillCollection, SkillEntry, SkillTree

ADAPTER_NAME = "llm-wiki-ops"
ADAPTER_DESCRIPTION = (
    "Use when any request asks to access or operate on an external LLMWikiOps wiki, "
    "including querying, ingesting, maintaining, or recovering it, whether or not "
    "the user has supplied its repository root."
)
BUILTIN_CATALOG_START = "<!-- LLMWIKIOPS_BUILTIN_CATALOG_START -->"
BUILTIN_CATALOG_END = "<!-- LLMWIKIOPS_BUILTIN_CATALOG_END -->"
_ADAPTER_TEMPLATE = Path(__file__).parent / "_data" / "adapter" / "SKILL.md.in"
MANAGED_ADAPTER_RECORD = ".llmwikiops-managed.json"
MANAGED_ADAPTER_SCHEMA_VERSION = 1
_RETAINED_ADAPTER_ROOT = ".llmwikiops-retained"
_RETAINED_ADAPTER_PREFIX = ".llmwikiops-retained-"
_MANAGED_ADAPTER_FIELDS = frozenset(
    {"schema_version", "implementation", "cli_version", "target", "files"}
)
_FILE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONCRETE_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class AgentTarget:
    """One explicitly supported agent skill installation location."""

    name: str
    relative_skill_root: PurePosixPath

    def __post_init__(self) -> None:
        if type(self.name) is not str or not is_safe_skill_name(self.name):
            raise ValueError("adapter target name must be canonical and safe")
        if type(self.relative_skill_root) is not PurePosixPath:
            raise TypeError("adapter target skill root must be a PurePosixPath")
        root = self.relative_skill_root
        if (
            root.is_absolute()
            or not root.parts
            or any(part in {"", ".", ".."} for part in root.parts)
        ):
            raise ValueError("adapter target skill root must be a safe relative path")


TARGETS: Mapping[str, AgentTarget] = MappingProxyType(
    {
        target.name: target
        for target in (
            AgentTarget("codex", PurePosixPath(".codex/skills")),
            AgentTarget("claude", PurePosixPath(".claude/skills")),
            AgentTarget("cursor", PurePosixPath(".cursor/skills")),
            AgentTarget("windsurf", PurePosixPath(".codeium/windsurf/skills")),
            AgentTarget("opencode", PurePosixPath(".config/opencode/skills")),
            AgentTarget("pi", PurePosixPath(".pi/agent/skills")),
            AgentTarget("kiro", PurePosixPath(".kiro/skills")),
        )
    }
)


def _require_target_name(target: object) -> str:
    if type(target) is not str or target not in TARGETS:
        raise ValueError(f"unknown or noncanonical adapter target: {target!r}")
    return target


def _require_absolute_root(root: object, label: str) -> Path:
    if type(root) is not _CONCRETE_PATH_TYPE:
        raise TypeError(f"{label} must be the concrete platform pathlib.Path type")
    if not root.is_absolute() or "\x00" in str(root) or ".." in root.parts:
        raise ValueError(f"{label} must be an absolute contained path")
    return root


def resolve_adapter_destination(
    target: object, *, home: Path, environ: Mapping[str, str]
) -> Path:
    """Resolve one explicit target without probing or mutating the filesystem."""
    target_name = _require_target_name(target)
    home_root = _require_absolute_root(home, "home")
    if not isinstance(environ, Mapping):
        raise TypeError("environ must be a mapping")

    if target_name == "codex" and "CODEX_HOME" in environ:
        override = environ["CODEX_HOME"]
        if type(override) is not str or not override or "\x00" in override:
            raise ValueError("CODEX_HOME must be a non-empty absolute path")
        override_root = Path(override)
        if not override_root.is_absolute() or ".." in override_root.parts:
            raise ValueError("CODEX_HOME must be a non-empty absolute path")
        return override_root / "skills" / ADAPTER_NAME

    definition = TARGETS[target_name]
    return home_root.joinpath(*definition.relative_skill_root.parts, ADAPTER_NAME)


def _validate_cli_version(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or any(
            character.isspace() or not character.isprintable() for character in value
        )
    ):
        raise ValueError("cli_version must be a non-empty printable token")
    return value


def _validate_managed_filename(value: object) -> str:
    if (
        type(value) is not str
        or unicodedata.normalize("NFC", value) != value
        or not is_safe_skill_name(value)
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or value == MANAGED_ADAPTER_RECORD
    ):
        raise ValueError(f"unsafe or noncanonical managed adapter filename: {value!r}")
    return value


def _validate_managed_files(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("managed adapter files must be a non-empty mapping")
    copied: dict[str, str] = {}
    for filename, digest in value.items():
        safe_name = _validate_managed_filename(filename)
        if type(digest) is not str or _FILE_DIGEST.fullmatch(digest) is None:
            raise ValueError(f"managed adapter file digest is invalid: {safe_name!r}")
        copied[safe_name] = digest
    if "SKILL.md" not in copied:
        raise ValueError("managed adapter files must include SKILL.md")
    return MappingProxyType(dict(sorted(copied.items())))


@dataclass(frozen=True)
class ManagedAdapterRecord:
    """Strict ownership record for one managed adapter installation."""

    schema_version: int
    implementation: str
    cli_version: str
    target: str
    files: Mapping[str, str]

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != MANAGED_ADAPTER_SCHEMA_VERSION
        ):
            raise ValueError("managed adapter schema_version must be exactly 1")
        if (
            type(self.implementation) is not str
            or self.implementation != IMPLEMENTATION_ID
        ):
            raise ValueError("managed adapter record has wrong implementation")
        _validate_cli_version(self.cli_version)
        _require_target_name(self.target)
        object.__setattr__(self, "files", _validate_managed_files(self.files))


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"managed adapter JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def parse_managed_record(content: bytes) -> ManagedAdapterRecord:
    """Parse one exact canonical-schema ownership record."""
    if type(content) is not bytes:
        raise TypeError("managed adapter record content must be bytes")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("managed adapter record must be valid UTF-8") from exc
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise ValueError("managed adapter record is malformed JSON") from exc
    if type(payload) is not dict or frozenset(payload) != _MANAGED_ADAPTER_FIELDS:
        raise ValueError("managed adapter record fields do not match the exact schema")
    return ManagedAdapterRecord(
        schema_version=payload["schema_version"],
        implementation=payload["implementation"],
        cli_version=payload["cli_version"],
        target=payload["target"],
        files=payload["files"],
    )


def render_managed_record(record: ManagedAdapterRecord) -> bytes:
    """Render canonical sorted UTF-8 JSON with exactly one final newline."""
    if type(record) is not ManagedAdapterRecord:
        raise TypeError("record must be a ManagedAdapterRecord")
    payload = {
        "schema_version": record.schema_version,
        "implementation": record.implementation,
        "cli_version": record.cli_version,
        "target": record.target,
        "files": dict(record.files),
    }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


@dataclass(frozen=True)
class DesiredAdapter:
    """Exact bytes desired for one adapter target, without filesystem effects."""

    target: str
    skill_md: bytes
    managed_record: bytes

    def __post_init__(self) -> None:
        _require_target_name(self.target)
        if type(self.skill_md) is not bytes or type(self.managed_record) is not bytes:
            raise TypeError("desired adapter artifacts must be bytes")
        record = parse_managed_record(self.managed_record)
        if render_managed_record(record) != self.managed_record:
            raise ValueError("desired adapter managed record must use canonical JSON bytes")
        if record.target != self.target:
            raise ValueError("desired adapter target does not match managed record target")
        expected_files = {
            "SKILL.md": "sha256:" + sha256(self.skill_md).hexdigest()
        }
        if record.files != expected_files:
            raise ValueError(
                "desired adapter record files must exactly match its SKILL.md artifact"
            )


def build_desired_adapter(
    target: object, cli_version: object, collection: SkillCollection
) -> DesiredAdapter:
    """Build deterministic adapter and ownership-record bytes without writing them."""
    target_name = _require_target_name(target)
    version = _validate_cli_version(cli_version)
    skill_md = render_adapter_skill(collection).encode("utf-8")
    record = ManagedAdapterRecord(
        schema_version=MANAGED_ADAPTER_SCHEMA_VERSION,
        implementation=IMPLEMENTATION_ID,
        cli_version=version,
        target=target_name,
        files={"SKILL.md": "sha256:" + sha256(skill_md).hexdigest()},
    )
    return DesiredAdapter(
        target=target_name,
        skill_md=skill_md,
        managed_record=render_managed_record(record),
    )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _same_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _same_file(left, right)
        and left.st_mode == right.st_mode
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _read_template(path: Path) -> str:
    try:
        observed = path.lstat()
    except OSError as exc:
        raise ValueError(f"adapter template could not be read safely: {path}") from exc
    if not stat.S_ISREG(observed.st_mode):
        raise ValueError("adapter template must be an ordinary regular file")
    if observed.st_nlink != 1:
        raise ValueError("adapter template must be a single-link regular file")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("adapter template changed or is not an ordinary file") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_snapshot(observed, opened):
            raise ValueError("adapter template changed while being read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if not _same_snapshot(opened, final):
            raise ValueError("adapter template changed while being read")
    finally:
        os.close(descriptor)

    try:
        template = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("adapter template must be valid UTF-8") from exc
    if "\r" in template:
        raise ValueError("adapter template must use LF newlines")
    if not template.endswith("\n") or template.endswith("\n\n"):
        raise ValueError("adapter template must have one final newline")
    return template


def _validate_entry_path(path: object) -> tuple[str, ...]:
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        raise ValueError(f"unsafe skill entry path: {path!r}")
    if path.startswith("/") or path.endswith("/"):
        raise ValueError(f"unsafe skill entry path: {path!r}")
    parts = tuple(path.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe skill entry path: {path!r}")
    return parts


def _validate_entry(entry: SkillEntry) -> tuple[str, ...]:
    parts = _validate_entry_path(entry.path)
    if entry.kind not in {"directory", "file"}:
        raise ValueError(f"invalid captured skill entry kind: {entry.path}")
    if not isinstance(entry.executable, bool) or not isinstance(entry.content, bytes):
        raise TypeError(f"invalid captured skill entry metadata: {entry.path}")
    if entry.kind == "directory" and (entry.executable or entry.content):
        raise ValueError(f"invalid captured skill directory metadata: {entry.path}")
    return parts


def _validate_skill(skill: SkillTree) -> SkillTree:
    if not is_safe_skill_name(skill.name):
        raise ValueError(f"unsafe captured skill name: {skill.name!r}")
    if not isinstance(skill.description, str) or not skill.description:
        raise ValueError(f"captured skill description is required: {skill.name}")
    if not isinstance(skill.entries, tuple) or not skill.entries:
        raise ValueError(f"captured skill entries are required: {skill.name}")

    paths = tuple(entry.path for entry in skill.entries)
    if paths != tuple(sorted(paths)):
        raise ValueError(f"captured skill entries must be sorted: {skill.name}")
    if len(paths) != len(set(paths)):
        raise ValueError(f"duplicate captured skill entry path: {skill.name}")

    parts_by_path = {entry.path: _validate_entry(entry) for entry in skill.entries}
    directories = {
        entry.path for entry in skill.entries if entry.kind == "directory"
    }
    for path, parts in parts_by_path.items():
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent not in directories:
                raise ValueError(
                    f"orphan captured skill entry has no parent directory: {path}"
                )

    direct_skill_files = [
        entry
        for entry in skill.entries
        if entry.path == "SKILL.md" and entry.kind == "file"
    ]
    if len(direct_skill_files) != 1:
        raise ValueError(
            f"captured skill topology requires one direct SKILL.md file: {skill.name}"
        )

    rebuilt = skill_trees._skill_tree_from_entries(  # type: ignore[attr-defined]
        Path("<captured-skill-collection>"), skill.name, skill.entries
    )
    if rebuilt.description != skill.description:
        raise ValueError(
            f"captured skill metadata description does not match SKILL.md: {skill.name}"
        )
    if rebuilt.digest != skill.digest:
        raise ValueError(f"captured skill digest does not match entries: {skill.name}")
    return rebuilt


def _validated_catalog(collection: SkillCollection) -> list[dict[str, str]]:
    if not isinstance(collection, SkillCollection) or not collection.skills:
        raise ValueError("skill collection must not be empty")
    if not isinstance(collection.skills, tuple):
        raise TypeError("captured skills must be a tuple")
    names = tuple(skill.name for skill in collection.skills)
    if len(names) != len(set(names)):
        raise ValueError("duplicate skill name in captured collection")
    if names != tuple(sorted(names)):
        raise ValueError("captured skill collection must be sorted")

    validated = tuple(_validate_skill(skill) for skill in collection.skills)
    return [
        {"name": skill.name, "description": skill.description}
        for skill in validated
    ]


def _encoded_catalog(catalog: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        catalog,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    # JSON permits literal '<'. Escaping it preserves json.loads() values while
    # preventing metadata from manufacturing HTML catalog delimiters.
    return encoded.replace("<", "\\u003c")


def _validate_template_frontmatter(template: str) -> None:
    try:
        parsed = parse_frontmatter(template)
    except FrontmatterError as exc:
        raise ValueError("adapter template frontmatter is invalid") from exc
    if parsed.fields != {"name", "description"}:
        raise ValueError(
            "adapter template frontmatter fields must be exactly name and description"
        )
    if parsed.scalars.get("name") != ADAPTER_NAME:
        raise ValueError(f"adapter template frontmatter name must be {ADAPTER_NAME}")
    if parsed.scalars.get("description") != ADAPTER_DESCRIPTION:
        raise ValueError("adapter template frontmatter description is not approved")


def render_adapter_skill(collection: SkillCollection) -> str:
    """Return one deterministic adapter skill containing catalog metadata only."""
    catalog = _validated_catalog(collection)
    template = _read_template(_ADAPTER_TEMPLATE)
    _validate_template_frontmatter(template)
    placeholder = BUILTIN_CATALOG_START + "\n" + BUILTIN_CATALOG_END
    if (
        template.count(BUILTIN_CATALOG_START) != 1
        or template.count(BUILTIN_CATALOG_END) != 1
        or placeholder not in template
        or template.index(BUILTIN_CATALOG_START) > template.index(BUILTIN_CATALOG_END)
    ):
        raise ValueError(
            "adapter template requires exactly one ordered empty catalog placeholder"
        )

    rendered = template.replace(
        placeholder,
        BUILTIN_CATALOG_START
        + "\n"
        + _encoded_catalog(catalog)
        + "\n"
        + BUILTIN_CATALOG_END,
        1,
    )
    if (
        rendered.count(BUILTIN_CATALOG_START) != 1
        or rendered.count(BUILTIN_CATALOG_END) != 1
        or rendered.index(BUILTIN_CATALOG_START) > rendered.index(BUILTIN_CATALOG_END)
    ):
        raise ValueError("rendered adapter catalog markers are not unique and ordered")
    if "\r" in rendered or not rendered.endswith("\n") or rendered.endswith("\n\n"):
        raise ValueError("rendered adapter must use UTF-8/LF with one final newline")
    rendered.encode("utf-8")
    return rendered


@dataclass(frozen=True)
class ManagedFileSnapshot:
    name: str
    identity: tuple[int, ...]
    content: bytes


@dataclass(frozen=True)
class ManagedTreeSnapshot:
    name: str
    identity: tuple[int, ...]
    files: tuple[ManagedFileSnapshot, ...]


@dataclass(frozen=True)
class AdapterInstallInspection:
    status: Literal[
        "missing", "current", "managed-upgrade", "owner-drift", "unmanaged", "error"
    ]
    snapshot: ManagedTreeSnapshot | None
    error: str | None = None


@dataclass(frozen=True)
class AdapterInstallResult:
    status: Literal["installed", "unchanged", "upgraded"]
    target: str
    destination: Path


class _UnknownManagedEntry(ValueError):
    pass


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
    )


def _directory_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("O_NOFOLLOW is required for safe adapter access")
    if not hasattr(os, "O_DIRECTORY"):
        raise ValueError("O_DIRECTORY is required for safe adapter access")
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_DIRECTORY | os.O_NOFOLLOW


def _regular_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("O_NOFOLLOW is required for safe adapter access")
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW


def _safe_component(name: object) -> str:
    if (
        type(name) is not str
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise ValueError("unsafe filesystem component")
    return name


def _open_directory_path(path: Path) -> int:
    root = _require_absolute_root(path, "directory")
    descriptor = os.open("/", _directory_flags())
    try:
        for component in root.parts[1:]:
            observed = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(observed.st_mode):
                raise ValueError("directory topology contains a non-directory")
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if not stat.S_ISDIR(opened.st_mode) or not _same_file(observed, opened):
                    raise ValueError("directory topology changed while opening")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _open_or_create_directory(path: Path) -> Iterator[int]:
    """Open/create an absolute directory chain without following links."""
    root = _require_absolute_root(path, "directory")
    descriptor = os.open("/", _directory_flags())
    try:
        for component in root.parts[1:]:
            try:
                observed = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                observed = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(observed.st_mode):
                raise ValueError("directory topology contains a non-directory")
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if not stat.S_ISDIR(opened.st_mode) or not _same_file(observed, opened):
                    raise ValueError("directory topology changed while opening")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _read_regular_file(parent_fd: int, name: str) -> ManagedFileSnapshot:
    safe_name = _safe_component(name)
    observed = os.stat(safe_name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise ValueError(
            f"managed file is not a single-link ordinary file: {safe_name}"
        )
    descriptor = os.open(safe_name, _regular_flags(), dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_snapshot(observed, opened):
            raise ValueError(f"managed file changed while opening: {safe_name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        final = os.fstat(descriptor)
        rebound = os.stat(safe_name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not _same_snapshot(opened, final)
            or not stat.S_ISREG(rebound.st_mode)
            or not _same_snapshot(final, rebound)
        ):
            raise ValueError(f"managed file changed while reading: {safe_name}")
        return ManagedFileSnapshot(safe_name, _identity(final), b"".join(chunks))
    finally:
        os.close(descriptor)


def _snapshot_bound_directory(
    parent_fd: int, name: str, descriptor: int
) -> ManagedTreeSnapshot:
    """Snapshot a managed directory through an already-open bound descriptor."""
    safe_name = _safe_component(name)
    opened = os.fstat(descriptor)
    rebound = os.stat(safe_name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(rebound.st_mode)
        or not _same_file(opened, rebound)
    ):
        raise ValueError("managed adapter directory changed while opening")
    names = tuple(sorted(os.listdir(descriptor)))
    allowed = {"SKILL.md", MANAGED_ADAPTER_RECORD}
    unknown = set(names) - allowed
    if unknown:
        raise _UnknownManagedEntry("managed adapter contains unknown entries")
    files = tuple(_read_regular_file(descriptor, child) for child in names)
    final_names = tuple(sorted(os.listdir(descriptor)))
    final = os.fstat(descriptor)
    rebound = os.stat(safe_name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        final_names != names
        or not _same_file(opened, final)
        or not stat.S_ISDIR(rebound.st_mode)
        or not _same_file(final, rebound)
    ):
        raise ValueError("managed adapter directory changed while reading")
    return ManagedTreeSnapshot(safe_name, _identity(final), files)


def _snapshot_child(parent_fd: int, name: str) -> ManagedTreeSnapshot:
    """Capture the small managed tree using only descriptor-relative operations."""
    safe_name = _safe_component(name)
    observed = os.stat(safe_name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(observed.st_mode):
        raise ValueError("managed adapter is not an ordinary directory")
    descriptor = os.open(safe_name, _directory_flags(), dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_file(observed, opened):
            raise ValueError("managed adapter directory changed while opening")
        return _snapshot_bound_directory(parent_fd, safe_name, descriptor)
    finally:
        os.close(descriptor)


def _file_map(snapshot: ManagedTreeSnapshot) -> dict[str, bytes]:
    return {item.name: item.content for item in snapshot.files}


def _classify_snapshot(
    snapshot: ManagedTreeSnapshot, desired: DesiredAdapter
) -> AdapterInstallInspection:
    files = _file_map(snapshot)
    skill = files.get("SKILL.md")
    record_bytes = files.get(MANAGED_ADAPTER_RECORD)
    if record_bytes is None:
        return AdapterInstallInspection("unmanaged", snapshot, None)
    try:
        record = parse_managed_record(record_bytes)
    except (TypeError, ValueError):
        return AdapterInstallInspection("unmanaged", snapshot, None)
    if render_managed_record(record) != record_bytes or record.target != desired.target:
        return AdapterInstallInspection("unmanaged", snapshot, None)
    expected_digest = record.files.get("SKILL.md")
    if (
        skill is None
        or set(record.files) != {"SKILL.md"}
        or expected_digest != "sha256:" + sha256(skill).hexdigest()
    ):
        return AdapterInstallInspection("owner-drift", snapshot, None)
    if skill == desired.skill_md and record_bytes == desired.managed_record:
        return AdapterInstallInspection("current", snapshot, None)
    return AdapterInstallInspection("managed-upgrade", snapshot, None)


def inspect_adapter_installation(
    destination: Path, desired: DesiredAdapter
) -> AdapterInstallInspection:
    """Classify one exact adapter destination without modifying it."""
    _require_absolute_root(destination, "destination")
    if type(desired) is not DesiredAdapter:
        raise TypeError("desired must be a DesiredAdapter")
    parent_fd: int | None = None
    try:
        try:
            parent_fd = _open_directory_path(destination.parent)
        except FileNotFoundError:
            return AdapterInstallInspection("missing", None, None)
        try:
            os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return AdapterInstallInspection("missing", None, None)
        try:
            snapshot = _snapshot_child(parent_fd, destination.name)
        except _UnknownManagedEntry as exc:
            return AdapterInstallInspection("owner-drift", None, str(exc))
        except (OSError, ValueError) as exc:
            return AdapterInstallInspection("error", None, str(exc))
        return _classify_snapshot(snapshot, desired)
    except (OSError, ValueError) as exc:
        return AdapterInstallInspection("error", None, str(exc))
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("short write while staging adapter")
        offset += written


def _write_stage(
    parent_fd: int,
    name: str,
    desired: DesiredAdapter,
    checkpoint: Callable[[str], None],
    capture: Callable[[ManagedTreeSnapshot], None],
) -> ManagedTreeSnapshot:
    os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    created = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(created.st_mode):
        raise ValueError("staged adapter was replaced immediately after creation")
    created_identity = _identity(created)
    try:
        stage_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except BaseException:
        try:
            empty = _snapshot_child(parent_fd, name)
            if empty.identity == created_identity and not empty.files:
                capture(empty)
        except (OSError, ValueError):
            pass
        raise
    try:

        def capture_bound_stage() -> None:
            capture(_snapshot_bound_directory(parent_fd, name, stage_fd))

        capture_bound_stage()
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        skill_fd = os.open("SKILL.md", flags, 0o600, dir_fd=stage_fd)
        try:
            _write_all(skill_fd, desired.skill_md)
            os.fsync(skill_fd)
        finally:
            os.close(skill_fd)
        os.fsync(stage_fd)
        capture_bound_stage()
        checkpoint("staged-files")
        record_fd = os.open(MANAGED_ADAPTER_RECORD, flags, 0o600, dir_fd=stage_fd)
        try:
            _write_all(record_fd, desired.managed_record)
            os.fsync(record_fd)
        finally:
            os.close(record_fd)
        os.fsync(stage_fd)
        capture_bound_stage()
        checkpoint("staged-record")
    except BaseException:
        try:
            capture_bound_stage()
        except (OSError, ValueError):
            # A concurrently replaced tree is left in its current namespace;
            # the last identity-bound snapshot will not match it.
            pass
        raise
    finally:
        os.close(stage_fd)
    snapshot = _snapshot_child(parent_fd, name)
    classified = _classify_snapshot(snapshot, desired)
    if classified.status != "current":
        raise ValueError("staged adapter verification failed")
    return snapshot


def _same_filesystem(left_fd: int, right_fd: int) -> bool:
    return os.fstat(left_fd).st_dev == os.fstat(right_fd).st_dev


def _validate_retention_directory(descriptor: int) -> None:
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode):
        raise ValueError("adapter retention root must be an ordinary directory")
    if stat.S_IMODE(observed.st_mode) != 0o700:
        raise ValueError("adapter retention root must have mode 0700")


def _verify_directory_path_binding(path: Path, descriptor: int) -> None:
    expected = os.fstat(descriptor)
    reopened = _open_directory_path(path)
    try:
        observed = os.fstat(reopened)
        if not _same_file(expected, observed):
            raise ValueError("adapter retention root identity changed")
    finally:
        os.close(reopened)


@contextmanager
def _open_retention_directory(path: Path) -> Iterator[int]:
    with _open_or_create_directory(path) as descriptor:
        _validate_retention_directory(descriptor)
        _verify_directory_path_binding(path, descriptor)
        try:
            yield descriptor
        finally:
            _verify_directory_path_binding(path, descriptor)


@contextmanager
def _open_retention_for_source(path: Path, source_parent_fd: int) -> Iterator[int]:
    with _open_retention_directory(path) as retained_parent_fd:
        if not _same_filesystem(source_parent_fd, retained_parent_fd):
            raise OSError(
                errno.EXDEV, "adapter skills and retention root must share a filesystem"
            )
        yield retained_parent_fd


def _retain_snapshot(
    source_parent_fd: int,
    retained_parent_fd: int,
    snapshot: ManagedTreeSnapshot,
) -> ManagedTreeSnapshot:
    """Atomically move verified evidence out of the active recovery namespace."""
    _validate_retention_directory(retained_parent_fd)
    if not _same_filesystem(source_parent_fd, retained_parent_fd):
        raise OSError(errno.EXDEV, "retention root is on a different filesystem")
    current = _snapshot_child(source_parent_fd, snapshot.name)
    if current != snapshot:
        raise ValueError("managed evidence changed before retention")
    token = secrets.token_hex(16)
    if type(token) is not str or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("retention token must be 32 lowercase hexadecimal characters")
    retained_name = _RETAINED_ADAPTER_PREFIX + token
    _rename_noreplace_between(
        source_parent_fd,
        snapshot.name,
        retained_parent_fd,
        retained_name,
    )
    retained = _snapshot_child(retained_parent_fd, retained_name)
    expected = ManagedTreeSnapshot(retained_name, snapshot.identity, snapshot.files)
    if retained != expected:
        raise ValueError("retained managed evidence changed; preserving evidence")
    try:
        os.stat(snapshot.name, dir_fd=source_parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise ValueError("active managed name was rebuilt; preserving evidence")
    os.fsync(retained_parent_fd)
    os.fsync(source_parent_fd)
    return retained


def _artifact_names(parent_fd: int) -> tuple[str, ...]:
    prefixes = (f".{ADAPTER_NAME}.stage-", f".{ADAPTER_NAME}.backup-")
    return tuple(
        sorted(name for name in os.listdir(parent_fd) if name.startswith(prefixes))
    )


def _rename_noreplace_between(
    source_parent_fd: int,
    source: str,
    destination_parent_fd: int,
    destination: str,
) -> None:
    """Rename between descriptor-bound directories without replacement."""
    safe_source = _safe_component(source)
    safe_destination = _safe_component(destination)
    _call_atomic_noreplace(
        source_parent_fd,
        os.fsencode(safe_source),
        destination_parent_fd,
        os.fsencode(safe_destination),
    )


def _call_atomic_noreplace(
    source_parent_fd: int,
    source: bytes,
    destination_parent_fd: int,
    destination: bytes,
) -> None:
    """Call the selected host primitive with already encoded path arguments."""
    rename, flag = _resolve_atomic_noreplace()
    ctypes.set_errno(0)
    result = rename(
        source_parent_fd,
        source,
        destination_parent_fd,
        destination,
        flag,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), os.fsdecode(destination))


def _resolve_atomic_noreplace(
    platform: str | None = None, library: Any | None = None
) -> tuple[Any, int]:
    """Resolve the host's descriptor-relative atomic no-replace primitive."""
    actual_platform = sys.platform if platform is None else platform
    if actual_platform.startswith("linux"):
        symbol = "renameat2"
        flag = 1  # RENAME_NOREPLACE
    elif actual_platform == "darwin":
        symbol = "renameatx_np"
        flag = 0x4  # RENAME_EXCL
    else:
        raise OSError(
            errno.ENOTSUP,
            f"atomic no-replace rename is unsupported on {actual_platform}",
        )
    actual_library = ctypes.CDLL(None, use_errno=True) if library is None else library
    try:
        rename = getattr(actual_library, symbol)
    except AttributeError as exc:
        raise OSError(
            errno.ENOTSUP, f"{symbol} atomic no-replace rename is required"
        ) from exc
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    return rename, flag


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    """Rename within one directory without ever replacing the destination."""
    _rename_noreplace_between(parent_fd, source, parent_fd, destination)


def _probe_rename_noreplace(
    source_parent_fd: int, destination_parent_fd: int
) -> None:
    """Exercise atomic no-replace with an uncreatable empty source path."""
    try:
        _call_atomic_noreplace(source_parent_fd, b"", destination_parent_fd, b"")
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return
        raise
    raise ValueError("empty-path rename capability probe unexpectedly succeeded")


def _verified_artifact(
    parent_fd: int, name: str, desired: DesiredAdapter
) -> ManagedTreeSnapshot:
    pattern = re.compile(
        rf"^\.{re.escape(ADAPTER_NAME)}\.(?:stage|backup)-[0-9a-f]{{32}}$"
    )
    if pattern.fullmatch(name) is None:
        raise ValueError("recovery artifact has a noncanonical name")
    snapshot = _snapshot_child(parent_fd, name)
    if ".stage-" in name and stat.S_IMODE(snapshot.identity[2]) != 0o700:
        raise ValueError("recovery artifact has an unsafe directory mode")
    classified = _classify_snapshot(snapshot, desired)
    if classified.status not in {"current", "managed-upgrade"}:
        raise ValueError("ambiguous recovery artifact; preserving evidence")
    return snapshot


def _recover_artifacts(
    parent_fd: int, retention_root: Path, desired: DesiredAdapter
) -> None:
    artifacts = _artifact_names(parent_fd)
    if not artifacts:
        return
    if len(artifacts) != 1:
        raise ValueError("ambiguous recovery artifacts; preserving evidence")
    name = artifacts[0]
    try:
        snapshot = _verified_artifact(parent_fd, name, desired)
    except (OSError, ValueError) as exc:
        raise ValueError("ambiguous recovery artifact; preserving evidence") from exc
    try:
        os.stat(ADAPTER_NAME, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        live = None
    else:
        try:
            live = _snapshot_child(parent_fd, ADAPTER_NAME)
        except FileNotFoundError as exc:
            raise ValueError("live adapter changed during recovery") from exc
    if ".backup-" in name:
        if live is None:
            _rename_noreplace(parent_fd, name, ADAPTER_NAME)
            os.fsync(parent_fd)
            return
        live_state = _classify_snapshot(live, desired)
        if live_state.status != "current":
            raise ValueError("ambiguous live and backup trees; preserving evidence")
        with _open_retention_for_source(retention_root, parent_fd) as retained_fd:
            _retain_snapshot(parent_fd, retained_fd, snapshot)
        return
    if live is None:
        if _classify_snapshot(snapshot, desired).status != "current":
            raise ValueError("staged recovery tree is not desired; preserving evidence")
        _rename_noreplace(parent_fd, name, ADAPTER_NAME)
        os.fsync(parent_fd)
        return
    live_state = _classify_snapshot(live, desired)
    if live_state.status == "current":
        with _open_retention_for_source(retention_root, parent_fd) as retained_fd:
            _retain_snapshot(parent_fd, retained_fd, snapshot)
        return
    if (
        live_state.status == "managed-upgrade"
        and _classify_snapshot(snapshot, desired).status == "current"
    ):
        with _open_retention_for_source(retention_root, parent_fd) as retained_fd:
            _retain_snapshot(parent_fd, retained_fd, snapshot)
        return
    raise ValueError("ambiguous live and staged trees; preserving evidence")


def _checkpoint_callback(
    callback: Callable[[str], None] | None,
) -> Callable[[str], None]:
    return callback if callback is not None else lambda _name: None


def install_adapter(
    target: str,
    *,
    cli_version: str,
    collection: SkillCollection,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    checkpoint: Callable[[str], None] | None = None,
) -> AdapterInstallResult:
    """Install or upgrade one explicitly selected globally managed adapter.

    The legacy ``backup-removed`` checkpoint name is retained for callback
    compatibility. It now means that the verified backup moved out of the active
    recovery namespace into retention; no managed tree is deleted.
    """
    actual_home = Path.home() if home is None else home
    actual_environ = dict(os.environ) if environ is None else environ
    destination = resolve_adapter_destination(
        target, home=actual_home, environ=actual_environ
    )
    desired = build_desired_adapter(target, cli_version, collection)
    invoke = _checkpoint_callback(checkpoint)
    retention_root = destination.parent.parent / _RETAINED_ADAPTER_ROOT
    stage_name: str | None = None
    stage_snapshot: ManagedTreeSnapshot | None = None
    backup_name: str | None = None
    backup_snapshot: ManagedTreeSnapshot | None = None
    with (
        _open_or_create_directory(destination.parent) as parent_fd,
        ExitStack() as retention_stack,
    ):
        _recover_artifacts(parent_fd, retention_root, desired)
        try:
            os.stat(ADAPTER_NAME, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            live_snapshot = None
            state = AdapterInstallInspection("missing", None, None)
        else:
            try:
                live_snapshot = _snapshot_child(parent_fd, ADAPTER_NAME)
            except _UnknownManagedEntry as exc:
                raise ValueError("owner drift detected; preserving live adapter") from exc
            except (OSError, ValueError) as exc:
                raise ValueError("unsafe live adapter; preserving evidence") from exc
            else:
                state = _classify_snapshot(live_snapshot, desired)
        if state.status == "current":
            return AdapterInstallResult("unchanged", desired.target, destination)
        if state.status in {"unmanaged", "owner-drift", "error"}:
            raise ValueError(f"{state.status} adapter detected; preserving live files")

        retained_parent_fd = retention_stack.enter_context(
            _open_retention_for_source(retention_root, parent_fd)
        )

        _probe_rename_noreplace(parent_fd, retained_parent_fd)
        stage_token = secrets.token_hex(16)
        if (
            type(stage_token) is not str
            or re.fullmatch(r"[0-9a-f]{32}", stage_token) is None
        ):
            raise ValueError("stage token must be 32 lowercase hexadecimal characters")
        stage_name = _RETAINED_ADAPTER_PREFIX + stage_token
        if stage_name in os.listdir(retained_parent_fd):
            raise ValueError("random stage name collision; preserving evidence")
        try:

            def capture_stage(snapshot: ManagedTreeSnapshot) -> None:
                nonlocal stage_snapshot
                stage_snapshot = snapshot

            stage_snapshot = _write_stage(
                retained_parent_fd, stage_name, desired, invoke, capture_stage
            )
            os.fsync(retained_parent_fd)
            if live_snapshot is None:
                try:
                    _rename_noreplace_between(
                        retained_parent_fd, stage_name, parent_fd, ADAPTER_NAME
                    )
                except FileExistsError as exc:
                    stage_snapshot = None
                    raise ValueError(
                        "live adapter appeared during promotion; preserving evidence"
                    ) from exc
                stage_name = None
                os.fsync(retained_parent_fd)
                os.fsync(parent_fd)
                invoke("stage-promoted")
                verified = _snapshot_child(parent_fd, ADAPTER_NAME)
                if _classify_snapshot(verified, desired).status != "current":
                    raise ValueError("installed adapter verification failed")
                return AdapterInstallResult("installed", desired.target, destination)

            backup_name = f".{ADAPTER_NAME}.backup-{secrets.token_hex(16)}"
            if backup_name in os.listdir(parent_fd):
                raise ValueError("random backup name collision; preserving evidence")
            _rename_noreplace(parent_fd, ADAPTER_NAME, backup_name)
            backup_snapshot = ManagedTreeSnapshot(
                backup_name, live_snapshot.identity, live_snapshot.files
            )
            os.fsync(parent_fd)
            invoke("live-moved-to-backup")
            try:
                _rename_noreplace_between(
                    retained_parent_fd, stage_name, parent_fd, ADAPTER_NAME
                )
            except FileExistsError as exc:
                stage_snapshot = None
                raise ValueError(
                    "live adapter appeared during promotion; preserving evidence"
                ) from exc
            stage_name = None
            os.fsync(retained_parent_fd)
            os.fsync(parent_fd)
            invoke("stage-promoted")
            verified = _snapshot_child(parent_fd, ADAPTER_NAME)
            if _classify_snapshot(verified, desired).status != "current":
                raise ValueError("upgraded adapter verification failed")
            _retain_snapshot(parent_fd, retained_parent_fd, backup_snapshot)
            backup_name = None
            # Compatibility checkpoint: the backup left the active recovery
            # namespace; its verified bytes remain in the retention root.
            invoke("backup-removed")
            return AdapterInstallResult("upgraded", desired.target, destination)
        except BaseException:
            # The new stage is already outside the active recovery namespace.
            if backup_name is not None:
                try:
                    os.stat(ADAPTER_NAME, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    try:
                        current_backup = _snapshot_child(parent_fd, backup_name)
                        if (
                            backup_snapshot is not None
                            and current_backup == backup_snapshot
                        ):
                            _rename_noreplace(parent_fd, backup_name, ADAPTER_NAME)
                            os.fsync(parent_fd)
                            backup_name = None
                    except (OSError, ValueError):
                        pass
            raise

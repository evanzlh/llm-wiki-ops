"""Render the packaged external-repository adapter from captured skill metadata."""

from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

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
_MANAGED_ADAPTER_FIELDS = frozenset(
    {"schema_version", "implementation", "cli_version", "target", "files"}
)
_FILE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


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
    if not isinstance(root, Path):
        raise TypeError(f"{label} must be a pathlib.Path")
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

from __future__ import annotations

from dataclasses import dataclass


_PROVENANCE_FIELDS = frozenset({"extracted", "inferred", "ambiguous"})


class FrontmatterError(ValueError):
    pass


@dataclass(frozen=True)
class Provenance:
    extracted: str
    inferred: str
    ambiguous: str


@dataclass(frozen=True)
class Relationship:
    target: str
    type: str


@dataclass(frozen=True)
class Frontmatter:
    scalars: dict[str, str]
    lists: dict[str, tuple[str, ...]]
    provenance: Provenance | None = None
    relationships: tuple[Relationship, ...] | None = None

    @property
    def fields(self) -> frozenset[str]:
        fields = set(self.scalars) | set(self.lists)
        if self.provenance is not None:
            fields.add("provenance")
        if self.relationships is not None:
            fields.add("relationships")
        return frozenset(fields)


def _starts_quote(value: str, index: int, *, inline: bool) -> bool:
    previous = index - 1
    while previous >= 0 and value[previous].isspace():
        previous -= 1
    return previous < 0 or (inline and value[previous] in "[,")


def _strip_comment(value: str) -> str:
    quote: str | None = None
    inline = value.lstrip().startswith("[")
    index = 0
    while index < len(value):
        char = value[index]
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif quote == "'":
            if char == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in "'\"" and _starts_quote(value, index, inline=inline):
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
        index += 1
    if quote is not None:
        raise FrontmatterError("unterminated quote in frontmatter value")
    return value.strip()


def _double_quoted(value: str) -> str:
    decoded: list[str] = []
    index = 1
    while index < len(value):
        char = value[index]
        if char == '"':
            if index != len(value) - 1:
                raise FrontmatterError("malformed quoted frontmatter scalar")
            return "".join(decoded)
        if char == "\\":
            if index + 1 >= len(value):
                raise FrontmatterError("malformed quoted frontmatter scalar")
            escape = value[index + 1]
            if escape not in {'"', "\\"}:
                raise FrontmatterError(f"unsupported double-quoted escape: \\{escape}")
            decoded.append(escape)
            index += 2
            continue
        decoded.append(char)
        index += 1
    raise FrontmatterError("malformed quoted frontmatter scalar")


def _single_quoted(value: str) -> str:
    decoded: list[str] = []
    index = 1
    while index < len(value):
        char = value[index]
        if char == "'":
            if index + 1 < len(value) and value[index + 1] == "'":
                decoded.append("'")
                index += 2
                continue
            if index != len(value) - 1:
                raise FrontmatterError("malformed quoted frontmatter scalar")
            return "".join(decoded)
        decoded.append(char)
        index += 1
    raise FrontmatterError("malformed quoted frontmatter scalar")


def _scalar(value: str) -> str:
    value = _strip_comment(value).strip()
    if not value:
        return value
    if value[0] == '"':
        return _double_quoted(value)
    if value[0] == "'":
        return _single_quoted(value)
    if value[0] in "!&*":
        raise FrontmatterError("unsupported YAML tag, anchor, or alias")
    if value[0] in "[{":
        raise FrontmatterError("nested flow collection is not supported")
    return value


def _inline_list(value: str) -> tuple[str, ...]:
    if not value.startswith("[") or not value.endswith("]"):
        raise FrontmatterError("malformed frontmatter list")
    body = value[1:-1].strip()
    if not body:
        return ()
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(body):
        char = body[index]
        if quote == '"':
            current.append(char)
            if char == "\\" and index + 1 < len(body):
                current.append(body[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
        elif quote == "'":
            current.append(char)
            if char == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    current.append(body[index + 1])
                    index += 2
                    continue
                quote = None
        elif char in "'\"" and not "".join(current).strip():
            quote = char
            current.append(char)
        elif char in "[]{}":
            raise FrontmatterError("nested flow collection is not supported")
        elif char == ",":
            item = _scalar("".join(current))
            if not item:
                raise FrontmatterError("empty item in frontmatter list")
            items.append(item)
            current = []
        else:
            current.append(char)
        index += 1
    if quote is not None:
        raise FrontmatterError("unterminated quote in frontmatter list")
    item = _scalar("".join(current))
    if not item:
        raise FrontmatterError("empty item in frontmatter list")
    items.append(item)
    return tuple(items)


def _document_lines(text: str) -> tuple[list[str], int]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("frontmatter opening delimiter is missing")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise FrontmatterError("frontmatter closing delimiter is missing")
    return lines, closing


def _parse_provenance_field(line: str) -> tuple[str, str]:
    field = line[2:]
    delimiter = field.find(":")
    if delimiter < 0 or (
        delimiter + 1 < len(field) and field[delimiter + 1] != " "
    ):
        raise FrontmatterError("provenance field has malformed mapping delimiter")

    key_region = field[:delimiter]
    if "\t" in key_region:
        raise FrontmatterError("provenance field contains a structural tab")

    raw_region = field[delimiter + 1 :]
    leading_width = len(raw_region) - len(raw_region.lstrip(" \t"))
    if "\t" in raw_region[:leading_width]:
        raise FrontmatterError("provenance field contains a structural tab")

    key = key_region.strip()
    raw = _strip_comment(raw_region).strip()
    quoted = bool(raw) and raw[0] in "'\""
    value = _scalar(raw)
    if not quoted and any(
        char == ":" and index + 1 < len(raw) and raw[index + 1] in " \t"
        for index, char in enumerate(raw)
    ):
        raise FrontmatterError("provenance scalar has an unquoted mapping delimiter")
    return key, value


def _parse_provenance(
    lines: list[str], index: int, closing: int
) -> tuple[Provenance, int]:
    values: dict[str, str] = {}
    while index < closing:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if not line[0].isspace():
            break
        if line.startswith("\t"):
            raise FrontmatterError("provenance indentation must not use tabs")
        if not line.startswith("  ") or len(line) == 2 or line[2].isspace():
            raise FrontmatterError("provenance indentation must use exactly two spaces")

        key, value = _parse_provenance_field(line)
        if not key:
            raise FrontmatterError("malformed provenance field")
        if key not in _PROVENANCE_FIELDS:
            raise FrontmatterError(f"provenance has unknown field: {key!r}")
        if key in values:
            raise FrontmatterError(f"provenance has duplicate field: {key!r}")
        if not value:
            raise FrontmatterError(f"provenance has empty field: {key!r}")
        values[key] = value
        index += 1

    missing = _PROVENANCE_FIELDS - set(values)
    if missing:
        raise FrontmatterError(
            f"provenance is missing required field: {sorted(missing)[0]!r}"
        )
    return (
        Provenance(
            extracted=values["extracted"],
            inferred=values["inferred"],
            ambiguous=values["ambiguous"],
        ),
        index,
    )


def parse_frontmatter(text: str) -> Frontmatter:
    lines, closing = _document_lines(text)

    scalars: dict[str, str] = {}
    lists: dict[str, tuple[str, ...]] = {}
    provenance: Provenance | None = None
    seen: set[str] = set()
    index = 1
    while index < closing:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[0].isspace() or ":" not in line:
            raise FrontmatterError(f"malformed frontmatter line {index + 1}")
        key, raw = line.split(":", 1)
        key = key.strip()
        if not key or key in seen:
            raise FrontmatterError(f"duplicate or empty frontmatter key: {key!r}")
        seen.add(key)
        raw = _strip_comment(raw).strip()
        if key == "provenance":
            if raw:
                raise FrontmatterError("provenance must be a block mapping")
            provenance, index = _parse_provenance(lines, index + 1, closing)
            continue
        if raw == "":
            values: list[str] = []
            index += 1
            while index < closing:
                item_line = lines[index]
                if not item_line.strip() or item_line.lstrip().startswith("#"):
                    index += 1
                    continue
                if not item_line[0].isspace():
                    break
                if not item_line.startswith("  - "):
                    raise FrontmatterError(f"malformed frontmatter list for {key}")
                item = _scalar(item_line[4:])
                if not item:
                    raise FrontmatterError(f"empty item in frontmatter list for {key}")
                values.append(item)
                index += 1
            lists[key] = tuple(values)
            continue
        if raw.startswith("["):
            lists[key] = _inline_list(raw)
        else:
            scalars[key] = _scalar(raw)
        index += 1
    return Frontmatter(
        scalars=scalars,
        lists=lists,
        provenance=provenance,
        relationships=None,
    )

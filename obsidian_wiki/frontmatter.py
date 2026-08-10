from __future__ import annotations

from dataclasses import dataclass


_PROVENANCE_FIELDS = frozenset({"extracted", "inferred", "ambiguous"})
_RELATIONSHIP_FIELDS = frozenset({"target", "type"})
_RELATIONSHIP_FORBIDDEN_PLAIN_STARTS = frozenset(",]}%@`")


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
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("frontmatter opening delimiter is missing")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise FrontmatterError("frontmatter closing delimiter is missing")
    return lines, closing


def _quote_starts_key_scalar(prefix: str) -> bool:
    remaining = prefix.strip()
    if not remaining:
        return True
    while remaining.startswith(("!", "&")):
        if remaining.startswith("!<"):
            closing = remaining.find(">", 2)
            if closing < 0:
                return False
            remaining = remaining[closing + 1 :].lstrip()
            continue
        separator = next(
            (
                index
                for index, char in enumerate(remaining)
                if char.isspace()
            ),
            None,
        )
        if separator is None:
            return True
        remaining = remaining[separator:].lstrip()
    return not remaining


def _top_level_mapping(line: str) -> tuple[str, str] | None:
    quote: str | None = None
    verbatim_tag = False
    index = 0
    while index < len(line):
        char = line[index]
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif quote == "'":
            if char == quote:
                if index + 1 < len(line) and line[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif verbatim_tag:
            if char == ">":
                verbatim_tag = False
        elif char in "'\"" and _quote_starts_key_scalar(line[:index]):
            quote = char
        elif char == "!" and index + 1 < len(line) and line[index + 1] == "<":
            verbatim_tag = True
            index += 1
        elif char == ":":
            return line[:index], line[index + 1 :]
        index += 1
    return None


def _restricted_uncommented_region(value: str) -> str:
    quote: str | None = None
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
        elif char in "'\"" and _starts_quote(value, index, inline=False):
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index]
        index += 1
    return value


def _validate_restricted_margins(value: str, owner: str) -> None:
    uncommented = _restricted_uncommented_region(value)
    leading_width = len(uncommented) - len(uncommented.lstrip())
    trailing_width = len(uncommented) - len(uncommented.rstrip())
    margins = uncommented[:leading_width]
    if trailing_width:
        margins += uncommented[-trailing_width:]
    if any(char != " " for char in margins):
        if "\t" in margins:
            raise FrontmatterError(f"{owner} contains a structural tab")
        raise FrontmatterError(f"{owner} has unsupported structural whitespace")


def _parse_provenance_field(line: str) -> tuple[str, str]:
    field = line[2:]
    delimiter = field.find(":")
    if delimiter < 0 or (
        delimiter + 1 < len(field) and field[delimiter + 1] != " "
    ):
        raise FrontmatterError("provenance field has malformed mapping delimiter")

    key_region = field[:delimiter]
    if any(char.isspace() and char != " " for char in key_region):
        if "\t" in key_region:
            raise FrontmatterError("provenance field contains a structural tab")
        raise FrontmatterError("provenance field has unsupported structural whitespace")

    raw_region = field[delimiter + 1 :]
    _validate_restricted_margins(raw_region, "provenance field")

    key = key_region.strip(" ")
    raw = _strip_comment(raw_region).strip(" ")
    quoted = bool(raw) and raw[0] in "'\""
    value = _scalar(raw)
    if not quoted and any(
        char == ":" and (index + 1 == len(raw) or raw[index + 1].isspace())
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


def _relationship_error(message: str) -> FrontmatterError:
    return FrontmatterError(f"relationships {message}")


def _decode_quoted_key(value: str) -> str | None:
    if len(value) < 2 or value[-1] != value[0] or value[0] not in "'\"":
        return None

    decoded: list[str] = []
    index = 1
    closing = len(value) - 1
    while index < closing:
        char = value[index]
        if value[0] == "'":
            if char == "'":
                if index + 1 >= closing or value[index + 1] != "'":
                    return None
                decoded.append("'")
                index += 2
                continue
            decoded.append(char)
            index += 1
            continue

        if char != "\\":
            decoded.append(char)
            index += 1
            continue
        if index + 1 >= closing:
            return None
        escape = value[index + 1]
        if escape in {'"', "\\"}:
            decoded.append(escape)
            index += 2
            continue
        widths = {"x": 2, "u": 4, "U": 8}
        width = widths.get(escape)
        if width is None or index + 2 + width > closing:
            return None
        digits = value[index + 2 : index + 2 + width]
        try:
            decoded.append(chr(int(digits, 16)))
        except (ValueError, OverflowError):
            return None
        index += 2 + width
    return "".join(decoded)


def _key_scalar_value(value: str) -> str | None:
    if value.startswith(("'", '"')):
        return _decode_quoted_key(value)
    return value


def _strip_key_properties(value: str) -> tuple[str, bool]:
    decorated = value
    has_property = False
    while decorated.startswith(("!", "&")):
        if decorated.startswith("!<"):
            closing = decorated.find(">", 2)
            if closing < 0:
                break
            separator = closing + 1
            if separator == len(decorated) or not decorated[separator].isspace():
                break
        else:
            separator = next(
                (
                    index
                    for index, char in enumerate(decorated)
                    if char.isspace()
                ),
                None,
            )
            if separator is None:
                break
        has_property = True
        decorated = decorated[separator:].strip()
    return decorated, has_property


def _relationship_key(
    key_region: str, relationship_aliases: set[str] | None = None
) -> bool:
    ascii_trimmed = key_region.strip(" ")
    if ascii_trimmed == "relationships":
        return True

    normalized = key_region.strip()
    if normalized == "relationships":
        raise _relationship_error("key has unsupported structural whitespace")

    if _key_scalar_value(normalized) == "relationships":
        raise _relationship_error("reserved key has an unsupported quoted spelling")

    decorated, has_property = _strip_key_properties(normalized)
    if has_property and _key_scalar_value(decorated) == "relationships":
        raise _relationship_error(
            "reserved key has an unsupported tagged or anchored spelling"
        )

    if (
        relationship_aliases is not None
        and normalized.startswith("*")
        and normalized[1:] in relationship_aliases
    ):
        raise _relationship_error("reserved key has an unsupported alias spelling")
    return False


def _check_indented_relationship_key(line: str) -> None:
    mapping = _top_level_mapping(line)
    if mapping is None:
        return
    key_region, _ = mapping
    leading_width = len(key_region) - len(key_region.lstrip())
    leading = key_region[:leading_width]
    if any(char != " " for char in leading):
        _relationship_key(key_region)


def _relationship_anchor(raw_region: str) -> str | None:
    raw = _restricted_uncommented_region(raw_region).strip()
    if not raw.startswith("&"):
        return None
    separator = next(
        (index for index, char in enumerate(raw) if char.isspace()), None
    )
    if separator is None:
        return None
    name = raw[1:separator]
    target, _ = _strip_key_properties(raw[separator:].strip())
    if name and _key_scalar_value(target) == "relationships":
        return name
    return None


def _unrelated_indented_block(raw_region: str) -> bool:
    raw = _restricted_uncommented_region(raw_region).strip()
    return not raw or raw[0] in "|>"


def _has_forbidden_relationship_control(value: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value)


def _validate_relationship_controls(value: str) -> None:
    if _has_forbidden_relationship_control(value):
        raise _relationship_error("contains an unsupported control character")


def _validate_relationship_comment_controls(value: str) -> None:
    uncommented = _restricted_uncommented_region(value)
    if len(uncommented) < len(value):
        _validate_relationship_controls(value[len(uncommented) :])


def _relationship_control_only_line(line: str) -> bool:
    return _has_forbidden_relationship_control(line) and all(
        char == " " or _has_forbidden_relationship_control(char) for char in line
    )


def _relationship_scalar(raw_region: str, key: str) -> str:
    _validate_relationship_comment_controls(raw_region)
    try:
        raw = _strip_comment(raw_region).strip(" ")
    except FrontmatterError as exc:
        raise _relationship_error(f"field {key!r} scalar: {exc}") from exc

    if not raw:
        return raw

    quoted = raw[0] in "'\""
    if not quoted:
        if raw[0] in "|>":
            raise _relationship_error(
                f"field {key!r} scalar uses an unsupported block scalar header"
            )
        if raw[0] in _RELATIONSHIP_FORBIDDEN_PLAIN_STARTS:
            raise _relationship_error(
                f"field {key!r} scalar starts with a forbidden YAML indicator"
            )
        if raw[0] in "-?:" and (
            len(raw) == 1 or raw[1].isspace()
        ):
            raise _relationship_error(
                f"field {key!r} scalar starts with a separated YAML indicator"
            )

    try:
        value = _scalar(raw)
    except FrontmatterError as exc:
        raise _relationship_error(f"field {key!r} scalar: {exc}") from exc

    if not quoted:
        if any(
            char == ":" and (
                index + 1 == len(raw) or raw[index + 1].isspace()
            )
            for index, char in enumerate(raw)
        ):
            raise _relationship_error(
                f"field {key!r} scalar has an unquoted mapping delimiter"
            )

    if _has_forbidden_relationship_control(raw):
        raise _relationship_error(
            f"field {key!r} scalar contains an unsupported control character"
        )
    return value


def _relationship_field(line: str) -> tuple[str, str]:
    delimiter = line.find(":")
    if delimiter < 0 or (
        delimiter + 1 < len(line) and line[delimiter + 1] != " "
    ):
        raise _relationship_error("field has malformed mapping delimiter")

    key = line[:delimiter]
    if not key:
        raise _relationship_error("has a malformed field")
    if any(char.isspace() for char in key):
        if "\t" in key:
            raise _relationship_error("field contains a structural tab")
        raise _relationship_error("field has unsupported structural whitespace")

    raw_region = line[delimiter + 1 :]
    _validate_restricted_margins(raw_region, "relationships field")

    return key, _relationship_scalar(raw_region, key)


def _relationship(values: dict[str, str]) -> Relationship:
    missing = _RELATIONSHIP_FIELDS - set(values)
    if missing:
        raise _relationship_error(
            f"item is missing required field: {sorted(missing)[0]!r}"
        )
    return Relationship(target=values["target"], type=values["type"])


def _add_relationship_field(values: dict[str, str], field: str) -> None:
    key, value = _relationship_field(field)
    if key not in _RELATIONSHIP_FIELDS:
        raise _relationship_error(f"item has unknown field: {key!r}")
    if key in values:
        raise _relationship_error(f"item has duplicate field: {key!r}")
    if not value:
        raise _relationship_error(f"item has empty field: {key!r}")
    values[key] = value


def _relationship_indentation(line: str) -> int:
    indentation = 0
    while indentation < len(line) and line[indentation].isspace():
        char = line[indentation]
        if char == "\t":
            raise _relationship_error("indentation must not use tabs")
        if char != " ":
            raise _relationship_error(
                "indentation has unsupported structural whitespace"
            )
        indentation += 1
    return indentation


def _parse_relationships_block(
    lines: list[str], index: int, closing: int
) -> tuple[tuple[Relationship, ...], int]:
    relationships: list[Relationship] = []
    current: dict[str, str] | None = None

    while index < closing:
        line = lines[index]
        if _relationship_control_only_line(line):
            _validate_relationship_controls(line)
        if not line.strip():
            index += 1
            continue
        if line.lstrip().startswith("#"):
            _validate_relationship_controls(line)
            index += 1
            continue
        if not line[0].isspace():
            if line == "-" or (line.startswith("-") and line[1].isspace()):
                raise _relationship_error(
                    "item indentation must use exactly two spaces"
                )
            break

        _validate_relationship_comment_controls(line)

        indentation = _relationship_indentation(line)
        if indentation == 2:
            item = line[2:]
            if not item.startswith("-") or (
                len(item) > 1 and item[1] != " "
            ):
                raise _relationship_error("has a malformed item boundary")
            if current is not None:
                relationships.append(_relationship(current))
            current = {}

            if item == "-":
                index += 1
                continue
            field = item[2:]
            if not field or field[0].isspace():
                raise _relationship_error("has a malformed item boundary")
            if field.startswith("#"):
                index += 1
                continue
            if field[0] in "[{":
                raise _relationship_error("item uses an unsupported flow mapping")
            _add_relationship_field(current, field)
            index += 1
            continue

        if indentation == 4:
            if current is None:
                raise _relationship_error("has a continuation without an item")
            _add_relationship_field(current, line[4:])
            index += 1
            continue

        raise _relationship_error(
            "item indentation must use exactly two or four spaces"
        )

    if current is not None:
        relationships.append(_relationship(current))
    if not relationships:
        raise _relationship_error("block must contain at least one item")
    return tuple(relationships), index


def _relationships_inline(raw_region: str) -> tuple[Relationship, ...] | None:
    if raw_region == "":
        return None
    if raw_region[0] != " ":
        if raw_region[0] == "\t":
            raise _relationship_error("mapping delimiter must not use a tab")
        raise _relationship_error("has a malformed mapping delimiter")

    _validate_restricted_margins(raw_region, "relationships inline value")
    _validate_relationship_comment_controls(raw_region)

    try:
        raw = _strip_comment(raw_region).strip(" ")
    except FrontmatterError as exc:
        raise _relationship_error(f"has an invalid inline value: {exc}") from exc
    if raw == "":
        return None
    if raw == "[]":
        return ()
    raise _relationship_error("has an unsupported inline value; only [] is allowed")


def parse_frontmatter(text: str) -> Frontmatter:
    lines, closing = _document_lines(text)

    scalars: dict[str, str] = {}
    lists: dict[str, tuple[str, ...]] = {}
    provenance: Provenance | None = None
    relationships: tuple[Relationship, ...] | None = None
    seen: set[str] = set()
    index = 1
    while index < closing:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[0].isspace():
            _check_indented_relationship_key(line)
            raise FrontmatterError(f"malformed frontmatter line {index + 1}")
        mapping = _top_level_mapping(line)
        if mapping is None:
            raise FrontmatterError(f"malformed frontmatter line {index + 1}")
        key_region, raw_region = mapping
        is_relationship = _relationship_key(key_region)
        key = key_region.strip()
        if not key or key in seen:
            raise FrontmatterError(f"duplicate or empty frontmatter key: {key!r}")
        seen.add(key)
        if is_relationship:
            relationships = _relationships_inline(raw_region)
            if relationships is None:
                relationships, index = _parse_relationships_block(
                    lines, index + 1, closing
                )
            else:
                index += 1
            continue

        raw = _strip_comment(raw_region).strip()
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
        relationships=relationships,
    )


def parse_relationships(text: str) -> tuple[Relationship, ...] | None:
    lines, closing = _document_lines(text)
    relationships: tuple[Relationship, ...] | None = None
    relationship_aliases: set[str] = set()
    unrelated_indented_block = False
    seen = False
    index = 1

    while index < closing:
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line[0].isspace():
            if unrelated_indented_block:
                index += 1
                continue
            stripped = line.strip()
            if stripped.startswith("?") and (
                len(stripped) == 1 or stripped[1].isspace()
            ):
                candidate = stripped[1:].strip()
                if candidate and _relationship_key(candidate, relationship_aliases):
                    raise _relationship_error(
                        "key uses an unsupported explicit spelling"
                    )
            mapping = _top_level_mapping(line)
            if mapping is not None and _relationship_key(
                mapping[0], relationship_aliases
            ):
                raise _relationship_error("key must not be indented at the root")
            index += 1
            continue

        unrelated_indented_block = False
        if line.startswith("#"):
            index += 1
            continue

        stripped = line.strip()
        if stripped.startswith("?") and (
            len(stripped) == 1 or stripped[1].isspace()
        ):
            candidate = stripped[1:].strip()
            if candidate and _relationship_key(candidate, relationship_aliases):
                raise _relationship_error("key uses an unsupported explicit spelling")
            index += 1
            continue

        mapping = _top_level_mapping(line)
        if mapping is None:
            index += 1
            continue
        key_region, raw_region = mapping
        if not _relationship_key(key_region, relationship_aliases):
            alias = _relationship_anchor(raw_region)
            if alias is not None:
                relationship_aliases.add(alias)
            unrelated_indented_block = _unrelated_indented_block(raw_region)
            index += 1
            continue
        if seen:
            raise _relationship_error("has a duplicate top-level key")
        seen = True

        relationships = _relationships_inline(raw_region)
        if relationships is None:
            relationships, index = _parse_relationships_block(
                lines, index + 1, closing
            )
        else:
            index += 1
            probe = index
            while probe < closing:
                continuation = lines[probe]
                if not continuation.strip() or continuation.lstrip().startswith("#"):
                    probe += 1
                    continue
                if continuation[0].isspace():
                    raise _relationship_error(
                        "inline value has unexpected indented content"
                    )
                break

    return relationships

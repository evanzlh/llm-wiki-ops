from __future__ import annotations

from dataclasses import dataclass


class FrontmatterError(ValueError):
    pass


@dataclass(frozen=True)
class Frontmatter:
    scalars: dict[str, str]
    lists: dict[str, tuple[str, ...]]


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


def parse_frontmatter(text: str) -> Frontmatter:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("frontmatter opening delimiter is missing")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise FrontmatterError("frontmatter closing delimiter is missing")

    scalars: dict[str, str] = {}
    lists: dict[str, tuple[str, ...]] = {}
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
        if not key or key in scalars or key in lists:
            raise FrontmatterError(f"duplicate or empty frontmatter key: {key!r}")
        raw = _strip_comment(raw).strip()
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
    return Frontmatter(scalars=scalars, lists=lists)

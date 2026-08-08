from __future__ import annotations

from dataclasses import dataclass


class FrontmatterError(ValueError):
    pass


@dataclass(frozen=True)
class Frontmatter:
    scalars: dict[str, str]
    lists: dict[str, tuple[str, ...]]


def _starts_quote(value: str, index: int) -> bool:
    return index == 0 or value[index - 1].isspace() or value[index - 1] in "[,"


def _strip_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"" and _starts_quote(value, index):
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    if quote is not None:
        raise FrontmatterError("unterminated quote in frontmatter value")
    return value.strip()


def _scalar(value: str) -> str:
    value = _strip_comment(value).strip()
    if not value:
        return value
    if value[0] in "'\"":
        quote = value[0]
        escaped = False
        for index, char in enumerate(value[1:], start=1):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                if index != len(value) - 1:
                    raise FrontmatterError("malformed quoted frontmatter scalar")
                return value[1:-1]
        raise FrontmatterError("malformed quoted frontmatter scalar")
    if value[-1] in "'\"":
        raise FrontmatterError("unmatched quote in frontmatter scalar")
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
    escaped = False
    for index, char in enumerate(body):
        if quote is not None:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"" and _starts_quote(body, index):
            quote = char
            current.append(char)
        elif char == ",":
            item = _scalar("".join(current))
            if not item:
                raise FrontmatterError("empty item in frontmatter list")
            items.append(item)
            current = []
        else:
            current.append(char)
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

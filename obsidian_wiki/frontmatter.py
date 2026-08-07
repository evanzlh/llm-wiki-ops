from __future__ import annotations

from dataclasses import dataclass


class FrontmatterError(ValueError):
    pass


@dataclass(frozen=True)
class Frontmatter:
    scalars: dict[str, str]
    lists: dict[str, tuple[str, ...]]


def _strip_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if char in "'\"" and not escaped:
            quote = None if quote == char else (char if quote is None else quote)
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
        escaped = char == "\\" and not escaped
    return value.strip()


def _scalar(value: str) -> str:
    value = _strip_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value.startswith(("'", '"')):
        raise FrontmatterError("malformed quoted frontmatter scalar")
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
    for char in body:
        if char in "'\"":
            quote = None if quote == char else (char if quote is None else quote)
        if char == "," and quote is None:
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
        if not line.strip():
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
            while index < closing and lines[index].strip():
                item_line = lines[index]
                if not item_line.startswith("  - "):
                    if item_line[0].isspace():
                        raise FrontmatterError(f"malformed frontmatter list for {key}")
                    break
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

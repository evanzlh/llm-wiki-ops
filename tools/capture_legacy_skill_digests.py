"""Capture deterministic schema-v1 digests for a legacy skill collection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from obsidian_wiki.skill_trees import discover_skill_collection


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    label = args.label.strip()
    if not label:
        parser.error("--label must not be empty")

    collection = discover_skill_collection(
        args.source, ignore_source_artifacts=True
    )
    payload = {
        "schema_version": 1,
        "collections": [
            {
                "label": label,
                "skills": {skill.name: skill.digest for skill in collection.skills},
            }
        ],
    }
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if args.output.exists():
        if args.output.read_bytes() != encoded:
            parser.error("refusing to overwrite an existing output with different bytes")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

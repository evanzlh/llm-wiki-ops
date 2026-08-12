"""Capture deterministic schema-v1 digests for a legacy skill collection."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Optional

from obsidian_wiki.skill_trees import discover_skill_collection


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _read_existing_output(path: Path) -> Optional[bytes]:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("could not inspect existing output: {}".format(exc)) from exc
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise ValueError("output must be an ordinary single-link regular file")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("could not safely open existing output: {}".format(exc)) from exc

    content: Optional[bytes] = None
    final: Optional[os.stat_result] = None
    failure: Optional[BaseException] = None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not _same_identity(observed, opened)
        ):
            raise ValueError("output must be an ordinary single-link regular file")
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
            raise ValueError("output changed while being read")
        content = b"".join(chunks)
    except BaseException as exc:
        failure = exc
    try:
        os.close(descriptor)
    except OSError as exc:
        if failure is None:
            failure = exc
    if failure is not None:
        if isinstance(failure, (OSError, ValueError)):
            raise ValueError(
                "could not safely read existing output: {}".format(failure)
            ) from failure
        raise failure
    assert content is not None
    assert final is not None
    try:
        final_path = path.lstat()
    except OSError as exc:
        raise ValueError("output path changed while being read") from exc
    if (
        not stat.S_ISREG(final_path.st_mode)
        or final_path.st_nlink != 1
        or not _same_identity(final, final_path)
    ):
        raise ValueError("output path changed while being read")
    return content


def _remove_created_output(path: Path, created: os.stat_result) -> None:
    try:
        current = path.lstat()
    except OSError:
        return
    if _same_identity(created, current) and stat.S_ISREG(current.st_mode):
        try:
            path.unlink()
        except OSError:
            pass


def _write_new_output(path: Path, encoded: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError("could not create output parent: {}".format(exc)) from exc

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o666)
    except OSError as exc:
        raise ValueError("could not exclusively create output: {}".format(exc)) from exc

    opened: Optional[os.stat_result] = None
    failure: Optional[BaseException] = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError(
                "output changed or is not an ordinary single-link regular file"
            )
        observed = path.lstat()
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or not _same_identity(opened, observed)
        ):
            raise ValueError(
                "output changed or is not an ordinary single-link regular file"
            )
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("output write made no progress")
            offset += written
        final = os.fstat(descriptor)
        if (
            not stat.S_ISREG(final.st_mode)
            or final.st_nlink != 1
            or not _same_identity(opened, final)
        ):
            raise ValueError(
                "output changed or is not an ordinary single-link regular file"
            )
    except BaseException as exc:
        failure = exc
    try:
        os.close(descriptor)
    except OSError as exc:
        if failure is None:
            failure = exc
    if failure is not None:
        if opened is not None:
            _remove_created_output(path, opened)
        if isinstance(failure, (OSError, ValueError)):
            raise ValueError(
                "could not completely write output: {}".format(failure)
            ) from failure
        raise failure
    assert opened is not None
    try:
        final_path = path.lstat()
    except OSError as exc:
        _remove_created_output(path, opened)
        raise ValueError("output path changed after writing") from exc
    if (
        not stat.S_ISREG(final_path.st_mode)
        or final_path.st_nlink != 1
        or not _same_identity(opened, final_path)
    ):
        _remove_created_output(path, opened)
        raise ValueError(
            "output changed or is not an ordinary single-link regular file"
        )


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
    try:
        existing = _read_existing_output(args.output)
        if existing is not None:
            if existing != encoded:
                parser.error(
                    "refusing to overwrite an existing output with different bytes"
                )
            return 0
        _write_new_output(args.output, encoded)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

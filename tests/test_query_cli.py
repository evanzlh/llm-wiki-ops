"""Tests for the high-level query CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_wiki import IMPLEMENTATION_ID


NATURAL_TEMPLATES = [
    'find "<term>"',
    'list pages about "<term>"',
    'find path from "<source>" to "<target>"',
]


def _page(
    vault: Path,
    name: str,
    *,
    title: str,
    summary: str,
    links: list[str] | None = None,
    tags: list[str] | None = None,
    lifecycle: str = "reviewed",
) -> None:
    path = vault / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {title}",
        "category: concepts",
        f"tags: [{', '.join(tags or ['test'])}]",
        "sources: [manual]",
        "created: 2026-07-01",
        "updated: 2026-07-01",
        f"lifecycle: {lifecycle}",
        f"summary: {summary}",
        "---",
        f"# {title}",
    ]
    for link in links or []:
        lines.append(f"[[{link}]]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def _find_args(term: str) -> tuple[str, ...]:
    return ("query", "--mode", "find", "--term", term)


def _portable_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "knowledge"
    vault = root / "wiki"
    (root / ".llmwikiops").mkdir(parents=True)
    (root / "sources").mkdir()
    vault.mkdir()
    (root / ".skills").mkdir()
    (root / ".llmwikiops/config.toml").write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".llmwikiops/local"
''',
        encoding="utf-8",
    )
    return root, vault


@pytest.mark.parametrize("options", [(), ("--json",), ("--pretty",)])
def test_query_describe_is_context_free_and_machine_readable(
    tmp_path: Path, options: tuple[str, ...]
) -> None:
    proc = _run(tmp_path / "home", "query", "--describe", *options)

    assert proc.returncode == 0
    assert proc.stderr == ""
    data = json.loads(proc.stdout)
    assert data["grammar_version"] == "query-language/v1"
    assert [item["template"] for item in data["natural_templates"]] == (
        NATURAL_TEMPLATES
    )


def test_query_rejects_legacy_bare_question_before_repository_resolution(
    tmp_path: Path,
) -> None:
    proc = _run(tmp_path / "home", "query", "transformer", "--json")

    assert proc.returncode == 2
    assert proc.stderr == ""
    error = json.loads(proc.stdout)["error"]
    assert error == {
        "code": "unsupported_query_structure",
        "message": "query does not match query-language/v1",
        "grammar_version": "query-language/v1",
        "templates": NATURAL_TEMPLATES,
    }


def test_query_human_error_prints_legal_templates(tmp_path: Path) -> None:
    proc = _run(tmp_path / "home", "query", "transformer")

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.splitlines() == [
        "error: query does not match query-language/v1",
        *[f"  {template}" for template in NATURAL_TEMPLATES],
    ]


def test_query_natural_and_explicit_chinese_forms_return_same_json(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "attention", title="注意力机制", summary="用于序列建模")

    natural = _run(home, "query", 'find "注意力机制"', "--json", cwd=root)
    explicit = _run(home, *_find_args("注意力机制"), "--json", cwd=root)

    assert natural.returncode == explicit.returncode == 0
    assert natural.stderr == explicit.stderr == ""
    assert json.loads(natural.stdout) == json.loads(explicit.stdout)


def test_query_rejects_mixed_natural_and_explicit_forms(tmp_path: Path) -> None:
    proc = _run(
        tmp_path / "home",
        "query",
        'find "topic"',
        "--mode",
        "find",
        "--term",
        "topic",
        "--json",
    )

    assert proc.returncode == 2
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["error"]["code"] == "invalid_query_arguments"


def test_query_requires_natural_form_or_explicit_mode(tmp_path: Path) -> None:
    proc = _run(tmp_path / "home", "query", "--term", "topic", "--json")

    assert proc.returncode == 2
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["error"]["code"] == "invalid_query_arguments"


def test_query_rejects_invalid_mode_with_stable_error(tmp_path: Path) -> None:
    proc = _run(
        tmp_path / "home",
        "query",
        "--mode",
        "search",
        "--term",
        "topic",
        "--json",
    )

    assert proc.returncode == 2
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["error"]["code"] == "unsupported_operation"


@pytest.mark.parametrize(
    "arguments",
    [
        ("--mode", "find"),
        ("--mode", "find", "--term", "topic", "--from", "source"),
        ("--mode", "list", "--term", "topic", "--to", "target"),
        ("--mode", "path", "--from", "source"),
        (
            "--mode",
            "path",
            "--from",
            "source",
            "--to",
            "target",
            "--term",
            "topic",
        ),
    ],
)
def test_query_rejects_missing_or_extra_operands(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    proc = _run(tmp_path / "home", "query", *arguments, "--json")

    assert proc.returncode == 2
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["error"]["code"] == "invalid_query_arguments"


@pytest.mark.parametrize(
    "query_options",
    [
        ('find "topic"',),
        ("--mode", "find"),
        ("--term", "topic"),
        ("--from", "source"),
        ("--to", "target"),
        ("--top", "9"),
        ("--top", "8"),
        ("--top", "9", "--top", "8"),
        ("--max-read", "2"),
        ("--max-read", "3"),
        ("--public-only",),
    ],
)
def test_query_describe_rejects_query_options(
    tmp_path: Path, query_options: tuple[str, ...]
) -> None:
    proc = _run(
        tmp_path / "home",
        "query",
        "--describe",
        *query_options,
        "--json",
    )

    assert proc.returncode == 2
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["error"]["code"] == "invalid_query_arguments"


@pytest.mark.parametrize(
    ("arguments", "message_fragment"),
    [
        (("--top", "nope", "--json"), "invalid int value: 'nope'"),
        (("--json", "--term"), "argument --term: expected one argument"),
        (("--from", "--json"), "argument --from: expected one argument"),
        (("--json", "--to"), "argument --to: expected one argument"),
        (("--unknown", "--json"), "unrecognized arguments: --unknown"),
        (("--json", "--unknown"), "unrecognized arguments: --unknown"),
    ],
)
def test_query_json_parse_errors_use_stable_query_payload(
    tmp_path: Path,
    arguments: tuple[str, ...],
    message_fragment: str,
) -> None:
    proc = _run(tmp_path / "home", "query", *arguments)

    assert proc.returncode == 2
    assert proc.stderr == ""
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "invalid_query_arguments"
    assert payload["error"]["grammar_version"] == "query-language/v1"
    assert message_fragment in payload["error"]["message"]


def test_query_human_parse_errors_keep_argparse_usage(tmp_path: Path) -> None:
    proc = _run(tmp_path / "home", "query", "--top", "nope")

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.startswith("usage: llmwikiops query")
    assert "invalid int value: 'nope'" in proc.stderr


def test_query_help_remains_argparse_help_with_json_flag(tmp_path: Path) -> None:
    proc = _run(tmp_path / "home", "query", "--json", "--help")

    assert proc.returncode == 0
    assert proc.stderr == ""
    assert proc.stdout.startswith("usage: llmwikiops query")


@pytest.mark.parametrize(
    "bound_options",
    [("--top", "0"), ("--top", "-1"), ("--max-read", "-1")],
)
def test_query_rejects_invalid_bounds_before_repository_resolution(
    tmp_path: Path, bound_options: tuple[str, ...]
) -> None:
    proc = _run(
        tmp_path / "home",
        *_find_args("topic"),
        *bound_options,
        "--json",
    )

    assert proc.returncode == 2
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["error"]["code"] == "invalid_query_arguments"


def test_query_accepts_zero_max_read(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "topic", title="Topic", summary="")

    proc = _run(
        home,
        *_find_args("topic"),
        "--max-read",
        "0",
        "--json",
        cwd=root,
    )

    assert proc.returncode == 0
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["should_read"] == []


def test_query_cli_uses_portable_vault_from_nested_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(
        vault,
        "transformer",
        title="Transformer Architecture",
        summary="Self-attention model.",
    )
    _page(
        vault,
        "attention",
        title="Attention Mechanism",
        summary="Weighted lookup.",
        links=["transformer"],
    )
    nested = root / "work/nested"
    nested.mkdir(parents=True)

    proc = _run(home, *_find_args("transformer"), "--json", cwd=nested)

    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert any(item["page"] == "transformer.md" for item in data["candidates"])
    assert "context_warnings" not in data


def test_query_cli_requires_portable_repository(tmp_path: Path) -> None:
    home = tmp_path / "home"

    proc = _run(home, *_find_args("anything"), "--json")

    assert proc.returncode == 1
    assert proc.stderr == ""
    assert "repository not configured" in json.loads(proc.stdout)["error"]["message"]


def test_query_cli_prefers_portable_vault_from_nested_cwd(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, portable_vault = _portable_root(tmp_path)
    global_vault = tmp_path / "global-vault"
    _page(
        portable_vault,
        "portable-result",
        title="Runtime Resolver",
        summary="Portable vault result.",
    )
    _page(
        global_vault,
        "global-result",
        title="Runtime Resolver",
        summary="Global vault result.",
    )
    config = home / ".llmwikiops/config"
    config.parent.mkdir(parents=True)
    config.write_text(f'OBSIDIAN_VAULT_PATH="{global_vault}"\n', encoding="utf-8")
    nested = root / "work/nested"
    nested.mkdir(parents=True)

    proc = _run(home, *_find_args("runtime resolver"), "--json", cwd=nested)

    assert proc.returncode == 0, proc.stderr
    pages = {item["page"] for item in json.loads(proc.stdout)["candidates"]}
    assert "portable-result.md" in pages
    assert "global-result.md" not in pages
    assert "context_warnings" not in json.loads(proc.stdout)


def test_query_cli_invalid_portable_config_never_falls_back_global(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, portable_vault = _portable_root(tmp_path)
    config_path = root / ".llmwikiops/config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            IMPLEMENTATION_ID, "Ar9av/obsidian-wiki"
        ),
        encoding="utf-8",
    )
    global_vault = tmp_path / "global-vault"
    _page(
        global_vault,
        "global-result",
        title="Runtime Resolver",
        summary="Must not be used.",
    )
    global_config = home / ".llmwikiops/config"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(
        f'OBSIDIAN_VAULT_PATH="{global_vault}"\n', encoding="utf-8"
    )
    nested = root / "work/nested"
    nested.mkdir(parents=True)

    proc = _run(home, *_find_args("runtime resolver"), "--json", cwd=nested)

    assert proc.returncode == 1
    assert proc.stderr == ""
    assert "implementation" in json.loads(proc.stdout)["error"]["message"]
    assert str(global_vault) not in proc.stdout
    assert not any(portable_vault.iterdir())


def test_query_reports_no_matches_as_success(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "known", title="Known", summary="Known summary")

    proc = _run(home, *_find_args("不存在"), "--json", cwd=root)

    assert proc.returncode == 0
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["status"] == "no_matches"


def test_query_path_ambiguity_returns_candidate_paths(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "concepts/agent", title="Concept Agent", summary="Concept")
    _page(vault, "projects/agent", title="Project Agent", summary="Project")
    _page(vault, "target", title="Target", summary="Target")

    proc = _run(
        home,
        "query",
        "--mode",
        "path",
        "--from",
        "agent",
        "--to",
        "Target",
        "--json",
        cwd=root,
    )

    assert proc.returncode == 2
    assert proc.stderr == ""
    error = json.loads(proc.stdout)["error"]
    assert error["code"] == "ambiguous_operand"
    assert error["details"] == {
        "operand": "source",
        "candidates": ["concepts/agent.md", "projects/agent.md"],
    }


def test_query_no_path_is_success(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "left", title="Left", summary="Left")
    _page(vault, "right", title="Right", summary="Right")

    proc = _run(
        home,
        "query",
        "--mode",
        "path",
        "--from",
        "Left",
        "--to",
        "Right",
        "--json",
        cwd=root,
    )

    assert proc.returncode == 0
    assert proc.stderr == ""
    assert json.loads(proc.stdout)["status"] == "no_path"


def test_query_human_output_starts_with_mode_and_status(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "topic", title="Topic", summary="Topic summary")

    proc = _run(home, *_find_args("Topic"), cwd=root)

    assert proc.returncode == 0
    assert proc.stderr == ""
    assert proc.stdout.splitlines()[:2] == ["mode: find", "status: ok"]
    assert "answer_type" not in proc.stdout


def test_query_json_success_respects_pretty(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "topic", title="Topic", summary="Topic summary")

    proc = _run(home, *_find_args("Topic"), "--json", "--pretty", cwd=root)

    assert proc.returncode == 0
    assert proc.stderr == ""
    assert proc.stdout.startswith("{\n  \"grammar_version\"")


def test_query_cli_public_only_excludes_private_metadata_and_body(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(
        vault,
        "public",
        title="Launch",
        summary="Public launch summary.",
        tags=["visibility/public"],
        lifecycle="verified",
    )
    _page(
        vault,
        "private-sentinel",
        title="Private sentinel",
        summary="PRIVATE-METADATA-SENTINEL",
        tags=["visibility/internal"],
        links=["public"],
    )
    (vault / "private-sentinel.md").write_text(
        "---\r\ntitle: Private sentinel\r\nsummary: PRIVATE-METADATA-SENTINEL\r\n"
        'tags:\r\n  - "visibility/internal" # restricted\r\n'
        "updated: 2026-07-01\r\nlifecycle: reviewed\r\n---\r\n"
        "PRIVATE-BODY-SENTINEL\r\n",
        encoding="utf-8",
    )

    proc = _run(
        home,
        *_find_args("launch"),
        "--public-only",
        "--json",
        cwd=root,
    )

    assert proc.returncode == 0, proc.stderr
    assert "PRIVATE-METADATA-SENTINEL" not in proc.stdout
    assert "private-sentinel" not in proc.stdout
    candidate = json.loads(proc.stdout)["candidates"][0]
    assert candidate["visibility"] == ["visibility/public"]
    assert candidate["lifecycle"] == "verified"
    assert candidate["updated"] == "2026-07-01"

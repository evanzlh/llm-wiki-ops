"""Tests for the high-level query CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from obsidian_wiki import IMPLEMENTATION_ID


def _page(vault: Path, name: str, *, title: str, summary: str, links: list[str] | None = None) -> None:
    path = vault / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {title}",
        "category: concepts",
        "tags: [test]",
        "sources: [manual]",
        "created: 2026-07-01",
        "updated: 2026-07-01",
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


def _portable_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "knowledge"
    vault = root / "wiki"
    (root / ".obsidian-wiki").mkdir(parents=True)
    (root / "sources").mkdir()
    vault.mkdir()
    (root / ".skills").mkdir()
    (root / ".obsidian-wiki/config.toml").write_text(
        f'''schema_version = 1
implementation = "{IMPLEMENTATION_ID}"
requires_cli = ">=0"
[paths]
vault = "wiki"
sources = ["sources"]
skills = ".skills"
local_state = ".obsidian-wiki/local"
''',
        encoding="utf-8",
    )
    return root, vault


def test_query_cli_uses_portable_vault_from_nested_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root, vault = _portable_root(tmp_path)
    _page(vault, "transformer", title="Transformer Architecture", summary="Self-attention model.")
    _page(vault, "attention", title="Attention Mechanism", summary="Weighted lookup.", links=["transformer"])
    nested = root / "work/nested"
    nested.mkdir(parents=True)

    proc = _run(home, "query", "transformer", "--json", cwd=nested)

    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert any(item["page"] == "transformer.md" for item in data["candidates"])
    assert "context_warnings" not in data


def test_query_cli_requires_portable_repository(tmp_path: Path) -> None:
    home = tmp_path / "home"

    proc = _run(home, "query", "anything", "--json")

    assert proc.returncode == 1
    assert "repository not configured" in proc.stderr


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
    config = home / ".obsidian-wiki/config"
    config.parent.mkdir(parents=True)
    config.write_text(f'OBSIDIAN_VAULT_PATH="{global_vault}"\n', encoding="utf-8")
    nested = root / "work/nested"
    nested.mkdir(parents=True)

    proc = _run(home, "query", "runtime resolver", "--json", cwd=nested)

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
    config_path = root / ".obsidian-wiki/config.toml"
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
    global_config = home / ".obsidian-wiki/config"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(
        f'OBSIDIAN_VAULT_PATH="{global_vault}"\n', encoding="utf-8"
    )
    nested = root / "work/nested"
    nested.mkdir(parents=True)

    proc = _run(home, "query", "runtime resolver", "--json", cwd=nested)

    assert proc.returncode == 1
    assert "implementation" in proc.stderr
    assert str(global_vault) not in proc.stdout
    assert not any(portable_vault.iterdir())

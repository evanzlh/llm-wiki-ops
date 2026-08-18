from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from obsidian_wiki import agent_adapter, cli
from obsidian_wiki.agent_adapter import (
    ADAPTER_DESCRIPTION,
    ADAPTER_NAME,
    BUILTIN_CATALOG_END,
    BUILTIN_CATALOG_START,
    render_adapter_skill,
)
from obsidian_wiki.frontmatter import parse_frontmatter
from obsidian_wiki.skill_trees import (
    SkillCollection,
    SkillEntry,
    discover_skill_collection,
)

EXPECTED_BUNDLED_CATALOG = (
    (
        "claude-history-ingest",
        "Use when mining selected Claude Code or Claude Desktop session history for durable repository knowledge.",
    ),
    (
        "codex-history-ingest",
        "Use when mining selected Codex rollout sessions for durable repository knowledge.",
    ),
    (
        "copilot-history-ingest",
        "Use when mining selected GitHub Copilot CLI or VS Code chat sessions for durable repository knowledge.",
    ),
    (
        "cross-linker",
        "Use when wiki pages need missing cross-references, orphan repair, or stronger graph connectivity.",
    ),
    (
        "daily-update",
        "Use when manually checking repository freshness, retained transactions, hot state, or selecting a routine page repair.",
    ),
    (
        "graph-colorize",
        "Use when the user wants Obsidian graph nodes colored by tag, category, visibility, or an explicit custom mapping.\n",
    ),
    (
        "hermes-history-ingest",
        "Use when mining selected Hermes memory or session history for durable repository knowledge.",
    ),
    (
        "impl-validator",
        'Validate whether an implementation matches its stated goal. Use this skill when a skill or agent wants a second opinion on its own output, when the user says "check this implementation", "validate what you did", "is this correct?", "review the output", or "did you do this right?". Also spawned automatically as a subagent by other skills to self-check their outputs before presenting to the user. Returns a structured pass/warn/fail verdict with specific actionable issues.\n',
    ),
    (
        "llm-wiki",
        "Canonical repository runtime protocol for resolving configuration, preserving source authority, and compiling reviewed knowledge through transactions.\n",
    ),
    (
        "obsidian-layout-adjustment",
        "Use when changing or debugging Obsidian appearance, layout, active CSS snippets, panes, tabs, sidebars, note surfaces, properties, backlinks, graph, or icons.\n",
    ),
    (
        "openclaw-history-ingest",
        "Use when mining selected OpenClaw memory or session history for durable repository knowledge.",
    ),
    (
        "pi-history-ingest",
        "Use when mining selected Pi agent session history for durable repository knowledge.",
    ),
    (
        "session-brain",
        'Build and maintain a topic graph over your agent session history. Reads every Claude session transcript plus the pruned sessions that survive only in history.jsonl, clusters them by topic using local TF-IDF (no API calls, no embeddings), and writes an interactive graph you can open in a browser. Use when the user says "/session-brain", "build my session map", "cluster my claude sessions", "map my session history", "rebuild the session graph", "show me my session graph", "what have I been working on lately", "what topics have gone stale". Different from wiki-history-ingest, which distils sessions into vault pages: this builds a retrieval index over the raw sessions and never writes to the vault.\n',
    ),
    (
        "session-search",
        'Find a past agent session by topic and load its context into the current conversation. Searches the session-brain topic graph, ranking by relevance, topic membership, and time decay, then loads the winning transcript. Use when the user says "/wiki-sessions <topic>", "which session did I do X in", "find the session where I fixed X", "when did I last work on Y", "what was that session about Z", "load the session where I set up X", "have I done this before". Read-only — never writes to the vault. Requires a graph built by the session-brain skill.\n',
    ),
    (
        "skill-creator",
        "Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.",
    ),
    (
        "tag-taxonomy",
        "Use when auditing wiki tags, normalizing tag vocabulary, or proposing tags for knowledge pages.",
    ),
    (
        "vault-skill-factory",
        "Use when mature, curated pages in the configured portable wiki should be distilled into a reviewable Agent Skill without installing it.\n",
    ),
    (
        "wiki-agent",
        "Use when answering a focused question from selected sessions of a named supported coding agent.",
    ),
    (
        "wiki-capture",
        "Use when the user asks to preserve the current conversation, save a finding, record a correction, or quickly place reviewed text into the source inbox.\n",
    ),
    (
        "wiki-context-pack",
        "Use when a downstream task needs a token-bounded, citation-ready context slice from the configured portable LLMWikiOps.\n",
    ),
    (
        "wiki-dedup",
        "Use when detecting duplicate knowledge pages, resolving identity collisions, or merging an owner-approved duplicate pair.",
    ),
    (
        "wiki-digest",
        "Use when the user wants a daily, weekly, monthly, or date-bounded digest of recent knowledge in the configured portable wiki.\n",
    ),
    (
        "wiki-export",
        "Use when the configured portable wiki needs a local review export as JSON, GraphML, Cypher, interactive HTML, or an optional OKF Markdown bundle.\n",
    ),
    (
        "wiki-history-ingest",
        "Use when selecting the supported tool-specific skill for coding-agent history input.",
    ),
    (
        "wiki-import",
        "Use when importing a graph.json export or OKF Markdown bundle into the configured wiki repository.\n",
    ),
    (
        "wiki-ingest",
        "Use when converting one or more reviewed source documents into structured wiki pages, including incremental, full, append, URL, and PageIndex inputs.\n",
    ),
    (
        "wiki-lint",
        "Use when auditing wiki health, validating page schema, or applying an owner-selected set of lint repairs.",
    ),
    (
        "wiki-narrate",
        "Use when turning a topic in the configured portable wiki into a cited briefing, plain-language explanation, or progressive lecture.\n",
    ),
    (
        "wiki-query",
        "Use when retrieving evidence from the compiled LLMWikiOps with one exact query-language/v1 operation.\n",
    ),
    (
        "wiki-rebuild",
        "Use when rebuilding an explicit set of knowledge pages from declared repository sources or replacing drifted derived pages.",
    ),
    (
        "wiki-research",
        "Use when researching a topic from external sources and compiling reviewed, cited findings into the configured wiki repository.\n",
    ),
    (
        "wiki-setup",
        "Initialize, clone, inspect, or upgrade an LLMWikiOps repository.",
    ),
    (
        "wiki-status",
        "Use when reporting repository knowledge health, source freshness, transaction state, graph structure, or durable wiki insights.",
    ),
    (
        "wiki-synthesize",
        "Use when discovering cross-page synthesis opportunities or drafting an owner-selected synthesis page from repository knowledge.",
    ),
    (
        "wiki-transaction-review",
        "Use when users ask to inspect, approve, reject, or recover a repository-local wiki transaction.",
    ),
    (
        "wiki-update",
        "Use when syncing reviewed project evidence into repository knowledge pages or refreshing project-derived knowledge.",
    ),
)

SAFE_READER_START = "<!-- LLMWIKIOPS_SAFE_READER_START -->"
SAFE_READER_END = "<!-- LLMWIKIOPS_SAFE_READER_END -->"
SAFE_READER_HEREDOC = (
    "LLMWIKIOPS_SAFE_ROOT_B64='BASE64URL_UTF8_EXACT_ROOT' "
    "LLMWIKIOPS_SAFE_REL_B64='BASE64URL_UTF8_RELATIVE_PATH_OR_EMPTY' "
    "LLMWIKIOPS_SAFE_MODE=root-bind LLMWIKIOPS_SAFE_EXPECT_ROOT='' "
    "LLMWIKIOPS_SAFE_EXPECT_REL_B64='' LLMWIKIOPS_SAFE_EXPECT_SIZE='' "
    "LLMWIKIOPS_SAFE_EXPECT_SHA256='' "
    "python - <<'PY'\n"
)
EXPECTED_ADAPTER_DESCRIPTION = (
    "Use when any request asks to access or operate on an external LLMWikiOps wiki, "
    "including querying, ingesting, maintaining, or recovering it, whether or not "
    "the user has supplied its repository root."
)


def make_skill_collection(
    root: Path, descriptions: dict[str, str], *, body: str = "# Task body\n"
) -> Path:
    for name, description in descriptions.items():
        skill = root / name
        skill.mkdir(parents=True)
        if description.endswith("\n"):
            encoded_description = "description: >\n" + "".join(
                f"  {line}\n" for line in description.splitlines()
            )
        else:
            encoded_description = f"description: {description}\n"
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\n"
            + encoded_description
            + "---\n\n"
            + body,
            encoding="utf-8",
        )
    return root


def encoded_catalog(rendered: str) -> list[dict[str, str]]:
    encoded = rendered.split(BUILTIN_CATALOG_START, 1)[1].split(
        BUILTIN_CATALOG_END, 1
    )[0]
    return json.loads(encoded)


def embedded_safe_reader_script(rendered: str) -> str:
    region = rendered.split(SAFE_READER_START, 1)[1].split(SAFE_READER_END, 1)[0]
    return region.split("python - <<'PY'\n", 1)[1].split("\nPY", 1)[0] + "\n"


def render_demo_adapter(tmp_path: Path) -> str:
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path / "catalog", {"demo": "Use when demo is requested."}
        )
    )
    return render_adapter_skill(collection)


def execute_safe_reader(
    rendered: str,
    root: Path,
    relative_path: str,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_relative_path: str | None = None,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> None:
    encoded_root = base64.urlsafe_b64encode(str(root).encode("utf-8")).decode("ascii")
    encoded_relative = base64.urlsafe_b64encode(
        relative_path.encode("utf-8")
    ).decode("ascii")
    root_metadata = root.stat()
    monkeypatch.setenv("LLMWIKIOPS_SAFE_ROOT_B64", encoded_root)
    monkeypatch.setenv("LLMWIKIOPS_SAFE_REL_B64", encoded_relative)
    legacy_path = root if not relative_path else root.joinpath(*relative_path.split("/"))
    monkeypatch.setenv(
        "LLMWIKIOPS_SAFE_PATH_B64",
        base64.urlsafe_b64encode(str(legacy_path).encode("utf-8")).decode("ascii"),
    )
    monkeypatch.setenv("LLMWIKIOPS_SAFE_MODE", mode)
    monkeypatch.setenv(
        "LLMWIKIOPS_SAFE_EXPECT_ROOT",
        "" if mode == "root-bind" else f"{root_metadata.st_dev}:{root_metadata.st_ino}",
    )
    monkeypatch.setenv(
        "LLMWIKIOPS_SAFE_EXPECT_REL_B64",
        ""
        if expected_relative_path is None
        else base64.urlsafe_b64encode(
            expected_relative_path.encode("utf-8")
        ).decode("ascii"),
    )
    monkeypatch.setenv(
        "LLMWIKIOPS_SAFE_EXPECT_SIZE",
        "" if expected_size is None else str(expected_size),
    )
    monkeypatch.setenv(
        "LLMWIKIOPS_SAFE_EXPECT_SHA256",
        "" if expected_sha256 is None else expected_sha256,
    )
    exec(  # noqa: S102 - executes the exact embedded protocol under test
        compile(embedded_safe_reader_script(rendered), "<safe-reader>", "exec"),
        {"__name__": "__main__"},
    )


def forged_collection_with_entries(
    collection: SkillCollection, entries: tuple[SkillEntry, ...]
) -> SkillCollection:
    skill = collection.skills[0]
    digest = sha256()

    def add(value: bytes) -> None:
        digest.update(str(len(value)).encode("ascii"))
        digest.update(b":")
        digest.update(value)

    add(skill.name.encode("utf-8"))
    for entry in entries:
        add(entry.path.encode("utf-8"))
        add(entry.kind.encode("ascii"))
        add(b"1" if entry.executable else b"0")
        add(str(len(entry.content)).encode("ascii"))
        add(entry.content)
    forged = replace(
        skill,
        entries=entries,
        digest="sha256:" + digest.hexdigest(),
    )
    return SkillCollection((forged,))


def test_renderer_embeds_exact_sorted_name_description_catalog(tmp_path: Path) -> None:
    source = make_skill_collection(
        tmp_path,
        {
            "zeta": "Use when a zeta task is requested.\n",
            "alpha": "Use when an alpha task is requested.",
        },
    )
    collection = discover_skill_collection(source)
    rendered = render_adapter_skill(collection)
    encoded = rendered.split(BUILTIN_CATALOG_START, 1)[1].split(
        BUILTIN_CATALOG_END, 1
    )[0]
    assert json.loads(encoded) == [
        {"name": "alpha", "description": "Use when an alpha task is requested."},
        {"name": "zeta", "description": "Use when a zeta task is requested.\n"},
    ]
    assert ADAPTER_NAME == "llm-wiki-ops"


def test_renderer_is_byte_stable_and_contains_no_task_bodies(tmp_path: Path) -> None:
    source = make_skill_collection(
        tmp_path,
        {"demo": "Use when demo routing is needed."},
        body="# SECRET TASK BODY SENTINEL\n",
    )
    collection = discover_skill_collection(source)
    first = render_adapter_skill(collection)
    second = render_adapter_skill(collection)
    assert first == second
    assert "SECRET TASK BODY SENTINEL" not in first


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("---\nname: other\ndescription: Use when demo runs.\n---\n", "equal"),
        ("---\nname: demo\n---\n", "required"),
    ],
)
def test_renderer_source_uses_strict_collection_metadata_boundaries(
    tmp_path: Path, contents: str, message: str
) -> None:
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        discover_skill_collection(tmp_path)


def test_renderer_rejects_duplicate_unsorted_or_changed_collection_metadata(
    tmp_path: Path,
) -> None:
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path,
            {
                "alpha": "Use when alpha is requested.",
                "zeta": "Use when zeta is requested.",
            },
        )
    )
    alpha, zeta = collection.skills

    with pytest.raises(ValueError, match="duplicate"):
        render_adapter_skill(SkillCollection((alpha, alpha)))
    with pytest.raises(ValueError, match="sorted"):
        render_adapter_skill(SkillCollection((zeta, alpha)))
    with pytest.raises(ValueError, match="description"):
        render_adapter_skill(SkillCollection((replace(alpha, description=""),)))
    with pytest.raises(ValueError, match="metadata|description"):
        render_adapter_skill(
            SkillCollection((replace(alpha, description="Use when changed."),))
        )


def test_renderer_rejects_forged_orphan_entry_before_reading_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path / "source", {"demo": "Use when demo is requested."}
        )
    )
    entries = tuple(
        sorted(
            collection.skills[0].entries
            + (SkillEntry("references/orphan.md", "file", False, b"orphan\n"),),
            key=lambda entry: entry.path,
        )
    )
    forged = forged_collection_with_entries(collection, entries)
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", tmp_path / "missing.in")

    with pytest.raises(ValueError, match="orphan|parent directory|topology"):
        render_adapter_skill(forged)


def test_renderer_rejects_forged_nul_entry_before_reading_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path / "source", {"demo": "Use when demo is requested."}
        )
    )
    entries = tuple(
        sorted(
            collection.skills[0].entries
            + (SkillEntry("references/unsafe\x00.md", "file", False, b"unsafe\n"),),
            key=lambda entry: entry.path,
        )
    )
    forged = forged_collection_with_entries(collection, entries)
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", tmp_path / "missing.in")

    with pytest.raises(ValueError, match="unsafe skill entry path"):
        render_adapter_skill(forged)


@pytest.mark.parametrize(
    "path",
    (
        "",
        "/absolute.md",
        "./relative.md",
        "references//note.md",
        "references/../note.md",
        "references\\note.md",
        "references/note.md/",
    ),
)
def test_renderer_rejects_forged_non_posix_or_noncanonical_entry_paths(
    tmp_path: Path, path: str
) -> None:
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path / "source", {"demo": "Use when demo is requested."}
        )
    )
    entries = tuple(
        sorted(
            collection.skills[0].entries
            + (SkillEntry(path, "file", False, b"unsafe\n"),),
            key=lambda entry: entry.path,
        )
    )
    forged = forged_collection_with_entries(collection, entries)

    with pytest.raises(ValueError, match="unsafe skill entry path"):
        render_adapter_skill(forged)


def test_renderer_catalog_escapes_literal_marker_text_in_descriptions(
    tmp_path: Path,
) -> None:
    description = (
        "Use when literal marker examples "
        f"{BUILTIN_CATALOG_START} and {BUILTIN_CATALOG_END} are requested."
    )
    collection = discover_skill_collection(
        make_skill_collection(tmp_path, {"demo": description})
    )

    rendered = render_adapter_skill(collection)

    assert encoded_catalog(rendered) == [
        {"name": "demo", "description": description}
    ]
    assert rendered.count(BUILTIN_CATALOG_START) == 1
    assert rendered.count(BUILTIN_CATALOG_END) == 1
    assert rendered.index(BUILTIN_CATALOG_START) < rendered.index(BUILTIN_CATALOG_END)


def test_template_contains_one_low_freedom_executable_safe_reader(
    tmp_path: Path,
) -> None:
    rendered = render_demo_adapter(tmp_path)

    assert rendered.count(SAFE_READER_START) == 1
    assert rendered.count(SAFE_READER_END) == 1
    assert rendered.count(SAFE_READER_HEREDOC) == 1
    script = embedded_safe_reader_script(rendered)
    for required in (
        "os.O_NOFOLLOW",
        "os.O_DIRECTORY",
        "os.lstat",
        "os.fstat",
        "os.read",
        "os.lseek",
        "os.listdir",
        "dir_fd=",
        "hashlib.sha256",
        "base64.b64decode",
        "root_identity",
        "skill-catalog",
        "LLMWIKIOPS_SAFE_EXPECT_REL_B64",
        "LLMWIKIOPS_SAFE_EXPECT_SIZE",
        "LLMWIKIOPS_SAFE_EXPECT_SHA256",
        "1048576",
        "256",
        "65536",
        'decode("utf-8")',
    ):
        assert required in script
    assert "MUST use only the deterministic safe reader" in rendered
    assert "LLMWIKIOPS_SAFE_PATH_B64" not in rendered
    assert "raw path in the shell command" in rendered
    assert "Never use `sed`, `cat`, `head`, `tail`, `awk`, separate `stat`" in rendered
    assert "or `sha256sum`" in rendered
    assert "Never use `find` anywhere" in rendered
    assert "full` mode requires the exact catalog-returned" in rendered


def test_embedded_safe_reader_binds_an_ordinary_exact_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "exact root's directory"
    root.mkdir()

    execute_safe_reader(rendered, root, "", "root-bind", monkeypatch)

    metadata = root.stat()
    assert json.loads(capsys.readouterr().out) == {
        "mode": "root-bind",
        "root": str(root),
        "root_identity": f"{metadata.st_dev}:{metadata.st_ino}",
    }


def test_embedded_safe_reader_reads_complete_ordinary_utf8_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root with a quote's"
    target = root / "nested directory" / "authority's complete body.md"
    target.parent.mkdir(parents=True)
    contents = "---\r\nname: demo\r\n---\r\n\r\n# Complete body\r\n"
    target.write_bytes(contents.encode("utf-8"))

    execute_safe_reader(
        rendered,
        root,
        "nested directory/authority's complete body.md",
        "unbound",
        monkeypatch,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "unbound"
    assert payload["root"] == str(root)
    assert payload["relative_path"] == (
        "nested directory/authority's complete body.md"
    )
    assert payload["root_identity"] == (
        f"{root.stat().st_dev}:{root.stat().st_ino}"
    )
    assert payload["sha256"] == "sha256:" + sha256(target.read_bytes()).hexdigest()
    assert payload["size"] == len(target.read_bytes())
    assert payload["text"] == contents


def test_embedded_safe_reader_returns_only_bounded_frontmatter_after_full_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    target = root / "route.md"
    target.write_bytes(
        b"---\r\nname: demo\r\ndescription: Route demo.\r\n---\r\nSECRET BODY\r\n"
    )

    execute_safe_reader(rendered, root, "route.md", "frontmatter", monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "frontmatter"
    assert payload["text"] == (
        "---\r\nname: demo\r\ndescription: Route demo.\r\n---\r\n"
    )
    assert "SECRET BODY" not in payload["text"]


def test_embedded_safe_reader_rejects_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target.md"
    target.write_text("ordinary\n", encoding="utf-8")
    linked = root / "linked.md"
    try:
        linked.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    with pytest.raises(SystemExit, match="safe-read-error"):
        execute_safe_reader(rendered, root, "linked.md", "unbound", monkeypatch)


def test_embedded_safe_reader_rejects_root_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    with pytest.raises(SystemExit, match="safe-read-error"):
        execute_safe_reader(rendered, linked_root, "", "root-bind", monkeypatch)


def test_embedded_safe_reader_rejects_symlinked_ancestor_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    make_skill_collection(outside, {"demo": "Use when demo is requested."})
    try:
        (root / ".skills").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    with pytest.raises(SystemExit, match="safe-read-error"):
        execute_safe_reader(
            rendered, root, ".skills/demo/SKILL.md", "unbound", monkeypatch
        )


def test_embedded_safe_reader_rejects_oversize_and_invalid_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    oversize = root / "oversize.md"
    oversize.write_bytes(b"x" * (1024 * 1024 + 1))
    invalid = root / "invalid.md"
    invalid.write_bytes(b"\xff\xfe")

    with pytest.raises(SystemExit, match="safe-read-error.*1 MiB"):
        execute_safe_reader(rendered, root, "oversize.md", "unbound", monkeypatch)
    with pytest.raises(SystemExit, match="safe-read-error.*UTF-8"):
        execute_safe_reader(rendered, root, "invalid.md", "unbound", monkeypatch)


@pytest.mark.parametrize(
    "relative_path",
    (
        "",
        "/absolute.md",
        "./file.md",
        "nested//file.md",
        "nested/../file.md",
        "nested\\file.md",
        "nested/file.md/",
        "control\x00file.md",
    ),
)
def test_embedded_safe_reader_rejects_unsafe_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(SystemExit, match="safe-read-error"):
        execute_safe_reader(rendered, root, relative_path, "unbound", monkeypatch)


def test_embedded_safe_reader_rejects_path_swap_during_same_fd_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    target = root / "authority.md"
    replacement = root / "replacement.md"
    target.write_bytes(b"trusted\n" * 16384)
    replacement.write_bytes(b"attacker\n")
    real_read = os.read
    swapped = False

    def swapping_read(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        data = real_read(descriptor, count)
        if data and not swapped:
            replacement.replace(target)
            swapped = True
        return data

    monkeypatch.setattr(os, "read", swapping_read)

    with pytest.raises(SystemExit, match="safe-read-error.*changed"):
        execute_safe_reader(rendered, root, "authority.md", "unbound", monkeypatch)


def test_embedded_safe_reader_rejects_ancestor_swap_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    ancestor = root / "authority"
    ancestor.mkdir(parents=True)
    target = ancestor / "SKILL.md"
    target.write_bytes(b"trusted\n" * 16384)
    moved = root / "moved-authority"
    real_read = os.read
    swapped = False

    def swapping_read(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        data = real_read(descriptor, count)
        if data and not swapped:
            ancestor.rename(moved)
            ancestor.mkdir()
            (ancestor / "SKILL.md").write_text("attacker\n", encoding="utf-8")
            swapped = True
        return data

    monkeypatch.setattr(os, "read", swapping_read)

    with pytest.raises(SystemExit, match="safe-read-error.*changed"):
        execute_safe_reader(
            rendered, root, "authority/SKILL.md", "unbound", monkeypatch
        )


def test_embedded_safe_reader_returns_sorted_fd_anchored_skill_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    skills = make_skill_collection(
        root / ".skills",
        {
            "zeta": "Use when zeta is requested.",
            "alpha": "Use when alpha is requested.",
        },
    )

    execute_safe_reader(rendered, root, ".skills", "skill-catalog", monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skill-catalog"
    assert payload["root_identity"] == f"{root.stat().st_dev}:{root.stat().st_ino}"
    assert [item["name"] for item in payload["skills"]] == ["alpha", "zeta"]
    for item in payload["skills"]:
        target = skills / item["name"] / "SKILL.md"
        assert item["relative_path"] == f".skills/{item['name']}/SKILL.md"
        assert item["size"] == len(target.read_bytes())
        assert item["sha256"] == "sha256:" + sha256(target.read_bytes()).hexdigest()
        assert item["frontmatter"].startswith(f"---\nname: {item['name']}\n")
        assert "# Task body" not in item["frontmatter"]


def test_embedded_safe_reader_full_accepts_exact_catalog_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    make_skill_collection(
        root / ".skills",
        {"demo": "Use when demo is requested."},
        body="# SAFE BODY\n",
    )
    execute_safe_reader(rendered, root, ".skills", "skill-catalog", monkeypatch)
    record = json.loads(capsys.readouterr().out)["skills"][0]

    execute_safe_reader(
        rendered,
        root,
        record["relative_path"],
        "full",
        monkeypatch,
        expected_relative_path=record["relative_path"],
        expected_size=record["size"],
        expected_sha256=record["sha256"],
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["relative_path"] == record["relative_path"]
    assert payload["size"] == record["size"]
    assert payload["sha256"] == record["sha256"]
    assert payload["text"].endswith("# SAFE BODY\n")


@pytest.mark.parametrize(
    "invalid_binding",
    (
        "missing-relative",
        "missing-size",
        "missing-sha256",
        "relative-mismatch",
        "size-mismatch",
        "sha256-mismatch",
    ),
)
def test_embedded_safe_reader_full_rejects_missing_or_mismatched_catalog_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid_binding: str,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    make_skill_collection(
        root / ".skills", {"demo": "Use when demo is requested."}
    )
    execute_safe_reader(rendered, root, ".skills", "skill-catalog", monkeypatch)
    record = json.loads(capsys.readouterr().out)["skills"][0]
    expected_relative_path: str | None = record["relative_path"]
    expected_size: int | None = record["size"]
    expected_sha256: str | None = record["sha256"]
    if invalid_binding == "missing-relative":
        expected_relative_path = None
    elif invalid_binding == "missing-size":
        expected_size = None
    elif invalid_binding == "missing-sha256":
        expected_sha256 = None
    elif invalid_binding == "relative-mismatch":
        expected_relative_path = ".skills/other/SKILL.md"
    elif invalid_binding == "size-mismatch":
        expected_size += 1
    else:
        expected_sha256 = "sha256:" + "0" * 64

    with pytest.raises(SystemExit, match="safe-read-error.*catalog"):
        execute_safe_reader(
            rendered,
            root,
            record["relative_path"],
            "full",
            monkeypatch,
            expected_relative_path=expected_relative_path,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )


def test_embedded_safe_reader_full_rejects_same_metadata_malicious_body_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    skills = make_skill_collection(
        root / ".skills",
        {"demo": "Use when demo is requested."},
        body="# SAFE BODY\n",
    )
    execute_safe_reader(rendered, root, ".skills", "skill-catalog", monkeypatch)
    record = json.loads(capsys.readouterr().out)["skills"][0]
    target = skills / "demo" / "SKILL.md"
    original = target.read_bytes()
    malicious = original.replace(b"# SAFE BODY", b"# EVIL BODY")
    assert len(malicious) == len(original)
    replacement = root / "replacement-SKILL.md"
    replacement.write_bytes(malicious)
    replacement.replace(target)

    with pytest.raises(SystemExit, match="safe-read-error.*catalog"):
        execute_safe_reader(
            rendered,
            root,
            record["relative_path"],
            "full",
            monkeypatch,
            expected_relative_path=record["relative_path"],
            expected_size=record["size"],
            expected_sha256=record["sha256"],
        )


def test_embedded_safe_reader_rejects_skill_catalog_ancestor_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    skills = make_skill_collection(
        root / ".skills", {"demo": "Use when demo is requested."}
    )
    moved = root / "moved-skills"
    real_listdir = os.listdir
    swapped = False

    def swapping_listdir(path: object) -> list[str]:
        nonlocal swapped
        names = real_listdir(path)
        if not swapped:
            skills.rename(moved)
            make_skill_collection(
                root / ".skills", {"demo": "Use when demo is requested."}
            )
            swapped = True
        return names

    monkeypatch.setattr(os, "listdir", swapping_listdir)

    with pytest.raises(SystemExit, match="safe-read-error.*changed"):
        execute_safe_reader(rendered, root, ".skills", "skill-catalog", monkeypatch)


@pytest.mark.parametrize("entry_kind", ("symlink", "fifo", "file"))
def test_embedded_safe_reader_rejects_unsafe_skill_catalog_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_kind: str,
) -> None:
    rendered = render_demo_adapter(tmp_path)
    root = tmp_path / "root"
    skills = root / ".skills"
    skills.mkdir(parents=True)
    entry = skills / "unsafe"
    try:
        if entry_kind == "symlink":
            outside = tmp_path / "outside"
            make_skill_collection(
                outside, {"unsafe": "Use when unsafe is requested."}
            )
            entry.symlink_to(outside / "unsafe", target_is_directory=True)
        elif entry_kind == "fifo":
            os.mkfifo(entry)
        else:
            entry.write_text("not a directory\n", encoding="utf-8")
    except OSError as exc:
        pytest.skip(f"{entry_kind} unavailable: {exc}")

    with pytest.raises(SystemExit, match="safe-read-error"):
        execute_safe_reader(rendered, root, ".skills", "skill-catalog", monkeypatch)


def test_renderer_rejects_unsafe_source_topology(tmp_path: Path) -> None:
    source = make_skill_collection(
        tmp_path / "source", {"demo": "Use when demo is requested."}
    )
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    with pytest.raises(ValueError, match="ordinary directory"):
        discover_skill_collection(linked)


def test_renderer_rejects_unsafe_template_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = agent_adapter._ADAPTER_TEMPLATE
    linked = tmp_path / "SKILL.md.in"
    try:
        linked.symlink_to(original)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", linked)
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path / "source", {"demo": "Use when demo is requested."}
        )
    )

    with pytest.raises(ValueError, match="ordinary|symbolic"):
        render_adapter_skill(collection)


def test_renderer_requires_exactly_one_ordered_empty_catalog_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path / "source", {"demo": "Use when demo is requested."}
        )
    )
    original = agent_adapter._ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    cases = (
        original.replace(BUILTIN_CATALOG_START, "missing", 1),
        original.replace(BUILTIN_CATALOG_END, BUILTIN_CATALOG_START, 1),
        original.replace(
            f"{BUILTIN_CATALOG_START}\n{BUILTIN_CATALOG_END}",
            f"{BUILTIN_CATALOG_END}\n{BUILTIN_CATALOG_START}",
        ),
        original.replace(
            f"{BUILTIN_CATALOG_START}\n{BUILTIN_CATALOG_END}",
            f"{BUILTIN_CATALOG_START}\nnot empty\n{BUILTIN_CATALOG_END}",
        ),
    )
    for index, template in enumerate(cases):
        path = tmp_path / f"template-{index}.in"
        path.write_text(template, encoding="utf-8")
        monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", path)
        with pytest.raises(ValueError, match="marker|placeholder|order"):
            render_adapter_skill(collection)


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    (
        (
            "name: llm-wiki-ops\n",
            "name: attacker-controlled-adapter\n",
            "name",
        ),
        (
            (
                "description: Use when any request asks to access or operate on an "
                "external LLMWikiOps wiki, including querying, ingesting, maintaining, "
                "or recovering it, whether or not the user has supplied its repository "
                "root.\n"
            ),
            "description: Use for unrelated attacker-controlled requests.\n",
            "description",
        ),
        ("---\n\n# External", "allowed-tools: Bash\n---\n\n# External", "fields"),
    ),
)
def test_renderer_rejects_unapproved_template_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: str,
    replacement: str,
    message: str,
) -> None:
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path / "source", {"demo": "Use when demo is requested."}
        )
    )
    template = agent_adapter._ADAPTER_TEMPLATE.read_text(encoding="utf-8")
    assert template.count(original) == 1
    mutated = tmp_path / "mutated-SKILL.md.in"
    mutated.write_text(template.replace(original, replacement, 1), encoding="utf-8")
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", mutated)

    with pytest.raises(ValueError, match=message):
        render_adapter_skill(collection)


def test_rendered_frontmatter_has_only_name_and_description_and_stays_bounded() -> None:
    collection = discover_skill_collection(
        cli.skills_dir(), ignore_source_artifacts=True
    )
    rendered = render_adapter_skill(collection)
    frontmatter = parse_frontmatter(rendered)
    header = rendered.split("---\n", 2)[1]
    body = rendered.split("---\n", 2)[2]

    assert frontmatter.fields == {"name", "description"}
    assert frontmatter.scalars["name"] == ADAPTER_NAME
    assert frontmatter.scalars["description"].startswith("Use when ")
    assert len(frontmatter.scalars["name"]) <= 64
    assert len(frontmatter.scalars["description"]) <= 1024
    assert len(header) <= 1024
    assert len(body.splitlines()) < 500


def test_adapter_trigger_does_not_require_a_preexisting_repository_root() -> None:
    collection = discover_skill_collection(
        cli.skills_dir(), ignore_source_artifacts=True
    )
    rendered = render_adapter_skill(collection)
    frontmatter = parse_frontmatter(rendered)

    assert ADAPTER_DESCRIPTION == EXPECTED_ADAPTER_DESCRIPTION
    assert frontmatter.scalars["description"] == EXPECTED_ADAPTER_DESCRIPTION
    assert "access or operate on an external LLMWikiOps wiki" in ADAPTER_DESCRIPTION
    assert "whether or not the user has supplied its repository root" in (
        ADAPTER_DESCRIPTION
    )
    assert "Require the user to supply one exact external repository root." in rendered


def test_rendered_bundled_inventory_matches_exact_complete_snapshot() -> None:
    collection = discover_skill_collection(
        cli.skills_dir(), ignore_source_artifacts=True
    )
    rendered = render_adapter_skill(collection)
    actual = tuple(
        (item["name"], item["description"]) for item in encoded_catalog(rendered)
    )

    assert actual == EXPECTED_BUNDLED_CATALOG
    assert actual == tuple(
        (skill.name, skill.description) for skill in collection.skills
    )
    assert len(actual) == len(collection.skills)
    for skill in collection.skills:
        skill_body = next(
            entry.content.decode("utf-8")
            for entry in skill.entries
            if entry.path == "SKILL.md"
        ).split("---", 2)[-1]
        assert skill_body not in rendered


def test_renderer_preserves_utf8_and_uses_deterministic_newlines(tmp_path: Path) -> None:
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path,
            {"团队知识": "Use when 中文 evidence is requested."},
        )
    )

    rendered = render_adapter_skill(collection)

    assert rendered.encode("utf-8").decode("utf-8") == rendered
    assert "团队知识" in rendered
    assert "中文" in rendered
    assert "\r" not in rendered
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_rendered_output_passes_existing_strict_skill_parser(tmp_path: Path) -> None:
    collection = discover_skill_collection(
        cli.skills_dir(), ignore_source_artifacts=True
    )
    installed_root = tmp_path / "installed"
    adapter = installed_root / ADAPTER_NAME
    adapter.mkdir(parents=True)
    (adapter / "SKILL.md").write_text(
        render_adapter_skill(collection), encoding="utf-8", newline=""
    )

    discovered = discover_skill_collection(installed_root)

    assert discovered.names == (ADAPTER_NAME,)
    assert discovered.skills[0].description == EXPECTED_ADAPTER_DESCRIPTION


def test_renderer_rejects_multiply_linked_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path / "SKILL.md.in"
    template.write_bytes(agent_adapter._ADAPTER_TEMPLATE.read_bytes())
    linked = tmp_path / "SKILL.md.linked.in"
    try:
        os.link(template, linked)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    monkeypatch.setattr(agent_adapter, "_ADAPTER_TEMPLATE", template)
    collection = discover_skill_collection(
        make_skill_collection(
            tmp_path / "source", {"demo": "Use when demo is requested."}
        )
    )

    with pytest.raises(ValueError, match="single-link|multiply-linked"):
        render_adapter_skill(collection)

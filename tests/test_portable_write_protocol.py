import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from obsidian_wiki.cli import (
    _list_record_payload,
    _normalize_cache_check_argv,
    _render_transaction_failure,
    build_parser,
)
from obsidian_wiki.transaction import TransactionError, TransactionRecord
from obsidian_wiki.transaction_guidance import guidance_for_record
from obsidian_wiki.transaction_validation import TransactionValidationReport


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "obsidian_wiki/_data/skills/llm-wiki/SKILL.md"
TRANSACTION_REVIEW = (
    ROOT / "obsidian_wiki/_data/skills/wiki-transaction-review/SKILL.md"
)
SOURCE_WORKFLOW_SKILLS = (
    ROOT / "obsidian_wiki/_data/skills/wiki-capture/SKILL.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-ingest/SKILL.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-import/SKILL.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-research/SKILL.md",
)
SOURCE_WORKFLOW_REFERENCES = (
    ROOT / "obsidian_wiki/_data/skills/wiki-capture/references/source-snapshot.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-ingest/references/ingest-prompts.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-ingest/references/pageindex.md",
    ROOT / "obsidian_wiki/_data/skills/wiki-ingest/references/url-sources.md",
)


def test_canonical_protocol_has_required_top_level_sections() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    headings = re.findall(r"(?m)^## (.+)$", text)
    assert headings[:3] == [
        "Configuration",
        "Authority and provenance",
        "Knowledge write protocol",
    ]


def test_canonical_protocol_defines_configuration_and_authority() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for required in (
        "nearest ancestor `.obsidian-wiki/config.toml`",
        "obsidian-wiki setup [DIR]",
        "fail closed",
        "repository root",
        "vault `AGENTS.md` when present",
        "repository-relative Source ID",
        "reviewed Markdown snapshot",
    ):
        assert required in flat


def test_canonical_protocol_is_one_eight_step_transaction() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    protocol = text.split("## Knowledge write protocol", 1)[1]
    steps = re.findall(r"(?m)^(\d+)\. \*\*(.+?)\*\*", protocol)
    assert [int(number) for number, _ in steps] == list(range(1, 9))
    for required in (
        "keep the wiki\n   read-only while building the source closure",
        "transaction begin --source",
        "--json --pretty",
        "candidate_vault",
        "transaction delete",
        "transaction validate",
        "transaction commit",
        "transaction list --json --pretty",
        "recommended_action",
        "allowed_actions",
        "hot status --json",
        "hot inputs --json --pretty",
        "hot mark-current",
    ):
        assert required in protocol


def test_begin_passes_the_complete_source_closure_to_one_option() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    command = (
        "obsidian-wiki transaction begin --source <source1> [source2 ...] "
        "--json --pretty"
    )
    assert command in text
    assert text.count("--source") == 1
    assert "Repeat `--source`" not in text


def test_cli_ownership_and_git_boundary_are_explicit() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    for required in (
        "Do not commit, push, or open a pull request",
        "never edit manifest shards directly",
        "never rewrite stable `index.md` or `log.md`",
    ):
        assert required in text


def test_candidate_contract_preserves_transaction_validated_invariants() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for required in (
        "title`, `category`, `tags`, `sources`, `created`, and `updated",
        "created = updated = started_at",
        "preserve the existing `created`",
        "non-empty subset of the transaction source closure",
        "concepts/`, `entities/`, `skills/`, `references/`, `synthesis/`, `journal/`, or `projects/",
        "OBSIDIAN_LINK_FORMAT",
    ):
        assert required in flat


def test_recovery_contract_matches_failure_and_list_payloads(
    capsys,
) -> None:
    record = TransactionRecord(
        transaction_id="tx-1",
        status="failed",
        started_at="2026-08-12T00:00:00+00:00",
        source_ids=("sources/a.md",),
        workspace=Path("/tmp/tx-1"),
        candidate_vault=Path("/tmp/tx-1/wiki"),
        preimages={},
        deletions=(),
    )
    guidance = guidance_for_record(record)
    listed = _list_record_payload(record, guidance)
    assert "error" not in listed and "recovery" not in listed
    assert set(listed["recommended_action"]) == {"command", "reason", "requires"}
    assert listed["recommended_action"] in listed["allowed_actions"]
    assert all(
        set(action) == {"command", "reason", "requires"}
        and isinstance(action["requires"], list)
        for action in listed["allowed_actions"]
    )

    class Manager:
        def load(self, transaction_id: str) -> TransactionRecord:
            assert transaction_id == "tx-1"
            return record

    args = argparse.Namespace(json=True, pretty=False)
    result = _render_transaction_failure(
        args,
        TransactionError("promotion failed"),
        manager=Manager(),
        transaction_id="tx-1",
    )
    assert result == 1
    envelope = json.loads(capsys.readouterr().out)
    assert set(envelope) == {"status", "error", "recovery"}
    assert set(envelope["error"]) == {"code", "message"}
    assert set(envelope["recovery"]) == {
        "transaction_id",
        "transaction_status",
        "inspect_command",
        "preferred_action",
        "alternatives",
    }
    assert envelope["recovery"]["transaction_id"] == listed["transaction_id"]


def test_recovery_protocol_cross_checks_identity_requirements_and_outcomes() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for required in (
        "Save the failed command envelope",
        "does not repeat `error` or `recovery`",
        "exactly one retained record",
        "empty, missing, mismatched, duplicated, or ambiguous",
        "satisfy every string in the action's `requires` list",
        "Only a successful `transaction commit` or `transaction retry` is a knowledge commit",
        "restore`, `abort`, and `discard` do not trigger hot refresh",
    ):
        assert required in flat


def test_transaction_review_fields_follow_cli_payload_ownership() -> None:
    record = TransactionRecord(
        transaction_id="tx-review",
        status="active",
        started_at="2026-08-12T00:00:00+00:00",
        source_ids=("sources/a.md",),
        workspace=Path("/tmp/tx-review"),
        candidate_vault=Path("/tmp/tx-review/wiki"),
        preimages={},
        deletions=("concepts/obsolete.md",),
    )
    listed = _list_record_payload(record, guidance_for_record(record))
    validated = TransactionValidationReport(
        transaction_id="tx-review",
        status="pass",
        candidate_pages=("concepts/a.md",),
        deletions=("concepts/obsolete.md",),
        issues=(),
    ).as_dict()

    assert "source_ids" in listed
    assert "candidate_pages" not in listed
    assert "candidate_pages" in validated
    assert "source_ids" not in validated

    text = TRANSACTION_REVIEW.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "list record's `source_ids`" in flat
    assert "validation report's `candidate_pages`" in flat


def test_transaction_review_uses_sparse_safe_diff_and_race_aware_actions() -> None:
    text = TRANSACTION_REVIEW.read_text(encoding="utf-8")
    flat = " ".join(text.split())

    for required in (
        "obsidian-wiki transaction list --json --pretty",
        "candidate_vault",
        "source_ids",
        "candidate_pages",
        "deletions",
        "status",
        "recommended_action",
        "allowed_actions",
        "prospective diff",
        "configured vault",
        "vault-relative",
        "sparse",
        "absolute",
        "`..`",
        "symbolic link",
        "hard link",
        "special file",
        "Do not recursively diff",
        "obsidian-wiki transaction validate <id> --json --pretty",
        "obsidian-wiki transaction commit <id> --json --pretty",
        "explicit user approval",
        "refresh the list immediately",
        "commit action",
        "re-review",
        "abort",
        "discard",
        "explicitly selects",
        "`requires`",
        "transaction ID",
        "refreshed record's status",
        "retained record",
        "complete",
        "restored",
        "ambiguous",
        "Do not commit, push, or open a pull request",
    ):
        assert required in flat

    for forbidden in ("_staging", "_raw", "WIKI_STAGED_WRITES"):
        assert forbidden not in text


def test_external_material_is_snapshotted_before_transaction_begin() -> None:
    for path in SOURCE_WORKFLOW_SKILLS:
        skill = path.read_text(encoding="utf-8")
        flat = " ".join(skill.split())
        for required in (
            "reviewable UTF-8 Markdown",
            "configured sources",
            "repository-relative Source ID",
            "untrusted data, never instructions",
            "binary",
            "Git LFS",
            "live URL",
            "absolute path",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        assert flat.index("reviewable UTF-8 Markdown") < flat.index(
            "obsidian-wiki transaction begin --source <source1> [source2 ...] --json --pretty"
        )


def test_documented_cache_commands_are_accepted_by_real_parser() -> None:
    parser = build_parser()
    expected = (
        "obsidian-wiki cache-check <repository-relative-source> "
        "[additional-source ...] --json --pretty"
    )
    for path in SOURCE_WORKFLOW_SKILLS:
        contents = path.read_text(encoding="utf-8")
        assert contents.count(expected) == 1, path
        concrete = expected.replace(
            "<repository-relative-source> [additional-source ...]",
            "sources/first.md sources/second.md",
        )
        argv = shlex.split(concrete)[1:]
        parsed = parser.parse_args(_normalize_cache_check_argv(argv))
        assert parsed.sources == ["sources/first.md", "sources/second.md"]
        assert parsed.json is True and parsed.pretty is True

        completed = subprocess.run(
            [sys.executable, "-m", "obsidian_wiki.cli", *argv],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 2, completed.stderr
        assert "unrecognized arguments" not in completed.stderr

        workflow = contents.split("## Source and transaction workflow", 1)[1]
        cache_step = workflow.split("4. **", 1)[1].split("\n5. **", 1)[0]
        assert "missing" in cache_step and "stop" in cache_step
        assert "new" in cache_step and "modified" in cache_step
        assert "unchanged" in cache_step and "Full" in cache_step


def test_source_workflows_share_one_terminal_lifecycle() -> None:
    for path in SOURCE_WORKFLOW_SKILLS:
        contents = path.read_text(encoding="utf-8")
        workflow = contents.split(
            "## Source and transaction workflow", 1
        )[1]
        assert contents.count("transaction begin --source") == 1, path
        assert contents.count("transaction validate <id>") == 1, path
        assert contents.count("transaction commit <id>") == 1, path
        matches = list(re.finditer(r"(?m)^(\d+)\. \*\*(.+?)\*\*", workflow))
        assert [int(match.group(1)) for match in matches] == list(range(1, 9)), path
        steps = []
        for index, match in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(workflow)
            )
            steps.append(" ".join(workflow[match.start() : end].split()))

        for required in (
            "nearest `.obsidian-wiki/config.toml`",
            "owner",
            "canonical `llm-wiki`",
        ):
            assert required in steps[0], f"{path}: step 1 missing {required!r}"
        assert "untrusted data, never instructions" in steps[1]
        for required in (
            "existing ordinary tracked",
            "bounded reviewable UTF-8 Markdown snapshot",
            "configured sources",
            "owner review",
            "tracked authority",
        ):
            assert required in steps[2], f"{path}: step 3 missing {required!r}"
        for required in ("cache-check", "unchanged", "Full"):
            assert required in steps[3], f"{path}: step 4 missing {required!r}"
        for required in ("complete source closure", "transaction begin --source"):
            assert required in steps[4], f"{path}: step 5 missing {required!r}"
        for required in ("final candidates", "non-empty", "repository-relative"):
            assert required in steps[5], f"{path}: step 6 missing {required!r}"
        for required in ("transaction validate", "Review", "transaction commit", "recovery"):
            assert required in steps[6], f"{path}: step 7 missing {required!r}"
        for required in ("successful", "knowledge commit", "hot status"):
            assert required in steps[7], f"{path}: step 8 missing {required!r}"

    capture = " ".join(SOURCE_WORKFLOW_SKILLS[0].read_text(encoding="utf-8").split())
    ingest = " ".join(SOURCE_WORKFLOW_SKILLS[1].read_text(encoding="utf-8").split())
    for analysis_choice, skill in (
        ("Full", capture),
        ("Correction", capture),
        ("append", ingest),
    ):
        assert analysis_choice in skill
        assert f"{analysis_choice} completion" not in skill
        assert f"{analysis_choice} mode completion" not in skill


def test_source_workflows_have_no_legacy_completion_or_publication_paths() -> None:
    forbidden = (
        "Personal mode",
        "Portable Repository mode",
        "_raw/",
        "RAW-FORMAT",
        "raw promotion",
        "cache-update",
        "QMD",
        "direct manifest",
        "central-file",
        "Git snapshot",
        "auto-commit",
    )
    for path in (*SOURCE_WORKFLOW_SKILLS, *SOURCE_WORKFLOW_REFERENCES):
        contents = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in contents, f"{path}: contains {term!r}"


def test_source_workflows_link_snapshot_rules_and_leave_git_to_owner() -> None:
    for path in SOURCE_WORKFLOW_SKILLS:
        contents = path.read_text(encoding="utf-8")
        match = re.search(
            r"\[[^]]*source snapshot[^]]*\]\(([^)]+source-snapshot\.md)\)",
            contents,
            re.I,
        )
        assert match, path
        assert (path.parent / match.group(1)).resolve().is_file(), path
        flat = " ".join(contents.split())
        for required in (
            "new snapshot requires owner Git review",
            "becomes tracked authority",
            "framework and agent must not run `git add`, `git commit`, or `git push`",
            '["git", "ls-files", "--error-unmatch", "--", "<Source ID>"]',
            "manifest-tracked",
            "Git-tracked",
            "owner review, stage, and commit externally, then rerun",
        ):
            assert required in flat, f"{path}: missing {required!r}"
        assert flat.index(
            '["git", "ls-files", "--error-unmatch", "--", "<Source ID>"]'
        ) < flat.index("cache-check") < flat.index("transaction begin --source")


def test_pageindex_documents_real_entrypoint_and_snapshot_gate() -> None:
    path = ROOT / "obsidian_wiki/_data/skills/wiki-ingest/references/pageindex.md"
    pageindex = path.read_text(encoding="utf-8")
    flat = " ".join(pageindex.split())
    for required in (
        "PAGEINDEX_REPO",
        "PAGEINDEX_MODEL",
        "PAGEINDEX_MIN_PAGES",
        "run_pageindex.py",
        "uv run --no-project python",
        "--pdf_path",
        "--model",
        "--if-add-node-summary yes",
        "--if-add-doc-description yes",
        "doc_description",
        "structure",
        "start_index",
        "end_index",
        "1-indexed physical PDF pages",
        "bounded reviewable UTF-8 Markdown snapshot",
        "owner review",
        "tracked authority",
        "repository-relative Source ID",
        "transaction begin --source",
        "fail closed",
    ):
        assert required in flat
    assert flat.index("run_pageindex.py") < flat.index(
        "bounded reviewable UTF-8 Markdown snapshot"
    ) < flat.index("owner review") < flat.index("tracked authority") < flat.index(
        "transaction begin --source"
    )
    match = re.search(
        r"\[[^]]*source snapshot[^]]*\]\(([^)]+source-snapshot\.md)\)",
        pageindex,
        re.I,
    )
    assert match
    assert (path.parent / match.group(1)).resolve().is_file()


def test_snapshot_hash_example_is_reproducible() -> None:
    snapshot = SOURCE_WORKFLOW_REFERENCES[0].read_text(encoding="utf-8")
    match = re.search(
        r'content_hash: "(sha256:[0-9a-f]{64})".*?```text\n(.*?)```',
        snapshot,
        re.S,
    )
    assert match
    exact_text = match.group(2)
    assert exact_text == "Hello, wiki.\n"
    assert "\r" not in exact_text and not exact_text.startswith("\ufeff")
    assert match.group(1) == "sha256:" + hashlib.sha256(
        exact_text.encode("utf-8")
    ).hexdigest()


def test_import_defines_executable_graph_and_okf_transformations() -> None:
    import_skill = SOURCE_WORKFLOW_SKILLS[2].read_text(encoding="utf-8")

    example = re.search(
        r"## graph\.json detection and mapping.*?```json\n(.*?)```",
        import_skill,
        re.S,
    )
    assert example
    graph = json.loads(example.group(1))
    assert set(graph) >= {"nodes", "links", "graph"}
    assert graph["nodes"] and set(graph["nodes"][0]) >= {
        "id",
        "label",
        "category",
        "tags",
    }
    assert set(graph["links"][0]) >= {"source", "target"}

    template = re.search(
        r"### Graph candidate template.*?```markdown\n(.*?)```",
        import_skill,
        re.S,
    )
    assert template
    opening, metadata, body = template.group(1).split("---", 2)
    assert opening == ""
    keys = {
        line.split(":", 1)[0]
        for line in metadata.splitlines()
        if line and not line.startswith((" ", "<"))
    }
    assert {"title", "category", "tags", "sources", "created", "updated"} <= keys
    assert "## Related" in body

    mode_rows = re.findall(
        r"(?m)^\| `(merge|skip|replace)` \| ([^|]+) \|$", import_skill
    )
    modes = {mode: behavior.strip() for mode, behavior in mode_rows}
    assert set(modes) == {"merge", "skip", "replace"}
    assert "preserv" in modes["merge"]
    assert "untouched" in modes["skip"]
    assert "replace" in modes["replace"]

    graph_section = import_skill.split("## graph.json detection and mapping", 1)[1]
    okf_section = import_skill.split("## OKF bundle detection and mapping", 1)[1]
    for required in ("adjacency", "typed", "## Related", "frontmatter"):
        assert required in graph_section
    for required in (
        "non-empty `type`",
        "concept ID",
        "Reverse-map",
        "Unicode",
        "folder-note",
        "OBSIDIAN_LINK_FORMAT=markdown",
        "substantive existing body",
    ):
        assert required in okf_section


def test_external_input_boundaries_are_bounded_and_fail_closed() -> None:
    url = " ".join(SOURCE_WORKFLOW_REFERENCES[3].read_text(encoding="utf-8").split())
    pageindex = " ".join(
        SOURCE_WORKFLOW_REFERENCES[2].read_text(encoding="utf-8").split()
    )
    import_skill = " ".join(
        SOURCE_WORKFLOW_SKILLS[2].read_text(encoding="utf-8").split()
    )
    research = " ".join(
        SOURCE_WORKFLOW_SKILLS[3].read_text(encoding="utf-8").split()
    )

    for required in (
        "HTTPS",
        "5 redirects",
        "30-second",
        "10 MiB",
        "credentials",
        "IP literals",
        "loopback",
        "private",
        "link-local",
        "multicast",
        "unspecified",
        "reserved",
        "text/plain",
        "text/markdown",
        "text/html",
        "application/json",
        "cross-origin subresources",
        "explicit authorization",
    ):
        assert required in url

    for required in (
        "results/<basename>_structure.json",
        "ordinary single-link file",
        "10 MiB",
        "depth",
        "page ranges",
        "argument vector",
        "must not trust stdout",
        "explicit disclosure authorization",
        "provider policy",
    ):
        assert required in pageindex

    archive_policy = ("10 MiB", "100 files", "10,000 records", "depth 20")
    for document in (import_skill, research):
        for required in archive_policy + (
            "path traversal",
            "absolute",
            "symbolic links",
            "hard links",
            "special files",
            "decompression bomb",
            "Git LFS pointer",
            "attribution",
            "license",
            "omission markers",
        ):
            assert required in document


def test_pageindex_preflight_precedes_invocation_and_postflight() -> None:
    pageindex = SOURCE_WORKFLOW_REFERENCES[2].read_text(encoding="utf-8")
    sequence = pageindex.split("## Safe execution sequence", 1)[1].split("\n## ", 1)[0]
    matches = list(re.finditer(r"(?m)^(\d+)\. \*\*(.+?)\*\*", sequence))
    assert [int(match.group(1)) for match in matches] == list(range(1, 7))
    steps = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(sequence)
        steps.append(" ".join(sequence[match.start() : end].split()))

    assert "<PAGEINDEX_REPO>/results/<basename>_structure.json" in steps[0]
    for required in ("owner-controlled ordinary directories", "contained", "symlink", "special"):
        assert required in steps[1]
    assert "must not exist" in steps[2]
    assert "hard link" in steps[2] and "stop before the command" in steps[2]
    assert "exclusive run" in steps[3] and "untrusted concurrent writer" in steps[3]
    assert "run_pageindex.py" in steps[4] and "argument vector" in steps[4]
    for required in ("postflight", "lstat", "contained", "ordinary single-link", "10 MiB", "schema"):
        assert required in steps[5]

    flat = " ".join(sequence.split())
    assert flat.index("preflight") < flat.index("run_pageindex.py") < flat.index("postflight")


def test_git_authority_commands_are_argv_safe_and_require_clean_head(tmp_path: Path) -> None:
    ls_template = '["git", "ls-files", "--error-unmatch", "--", "<Source ID>"]'
    status_template = (
        '["git", "status", "--porcelain=v1", "--untracked-files=all", "--", '
        '"<Source ID>"]'
    )
    for path in SOURCE_WORKFLOW_SKILLS:
        flat = " ".join(path.read_text(encoding="utf-8").split())
        for required in (
            ls_template,
            status_template,
            "non-empty POSIX repository-relative Source ID",
            "NUL",
            "backslash",
            "configured sources",
            "source_id semantics",
            "output must be empty",
            "no HEAD",
            "tracked is not committed-reviewed",
        ):
            assert required in flat, f"{path}: missing {required!r}"

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    source_id = "sources/reviewed name;still-data.md"
    source = repo / source_id
    source.parent.mkdir()
    source.write_text("reviewed\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", source_id], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "source"], cwd=repo, check=True)

    listed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", source_id],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    clean = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", source_id],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0 and listed.stdout.strip() == source_id
    assert clean.returncode == 0 and clean.stdout == ""
    assert not (repo / "still-data.md").exists()


def test_research_uses_url_policy_and_import_full_is_orthogonal() -> None:
    research_path = SOURCE_WORKFLOW_SKILLS[3]
    research = research_path.read_text(encoding="utf-8")
    match = re.search(r"\[[^]]*URL[^]]*policy[^]]*\]\(([^)]+url-sources\.md)\)", research, re.I)
    assert match
    assert (research_path.parent / match.group(1)).resolve().is_file()
    assert "Every URL" in research and "must use" in research

    import_skill = " ".join(SOURCE_WORKFLOW_SKILLS[2].read_text(encoding="utf-8").split())
    assert "Full is orthogonal to merge, skip, and replace" in import_skill
    assert "analyze unchanged snapshots" in import_skill
    assert "same single transaction lifecycle" in import_skill


def test_pageindex_node_id_is_optional_but_validated_when_present() -> None:
    pageindex = " ".join(
        SOURCE_WORKFLOW_REFERENCES[2].read_text(encoding="utf-8").split()
    )
    assert "`node_id` is optional" in pageindex
    assert "when present" in pageindex
    assert "non-empty string" in pageindex
    assert "unique" in pageindex
    assert "Missing `node_id` is allowed" in pageindex

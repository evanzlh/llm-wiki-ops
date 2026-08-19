# External Adapter Preflight-Only Read Design

**Date:** 2026-08-19

**Status:** Approved direction; awaiting written-spec review

## Context

The static-repository Adapter already replaced its adversarial TOCTOU reader with
an explicit-root `info --json` then `check` preflight. Behavioral evaluation then
added increasingly detailed instructions requiring every later byte-reading
process to repeat `lstat`, type, link-count, and size checks. A fresh recovery
scenario still omitted one repeated check even though the Adapter stated the rule
explicitly.

This is not a missing edge-case sentence. It shows that a natural-language Skill
is a poor place to emulate a filesystem reference monitor. The repeated-read
protocol also duplicates static validation already performed by `check`, grows
the frequently loaded Adapter to its size limit, and conflicts with the approved
owner-controlled, local, quiescent repository model.

## Considered approaches

1. **Preflight-only reads (selected).** Treat successful exact-root `info` and
   `check` as the static safety boundary, then permit ordinary bounded reads.
   This is the smallest protocol and matches the supported threat model.
2. **Keep adding Agent instructions.** Add special cases for recovery metadata,
   preimages, citations, and future rereads. This remains nondeterministic,
   consumes Adapter budget, and recreates the complexity being removed.
3. **Add a deterministic read CLI/helper.** Mechanically enforce every read.
   This could defend a stronger concurrency model, but reintroduces a reader API
   the project explicitly does not need for a quiescent owner-controlled Wiki.

## Supported boundary

The supported repository remains one user-supplied exact absolute root on an
owner-controlled local filesystem. The user keeps it quiescent throughout the
operation. No sync process, branch switch, pull, editor automation, other Agent,
or malicious process changes repository configuration, authority, skills, or
review evidence while the Agent is working.

The Agent must completely load the Adapter, keep its business CWD unchanged, bind
the exact `<wiki-cli>` and `<git-cli>` argv prefixes, and run these commands
strictly in order before any ordinary external repository read, listing, or
search:

```text
<wiki-cli> info --json
<wiki-cli> check
```

`info` must return the supplied exact root and a compatible resolved runtime;
`check` must pass. Either failure stops the operation without fallback, root
search, authority loading, or mutation.

`check` remains the deterministic owner of static topology validation, including
configured path containment, symbolic links, managed-file link counts, special
files, unsafe names, and other portable repository invariants. This design does
not weaken `obsidian_wiki.portable` or `check_portable_repo`.

## Reads after preflight

After successful preflight, the Agent may use ordinary file tools against paths
derived from the verified configuration. It must still:

- enumerate only the configured skills directory's direct children rather than
  recursively hunting the repository;
- read routing frontmatter within 64 KiB and require a complete valid block;
- reject duplicate or malformed skill names;
- limit a complete authority, task, candidate, query-result, hot, recovery, or
  citation read to 1 MiB;
- preserve catalog merge, rerouting, authority order, one-body-per-step loading,
  exact-root command binding, and transaction/recovery review gates.

The Adapter no longer requires a metadata check in the same process as each byte
read. It does not prescribe `lstat`, `stat.S_ISREG`, link-count checks,
non-following open mechanics, per-read snapshots, or repeated checks for later
formatting, citation, hashing, JSON, or preimage reads. Resource bounds describe
the maximum content an Agent may consume; they are not a TOCTOU defense.

If a command, Git status/diff, or observed content indicates that relevant
repository evidence changed after preflight, the Agent stops and restarts only
after the repository is quiescent. The Adapter does not attempt to distinguish
benign from hostile concurrent replacement.

## Read-only correction before authority

Catalog routing is accepted by its final validated state, not by requiring the
Agent's first parsing attempt to be perfect. Before opening any authority body,
the Agent may discard an incomplete or invalid read-only catalog result and run
a corrected bounded parser. A discarded attempt grants no routing authority and
none of its fields may be merged, selected, or used for execution.

The correction boundary closes only when one final catalog result:

- covers every direct skill entry from the verified configured skills path;
- contains each entry's exact nonempty `name` and complete nonempty
  `description`;
- has valid complete frontmatter and no duplicate or ambiguous name; and
- has been inspected as a whole before repository descriptions are merged and
  selection is rerun.

Until that state exists, the Agent must not open an authority body, execute a
Wiki task, or make a repository change. If it cannot obtain the final valid
catalog, it stops. This allowance does not apply to root binding: a failed,
empty, malformed, unresolved, or wrong-root `info --json`, or a failed `check`,
still ends the operation without retrying another root or continuing to ordinary
repository access.

## Behavioral evidence model

Behavioral evaluation distinguishes repository behavior from the Codex JSONL
event renderer. The evaluated command wrapper records the exact argv, CWD, exit
status, stdout bytes, and stderr bytes while passing those streams through
unchanged. For required structured CLI calls, the audit validates the captured
stdout as nonempty structured output and checks its required fields. A missing
`aggregated_output` field, or an empty value there, is not by itself evidence
that the underlying CLI emitted no output when the wrapper's independent stream
record proves otherwise.

Agent behavior must still be consistent with the validated result: `check` may
start only after valid exact-root `info`, and authority loading may start only
after completed `check` and a final valid catalog. If independent stream evidence
also shows empty or malformed output, the run fails even if the Agent claims
success. The wrapper is evaluation instrumentation only; it does not parse on
the Agent's behalf or change production commands.

## Testing and acceptance

Contract tests will first fail against the repeated-read language, then require:

- one complete Adapter load and strict exact-root `info` then `check` ordering;
- no per-read `lstat`, `stat.S_ISREG`, `os.path.isfile`, same-process metadata,
  link-count, snapshot, or reread-revalidation protocol in the generated Adapter;
- retained 64 KiB frontmatter and 1 MiB complete-read limits;
- read-only catalog correction may replace a discarded invalid attempt, but no
  authority read or execution may occur before one final complete valid catalog;
- retained direct catalog enumeration, routing, authority order, recovery review,
  hot-refresh, exact `-C`, unchanged business CWD, and no alternate-root behavior;
- independent command-stream evidence for required CLI output, without treating
  Codex JSONL `aggregated_output` as the sole observation channel;
- unchanged deterministic portable/check coverage for static unsafe topology;
- source, wheel, sdist, and installed Adapter byte parity.

Fresh behavioral evaluation will judge ordinary post-preflight bounded reads as
valid without auditing a metadata syscall before each open. It will still fail
preflight-order violations, unbounded or recursive hunting, wrong-root commands,
use of a discarded catalog result, authority access before final catalog
validation, authority-order/rerouting errors, reconstructed recovery commands,
premature hot marking, unexpected writes, or evidence of concurrent repository
change. A corrected read-only catalog parse completed before authority is not a
failure.

## Scope

This revision changes only the generated Adapter text, its renderer/contract
tests where required, the static-repository design documentation, and behavioral
acceptance criteria. It adds no CLI, filesystem reader, repository selector,
automatic installation, Wiki migration, or runtime-skill change.

This document supersedes the per-read metadata-check requirements in
`2026-08-19-external-adapter-static-repository-design.md`; all other boundaries
and acceptance criteria in that design remain in force.

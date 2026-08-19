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

## Testing and acceptance

Contract tests will first fail against the repeated-read language, then require:

- one complete Adapter load and strict exact-root `info` then `check` ordering;
- no per-read `lstat`, `stat.S_ISREG`, `os.path.isfile`, same-process metadata,
  link-count, snapshot, or reread-revalidation protocol in the generated Adapter;
- retained 64 KiB frontmatter and 1 MiB complete-read limits;
- retained direct catalog enumeration, routing, authority order, recovery review,
  hot-refresh, exact `-C`, unchanged business CWD, and no alternate-root behavior;
- unchanged deterministic portable/check coverage for static unsafe topology;
- source, wheel, sdist, and installed Adapter byte parity.

Fresh behavioral evaluation will judge ordinary post-preflight bounded reads as
valid without auditing a metadata syscall before each open. It will still fail
preflight-order violations, unbounded or recursive hunting, wrong-root commands,
authority-order/rerouting errors, reconstructed recovery commands, premature hot
marking, unexpected writes, or evidence of concurrent repository change.

## Scope

This revision changes only the generated Adapter text, its renderer/contract
tests where required, the static-repository design documentation, and behavioral
acceptance criteria. It adds no CLI, filesystem reader, repository selector,
automatic installation, Wiki migration, or runtime-skill change.

This document supersedes the per-read metadata-check requirements in
`2026-08-19-external-adapter-static-repository-design.md`; all other boundaries
and acceptance criteria in that design remain in force.

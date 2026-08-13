> **Superseded (2026-08-12):** Current behavior is defined by the
> [Portable-Only Repository Design](2026-08-12-portable-only-design.md).

# CLI Runtime Context and Recovery Guidance Design

## Status

Approved in conversation on 2026-08-10. This design is a separate follow-up to
the restricted nested frontmatter work. It changes CLI diagnostics and recovery
guidance only; it does not change configuration precedence, transaction state
transitions, or portable write semantics.

## Problem

The CLI currently exposes three related usability gaps:

1. `obsidian-wiki info` reports installation paths and the personal global
   configuration even when the current directory resolves a portable
   repository. The output can therefore describe a different vault from the
   one that ordinary commands will use.
2. An explicit PATH or `@name` override inside a portable repository correctly
   wins by precedence, but the CLI does not explain that portable semantics
   were bypassed. Even an override that points at the same vault changes the
   resolution mode from `portable` to `explicit` or `named`.
3. Transaction failures expose the low-level error but require the operator to
   remember the status/action matrix. JSON callers receive human error text
   instead of a structured failure document.

The result is avoidable ambiguity about the active vault and unnecessary risk
when recovering retained portable transaction state.

## Goals

- Make the active runtime context observable without changing it.
- Warn once when an explicit vault target overrides a portable context found
  from the current directory.
- Preserve explicit PATH and `@name` precedence for the participating
  configuration-aware commands.
- Prevent explicit vault arguments from bypassing the portable prohibition on
  Personal-mode `sync` and `sync-setup` Git mutations.
- Make `info` useful both before setup and inside Personal or Portable modes.
- Return valid structured JSON for context warnings and transaction failures.
- Derive safe, status-aware, copyable transaction recovery guidance without
  executing recovery automatically.
- Remain platform-independent and dependency-free.

## Non-goals

- Do not change the Config Resolution Protocol or persist a selected runtime.
- Do not add an interactive recovery wizard or a `transaction recover`
  command.
- Do not auto-run `retry`, `restore`, `abort`, or `discard`.
- Do not change transaction state transitions, validation, snapshots, or Git
  boundaries.
- Do not convert every CLI command to a common response envelope.
- Do not reinterpret raw-path graph, batch, cache, migration, or setup commands
  as configuration-resolution commands.
- Do not add a YAML or shell-execution dependency.

## Architecture

Use two small, pure diagnostic components and thin CLI renderers.

### Runtime inspection

A runtime-inspection component returns one immutable `RuntimeInspection` value
containing:

- the active `ResolvedConfig`, when resolution succeeded;
- a runtime status: `resolved`, `unconfigured`, or `error`;
- the current working directory;
- the nearest lexically discovered portable config path, if one exists;
- zero or more immutable `ContextWarning` values;
- a resolution error when a present or explicitly selected config is invalid.

Each `ContextWarning` has stable fields:

- `code`;
- `message`;
- `hint`;
- `portable_config`;
- `selected_mode`;
- `selected_source`;
- `selected_vault`.

The four path/source fields are serialized as strings. `selected_mode`,
`selected_source`, and `selected_vault` describe a successfully resolved
explicit target; a failed explicit resolution does not claim that an override
became active and therefore does not emit this warning.

The warning code for this feature is
`portable-context-overridden`.

`resolve_config()` remains the source of truth for the active runtime. Runtime
inspection lexically discovers the nearest ancestor
`.obsidian-wiki/config.toml` separately so it can describe a shadowed portable
context. Discovery treats an ordinary entry, a symlink, and a dangling symlink
at that exact path as a candidate; it does not follow or trust the candidate to
generate a warning. When PATH or `@name` is explicit, inspection does not load
or validate that shadowed TOML solely for the warning. This preserves
precedence level 0: an invalid shadowed portable config cannot block an
explicit target. When no override is supplied, normal portable resolution
loads the TOML and continues to fail closed if it is invalid.

An explicit or named target produces one warning whenever a portable config
path was discovered from the current directory. This includes an explicit path
that resolves to the same vault, because the selected mode no longer carries
portable roots, sources, managed skills, local state, or write rules.

### Transaction recovery guidance

A separate pure component maps a trusted transaction record and the failed
operation to one immutable `RecoveryGuidance` value. It contains:

- transaction ID and trusted status, when available;
- the inspection command;
- one preferred `RecoveryAction`, when it is safe to recommend one;
- zero or more alternative `RecoveryAction` values;
- the prerequisite for every action.

Each action contains a copyable argv-equivalent command string, a reason, and a
list of requirements. The component only formats already supported transaction
commands. It never calls a transaction action, changes the worktree, or invokes
Git.

If a record cannot be read and validated, guidance must not infer a status or
recovery action. It may only recommend
`obsidian-wiki transaction list --json`.

## Runtime Warning Behavior

The following configuration-aware commands participate:

- `info`;
- `doctor`;
- `lint`;
- `trust-record`;
- `trust-check`;
- `query`;
- `context-pack`.

Human-readable commands emit at most one context warning to stderr after
resolution and before their ordinary output. Successful stdout remains the
command's existing output.

JSON object responses always include:

```json
{
  "context_warnings": []
}
```

When portable context is overridden, the array contains the structured warning
object. This field name deliberately does not reuse `warnings`: `doctor` and
`check` already use that name as a numeric health count. Context warnings are
informational; they do not change the command exit code and do not participate
in `--strict` health status.

Raw-path graph, batch, and cache commands remain raw-path interfaces and do not
emit this warning. Portable-only `check`, `repo`, `transaction`, and `hot`
commands do not accept a vault override and therefore cannot enter this state.
Personal setup remains an explicit installation workflow rather than a runtime
inspection command.

## Git Mutation Safety

`sync` and `sync-setup` are a stricter boundary. Before config resolution or any
filesystem, Git, or remote mutation, they lexically check for a nearest
portable config entry, including a symlink or dangling symlink. If found, they
reject the invocation even when `--vault` supplies a PATH that would otherwise
win resolution.

The error explains that portable repositories use an explicit branch and pull
request workflow. It does not offer a force flag. This closes the existing
documented bypass without changing Personal-mode behavior outside portable
repositories.

## `info` Contract

`info` accepts optional `--vault PATH|@name`, plus `--json` and `--pretty`.
The override previews one invocation only and never writes or switches a
default config.

### Human output

Human output has two sections:

```text
Runtime context
  status:       resolved
  mode:         portable
  source:       /repo/.obsidian-wiki/config.toml
  vault:        /repo/wiki
  repository:   /repo
  sources:      /repo/sources
  skills:       /repo/.skills
  local state:  /repo/.obsidian-wiki/local

CLI installation
  version:        ...
  bundled skills: ...
  skills root:    ...
  bootstrap:      ...
  global config:  ...
  global vault:   ...
  agent installs: ...
```

Absolute paths are runtime diagnostics only and are never written to portable
repository files.

### JSON output

JSON uses one document with three stable top-level sections:

```json
{
  "runtime": {
    "status": "resolved",
    "mode": "portable",
    "source": "/repo/.obsidian-wiki/config.toml",
    "vault": "/repo/wiki",
    "portable": {
      "root": "/repo",
      "sources": ["/repo/sources"],
      "skills": "/repo/.skills",
      "local_state": "/repo/.obsidian-wiki/local"
    }
  },
  "installation": {
    "version": "...",
    "skills": "...",
    "bootstrap": "...",
    "global_config": "...",
    "global_default": {},
    "agent_installs": []
  },
  "warnings": []
}
```

Installation and stale-version checks must first collect data and then render
it. Existing helpers that print human text cannot run while assembling JSON.

### Missing and invalid configuration

With no portable, environment, or global config, `info` remains available as
an installation diagnostic. It returns exit 0, reports
`runtime.status = "unconfigured"`, and includes a stable `runtime.guidance`
string directing the user to setup.

When a present or explicitly selected config is invalid, `info` does not fall
back. It returns exit 1 with `runtime.status = "error"`. JSON mode still emits
exactly one valid document containing the installation section. Human mode
prints the runtime error to stderr. A missing or invalid `@name` follows this
error path.

An explicit PATH may resolve even if the directory does not exist; `info`
describes resolution rather than replacing `doctor`'s health checks.

## Transaction Failure Contract

Transaction command handlers catch configuration, manifest, and transaction
errors before the global human-text exception handler. Human failures write
only to stderr. JSON failures write exactly one JSON document to stdout and no
duplicate human error.

Stable error codes are `config-error`, `manifest-error`, and
`transaction-error`. A failure without a trusted transaction record still
includes a `recovery` object, with null transaction fields, the list inspection
command, and no preferred or alternative mutation.

The JSON form is:

```json
{
  "status": "error",
  "error": {
    "code": "transaction-error",
    "message": "..."
  },
  "recovery": {
    "transaction_id": "tx-1",
    "transaction_status": "failed",
    "inspect_command": "obsidian-wiki transaction list --json",
    "preferred_action": {
      "command": "obsidian-wiki transaction retry tx-1",
      "reason": "retry after the original cause is removed",
      "requires": [
        "affected targets still match their recorded preimages"
      ]
    },
    "alternatives": []
  }
}
```

The human form prints, in order:

1. the original error;
2. the trusted transaction status, when known;
3. the inspection command;
4. the preferred command and its prerequisites;
5. allowed alternatives and their prerequisites.

The recovery matrix is:

| Status | Preferred guidance | Alternatives |
|---|---|---|
| `active` | Fix the cause, review the candidate, then `commit`. | `abort` to abandon staged work. |
| `promoting` | `restore`; it is the only supported recovery action. | None. |
| `failed` | Fix the cause and `retry` while preimages still match. | `restore`; `abort`; `discard` only after the outcome is understood. |
| `complete` | Review the Git diff and `discard` after accepting it. | `restore` only while affected files still match postimages. |
| `restored` | Verify the restored result, then `discard`. | A second `restore` remains a no-op but is not the preferred action. |
| Unknown/unreadable | Run `transaction list --json`. | No guessed mutation. |

When `begin` fails before it creates an ID, the CLI only recommends the list
command. Config resolution failures outside portable mode do not fabricate a
transaction record or state-specific action.

`transaction list --json` retains its top-level array for compatibility. Each
record gains `recommended_action` and `allowed_actions`. Human list output gains
a recommended-command column. These fields come from the same pure guidance
mapper as failure output. `recommended_action` is either one complete
`RecoveryAction` object or null; `allowed_actions` is an array of complete
`RecoveryAction` objects, including the recommended action when one exists.

## Error and Compatibility Rules

- One invocation emits a context warning at most once.
- Context warnings never change success or strict-mode exit codes.
- Existing JSON business fields retain their types and meanings.
- Transaction success payloads retain their current schema.
- Transaction failure JSON always remains parseable.
- Invalid portable config never silently falls back when it is authoritative.
- An explicit PATH or `@name` remains authoritative even when a shadowed
  portable config is invalid.
- Recovery guidance uses only validated transaction IDs and existing literal
  subcommands; it never evaluates or invokes a shell string.
- No operation in this feature writes resolved absolute paths into portable
  files.

## Testing Strategy

### Runtime inspection unit tests

- portable resolution without an override produces no warning;
- PATH and `@name` overrides produce one warning;
- same-vault explicit override still warns;
- override outside a portable CWD does not warn;
- invalid authoritative portable config fails closed;
- invalid shadowed portable config does not block an explicit override;
- unconfigured runtime is distinguished from invalid configuration.

### CLI output tests

- `info` human and JSON output for portable, env, global, named, explicit,
  unconfigured, and invalid modes;
- `info --vault` does not change any config file or active symlink;
- JSON output is a single document with no stale-check text contamination;
- each participating human command emits one stderr warning;
- each participating JSON command includes one structured warning;
- commands without an override expose an empty context warning array;
- existing numeric `warnings` fields remain numeric.

### Mutation-safety tests

- `sync --vault` and `sync-setup --vault` fail in a portable CWD before their
  filesystem, Git, or remote helpers are called;
- Personal-mode sync behavior outside portable repositories is unchanged;
- runtime inspection and recovery guidance perform no writes.

### Transaction guidance tests

- every trusted status maps to the approved preferred and alternative actions;
- unknown, missing, malformed, symlinked, or unreadable records produce only
  inspection guidance;
- failures before transaction ID creation produce only list guidance;
- human failures keep stdout empty and include prerequisites in stderr;
- JSON failures contain the stable error and recovery objects;
- `transaction list` retains its top-level JSON array and adds per-record
  actions;
- spies prove that generating guidance never calls a recovery action or Git.

### Regression tests

Run the existing config, doctor, query, context-pack, lint, trust, sync,
transaction, installation-policy, portable-check, and collaboration suites,
then the complete test suite and README translation parity check.

## Documentation

Update:

- `docs/cli.md` with the resolved `info` output, context-warning JSON field,
  sync bypass refusal, transaction failure envelope, and recovery guidance;
- `docs/configuration.md` with explicit-override warning semantics and the
  stricter portable Git-mutation boundary.

The README files do not change because this feature adds no new top-level
installation method or workflow. If implementation changes a landing-page
example after all, both README versions must be updated together.

## Acceptance Criteria

- `info` accurately distinguishes the active runtime from installation and
  global-default state in human and JSON modes.
- Explicit PATH and `@name` overrides inside a portable CWD emit exactly one
  warning, including same-vault overrides.
- Participating JSON commands contain structured context warnings without
  changing existing warning counters.
- `sync` and `sync-setup` cannot bypass portable Git safety with `--vault`.
- Transaction failures provide valid structured JSON or actionable human
  guidance based only on trusted state.
- No recovery action occurs without a separate explicit command.
- Existing Personal and Portable resolution precedence remains unchanged.
- Focused and full tests pass, documentation is current, README parity remains
  clean, and the working tree contains no uncommitted implementation changes.

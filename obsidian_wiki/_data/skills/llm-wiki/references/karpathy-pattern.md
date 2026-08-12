# Repository Compilation Pattern

The wiki is a compiled artifact: reviewed Markdown sources are transformed into
small, linked Markdown pages. Source material remains authoritative; the vault
is the readable knowledge graph derived from it.

## Source layer

There is exactly one configured source root. Every ordinary input is a tracked
file beneath that root and is named by a repository-relative Source ID. External
or live material becomes authoritative only after it is captured as a reviewed
Markdown snapshot in that source root.

## Compilation layer

An agent reads a bounded source closure, distils concepts, updates existing
pages instead of duplicating them, and writes final candidate paths. Pages carry
required frontmatter and connect related ideas with `[[wikilinks]]`.

All mutation uses one transaction: begin with Source IDs, write candidates,
declare deletions, validate, review, and commit. Recovery follows the structured
transaction record rather than guessing.

## Tracking layer

The repository uses manifest v2 with sharded entries and exactly one configured
source root. `transaction commit owns` the immutable operation record and shard
updates; in normative terms, transaction commit owns all manifest mutation.
Agents never edit manifest shards directly. Stable `index.md` and `log.md` are
not rewritten during ordinary compilation.

This separation keeps provenance reviewable: sources explain why knowledge
exists, transactions explain how it changed, and compiled pages explain what the
repository currently knows.

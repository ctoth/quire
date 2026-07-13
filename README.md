# Quire

Quire is a typed, schema-aware document store built directly on Git object
plumbing. It writes blobs, trees, commits, refs, and notes without requiring a
checkout or materializing documents in a working tree.

Applications define their own document types and semantics. Quire supplies the
generic storage machinery: typed families, placement policies, contract
manifests, atomic multi-document writes, foreign-key validation, and optional
derived SQL and vector stores.

## Why Quire

Git already provides content-addressed objects, immutable history, branches,
and atomic ref updates. Quire adds the typed application boundary that raw Git
lacks:

- **Typed documents.** Documents decode into strict `msgspec.Struct` values.
- **Explicit placement.** A family decides how a logical reference maps to a
  branch and path or to a dedicated blob ref.
- **Schema contracts.** Persisted family and registry shapes carry a
  `VersionId`; incompatible drift must be accompanied by a version change.
- **Safe publication.** Compare-and-swap writes reject a stale expected branch
  head before creating new objects.
- **Atomic family transactions.** Adds, moves, and deletes across families can
  publish as one commit and validate references against the staged result.
- **Lazy discovery.** APIs that scan refs, commits, trees, and families return
  iterators; callers choose when to materialize them.

Quire is useful when Git history and branch identity are part of the storage
model, but a checked-out filesystem is not.

## Choose the layer you need

| Need | Start with |
| --- | --- |
| Strict YAML or JSON document decoding | `quire.documents` |
| Typed documents stored in Git | `ArtifactFamily`, `DocumentFamilyStore` |
| Several related document families | `FamilyRegistry` and `registry.bind(...)` |
| One declaration for document, storage, and derived schema | `quire.charter_class` |
| Raw commits, refs, notes, history, or a selected-ref fetch | `GitStore` |
| Rebuildable content-addressed SQLite output | `quire.derived_store` |
| SQLAlchemy or FTS5 projections | install `quire[sql]` |
| sqlite-vec caches | install `quire[vector]` |

The [documentation index](docs/index.md) gives a guided path through these
layers. The [architecture guide](docs/architecture.md) explains their ownership
boundaries and invariants.

## Install

Quire requires Python 3.11 or newer.

```bash
uv add quire
```

Optional capabilities are installed explicitly:

```bash
uv add "quire[sql]"     # SQLAlchemy schemas, sessions, and FTS5
uv add "quire[vector]"  # SQL support plus sqlite-vec
```

The root `quire` package exports the core Git, document-family, contract,
projection, lifecycle, and generic derived-store APIs. SQL and vector APIs live
in their owning modules, such as `quire.sqlalchemy_schema`,
`quire.sqlalchemy_store`, and `quire.sqlite_vec_store`; importing core Quire does
not require those optional dependencies.

## Quick start

This example declares a family, stores two typed documents in one commit, and
reads them back. `GitStore.init_memory()` uses Dulwich's in-memory repository,
so it does not touch the filesystem.

```python
from dataclasses import dataclass

from quire import DocumentFamilyStore, GitStore, VersionId, registry_from_charters
from quire.charter_class import CharterDoc, charter


@charter(
    key="notes",
    name="notes",
    contract_version="2026.07.01",
    placement="notes",
    identity_field="note_id",
)
class Note(CharterDoc):
    note_id: str
    title: str
    body: str = ""


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


version = VersionId("2026.07.01")
registry = registry_from_charters(
    Note.__charter__,
    name="notebook",
    contract_version=version,
)
owner = Owner()
store = DocumentFamilyStore(owner=owner, backend=GitStore.init_memory())
bound = registry.bind(owner, store)

with bound.transact(message="seed notebook") as transaction:
    transaction.notes.save("welcome", Note("welcome", "Welcome"))
    transaction.notes.save("todo", Note("todo", "Next steps", "Write more."))

assert list(bound.notes.iter_refs()) == ["todo", "welcome"]
assert bound.notes.require("welcome").title == "Welcome"
```

The decorated class is the document type. Its attached `FamilyCharter` derives
the artifact family, document codec, schema metadata, and registry definition;
the application does not maintain those as parallel declarations.

The same example is available as a
[runnable file](docs/examples/quickstart.py).

For custom placement and codec policies, families can also be assembled
directly. See [Working with document families](docs/families.md).

## Core guarantees

### Object-store-first Git

`GitStore.init(path)` creates a repository, but ordinary reads and writes
operate on Git objects. A call such as `commit_files()` does not write the
corresponding files into the repository directory. `materialize()` and
`materialize_worktree()` are explicit side doors for callers that need files.

`GitStore` also provides lazy tree and history traversal, typed refs and notes,
merge-base and parent inspection, dry-run unreachable-object reporting, and a
selected-ref fetch primitive. It is deliberately not a remote registry, merge
policy, trust system, or Git porcelain.

### Transactions and concurrency

Writers accept an `expected_head` when a branch might advance between planning
and publication:

```python
head = store.backend.branch_sha("master")

# Compute or validate the proposed changes.

with bound.transact(message="apply plan", expected_head=head) as transaction:
    transaction.notes.save("welcome", Note("welcome", "Hello again"))
```

If another writer advances `master`, Quire raises `HeadMismatchError` before
writing a blob, tree, or commit. A multi-family transaction is pinned to one
target branch and rejects accidental cross-branch writes.

Filesystem-backed mutation paths are serialized within a process and guarded
by a repository lock across processes. The expected-head check still matters:
locking makes publication consistent, while compare-and-swap expresses which
state the caller intended to replace.

### Contracts

A registry can emit a deterministic manifest for its persisted ABI:

```python
from quire import check_contract_manifest

baseline = registry.contract_manifest(
    package_name="notebook",
    package_version="0.1.0",
)

# Build the manifest again after changing a family declaration.
updated = changed_registry.contract_manifest(
    package_name="notebook",
    package_version="0.1.0",
)

check_contract_manifest(baseline, updated)
```

Changing a contract body without changing its version raises
`ContractManifestError`. A caller may instead provide a documented
`CompatibilityMarker` for an intentionally compatible change. Quire forces the
compatibility decision; it does not define an application's migration policy.

### References and foreign keys

Families can declare their identity field, alternate reference keys, and
cross-family `ForeignKeySpec` values. Registry construction validates the
foreign-key graph. Bound writes validate references against one captured commit
and publish with that same commit as the compare-and-swap expectation.

A transaction validates its complete staged result, so it can add a target and
its dependent together. Deleting or replacing a target that would leave an
existing dependent dangling fails before publication. Only the touched portion
of the foreign-key graph is scanned.

### Explicit federation plumbing

`fetch_ref` transfers one caller-selected remote ref into one caller-selected
local ref. Publication requires an expected local value:

```python
from quire import GitStore, RefName

tracking = RefName("refs/remotes/peer/master")
fetched = store.backend.fetch_ref(
    "https://example.test/peer.git",
    RefName("refs/heads/master"),
    tracking,
    expected_local=store.backend.read_ref(tracking),
)
```

The caller owns transport locations, ref naming, authentication, trust, merge,
and schema policy. Quire only supplies generic object transfer and atomic ref
publication.

## What Quire does not own

- Application concepts, commands, workflows, and user-facing policy.
- A configured-remotes model, automatic synchronization, or merge resolution
  interface.
- A working-tree lifecycle.
- Application-specific undo rules. Quire may expose generic commit/tree revert
  mechanics; the application decides what may be undone.
- An authoritative query database. Derived SQL and vector stores are explicit,
  rebuildable projections of authored data.

## Documentation

- [Documentation index](docs/index.md)
- [Architecture and boundaries](docs/architecture.md)
- [Working with document families](docs/families.md)
- [Charters and derived schemas](docs/charters.md)
- [Changelog](CHANGELOG.md)

The public API is also reflected by `quire/__init__.py`. Optional capability
modules define their own `__all__` exports.

## Development

Install the locked development environment and run the ordinary gates with:

```bash
uv sync --all-extras --dev
uv run ruff check .
uv run pyright quire
uv run pytest
uv build
```

The ordinary test command excludes performance benchmarks. Run them explicitly:

```bash
uv run pytest -m benchmark --benchmark-only tests/test_benchmarks.py
```

## Status

Quire is currently `0.2.x` and alpha-quality. The package surface is deliberate,
but breaking changes are still possible. Persisted shape changes should be made
visible through contract-version changes rather than silent drift.

## License

MIT. See [LICENSE](LICENSE).

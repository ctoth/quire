# quire

A typed, schema-aware document store built on git plumbing.

Quire treats git as a content-addressable object store with branches, refs, and
notes — not as a version-control tool. There is no working tree, no checkout,
no file materialization. A commit is a transaction against the object store;
a document is a validated struct addressed by a pluggable placement policy;
a schema change is a contract version bump that the library refuses to let you
forget.

It is the generic storage substrate — semantic applications (claim stores,
concept graphs, knowledge bases) layer on top without depending on a
checked-out tree.

## Why it exists

Most document stores bolt history onto a database. Quire inverts that:
git is already a transactional, content-addressable, branch-native store —
so skip the database. What git lacks is a typed, schema-checked surface.
That is what quire adds.

- **Object-store-first.** Commit without materializing files. `init_memory()`
  gives you a fully functional in-RAM repo for tests.
- **Placements separate identity from storage.** A ref maps to
  `(branch, path)` through a pluggable codec — flat YAML per namespace,
  hash-scattered for large collections, fixed files, templated paths,
  nested or subdir layouts, singletons, or per-identity branches.
- **Contracts are first-class.** Every family declares a `VersionId`.
  `check_contract_manifest` refuses silent shape drift: bump the version,
  or file a `CompatibilityMarker` with a written reason. You cannot quietly
  break a persisted ABI.
- **Compare-and-swap writes.** Every writer accepts `expected_head`. If the
  branch tip moved between read and commit, you get a typed
  `HeadMismatchError` before any objects are written.
- **Batched transactions.** `transact()` coalesces adds, deletes, and moves
  into a single commit, with foreign-key validation against the
  post-transaction state.
- **Structural typing throughout.** `ArtifactFamily[TOwner, TRef, TDoc]`,
  `Protocol` backends, msgspec-validated decode.

## Non-goals

- Not a git porcelain. There are no configured remotes, push workflow, or merge
  resolution UI. Quire does expose low-level graph and transport plumbing such
  as parent inspection, native merge-base calculation, and fetching one
  caller-selected ref into one caller-selected local ref.
- Not a working-tree manager. `materialize_worktree()` exists for the cases
  that need it, but it is a side door, not the front door.
- Not a general-purpose ORM. Documents are `msgspec.Struct` values; identity,
  placement, and contract are explicit.

## Install

```
pip install quire
```

Requires Python 3.11+. Depends on `dulwich`, `msgspec`, and `pyyaml`.

## A small example

```python
import msgspec

from quire import (
    ArtifactFamily,
    BoundFamilyRegistry,
    DocumentFamilyStore,
    FamilyDefinition,
    FamilyRegistry,
    FlatYamlPlacement,
    GitStore,
    VersionId,
)


class Claim(msgspec.Struct):
    name: str
    strength: float = 0.0


V = VersionId("2026.05.01", allow_placeholder=False)

claims = ArtifactFamily(
    name="claims",
    contract_version=V,
    doc_type=Claim,
    placement=FlatYamlPlacement("claims", str),  # claims/<ref>.yaml
)

registry = FamilyRegistry(
    name="demo",
    contract_version=V,
    families=(FamilyDefinition(key="claims", name="claims", contract_version=V, artifact_family=claims),),
)


class Owner:
    branch = "master"


store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
bound = registry.bind(store.owner, store)

with bound.transact(message="seed") as txn:
    txn.claims.save("alpha", Claim(name="alpha", strength=0.8))
    txn.claims.save("beta",  Claim(name="beta"))

assert list(bound.claims.iter()) == ["alpha", "beta"]
assert bound.claims.require("alpha").strength == 0.8
```

No filesystem was touched. The repo lives in process memory; the
transaction produces a single commit with both records.

## Concept map

| Layer | Responsibility |
| --- | --- |
| `GitStore` + `GitStorePolicy` | Raw object store ops: `commit_files`, `commit_flat_tree`, refs, notes, branches, native merge-base, and CAS-published single-ref fetch. Backed by `dulwich` (`Repo` or `MemoryRepo`). Policy controls ignored paths and other generic knobs. |
| `GitGcReport` | Dry-run gc reporting unreachable objects. |
| `RefName`, `NotesRef`, `VersionId` | Validated newtypes — placeholder refs and empty versions are rejected at construction. |
| `TreePath`, `GitTreePath`, `FilesystemTreePath`, `coerce_tree_path` | Typed path values that distinguish object-store paths from filesystem paths and agree with tree walking. |
| `ArtifactFamily` | A typed document family: `doc_type`, placement, optional codec/render/normalize/validate hooks. |
| Placements | `FlatYamlPlacement`, `HashScatteredYamlPlacement`, `NestedFlatYamlPlacement`, `FixedFilePlacement`, `SubdirFixedFilePlacement`, `TemplateFilePlacement`, `SingletonFilePlacement` — all pluggable. |
| `BranchPlacement` | `owner` / `primary` / `current` / `fixed` / `template` — where the artifact is written. Templates can derive a branch name from the ref itself. |
| Ref codecs | `encode_ref_value` plus `single_field_ref_type` / `singleton_ref_type` helpers; reversible `stem`, `base64url`, and `uri` codecs for ref-to-filename mapping. |
| `FamilyIdentityPolicy` | Per-family hooks for artifact id, version id, canonical payload, logical id fields, source-local fields. |
| `FamilyRegistry` → `BoundFamilyRegistry` → `BoundFamily` | Grouped families with duplicate-key/name/accessor checks; `bound.<accessor>.save(...)` attribute access. |
| `DocumentFamilyStore` + `BoundFamilyTransaction` + `TransactionalBoundFamily` | Load/save/move/delete, prepare-then-commit, batched transactions with per-family attribute access inside the transaction. |
| `HeadMismatchError` | Typed compare-and-swap failure raised before any object writes happen. |
| `ContractManifest` + `check_contract_manifest` | Persisted ABI. Body drift without a version bump or compatibility marker raises. |
| `ReferenceKey`, `FamilyReferenceIndex`, `CrossFamilyReferenceIndex`, `ReferenceResolution`, `ForeignKeySpec` | Declarative family references, alias indexing with match provenance, and mandatory cross-family FK validation. |
| `canonical_json_bytes`, `canonical_json_sha256` | Deterministic payload canonicalization for hashing and contract bodies. |

## Transactions and concurrency

Writers and transactions accept `expected_head`:

```python
head = store.backend.branch_sha("master")
# ... think about it, plan changes ...
with bound.transact(message="apply plan", expected_head=head) as txn:
    txn.claims.save("alpha", Claim(name="alpha", strength=0.9))
```

If another writer advanced `master` in between, `HeadMismatchError` fires
before any tree, blob, or commit object is written — no orphaned objects,
no partial state. Multi-artifact transactions stay pinned to a single
target branch and refuse cross-branch writes.

`GitStore` serializes filesystem-backed mutations and uses compare-and-swap
ref updates under the hood, so concurrent writers from separate processes
still observe a consistent ref history.

For explicit federation plumbing, `fetch_ref` accepts a transport location and
typed remote/local refs. It fetches only the selected ref's reachable objects,
verifies that the target is a commit, and publishes the local ref only if its
current value matches the mandatory expectation:

```python
from quire import GitStore, RefName

tracking = RefName("refs/remotes/peer/master")
fetched = store.fetch_ref(
    "https://example.test/peer.git",
    RefName("refs/heads/master"),
    tracking,
    expected_local=store.read_ref(tracking),
)
```

The caller owns transport location, ref naming, and policy. Quire does not keep
a remote registry or infer merge, trust, or schema semantics.

## Registry queries

`FamilyRegistry` has generic lookup helpers for storage applications that need
to select families without hard-coding their catalog. Metadata stays
application-owned, but Quire provides the mechanics:

```python
semantic = registry.select_by_metadata("semantic", True)
rules = registry.by_metadata("root", "rules")
ordered = registry.select(lambda family: family.metadata_value("rank", 100) < 50)
```

Placement-backed roots are also queryable without inspecting
`placement.contract_body()`:

```python
assert registry.by_storage_root("claims").name == "claims"
assert registry.family_for_path("claims/example.yaml").name == "claims"
assert registry.by_name("claims").storage_root() == "claims"
```

Query-only views can pass `validate_foreign_keys=False` when they intentionally
contain only a subset of a larger registry. Duplicate keys, names, and
accessors are still rejected.

## References and foreign keys

Families can declare the artifact identity field and any additional reference
keys that should resolve to that identity. Quire builds `FamilyReferenceIndex`
values from loaded family records and raises typed errors for missing or
ambiguous references. Each resolution carries `match provenance` so callers
can tell whether a hit came from the primary identity field or an alias.

```python
from quire import ForeignKeySpec, ReferenceKey

concepts = FamilyDefinition(
    key="concepts",
    name="concepts",
    contract_version=V,
    artifact_family=concept_family,
    identity_field="artifact_id",
    reference_keys=(
        ReferenceKey.field("artifact_id"),
        ReferenceKey.field("aliases[]"),
    ),
)

claims = FamilyDefinition(
    key="claims",
    name="claims",
    contract_version=V,
    artifact_family=claim_family,
    identity_field="artifact_id",
    foreign_keys=(
        ForeignKeySpec(
            name="claim_concept",
            contract_version=V,
            source_family="claims",
            source_field="concept",
            target_family="concepts",
        ),
    ),
)
```

Bound family writes and transactions validate declared foreign keys before
committing. Validation uses the post-transaction state, so a transaction can add
a target record and a dependent record together. Deleting or replacing a target
that would leave existing dependents dangling fails before the commit is
written. FK validation is scoped to the foreign-key graph touched by the
transaction, so unrelated families pay no scan cost.

## Contracts, briefly

```python
baseline = registry.contract_manifest(package_name="demo", package_version="0.2.0")
# ... later, after changing a placement namespace ...
updated  = changed_registry.contract_manifest(package_name="demo", package_version="0.2.0")

check_contract_manifest(baseline, updated)
# ContractManifestError: Contract body changed without version bump or
# compatibility marker: family:claims
```

Either raise the family's `contract_version`, or add a `CompatibilityMarker`
explaining why the shape change is backwards-compatible. The library forces
the question.

## Status

`0.2.x`. The package surface is small and deliberate; breaking changes in this
phase are announced via contract-version bumps rather than silent shape drift.
See `quire/__init__.py` for the exported surface.

## License

MIT. See [LICENSE](LICENSE).

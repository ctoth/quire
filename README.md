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
  hash-scattered for large collections, fixed files, templated paths, or
  per-identity branches.
- **Contracts are first-class.** Every family declares a `VersionId`.
  `check_contract_manifest` refuses silent shape drift: bump the version,
  or file a `CompatibilityMarker` with a written reason. You cannot quietly
  break a persisted ABI.
- **Batched transactions.** `store.transact()` coalesces adds and deletes
  into a single commit.
- **Structural typing throughout.** `ArtifactFamily[TOwner, TRef, TDoc]`,
  `Protocol` backends, msgspec-validated decode.

## Non-goals

- Not a git porcelain. No push/pull, no remotes, no merge resolution UI.
  Quire does expose low-level graph plumbing such as parent inspection and
  merge-base calculation so downstream semantic merge code can build on the
  object store without shelling out to git.
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

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.families import FamilyDefinition, FamilyRegistry
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.versions import VersionId


class Claim(msgspec.Struct):
    name: str
    strength: float = 0.0


V = VersionId("2026.04.18", allow_placeholder=False)

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

bound.claims.save("alpha", Claim(name="alpha", strength=0.8), message="seed alpha")
bound.claims.save("beta",  Claim(name="beta"),                message="seed beta")

assert list(bound.claims.iter()) == ["alpha", "beta"]
assert bound.claims.require("alpha").strength == 0.8
```

No filesystem was touched. The repo lives in process memory; each `save` is a
real commit against the object store with a real tree and a real SHA.

## Concept map

| Layer | Responsibility |
| --- | --- |
| `GitStore` | Raw object store ops: `commit_files`, `commit_flat_tree`, refs, notes, branches, merge-base. Backed by `dulwich` (`Repo` or `MemoryRepo`). |
| `RefName`, `NotesRef`, `VersionId` | Validated newtypes — placeholder refs and empty versions are rejected at construction. |
| `ArtifactFamily` | A typed document family: `doc_type`, placement, optional codec/render/validate hooks. |
| Placements | `FlatYamlPlacement`, `HashScatteredYamlPlacement`, `FixedFilePlacement`, `TemplateFilePlacement`, `SingletonFilePlacement` — all pluggable. |
| `BranchPlacement` | `owner` / `primary` / `current` / `fixed` / `template` — where the artifact is written. Templates can derive a branch name from the ref itself. |
| `FamilyRegistry` → `BoundFamilyRegistry` → `BoundFamily` | Grouped families with duplicate-key/name/accessor checks; `bound.<accessor>.save(...)` attribute access. |
| `DocumentFamilyStore` + `DocumentFamilyTransaction` | Load/save/move/delete, prepare-then-commit, batched transactions. |
| `ContractManifest` + `check_contract_manifest` | Persisted ABI. Body drift without a version bump or compatibility marker raises. |
| `ReferenceKey`, `FamilyReferenceIndex`, `ForeignKeySpec` | Declarative family references and mandatory cross-family FK validation. |

## References and foreign keys

Families can declare the artifact identity field and any additional reference
keys that should resolve to that identity. Quire builds `FamilyReferenceIndex`
values from loaded family records and raises typed errors for missing or
ambiguous references.

```python
from quire.references import ForeignKeySpec, ReferenceKey

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
written.

## Contracts, briefly

```python
baseline = registry.contract_manifest(package_name="demo", package_version="0.1.0")
# ... later, after changing a placement namespace ...
updated  = changed_registry.contract_manifest(package_name="demo", package_version="0.1.0")

check_contract_manifest(baseline, updated)
# ContractManifestError: Contract body changed without version bump or
# compatibility marker: family:claims
```

Either raise the family's `contract_version`, or add a `CompatibilityMarker`
explaining why the shape change is backwards-compatible. The library forces
the question.

## Status

`0.1.x`. The package surface is small and deliberate; breaking changes in this
phase are expected to be announced via contract-version bumps rather than
silent shape drift. See `quire/__init__.py` for the exported surface.

## License

MIT. See [LICENSE](LICENSE).

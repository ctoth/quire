# Working with document families

A document family connects four things that should remain explicit: a typed
document, a logical reference type, a placement policy, and a contract version.
Registries group families when writes or references cross family boundaries.

For new statically declared families, `@charter` is usually the shortest
authoring form. Direct `ArtifactFamily` construction is appropriate when a
consumer needs a custom codec, placement object, normalization hook, or a family
assembled at runtime.

## The minimal pieces

```python
from dataclasses import dataclass

import msgspec

from quire import (
    ArtifactFamily,
    DocumentFamilyStore,
    FamilyDefinition,
    FamilyRegistry,
    FlatYamlPlacement,
    GitStore,
    VersionId,
)


class Page(msgspec.Struct, forbid_unknown_fields=True):
    page_id: str
    title: str
    text: str = ""


@dataclass(frozen=True)
class Workspace:
    branch: str = "master"


version = VersionId("2026.07.01")
pages = ArtifactFamily(
    name="pages",
    contract_version=version,
    doc_type=Page,
    placement=FlatYamlPlacement("pages", str),
)
registry = FamilyRegistry(
    name="library",
    contract_version=version,
    families=(
        FamilyDefinition(
            key="pages",
            name="pages",
            contract_version=version,
            artifact_family=pages,
            identity_field="page_id",
        ),
    ),
)

workspace = Workspace()
store = DocumentFamilyStore(workspace, GitStore.init_memory())
bound = registry.bind(workspace, store)
```

`FlatYamlPlacement("pages", str)` maps the logical ref `"intro"` to
`pages/intro.yaml` on the owner's branch. The Git tree path is an implementation
of the placement contract, not the document's identity.

## Point reads and lazy iteration

Bound families distinguish optional and required reads:

```python
missing = bound.pages.load("missing")
assert missing is None

bound.pages.save(
    "intro",
    Page(page_id="intro", title="Introduction"),
    message="add introduction",
)

page = bound.pages.require("intro")
refs = tuple(bound.pages.iter_refs())
```

`load()` returns `None` for an absent artifact. `require()` raises for absence.
`iter_refs()` is lazy; `tuple(...)` above is the caller's explicit decision to
materialize it.

Reads may be pinned to a branch or commit. Use a commit when several reads must
describe one immutable repository state rather than whatever the branch points
to at each call.

## Atomic transactions

Use a registry transaction whenever several path-backed changes form one
logical storage operation:

```python
with bound.transact(message="reorganize pages") as transaction:
    transaction.pages.save(
        "guide",
        Page(page_id="guide", title="Guide"),
    )
    transaction.pages.delete("obsolete")
```

Preparation and validation happen before publication. The transaction produces
one commit, not one commit per method call.

For optimistic concurrency, capture the head before planning:

```python
expected = store.backend.branch_sha(workspace.branch)

with bound.transact(
    message="apply reviewed edit",
    expected_head=expected,
) as transaction:
    transaction.pages.save(
        "intro",
        Page(page_id="intro", title="Start here"),
    )
```

If the branch moved, publication raises `HeadMismatchError`. Do not catch that
error and retry the same prepared write blindly: reread the new state and repeat
the application's planning or merge decision.

## Placement policies

Quire's built-in path placements cover common layouts:

- `FlatYamlPlacement`: `<root>/<encoded-ref>.yaml`.
- `HashScatteredYamlPlacement`: spreads encoded refs across hashed directories.
- `NestedFlatYamlPlacement`: a nested reference maps to nested directories.
- `FixedFilePlacement` and `SubdirFixedFilePlacement`: fixed filenames.
- `TemplateFilePlacement`: a declared path template.
- `SingletonFilePlacement`: one path-backed artifact.
- `BlobRefPlacement`: one branchless blob stored behind a dedicated Git ref.

`BranchPlacement` controls whether a path-backed artifact uses the owner,
primary, current, fixed, or templated branch.

Placements expose their ability to enumerate honestly. A placement that cannot
recover logical refs from storage raises `UnscannablePlacementError`; a layout
that requires an external index raises `IndexRequiredError`. Quire does not hide
a repository scan behind a point-operation name.

## Reference encoding

Filename encoding is part of the storage contract. Use a reversible codec when
arbitrary logical references must survive a filename round trip. Built-in
strategies include stem-safe, base64url, and URI forms.

For structured reference values, `single_field_ref_type` creates a typed
single-field ref, and `singleton_ref_type` creates a typed singleton ref. Avoid
duplicating ref-to-path logic in a consumer map; put storage identity in the
family's placement declaration.

## Cross-family references

A `FamilyDefinition` may declare:

- `identity_field`: the document field containing the primary identity;
- `reference_keys`: primary or alias paths accepted during resolution; and
- `foreign_keys`: paths in this family that must resolve against another
  family.

```python
from quire import ForeignKeySpec, ReferenceKey

concepts = FamilyDefinition(
    key="concepts",
    name="concepts",
    contract_version=version,
    artifact_family=concept_artifacts,
    identity_field="concept_id",
    reference_keys=(
        ReferenceKey.field("concept_id"),
        ReferenceKey.field("aliases[]"),
    ),
)

pages = FamilyDefinition(
    key="pages",
    name="pages",
    contract_version=version,
    artifact_family=page_artifacts,
    identity_field="page_id",
    foreign_keys=(
        ForeignKeySpec(
            name="page_subject",
            contract_version=version,
            source_family="pages",
            source_field="subject_id",
            target_family="concepts",
        ),
    ),
)
```

Registry construction rejects unknown target families and incorrectly owned
foreign keys. Resolution reports whether it matched primary identity or an
alias and raises typed missing or ambiguity errors instead of guessing.

Bound writes validate the affected foreign-key graph. Transactions validate
against the staged post-transaction state, which permits adding a referenced
record and its dependent in the same commit.

## Custom document codecs

The default family codec converts strict `msgspec.Struct` documents to and from
YAML. `DocumentCodec` groups five related operations: convert, decode, encode,
render, and conversion to a canonical payload.

Attach a custom codec to the `ArtifactFamily` when one family has a distinct
wire format. Attach it to `DocumentFamilyStore` only when it is truly the
default for every family using that store. Keeping the codec on the narrowest
owner prevents a multi-family registry from silently decoding one family with
another family's format.

Strict standalone helpers are available from `quire.documents`:

```python
from quire.documents import DocumentStruct, decode_json_document_bytes


class Metadata(DocumentStruct):
    title: str
    revision: int


metadata = decode_json_document_bytes(
    b'{"title":"Guide","revision":2}',
    Metadata,
    source="metadata.json",
)
```

`decode_json_document_bytes` accepts JSON, not YAML syntax that happens to be
representable as the same data. Unknown fields fail because `DocumentStruct`
uses strict decoding.

## Contract manifests

After assembling a registry, persist or compare its contract manifest at the
application's release boundary:

```python
manifest = registry.contract_manifest(
    package_name="library",
    package_version="1.0.0",
)
```

The manifest captures generic persisted shape. A changed body at the same
version is an error unless the caller supplies an explicit compatibility
marker. `VersionId` itself is an opaque identifier; any declaration-time format
policy belongs to `contract_version`, not to the value object.

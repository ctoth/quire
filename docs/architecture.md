# Architecture and boundaries

Quire is a generic typed Git/document substrate. It owns storage mechanics and
storage-format contracts; a consuming application owns domain meaning and
workflow policy.

This boundary is the main architectural constraint. A feature belongs in Quire
when it can be expressed in terms of documents, families, paths, Git objects,
refs, commits, schema declarations, or generic derived-store mechanics without
knowing an application's vocabulary.

## Layer responsibilities

### Git object layer

`GitStore` owns raw repository mechanics:

- creating or opening Dulwich repositories;
- reading and writing blobs, trees, commits, refs, and notes;
- lazy traversal of histories and trees;
- atomic ref publication with expected-value checks;
- merge-base and parent inspection;
- selected-ref object transfer; and
- explicit filesystem materialization.

This layer does not know about document types or application schemas. A commit
message is opaque text, a path is an object-tree path, and a ref is a validated
name. The transport primitive does not imply a remote registry, synchronization
policy, or trust model.

### Document and artifact layer

`quire.documents` owns strict decoding and reusable codecs. `ArtifactFamily`
adds typed identity and placement:

- the document type;
- a contract version;
- a policy that maps owner plus logical reference to an artifact address;
- optional conversion, encoding, rendering, normalization, and validation
  hooks; and
- optional identity and canonical-payload hooks.

Placement policies keep logical identity independent of physical storage.
Path-backed placements locate documents in a branch tree. `BlobRefPlacement`
stores a singleton document directly behind a Git ref. A placement may be
scannable or point-only; a point lookup must not conceal an unbounded scan.

### Family and transaction layer

`FamilyDefinition` adds relationships that only make sense in a catalog:
identity fields, alternate reference keys, foreign keys, metadata, and an
attribute-safe accessor name. `FamilyRegistry` validates the catalog before it
is bound to storage.

`DocumentFamilyStore` performs typed reads and writes. Binding a registry
produces `BoundFamilyRegistry`, whose family accessors share one owner and one
store. Its transaction surface stages changes, validates the staged result, and
publishes all path-backed changes in one commit.

Registry selection and enumeration are lazy by default. Materialization belongs
at the call site, where its cost is visible.

### Charter and schema layer

A `FamilyCharter` is the bridge between authored documents and derived schema
descriptions. It can provide:

- the family definition and codec;
- typed document fields and storage fields;
- lifecycle states and transitions;
- indexes, relationships, FTS indexes, and vector cache declarations;
- projection metadata; and
- a deterministic `SchemaObject`.

`@charter` derives that structure from one `CharterDoc` class. The imperative
`FamilyCharter` form remains useful when declarations must be assembled
programmatically.

The schema layer describes generic structure. The application still decides
which domain families exist and what their fields mean.

### Derived-store layer

Derived stores are query or cache artifacts built from authored inputs. Core
Quire owns generic mechanics such as:

- deterministic content hashes;
- dependency and projection-step ordering;
- temporary-build and atomic-publication behavior;
- reuse of matching materializations;
- garbage-collection reports; and
- safe writable and read-only SQLite connection policy.

The `sql` capability derives SQLAlchemy tables and models from a charter catalog
and supplies SQL/FTS sessions. The `vector` capability adds sqlite-vec cache
management.

Derived stores are not authoritative authored data. Quire does not decide when
an application rebuilds them, how it presents queries, or what a failed
projection means to a user.

## Write path invariants

A safe branch-backed write follows this sequence:

1. Capture the target branch head.
2. Resolve placements and prepare encoded document bytes.
3. Apply staged changes to the captured tree in memory.
4. Validate affected foreign keys against the staged post-transaction state.
5. Recheck the expected branch head.
6. Create objects and atomically publish the new ref value.

The expected-head check must happen before new objects are written. A mismatch
raises `HeadMismatchError` and leaves neither a partial commit nor newly orphaned
objects from the rejected write.

A multi-artifact transaction has one target branch. If placement resolution
would spread its path-backed writes across branches, the transaction fails
instead of silently becoming several commits.

Ref-backed documents use the corresponding expected-ref compare-and-swap rule.
They do not manufacture a branch or tree solely to look like path-backed
documents.

## Read path invariants

Reads that participate in one validation or transaction plan use one captured
commit. They do not resolve the branch head independently for each document,
because that could construct a view that never existed at a single Git state.

Tree entry classification agrees with typed tree walking. Quire loads an object
before classifying it; it does not infer that every raw tree tuple represents a
document file. In particular, submodule commit entries are not document
artifacts.

Potentially unbounded surfaces are iterator-first. APIs use names such as
`iter_refs`, `iter_log`, and `iter_subtree_files`; a caller that needs a list
must request one explicitly.

## Contract boundary

Contracts describe persisted storage shape, not application behavior.
Deterministic contract bodies cover the generic pieces that affect storage,
including placement and declared family relationships. A contract version is
an opaque identifier, not a date parser.

`check_contract_manifest` rejects body drift at an unchanged version unless an
explicit compatibility marker documents why the change is compatible. Quire
does not infer migrations, command compatibility, or undo policy from that
marker.

## Ownership test for new features

Before adding a feature to Quire, ask:

1. Can its inputs and outputs be named entirely with generic storage concepts?
2. Would a second unrelated document application reasonably reuse it?
3. Does it preserve iterator-first enumeration and explicit scan costs?
4. Does it retain expected-head or expected-ref safety at publication?
5. Is storage identity declared by the family rather than held in an ad hoc
   consumer map?

If the feature needs a domain command name, semantic object type, promotion
workflow, user-facing undo rule, or trust decision, it belongs in the consuming
application. Quire should expose only the generic primitive the application
needs.

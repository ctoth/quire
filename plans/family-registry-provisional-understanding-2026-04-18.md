# Family Registry Provisional Understanding

Date: 2026-04-18

This note records current working understanding. It is provisional by design:
revise it when Quire and downstream repositories expose a better shape.

## Core Model

- A family is conceptually a Git-backed, typed table.
- A family ref is the table primary key.
- A family document is the row payload. Some downstream schemas may temporarily
  store collection documents, but the family abstraction should still treat each
  ref/document pair as one logical row.
- A placement is the physical storage/index policy for that table: flat YAML,
  hash-scattered YAML, fixed files, singleton files, refs, notes, or future
  indexed object layouts.
- A family contract version describes the declared family surface: document
  type, ref type, placement, codecs, validation hooks, foreign keys, and other
  table-level behavior.
- The schema belongs to the family declaration. A large document class may live
  in a helper module for readability, but there must not be a second public
  schema registry that restates the family schema beside the family definition.

## Registry Shape

- Family keys should be enum-like: a closed set for a registry version, stable
  symbolic keys in code, and serialized strings only at boundaries.
- Quire should support user-supplied enum or string-enum keys rather than owning
  downstream project names.
- A registry should be the single declared source for family keys, family
  definitions, placement policies, contract versions, and registry-level
  contract metadata.
- The registry should expose schema through the family definition. Downstream
  code should ask the family for its document type, ref type, placement, codec,
  foreign keys, and semantic metadata instead of importing a parallel
  `SemanticFamilyDefinition` or hand-maintained schema table.
- A bound registry should attach that declaration to an owner plus a
  `DocumentFamilyStore`.

Target shape:

```python
repo.families.claims.list()
repo.families.claims.require(ref)
repo.families.claims.save(ref, document, message="...")

repo.families.by_key(PropstoreFamily.CLAIMS).list(commit=sha)
repo.families.by_name("claims").require(ref)
```

`by_name` and other string lookup APIs are boundary APIs for CLI, import,
serialized metadata, and diagnostics. Internal code should prefer enum-like
keys or bound attributes.

## Ownership

- Quire owns the generic machinery: family definitions, registries, bound
  registries, bound family operations, contract manifests, version checks, and
  foreign-key metadata primitives.
- Downstream projects own their family declarations and domain metadata.
- Downstream projects should not build local wrapper layers for listing,
  loading, saving, path resolution, or ref recovery. Those operations belong to
  Quire's store and bound family objects.

## Sidecar And Indexes

- SQLite sidecars and similar databases are derived indexes over family data.
- Semantic input should flow from Git artifact bytes into typed documents and
  then into index rows.
- Index builders should not require materializing semantic working-tree
  directories just to enumerate family data.
- SQLite may still write an output database to a file. That does not require
  semantic inputs to be filesystem paths.

## Open Design Questions

- Whether bound family attributes should use singular or plural names. The
  current downstream preference is plural for multi-row families, such as
  `repo.families.claims` and `repo.families.concepts`.
- Whether Quire should generate typed accessors from enum members or provide a
  small dynamic attribute surface.
- How hash-scattered and opaque placements expose listing when path recovery is
  not possible without an index.
- How mandatory version changes are enforced when code or contract surfaces
  change.
- How Quire should expose transactions at the bound-registry level so downstream
  code does not need to drop back to the raw artifact store for multi-family
  commits.

## Execution Workstream

This is the active workstream shape. It intentionally targets the final
interface directly: change the interface, update every caller, delete the old
path. No compatibility bridge is part of this plan.

### Phase 1: Finish Downstream Deletions

- In propstore, keep deleting repository-native semantic directory loaders and
  root variables first.
- Keep only explicit loose-file IO boundaries, such as command-line validation
  of an arbitrary user-provided file or directory.
- Sidecar builds must accept the repository/family surface, not a semantic tree
  root. SQLite can write to a file, but semantic input rows come from family
  documents.
- Every production caller that needs repository semantic data must go through
  the family store until Quire exposes the bound registry.

### Phase 2: Add Quire Family Registry

- Add a generic `FamilyRegistry` to Quire. It owns the ordered set of declared
  family definitions, registry contract metadata, and lookup by enum-like key
  and serialized family name.
- Add a `BoundFamilyRegistry` that binds a `FamilyRegistry` to an owner object
  and a `DocumentFamilyStore`.
- Add a `BoundFamily` object with the same primitive operations as the current
  family store: `list`, `load`, `require`, `handle`, `prepare`, `save`,
  `delete`, and `move`.
- Provide boundary lookup through `by_name(...)`; internal code should prefer
  enum-like keys or generated/dynamic attributes.
- Do not make Quire own downstream family names. Quire owns the registry
  machinery; downstream repositories declare the families.

### Phase 3: Make Versioning First-Class

- Store the registry contract version and each family contract version in one
  manifest generated from the registry.
- Treat missing versions as invalid declarations.
- Treat duplicate serialized family names as invalid declarations.
- Treat duplicate enum-like keys as invalid declarations.
- Add tests proving that version-bearing manifests change when declared family
  surfaces change. Do not rely on a self-referential content hash.

### Phase 4: Make Placement A Policy Object

- Keep path-like placement as only one placement implementation.
- Add room for hash-scattered placement where a ref maps to a shard path, and
  listing can use an index/ref listing instead of deriving identity from a
  materialized working tree.
- Keep fixed files, singleton files, refs, and notes conceptually at the same
  level as flat YAML placement.
- Make it clear in APIs that an artifact address is a handle, not necessarily a
  filesystem path.

### Phase 5: Move Propstore To `repo.families`

- Propstore declares an enum-like family key set and one registry declaration.
- `Repository` exposes a bound Quire registry, conceptually
  `repo.families.claims`.
- Production propstore code stops importing individual family constants for
  ordinary family operations where the bound family attribute is available.
- Serialized strings such as `claims`, path fragments such as `.yaml`, and
  fixed source filenames live in family/placement declarations only.
- Propstore schema classes travel with the family declarations at the public
  API level: there is one registry entry for `claims`, and that entry owns the
  claim document type, ref type, placement, import metadata, foreign keys, and
  contract version. Helper modules may contain large msgspec structs, but they
  are implementation detail, not a second schema surface.

Current execution checkpoint:

- Quire has `FamilyRegistry`, `BoundFamilyRegistry`, and `BoundFamily`, exported
  from the package and covered by tests.
- Quire has hash-scattered placement as a first path toward non-materialized
  large family storage.
- Propstore depends on the pushed Quire commit that contains that API.
- Propstore exposes `repo.families` from its `Repository`.
- Sidecar build, compilation context loading, concept ID allocation, grounding
  loading/inspection, artifact-code verification, and context workflows have
  been cut over to bound families.
- Production `repo.artifacts.<operation>(FAMILY, ...)` calls have been removed.
  Remaining cleanup is now about deleting duplicate registry/schema surfaces,
  broad artifact barrels, and helper wrappers that keep family constants in
  ordinary callers.
- Additional kept reductions have cut over form workflows, compiler historical
  loading, micropub workflows, worldlines, merge inputs, source authoring,
  source promotion path resolution, claim CLI reads, merge CLI reads, project
  initialization, proposal writes, repository import writes, concept CLI reads,
  and merge manifest writes.
- Propstore's separate `SemanticFamilyDefinition` / `SemanticFamilyRegistry`
  module has been deleted. Semantic family metadata now lives on the Quire
  `FamilyDefinition` entries in `PROPSTORE_FAMILY_REGISTRY`, and the checked-in
  contract manifest uses `family-registry:propstore` / `family:*` entries
  instead of `semantic_family:*` entries.
- Propstore's public raw `repo.artifacts` construction path has been deleted.
  `Repository` now keeps the Quire `DocumentFamilyStore` private and exposes
  the bound registry as the public repository family surface.
- `propstore.artifacts.policy` has been deleted; tests now use bound families
  directly and enforce the absence of raw artifact-store factories.

Remaining production buckets:

- Delete broad `propstore.artifacts` barrel exports for family constants; callers
  should use `repo.families` or the single registry declaration.
- Move source artifact-code verification, identity normalization, and reference
  indexes out of the artifact package when they are domain behavior rather than
  family declaration behavior.
- Decide the final module shape for large msgspec structs: colocated with
  family declaration when small, or imported as private helper schema modules
  when large. In both cases the family registry is the only public schema
  surface.

### Phase 6: Remove Propstore-Local Registry Machinery

- Delete propstore-local family registry behavior that has moved to Quire.
- Keep only propstore-owned domain declarations, document types, ref types, and
  semantic metadata.
- Run propstore's full logged suite and Quire's full `uv run pytest` suite.

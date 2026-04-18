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

## Registry Shape

- Family keys should be enum-like: a closed set for a registry version, stable
  symbolic keys in code, and serialized strings only at boundaries.
- Quire should support user-supplied enum or string-enum keys rather than owning
  downstream project names.
- A registry should be the single declared source for family keys, family
  definitions, placement policies, contract versions, and registry-level
  contract metadata.
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

Current execution checkpoint:

- Quire has `FamilyRegistry`, `BoundFamilyRegistry`, and `BoundFamily`, exported
  from the package and covered by tests.
- Propstore depends on the pushed Quire commit that contains that API.
- Propstore exposes `repo.families` from its `Repository`.
- Sidecar build, compilation context loading, concept ID allocation, grounding
  loading/inspection, artifact-code verification, and context workflows have
  been cut over to bound families.
- The remaining deletion-first loop is mechanical but important: for each
  production module still calling `repo.artifacts.<operation>(FAMILY, ...)`,
  replace it with the owning bound family, delete the now-unused family imports,
  run the narrow logged tests, and commit the kept reduction.

Remaining production buckets:

- Form workflows and form utilities.
- Compiler workflow loading across current and historical commits.
- Concept CLI owner logic still inside `propstore.cli.concept`.
- Source authoring/finalize/promote modules.
- Merge classification and structured merge.
- Micropub, proposal, worldline, repository-history, alias, and index helpers.

### Phase 6: Remove Propstore-Local Registry Machinery

- Delete propstore-local family registry behavior that has moved to Quire.
- Keep only propstore-owned domain declarations, document types, ref types, and
  semantic metadata.
- Run propstore's full logged suite and Quire's full `uv run pytest` suite.

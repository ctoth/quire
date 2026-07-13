# Quire documentation

Quire has three related but distinct surfaces: typed authored documents in Git,
declarations that derive storage and query schemas, and rebuildable derived
stores. Start with the smallest surface that solves the storage problem.

## Reading path

1. Read the [README](../README.md) for the project overview, installation, and a
   complete in-memory example.
2. Read [Working with document families](families.md) when defining placements,
   registries, transactions, references, or custom codecs.
3. Read [Charters and derived schemas](charters.md) when one declaration must
   drive document storage, schema metadata, SQLAlchemy models, FTS, or vector
   caches.
4. Read [Architecture and boundaries](architecture.md) when deciding which
   package should own a new capability or when integrating Quire into a larger
   application.

## API map

| Surface | Purpose | Installation |
| --- | --- | --- |
| `quire.documents` | Strict typed YAML/JSON decoding and document codecs | core |
| `quire.git_store` | Git objects, commits, trees, refs, notes, history, and transport plumbing | core |
| `quire.artifacts` | Typed artifact families and placement policies | core |
| `quire.family_store`, `quire.families` | Bound family reads, writes, registries, and transactions | core |
| `quire.contracts` | Deterministic storage contracts and compatibility checks | core |
| `quire.charter_class`, `quire.charters` | Declarative and imperative family charters | core |
| `quire.derived_store`, `quire.derived_runtime` | Content-addressed derived output and SQLite runtime policy | core |
| `quire.sqlalchemy_schema`, `quire.sqlalchemy_store` | Derived SQLAlchemy schema, sessions, and FTS5 | `quire[sql]` |
| `quire.sqlite_vec_store` | sqlite-vec cache management | `quire[vector]` |

The root `quire` package re-exports the stable core surface. Authoring helpers
and optional capabilities remain in their owning modules so their boundaries
are visible at the import site.

## Terminology

- **Document**: a strict typed value, normally a `msgspec.Struct`.
- **Reference**: the typed logical identifier a caller uses for a document.
- **Artifact family**: a document type plus its placement, codec, validation,
  and contract version.
- **Family definition**: an artifact family plus registry-level identity,
  reference, foreign-key, accessor, and metadata declarations.
- **Registry**: a validated collection of family definitions.
- **Bound family**: a family definition attached to an owner and a concrete
  document store.
- **Charter**: a declaration from which Quire derives a family definition,
  document codec, and schema description.
- **Derived store**: rebuildable query-oriented output keyed by authored input
  identity and schema/dependency content.

## Project status

Release notes are maintained in the [changelog](../CHANGELOG.md). Quire is an
alpha package: use contract manifests to make persisted-format changes explicit,
and consult the package exports for the current public surface.

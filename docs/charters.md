# Charters and derived schemas

A charter is one generic declaration from which Quire can derive several views
of the same family:

- the strict authored document type and codec;
- the `ArtifactFamily` and `FamilyDefinition` used by Git storage;
- a deterministic schema object and schema catalog;
- optional lifecycle, index, relationship, FTS, and vector declarations; and
- an optional SQLAlchemy model in a schema-local registry.

Charters prevent storage and query schemas from becoming independently authored
copies. They describe structure, not application commands or domain policy.

## Declarative charters

Use `CharterDoc` and `@charter` when a family can be declared statically:

```python
from typing import Annotated

from quire.charter_class import CharterDoc, charter, charter_field


@charter(
    key="articles",
    name="articles",
    contract_version="2026.07.01",
    placement="articles",
    identity_field="article_id",
)
class Article(CharterDoc):
    article_id: Annotated[str, charter_field(primary_key=True)]
    title: Annotated[str, charter_field(index=True, search=True)]
    tags: Annotated[tuple[str, ...], charter_field(json=True)] = ()
    summary: str | None = None
```

The class remains the public document type. The decorator attaches:

- `Article.__charter__`: the derived `FamilyCharter`; and
- `Article.__charter_model__`: the generated model used by SQL schema mapping.

`CharterDoc` is a strict `msgspec.Struct`, so unknown document fields fail
decoding. Ordinary annotations define document fields. `Annotated` plus
`charter_field(...)` adds storage or projection metadata only where needed.

## Field metadata

Common `charter_field` options include:

| Option | Meaning |
| --- | --- |
| `primary_key=True` | Marks the derived storage primary key. |
| `column_name="..."` | Uses a different physical storage name while retaining the document name. |
| `json=True` | Stores a structured value through the charter's JSON boundary. |
| `index=True`, `unique=True` | Declares ordinary derived-store indexes. |
| `search=True` | Marks a field for a declared search projection. |
| `vector_dimensions=N` | Supplies vector dimensionality metadata. |
| `foreign_key=...` | Declares a family foreign key at the source field. |
| `document_only=True` | Keeps a typed document projection without a duplicate physical column. |
| `versioned=False` | Excludes the field from the charter's document version hash. |
| `source_local_only=True` | Marks generic source-local storage metadata. |

Python optionality and defaults remain visible in the class. Use `T | None` for
an optional document value and a normal class default for a defaulted value.
Quire normalizes those into explicit schema nullability and defaults.

Fields with `json=True` preserve their typed document form while using a JSON
parse boundary for derived storage. This is distinct from accepting arbitrary
untyped mappings.

## Registries from charters

Build a storage registry directly from the attached charters:

```python
from quire import VersionId, registry_from_charters

registry = registry_from_charters(
    Article.__charter__,
    Author.__charter__,
    name="publication",
    contract_version=VersionId("2026.07.01"),
)
```

`registry_from_charters` makes each artifact family storage-complete by attaching
its charter codec. It also lifts field-level foreign-key declarations into the
owning `FamilyDefinition` and validates the resulting graph. Consumers should
not duplicate those foreign keys in a parallel registry literal.

If a registry intentionally contains only a query subset of a larger catalog,
pass `validate_foreign_keys=False`. This disables cross-family target validation,
not duplicate key, name, or accessor checks.

## Foreign-key declarations

Foreign keys are storage relationships, so their identity belongs in the
charter/family declaration:

```python
from typing import Annotated

from quire import ForeignKeySpec, VersionId
from quire.charter_class import CharterDoc, charter, charter_field


fk_version = VersionId("2026.07.01")


@charter(
    key="comments",
    name="comments",
    contract_version="2026.07.01",
    placement="comments",
    identity_field="comment_id",
)
class Comment(CharterDoc):
    comment_id: str
    article_id: Annotated[
        str,
        charter_field(
            foreign_key=ForeignKeySpec(
                name="comment_article",
                contract_version=fk_version,
                source_family="comments",
                source_field="article_id",
                target_family="articles",
            )
        ),
    ]
    text: str
```

The source family and source field are explicit and become part of the generic
family relationship graph. Registry construction rejects a target family that
is not present.

## Schema catalogs

`charter_catalog` lowers charters into an immutable, deterministic
`SchemaCatalog`:

```python
from quire import charter_catalog

catalog = charter_catalog(
    Article.__charter__,
    Comment.__charter__,
    metadata={"schema_version": 3},
)

schema_hash = catalog.schema_hash()
article_schema = next(
    schema_object
    for schema_object in catalog.objects
    if schema_object.name == "articles"
)
```

The catalog is data. It can be hashed for cache identity, inspected by generic
projection code, or passed to an optional schema backend. An application still
owns when a catalog version changes and how derived output is rebuilt.

## SQL capability

Install the SQL capability before importing its modules:

```bash
uv add "quire[sql]"
```

Build and materialize a SQLAlchemy schema with explicit capability imports:

```python
from quire.sqlalchemy_schema import build_sqlalchemy_schema
from quire.sqlalchemy_store import create_sqlalchemy_store, readonly_session

schema = build_sqlalchemy_schema(catalog)
create_sqlalchemy_store("publication.sqlite", schema)

with readonly_session("publication.sqlite", schema) as session:
    ArticleModel = schema.model("articles")
    rows = session.query(ArticleModel).all()
```

Generated models and mappers belong to the returned schema instance. Quire does
not clear or mutate a process-global SQLAlchemy mapper registry to build another
schema.

The SQL module also supports declared FTS5 indexes. FTS definitions belong on
the charter, while population and query timing remain application decisions.

## Vector capability

The vector extra includes the SQL capability and sqlite-vec:

```bash
uv add "quire[vector]"
```

`quire.sqlite_vec_store` manages generic vector-cache schema, model identities,
entity content hashes, nearest-neighbor iteration, and snapshot/restore. Vector
caches are derived artifacts: the application supplies embeddings and decides
when their source content is stale.

## Content-addressed derived stores

`DerivedStoreManager` publishes rebuildable SQLite output under a cache key
derived from projection identity, source commit, and a caller-supplied content
hash:

```python
from pathlib import Path
import sqlite3

from quire import DerivedStoreManager, derived_store_content_hash


def build_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE article_search (article_id TEXT PRIMARY KEY, title TEXT NOT NULL)"
        )
        connection.commit()
    finally:
        connection.close()


content_hash = derived_store_content_hash(
    projection_version="search-v1",
    schema_hash=catalog.schema_hash(),
    dependencies={"sqlite": sqlite3.sqlite_version},
)

manager = DerivedStoreManager(".cache/derived")
handle = manager.materialize(
    projection_id="publication.search",
    source_commit=source_commit,
    content_hash=content_hash,
    build=build_database,
)
```

Matching output is reused. A build occurs in temporary storage and is published
only after success. The manager does not discover authored inputs, run domain
queries, or select a projection version on the application's behalf.

Use `ProjectionBuildStep` and `order_projection_steps` for a generic dependency
order when one derived store contains several projection stages. Cycles are
rejected.

## Imperative charters

`FamilyCharter`, `CharterField`, and the related dataclasses support declarations
assembled dynamically. They lower to the same family, codec, and schema
surfaces. Prefer the declarative class when both forms can express the same
family: a single public document class is easier to type-check and harder to
drift than separate model and field declarations.

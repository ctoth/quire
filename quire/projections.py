from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Protocol, TypeAlias, cast


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DYNAMIC_SEGMENT = re.compile(r"^[A-Za-z0-9_]+$")


class ProjectionEncoder(Protocol):
    def __call__(self, value: Any) -> Any: ...


class ProjectionDecoder(Protocol):
    def __call__(self, value: Any) -> Any: ...


def json_encoder(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def json_decoder(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"Expected JSON text, got {type(value).__name__}")
    return json.loads(value)


def quote_identifier(identifier: str) -> str:
    _validate_identifier(identifier, "SQLite identifier")
    return f'"{identifier}"'


@dataclass(frozen=True)
class ProjectionColumn:
    name: str
    sql_type: str
    nullable: bool = True
    primary_key: bool = False
    insertable: bool = True
    default_sql: str | None = None
    check_sql: str | None = None
    encoder: ProjectionEncoder | None = None
    decoder: ProjectionDecoder | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "projection column")

    def ddl(self) -> str:
        parts = [quote_identifier(self.name), self.sql_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.nullable:
            parts.append("NOT NULL")
        if self.default_sql is not None:
            parts.extend(("DEFAULT", self.default_sql))
        if self.check_sql is not None:
            parts.append(f"CHECK({self.check_sql})")
        return " ".join(parts)

    def encode(self, value: Any) -> Any:
        if self.encoder is None:
            return value
        return self.encoder(value)

    def decode(self, value: Any) -> Any:
        if self.decoder is None:
            return value
        return self.decoder(value)

    def schema_hash_material(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sql_type": self.sql_type,
            "nullable": self.nullable,
            "primary_key": self.primary_key,
            "insertable": self.insertable,
            "default_sql": self.default_sql,
            "check_sql": self.check_sql,
            "codec": _codec_name(self.encoder, self.decoder),
        }


_UNSET: Any = object()


@dataclass(frozen=True)
class ProjectionField:
    """Reusable typed projection-field descriptor.

    A field describes the stable semantic storage role. Individual tables can
    derive columns from that descriptor with local cardinality or key overrides
    without re-declaring the SQL type and codec policy.
    """

    name: str
    sql_type: str
    nullable: bool = True
    primary_key: bool = False
    insertable: bool = True
    default_sql: str | None = None
    check_sql: str | None = None
    encoder: ProjectionEncoder | None = None
    decoder: ProjectionDecoder | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "projection field")

    def column(
        self,
        *,
        name: str | None = None,
        sql_type: str | None = None,
        nullable: bool | None = None,
        primary_key: bool | None = None,
        insertable: bool | None = None,
        default_sql: Any = _UNSET,
        check_sql: Any = _UNSET,
        encoder: Any = _UNSET,
        decoder: Any = _UNSET,
    ) -> ProjectionColumn:
        return ProjectionColumn(
            name=self.name if name is None else name,
            sql_type=self.sql_type if sql_type is None else sql_type,
            nullable=self.nullable if nullable is None else nullable,
            primary_key=self.primary_key if primary_key is None else primary_key,
            insertable=self.insertable if insertable is None else insertable,
            default_sql=self.default_sql if default_sql is _UNSET else default_sql,
            check_sql=self.check_sql if check_sql is _UNSET else check_sql,
            encoder=self.encoder if encoder is _UNSET else encoder,
            decoder=self.decoder if decoder is _UNSET else decoder,
        )


def text_field(name: str, *, nullable: bool = True) -> ProjectionField:
    return ProjectionField(name, "TEXT", nullable=nullable)


def integer_field(name: str, *, nullable: bool = True) -> ProjectionField:
    return ProjectionField(name, "INTEGER", nullable=nullable)


def real_field(name: str, *, nullable: bool = True) -> ProjectionField:
    return ProjectionField(name, "REAL", nullable=nullable)


def json_text_field(name: str, *, nullable: bool = True) -> ProjectionField:
    return ProjectionField(
        name,
        "TEXT",
        nullable=nullable,
        encoder=json_encoder,
        decoder=json_decoder,
    )


def family_reference_field(
    family: str,
    *,
    role: str | None = None,
    nullable: bool = True,
) -> ProjectionField:
    _validate_identifier(family, "family reference")
    if role is not None:
        _validate_identifier(role, "family reference role")
    prefix = family if role is None else f"{role}_{family}"
    return ProjectionField(f"{prefix}_id", "TEXT", nullable=nullable)


@dataclass(frozen=True)
class ProjectionForeignKey:
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError("Projection foreign key must declare columns")
        if len(self.columns) != len(self.ref_columns):
            raise ValueError("Projection foreign key column counts must match")
        _validate_dynamic_name(self.ref_table, "projection foreign-key table")
        for column in self.columns:
            _validate_identifier(column, "projection foreign-key column")
        for column in self.ref_columns:
            _validate_identifier(column, "projection foreign-key reference column")

    def schema_hash_material(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "ref_table": self.ref_table,
            "ref_columns": self.ref_columns,
        }

    def ddl(self, bindings: Mapping[str, str] | None = None) -> str:
        columns = ", ".join(quote_identifier(column) for column in self.columns)
        ref_table = render_projection_name(self.ref_table, bindings)
        ref_columns = ", ".join(quote_identifier(column) for column in self.ref_columns)
        return (
            f"FOREIGN KEY ({columns}) REFERENCES "
            f"{quote_identifier(ref_table)}({ref_columns})"
        )


@dataclass(frozen=True)
class ProjectionIndex:
    name: str
    columns: tuple[str, ...]
    unique: bool = False
    where_sql: str | None = None

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError(f"Projection index {self.name!r} must declare columns")
        _validate_identifier(self.name, "projection index")
        for column in self.columns:
            _validate_identifier(column, "projection index column")

    def schema_hash_material(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": self.columns,
            "unique": self.unique,
            "where_sql": self.where_sql,
        }

    def ddl(self, table_name: str) -> str:
        unique = "UNIQUE " if self.unique else ""
        columns = ", ".join(quote_identifier(column) for column in self.columns)
        statement = (
            f"CREATE {unique}INDEX IF NOT EXISTS {quote_identifier(self.name)} "
            f"ON {quote_identifier(table_name)}({columns})"
        )
        if self.where_sql is not None:
            statement += f" WHERE {self.where_sql}"
        return statement


@dataclass(frozen=True)
class ProjectionRow:
    table: str
    values: Mapping[str, Any]

    def value_for(self, column: ProjectionColumn) -> Any:
        return column.encode(self.values.get(column.name))


@dataclass(frozen=True)
class ProjectionCatalogColumn:
    name: str
    sql_type: str
    nullable: bool
    primary_key: bool = False
    insertable: bool = True


@dataclass(frozen=True)
class ProjectionCatalogEntry:
    name: str
    kind: str
    columns: tuple[ProjectionCatalogColumn, ...]
    declaration_name: str
    dynamic: bool = False

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


@dataclass(frozen=True)
class ProjectionRuntimeCatalog:
    entries: tuple[ProjectionCatalogEntry, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.entries)

    def entry(self, name: str) -> ProjectionCatalogEntry:
        for item in self.entries:
            if item.name == name:
                return item
        raise KeyError(name)


@dataclass(frozen=True)
class ProjectionTable:
    name: str
    columns: tuple[ProjectionColumn, ...]
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ProjectionForeignKey, ...] = ()
    indexes: tuple[ProjectionIndex, ...] = ()
    checks: tuple[str, ...] = ()
    if_not_exists: bool = False
    row_factory: Callable[[Mapping[str, Any]], Any] | None = None

    def __post_init__(self) -> None:
        _validate_dynamic_name(self.name, "projection table")
        column_names = tuple(column.name for column in self.columns)
        if not column_names:
            raise ValueError(f"Projection table {self.name!r} must declare columns")
        if len(set(column_names)) != len(column_names):
            raise ValueError(f"Projection table {self.name!r} has duplicate columns")
        declared_columns = set(column_names)
        for key_column in self.primary_key:
            if key_column not in declared_columns:
                raise ValueError(f"Primary key column {key_column!r} is not declared")
        for foreign_key in self.foreign_keys:
            missing = set(foreign_key.columns) - declared_columns
            if missing:
                raise ValueError(
                    f"Foreign key references undeclared columns: {sorted(missing)}"
                )
        for index in self.indexes:
            missing = set(index.columns) - declared_columns
            if missing:
                raise ValueError(
                    f"Index {index.name!r} references undeclared columns: {sorted(missing)}"
                )

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def insert_columns(self) -> tuple[ProjectionColumn, ...]:
        return tuple(column for column in self.columns if column.insertable)

    def projection_name(self, bindings: Mapping[str, str] | None = None) -> str:
        return render_projection_name(self.name, bindings)

    def create_ddl(self, bindings: Mapping[str, str] | None = None) -> str:
        table_name = self.projection_name(bindings)
        exists = " IF NOT EXISTS" if self.if_not_exists else ""
        parts = [column.ddl() for column in self.columns]
        if self.primary_key:
            key_columns = ", ".join(quote_identifier(column) for column in self.primary_key)
            parts.append(f"PRIMARY KEY ({key_columns})")
        parts.extend(foreign_key.ddl(bindings) for foreign_key in self.foreign_keys)
        parts.extend(f"CHECK({check})" for check in self.checks)
        body = ",\n    ".join(parts)
        return f"CREATE TABLE{exists} {quote_identifier(table_name)} (\n    {body}\n)"

    def ddl_statements(
        self,
        bindings: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        table_name = self.projection_name(bindings)
        return (self.create_ddl(bindings),) + tuple(
            index.ddl(table_name) for index in self.indexes
        )

    def insert_sql(
        self,
        *,
        or_ignore: bool = False,
        or_replace: bool = False,
        bindings: Mapping[str, str] | None = None,
    ) -> str:
        if or_ignore and or_replace:
            raise ValueError("insert_sql accepts only one conflict policy")
        table_name = self.projection_name(bindings)
        if or_ignore:
            verb = "INSERT OR IGNORE"
        elif or_replace:
            verb = "INSERT OR REPLACE"
        else:
            verb = "INSERT"
        columns = ", ".join(quote_identifier(column.name) for column in self.insert_columns)
        params = ", ".join(f":{column.name}" for column in self.insert_columns)
        return f"{verb} INTO {quote_identifier(table_name)} ({columns}) VALUES ({params})"

    def select_all_sql(
        self,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> str:
        table_name = self.projection_name(bindings)
        columns = ", ".join(quote_identifier(column.name) for column in self.columns)
        return f"SELECT {columns} FROM {quote_identifier(table_name)}"

    def row(self, **values: Any) -> ProjectionRow:
        return ProjectionRow(table=self.name, values=values)

    def encode_row(self, values: Mapping[str, Any] | object) -> dict[str, Any]:
        row_values = _row_values(values)
        return {column.name: column.encode(row_values.get(column.name)) for column in self.columns}

    def insert_row(
        self,
        conn: sqlite3.Connection,
        values: Mapping[str, Any] | object,
        *,
        or_ignore: bool = False,
        or_replace: bool = False,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        conn.execute(
            self.insert_sql(
                or_ignore=or_ignore,
                or_replace=or_replace,
                bindings=bindings,
            ),
            self.encode_row(values),
        )

    def insert_rows(
        self,
        conn: sqlite3.Connection,
        rows: Iterable[Mapping[str, Any] | object],
        *,
        or_ignore: bool = False,
        or_replace: bool = False,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        conn.executemany(
            self.insert_sql(
                or_ignore=or_ignore,
                or_replace=or_replace,
                bindings=bindings,
            ),
            tuple(self.encode_row(row) for row in rows),
        )

    def select_all(
        self,
        conn: sqlite3.Connection,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> tuple[Any, ...]:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(self.select_all_sql(bindings=bindings)).fetchall()
        return tuple(self.decode_row(row) for row in rows)

    def decode_row(self, row: sqlite3.Row | Mapping[str, Any]) -> Any:
        if isinstance(row, sqlite3.Row):
            row_keys = set(row.keys())
        else:
            row_keys = set(row)
        decoded = {
            column.name: column.decode(row[column.name])
            for column in self.columns
            if column.name in row_keys
        }
        if self.row_factory is not None:
            return self.row_factory(decoded)
        return ProjectionRow(table=self.name, values=decoded)

    def schema_hash_material(self) -> dict[str, Any]:
        return {
            "kind": "table",
            "name": self.name,
            "columns": tuple(column.schema_hash_material() for column in self.columns),
            "primary_key": self.primary_key,
            "foreign_keys": tuple(key.schema_hash_material() for key in self.foreign_keys),
            "indexes": tuple(index.schema_hash_material() for index in self.indexes),
            "checks": self.checks,
            "if_not_exists": self.if_not_exists,
        }


@dataclass(frozen=True)
class FtsProjection:
    table: str
    key_column: str
    columns: tuple[str, ...]
    source_query: str | None = None
    row_plan: str | None = None

    def __post_init__(self) -> None:
        _validate_dynamic_name(self.table, "FTS projection")
        column_names = self.column_names
        if len(set(column_names)) != len(column_names):
            raise ValueError(f"FTS projection {self.table!r} has duplicate columns")
        for column in column_names:
            _validate_identifier(column, "FTS projection column")
        if self.source_query is not None and self.row_plan is not None:
            raise ValueError(
                f"FTS projection {self.table!r} must use source_query or row_plan, not both"
            )

    @property
    def column_names(self) -> tuple[str, ...]:
        return (self.key_column,) + self.columns

    def projection_name(self, bindings: Mapping[str, str] | None = None) -> str:
        return render_projection_name(self.table, bindings)

    def ddl_statements(
        self,
        bindings: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        table_name = self.projection_name(bindings)
        columns = [quote_identifier(self.key_column) + " UNINDEXED"]
        columns.extend(quote_identifier(column) for column in self.columns)
        return (
            f"CREATE VIRTUAL TABLE {quote_identifier(table_name)} "
            f"USING fts5({', '.join(columns)})",
        )

    def insert_sql(self, bindings: Mapping[str, str] | None = None) -> str:
        table_name = self.projection_name(bindings)
        columns = ", ".join(quote_identifier(column) for column in self.column_names)
        params = ", ".join(f":{column}" for column in self.column_names)
        return f"INSERT INTO {quote_identifier(table_name)} ({columns}) VALUES ({params})"

    def row(self, **values: Any) -> ProjectionRow:
        return ProjectionRow(table=self.table, values=values)

    def insert_row(
        self,
        conn: sqlite3.Connection,
        values: Mapping[str, Any] | object,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        conn.execute(self.insert_sql(bindings), _row_values(values))

    def insert_rows(
        self,
        conn: sqlite3.Connection,
        rows: Iterable[Mapping[str, Any] | object],
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        conn.executemany(
            self.insert_sql(bindings),
            tuple(_row_values(row) for row in rows),
        )

    def population_sql(self, bindings: Mapping[str, str] | None = None) -> str:
        if self.source_query is None:
            raise ValueError(f"FTS projection {self.table!r} has no source query")
        table_name = self.projection_name(bindings)
        columns = ", ".join(quote_identifier(column) for column in self.column_names)
        return f"INSERT INTO {quote_identifier(table_name)} ({columns}) {self.source_query}"

    def populate_from_source_query(
        self,
        conn: sqlite3.Connection,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        conn.execute(self.population_sql(bindings))

    def population_plan(self) -> str:
        if self.source_query is not None:
            return self.source_query
        if self.row_plan is not None:
            return self.row_plan
        raise ValueError(f"FTS projection {self.table!r} has no population plan")

    def validate_search_columns(self, columns: tuple[str, ...]) -> None:
        declared = set(self.column_names)
        missing = tuple(column for column in columns if column not in declared)
        if missing:
            joined = ", ".join(missing)
            raise ValueError(
                f"FTS projection {self.table!r} does not declare search column(s): {joined}"
            )

    def match_sql(
        self,
        select_columns: tuple[str, ...],
        *,
        bindings: Mapping[str, str] | None = None,
        query_param: str = "query",
        limit_param: str | None = None,
    ) -> str:
        self.validate_search_columns(select_columns)
        table_name = self.projection_name(bindings)
        selected = ", ".join(quote_identifier(column) for column in select_columns)
        statement = (
            f"SELECT {selected} FROM {quote_identifier(table_name)} "
            f"WHERE {quote_identifier(table_name)} MATCH :{query_param}"
        )
        if limit_param is not None:
            statement += f" LIMIT :{limit_param}"
        return statement

    def schema_hash_material(self) -> dict[str, Any]:
        return {
            "kind": "fts5",
            "table": self.table,
            "key_column": self.key_column,
            "columns": self.columns,
            "source_query": self.source_query,
            "row_plan": self.row_plan,
        }


@dataclass(frozen=True)
class VecProjection:
    table: str
    key_column: ProjectionColumn | None
    vector_column: ProjectionColumn
    metadata_columns: tuple[ProjectionColumn, ...] = ()

    def __post_init__(self) -> None:
        _validate_dynamic_name(self.table, "vector projection")
        column_names = self.column_names
        if len(set(column_names)) != len(column_names):
            raise ValueError(f"Vector projection {self.table!r} has duplicate columns")

    @property
    def columns(self) -> tuple[ProjectionColumn, ...]:
        if self.key_column is None:
            return (self.vector_column,) + self.metadata_columns
        return (self.key_column, self.vector_column) + self.metadata_columns

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def projection_name(self, bindings: Mapping[str, str] | None = None) -> str:
        return render_projection_name(self.table, bindings)

    def ddl_statements(
        self,
        bindings: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        table_name = self.projection_name(bindings)
        columns = ", ".join(
            f"{column.name} {_render_dynamic_text(column.sql_type, bindings)}"
            for column in self.columns
        )
        return (
            f"CREATE VIRTUAL TABLE {quote_identifier(table_name)} "
            f"USING vec0({columns})",
        )

    def insert_sql(self, bindings: Mapping[str, str] | None = None) -> str:
        table_name = self.projection_name(bindings)
        columns = ", ".join(quote_identifier(column.name) for column in self.columns)
        params = ", ".join(f":{column.name}" for column in self.columns)
        return f"INSERT INTO {quote_identifier(table_name)} ({columns}) VALUES ({params})"

    def insert_rowid_sql(self, bindings: Mapping[str, str] | None = None) -> str:
        table_name = self.projection_name(bindings)
        columns = ", ".join(
            ("rowid",) + tuple(quote_identifier(column.name) for column in self.columns)
        )
        params = ", ".join((":rowid",) + tuple(f":{column.name}" for column in self.columns))
        return f"INSERT INTO {quote_identifier(table_name)} ({columns}) VALUES ({params})"

    def delete_rowid_sql(self, bindings: Mapping[str, str] | None = None) -> str:
        table_name = self.projection_name(bindings)
        return f"DELETE FROM {quote_identifier(table_name)} WHERE rowid = :rowid"

    def row(self, **values: Any) -> ProjectionRow:
        return ProjectionRow(table=self.table, values=values)

    def insert_row(
        self,
        conn: sqlite3.Connection,
        values: Mapping[str, Any] | object,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        conn.execute(self.insert_sql(bindings), self.encode_row(values))

    def insert_rowid(
        self,
        conn: sqlite3.Connection,
        values: Mapping[str, Any] | object,
        *,
        rowid: int,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        row = {"rowid": rowid, **self.encode_row(values)}
        conn.execute(self.insert_rowid_sql(bindings), row)

    def insert_rows(
        self,
        conn: sqlite3.Connection,
        rows: Iterable[Mapping[str, Any] | object],
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        conn.executemany(
            self.insert_sql(bindings),
            tuple(self.encode_row(row) for row in rows),
        )

    def delete_rowid(
        self,
        conn: sqlite3.Connection,
        *,
        rowid: int,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        conn.execute(self.delete_rowid_sql(bindings), {"rowid": rowid})

    def search_sql(
        self,
        *,
        bindings: Mapping[str, str] | None = None,
        vector_param: str = "query_vector",
        limit_param: str = "k",
    ) -> str:
        table_name = self.projection_name(bindings)
        vector_column = quote_identifier(self.vector_column.name)
        return (
            f"SELECT rowid, distance FROM {quote_identifier(table_name)} "
            f"WHERE {vector_column} MATCH :{vector_param} AND k = :{limit_param} "
            "ORDER BY distance"
        )

    def encode_row(self, values: Mapping[str, Any] | object) -> dict[str, Any]:
        row_values = _row_values(values)
        return {column.name: column.encode(row_values.get(column.name)) for column in self.columns}

    def schema_hash_material(self) -> dict[str, Any]:
        return {
            "kind": "vec0",
            "table": self.table,
            "columns": tuple(column.schema_hash_material() for column in self.columns),
        }


SemanticProjection: TypeAlias = ProjectionTable | FtsProjection | VecProjection


@dataclass(frozen=True)
class ProjectionSchema:
    projections: tuple[SemanticProjection, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = tuple(projection_name(projection) for projection in self.projections)
        if len(set(names)) != len(names):
            raise ValueError("Projection schema has duplicate projection names")

    @property
    def projection_names(self) -> tuple[str, ...]:
        return tuple(projection_name(projection) for projection in self.projections)

    def projection(self, name: str) -> SemanticProjection:
        for item in self.projections:
            if projection_name(item) == name:
                return item
        raise KeyError(name)

    def ddl_statements(
        self,
        bindings: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        statements: list[str] = []
        for projection in self.projections:
            if isinstance(projection, (ProjectionTable, FtsProjection, VecProjection)):
                statements.extend(projection.ddl_statements(bindings))
        return tuple(statements)

    def runtime_catalog(
        self,
        bindings: Mapping[str, str] | None = None,
    ) -> ProjectionRuntimeCatalog:
        return ProjectionRuntimeCatalog(
            entries=tuple(
                projection_catalog_entry(projection, bindings=bindings)
                for projection in self.projections
            ),
            metadata=self.metadata,
        )

    def create_all(
        self,
        conn: sqlite3.Connection,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        for statement in self.ddl_statements(bindings):
            conn.execute(statement)

    def validate_connection(
        self,
        conn: sqlite3.Connection,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> None:
        missing_tables: list[str] = []
        missing_columns: list[str] = []
        for projection in self.projections:
            if not isinstance(projection, (ProjectionTable, FtsProjection, VecProjection)):
                continue
            table_name = projection.projection_name(bindings)
            if not _has_table(conn, table_name):
                missing_tables.append(table_name)
                continue
            for column in projection.column_names:
                if column not in _table_columns(conn, table_name):
                    missing_columns.append(f"{table_name}.{column}")
        if missing_tables:
            missing = ", ".join(sorted(missing_tables))
            raise ProjectionSchemaError(f"missing table(s) {missing}")
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ProjectionSchemaError(f"missing column(s) {missing}")

    def schema_hash_material(self) -> str:
        material = {
            "metadata": self.metadata,
            "projections": tuple(
                projection.schema_hash_material() for projection in self.projections
            ),
        }
        return json.dumps(material, sort_keys=True, separators=(",", ":"))


class ProjectionSchemaError(ValueError):
    pass


def create_projection_schema(
    *projections: SemanticProjection,
    metadata: Mapping[str, Any] | None = None,
) -> ProjectionSchema:
    return ProjectionSchema(
        projections=tuple(projections),
        metadata={} if metadata is None else dict(metadata),
    )


def projection_name(projection: SemanticProjection) -> str:
    if isinstance(projection, ProjectionTable):
        return projection.name
    return projection.table


def projection_catalog_entry(
    projection: SemanticProjection,
    *,
    bindings: Mapping[str, str] | None = None,
) -> ProjectionCatalogEntry:
    declared_name = projection_name(projection)
    name = projection.projection_name(bindings)
    if isinstance(projection, ProjectionTable):
        columns = tuple(
            ProjectionCatalogColumn(
                name=column.name,
                sql_type=column.sql_type,
                nullable=column.nullable,
                primary_key=column.primary_key or column.name in projection.primary_key,
                insertable=column.insertable,
            )
            for column in projection.columns
        )
        kind = "table"
    elif isinstance(projection, FtsProjection):
        columns = (ProjectionCatalogColumn(projection.key_column, "TEXT", False),) + tuple(
            ProjectionCatalogColumn(column, "TEXT", True) for column in projection.columns
        )
        kind = "fts5"
    else:
        columns = tuple(
            ProjectionCatalogColumn(
                name=column.name,
                sql_type=column.sql_type,
                nullable=column.nullable,
                primary_key=column.primary_key,
                insertable=column.insertable,
            )
            for column in projection.columns
        )
        kind = "vec0"
    return ProjectionCatalogEntry(
        name=name,
        kind=kind,
        columns=columns,
        declaration_name=declared_name,
        dynamic="{" in declared_name or "}" in declared_name,
    )


def render_projection_name(name: str, bindings: Mapping[str, str] | None = None) -> str:
    if "{" not in name:
        _validate_identifier(name, "projection name")
        return name
    if bindings is None:
        raise ValueError(f"Dynamic projection name {name!r} requires bindings")
    rendered = name
    for key, value in bindings.items():
        if not _DYNAMIC_SEGMENT.fullmatch(value):
            raise ValueError(f"Invalid dynamic name segment for {key}: {value!r}")
        rendered = rendered.replace("{" + key + "}", value)
    if "{" in rendered or "}" in rendered:
        raise ValueError(f"Unbound dynamic projection name segment in {name!r}")
    _validate_identifier(rendered, "projection name")
    return rendered


def _render_dynamic_text(
    value: str,
    bindings: Mapping[str, str] | None = None,
) -> str:
    if "{" not in value:
        return value
    if bindings is None:
        raise ValueError(f"Dynamic projection text {value!r} requires bindings")
    rendered = value
    for key, replacement in bindings.items():
        if not _DYNAMIC_SEGMENT.fullmatch(replacement):
            raise ValueError(f"Invalid dynamic text segment for {key}: {replacement!r}")
        rendered = rendered.replace("{" + key + "}", replacement)
    if "{" in rendered or "}" in rendered:
        raise ValueError(f"Unbound dynamic projection text segment in {value!r}")
    return rendered


def _row_values(values: Mapping[str, Any] | object) -> Mapping[str, Any]:
    if isinstance(values, Mapping):
        return values
    if isinstance(values, ProjectionRow):
        return values.values
    if is_dataclass(values):
        return asdict(cast(Any, values))
    raise TypeError(
        "Projection row must be a mapping, ProjectionRow, or dataclass, "
        f"got {type(values).__name__}"
    )


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    return {str(row[1]) for row in rows}


def _validate_dynamic_name(name: str, label: str) -> None:
    if "{" in name or "}" in name:
        return
    _validate_identifier(name, label)


def _validate_identifier(identifier: str, label: str) -> None:
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"Invalid {label}: {identifier!r}")


def _codec_name(
    encoder: ProjectionEncoder | None,
    decoder: ProjectionDecoder | None,
) -> str | None:
    if encoder is json_encoder and decoder is json_decoder:
        return "json"
    if encoder is None and decoder is None:
        return None
    names = (
        getattr(encoder, "__name__", encoder.__class__.__name__ if encoder else "none"),
        getattr(decoder, "__name__", decoder.__class__.__name__ if decoder else "none"),
    )
    return ":".join(names)


ARTIFACT_ID_FIELD = ProjectionField("id", "TEXT")
AUTOINCREMENT_ID_FIELD = ProjectionField(
    "id",
    "INTEGER PRIMARY KEY AUTOINCREMENT",
    insertable=False,
)
PRIMARY_LOGICAL_ID_FIELD = ProjectionField(
    "primary_logical_id",
    "TEXT",
    nullable=False,
    default_sql="''",
)
LOGICAL_IDS_JSON_FIELD = ProjectionField(
    "logical_ids_json",
    "TEXT",
    nullable=False,
    default_sql="'[]'",
)
VERSION_ID_FIELD = ProjectionField(
    "version_id",
    "TEXT",
    nullable=False,
    default_sql="''",
)
CONTENT_HASH_FIELD = ProjectionField("content_hash", "TEXT", nullable=False)
SEQUENCE_FIELD = ProjectionField("seq", "INTEGER", nullable=False)
CONDITIONS_CEL_FIELD = ProjectionField("conditions_cel", "TEXT")
CONDITIONS_IR_FIELD = ProjectionField("conditions_ir", "TEXT")
PROVENANCE_JSON_FIELD = ProjectionField("provenance_json", "TEXT")

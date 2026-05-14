from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias


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


@dataclass(frozen=True)
class ProjectionRow:
    table: str
    values: Mapping[str, Any]

    def value_for(self, column: ProjectionColumn) -> Any:
        return column.encode(self.values.get(column.name))


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

    def encode_row(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return {column.name: column.encode(values.get(column.name)) for column in self.columns}

    def decode_row(self, row: Mapping[str, Any]) -> Any:
        decoded = {
            column.name: column.decode(row[column.name])
            for column in self.columns
            if column.name in row
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

    def encode_row(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return {column.name: column.encode(values.get(column.name)) for column in self.columns}

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

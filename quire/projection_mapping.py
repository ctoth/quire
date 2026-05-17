from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Generic, TypeVar, get_args, get_origin, get_type_hints

from quire.projections import (
    ProjectionColumn,
    ProjectionField,
    ProjectionForeignKey,
    ProjectionIndex,
    ProjectionRow,
    ProjectionTable,
    json_decoder,
    json_encoder,
    quote_identifier,
)


_MISSING = object()
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ProjectionCodec:
    name: str = "scalar"
    sql_type: str = "TEXT"
    encoder: Callable[[Any], Any] | None = None
    decoder: Callable[[Any], Any] | None = None

    def encode(self, value: Any) -> Any:
        if self.encoder is None:
            return value
        return self.encoder(value)

    def decode(self, value: Any) -> Any:
        if self.decoder is None:
            return value
        return self.decoder(value)

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {"name": self.name, "sql_type": self.sql_type}


SCALAR_CODEC = ProjectionCodec()
JSON_CODEC = ProjectionCodec("json", "TEXT", json_encoder, json_decoder)


@dataclass(frozen=True)
class ProjectionBinding:
    path: tuple[str, ...]
    field: ProjectionField | None = None
    projection_column_owner: ProjectionColumn | None = None
    read_name: str | None = None
    missing: str = "none"
    default: Any = None

    def __post_init__(self) -> None:
        owner_count = sum(owner is not None for owner in (self.field, self.projection_column_owner))
        if owner_count != 1:
            raise ValueError("ProjectionBinding must reference exactly one physical owner")

    @property
    def projection_column(self) -> ProjectionColumn:
        if self.projection_column_owner is not None:
            return self.projection_column_owner
        if self.field is None:
            raise ValueError("ProjectionBinding has no physical owner")
        return self.field.column()

    @property
    def column_name(self) -> str:
        return self.projection_column.name

    def column_spec(self) -> ProjectionColumn:
        return self.projection_column

    def encode_value(self, source: object) -> Any:
        value = _read_path(source, self.path, default=_MISSING)
        if value is _MISSING:
            if self.missing == "raise":
                raise KeyError(".".join(self.path))
            value = self.default
        return self.projection_column.encode(value)

    def decode_value(self, value: Any) -> Any:
        return self.projection_column.decode(value)

    def schema_hash_material(self) -> Mapping[str, Any]:
        owner: dict[str, Any]
        if self.field is not None:
            owner = {"kind": "ProjectionField", "name": self.field.name}
        else:
            owner = {"kind": "ProjectionColumn", "name": self.projection_column.name}
        return {
            "kind": type(self).__name__,
            "path": self.path,
            "owner": owner,
            "read_name": self.read_name,
            "missing": self.missing,
        }


@dataclass(frozen=True)
class ScalarPath:
    path: tuple[str, ...]
    column: str
    codec: ProjectionCodec = SCALAR_CODEC
    nullable: bool = True
    primary_key: bool = False
    insertable: bool = True
    indexed: bool = False
    default: Any = None
    default_sql: str | None = None
    check_sql: str | None = None
    missing: str = "none"

    def column_spec(self) -> ProjectionColumn:
        return ProjectionColumn(
            self.column,
            self.codec.sql_type,
            nullable=self.nullable,
            primary_key=self.primary_key,
            insertable=self.insertable,
            default_sql=self.default_sql,
            check_sql=self.check_sql,
            encoder=self.codec.encode,
            decoder=self.codec.decode,
        )

    def encode_value(self, source: object) -> Any:
        value = _read_path(source, self.path, default=_MISSING)
        if value is _MISSING:
            if self.missing == "raise":
                raise KeyError(".".join(self.path))
            value = self.default
        return self.codec.encode(value)

    def decode_value(self, value: Any) -> Any:
        return self.codec.decode(value)

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": type(self).__name__,
            "path": self.path,
            "column": self.column,
            "nullable": self.nullable,
            "primary_key": self.primary_key,
            "insertable": self.insertable,
            "indexed": self.indexed,
            "missing": self.missing,
            "default_sql": self.default_sql,
            "check_sql": self.check_sql,
            "codec": self.codec.schema_hash_material(),
        }


@dataclass(frozen=True)
class JsonPath(ScalarPath):
    codec: ProjectionCodec = JSON_CODEC


@dataclass(frozen=True)
class EnumPath(ScalarPath):
    enum: type[Enum] = Enum

    def encode_value(self, source: object) -> Any:
        value = _read_path(source, self.path, default=_MISSING)
        if value is _MISSING:
            if self.missing == "raise":
                raise KeyError(".".join(self.path))
            value = self.default
        if value is None:
            return None
        if isinstance(value, self.enum):
            return value.value
        return self.enum(value).value

    def decode_value(self, value: Any) -> Any:
        if value is None:
            return None
        return self.enum(value)

    def schema_hash_material(self) -> Mapping[str, Any]:
        material = dict(super().schema_hash_material())
        material["enum"] = f"{self.enum.__module__}.{self.enum.__qualname__}"
        return material


@dataclass(frozen=True)
class ReferencePath(ScalarPath):
    family: str = ""
    ref_column: str = "id"

    def foreign_key(self) -> ProjectionForeignKey:
        return ProjectionForeignKey((self.column,), self.family, (self.ref_column,))

    def schema_hash_material(self) -> Mapping[str, Any]:
        material = dict(super().schema_hash_material())
        material["family"] = self.family
        material["ref_column"] = self.ref_column
        return material


@dataclass(frozen=True)
class ProjectionRenderView:
    source_path: tuple[str, ...]
    output_key: str
    codec: ProjectionCodec = SCALAR_CODEC
    default: Any = None
    missing: str = "none"

    def encode_value(self, source: object) -> Any:
        value = _read_path(source, self.source_path, default=_MISSING)
        if value is _MISSING:
            if self.missing == "raise":
                raise KeyError(".".join(self.source_path))
            value = self.default
        return self.codec.encode(value)

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": type(self).__name__,
            "source_path": self.source_path,
            "output_key": self.output_key,
            "missing": self.missing,
            "codec": self.codec.schema_hash_material(),
        }


@dataclass(frozen=True)
class ProjectionInputKey:
    key: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("ProjectionInputKey requires a non-empty key")

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": type(self).__name__,
            "key": self.key,
        }


ProjectionPath = ScalarPath | JsonPath | EnumPath | ReferencePath | ProjectionBinding


@dataclass(frozen=True)
class ProjectionComponent:
    path: tuple[str, ...]
    bindings: tuple[ProjectionBinding, ...]
    encoder: Callable[[Any], Mapping[str, Any]]
    decoder: Callable[[Mapping[str, Any]], Any]

    def encode_values(self, source: object) -> Mapping[str, object]:
        value = _read_path(source, self.path, default=None)
        raw_values = self.encoder(value)
        row: dict[str, object] = {}
        for binding in self.bindings:
            column = binding.column_name
            row[column] = binding.projection_column.encode(raw_values.get(column))
        return row

    def decode_value(self, row: Mapping[str, object]) -> Any:
        values: dict[str, Any] = {}
        for binding in self.bindings:
            column = _decode_column_for(binding, row)
            if column is not None:
                values[binding.column_name] = binding.decode_value(row[column])
            elif binding.missing == "raise":
                raise KeyError(binding.column_name)
            else:
                values[binding.column_name] = binding.default
        return self.decoder(values)

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": "ProjectionComponent",
            "path": self.path,
            "bindings": tuple(_stable_field_material(binding) for binding in self.bindings),
        }


@dataclass(frozen=True)
class ProjectionMetadata:
    path: tuple[str, ...]
    fields: tuple[ProjectionPath, ...]
    result_type: type[Any] | None = None

    def encode_values(self, source: object) -> Mapping[str, object]:
        metadata_source = source if not self.path else _read_path(source, self.path, default={})
        row: dict[str, object] = {}
        for field in self.fields:
            row[_column_name_for(field)] = field.encode_value(metadata_source)
        return row

    def decode_value(self, row: Mapping[str, object]) -> Any:
        data: dict[str, Any] = {}
        for field in self.fields:
            column = _decode_column_for(field, row)
            if column is not None:
                _assign_path(data, field.path, field.decode_value(row[column]))
            elif field.missing == "raise":
                raise KeyError(_column_name_for(field))
            else:
                _assign_path(data, field.path, field.default)
        if self.result_type is None or self.result_type is dict:
            return data
        return _construct(self.result_type, data)

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": "ProjectionMetadata",
            "path": self.path,
            "result_type": None
            if self.result_type is None
            else f"{self.result_type.__module__}.{self.result_type.__qualname__}",
            "fields": tuple(_stable_field_material(field) for field in self.fields),
        }


@dataclass(frozen=True)
class ProjectionAttachedRows:
    path: tuple[str, ...]
    table: str
    fields: tuple[ProjectionPath, ...]
    parent_fk: str
    parent_path: tuple[str, ...] = ("id",)
    item_parent_path: tuple[str, ...] | None = None
    item_type: type[Any] | None = None
    order_by: tuple[str | ProjectionPath, ...] = ()
    fetch: str = "parent_keyed_select"

    def __post_init__(self) -> None:
        if len(self.path) != 1:
            raise ValueError("Attached projection rows require a top-level attachment path")

    @property
    def attachment_key(self) -> str:
        return self.path[0]

    def child_table(self, parent_table: str) -> ProjectionTable:
        columns = (ProjectionColumn(self.parent_fk, "TEXT", nullable=False),) + tuple(
            field.column_spec() for field in self.fields
        )
        return ProjectionTable(
            self.table,
            columns,
            foreign_keys=(ProjectionForeignKey((self.parent_fk,), parent_table, ("id",)),),
        )

    def encode_rows(self, source: object) -> tuple[ProjectionRow, ...]:
        parent_value = _read_path(source, self.parent_path, default=_MISSING)
        if parent_value is _MISSING:
            raise KeyError(".".join(self.parent_path))
        values = _read_path(source, self.path, default=())
        if values is None:
            values = ()
        rows: list[ProjectionRow] = []
        for item in values:
            row_values = {self.parent_fk: parent_value}
            for field in self.fields:
                row_values[_column_name_for(field)] = field.encode_value(item)
            rows.append(ProjectionRow(self.table, row_values))
        return tuple(rows)

    def decode_rows(self, raw_rows: object) -> tuple[Any, ...]:
        if raw_rows is None:
            return ()
        if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, str):
            raise TypeError(f"Attached rows {'.'.join(self.path)} expects a row sequence")
        decoded: list[Any] = []
        for raw_row in raw_rows:
            row_map = raw_row.values if isinstance(raw_row, ProjectionRow) else raw_row
            if not isinstance(row_map, Mapping):
                raise TypeError("Repeated child row must be a mapping or ProjectionRow")
            item_data: dict[str, Any] = {}
            if self.item_parent_path is not None:
                if self.parent_fk not in row_map:
                    raise KeyError(self.parent_fk)
                _assign_path(item_data, self.item_parent_path, row_map[self.parent_fk])
            for field in self.fields:
                column = _decode_column_for(field, row_map)
                raw_value = row_map.get(column) if column is not None else None
                _assign_path(item_data, field.path, field.decode_value(raw_value))
            decoded.append(_construct(self.item_type, item_data) if self.item_type is not None else item_data)
        return tuple(decoded)

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": "ProjectionAttachedRows",
            "path": self.path,
            "table": self.table,
            "parent_fk": self.parent_fk,
            "parent_path": self.parent_path,
            "item_parent_path": self.item_parent_path,
            "order_by": tuple(_attached_order_material(order_key) for order_key in self.order_by),
            "fetch": self.fetch,
            "fields": tuple(_stable_field_material(field) for field in self.fields),
        }


ProjectionSpec = ProjectionPath | ProjectionComponent | ProjectionMetadata | ProjectionAttachedRows | ProjectionRenderView | ProjectionInputKey


@dataclass(frozen=True)
class ProjectionSelectedColumn:
    source_alias: str
    column: ProjectionColumn
    read_name: str | None = None

    @classmethod
    def from_binding(cls, source_alias: str, binding: ProjectionBinding) -> ProjectionSelectedColumn:
        return cls(
            source_alias=source_alias,
            column=binding.projection_column,
            read_name=binding.read_name,
        )

    @property
    def output_name(self) -> str:
        return self.column.name if self.read_name is None else self.read_name

    def select_sql(self) -> str:
        expression = f"{quote_identifier(self.source_alias)}.{quote_identifier(self.column.name)}"
        if self.output_name == self.column.name:
            return expression
        return f"{expression} AS {quote_identifier(self.output_name)}"

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": "ProjectionSelectedColumn",
            "source_alias": self.source_alias,
            "column": self.column.name,
            "read_name": self.read_name,
        }


@dataclass(frozen=True)
class ProjectionJoin:
    table: ProjectionTable
    alias: str
    left_alias: str
    left_column: ProjectionColumn
    right_column: ProjectionColumn
    kind: str = "LEFT"

    def join_sql(self) -> str:
        join_kind = self.kind.upper()
        if join_kind not in {"INNER", "LEFT", "LEFT OUTER", "CROSS"}:
            raise ValueError(f"Unsupported projection join kind: {self.kind!r}")
        if join_kind == "CROSS":
            return f"CROSS JOIN {quote_identifier(self.table.name)} AS {quote_identifier(self.alias)}"
        left = f"{quote_identifier(self.alias)}.{quote_identifier(self.right_column.name)}"
        right = f"{quote_identifier(self.left_alias)}.{quote_identifier(self.left_column.name)}"
        return (
            f"{join_kind} JOIN {quote_identifier(self.table.name)} AS {quote_identifier(self.alias)} "
            f"ON {left} = {right}"
        )

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": "ProjectionJoin",
            "table": self.table.name,
            "alias": self.alias,
            "left_alias": self.left_alias,
            "left_column": self.left_column.name,
            "right_column": self.right_column.name,
            "join_kind": self.kind.upper(),
        }


@dataclass(frozen=True)
class ProjectionDiscriminator:
    column: ProjectionColumn
    value: object

    def predicate_sql(self, source_alias: str) -> str:
        expression = f"{quote_identifier(source_alias)}.{quote_identifier(self.column.name)}"
        return f"{expression} = {_sql_literal(self.column.encode(self.value))}"

    def row_values(self) -> Mapping[str, object]:
        return {self.column.name: self.column.encode(self.value)}

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": "ProjectionDiscriminator",
            "column": self.column.name,
            "value": self.column.encode(self.value),
        }


@dataclass(frozen=True)
class ProjectionQueryPlan:
    name: str
    base_table: ProjectionTable
    base_alias: str
    selections: tuple[ProjectionSelectedColumn, ...]
    joins: tuple[ProjectionJoin, ...] = ()
    discriminators: tuple[ProjectionDiscriminator, ...] = ()
    order_by: tuple[str, ...] = ()

    def select_sql(self, where_sql: str = "") -> str:
        if not self.selections:
            raise ValueError(f"Projection query plan {self.name!r} must declare selections")
        select_columns = ",\n            ".join(selection.select_sql() for selection in self.selections)
        join_sql = "\n        ".join(join.join_sql() for join in self.joins)
        sql = (
            "SELECT\n"
            f"            {select_columns}\n"
            f"        FROM {quote_identifier(self.base_table.name)} AS {quote_identifier(self.base_alias)}"
        )
        if join_sql:
            sql = f"{sql}\n        {join_sql}"
        discriminator_sql = " AND ".join(
            discriminator.predicate_sql(self.base_alias)
            for discriminator in self.discriminators
        )
        if where_sql:
            if discriminator_sql and where_sql.strip().upper().startswith("WHERE "):
                sql = f"{sql} WHERE {discriminator_sql} AND {where_sql.strip()[6:]}"
            elif discriminator_sql:
                sql = f"{sql} WHERE {discriminator_sql} {where_sql}"
            else:
                sql = f"{sql} {where_sql}"
        elif discriminator_sql:
            sql = f"{sql} WHERE {discriminator_sql}"
        if self.order_by and not where_sql:
            sql = f"{sql} ORDER BY {', '.join(self.order_by)}"
        return sql

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": "ProjectionQueryPlan",
            "name": self.name,
            "base_table": self.base_table.name,
            "base_alias": self.base_alias,
            "selections": tuple(selection.schema_hash_material() for selection in self.selections),
            "joins": tuple(join.schema_hash_material() for join in self.joins),
            "discriminators": tuple(
                discriminator.schema_hash_material()
                for discriminator in self.discriminators
            ),
            "order_by": self.order_by,
        }


@dataclass(frozen=True)
class ProjectionModel(Generic[ResultT]):
    name: str
    table: str
    result_type: type[ResultT]
    fields: tuple[ProjectionSpec, ...]
    primary_key: tuple[str, ...] = ()
    indexes: tuple[ProjectionIndex, ...] = ()
    checks: tuple[str, ...] = ()
    if_not_exists: bool = False

    def to_row(self, source: object) -> Mapping[str, object]:
        row: dict[str, object] = {}
        for field in self.fields:
            if isinstance(field, ProjectionAttachedRows | ProjectionRenderView | ProjectionInputKey):
                continue
            if isinstance(field, ProjectionComponent):
                row.update(field.encode_values(source))
                continue
            if isinstance(field, ProjectionMetadata):
                row.update(field.encode_values(source))
                continue
            row[_column_name_for(field)] = field.encode_value(source)
        return row

    def to_mapping(self, source: object) -> Mapping[str, object]:
        row = dict(self.to_row(source))
        for field in self.fields:
            if isinstance(field, ProjectionRenderView):
                row[field.output_key] = field.encode_value(source)
        return row

    def from_row(self, row: Mapping[str, object]) -> ResultT:
        known = {
            column
            for field in self.fields
            if not isinstance(field, ProjectionAttachedRows | ProjectionRenderView | ProjectionInputKey | ProjectionComponent | ProjectionMetadata)
            for column in _read_names_for(field)
        }
        known.update(
            decoded_column
            for field in self.fields
            if isinstance(field, ProjectionComponent)
            for column in field.bindings
            for decoded_column in _read_names_for(column)
        )
        known.update(
            decoded_column
            for field in self.fields
            if isinstance(field, ProjectionMetadata)
            for metadata_field in field.fields
            for decoded_column in _read_names_for(metadata_field)
        )
        known.update(
            key
            for field in self.fields
            if isinstance(field, ProjectionAttachedRows)
            for key in (field.attachment_key,)
        )
        known.update(field.output_key for field in self.fields if isinstance(field, ProjectionRenderView))
        known.update(field.key for field in self.fields if isinstance(field, ProjectionInputKey))
        extras = {key: value for key, value in row.items() if key not in known}
        if extras:
            raise KeyError(f"Unknown projection row key(s): {', '.join(sorted(extras))}")

        data: dict[str, Any] = {}
        for field in self.fields:
            if isinstance(field, ProjectionAttachedRows):
                _assign_path(data, field.path, field.decode_rows(row.get(field.attachment_key)))
            elif isinstance(field, ProjectionComponent):
                _assign_path(data, field.path, field.decode_value(row))
            elif isinstance(field, ProjectionMetadata):
                metadata_value = field.decode_value(row)
                if field.path:
                    _assign_path(data, field.path, metadata_value)
                elif isinstance(metadata_value, Mapping):
                    data.update(metadata_value)
                else:
                    raise TypeError("Top-level projection metadata must decode to a mapping")
            elif isinstance(field, ProjectionRenderView | ProjectionInputKey):
                continue
            elif (column := _decode_column_for(field, row)) is not None:
                _assign_path(data, field.path, field.decode_value(row[column]))
            elif field.missing == "raise":
                raise KeyError(_column_name_for(field))
            else:
                _assign_path(data, field.path, field.default)
        return _construct(self.result_type, data)

    def coerce(self, value: object) -> ResultT:
        if isinstance(value, self.result_type):
            return value
        if not isinstance(value, Mapping):
            expected = f"{self.result_type.__name__} or mapping"
            raise TypeError(f"{self.name} projection expects {expected}")
        return self.from_row(value)

    def child_rows(self, source: object) -> tuple[ProjectionRow, ...]:
        rows: list[ProjectionRow] = []
        for field in self.fields:
            if isinstance(field, ProjectionAttachedRows):
                rows.extend(field.encode_rows(source))
        return tuple(rows)

    def attach_child_rows(
        self,
        parent_rows: Sequence[Mapping[str, object]],
        child_rows_by_table: Mapping[str, Sequence[Mapping[str, object] | ProjectionRow]],
    ) -> tuple[Mapping[str, object], ...]:
        rows = [dict(parent_row) for parent_row in parent_rows]
        for field in self.fields:
            if not isinstance(field, ProjectionAttachedRows):
                continue
            parent_column = _parent_column_for_path(self.fields, field.parent_path)
            grouped = _group_attached_rows(field, child_rows_by_table.get(field.table, ()))
            for row in rows:
                if parent_column not in row:
                    raise KeyError(parent_column)
                parent_value = row[parent_column]
                if not isinstance(parent_value, Hashable):
                    raise TypeError(f"Attached row parent key {parent_column} must be hashable")
                row[field.attachment_key] = tuple(grouped.get(parent_value, ()))
        return tuple(rows)

    def attached_rows_select_sql(
        self,
        field: ProjectionAttachedRows,
        parent_count: int,
    ) -> str:
        if field not in self.fields:
            raise KeyError(field.attachment_key)
        if field.fetch != "parent_keyed_select":
            raise ValueError(f"Unsupported attached-row fetch mode: {field.fetch!r}")
        if parent_count < 1:
            raise ValueError("attached row select requires at least one parent key")
        columns = (field.parent_fk,) + tuple(_column_name_for(child_field) for child_field in field.fields)
        select_columns = ", ".join(quote_identifier(column) for column in columns)
        placeholders = ", ".join("?" for _ in range(parent_count))
        order_columns = (field.parent_fk,) + tuple(
            order_key if isinstance(order_key, str) else _column_name_for(order_key)
            for order_key in field.order_by
        )
        order_sql = ", ".join(quote_identifier(column) for column in order_columns)
        return (
            f"SELECT {select_columns} FROM {quote_identifier(field.table)} "
            f"WHERE {quote_identifier(field.parent_fk)} IN ({placeholders}) "
            f"ORDER BY {order_sql}"
        )

    def select_with_attached_rows(
        self,
        conn: sqlite3.Connection,
        query_plan: ProjectionQueryPlan,
        where_sql: str = "",
        params: Sequence[object] = (),
    ) -> tuple[ResultT, ...]:
        conn.row_factory = sqlite3.Row
        parent_rows = tuple(
            _row_mapping(row)
            for row in conn.execute(query_plan.select_sql(where_sql), tuple(params)).fetchall()
        )
        if not parent_rows:
            return ()
        child_rows_by_table: dict[str, tuple[Mapping[str, object], ...]] = {}
        for field in self.fields:
            if not isinstance(field, ProjectionAttachedRows):
                continue
            parent_column = _parent_column_for_path(self.fields, field.parent_path)
            parent_values = tuple(row[parent_column] for row in parent_rows)
            child_rows = conn.execute(
                self.attached_rows_select_sql(field, len(parent_values)),
                parent_values,
            ).fetchall()
            child_rows_by_table[field.table] = tuple(_row_mapping(row) for row in child_rows)
        attached_rows = self.attach_child_rows(parent_rows, child_rows_by_table)
        return tuple(self.from_row(row) for row in attached_rows)

    def projection_tables(self) -> tuple[ProjectionTable, ...]:
        columns = tuple(
            field.column_spec()
            for field in self.fields
            if not isinstance(field, ProjectionAttachedRows | ProjectionRenderView | ProjectionInputKey | ProjectionComponent | ProjectionMetadata)
        ) + tuple(
            column.column_spec()
            for field in self.fields
            if isinstance(field, ProjectionComponent)
            for column in field.bindings
        ) + tuple(
            metadata_field.column_spec()
            for field in self.fields
            if isinstance(field, ProjectionMetadata)
            for metadata_field in field.fields
        )
        foreign_keys = tuple(
            field.foreign_key()
            for field in self.fields
            if isinstance(field, ReferencePath)
        )
        indexes = self.indexes + tuple(
            ProjectionIndex(f"idx_{self.table}_{field.column}", (field.column,))
            for field in self.fields
            if isinstance(field, ScalarPath) and field.indexed
        )
        tables = [
            ProjectionTable(
                self.table,
                columns,
                primary_key=self.primary_key,
                foreign_keys=foreign_keys,
                indexes=indexes,
                checks=self.checks,
                if_not_exists=self.if_not_exists,
                row_factory=self.from_row,
            )
        ]
        for field in self.fields:
            if isinstance(field, ProjectionAttachedRows):
                tables.append(field.child_table(self.table))
        return tuple(tables)

    def schema_hash_material(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "table": self.table,
            "result_type": f"{self.result_type.__module__}.{self.result_type.__qualname__}",
            "primary_key": self.primary_key,
            "indexes": tuple(index.schema_hash_material() for index in self.indexes),
            "checks": self.checks,
            "if_not_exists": self.if_not_exists,
            "fields": tuple(
                sorted(
                    (_stable_field_material(field) for field in self.fields),
                    key=lambda item: json.dumps(item, sort_keys=True, default=str),
                )
            ),
        }


def _stable_field_material(field: ProjectionSpec) -> Mapping[str, Any]:
    return dict(field.schema_hash_material())


def _column_name_for(field: ProjectionPath) -> str:
    if isinstance(field, ProjectionBinding):
        return field.column_name
    return field.column


def _encode_projection_value(field: ProjectionPath, value: Any) -> Any:
    if isinstance(field, ProjectionBinding):
        return field.projection_column.encode(value)
    return field.codec.encode(value)


def _read_names_for(field: ProjectionPath) -> tuple[str, ...]:
    if isinstance(field, ProjectionBinding):
        if field.read_name is None or field.read_name == field.column_name:
            return (field.column_name,)
        return (field.column_name, field.read_name)
    return (field.column,)


def _decode_column_for(field: ProjectionPath, row: Mapping[str, object]) -> str | None:
    for column in _read_names_for(field):
        if column in row:
            return column
    return None


def _attached_order_material(order_key: str | ProjectionPath) -> Mapping[str, Any]:
    if isinstance(order_key, str):
        return {"kind": "column", "name": order_key}
    return _stable_field_material(order_key)


def _parent_column_for_path(fields: tuple[ProjectionSpec, ...], path: tuple[str, ...]) -> str:
    for field in fields:
        if isinstance(field, ProjectionComponent):
            for binding in field.bindings:
                if binding.path == path:
                    return _column_name_for(binding)
        elif isinstance(field, ScalarPath | ProjectionBinding) and field.path == path:
            return _column_name_for(field)
    raise KeyError(".".join(path))


def _group_attached_rows(
    field: ProjectionAttachedRows,
    raw_rows: Sequence[Mapping[str, object] | ProjectionRow],
) -> dict[Hashable, list[Mapping[str, object]]]:
    grouped: dict[Hashable, list[Mapping[str, object]]] = {}
    for raw_row in raw_rows:
        row_map = raw_row.values if isinstance(raw_row, ProjectionRow) else raw_row
        if not isinstance(row_map, Mapping):
            raise TypeError("Attached child row must be a mapping or ProjectionRow")
        if field.parent_fk not in row_map:
            raise KeyError(field.parent_fk)
        parent_value = row_map[field.parent_fk]
        if not isinstance(parent_value, Hashable):
            raise TypeError(f"Attached row child key {field.parent_fk} must be hashable")
        grouped.setdefault(parent_value, []).append(row_map)
    if field.order_by:
        for rows in grouped.values():
            rows.sort(key=lambda row: _attached_order_values(field, row))
    return grouped


def _attached_order_values(field: ProjectionAttachedRows, row: Mapping[str, object]) -> tuple[object, ...]:
    values: list[object] = []
    for order_key in field.order_by:
        column = order_key if isinstance(order_key, str) else _column_name_for(order_key)
        if column not in row:
            raise KeyError(column)
        values.append(row[column])
    return tuple(values)


def _row_mapping(row: sqlite3.Row | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return row


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _read_path(source: object, path: tuple[str, ...], *, default: Any = None) -> Any:
    current = source
    for part in path:
        if isinstance(current, Mapping):
            if part not in current:
                return default
            current = current[part]
        else:
            if not hasattr(current, part):
                return default
            current = getattr(current, part)
    return current


def _assign_path(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = target
    for part in path[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[path[-1]] = value


def _construct(result_type: type[ResultT], data: Mapping[str, Any]) -> ResultT:
    if not is_dataclass(result_type):
        return result_type(**data)
    hints = get_type_hints(result_type)
    kwargs: dict[str, Any] = {}
    for field in fields(result_type):
        if field.name in data:
            kwargs[field.name] = _coerce_value(hints.get(field.name), data[field.name])
        elif field.default is not MISSING or field.default_factory is not MISSING:
            continue
        else:
            kwargs[field.name] = None
    return result_type(**kwargs)


def _coerce_value(annotation: Any, value: Any) -> Any:
    if value is None or annotation is None:
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is tuple and args and isinstance(value, tuple):
        item_type = args[0]
        if item_type is Ellipsis:
            return value
        return tuple(_coerce_value(item_type, item) for item in value)
    candidates = [arg for arg in args if isinstance(arg, type)] if origin is not None else []
    if isinstance(annotation, type):
        candidates.append(annotation)
    for candidate in candidates:
        if is_dataclass(candidate) and isinstance(value, Mapping):
            return _construct(candidate, value)
    return value

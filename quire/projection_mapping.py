from __future__ import annotations

import json
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
class ProjectionAttachedRows:
    path: tuple[str, ...]
    table: str
    fields: tuple[ProjectionPath, ...]
    parent_fk: str
    parent_path: tuple[str, ...] = ("id",)
    item_parent_path: tuple[str, ...] | None = None
    item_type: type[Any] | None = None
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
            "fetch": self.fetch,
            "fields": tuple(_stable_field_material(field) for field in self.fields),
        }


ProjectionSpec = ProjectionPath | ProjectionComponent | ProjectionAttachedRows | ProjectionRenderView


@dataclass(frozen=True)
class ProjectionModel(Generic[ResultT]):
    name: str
    table: str
    result_type: type[ResultT]
    fields: tuple[ProjectionSpec, ...]
    attribute_bucket: tuple[str, ...] | None = None
    ignored_columns: tuple[str, ...] = ()
    primary_key: tuple[str, ...] = ()
    indexes: tuple[ProjectionIndex, ...] = ()
    checks: tuple[str, ...] = ()
    if_not_exists: bool = False

    def to_row(self, source: object) -> Mapping[str, object]:
        row: dict[str, object] = {}
        for field in self.fields:
            if isinstance(field, ProjectionAttachedRows | ProjectionRenderView):
                continue
            if isinstance(field, ProjectionComponent):
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
            if not isinstance(field, ProjectionAttachedRows | ProjectionRenderView | ProjectionComponent)
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
            key
            for field in self.fields
            if isinstance(field, ProjectionAttachedRows)
            for key in (field.attachment_key,)
        )
        known.update(field.output_key for field in self.fields if isinstance(field, ProjectionRenderView))
        ignored = set(self.ignored_columns)
        extras = {key: value for key, value in row.items() if key not in known and key not in ignored}
        if extras and self.attribute_bucket is None:
            raise KeyError(f"Unknown projection row key(s): {', '.join(sorted(extras))}")

        data: dict[str, Any] = {}
        for field in self.fields:
            if isinstance(field, ProjectionAttachedRows):
                _assign_path(data, field.path, field.decode_rows(row.get(field.attachment_key)))
            elif isinstance(field, ProjectionComponent):
                _assign_path(data, field.path, field.decode_value(row))
            elif isinstance(field, ProjectionRenderView):
                continue
            elif (column := _decode_column_for(field, row)) is not None:
                _assign_path(data, field.path, field.decode_value(row[column]))
            elif field.missing == "raise":
                raise KeyError(_column_name_for(field))
            else:
                _assign_path(data, field.path, field.default)
        if extras and self.attribute_bucket is not None:
            _assign_path(data, self.attribute_bucket, extras)
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

    def projection_tables(self) -> tuple[ProjectionTable, ...]:
        columns = tuple(
            field.column_spec()
            for field in self.fields
            if not isinstance(field, ProjectionAttachedRows | ProjectionRenderView | ProjectionComponent)
        ) + tuple(
            column.column_spec()
            for field in self.fields
            if isinstance(field, ProjectionComponent)
            for column in field.bindings
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
            "attribute_bucket": self.attribute_bucket,
            "ignored_columns": tuple(sorted(self.ignored_columns)),
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
        return (field.read_name, field.column_name)
    return (field.column,)


def _decode_column_for(field: ProjectionPath, row: Mapping[str, object]) -> str | None:
    for column in _read_names_for(field):
        if column in row:
            return column
    return None


def _parent_column_for_path(fields: tuple[ProjectionSpec, ...], path: tuple[str, ...]) -> str:
    for field in fields:
        if isinstance(field, ProjectionComponent):
            for binding in field.bindings:
                if binding.path == path:
                    return _column_name_for(binding)
        elif not isinstance(field, ProjectionAttachedRows | ProjectionRenderView) and field.path == path:
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
    return grouped


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

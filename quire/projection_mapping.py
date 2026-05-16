from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Generic, TypeVar, cast, get_args, get_origin, get_type_hints

from quire.projections import (
    ProjectionColumn,
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
class ScalarPath:
    path: tuple[str, ...]
    column: str
    codec: ProjectionCodec = SCALAR_CODEC
    nullable: bool = True
    primary_key: bool = False
    indexed: bool = False
    default: Any = None
    missing: str = "none"

    def column_spec(self) -> ProjectionColumn:
        return ProjectionColumn(
            self.column,
            self.codec.sql_type,
            nullable=self.nullable,
            primary_key=self.primary_key,
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
            "indexed": self.indexed,
            "missing": self.missing,
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
class DerivedPath:
    path: tuple[str, ...]
    key: str
    codec: ProjectionCodec = SCALAR_CODEC
    default: Any = None
    missing: str = "none"

    def encode_value(self, source: object) -> Any:
        value = _read_path(source, self.path, default=_MISSING)
        if value is _MISSING:
            if self.missing == "raise":
                raise KeyError(".".join(self.path))
            value = self.default
        return self.codec.encode(value)

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": type(self).__name__,
            "path": self.path,
            "key": self.key,
            "missing": self.missing,
            "codec": self.codec.schema_hash_material(),
        }


ProjectionPath = ScalarPath | JsonPath | EnumPath | ReferencePath


@dataclass(frozen=True)
class RepeatedPath:
    path: tuple[str, ...]
    table: str
    fields: tuple[ProjectionPath, ...]
    parent_fk: str
    parent_path: tuple[str, ...] = ("id",)
    item_type: type[Any] | None = None
    fetch: str = "parent_keyed_select"

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
                row_values[field.column] = field.encode_value(item)
            rows.append(ProjectionRow(self.table, row_values))
        return tuple(rows)

    def decode_rows(self, raw_rows: object) -> tuple[Any, ...]:
        if raw_rows is None:
            return ()
        if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, str):
            raise TypeError(f"Repeated path {'.'.join(self.path)} expects a row sequence")
        decoded: list[Any] = []
        for raw_row in raw_rows:
            row_map = raw_row.values if isinstance(raw_row, ProjectionRow) else raw_row
            if not isinstance(row_map, Mapping):
                raise TypeError("Repeated child row must be a mapping or ProjectionRow")
            item_data: dict[str, Any] = {}
            for field in self.fields:
                _assign_path(item_data, field.path, field.decode_value(row_map.get(field.column)))
            decoded.append(_construct(self.item_type, item_data) if self.item_type is not None else item_data)
        return tuple(decoded)

    def schema_hash_material(self) -> Mapping[str, Any]:
        return {
            "kind": "RepeatedPath",
            "path": self.path,
            "table": self.table,
            "parent_fk": self.parent_fk,
            "parent_path": self.parent_path,
            "fetch": self.fetch,
            "fields": tuple(_stable_field_material(field) for field in self.fields),
        }


ProjectionSpec = ProjectionPath | RepeatedPath | DerivedPath


@dataclass(frozen=True)
class ProjectionModel(Generic[ResultT]):
    name: str
    table: str
    result_type: type[ResultT] | None
    fields: tuple[ProjectionSpec, ...]
    attribute_bucket: tuple[str, ...] | None = None
    primary_key: tuple[str, ...] = ()

    def to_row(self, source: object) -> Mapping[str, object]:
        row: dict[str, object] = {}
        for field in self.fields:
            if isinstance(field, RepeatedPath | DerivedPath):
                continue
            row[field.column] = field.encode_value(source)
        return row

    def to_mapping(self, source: object) -> Mapping[str, object]:
        row = dict(self.to_row(source))
        for field in self.fields:
            if isinstance(field, DerivedPath):
                row[field.key] = field.encode_value(source)
        return row

    def from_row(self, row: Mapping[str, object]) -> ResultT:
        known = {
            field.column
            for field in self.fields
            if not isinstance(field, RepeatedPath | DerivedPath)
        }
        known.update(field.table for field in self.fields if isinstance(field, RepeatedPath))
        known.update(field.key for field in self.fields if isinstance(field, DerivedPath))
        extras = {key: value for key, value in row.items() if key not in known}
        if extras and self.attribute_bucket is None:
            raise KeyError(f"Unknown projection row key(s): {', '.join(sorted(extras))}")

        data: dict[str, Any] = {}
        for field in self.fields:
            if isinstance(field, RepeatedPath):
                _assign_path(data, field.path, field.decode_rows(row.get(field.table)))
            elif isinstance(field, DerivedPath):
                continue
            elif field.column in row:
                _assign_path(data, field.path, field.decode_value(row[field.column]))
            elif field.missing == "raise":
                raise KeyError(field.column)
            else:
                _assign_path(data, field.path, field.default)
        if extras and self.attribute_bucket is not None:
            _assign_path(data, self.attribute_bucket, extras)
        return cast(ResultT, _construct(self.result_type, data))

    def coerce(self, value: object) -> ResultT:
        if self.result_type is not None and isinstance(value, self.result_type):
            return value
        if not isinstance(value, Mapping):
            expected = (
                "mapping"
                if self.result_type is None
                else f"{self.result_type.__name__} or mapping"
            )
            raise TypeError(f"{self.name} projection expects {expected}")
        return self.from_row(value)

    def child_rows(self, source: object) -> tuple[ProjectionRow, ...]:
        rows: list[ProjectionRow] = []
        for field in self.fields:
            if isinstance(field, RepeatedPath):
                rows.extend(field.encode_rows(source))
        return tuple(rows)

    def projection_tables(self) -> tuple[ProjectionTable, ...]:
        columns = tuple(
            field.column_spec()
            for field in self.fields
            if not isinstance(field, RepeatedPath | DerivedPath)
        )
        foreign_keys = tuple(
            field.foreign_key()
            for field in self.fields
            if isinstance(field, ReferencePath)
        )
        indexes = tuple(
            ProjectionIndex(f"idx_{self.table}_{field.column}", (field.column,))
            for field in self.fields
            if not isinstance(field, RepeatedPath | DerivedPath) and field.indexed
        )
        tables = [
            ProjectionTable(
                self.table,
                columns,
                primary_key=self.primary_key,
                foreign_keys=foreign_keys,
                indexes=indexes,
                row_factory=self.from_row,
            )
        ]
        for field in self.fields:
            if isinstance(field, RepeatedPath):
                tables.append(field.child_table(self.table))
        return tuple(tables)

    def schema_hash_material(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "table": self.table,
            "result_type": None if self.result_type is None else f"{self.result_type.__module__}.{self.result_type.__qualname__}",
            "attribute_bucket": self.attribute_bucket,
            "primary_key": self.primary_key,
            "fields": tuple(
                sorted(
                    (_stable_field_material(field) for field in self.fields),
                    key=lambda item: json.dumps(item, sort_keys=True, default=str),
                )
            ),
        }


def _stable_field_material(field: ProjectionSpec) -> Mapping[str, Any]:
    return dict(field.schema_hash_material())


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


def _construct(result_type: type[Any] | None, data: Mapping[str, Any]) -> Any:
    if result_type is None:
        return dict(data)
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

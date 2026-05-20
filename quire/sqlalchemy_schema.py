from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, cast

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import clear_mappers, registry, relationship
from sqlalchemy.sql.type_api import TypeEngine
from sqlalchemy.types import TypeDecorator

from quire.schema_catalog import SchemaCatalog
from quire.schema_ir import SchemaField, SchemaObject
from quire.sql_types import SqlTypeSpec

__all__ = [
    "EnumText",
    "JsonValueObject",
    "SqlAlchemySchema",
    "build_sqlalchemy_schema",
]


class EnumText(TypeDecorator[Any]):
    impl = Text
    cache_ok = True

    def __init__(self, enum_type: type[Enum]) -> None:
        super().__init__()
        self.enum_type = enum_type

    def process_bind_param(self, value: object, dialect: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_type):
            return str(value.value)
        return str(value)

    def process_result_value(self, value: object, dialect: object) -> object:
        if value is None:
            return None
        return self.enum_type(value)


class JsonValueObject(TypeDecorator[Any]):
    impl = Text
    cache_ok = True

    def __init__(self, value_type: type[Any]) -> None:
        super().__init__()
        self.value_type = value_type

    def process_bind_param(self, value: object, dialect: object) -> str | None:
        if value is None:
            return None
        return json.dumps(_json_payload(value), sort_keys=True, separators=(",", ":"))

    def process_result_value(self, value: object, dialect: object) -> object:
        if value is None:
            return None
        payload = json.loads(str(value))
        if isinstance(payload, Mapping):
            return self.value_type(**payload)
        return payload


@dataclass(frozen=True)
class SqlAlchemySchema:
    catalog: SchemaCatalog
    metadata: MetaData
    mapper_registry: registry
    tables: Mapping[str, Table]
    models_by_family: Mapping[str, type[Any]]
    catalog_hash: str

    def table(self, family_name: str) -> Table:
        try:
            return self.tables[family_name]
        except KeyError as exc:
            raise KeyError(f"unknown SQLAlchemy schema table {family_name!r}") from exc

    def model(self, family_name: str) -> type[Any]:
        try:
            return self.models_by_family[family_name]
        except KeyError as exc:
            raise KeyError(f"unknown SQLAlchemy schema model {family_name!r}") from exc


def build_sqlalchemy_schema(catalog: SchemaCatalog) -> SqlAlchemySchema:
    clear_mappers()
    metadata = MetaData()
    mapper_registry = registry(metadata=metadata)
    tables = {
        schema_object.family_name: _table_from_schema_object(metadata, schema_object)
        for schema_object in catalog.objects
    }
    models_by_family = {
        schema_object.family_name: _load_type(schema_object.model_path)
        for schema_object in catalog.objects
    }
    schema = SqlAlchemySchema(
        catalog=catalog,
        metadata=metadata,
        mapper_registry=mapper_registry,
        tables=MappingProxyType(tables),
        models_by_family=MappingProxyType(models_by_family),
        catalog_hash=catalog.schema_hash(),
    )
    _map_models(schema)
    return schema


def _table_from_schema_object(metadata: MetaData, schema_object: SchemaObject) -> Table:
    columns = tuple(_column_from_schema_field(schema_object, field) for field in schema_object.fields)
    table_args: list[Any] = [
        UniqueConstraint(*index.fields, name=index.name)
        for index in schema_object.indexes
        if index.unique
    ]
    table = Table(schema_object.family_name, metadata, *columns, *table_args)
    for field in schema_object.fields:
        if field.index:
            Index(f"ix_{schema_object.family_name}_{field.name}", table.c[field.name])
        if field.unique:
            Index(
                f"ux_{schema_object.family_name}_{field.name}",
                table.c[field.name],
                unique=True,
            )
    for index in schema_object.indexes:
        if not index.unique:
            Index(index.name, *(table.c[field] for field in index.fields))
    return table


def _column_from_schema_field(schema_object: SchemaObject, field: SchemaField) -> Column[Any]:
    foreign_key = (
        None
        if field.foreign_key is None
        else ForeignKey(
            f"{field.foreign_key.target_family}.{field.foreign_key.target_field}",
            name=f"fk_{field.foreign_key.name}",
        )
    )
    args: list[Any] = []
    if foreign_key is not None:
        args.append(foreign_key)
    kwargs: dict[str, Any] = {
        "nullable": field.nullable,
        "primary_key": field.primary_key,
        "info": _column_info(schema_object, field),
    }
    if field.default is not None:
        kwargs["default"] = field.default
    if field.default_sql is not None:
        kwargs["server_default"] = text(field.default_sql)
    return Column(field.name, _sqlalchemy_type(field), *args, **kwargs)


def _sqlalchemy_type(field: SchemaField) -> TypeEngine[Any]:
    sql_type = cast(SqlTypeSpec, field.sql_type)
    if sql_type.sqlalchemy_type == "Text":
        return Text()
    if sql_type.sqlalchemy_type == "Integer":
        return Integer()
    if sql_type.sqlalchemy_type == "Float":
        return Float()
    if sql_type.sqlalchemy_type == "Boolean":
        return Boolean()
    if sql_type.sqlalchemy_type == "LargeBinary":
        return LargeBinary()
    if sql_type.sqlalchemy_type == "EnumText":
        enum_type = cast(type[Enum], _load_type(field.python_type))
        return EnumText(enum_type)
    if sql_type.sqlalchemy_type == "JsonValueObject":
        value_type_path = sql_type.value_object_type or field.python_type
        return JsonValueObject(_load_type(value_type_path))
    return Text()


def _column_info(schema_object: SchemaObject, field: SchemaField) -> dict[str, object]:
    return {
        "canonical_only": field.canonical_only,
        "family": schema_object.family_name,
        "python_type": field.python_type,
        "search": field.search,
        "semantic_metadata": dict(field.metadata),
        "source_local_only": field.source_local_only,
        "vector_dimensions": field.vector_dimensions,
    }


def _map_models(schema: SqlAlchemySchema) -> None:
    for schema_object in schema.catalog.objects:
        table = schema.table(schema_object.family_name)
        model = schema.model(schema_object.family_name)
        properties: dict[str, Any] = {}
        for rel in schema_object.relationships:
            target_model = schema.model(rel.target_family)
            target_table = schema.table(rel.target_family)
            relationship_kwargs: dict[str, Any] = {
                "uselist": rel.uselist,
                "lazy": "selectin",
            }
            if rel.back_populates is not None:
                relationship_kwargs["back_populates"] = rel.back_populates
            foreign_key_column = _relationship_foreign_key_column(
                table,
                target_table,
                rel.foreign_key,
            )
            if foreign_key_column is not None:
                relationship_kwargs["foreign_keys"] = (foreign_key_column,)
            properties[rel.name] = relationship(target_model, **relationship_kwargs)
        schema.mapper_registry.map_imperatively(model, table, properties=properties)


def _relationship_foreign_key_column(
    source_table: Table,
    target_table: Table,
    foreign_key_name: str | None,
) -> Column[Any] | None:
    if foreign_key_name is None:
        return None
    if foreign_key_name in source_table.c:
        return source_table.c[foreign_key_name]
    if foreign_key_name in target_table.c:
        return target_table.c[foreign_key_name]
    return None


def _load_type(path: str) -> type[Any]:
    module_name, _, qualname = path.partition(".")
    parts = path.split(".")
    for split_at in range(len(parts), 0, -1):
        candidate_module = ".".join(parts[:split_at])
        try:
            module = importlib.import_module(candidate_module)
        except ModuleNotFoundError:
            continue
        value: object = module
        for attr in parts[split_at:]:
            value = getattr(value, attr)
        if isinstance(value, type):
            return value
    raise ImportError(f"cannot import type {path!r}")


def _json_payload(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return value

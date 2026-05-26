from __future__ import annotations

import importlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from functools import cache
from types import MappingProxyType
from typing import Any, cast

import msgspec
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
    select,
    text,
)
from sqlalchemy.orm import clear_mappers, registry, relationship
from sqlalchemy.sql.type_api import TypeEngine
from sqlalchemy.types import TypeDecorator
from sqlalchemy_fts5 import FTS5Table

from quire.schema_catalog import SchemaCatalog
from quire.schema_ir import SchemaField, SchemaFtsIndex, SchemaObject, SchemaVectorCache
from quire.references import FamilyReferenceIndex, MissingReferenceError, ReferenceKey
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


@cache
def _make_json_type_decorator(python_type: object) -> type[TypeDecorator[Any]]:
    class JsonBoundary(TypeDecorator[Any]):
        impl = Text
        cache_ok = True

        def process_bind_param(self, value: object, dialect: object) -> str | None:
            if value is None:
                return None
            return msgspec.json.encode(value).decode("utf-8")

        def process_result_value(self, value: object, dialect: object) -> object:
            if value is None:
                return None
            return msgspec.json.decode(cast(Any, value), type=cast(Any, python_type))

    JsonBoundary.__name__ = f"JsonBoundary_{abs(hash(python_type))}"
    return JsonBoundary


@dataclass(frozen=True)
class SqlAlchemySchema:
    catalog: SchemaCatalog
    metadata: MetaData
    mapper_registry: registry
    tables: Mapping[str, Table]
    fts_tables: Mapping[str, Table]
    fts_indexes: Mapping[str, SchemaFtsIndex]
    vector_caches: Mapping[str, SchemaVectorCache]
    models_by_family: Mapping[str, type[Any]]
    polymorphic_models_by_family: Mapping[str, Mapping[str, type[Any]]]
    catalog_hash: str

    def table(self, family_name: str) -> Table:
        try:
            return self.tables[family_name]
        except KeyError as exc:
            raise KeyError(f"unknown SQLAlchemy schema table {family_name!r}") from exc

    def schema_object(self, family_name: str) -> SchemaObject:
        for schema_object in self.catalog.objects:
            if schema_object.family_name == family_name:
                return schema_object
        raise KeyError(f"unknown SQLAlchemy schema family {family_name!r}")

    def model(self, family_name: str) -> type[Any]:
        try:
            return self.models_by_family[family_name]
        except KeyError as exc:
            raise KeyError(f"unknown SQLAlchemy schema model {family_name!r}") from exc

    def polymorphic_model(self, family_name: str, identity: str) -> type[Any]:
        try:
            return self.polymorphic_models_by_family[family_name][identity]
        except KeyError as exc:
            raise KeyError(
                f"unknown SQLAlchemy polymorphic model {family_name!r}/{identity!r}"
            ) from exc

    def identity_field(self, family_name: str) -> str:
        schema_object = self.schema_object(family_name)
        if schema_object.identity_field is not None:
            return schema_object.identity_field
        primary_keys = tuple(field.name for field in schema_object.fields if field.primary_key)
        if len(primary_keys) == 1:
            return primary_keys[0]
        raise ValueError(f"family {family_name!r} has no single identity field")

    def reference_index_from_records(
        self,
        family_name: str,
        records: Iterable[object],
    ) -> FamilyReferenceIndex[object]:
        schema_object = self.schema_object(family_name)
        identity_key = ReferenceKey.field(self.identity_field(family_name))
        return FamilyReferenceIndex.from_records(
            records,
            family=family_name,
            artifact_id=lambda record: next(iter(identity_key(record)), None),
            keys=schema_object.reference_keys,
        )

    def resolve_reference_id(
        self,
        session: object,
        family_name: str,
        reference: object,
    ) -> str | None:
        model = self.model(family_name)
        records = cast(Any, session).execute(select(model)).scalars()
        return self.reference_index_from_records(family_name, records).resolve_id(reference)

    def require_reference_id(
        self,
        session: object,
        family_name: str,
        reference: str,
    ) -> str:
        resolved = self.resolve_reference_id(session, family_name, reference)
        if resolved is None:
            raise MissingReferenceError(reference)
        return resolved

    def fts_table(self, index_name: str) -> Table:
        try:
            return self.fts_tables[index_name]
        except KeyError as exc:
            raise KeyError(f"unknown SQLAlchemy FTS index {index_name!r}") from exc

    def fts_index(self, index_name: str) -> SchemaFtsIndex:
        try:
            return self.fts_indexes[index_name]
        except KeyError as exc:
            raise KeyError(f"unknown SQLAlchemy FTS index {index_name!r}") from exc

    def vector_cache(self, cache_name: str) -> SchemaVectorCache:
        try:
            return self.vector_caches[cache_name]
        except KeyError as exc:
            raise KeyError(f"unknown SQLAlchemy vector cache {cache_name!r}") from exc

    @property
    def has_vector_caches(self) -> bool:
        return bool(self.vector_caches)

    def construct(self, family_name: str, values: Mapping[str, object]) -> object:
        schema_object = self.schema_object(family_name)
        field_names = {field.name for field in schema_object.fields}
        unknown = set(values) - field_names
        if unknown:
            joined = ", ".join(sorted(unknown))
            raise ValueError(f"unknown field(s) for family {family_name!r}: {joined}.")
        missing = {
            field.name
            for field in schema_object.fields
            if (
                field.name not in values
                and not field.nullable
                and field.default is None
                and field.default_sql is None
                and not field.generated
            )
        }
        if missing:
            joined = ", ".join(sorted(missing))
            raise ValueError(f"missing required field(s) for family {family_name!r}: {joined}.")
        model = self.model(family_name)
        return model(**dict(values))


def build_sqlalchemy_schema(catalog: SchemaCatalog) -> SqlAlchemySchema:
    clear_mappers()
    metadata = MetaData()
    mapper_registry = registry(metadata=metadata)
    tables = {
        schema_object.family_name: _table_from_schema_object(metadata, schema_object)
        for schema_object in catalog.objects
    }
    fts_indexes = _fts_indexes_from_catalog(catalog)
    fts_tables = {
        name: _fts_table_from_index(metadata, index)
        for name, index in fts_indexes.items()
    }
    vector_caches = _vector_caches_from_catalog(catalog)
    models_by_family = {
        schema_object.family_name: _load_type(schema_object.model_path)
        for schema_object in catalog.objects
    }
    polymorphic_models_by_family = {
        schema_object.family_name: MappingProxyType({
            model.identity: _load_type(model.model_path)
            for model in schema_object.polymorphic_models
        })
        for schema_object in catalog.objects
    }
    schema = SqlAlchemySchema(
        catalog=catalog,
        metadata=metadata,
        mapper_registry=mapper_registry,
        tables=MappingProxyType(tables),
        fts_tables=MappingProxyType(fts_tables),
        fts_indexes=MappingProxyType(fts_indexes),
        vector_caches=MappingProxyType(vector_caches),
        models_by_family=MappingProxyType(models_by_family),
        polymorphic_models_by_family=MappingProxyType(polymorphic_models_by_family),
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


def _fts_indexes_from_catalog(catalog: SchemaCatalog) -> dict[str, SchemaFtsIndex]:
    indexes: dict[str, SchemaFtsIndex] = {}
    for schema_object in catalog.objects:
        field_names = {field.name for field in schema_object.fields}
        for index in schema_object.fts_indexes:
            if index.source_query is None:
                missing = {index.entity_id_field, *index.fields} - field_names
                if missing:
                    joined = ", ".join(sorted(missing))
                    raise ValueError(
                        f"FTS index {index.name!r} references unknown field(s): {joined}."
                    )
            if index.name in indexes:
                raise ValueError(f"duplicate FTS index name {index.name!r}.")
            indexes[index.name] = index
    return indexes


def _fts_table_from_index(metadata: MetaData, index: SchemaFtsIndex) -> Table:
    return FTS5Table(
        index.name,
        metadata,
        columns=[index.entity_id_field, *index.fields],
        tokenize=index.tokenize,
    )


def _vector_caches_from_catalog(catalog: SchemaCatalog) -> dict[str, SchemaVectorCache]:
    caches: dict[str, SchemaVectorCache] = {}
    for schema_object in catalog.objects:
        field_names = {field.name for field in schema_object.fields}
        for cache in schema_object.vector_caches:
            missing = {
                cache.entity_id_field,
                cache.source_seq_field,
                cache.source_content_hash_field,
            } - field_names
            if missing:
                joined = ", ".join(sorted(missing))
                raise ValueError(
                    f"Vector cache {cache.name!r} references unknown field(s): {joined}."
                )
            if cache.name in caches:
                raise ValueError(f"duplicate vector cache name {cache.name!r}.")
            caches[cache.name] = cache
    return caches


def _column_from_schema_field(schema_object: SchemaObject, field: SchemaField) -> Column[Any]:
    args: list[Any] = [
        ForeignKey(
            f"{foreign_key.target_family}.{foreign_key.target_field}",
            name=f"fk_{foreign_key.name}",
        )
        for foreign_key in _schema_field_foreign_keys(field)
    ]
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


def _schema_field_foreign_keys(field: SchemaField) -> tuple[Any, ...]:
    if field.parse_boundary == "json":
        return ()
    if field.foreign_keys:
        return field.foreign_keys
    if field.foreign_key is not None:
        return (field.foreign_key,)
    return ()


def _sqlalchemy_type(field: SchemaField) -> TypeEngine[Any]:
    sql_type = cast(SqlTypeSpec, field.sql_type)
    if field.parse_boundary == "json":
        if field.parse_python_type is None:
            raise ValueError(
                f"JSON parse-boundary field {field.name!r} is missing its authored "
                "Python type."
            )
        return _make_json_type_decorator(field.parse_python_type)()
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
            order_by_columns = _relationship_order_by_columns(
                table,
                target_table,
                rel.order_by,
            )
            if order_by_columns:
                relationship_kwargs["order_by"] = order_by_columns
            properties[rel.name] = relationship(target_model, **relationship_kwargs)
        mapper_kwargs: dict[str, Any] = {"properties": properties}
        if not table.primary_key.columns:
            mapper_kwargs["primary_key"] = tuple(table.c)
        if schema_object.polymorphic_on is not None:
            if schema_object.polymorphic_on not in table.c:
                raise KeyError(
                    f"polymorphic field {schema_object.polymorphic_on!r} is not present "
                    f"on {table.name!r}"
                )
            mapper_kwargs["polymorphic_on"] = table.c[schema_object.polymorphic_on]
        elif schema_object.polymorphic_models:
            raise ValueError(
                f"family {schema_object.family_name!r} declares polymorphic models "
                "without polymorphic_on"
            )
        if schema_object.polymorphic_identity is not None:
            mapper_kwargs["polymorphic_identity"] = schema_object.polymorphic_identity
        schema.mapper_registry.map_imperatively(model, table, **mapper_kwargs)
        for polymorphic_model in schema_object.polymorphic_models:
            schema.mapper_registry.map_imperatively(
                schema.polymorphic_model(
                    schema_object.family_name,
                    polymorphic_model.identity,
                ),
                inherits=model,
                polymorphic_identity=polymorphic_model.identity,
            )


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


def _relationship_order_by_columns(
    source_table: Table,
    target_table: Table,
    order_by: tuple[str, ...],
) -> tuple[Column[Any], ...]:
    columns: list[Column[Any]] = []
    for field_name in order_by:
        if field_name in target_table.c:
            columns.append(target_table.c[field_name])
        elif field_name in source_table.c:
            columns.append(source_table.c[field_name])
        else:
            raise KeyError(
                f"relationship order_by field {field_name!r} is not present "
                f"on {source_table.name!r} or {target_table.name!r}"
            )
    return tuple(columns)


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

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from quire.families import FamilyDefinition
from quire.references import ForeignKeySpec
from quire.schema_catalog import SchemaCatalog
from quire.schema_ir import (
    SchemaField,
    SchemaFtsIndex,
    SchemaForeignKey,
    SchemaIndex,
    SchemaObject,
    SchemaPolymorphicModel,
    SchemaRelationship,
    SchemaVectorCache,
    python_type_path,
)
from quire.sql_types import python_type_to_sql


class FamilyModel:
    """Base for charter-mapped family models with behavior but no field shape."""


@dataclass(frozen=True)
class CharterField:
    name: str
    python_type: type[Any]
    nullable: bool = True
    primary_key: bool = False
    foreign_key: ForeignKeySpec | None = None
    index: bool = False
    unique: bool = False
    generated: bool = False
    default: object | None = None
    default_sql: str | None = None
    json_value_object: bool = False
    enum_type: type[Enum] | None = None
    search: bool = False
    vector_dimensions: int | None = None
    source_local_only: bool = False
    canonical_only: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_schema_field(self) -> SchemaField:
        sql_type = python_type_to_sql(
            self.python_type,
            json_value_object=self.json_value_object,
            enum_type=self.enum_type,
        )
        return SchemaField(
            name=self.name,
            python_type=python_type_path(self.python_type),
            sql_type=sql_type,
            nullable=self.nullable,
            primary_key=self.primary_key,
            foreign_key=(
                None
                if self.foreign_key is None
                else _schema_foreign_key(self.foreign_key)
            ),
            index=self.index,
            unique=self.unique,
            generated=self.generated,
            default=self.default,
            default_sql=self.default_sql,
            json_value_object=self.json_value_object,
            enum_values=sql_type.enum_values,
            search=self.search,
            vector_dimensions=self.vector_dimensions,
            source_local_only=self.source_local_only,
            canonical_only=self.canonical_only,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class CharterIndex:
    name: str
    fields: tuple[str, ...]
    unique: bool = False

    def to_schema_index(self) -> SchemaIndex:
        return SchemaIndex(name=self.name, fields=self.fields, unique=self.unique)


@dataclass(frozen=True)
class CharterFtsIndex:
    name: str
    entity_id_field: str
    fields: tuple[str, ...]
    tokenize: str | None = None
    source_query: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_schema_fts_index(self, family_name: str) -> SchemaFtsIndex:
        return SchemaFtsIndex(
            name=self.name,
            family_name=family_name,
            entity_id_field=self.entity_id_field,
            fields=self.fields,
            tokenize=self.tokenize,
            source_query=self.source_query,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class CharterVectorCache:
    name: str
    table: str
    dimensions: int | None = None
    entity_id_field: str = "id"
    source_seq_field: str = "seq"
    source_content_hash_field: str = "content_hash"
    status_table: str | None = None
    embedding_column: str = "embedding"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_schema_vector_cache(self, family_name: str) -> SchemaVectorCache:
        return SchemaVectorCache(
            name=self.name,
            family_name=family_name,
            table=self.table,
            dimensions=self.dimensions,
            entity_id_field=self.entity_id_field,
            source_seq_field=self.source_seq_field,
            source_content_hash_field=self.source_content_hash_field,
            status_table=self.status_table,
            embedding_column=self.embedding_column,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class CharterRelationship:
    name: str
    target_family: str
    foreign_key: str | None = None
    back_populates: str | None = None
    uselist: bool = True
    association_object: bool = False
    order_by: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_schema_relationship(self) -> SchemaRelationship:
        return SchemaRelationship(
            name=self.name,
            target_family=self.target_family,
            foreign_key=self.foreign_key,
            back_populates=self.back_populates,
            uselist=self.uselist,
            association_object=self.association_object,
            order_by=self.order_by,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class CharterPolymorphicModel:
    model: type[Any]
    identity: str

    def to_schema_polymorphic_model(self) -> SchemaPolymorphicModel:
        return SchemaPolymorphicModel(
            model_path=python_type_path(self.model),
            identity=self.identity,
        )


@dataclass(frozen=True)
class FamilyCharter:
    family: FamilyDefinition[Any, Any, Any, Any]
    model: type[Any]
    fields: tuple[CharterField, ...]
    lifecycle_states: tuple[str, ...] = ()
    indexes: tuple[CharterIndex, ...] = ()
    fts_indexes: tuple[CharterFtsIndex, ...] = ()
    vector_caches: tuple[CharterVectorCache, ...] = ()
    relationships: tuple[CharterRelationship, ...] = ()
    polymorphic_on: str | None = None
    polymorphic_identity: str | None = None
    polymorphic_models: tuple[CharterPolymorphicModel, ...] = ()
    semantic_metadata: Mapping[str, object] = field(default_factory=dict)

    def to_schema_object(self) -> SchemaObject:
        return SchemaObject(
            name=self.family.name,
            family_name=self.family.name,
            artifact_family_name=self.family.artifact_family.name,
            artifact_contract_version=str(self.family.artifact_family.contract_version),
            model_path=python_type_path(self.model),
            fields=tuple(field.to_schema_field() for field in self.fields),
            identity_field=self.family.identity_field,
            reference_keys=self.family.reference_keys,
            lifecycle_states=self.lifecycle_states,
            indexes=tuple(index.to_schema_index() for index in self.indexes),
            fts_indexes=tuple(
                index.to_schema_fts_index(self.family.name)
                for index in self.fts_indexes
            ),
            vector_caches=tuple(
                cache.to_schema_vector_cache(self.family.name)
                for cache in self.vector_caches
            ),
            relationships=tuple(
                relationship.to_schema_relationship()
                for relationship in self.relationships
            ),
            polymorphic_on=self.polymorphic_on,
            polymorphic_identity=self.polymorphic_identity,
            polymorphic_models=tuple(
                model.to_schema_polymorphic_model()
                for model in self.polymorphic_models
            ),
            semantic_metadata=self.semantic_metadata,
        )


def charter_catalog(
    *charters: FamilyCharter,
    metadata: Mapping[str, object] | None = None,
) -> SchemaCatalog:
    return SchemaCatalog(
        objects=tuple(charter.to_schema_object() for charter in charters),
        metadata={} if metadata is None else metadata,
    )


def _schema_foreign_key(spec: ForeignKeySpec) -> SchemaForeignKey:
    return SchemaForeignKey(
        name=spec.name,
        source_family=spec.source_family,
        source_field=spec.source_field,
        target_family=spec.target_family,
        target_field=spec.target_field,
        required=spec.required,
        many=spec.many,
    )

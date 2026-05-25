from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from quire.references import ReferenceKey
from quire.versions import VersionId


def python_type_path(python_type: type[Any]) -> str:
    return f"{python_type.__module__}.{python_type.__qualname__}"


@dataclass(frozen=True)
class SchemaForeignKey:
    name: str
    source_family: str
    source_field: str
    target_family: str
    target_field: str = "id"
    required: bool = True
    many: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "many": self.many,
            "name": self.name,
            "required": self.required,
            "source_family": self.source_family,
            "source_field": self.source_field,
            "target_family": self.target_family,
            "target_field": self.target_field,
        }


@dataclass(frozen=True)
class SchemaField:
    name: str
    python_type: str
    sql_type: object
    nullable: bool = True
    primary_key: bool = False
    foreign_key: SchemaForeignKey | None = None
    index: bool = False
    unique: bool = False
    generated: bool = False
    default: object | None = None
    default_sql: str | None = None
    json_value_object: bool = False
    enum_values: tuple[str, ...] = ()
    search: bool = False
    vector_dimensions: int | None = None
    source_local_only: bool = False
    canonical_only: bool = False
    document: bool = True
    document_name: str | None = None
    document_order: int | None = None
    states: frozenset[str] | None = None
    artifact: bool = False
    artifact_name: str | None = None
    graph_node_label: bool = False
    graph_metadata: bool = False
    local_id: bool = False
    local_id_policy: str | None = None
    contract_version: VersionId | None = None
    parse_boundary: Literal["yaml", "json", "sqlite"] | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        return {
            "artifact": self.artifact,
            "artifact_name": self.artifact_name,
            "canonical_only": self.canonical_only,
            "contract_version": self.contract_version,
            "default": self.default,
            "default_sql": self.default_sql,
            "document": self.document,
            "document_name": self.document_name,
            "document_order": self.document_order,
            "enum_values": self.enum_values,
            "foreign_key": None if self.foreign_key is None else self.foreign_key.payload(),
            "generated": self.generated,
            "graph_metadata": self.graph_metadata,
            "graph_node_label": self.graph_node_label,
            "index": self.index,
            "json_value_object": self.json_value_object,
            "local_id": self.local_id,
            "local_id_policy": self.local_id_policy,
            "metadata": dict(sorted(self.metadata.items())),
            "name": self.name,
            "nullable": self.nullable,
            "parse_boundary": self.parse_boundary,
            "primary_key": self.primary_key,
            "python_type": self.python_type,
            "search": self.search,
            "source_local_only": self.source_local_only,
            "sql_type": _payload(self.sql_type),
            "states": self.states,
            "unique": self.unique,
            "vector_dimensions": self.vector_dimensions,
        }


@dataclass(frozen=True)
class SchemaIndex:
    name: str
    fields: tuple[str, ...]
    unique: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "fields": self.fields,
            "name": self.name,
            "unique": self.unique,
        }


@dataclass(frozen=True)
class SchemaFtsIndex:
    name: str
    family_name: str
    entity_id_field: str
    fields: tuple[str, ...]
    tokenize: str | None = None
    source_query: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def column_names(self) -> tuple[str, ...]:
        return (self.entity_id_field, *self.fields)

    def payload(self) -> dict[str, object]:
        return {
            "entity_id_field": self.entity_id_field,
            "family_name": self.family_name,
            "fields": self.fields,
            "metadata": dict(sorted(self.metadata.items())),
            "name": self.name,
            "source_query": self.source_query,
            "tokenize": self.tokenize,
        }


@dataclass(frozen=True)
class SchemaVectorCache:
    name: str
    family_name: str
    table: str
    dimensions: int | None = None
    entity_id_field: str = "id"
    source_seq_field: str = "seq"
    source_content_hash_field: str = "content_hash"
    status_table: str | None = None
    embedding_column: str = "embedding"
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def status_table_name(self) -> str:
        if self.status_table is not None:
            return self.status_table
        return f"{self.name}_embedding_status"

    def payload(self) -> dict[str, object]:
        return {
            "dimensions": self.dimensions,
            "embedding_column": self.embedding_column,
            "entity_id_field": self.entity_id_field,
            "family_name": self.family_name,
            "metadata": dict(sorted(self.metadata.items())),
            "name": self.name,
            "source_content_hash_field": self.source_content_hash_field,
            "source_seq_field": self.source_seq_field,
            "status_table": self.status_table_name,
            "table": self.table,
        }


@dataclass(frozen=True)
class SchemaRelationship:
    name: str
    target_family: str
    foreign_key: str | None = None
    back_populates: str | None = None
    uselist: bool = True
    association_object: bool = False
    order_by: tuple[str, ...] = ()
    artifact_dependency: bool = False
    graph_edge: bool = False
    graph_edge_kind: str | None = None
    states: frozenset[str] | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        return {
            "association_object": self.association_object,
            "artifact_dependency": self.artifact_dependency,
            "back_populates": self.back_populates,
            "foreign_key": self.foreign_key,
            "graph_edge": self.graph_edge,
            "graph_edge_kind": self.graph_edge_kind,
            "metadata": dict(sorted(self.metadata.items())),
            "name": self.name,
            "order_by": self.order_by,
            "states": self.states,
            "target_family": self.target_family,
            "uselist": self.uselist,
        }


@dataclass(frozen=True)
class SchemaPolymorphicModel:
    model_path: str
    identity: str

    def payload(self) -> dict[str, object]:
        return {
            "identity": self.identity,
            "model": self.model_path,
        }


@dataclass(frozen=True)
class SchemaObject:
    name: str
    family_name: str
    artifact_family_name: str
    artifact_contract_version: str
    model_path: str
    fields: tuple[SchemaField, ...]
    identity_field: str | None = None
    reference_keys: tuple[ReferenceKey, ...] = ()
    lifecycle_states: tuple[str, ...] = ()
    document_contract_version: VersionId | None = None
    indexes: tuple[SchemaIndex, ...] = ()
    fts_indexes: tuple[SchemaFtsIndex, ...] = ()
    vector_caches: tuple[SchemaVectorCache, ...] = ()
    relationships: tuple[SchemaRelationship, ...] = ()
    polymorphic_on: str | None = None
    polymorphic_identity: str | None = None
    polymorphic_models: tuple[SchemaPolymorphicModel, ...] = ()
    semantic_metadata: Mapping[str, object] = field(default_factory=dict)

    def field(self, name: str) -> SchemaField:
        for schema_field in self.fields:
            if schema_field.name == name:
                return schema_field
        raise KeyError(f"unknown schema field {name!r} on {self.name!r}")

    def payload(self) -> dict[str, object]:
        return {
            "family": {
                "artifact_contract_version": self.artifact_contract_version,
                "artifact_family": self.artifact_family_name,
                "document_contract_version": self.document_contract_version,
                "identity_field": self.identity_field,
                "name": self.family_name,
                "reference_keys": tuple(key.contract_body() for key in self.reference_keys),
            },
            "fields": tuple(field.payload() for field in _sort_by_name(self.fields)),
            "fts_indexes": tuple(index.payload() for index in _sort_by_name(self.fts_indexes)),
            "indexes": tuple(index.payload() for index in _sort_by_name(self.indexes)),
            "lifecycle_states": self.lifecycle_states,
            "model": self.model_path,
            "name": self.name,
            "relationships": tuple(
                relationship.payload()
                for relationship in _sort_by_name(self.relationships)
            ),
            "polymorphic": {
                "identity": self.polymorphic_identity,
                "models": tuple(
                    model.payload()
                    for model in sorted(
                        self.polymorphic_models,
                        key=lambda model: model.identity,
                    )
                ),
                "on": self.polymorphic_on,
            },
            "semantic_metadata": dict(sorted(self.semantic_metadata.items())),
            "vector_caches": tuple(cache.payload() for cache in _sort_by_name(self.vector_caches)),
        }


def _payload(value: object) -> object:
    payload = getattr(value, "payload", None)
    if callable(payload):
        return payload()
    return value


def _sort_by_name(items: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(sorted(items, key=lambda item: item.name))

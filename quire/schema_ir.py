from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import UnionType
from typing import TYPE_CHECKING, Any, Literal, Union, get_args, get_origin

import msgspec

from quire.documents.batch import DocumentBatchSpec
from quire.lifecycle import FamilyState, FamilyTransition
from quire.projection_kinds import iter_projection_kinds
from quire.references import ReferenceKey
from quire.versions import VersionId

if TYPE_CHECKING:
    from quire.charters import CharterField


def python_type_path(python_type: object) -> str:
    origin = get_origin(python_type)
    if origin is Union or origin is UnionType or isinstance(python_type, UnionType):
        return " | ".join(python_type_path(arg) for arg in get_args(python_type))
    if python_type is type(None):
        return "None"
    if isinstance(python_type, type):
        return f"{python_type.__module__}.{python_type.__qualname__}"
    return repr(python_type)


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
    """Lowered, SQL-resolved view of one :class:`~quire.charters.CharterField`.

    Holds only *intrinsic* column attributes (name, resolved SQL type, nullability,
    primary key, document/storage shape, ...). Field-level *projections* — index,
    unique, foreign keys, graph node/edge, artifact, local-id, search, vector — are
    no longer mirrored here. They are sourced through the projection-kind registry
    over :attr:`charter_field`, the single source of truth, so both the SQL builder
    and the contract payload consume one field type instead of a parallel flag copy.
    """

    name: str
    python_type: str
    sql_type: object
    charter_field: "CharterField" = field(repr=False, compare=False)
    nullable: bool = True
    primary_key: bool = False
    generated: bool = False
    versioned: bool = True
    default: object | None = None
    default_sql: str | None = None
    json_value_object: bool = False
    enum_values: tuple[str, ...] = ()
    source_local_only: bool = False
    canonical_only: bool = False
    document: bool = True
    storage: bool = True
    document_name: str | None = None
    document_order: int | None = None
    states: frozenset[str] | None = None
    contract_version: VersionId | None = None
    parse_python_type: object | None = field(default=None, repr=False, compare=False)
    parse_boundary: Literal["yaml", "json", "sqlite"] | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def projection_payload(self) -> dict[str, dict[str, object]]:
        """Deterministic contract body for every projection kind that applies.

        Keyed by kind name (``iter_projection_kinds`` yields name-sorted); each
        kind's :meth:`schema_payload` output has its keys sorted. This is what
        makes adding/changing a field's projection participation visible to
        ``check_contract_manifest`` without re-enumerating flags here.
        """

        return {
            kind.name: dict(sorted(kind.schema_payload(self.charter_field).items()))
            for kind in iter_projection_kinds()
            if kind.applies(self.charter_field)
        }

    def payload(self) -> dict[str, object]:
        return {
            "canonical_only": self.canonical_only,
            "contract_version": self.contract_version,
            "default": _default_payload(self.default),
            "default_sql": self.default_sql,
            "document": self.document,
            "document_name": self.document_name,
            "document_order": self.document_order,
            "enum_values": self.enum_values,
            "generated": self.generated,
            "json_value_object": self.json_value_object,
            "metadata": dict(sorted(self.metadata.items())),
            "name": self.name,
            "nullable": self.nullable,
            "parse_boundary": self.parse_boundary,
            "primary_key": self.primary_key,
            "projections": self.projection_payload(),
            "python_type": self.python_type,
            "source_local_only": self.source_local_only,
            "sql_type": _payload(self.sql_type),
            "states": self.states,
            "storage": self.storage,
            "storage_codec": self.charter_field.storage_codec,
            "versioned": self.versioned,
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
    states: tuple[FamilyState, ...] = ()
    transitions: tuple[FamilyTransition, ...] = ()
    document_contract_version: VersionId | None = None
    batch_specs: tuple[DocumentBatchSpec[Any], ...] = ()
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
            "batch_specs": tuple(
                {
                    "batch_name": spec.batch_name,
                    "item_type": python_type_path(spec.item_type),
                    "items_field": spec.items_field,
                    "inherited_item_fields": spec.inherited_item_fields,
                }
                for spec in self.batch_specs
            ),
            "fields": tuple(field.payload() for field in _sort_by_name(self.fields)),
            "fts_indexes": tuple(index.payload() for index in _sort_by_name(self.fts_indexes)),
            "indexes": tuple(index.payload() for index in _sort_by_name(self.indexes)),
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
            "states": tuple(_state_payload(state) for state in _sort_by_name(self.states)),
            "transitions": tuple(
                _transition_payload(transition)
                for transition in _sort_by_name(self.transitions)
            ),
            "vector_caches": tuple(cache.payload() for cache in _sort_by_name(self.vector_caches)),
        }


def _payload(value: object) -> object:
    payload = getattr(value, "payload", None)
    if callable(payload):
        return payload()
    return value


def _default_payload(value: object) -> object:
    """Render a charter field default into JSON-serializable builtins.

    ``CharterField.default`` may be a complex object (e.g. a ``msgspec.Struct``
    produced by a field ``default_factory``) because the same value also drives
    document regeneration. The schema catalog, however, is canonicalized to JSON
    for hashing and persistence, so the payload representation must be builtins.
    ``msgspec.to_builtins`` leaves JSON-native scalars/lists/dicts unchanged
    (no catalog-hash churn) and lowers structs/enums to builtins.
    """

    if value is None:
        return None
    return msgspec.to_builtins(value)


def _sort_by_name(items: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(sorted(items, key=lambda item: item.name))


def _state_payload(state: FamilyState) -> dict[str, object]:
    return {
        "document_label": state.document_label,
        "name": state.name,
        "terminal": state.terminal,
    }


def _transition_payload(transition: FamilyTransition) -> dict[str, object]:
    return {
        "conflict_policy": transition.conflict_policy.value,
        "guard": transition.guard,
        "materializer": transition.materializer,
        "merge": transition.merge,
        "name": transition.name,
        "source": transition.source,
        "target": transition.target,
    }

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Literal, Optional, cast

import msgspec

from quire.documents.codecs import (
    DocumentCodec,
    convert_document,
    decode_document,
    document_to_payload,
    encode_document,
    render_document,
)
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
from quire.sql_types import is_optional_type, optional_inner_type, python_type_to_sql
from quire.versions import VersionId


_UNSPECIFIED_NULLABLE = object()


class FamilyModel:
    """Base for charter-mapped family models with behavior but no field shape."""

    def __init__(self, **values: object) -> None:
        for key, value in values.items():
            setattr(self, key, value)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)


@dataclass(frozen=True)
class CharterField:
    name: str
    python_type: object
    nullable: bool | object = _UNSPECIFIED_NULLABLE
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
    _nullable_explicit: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.nullable is _UNSPECIFIED_NULLABLE:
            object.__setattr__(self, "nullable", True)
        else:
            object.__setattr__(self, "_nullable_explicit", True)

    def to_schema_field(self) -> SchemaField:
        schema_python_type = str if self.parse_boundary == "json" else self.python_type
        sql_type = python_type_to_sql(
            schema_python_type,
            json_value_object=False if self.parse_boundary == "json" else self.json_value_object,
            enum_type=None if self.parse_boundary == "json" else self.enum_type,
        )
        return SchemaField(
            name=self.name,
            python_type=python_type_path(schema_python_type),
            sql_type=sql_type,
            nullable=bool(self.nullable) or is_optional_type(self.python_type),
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
            document=self.document,
            document_name=self.document_name,
            document_order=self.document_order,
            states=self.states,
            artifact=self.artifact,
            artifact_name=self.artifact_name,
            graph_node_label=self.graph_node_label,
            graph_metadata=self.graph_metadata,
            local_id=self.local_id,
            local_id_policy=self.local_id_policy,
            contract_version=self.contract_version,
            parse_python_type=self.python_type if self.parse_boundary == "json" else None,
            parse_boundary=self.parse_boundary,
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
    artifact_dependency: bool = False
    graph_edge: bool = False
    graph_edge_kind: str | None = None
    states: frozenset[str] | None = None
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
            artifact_dependency=self.artifact_dependency,
            graph_edge=self.graph_edge,
            graph_edge_kind=self.graph_edge_kind,
            states=self.states,
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
    document_contract_version: VersionId | None = None
    semantic_metadata: Mapping[str, object] = field(default_factory=dict)
    validators: tuple[Callable[[msgspec.Struct], None], ...] = ()
    _generated_document_cache: dict[str | None, type[msgspec.Struct]] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _document_codec_cache: dict[str | None, DocumentCodec] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def generated_document(self, state: str | None = None) -> type[msgspec.Struct]:
        cached = self._generated_document_cache.get(state)
        if cached is not None:
            return cached

        document_fields = _document_struct_fields(self.fields, state=state)
        validators = self.validators

        class _GeneratedDocumentValidatorMixin(msgspec.Struct):
            def __post_init__(self) -> None:
                for validator in validators:
                    validator(self)

        document_type = msgspec.defstruct(
            _document_struct_name(self.family.name, state),
            cast(Any, document_fields),
            bases=(_GeneratedDocumentValidatorMixin,),
            module=self.model.__module__,
            forbid_unknown_fields=True,
        )
        self._generated_document_cache[state] = document_type
        return document_type

    def document_codec(self, state: str | None = None) -> DocumentCodec:
        cached = self._document_codec_cache.get(state)
        if cached is not None:
            return cached

        document_type = self.generated_document(state)
        json_blob_fields = _json_blob_document_fields(self.fields, state=state)

        if json_blob_fields:

            def json_blob_payload(document: object) -> object:
                return _encode_json_blob_fields(
                    document_to_payload(document),
                    json_blob_fields,
                )

            def json_blob_encode(document: object) -> bytes:
                return msgspec.yaml.encode(json_blob_payload(document))

            def json_blob_render(document: object) -> str:
                return json_blob_encode(document).decode("utf-8").rstrip()

            def convert_generated_document(
                payload: object,
                _document_type: type[Any],
                *,
                source: str,
            ) -> object:
                return convert_document(
                    _decode_json_blob_fields(payload, json_blob_fields),
                    document_type,
                    source=source,
                )

            def decode_generated_document(
                payload: bytes,
                _document_type: type[Any],
                *,
                source: str,
            ) -> object:
                try:
                    decoded_payload = msgspec.yaml.decode(payload)
                except msgspec.DecodeError as exc:
                    raise ValueError(f"{source}: invalid YAML payload") from exc
                return convert_generated_document(
                    decoded_payload,
                    document_type,
                    source=source,
                )

            codec = DocumentCodec(
                convert_document=convert_generated_document,
                decode_document=decode_generated_document,
                encode_document=json_blob_encode,
                render_document=json_blob_render,
                document_to_payload=json_blob_payload,
            )
            self._document_codec_cache[state] = codec
            return codec

        def convert_generated_document(
            payload: object,
            _document_type: type[Any],
            *,
            source: str,
        ) -> object:
            return convert_document(payload, document_type, source=source)

        def decode_generated_document(
            payload: bytes,
            _document_type: type[Any],
            *,
            source: str,
        ) -> object:
            return decode_document(payload, document_type, source=source)

        codec = DocumentCodec(
            convert_document=convert_generated_document,
            decode_document=decode_generated_document,
            encode_document=encode_document,
            render_document=render_document,
            document_to_payload=document_to_payload,
        )
        self._document_codec_cache[state] = codec
        return codec

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
            document_contract_version=self.document_contract_version,
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


def _document_struct_fields(
    fields: tuple[CharterField, ...],
    *,
    state: str | None,
) -> list[tuple[str, object] | tuple[str, object, object]]:
    ordered_fields = sorted(
        (
            (index, charter_field)
            for index, charter_field in enumerate(fields)
            if charter_field.document and _field_matches_state(charter_field, state)
        ),
        key=lambda item: (
            item[1].document_order if item[1].document_order is not None else item[0],
            item[0],
        ),
    )
    struct_fields: list[tuple[str, object] | tuple[str, object, object]] = []
    for _index, charter_field in ordered_fields:
        name = charter_field.document_name or charter_field.name
        python_type = _document_python_type(charter_field)
        if charter_field.default is None and _field_defaults_to_none(charter_field):
            struct_fields.append((name, python_type, None))
        elif charter_field.default is None:
            struct_fields.append((name, python_type))
        else:
            struct_fields.append((name, python_type, charter_field.default))
    return struct_fields


def _document_python_type(field: CharterField) -> object:
    if is_optional_type(field.python_type):
        return field.python_type
    if bool(field.nullable) and field._nullable_explicit:
        inner_type = optional_inner_type(field.python_type)
        if field.parse_boundary == "json":
            return _optional_document_python_type(inner_type)
        if isinstance(inner_type, type):
            return inner_type | None
    return field.python_type


def _optional_document_python_type(python_type: object) -> object:
    try:
        return python_type | None  # type: ignore[operator]
    except TypeError:
        return Optional[python_type]  # type: ignore[index]


def _field_defaults_to_none(field: CharterField) -> bool:
    return is_optional_type(field.python_type) or (
        bool(field.nullable) and field._nullable_explicit
    )


def _field_matches_state(field: CharterField, state: str | None) -> bool:
    return field.states is None or state is None or state in field.states


def _document_struct_name(family_name: str, state: str | None) -> str:
    parts = [family_name]
    if state is not None:
        parts.append(state)
    name = "".join(_title_identifier_part(part) for part in parts)
    if not name or not (name[0].isalpha() or name[0] == "_"):
        name = f"Family{name}"
    return f"{name}Document"


def _title_identifier_part(value: str) -> str:
    return "".join(
        part[:1].upper() + part[1:]
        for part in re.split(r"[^0-9A-Za-z_]+", value)
        if part
    )


def _json_blob_document_fields(
    fields: tuple[CharterField, ...],
    *,
    state: str | None,
) -> dict[str, object]:
    return {
        field.document_name or field.name: field.python_type
        for field in fields
        if field.document
        and field.parse_boundary == "json"
        and _field_matches_state(field, state)
    }


def _encode_json_blob_fields(
    payload: object,
    json_blob_fields: Mapping[str, object],
) -> object:
    if not isinstance(payload, Mapping):
        return payload
    encoded: dict[object, object] = dict(payload)
    for name in json_blob_fields:
        if name not in encoded or encoded[name] is None:
            continue
        encoded[name] = msgspec.json.encode(encoded[name]).decode("utf-8")
    return encoded


def _decode_json_blob_fields(
    payload: object,
    json_blob_fields: Mapping[str, object],
) -> object:
    if not isinstance(payload, Mapping):
        return payload
    decoded: dict[object, object] = dict(payload)
    for name, python_type in json_blob_fields.items():
        if name not in decoded or decoded[name] is None:
            continue
        value: object = decoded[name]
        if isinstance(value, str):
            decoded[name] = msgspec.json.decode(value.encode("utf-8"), type=python_type)
        elif isinstance(value, bytes):
            decoded[name] = msgspec.json.decode(value, type=python_type)
        else:
            decoded[name] = msgspec.convert(value, type=python_type, strict=True)
    return decoded


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

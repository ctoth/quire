from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any

import msgspec

from quire.charters import CharterField, FamilyCharter
from quire.hashing import canonical_json_sha256
from quire.references import ForeignKeySpec


@dataclass(frozen=True)
class ArtifactIdentity:
    family: str
    identity: str


@dataclass(frozen=True)
class ArtifactDependency:
    source: ArtifactIdentity
    target_family: str
    target_identity: str
    field: str
    foreign_key: str
    index: int | None = None


@dataclass(frozen=True)
class GraphNodeProjection:
    identity: ArtifactIdentity
    label: str
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class GraphEdgeProjection:
    source: ArtifactIdentity
    target_family: str
    target_identity: str
    edge_type: str
    field: str
    foreign_key: str
    index: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def artifact_identity(charter: FamilyCharter, record: object) -> ArtifactIdentity:
    identity_field = charter.family.identity_field
    if not identity_field:
        raise ValueError(f"{charter.family.name}: artifact projection requires identity_field")
    value = getattr(record, identity_field)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{charter.family.name}: identity field {identity_field!r} "
            "must be a non-empty string"
        )
    return ArtifactIdentity(charter.family.name, value)


def artifact_payload(
    charter: FamilyCharter,
    record: object,
    *,
    omit_artifact_fields: bool = True,
    omit_none: bool = False,
) -> object:
    payload = {
        field.document_name or field.name: _payload_value(getattr(record, field.name, None))
        for field in charter.fields
        if field.document
    }
    artifact_document_names = {
        field.document_name or field.name
        for field in charter.fields
        if field.artifact
    }
    projected = dict(payload)
    if omit_artifact_fields:
        for name in artifact_document_names:
            projected.pop(name, None)
    if omit_none:
        for name in tuple(projected):
            if projected[name] is None:
                projected.pop(name)
    return projected


def artifact_digest(
    charter: FamilyCharter,
    record: object,
    *,
    omit_artifact_fields: bool = True,
    omit_none: bool = False,
) -> str:
    return canonical_json_sha256(
        artifact_payload(
            charter,
            record,
            omit_artifact_fields=omit_artifact_fields,
            omit_none=omit_none,
        )
    )


def iter_artifact_dependencies(
    charter: FamilyCharter,
    record: object,
) -> Iterator[ArtifactDependency]:
    source = artifact_identity(charter, record)
    for field in charter.fields:
        if not field.artifact_dependency:
            continue
        foreign_keys = _field_foreign_keys(field)
        if not foreign_keys:
            raise ValueError(
                f"{charter.family.name}.{field.name}: artifact dependency "
                "requires a foreign key"
            )
        value = getattr(record, field.name, None)
        for foreign_key in foreign_keys:
            yield from _dependency_values(source, field, foreign_key, value)


def graph_node_projection(
    charter: FamilyCharter,
    record: object,
) -> GraphNodeProjection:
    identity = artifact_identity(charter, record)
    label = identity.identity
    metadata: dict[str, object] = {}
    for field in charter.fields:
        value = getattr(record, field.name, None)
        if field.graph_node_label and value is not None:
            label = str(value)
        if field.graph_metadata and value is not None:
            metadata[field.document_name or field.name] = value
    return GraphNodeProjection(identity=identity, label=label, metadata=metadata)


def iter_graph_edges(
    charter: FamilyCharter,
    record: object,
) -> Iterator[GraphEdgeProjection]:
    source = artifact_identity(charter, record)
    for field in charter.fields:
        if not field.graph_edge:
            continue
        foreign_keys = _field_foreign_keys(field)
        if not foreign_keys:
            raise ValueError(
                f"{charter.family.name}.{field.name}: graph edge requires a foreign key"
            )
        value = getattr(record, field.name, None)
        for foreign_key in foreign_keys:
            edge_type = field.graph_edge_kind or foreign_key.name
            for dependency in _dependency_values(source, field, foreign_key, value):
                yield GraphEdgeProjection(
                    source=dependency.source,
                    target_family=dependency.target_family,
                    target_identity=dependency.target_identity,
                    edge_type=edge_type,
                    field=dependency.field,
                    foreign_key=dependency.foreign_key,
                    index=dependency.index,
                    metadata={
                        key: item
                        for key, item in field.metadata.items()
                        if isinstance(key, str)
                    },
                )


def _field_foreign_keys(field: CharterField) -> tuple[ForeignKeySpec, ...]:
    if field.foreign_keys:
        return field.foreign_keys
    if field.foreign_key is not None:
        return (field.foreign_key,)
    return ()


def _payload_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, msgspec.Struct):
        return msgspec.to_builtins(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, tuple | list):
        return [_payload_value(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _payload_value(item)
            for key, item in value.items()
        }
    return value


def _dependency_values(
    source: ArtifactIdentity,
    field: CharterField,
    foreign_key: ForeignKeySpec,
    value: object,
) -> Iterator[ArtifactDependency]:
    if value is None:
        return
    if isinstance(value, str):
        yield ArtifactDependency(
            source=source,
            target_family=foreign_key.target_family,
            target_identity=value,
            field=field.name,
            foreign_key=foreign_key.name,
        )
        return
    if isinstance(value, tuple | list | frozenset):
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item:
                raise ValueError(
                    f"{source.family}.{field.name}[{index}]: dependency "
                    "values must be non-empty strings"
                )
            yield ArtifactDependency(
                source=source,
                target_family=foreign_key.target_family,
                target_identity=item,
                field=field.name,
                foreign_key=foreign_key.name,
                index=index,
            )
        return
    raise ValueError(
        f"{source.family}.{field.name}: dependency value must be a string "
        "or sequence of strings"
    )

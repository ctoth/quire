from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field as dataclass_field, is_dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

import msgspec

from quire.charters import CharterField, FamilyCharter
from quire.hashing import canonical_json_sha256
from quire.projection_kinds import (
    ProjectionKind,
    iter_projection_kinds,
    register_projection_kind,
)
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
    metadata: Mapping[str, object] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class GraphHyperedgeProjection:
    """An N-ary edge: ``sources`` (antecedents) jointly relate to one target.

    Unlike :class:`GraphEdgeProjection` (a single source -> single target), a
    hyperedge keeps the antecedent grouping intact instead of fanning out into N
    independent binary edges that duplicate the edge metadata and lose the joint
    relationship (e.g. a parameterization: input concepts -> output via a formula).
    """

    sources: tuple[ArtifactIdentity, ...]
    target_family: str
    target_identity: str
    edge_type: str
    field: str
    metadata: Mapping[str, object] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class NodeContribution:
    """One field's contribution to a record's :class:`GraphNodeProjection`.

    ``label`` overrides the node label when not ``None`` (last writer wins, in
    field order); ``metadata`` entries are merged into the node metadata.
    """

    label: str | None
    metadata: Mapping[str, object]


@runtime_checkable
class GraphNodeKind(ProjectionKind, Protocol):
    """A projection kind contributing to the aggregated graph node of a record."""

    def contribute_node(self, field: CharterField, record: object) -> NodeContribution:
        ...


@runtime_checkable
class GraphEdgeKind(ProjectionKind, Protocol):
    """A projection kind emitting graph edges for an applying field."""

    def iter_edges(
        self,
        charter: FamilyCharter,
        record: object,
        field: CharterField,
    ) -> Iterator[GraphEdgeProjection]:
        ...


@runtime_checkable
class ArtifactDependencyKind(ProjectionKind, Protocol):
    """A projection kind emitting artifact dependencies for an applying field."""

    def iter_dependencies(
        self,
        charter: FamilyCharter,
        record: object,
        field: CharterField,
        source: ArtifactIdentity,
    ) -> Iterator[ArtifactDependency]:
        ...


@runtime_checkable
class GraphHyperedgeKind(ProjectionKind, Protocol):
    """A projection kind emitting N-ary graph hyperedges for an applying field."""

    def iter_hyperedges(
        self,
        charter: FamilyCharter,
        record: object,
        field: CharterField,
    ) -> Iterator[GraphHyperedgeProjection]:
        ...


class _GraphNodeKind:
    name = "graph-node"

    def applies(self, field: CharterField) -> bool:
        return field.graph_node_label or field.graph_metadata

    def contribute_node(self, field: CharterField, record: object) -> NodeContribution:
        value = getattr(record, field.name, None)
        label = (
            str(value)
            if field.graph_node_label and value is not None
            else None
        )
        metadata: dict[str, object] = {}
        if field.graph_metadata and value is not None:
            metadata[field.document_name or field.name] = value
        return NodeContribution(label=label, metadata=metadata)

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        return {
            "graph_node_label": field.graph_node_label,
            "graph_metadata": field.graph_metadata,
        }


class _GraphEdgeKind:
    name = "graph-edge"

    def applies(self, field: CharterField) -> bool:
        return field.graph_edge

    def iter_edges(
        self,
        charter: FamilyCharter,
        record: object,
        field: CharterField,
    ) -> Iterator[GraphEdgeProjection]:
        foreign_keys = _field_foreign_keys(field)
        if not foreign_keys:
            raise ValueError(
                f"{charter.family.name}.{field.name}: graph edge requires a foreign key"
            )
        value = getattr(record, field.name, None)
        for foreign_key in foreign_keys:
            source = _graph_edge_source(charter, record, field, foreign_key)
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

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        return {
            "graph_edge": field.graph_edge,
            "graph_edge_kind": field.graph_edge_kind,
            "graph_edge_source_field": field.graph_edge_source_field,
            "graph_edge_source_family": field.graph_edge_source_family,
        }


class _ArtifactDependencyKind:
    name = "artifact-dependency"

    def applies(self, field: CharterField) -> bool:
        return field.artifact_dependency

    def iter_dependencies(
        self,
        charter: FamilyCharter,
        record: object,
        field: CharterField,
        source: ArtifactIdentity,
    ) -> Iterator[ArtifactDependency]:
        foreign_keys = _field_foreign_keys(field)
        if not foreign_keys:
            raise ValueError(
                f"{charter.family.name}.{field.name}: artifact dependency "
                "requires a foreign key"
            )
        value = getattr(record, field.name, None)
        for foreign_key in foreign_keys:
            yield from _dependency_values(source, field, foreign_key, value)

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        return {"artifact_dependency": field.artifact_dependency}


class _ArtifactPlacementKind:
    """Git-tree placement projection (``artifact`` / ``artifact_name``).

    Contract-only: the placement is consumed by ``artifact_payload`` directly off
    the charter field; this kind exposes it to the schema contract.
    """

    name = "artifact"

    def applies(self, field: CharterField) -> bool:
        return field.artifact

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        return {"artifact_name": field.artifact_name}


class _LocalIdKind:
    """Source-local identity projection (``local_id`` / ``local_id_policy``)."""

    name = "local-id"

    def applies(self, field: CharterField) -> bool:
        return field.local_id

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        return {"local_id_policy": field.local_id_policy}


class _SearchKind:
    """FTS-participation projection hint (``search``)."""

    name = "search"

    def applies(self, field: CharterField) -> bool:
        return field.search

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        return {}


class _VectorKind:
    """Embedding-participation projection (``vector_dimensions``)."""

    name = "vector"

    def applies(self, field: CharterField) -> bool:
        return field.vector_dimensions is not None

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        return {"vector_dimensions": field.vector_dimensions}


class _HyperedgeKind:
    """N-ary hyperedge projection declared entirely via ``field.metadata``.

    Opting in needs NO new ``CharterField`` flag: a field carries
    ``metadata={"hyperedge": {"sources_field": ..., "source_family": ...,
    "target_field": ..., "target_family": ..., "kind": ...}}`` and this
    registered kind emits one :class:`GraphHyperedgeProjection` per record,
    preserving the antecedent grouping instead of fanning out into binary edges.
    This is the registry's extensibility proof: a new projection kind added with
    zero changes to ``CharterField`` or the existing consumers.
    """

    name = "hyperedge"

    def applies(self, field: CharterField) -> bool:
        return "hyperedge" in field.metadata

    def _config(self, field: CharterField) -> Mapping[str, object]:
        config = field.metadata["hyperedge"]
        if not isinstance(config, Mapping):
            raise ValueError(
                f"{field.name}: hyperedge metadata must be a mapping"
            )
        return config

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        config = self._config(field)
        return {"hyperedge": {key: config[key] for key in sorted(config)}}

    def iter_hyperedges(
        self,
        charter: FamilyCharter,
        record: object,
        field: CharterField,
    ) -> Iterator[GraphHyperedgeProjection]:
        config = self._config(field)
        target_value = getattr(record, str(config["target_field"]), None)
        if not isinstance(target_value, str) or not target_value:
            return
        source_family = str(config["source_family"])
        source_values = getattr(record, str(config["sources_field"]), None) or ()
        sources: list[ArtifactIdentity] = []
        for index, value in enumerate(source_values):
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"{charter.family.name}.{field.name}[{index}]: hyperedge "
                    "source must be a non-empty string"
                )
            sources.append(ArtifactIdentity(source_family, value))
        if not sources:
            return
        yield GraphHyperedgeProjection(
            sources=tuple(sources),
            target_family=str(config["target_family"]),
            target_identity=target_value,
            edge_type=str(config.get("kind") or field.name),
            field=field.name,
            metadata={
                key: item
                for key, item in field.metadata.items()
                if isinstance(key, str) and key != "hyperedge"
            },
        )


register_projection_kind(_GraphNodeKind())
register_projection_kind(_GraphEdgeKind())
register_projection_kind(_HyperedgeKind())
register_projection_kind(_ArtifactDependencyKind())
register_projection_kind(_ArtifactPlacementKind())
register_projection_kind(_LocalIdKind())
register_projection_kind(_SearchKind())
register_projection_kind(_VectorKind())


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
    kinds = [
        kind for kind in iter_projection_kinds()
        if isinstance(kind, ArtifactDependencyKind)
    ]
    for field in charter.fields:
        for kind in kinds:
            if kind.applies(field):
                yield from kind.iter_dependencies(charter, record, field, source)


def graph_node_projection(
    charter: FamilyCharter,
    record: object,
) -> GraphNodeProjection:
    identity = artifact_identity(charter, record)
    label = identity.identity
    metadata: dict[str, object] = {}
    kinds = [
        kind for kind in iter_projection_kinds()
        if isinstance(kind, GraphNodeKind)
    ]
    for field in charter.fields:
        for kind in kinds:
            if not kind.applies(field):
                continue
            contribution = kind.contribute_node(field, record)
            if contribution.label is not None:
                label = contribution.label
            metadata.update(contribution.metadata)
    return GraphNodeProjection(identity=identity, label=label, metadata=metadata)


def iter_graph_edges(
    charter: FamilyCharter,
    record: object,
) -> Iterator[GraphEdgeProjection]:
    kinds = [
        kind for kind in iter_projection_kinds()
        if isinstance(kind, GraphEdgeKind)
    ]
    for field in charter.fields:
        for kind in kinds:
            if kind.applies(field):
                yield from kind.iter_edges(charter, record, field)


def iter_graph_hyperedges(
    charter: FamilyCharter,
    record: object,
) -> Iterator[GraphHyperedgeProjection]:
    kinds = [
        kind for kind in iter_projection_kinds()
        if isinstance(kind, GraphHyperedgeKind)
    ]
    for field in charter.fields:
        for kind in kinds:
            if kind.applies(field):
                yield from kind.iter_hyperedges(charter, record, field)


def _field_foreign_keys(field: CharterField) -> tuple[ForeignKeySpec, ...]:
    if field.foreign_keys:
        return field.foreign_keys
    if field.foreign_key is not None:
        return (field.foreign_key,)
    return ()


def _graph_edge_source(
    charter: FamilyCharter,
    record: object,
    field: CharterField,
    foreign_key: ForeignKeySpec,
) -> ArtifactIdentity:
    source_field = field.graph_edge_source_field
    if source_field is None:
        return artifact_identity(charter, record)
    value = getattr(record, source_field, None)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{charter.family.name}.{field.name}: graph edge source field "
            f"{source_field!r} must be a non-empty string"
        )
    return ArtifactIdentity(
        field.graph_edge_source_family or foreign_key.source_family,
        value,
    )


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

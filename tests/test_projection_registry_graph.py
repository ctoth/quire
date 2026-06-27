"""Graph / artifact-dependency consumers dispatch via the projection registry.

Locks the registry-driven behavior of ``graph_node_projection``,
``iter_graph_edges`` and ``iter_artifact_dependencies`` to the same outputs the
hand-rolled ``if field.<flag>`` consumers produced, and proves the built-in
graph/artifact kinds are registered and satisfy their protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter
from quire.families import FamilyDefinition
from quire.projection_kinds import ProjectionKind, projection_kind
from quire.projections import (
    ArtifactDependencyKind,
    GraphEdgeKind,
    GraphNodeKind,
    graph_node_projection,
    iter_artifact_dependencies,
    iter_graph_edges,
)
from quire.references import ForeignKeySpec
from quire.versions import VersionId


class DemoFamily(str, Enum):
    CLAIMS = "claims"


class ClaimDoc(msgspec.Struct, kw_only=True):
    id: str
    label: str
    concept_id: str
    related_id: str | None = None
    premise_ids: tuple[str, ...] = ()
    artifact_code: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class Claim:
    id: str
    label: str
    concept_id: str
    family: DemoFamily = DemoFamily.CLAIMS
    related_id: str | None = None
    premise_ids: tuple[str, ...] = ()
    artifact_code: str | None = None
    status: str | None = None


def _version() -> VersionId:
    return VersionId("2026.05.26", allow_placeholder=False)


def _foreign_key(name: str, field: str) -> ForeignKeySpec:
    return ForeignKeySpec(
        name=name,
        contract_version=_version(),
        source_family="claims",
        source_field=field,
        target_family="concepts",
    )


def _charter() -> FamilyCharter:
    concept_fk = _foreign_key("claim_concept", "concept_id")
    premise_fk = ForeignKeySpec(
        name="claim_premise",
        contract_version=_version(),
        source_family="claims",
        source_field="premise_ids",
        target_family="claims",
        many=True,
    )
    family = FamilyDefinition(
        key=DemoFamily.CLAIMS,
        name="claims",
        contract_version=_version(),
        artifact_family=ArtifactFamily(
            name="claim",
            contract_version=_version(),
            doc_type=ClaimDoc,
            placement=FlatYamlPlacement("claims", str),
        ),
        identity_field="id",
    )
    return FamilyCharter(
        family=family,
        model=Claim,
        fields=(
            CharterField("id", str, primary_key=True, nullable=False),
            CharterField("label", str, graph_node_label=True),
            CharterField("family", DemoFamily),
            CharterField(
                "concept_id",
                str,
                foreign_key=concept_fk,
                artifact_dependency=True,
                graph_edge=True,
                graph_edge_kind="claim_of",
            ),
            CharterField(
                "related_id",
                str | None,
                foreign_key=_foreign_key("claim_related", "related_id"),
                graph_edge=True,
                graph_edge_kind="related",
                graph_edge_source_field="concept_id",
                graph_edge_source_family="concepts",
            ),
            CharterField(
                "premise_ids",
                tuple[str, ...],
                foreign_key=premise_fk,
                artifact_dependency=True,
            ),
            CharterField("artifact_code", str, artifact=True, nullable=True),
            CharterField("status", str, graph_metadata=True, nullable=True),
        ),
    )


def _record() -> Claim:
    return Claim(
        id="claim-a",
        label="Claim A",
        concept_id="concept-a",
        related_id="concept-related",
        premise_ids=("claim-b", "claim-c"),
        artifact_code="sha256:stored",
        status="accepted",
    )


def test_builtin_graph_artifact_kinds_are_registered() -> None:
    node = projection_kind("graph-node")
    edge = projection_kind("graph-edge")
    dep = projection_kind("artifact-dependency")

    assert isinstance(node, ProjectionKind)
    assert isinstance(edge, ProjectionKind)
    assert isinstance(dep, ProjectionKind)


def test_kinds_satisfy_their_consumer_subprotocols() -> None:
    assert isinstance(projection_kind("graph-node"), GraphNodeKind)
    assert isinstance(projection_kind("graph-edge"), GraphEdgeKind)
    assert isinstance(projection_kind("artifact-dependency"), ArtifactDependencyKind)


def test_kinds_are_disjoint_across_subprotocols() -> None:
    node = projection_kind("graph-node")
    edge = projection_kind("graph-edge")
    dep = projection_kind("artifact-dependency")

    assert not isinstance(node, GraphEdgeKind)
    assert not isinstance(node, ArtifactDependencyKind)
    assert not isinstance(edge, GraphNodeKind)
    assert not isinstance(edge, ArtifactDependencyKind)
    assert not isinstance(dep, GraphNodeKind)
    assert not isinstance(dep, GraphEdgeKind)


def test_kind_applies_tracks_field_flags() -> None:
    charter = _charter()
    fields = {field.name: field for field in charter.fields}
    node = projection_kind("graph-node")
    edge = projection_kind("graph-edge")
    dep = projection_kind("artifact-dependency")

    assert node.applies(fields["label"]) is True
    assert node.applies(fields["status"]) is True
    assert node.applies(fields["concept_id"]) is False
    assert edge.applies(fields["concept_id"]) is True
    assert edge.applies(fields["label"]) is False
    assert dep.applies(fields["concept_id"]) is True
    assert dep.applies(fields["premise_ids"]) is True
    assert dep.applies(fields["label"]) is False


def test_registry_graph_node_matches_reference_output() -> None:
    node = graph_node_projection(_charter(), _record())

    assert node.identity.family == "claims"
    assert node.identity.identity == "claim-a"
    assert node.label == "Claim A"
    assert node.metadata == {"status": "accepted"}


def test_registry_graph_edges_match_reference_output() -> None:
    edges = tuple(iter_graph_edges(_charter(), _record()))

    projected = [
        (
            edge.edge_type,
            edge.source.family,
            edge.source.identity,
            edge.target_family,
            edge.target_identity,
            edge.index,
        )
        for edge in edges
    ]
    assert projected == [
        ("claim_of", "claims", "claim-a", "concepts", "concept-a", None),
        ("related", "concepts", "concept-a", "concepts", "concept-related", None),
    ]


def test_registry_artifact_dependencies_match_reference_output() -> None:
    dependencies = tuple(iter_artifact_dependencies(_charter(), _record()))

    projected = [
        (item.target_family, item.target_identity, item.field, item.index)
        for item in dependencies
    ]
    assert projected == [
        ("concepts", "concept-a", "concept_id", None),
        ("claims", "claim-b", "premise_ids", 0),
        ("claims", "claim-c", "premise_ids", 1),
    ]


def test_schema_payload_keeps_per_flag_keys() -> None:
    charter = _charter()
    fields = {field.name: field for field in charter.fields}

    assert projection_kind("graph-node").schema_payload(fields["label"]) == {
        "graph_node_label": True,
        "graph_metadata": False,
    }
    assert projection_kind("graph-edge").schema_payload(fields["concept_id"]) == {
        "graph_edge": True,
        "graph_edge_kind": "claim_of",
        "graph_edge_source_field": None,
        "graph_edge_source_family": None,
    }
    assert projection_kind("artifact-dependency").schema_payload(
        fields["concept_id"]
    ) == {"artifact_dependency": True}

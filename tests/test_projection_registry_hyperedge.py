"""N-ary hyperedge projection, declared via field metadata (no CharterField flag).

Proves the registry's extensibility: a new projection kind (``hyperedge``) is
added with zero changes to CharterField or the existing consumers, opting in
purely through ``field.metadata``. The motivating case is a parameterization —
N input concepts jointly map to one output — which the binary graph edge would
fan out into N edges that lose the joint grouping.
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
    ArtifactIdentity,
    GraphHyperedgeKind,
    GraphHyperedgeProjection,
    iter_graph_hyperedges,
)
from quire.versions import VersionId
from quire.contracts import contract_version


class DemoFamily(str, Enum):
    PARAMETERIZATIONS = "parameterizations"


class ParamDoc(msgspec.Struct, kw_only=True):
    id: str
    input_concept_ids: tuple[str, ...] = ()
    output_concept_id: str | None = None


@dataclass(frozen=True)
class Parameterization:
    id: str
    input_concept_ids: tuple[str, ...] = ()
    output_concept_id: str | None = None
    family: DemoFamily = DemoFamily.PARAMETERIZATIONS


def _version() -> VersionId:
    return contract_version("2026.05.26")


_HYPEREDGE_META = {
    "hyperedge": {
        "sources_field": "input_concept_ids",
        "source_family": "concepts",
        "target_field": "output_concept_id",
        "target_family": "concepts",
        "kind": "parameterization",
    }
}


def _charter() -> FamilyCharter:
    family = FamilyDefinition(
        key=DemoFamily.PARAMETERIZATIONS,
        name="parameterizations",
        contract_version=_version(),
        artifact_family=ArtifactFamily(
            name="parameterization",
            contract_version=_version(),
            doc_type=ParamDoc,
            placement=FlatYamlPlacement("parameterizations", str),
        ),
        identity_field="id",
    )
    return FamilyCharter(
        family=family,
        model=Parameterization,
        fields=(
            CharterField("id", str, primary_key=True, nullable=False),
            CharterField(
                "input_concept_ids",
                tuple[str, ...],
                metadata=_HYPEREDGE_META,
            ),
            CharterField("output_concept_id", str | None, nullable=True),
        ),
    )


def _record() -> Parameterization:
    return Parameterization(
        id="param-a",
        input_concept_ids=("concept-x", "concept-y", "concept-z"),
        output_concept_id="concept-out",
    )


def test_hyperedge_kind_is_registered_and_satisfies_subprotocol() -> None:
    kind = projection_kind("hyperedge")
    assert isinstance(kind, ProjectionKind)
    assert isinstance(kind, GraphHyperedgeKind)


def test_hyperedge_applies_only_to_metadata_carrying_fields() -> None:
    kind = projection_kind("hyperedge")
    fields = {field.name: field for field in _charter().fields}
    assert kind.applies(fields["input_concept_ids"]) is True
    assert kind.applies(fields["id"]) is False
    assert kind.applies(fields["output_concept_id"]) is False


def test_emits_one_hyperedge_preserving_antecedent_grouping() -> None:
    edges = list(iter_graph_hyperedges(_charter(), _record()))
    assert len(edges) == 1
    edge = edges[0]
    assert edge == GraphHyperedgeProjection(
        sources=(
            ArtifactIdentity("concepts", "concept-x"),
            ArtifactIdentity("concepts", "concept-y"),
            ArtifactIdentity("concepts", "concept-z"),
        ),
        target_family="concepts",
        target_identity="concept-out",
        edge_type="parameterization",
        field="input_concept_ids",
        metadata={},
    )


def test_no_hyperedge_when_target_missing() -> None:
    record = Parameterization(
        id="param-b",
        input_concept_ids=("concept-x",),
        output_concept_id=None,
    )
    assert list(iter_graph_hyperedges(_charter(), record)) == []


def test_no_hyperedge_when_sources_empty() -> None:
    record = Parameterization(
        id="param-c",
        input_concept_ids=(),
        output_concept_id="concept-out",
    )
    assert list(iter_graph_hyperedges(_charter(), record)) == []


def test_schema_payload_is_deterministic_and_sorted() -> None:
    kind = projection_kind("hyperedge")
    field = {f.name: f for f in _charter().fields}["input_concept_ids"]
    payload = kind.schema_payload(field)
    inner = payload["hyperedge"]
    assert list(inner.keys()) == sorted(inner.keys())
    assert inner["kind"] == "parameterization"
    assert inner["sources_field"] == "input_concept_ids"

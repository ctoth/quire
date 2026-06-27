from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import msgspec
import pytest

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter
from quire.families import FamilyDefinition
from quire.projections import (
    artifact_digest,
    artifact_identity,
    artifact_payload,
    graph_node_projection,
    iter_artifact_dependencies,
    iter_graph_edges,
)
from quire.references import ForeignKeySpec
from quire.versions import VersionId
from quire.contracts import contract_version


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
    return contract_version("2026.05.26")


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


def test_artifact_identity_uses_family_identity_field() -> None:
    assert artifact_identity(_charter(), _record()).identity == "claim-a"


def test_artifact_payload_omits_artifact_fields_before_hashing() -> None:
    payload = artifact_payload(_charter(), _record())

    assert isinstance(payload, dict)
    assert payload["id"] == "claim-a"
    assert payload["family"] == "claims"
    assert "artifact_code" not in payload
    assert artifact_digest(_charter(), _record()).startswith("sha256:")


def test_iter_artifact_dependencies_projects_scalar_and_many_foreign_keys() -> None:
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


def test_graph_projection_uses_label_metadata_and_graph_edges() -> None:
    charter = _charter()
    record = _record()

    node = graph_node_projection(charter, record)
    edges = tuple(iter_graph_edges(charter, record))

    assert node.identity.family == "claims"
    assert node.label == "Claim A"
    assert node.metadata == {"status": "accepted"}
    assert len(edges) == 2
    assert edges[0].edge_type == "claim_of"
    assert edges[0].target_family == "concepts"
    assert edges[0].target_identity == "concept-a"
    assert edges[1].edge_type == "related"
    assert edges[1].source.family == "concepts"
    assert edges[1].source.identity == "concept-a"
    assert edges[1].target_identity == "concept-related"


def test_dependency_field_requires_foreign_key() -> None:
    family = _charter().family
    charter = FamilyCharter(
        family=family,
        model=Claim,
        fields=(
            CharterField("id", str, primary_key=True, nullable=False),
            CharterField("concept_id", str, artifact_dependency=True),
        ),
    )

    with pytest.raises(ValueError, match="requires a foreign key"):
        tuple(iter_artifact_dependencies(charter, _record()))

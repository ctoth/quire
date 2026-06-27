from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, CharterRelationship, FamilyCharter
from quire.families import FamilyDefinition
from quire.references import ReferenceKey
from quire.versions import VersionId


class DemoFamily(str, Enum):
    CLAIMS = "claims"


class ClaimDoc(msgspec.Struct):
    artifact_id: str


@dataclass
class Claim:
    artifact_id: str
    concept_id: str


def _claim_family() -> FamilyDefinition[object, DemoFamily, str, ClaimDoc]:
    version = VersionId("2026.05.25", allow_placeholder=False)
    return FamilyDefinition(
        key=DemoFamily.CLAIMS,
        name="claims",
        contract_version=version,
        artifact_family=ArtifactFamily(
            name="claim_artifact",
            contract_version=version,
            doc_type=ClaimDoc,
            placement=FlatYamlPlacement("claims", str),
        ),
        identity_field="artifact_id",
        reference_keys=(ReferenceKey.field("concept_id"),),
    )


def test_charter_field_typed_document_defaults_and_states() -> None:
    assert CharterField("artifact_id", str).document is True
    assert CharterField("artifact_id", str, document=False).document is False
    assert CharterField(
        "artifact_id",
        str,
        states=frozenset({"source-local", "canonical"}),
    ).states == frozenset({"source-local", "canonical"})


def test_charter_relationship_typed_artifact_dependency() -> None:
    assert CharterRelationship(
        "concept",
        "concepts",
        artifact_dependency=True,
    ).artifact_dependency is True


def test_family_charter_document_contract_version() -> None:
    contract_version = VersionId("2026.05.25")
    charter = FamilyCharter(
        family=_claim_family(),
        model=Claim,
        fields=(CharterField("artifact_id", str),),
        document_contract_version=contract_version,
    )

    assert charter.document_contract_version == contract_version


def test_to_schema_object_projects_typed_attributes() -> None:
    contract_version = VersionId("2026.05.25")
    charter = FamilyCharter(
        family=_claim_family(),
        model=Claim,
        fields=(
            CharterField(
                "artifact_id",
                str,
                document=False,
                document_name="artifact-id",
                document_order=10,
                states=frozenset({"source-local", "canonical"}),
                artifact=True,
                artifact_name="artifact-id",
                graph_node_label=True,
                graph_metadata=True,
                local_id=True,
                local_id_policy="claim-local",
                contract_version=contract_version,
                parse_boundary="yaml",
            ),
        ),
        relationships=(
            CharterRelationship(
                "concept",
                "concepts",
                artifact_dependency=True,
                graph_edge=True,
                graph_edge_kind="mentions",
                states=frozenset({"canonical"}),
            ),
        ),
        document_contract_version=contract_version,
    )

    schema = charter.to_schema_object()
    schema_field = schema.field("artifact_id")
    schema_relationship = schema.relationships[0]

    assert schema.document_contract_version == contract_version
    assert schema_field.document is False
    assert schema_field.document_name == "artifact-id"
    assert schema_field.document_order == 10
    assert schema_field.states == frozenset({"source-local", "canonical"})
    assert schema_field.charter_field.artifact is True
    assert schema_field.charter_field.artifact_name == "artifact-id"
    assert schema_field.charter_field.graph_node_label is True
    assert schema_field.charter_field.graph_metadata is True
    assert schema_field.charter_field.local_id is True
    assert schema_field.charter_field.local_id_policy == "claim-local"
    assert schema_field.contract_version == contract_version
    assert schema_field.parse_boundary == "yaml"
    assert schema_relationship.artifact_dependency is True
    assert schema_relationship.graph_edge is True
    assert schema_relationship.graph_edge_kind == "mentions"
    assert schema_relationship.states == frozenset({"canonical"})

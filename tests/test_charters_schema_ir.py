from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter, charter_catalog
from quire.families import FamilyDefinition
from quire.references import ForeignKeySpec
from quire.schema_catalog import SchemaCatalog
from quire.sql_types import python_type_to_sql
from quire.versions import VersionId


class DemoFamily(str, Enum):
    CLAIMS = "claims"
    CONCEPTS = "concepts"


class ClaimDoc(msgspec.Struct):
    artifact_id: str
    concept_id: str
    trust: dict[str, object] | None = None


@dataclass
class Claim:
    artifact_id: str
    concept_id: str
    trust: "SourceTrust | None" = None


@dataclass(frozen=True)
class SourceTrust:
    score: float
    method: str


def _claim_family() -> FamilyDefinition[object, DemoFamily, str, ClaimDoc]:
    version = VersionId("2026.05.20", allow_placeholder=False)
    foreign_key = ForeignKeySpec(
        name="claim_concept",
        contract_version=version,
        source_family="claims",
        source_field="concept_id",
        target_family="concepts",
    )
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
        foreign_keys=(foreign_key,),
        metadata={"semantic": True, "import_order": 20},
    )


def test_family_charter_composes_with_existing_family_and_reference_apis() -> None:
    family = _claim_family()
    charter = FamilyCharter(
        family=family,
        model=Claim,
        lifecycle_states=("authored", "checked", "canonical"),
        fields=(
            CharterField("artifact_id", str, primary_key=True, nullable=False),
            CharterField(
                "concept_id",
                str,
                nullable=False,
                foreign_key=family.foreign_keys[0],
                index=True,
            ),
            CharterField("trust", SourceTrust, nullable=True, json_value_object=True),
            CharterField("draft_note", str, source_local_only=True),
            CharterField("canonical_rank", int, canonical_only=True, default=0),
        ),
        semantic_metadata={"owner": "propstore.claims", "semantic": True},
    )

    schema = charter.to_schema_object()

    assert schema.name == "claims"
    assert schema.family_name == "claims"
    assert schema.artifact_family_name == "claim_artifact"
    assert schema.artifact_contract_version == "2026.05.20"
    assert schema.model_path == "test_charters_schema_ir.Claim"
    assert schema.semantic_metadata["owner"] == "propstore.claims"
    assert schema.lifecycle_states == ("authored", "checked", "canonical")
    concept_field = schema.field("concept_id")
    assert concept_field.foreign_key is not None
    assert concept_field.foreign_key.target_family == "concepts"
    assert concept_field.index is True
    assert schema.field("trust").json_value_object is True
    assert schema.field("draft_note").source_local_only is True
    assert schema.field("canonical_rank").canonical_only is True


def test_python_type_mapping_uses_types_without_marker_wrappers() -> None:
    assert python_type_to_sql(str).ddl_name == "TEXT"
    assert python_type_to_sql(int).ddl_name == "INTEGER"
    assert python_type_to_sql(float).ddl_name == "REAL"
    assert python_type_to_sql(bool).ddl_name == "BOOLEAN"
    assert python_type_to_sql(SourceTrust, json_value_object=True).storage_kind == "json"
    assert python_type_to_sql(DemoFamily, enum_type=DemoFamily).storage_kind == "enum"


def test_schema_catalog_payload_and_hash_are_stable() -> None:
    family = _claim_family()
    first = FamilyCharter(
        family=family,
        model=Claim,
        fields=(
            CharterField("artifact_id", str, primary_key=True, nullable=False),
            CharterField("concept_id", str, nullable=False, foreign_key=family.foreign_keys[0]),
        ),
        semantic_metadata={"semantic": True},
    )
    second = FamilyCharter(
        family=family,
        model=Claim,
        fields=tuple(reversed(first.fields)),
        semantic_metadata={"semantic": True},
    )

    first_catalog = charter_catalog(first, metadata={"owner": "test"})
    second_catalog = SchemaCatalog(objects=(second.to_schema_object(),), metadata={"owner": "test"})

    assert first_catalog.payload() == second_catalog.payload()
    assert first_catalog.schema_hash() == second_catalog.schema_hash()
    payload = first_catalog.payload()
    objects = cast(tuple[dict[str, Any], ...], payload["objects"])
    family_payload = cast(dict[str, object], objects[0]["family"])
    assert family_payload["artifact_family"] == "claim_artifact"
    assert first_catalog.schema_hash().startswith("sha256:")

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import msgspec

from quire import DocumentBatchSpec
from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter
from quire.families import FamilyDefinition
from quire.references import ForeignKeySpec
from quire.versions import VersionId


class DemoFamily(str, Enum):
    CLAIMS = "claims"


class ClaimDoc(msgspec.Struct):
    id: str
    source_id: str


class ClaimBatchItem(msgspec.Struct):
    id: str
    source_id: str


@dataclass
class Claim:
    id: str
    source_id: str


def _family() -> FamilyDefinition[object, DemoFamily, str, ClaimDoc]:
    version = VersionId("2026.05.24", allow_placeholder=False)
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
        identity_field="id",
    )


def _foreign_key(name: str, target_family: str) -> ForeignKeySpec:
    return ForeignKeySpec(
        name=name,
        contract_version=VersionId("2026.05.24", allow_placeholder=False),
        source_family="claims",
        source_field="source_id",
        target_family=target_family,
    )


def test_charter_field_projects_multiple_foreign_keys() -> None:
    first = _foreign_key("claim_source", "sources")
    second = _foreign_key("claim_author", "authors")

    schema_field = CharterField(
        "source_id",
        str,
        foreign_keys=(first, second),
    ).to_schema_field()

    assert tuple(key.name for key in schema_field.charter_field.foreign_keys) == (
        "claim_source",
        "claim_author",
    )
    assert tuple(key.target_family for key in schema_field.charter_field.foreign_keys) == (
        "sources",
        "authors",
    )


def test_family_charter_projects_batch_specs() -> None:
    batch_spec = DocumentBatchSpec(
        batch_name="claim_batch",
        item_type=ClaimBatchItem,
        items_field="claims",
    )

    schema_object = FamilyCharter(
        family=_family(),
        model=Claim,
        fields=(CharterField("id", str, primary_key=True, nullable=False),),
        batch_specs=(batch_spec,),
    ).to_schema_object()

    assert schema_object.batch_specs == (batch_spec,)


def test_existing_single_foreign_key_behavior_unchanged() -> None:
    foreign_key = _foreign_key("claim_source", "sources")

    schema_field = CharterField(
        "source_id",
        str,
        foreign_key=foreign_key,
    ).to_schema_field()

    assert schema_field.charter_field.foreign_key is not None
    assert schema_field.charter_field.foreign_key.name == "claim_source"
    assert schema_field.charter_field.foreign_key.target_family == "sources"

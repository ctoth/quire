from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter
from quire.families import FamilyDefinition
from quire.versions import VersionId


class DemoFamily(str, Enum):
    DEMOS = "demos"


class DemoDoc(msgspec.Struct):
    id: str


@dataclass
class Demo:
    id: str
    name: str
    value: int


def _minimal_family() -> FamilyDefinition[object, DemoFamily, str, DemoDoc]:
    version = VersionId("2026.05.25", allow_placeholder=False)
    return FamilyDefinition(
        key=DemoFamily.DEMOS,
        name="demos",
        contract_version=version,
        artifact_family=ArtifactFamily(
            name="demo_artifact",
            contract_version=version,
            doc_type=DemoDoc,
            placement=FlatYamlPlacement("demos", str),
        ),
        identity_field="id",
    )


def _minimal_charter() -> FamilyCharter:
    return FamilyCharter(
        family=_minimal_family(),
        model=Demo,
        fields=(
            CharterField("id", str, primary_key=True),
            CharterField("name", str),
            CharterField("value", int, document=False),
        ),
    )


def test_generated_document_includes_document_fields_only() -> None:
    document_type = _minimal_charter().generated_document()

    assert issubclass(document_type, msgspec.Struct)
    assert document_type.__name__ == "DemosDocument"
    assert document_type.__struct_fields__ == ("id", "name")


def test_generated_document_filters_state_conditional_fields() -> None:
    charter = FamilyCharter(
        family=_minimal_family(),
        model=Demo,
        fields=(
            CharterField("id", str, primary_key=True),
            CharterField("proposed_only", str, states=frozenset({"proposal"})),
            CharterField("canonical_only", str, states=frozenset({"canonical"})),
        ),
    )

    proposal_document = charter.generated_document("proposal")
    canonical_document = charter.generated_document("canonical")
    all_state_document = charter.generated_document()

    assert set(proposal_document.__struct_fields__) == {"id", "proposed_only"}
    assert set(canonical_document.__struct_fields__) == {"id", "canonical_only"}
    assert set(all_state_document.__struct_fields__) == {
        "id",
        "proposed_only",
        "canonical_only",
    }


def test_document_codec_round_trips_generated_document() -> None:
    charter = _minimal_charter()
    document_type = charter.generated_document()
    document = document_type(id="demo-1", name="Demo")
    codec = charter.document_codec()

    encoded = codec.encode(document)

    assert codec.decode(encoded, document_type, source="demo.yaml") == document


def test_generated_document_is_memoized_by_state() -> None:
    charter = _minimal_charter()

    assert charter.generated_document() is charter.generated_document()
    assert charter.generated_document("proposal") is charter.generated_document("proposal")
    assert charter.generated_document() is not charter.generated_document("proposal")

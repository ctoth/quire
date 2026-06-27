from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import get_type_hints

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter, charter_catalog
from quire.families import FamilyDefinition
from quire.sqlalchemy_schema import build_sqlalchemy_schema
from quire.contracts import contract_version


class DemoFamily(str, Enum):
    DEMOS = "demos"


class DemoDoc(msgspec.Struct):
    id: str


@dataclass
class Demo:
    id: str
    base: str | None = None
    is_dimensionless: bool = False


def _minimal_family() -> FamilyDefinition[object, DemoFamily, str, DemoDoc]:
    version = contract_version("2026.05.25")
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


def test_nullable_field_generates_optional_field_with_none_default() -> None:
    charter = FamilyCharter(
        family=_minimal_family(),
        model=Demo,
        fields=(
            CharterField("id", str, primary_key=True, nullable=False),
            CharterField("base", str, nullable=True),
        ),
    )

    document_type = charter.generated_document()
    document = document_type(id="demo")

    assert getattr(document, "base") is None
    assert get_type_hints(document_type)["base"] == str | None


def test_document_name_controls_document_shape_without_renaming_storage() -> None:
    charter = FamilyCharter(
        family=_minimal_family(),
        model=Demo,
        fields=(
            CharterField("id", str, primary_key=True, nullable=False),
            CharterField(
                "is_dimensionless",
                bool,
                nullable=False,
                document_name="dimensionless",
            ),
        ),
    )

    document_type = charter.generated_document()
    document = document_type(id="demo", dimensionless=True)
    codec = charter.document_codec()
    schema = build_sqlalchemy_schema(charter_catalog(charter))

    assert document_type.__struct_fields__ == ("id", "dimensionless")
    assert codec.payload(document) == {"id": "demo", "dimensionless": True}
    assert "dimensionless" not in schema.table("demos").c
    assert "is_dimensionless" in schema.table("demos").c


def test_pep604_optional_field_works_in_sql_projection_and_generated_document() -> None:
    charter = FamilyCharter(
        family=_minimal_family(),
        model=Demo,
        fields=(
            CharterField("id", str, primary_key=True, nullable=False),
            CharterField("base", str | None),
        ),
    )

    schema = build_sqlalchemy_schema(charter_catalog(charter))
    document_type = charter.generated_document()
    document = document_type(id="demo")

    assert schema.table("demos").c.base.nullable is True
    assert getattr(document, "base") is None
    assert get_type_hints(document_type)["base"] == str | None

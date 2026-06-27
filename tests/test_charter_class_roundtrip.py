"""STEP 2 roundtrip proof: decode REAL propstore ``knowledge/`` artifacts through
BOTH a hand-written charter (mirroring propstore's ``FORM_CHARTER``) and a
declarative-class equivalent, asserting identical decoded values AND identical
re-encoded bytes. The declarative shape must be a behaviour-preserving drop-in.

Fixtures in ``tests/fixtures/charter_class/`` are verbatim copies of real authored
form documents from the propstore ``knowledge`` store (``forms/distance.yaml`` etc.).
The ``form`` family exercises a ``document_name`` rename (``is_dimensionless`` column
-> ``dimensionless`` doc field), ``document_order`` (forces ``dimensionless`` first),
and JSON-blob nested fields (``dimensions``/``common_alternatives``/...).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import msgspec
import pytest

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter, FamilyModel
from quire.charter_class import CharterDoc, charter, charter_field
from quire.families import FamilyDefinition
from quire.contracts import contract_version


_FIXTURES = Path(__file__).parent / "fixtures" / "charter_class"
_VERSION = contract_version("2026.05.20")


# Nested struct types shared by BOTH charters (these are plain msgspec.Structs in
# propstore today; identity must be shared so encoded bytes match).
class FormAlternativeDocument(CharterDoc):
    unit: str
    type: str
    multiplier: float = 1.0
    offset: float = 0.0
    base: float = 10.0
    divisor: float = 1.0
    reference: float = 1.0


class FormExtraUnitDocument(CharterDoc):
    symbol: str
    dimensions: dict[str, int] = msgspec.field(default_factory=dict)


# --- hand-written FORM_CHARTER (mirrors propstore forms/declaration.py) ------


def _hand_written_charter() -> FamilyCharter:
    model = type("FormModel", (FamilyModel,), {})
    model.__module__ = __name__
    model.__qualname__ = "FormModel"
    return FamilyCharter(
        family=FamilyDefinition(
            key="form",
            name="form",
            contract_version=_VERSION,
            artifact_family=ArtifactFamily(
                name="propstore-world-form",
                contract_version=_VERSION,
                doc_type=model,
                placement=FlatYamlPlacement(".derived/form", str),
            ),
            identity_field="name",
        ),
        model=model,
        fields=(
            CharterField("name", str, primary_key=True, nullable=False),
            CharterField("kind", str, nullable=True),
            CharterField("unit_symbol", str, nullable=True),
            CharterField(
                "is_dimensionless",
                bool,
                nullable=False,
                default_sql="0",
                document_name="dimensionless",
                document_order=0,
            ),
            CharterField("dimensions", dict[str, int], parse_boundary="json", nullable=True),
            CharterField("base", str, nullable=True),
            CharterField("qudt", str, nullable=True),
            CharterField("parameters", dict[str, Any], parse_boundary="json", nullable=True),
            CharterField(
                "common_alternatives",
                tuple[FormAlternativeDocument, ...],
                parse_boundary="json",
                nullable=True,
                default=(),
            ),
            CharterField(
                "delta_alternatives",
                tuple[FormAlternativeDocument, ...],
                parse_boundary="json",
                nullable=True,
                default=(),
            ),
            CharterField("note", str, nullable=True),
            CharterField(
                "extra_units",
                tuple[FormExtraUnitDocument, ...],
                parse_boundary="json",
                nullable=True,
                default=(),
            ),
            CharterField("min", float, nullable=True),
            CharterField("max", float, nullable=True),
        ),
        semantic_metadata={"semantic": "propstore.world"},
    )


# --- declarative equivalent --------------------------------------------------


@charter(
    key="form",
    name="form",
    contract_version="2026.05.20",
    placement=".derived/form",
    identity_field="name",
    semantic="propstore.world",
    artifact_family_name="propstore-world-form",
    model_name="FormModel",
)
class FormDocument(CharterDoc):
    # Required fields first (msgspec requirement). `dimensionless` carries
    # `order=0` to mirror the hand-written charter's `document_order=0` so the
    # generated-document field order AND the schema document_order both match.
    name: Annotated[str, charter_field(primary_key=True)]
    dimensionless: Annotated[
        bool, charter_field(column_name="is_dimensionless", nullable=False, default_sql="0", order=0)
    ]
    kind: str | None = None
    unit_symbol: str | None = None
    dimensions: Annotated[dict[str, int] | None, charter_field(json=True)] = None
    base: str | None = None
    qudt: str | None = None
    parameters: Annotated[dict[str, Any] | None, charter_field(json=True)] = None
    common_alternatives: Annotated[
        tuple[FormAlternativeDocument, ...], charter_field(json=True, nullable=True)
    ] = ()
    delta_alternatives: Annotated[
        tuple[FormAlternativeDocument, ...], charter_field(json=True, nullable=True)
    ] = ()
    note: str | None = None
    extra_units: Annotated[
        tuple[FormExtraUnitDocument, ...], charter_field(json=True, nullable=True)
    ] = ()
    min: float | None = None
    max: float | None = None


def _derived_charter() -> FamilyCharter:
    return FormDocument.__charter__  # type: ignore[attr-defined]


_FIXTURE_NAMES = ["form_distance.yaml", "form_boolean.yaml", "form_dimensionless.yaml"]


def test_generated_document_field_order_matches() -> None:
    # The CONSUMER-FACING generated document field order is identical: the
    # hand-written charter uses document_order=0 to hoist `dimensionless`, and the
    # declarative class achieves the same order by declaring it second (required)
    # with charter_field(order=0).
    hand = _hand_written_charter().generated_document()
    derived = _derived_charter().generated_document()
    assert hand.__struct_fields__ == derived.__struct_fields__
    assert hand.__struct_fields__[:2] == ("name", "dimensionless")


def test_schema_fields_equivalent_modulo_column_order() -> None:
    # Each SchemaField is identical when keyed by name (types, nullability,
    # primary_key, default_sql, document_name, document_order all match).
    hand = {f.name: f for f in _hand_written_charter().to_schema_object().fields}
    derived = {f.name: f for f in _derived_charter().to_schema_object().fields}
    assert hand == derived

    # MIGRATION NOTE (genuine design gap, form family only): the hand-written
    # FORM_CHARTER declares `is_dimensionless` 4th in its field TUPLE (DDL column
    # order) but sets document_order=0 to make it 2nd in the document. The
    # declarative class has a single attribute order, and msgspec forbids a
    # required field (`dimensionless`) after optional ones, so the class cannot
    # reproduce the interleaved DDL column order `name,kind,unit_symbol,
    # is_dimensionless`. The generated DOCUMENT (consumer-facing bytes) is exact;
    # only the internal SchemaObject field-LIST order reorders for this one field.
    hand_order = [f.name for f in _hand_written_charter().to_schema_object().fields]
    derived_order = [f.name for f in _derived_charter().to_schema_object().fields]
    assert hand_order != derived_order  # the documented divergence
    assert set(hand_order) == set(derived_order)
    assert hand_order.index("is_dimensionless") == 3
    assert derived_order.index("is_dimensionless") == 1


@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_real_artifact_roundtrip_identical(fixture_name: str) -> None:
    payload = (_FIXTURES / fixture_name).read_bytes()

    hand_charter = _hand_written_charter()
    derived_charter = _derived_charter()
    hand_type = hand_charter.generated_document()
    derived_type = derived_charter.generated_document()
    hand_codec = hand_charter.document_codec()
    derived_codec = derived_charter.document_codec()

    hand_doc = hand_codec.decode(payload, hand_type, source=fixture_name)
    derived_doc = derived_codec.decode(payload, derived_type, source=fixture_name)

    # Identical decoded values (compare the public attributes).
    hand_values = {f: getattr(hand_doc, f) for f in hand_type.__struct_fields__}
    derived_values = {f: getattr(derived_doc, f) for f in derived_type.__struct_fields__}
    assert hand_values == derived_values

    # Identical re-encoded bytes (the behaviour-preserving drop-in requirement).
    assert hand_codec.encode(hand_doc) == derived_codec.encode(derived_doc)

    # And the re-encoded document re-decodes to the same value (true round-trip).
    re_decoded = derived_codec.decode(
        derived_codec.encode(derived_doc), derived_type, source=fixture_name
    )
    assert {f: getattr(re_decoded, f) for f in derived_type.__struct_fields__} == derived_values

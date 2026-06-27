"""STEP 0 spike: prove the declarative ``@charter`` shape derives a FamilyCharter
equivalent to a hand-written one for a real simple family (``source-trust-quality``).

Equivalence is defined behaviourally (not object identity):
- identical generated-document ``__struct_fields__``, field types, defaults, name;
- identical ``to_schema_object()`` SchemaObject;
- identical document-codec round-trip bytes.

The hand-written charter and the declarative class use a model class with the SAME
module + qualname so ``model_path`` matches by construction (the declarative builder
generates ``<ClassName>Model`` in ``cls.__module__``).
"""

from __future__ import annotations

from enum import Enum

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter, FamilyModel
from quire.charter_class import CharterDoc, charter
from quire.families import FamilyDefinition
from quire.contracts import contract_version


class ProvenanceStatus(str, Enum):
    MEASURED = "measured"
    CALIBRATED = "calibrated"
    STATED = "stated"
    DEFAULTED = "defaulted"
    VACUOUS = "vacuous"


_VERSION = contract_version("2026.05.25")


# --- declarative shape -----------------------------------------------------


@charter(
    key="source-trust-quality",
    name="source-trust-quality",
    contract_version="2026.05.25",
    placement=".derived/source-trust-quality",
    identity_field="status",
    semantic="propstore.source",
)
class SourceTrustQualityDocument(CharterDoc):
    status: ProvenanceStatus
    b: float | int
    d: float | int
    u: float | int
    a: float | int


# --- hand-written shape (mirrors propstore sources/declaration.py:63-85) ----


def _hand_written_charter() -> FamilyCharter:
    # Generated-model name/module must match what @charter produces so that
    # SchemaObject.model_path is identical.
    model = type(
        "SourceTrustQualityDocumentModel",
        (FamilyModel,),
        {},
    )
    model.__module__ = SourceTrustQualityDocument.__module__
    model.__qualname__ = "SourceTrustQualityDocumentModel"
    return FamilyCharter(
        family=FamilyDefinition(
            key="source-trust-quality",
            name="source-trust-quality",
            contract_version=_VERSION,
            artifact_family=ArtifactFamily(
                name="source-trust-quality",
                contract_version=_VERSION,
                doc_type=model,
                placement=FlatYamlPlacement(".derived/source-trust-quality", str),
            ),
            identity_field="status",
        ),
        model=model,
        fields=(
            CharterField("status", ProvenanceStatus, nullable=False, enum_type=ProvenanceStatus),
            CharterField("b", float | int, nullable=False),
            CharterField("d", float | int, nullable=False),
            CharterField("u", float | int, nullable=False),
            CharterField("a", float | int, nullable=False),
        ),
        semantic_metadata={"semantic": "propstore.source"},
    )


def _derived_charter() -> FamilyCharter:
    return SourceTrustQualityDocument.__charter__  # type: ignore[attr-defined]


# --- equivalence assertions ------------------------------------------------


def test_generated_document_struct_fields_match() -> None:
    hand = _hand_written_charter().generated_document()
    derived = _derived_charter().generated_document()
    assert hand.__struct_fields__ == derived.__struct_fields__
    assert hand.__name__ == derived.__name__
    # Field encode-types must match (msgspec records them on the struct).
    hand_encode = {f.name: f.type for f in msgspec.structs.fields(hand)}
    derived_encode = {f.name: f.type for f in msgspec.structs.fields(derived)}
    assert hand_encode == derived_encode
    hand_defaults = {f.name: f.default for f in msgspec.structs.fields(hand)}
    derived_defaults = {f.name: f.default for f in msgspec.structs.fields(derived)}
    assert hand_defaults == derived_defaults


def test_schema_object_matches() -> None:
    assert _hand_written_charter().to_schema_object() == _derived_charter().to_schema_object()


def test_document_codec_bytes_match() -> None:
    hand_charter = _hand_written_charter()
    derived_charter = _derived_charter()
    hand_doc_type = hand_charter.generated_document()
    derived_doc_type = derived_charter.generated_document()

    values = dict(status=ProvenanceStatus.MEASURED, b=0.7, d=0.1, u=0.2, a=0.5)
    hand_doc = hand_doc_type(**values)
    derived_doc = derived_doc_type(**values)

    hand_codec = hand_charter.document_codec()
    derived_codec = derived_charter.document_codec()

    hand_bytes = hand_codec.encode(hand_doc)
    derived_bytes = derived_codec.encode(derived_doc)
    assert hand_bytes == derived_bytes

    # Round-trip through the derived codec back into the derived doc type.
    decoded = derived_codec.decode(derived_bytes, derived_doc_type, source="x.yaml")
    assert decoded == derived_doc


def test_derived_document_is_real_msgspec_struct() -> None:
    # The authored class itself is a usable msgspec.Struct (Pyright-typed fields).
    instance = SourceTrustQualityDocument(
        status=ProvenanceStatus.STATED, b=1.0, d=0.0, u=0.0, a=0.5
    )
    assert instance.status is ProvenanceStatus.STATED
    assert isinstance(instance, msgspec.Struct)

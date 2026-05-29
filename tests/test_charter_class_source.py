"""STEP 1: comprehensive equivalence for the ``source`` family.

``SOURCE_CHARTER`` (propstore sources/declaration.py:305-349) exercises the
load-bearing features: column rename (``source_id`` -> doc ``id``), a
``document=False`` primary-key column (``slug``), JSON-blob nested fields
(``origin``/``trust``/``metadata``/``quality``), optional JSON fields, more
``document=False`` JSON columns (``quality``/``derived_from``), an ``artifact``
field, and a secondary index. The declarative class must derive a charter whose
generated document, schema object, and codec bytes match the hand-written one.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, get_args, get_origin

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, CharterIndex, FamilyCharter, FamilyModel
from quire.charter_class import CharterDoc, charter, charter_field, column
from quire.families import FamilyDefinition
from quire.sql_types import SqlTypeSpec
from quire.versions import VersionId


def _strip_annotated(annotation: object) -> object:
    """Return the inner type of an ``Annotated[...]`` annotation, else the type.

    Since Fix 1 makes ``generated_document()`` return the authored class itself,
    its msgspec field types carry the ``Annotated[T, CharterFieldSpec(...)]``
    metadata. msgspec ignores that metadata for encode/decode, so the effective
    (codec-relevant) type is the inner ``T``. Comparisons against a hand-written
    charter's stripped types strip the wrapper first.
    """

    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


_VERSION = VersionId("2026.05.25", allow_placeholder=False)


class SourceKind(str, Enum):
    PAPER = "paper"
    DATASET = "dataset"


class SourceOriginDocument(CharterDoc):
    type: str
    value: str


class SourceTrustDocument(CharterDoc):
    status: str
    score: float


class SourceMetadataDocument(CharterDoc):
    title: str | None = None
    note: str | None = None


class SourceTrustQualityDocument(CharterDoc):
    status: str
    b: float | int
    d: float | int
    u: float | int
    a: float | int


# --- declarative shape -----------------------------------------------------


@charter(
    key="source",
    name="source",
    contract_version="2026.05.25",
    placement=".derived/source",
    identity_field="slug",
    semantic="propstore.world",
    artifact_family_name="propstore-world-source",
    extra_columns=(
        column("slug", str, primary_key=True, nullable=False),
        column("quality", SourceTrustQualityDocument, json=True, nullable=True),
        column("derived_from", list, json=True, nullable=True),
    ),
    indexes=(CharterIndex("idx_source_source_id", ("source_id",)),),
)
class SourceDocument(CharterDoc):
    id: Annotated[str, charter_field(column_name="source_id")]
    kind: SourceKind
    origin: Annotated[SourceOriginDocument, charter_field(json=True)]
    trust: Annotated[SourceTrustDocument, charter_field(json=True)]
    metadata: Annotated[SourceMetadataDocument | None, charter_field(json=True)] = None
    artifact_code: Annotated[str | None, charter_field(artifact=True)] = None


# --- hand-written shape (mirrors propstore sources/declaration.py) ----------


def _hand_written_charter() -> FamilyCharter:
    model = type("SourceDocumentModel", (FamilyModel,), {})
    model.__module__ = SourceDocument.__module__
    model.__qualname__ = "SourceDocumentModel"
    return FamilyCharter(
        family=FamilyDefinition(
            key="source",
            name="source",
            contract_version=_VERSION,
            artifact_family=ArtifactFamily(
                name="propstore-world-source",
                contract_version=_VERSION,
                doc_type=model,
                placement=FlatYamlPlacement(".derived/source", str),
            ),
            identity_field="slug",
        ),
        model=model,
        fields=(
            CharterField("source_id", str, nullable=False, document_name="id"),
            CharterField("kind", SourceKind, nullable=False),
            CharterField("origin", SourceOriginDocument, parse_boundary="json", nullable=False),
            CharterField("trust", SourceTrustDocument, parse_boundary="json", nullable=False),
            CharterField(
                "metadata", SourceMetadataDocument, parse_boundary="json", nullable=True
            ),
            CharterField("artifact_code", str, artifact=True, nullable=True),
            CharterField("slug", str, primary_key=True, nullable=False, document=False),
            CharterField(
                "quality",
                SourceTrustQualityDocument,
                parse_boundary="json",
                nullable=True,
                document=False,
            ),
            CharterField(
                "derived_from",
                list,
                parse_boundary="json",
                nullable=True,
                document=False,
            ),
        ),
        indexes=(CharterIndex("idx_source_source_id", ("source_id",)),),
        semantic_metadata={"semantic": "propstore.world"},
    )


def _derived_charter() -> FamilyCharter:
    return SourceDocument.__charter__  # type: ignore[attr-defined]


def test_generated_document_matches() -> None:
    hand = _hand_written_charter().generated_document()
    derived = _derived_charter().generated_document()
    assert hand.__struct_fields__ == derived.__struct_fields__ == (
        "id",
        "kind",
        "origin",
        "trust",
        "metadata",
        "artifact_code",
    )
    hand_types = {f.name: _strip_annotated(f.type) for f in msgspec.structs.fields(hand)}
    derived_types = {f.name: _strip_annotated(f.type) for f in msgspec.structs.fields(derived)}
    assert hand_types == derived_types
    hand_defaults = {f.name: f.default for f in msgspec.structs.fields(hand)}
    derived_defaults = {f.name: f.default for f in msgspec.structs.fields(derived)}
    assert hand_defaults == derived_defaults


def test_schema_object_matches() -> None:
    assert _hand_written_charter().to_schema_object() == _derived_charter().to_schema_object()


def test_document_codec_bytes_match() -> None:
    hand_charter = _hand_written_charter()
    derived_charter = _derived_charter()
    hand_type = hand_charter.generated_document()
    derived_type = derived_charter.generated_document()

    origin = SourceOriginDocument(type="url", value="https://example.org")
    trust = SourceTrustDocument(status="measured", score=0.7)
    metadata = SourceMetadataDocument(title="A paper", note=None)

    hand_doc = hand_type(
        id="src-1", kind=SourceKind.PAPER, origin=origin, trust=trust, metadata=metadata
    )
    derived_doc = derived_type(
        id="src-1", kind=SourceKind.PAPER, origin=origin, trust=trust, metadata=metadata
    )

    hand_codec = hand_charter.document_codec()
    derived_codec = derived_charter.document_codec()
    assert hand_codec.encode(hand_doc) == derived_codec.encode(derived_doc)

    encoded = derived_codec.encode(derived_doc)
    decoded = derived_codec.decode(encoded, derived_type, source="src.yaml")
    assert decoded == derived_doc


def test_json_blob_fields_are_str_columns() -> None:
    # origin/trust/metadata/quality are JSON-string columns in the schema while
    # the document type stays the nested struct.
    schema = _derived_charter().to_schema_object()
    by_name = {f.name: f for f in schema.fields}
    origin_sql_type = by_name["origin"].sql_type
    assert isinstance(origin_sql_type, SqlTypeSpec)
    assert origin_sql_type.storage_kind == "text"
    assert by_name["origin"].parse_boundary == "json"
    assert by_name["origin"].document is True
    assert by_name["quality"].document is False
    assert by_name["slug"].primary_key is True
    assert by_name["slug"].document is False

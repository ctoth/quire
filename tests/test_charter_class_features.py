"""STEP 1: remaining load-bearing features — validators, batch over the family's
own generated document, default/default_sql columns, and the ``merge`` one-field
custom-document case (document-level equivalence)."""

from __future__ import annotations

from typing import Annotated, get_args, get_origin

import msgspec
import pytest

from quire.charters import FamilyCharter
from quire.charter_class import CharterDoc, charter, charter_field, column
from quire.documents.batch import DocumentBatchSpec
from quire.contracts import contract_version


def _strip_annotated(annotation: object) -> object:
    """Inner type of an ``Annotated[...]``; the type otherwise.

    Fix 1 makes ``generated_document()`` return the authored class, whose field
    types carry ``Annotated[T, CharterFieldSpec(...)]`` metadata that msgspec
    ignores for encode/decode. Compare the codec-effective inner type.
    """

    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


_VERSION = contract_version("2026.05.25")


# --- validators ------------------------------------------------------------


def _validate_positive(document: msgspec.Struct) -> None:
    if getattr(document, "value") < 0:
        raise ValueError("value must be non-negative")


@charter(
    key="measurement",
    name="measurement",
    contract_version="2026.05.25",
    placement=".derived/measurement",
    identity_field="id",
    semantic="propstore.world",
    validators=(_validate_positive,),
)
class MeasurementDocument(CharterDoc):
    id: str
    value: int


def test_validator_runs_on_decode() -> None:
    charter_obj: FamilyCharter = MeasurementDocument.__charter__  # type: ignore[attr-defined]
    doc_type = charter_obj.generated_document()
    codec = charter_obj.document_codec()
    good = doc_type(id="m1", value=3)
    encoded = codec.encode(good)
    assert codec.decode(encoded, doc_type, source="m.yaml") == good

    bad = msgspec.yaml.encode({"id": "m2", "value": -1})
    with pytest.raises(ValueError, match="non-negative"):
        codec.decode(bad, doc_type, source="m.yaml")


# --- batch over the family's own generated document ------------------------


@charter(
    key="claim",
    name="claim",
    contract_version="2026.05.25",
    placement=".derived/claim",
    identity_field="id",
    semantic="propstore.world",
    batch=lambda doc: DocumentBatchSpec(
        batch_name="claims",
        item_type=doc,
        items_field="claims",
        inherited_item_fields=("source",),
    ),
)
class ClaimDocument(CharterDoc):
    id: str
    text: str
    source: str | None = None


def test_batch_spec_references_own_generated_document() -> None:
    charter_obj: FamilyCharter = ClaimDocument.__charter__  # type: ignore[attr-defined]
    assert len(charter_obj.batch_specs) == 1
    spec = charter_obj.batch_specs[0]
    assert spec.batch_name == "claims"
    assert spec.items_field == "claims"
    assert spec.inherited_item_fields == ("source",)
    # item_type IS the family's own generated document (no object.__setattr__ hack
    # leaking into authoring; the factory received the generated doc).
    assert spec.item_type is charter_obj.generated_document()


# --- default / default_sql -------------------------------------------------


@charter(
    key="payload",
    name="payload",
    contract_version="2026.05.25",
    placement=".derived/payload",
    identity_field="id",
    semantic="propstore.world",
)
class PayloadDocument(CharterDoc):
    id: str
    tags: Annotated[tuple[str, ...], charter_field(default_sql="'[]'")] = ()


def test_default_and_default_sql() -> None:
    charter_obj: FamilyCharter = PayloadDocument.__charter__  # type: ignore[attr-defined]
    doc_type = charter_obj.generated_document()
    fields = {f.name: f for f in msgspec.structs.fields(doc_type)}
    assert fields["tags"].default == ()
    schema = charter_obj.to_schema_object()
    by_name = {f.name: f for f in schema.fields}
    assert by_name["tags"].default_sql == "'[]'"
    assert by_name["tags"].default == ()


# --- merge: one-field custom document (document-level equivalence) ---------


class MergeManifestPayloadDocument(CharterDoc):
    branch_a: str
    branch_b: str
    arguments: tuple[str, ...] = ()


@charter(
    key="merge_manifest",
    name="merge_manifest",
    contract_version="2026.05.25",
    placement=".derived/merge_manifest",
    identity_field="id",
    semantic="propstore.world",
    extra_columns=(
        column("id", str, primary_key=True, nullable=False, default="merge_manifest",
               default_sql="'merge_manifest'"),
        column("branch_a", str, nullable=False),
        column("branch_b", str, nullable=False),
        column("arguments", tuple[str, ...], json=True, nullable=False, default=(),
               default_sql="'[]'"),
    ),
)
class MergeManifestDocument(CharterDoc):
    merge: Annotated[MergeManifestPayloadDocument, charter_field(json=True)]


def _hand_written_merge_document_type() -> type[msgspec.Struct]:
    # The hand-written merge family overrides generated_document to a single
    # {"merge": MergeManifestPayloadDocument} struct.
    return msgspec.defstruct(
        "MergeManifestDocument",
        [("merge", MergeManifestPayloadDocument)],
        module=__name__,
        forbid_unknown_fields=True,
    )


def test_merge_document_is_single_merge_field() -> None:
    charter_obj: FamilyCharter = MergeManifestDocument.__charter__  # type: ignore[attr-defined]
    derived = charter_obj.generated_document()
    hand = _hand_written_merge_document_type()
    assert derived.__struct_fields__ == hand.__struct_fields__ == ("merge",)
    derived_types = {f.name: _strip_annotated(f.type) for f in msgspec.structs.fields(derived)}
    hand_types = {f.name: _strip_annotated(f.type) for f in msgspec.structs.fields(hand)}
    assert derived_types == hand_types

    payload = MergeManifestPayloadDocument(branch_a="a", branch_b="b", arguments=("x",))
    derived_doc = derived(merge=payload)
    # Document round-trips through the codec.
    codec = charter_obj.document_codec()
    encoded = codec.encode(derived_doc)
    assert codec.decode(encoded, derived, source="m.yaml") == derived_doc


# --- document_only: document field with NO backing storage column ----------


@charter(
    key="merge_only",
    name="merge_only",
    contract_version="2026.05.25",
    placement=".derived/merge_only",
    identity_field="id",
    semantic="propstore.world",
    extra_columns=(
        column("id", str, primary_key=True, nullable=False, default="merge_only",
               default_sql="'merge_only'"),
        column("branch_a", str, nullable=False),
        column("branch_b", str, nullable=False),
        column("arguments", tuple[str, ...], json=True, nullable=False, default=(),
               default_sql="'[]'"),
    ),
)
class MergeOnlyDocument(CharterDoc):
    # `merge` is part of the typed document + contract manifest, but the real
    # data lives in branch_a / branch_b / arguments columns. document_only=True
    # keeps it in the document projection while emitting NO storage column.
    merge: Annotated[
        MergeManifestPayloadDocument, charter_field(json=True, document_only=True)
    ]


def test_document_only_field_in_document_not_in_schema() -> None:
    charter_obj: FamilyCharter = MergeOnlyDocument.__charter__  # type: ignore[attr-defined]

    # (a) the field IS in the generated document / __struct_fields__.
    derived = charter_obj.generated_document()
    assert derived.__struct_fields__ == ("merge",)
    assert "merge" in MergeOnlyDocument.__struct_fields__

    # (b) the SQLAlchemy schema builder emits NO column for it. The `merge`
    # field is still carried in the IR (storage=False) so the manifest records
    # its existence, but the built table's columns are exactly the storage ones.
    schema = charter_obj.to_schema_object()
    merge_schema_field = schema.field("merge")
    assert merge_schema_field.document is True
    assert merge_schema_field.storage is False

    from quire.charters import charter_catalog
    from quire.sqlalchemy_schema import build_sqlalchemy_schema

    sql_schema = build_sqlalchemy_schema(charter_catalog(charter_obj))
    table = sql_schema.table("merge_only")
    assert "merge" not in table.c
    assert set(table.c.keys()) == {"id", "branch_a", "branch_b", "arguments"}

    # (c) the document still round-trips through the codec.
    payload = MergeManifestPayloadDocument(branch_a="a", branch_b="b", arguments=("x",))
    derived_doc = derived(merge=payload)
    codec = charter_obj.document_codec()
    encoded = codec.encode(derived_doc)
    assert codec.decode(encoded, derived, source="m.yaml") == derived_doc


def test_document_only_field_marked_in_manifest_payload() -> None:
    # The field appears in the contract manifest flagged document=True /
    # storage=False so the schema builder knows to skip its column.
    charter_obj: FamilyCharter = MergeOnlyDocument.__charter__  # type: ignore[attr-defined]
    schema = charter_obj.to_schema_object()
    payload = schema.payload()
    field_payloads = {f["name"]: f for f in payload["fields"]}  # type: ignore[index]
    assert field_payloads["merge"]["document"] is True
    assert field_payloads["merge"]["storage"] is False
    # The charter field carries the same flags.
    merge_field = next(f for f in charter_obj.fields if f.name == "merge")
    assert merge_field.document is True
    assert merge_field.storage is False

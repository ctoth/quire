"""Regression tests for self-referential / recursive @charter field types.

`@charter` resolves field type hints at DECORATION time via
``get_type_hints(cls, include_extras=True)``. A self-referential annotation
(the class naming itself, or a tagged-union naming itself plus a sibling) used
to raise ``NameError`` because the class name is not bound in its module until
the decorator returns. The fix binds the decorated class into its own module
namespace under ``cls.__name__`` before hint resolution.

These tests cover both shapes propstore needs before migrating claims/worldlines:
- a tagged-union self+sibling pair (atomic / ist-of-ist-of-atomic);
- a direct self-recursive dict field.
"""

from __future__ import annotations

from typing import Annotated

import msgspec

from quire.charter_class import CharterDoc, charter, charter_field
from quire.charters import FamilyCharter


# --- tagged-union self + sibling reference (the repro) ----------------------


@charter(
    key="atomic",
    name="atomic",
    contract_version="2026.05.25",
    placement=".derived/atomic",
    identity_field="predicate",
    semantic="propstore.world",
    artifact_family_name="probe-atomic",
    model_name="ProbeAtomic",
)
class AtomicPropositionDocument(CharterDoc, tag="atomic"):
    predicate: str


@charter(
    key="ist",
    name="ist",
    contract_version="2026.05.25",
    placement=".derived/ist",
    identity_field="context_id",
    semantic="propstore.world",
    artifact_family_name="probe-ist",
    model_name="ProbeIst",
)
class IstPropositionDocument(CharterDoc, tag="ist"):
    context_id: str
    proposition: Annotated[
        "AtomicPropositionDocument | IstPropositionDocument",
        charter_field(json=True),
    ]


def test_self_and_sibling_charters_build_without_nameerror() -> None:
    # Reaching this point means decoration did not raise NameError.
    assert isinstance(AtomicPropositionDocument.__charter__, FamilyCharter)
    assert isinstance(IstPropositionDocument.__charter__, FamilyCharter)


def test_get_type_hints_resolves_self_and_sibling() -> None:
    from typing import get_type_hints

    hints = get_type_hints(IstPropositionDocument, include_extras=True)
    # The forward ref resolved (no string left, no NameError).
    assert "proposition" in hints


def test_generated_document_is_the_decorated_class() -> None:
    assert IstPropositionDocument.__charter__.generated_document() is IstPropositionDocument
    assert AtomicPropositionDocument.__charter__.generated_document() is AtomicPropositionDocument


def test_tagged_union_deeply_nested_roundtrip() -> None:
    # ist of ist of atomic — the tagged union must discriminate correctly.
    inner_atomic = AtomicPropositionDocument(predicate="P")
    inner_ist = IstPropositionDocument(context_id="c1", proposition=inner_atomic)
    outer = IstPropositionDocument(context_id="c0", proposition=inner_ist)

    encoded = msgspec.json.encode(outer)
    decoded = msgspec.json.decode(encoded, type=IstPropositionDocument)
    assert decoded == outer
    # Discrimination by tag survived the round-trip.
    assert isinstance(decoded.proposition, IstPropositionDocument)
    assert isinstance(decoded.proposition.proposition, AtomicPropositionDocument)
    assert decoded.proposition.proposition.predicate == "P"


def test_tagged_union_codec_roundtrip_through_charter() -> None:
    # The same nesting through the charter's document codec (json-blob path).
    charter_obj: FamilyCharter = IstPropositionDocument.__charter__
    codec = charter_obj.document_codec()
    doc = IstPropositionDocument(
        context_id="c0",
        proposition=IstPropositionDocument(
            context_id="c1", proposition=AtomicPropositionDocument(predicate="P")
        ),
    )
    encoded = codec.encode(doc)
    decoded = codec.decode(encoded, IstPropositionDocument, source="x.yaml")
    assert decoded == doc


# --- direct self-recursion (worldline-input shape) --------------------------


@charter(
    key="worldline_input_source",
    name="worldline_input_source",
    contract_version="2026.05.25",
    placement=".derived/worldline_input_source",
    identity_field="source_id",
    semantic="propstore.world",
    artifact_family_name="probe-worldline-input",
    model_name="ProbeWorldlineInput",
)
class WorldlineInputSourceDocument(CharterDoc):
    source_id: str
    inputs_used: Annotated[
        "dict[str, WorldlineInputSourceDocument]",
        charter_field(json=True),
    ] = {}


def test_direct_self_recursion_builds_and_roundtrips() -> None:
    assert isinstance(WorldlineInputSourceDocument.__charter__, FamilyCharter)

    leaf = WorldlineInputSourceDocument(source_id="leaf")
    mid = WorldlineInputSourceDocument(source_id="mid", inputs_used={"leaf": leaf})
    root = WorldlineInputSourceDocument(source_id="root", inputs_used={"mid": mid})

    encoded = msgspec.json.encode(root)
    decoded = msgspec.json.decode(encoded, type=WorldlineInputSourceDocument)
    assert decoded == root
    assert decoded.inputs_used["mid"].inputs_used["leaf"].source_id == "leaf"


def test_self_recursion_default_is_empty_dict() -> None:
    doc = WorldlineInputSourceDocument(source_id="x")
    assert doc.inputs_used == {}

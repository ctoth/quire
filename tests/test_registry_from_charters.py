"""``registry_from_charters`` derives a family registry's foreign-key graph from
field-level charter annotations rather than a hand-authored literal table.

These tests pin the contract propstore's charter-derived family registry relies
on: a foreign key declared with ``charter_field(foreign_key=...)`` (or
``foreign_keys=(...)``) on a document field is lifted onto the owning
``FamilyDefinition.foreign_keys`` and validated by ``FamilyRegistry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

import pytest

from quire.charter_class import CharterDoc, charter, charter_field
from quire.charters import (
    FamilyCharter,
    charter_field_foreign_keys,
    registry_from_charters,
)
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.references import ForeignKeySpec
from quire.versions import VersionId


_VERSION = VersionId("2026.06.29", allow_placeholder=False)
_FK_VERSION = VersionId("2026.06.29", allow_placeholder=False)


@charter(
    key="widget",
    name="widget",
    contract_version="2026.06.29",
    placement="widgets",
    identity_field="widget_id",
)
class Widget(CharterDoc):
    widget_id: str
    label: str


@charter(
    key="gadget",
    name="gadget",
    contract_version="2026.06.29",
    placement="gadgets",
    identity_field="gadget_id",
)
class Gadget(CharterDoc):
    gadget_id: str
    primary_widget: Annotated[
        str,
        charter_field(
            foreign_key=ForeignKeySpec(
                name="gadget_primary_widget",
                contract_version=_FK_VERSION,
                source_family="gadget",
                source_field="primary_widget",
                target_family="widget",
            )
        ),
    ]
    related_widgets: Annotated[
        tuple[str, ...],
        charter_field(
            json=True,
            foreign_keys=(
                ForeignKeySpec(
                    name="gadget_related_widgets",
                    contract_version=_FK_VERSION,
                    source_family="gadget",
                    source_field="related_widgets[]",
                    target_family="widget",
                    many=True,
                    required=False,
                ),
            ),
        ),
    ] = ()


@charter(
    key="blob",
    name="blob",
    contract_version="2026.06.29",
    placement="blobs",
    identity_field="blob_id",
)
class Blob(CharterDoc):
    blob_id: str
    payload: Annotated[tuple[str, ...], charter_field(json=True)] = ()


@dataclass(frozen=True)
class _Owner:
    branch: str = "master"


def _charter(model: type[CharterDoc]) -> FamilyCharter:
    return cast("FamilyCharter", model.__charter__)


def test_charter_field_foreign_keys_collapses_both_spellings() -> None:
    gadget = _charter(Gadget)
    by_name = {field.name: field for field in gadget.fields}

    single = charter_field_foreign_keys(by_name["primary_widget"])
    assert tuple(spec.name for spec in single) == ("gadget_primary_widget",)

    many = charter_field_foreign_keys(by_name["related_widgets"])
    assert tuple(spec.name for spec in many) == ("gadget_related_widgets",)

    # A field with no FK annotation collapses to the empty tuple.
    assert charter_field_foreign_keys(by_name["gadget_id"]) == ()


def test_registry_lifts_field_foreign_keys_onto_definition() -> None:
    registry = registry_from_charters(
        _charter(Widget),
        _charter(Gadget),
        name="derivation",
        contract_version=_VERSION,
    )

    # The widget family declares no field FKs; gadget's two field FKs are lifted.
    assert registry.by_name("widget").foreign_keys == ()
    gadget_fks = registry.by_name("gadget").foreign_keys
    assert {spec.name for spec in gadget_fks} == {
        "gadget_primary_widget",
        "gadget_related_widgets",
    }
    # The lifted spec carries the exact field path, so the graph is derived from
    # the annotation, not a separately authored literal.
    related = next(s for s in gadget_fks if s.name == "gadget_related_widgets")
    assert related.source_field == "related_widgets[]"
    assert related.target_family == "widget"
    assert related.many is True


def test_registry_families_round_trip_through_a_bound_store() -> None:
    # A bound multi-family DocumentFamilyStore carries one default codec; the
    # registry must make each family storage-complete (its charter document codec
    # wired onto the artifact family) so an authored document with a json-blob
    # field round-trips through save/load.
    registry = registry_from_charters(
        _charter(Blob),
        name="derivation",
        contract_version=_VERSION,
    )
    store: DocumentFamilyStore[object] = DocumentFamilyStore[object](
        owner=_Owner(), backend=GitStore.init_memory()
    )
    bound = registry.bind(_Owner(), store)
    blob = Blob(blob_id="b1", payload=("a", "b"))
    bound.blob.save("b1", blob, message="author blob")
    loaded = bound.blob.load("b1")
    assert loaded == blob


def test_registry_rejects_foreign_key_to_unregistered_family() -> None:
    # Building a registry that omits the FK target family must fail at
    # construction (the graph is validated, not silently accepted).
    with pytest.raises(ValueError, match="target family is unknown"):
        registry_from_charters(
            _charter(Gadget),
            name="derivation",
            contract_version=_VERSION,
        )

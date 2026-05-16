from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pytest

from quire.projection_mapping import (
    EnumPath,
    JsonPath,
    ProjectionModel,
    ReferencePath,
    RepeatedPath,
    ScalarPath,
)
from quire.projections import ProjectionRow


@dataclass(frozen=True)
class FlatRecord:
    id: str
    title: str | None = None


@dataclass(frozen=True)
class Origin:
    type: str | None = None


@dataclass(frozen=True)
class Source:
    origin: Origin | None = None


@dataclass(frozen=True)
class NestedRecord:
    id: str
    source: Source | None = None


class Status(Enum):
    DRAFT = "draft"
    READY = "ready"


@dataclass(frozen=True)
class StatusRecord:
    id: str
    status: Status | None = None


@dataclass(frozen=True)
class JsonRecord:
    id: str
    payload: object = None


@dataclass(frozen=True)
class OptionalRecord:
    id: str
    note: str | None = None


@dataclass(frozen=True)
class Link:
    concept_id: str
    role: str


@dataclass(frozen=True)
class Parent:
    id: str
    links: tuple[Link, ...] = ()


@dataclass(frozen=True)
class ReferenceRecord:
    id: str
    context_id: str | None = None


@dataclass(frozen=True)
class AttributeRecord:
    id: str
    attributes: dict[str, object] | None = None


@dataclass(frozen=True)
class RelationshipFixture:
    source_id: str
    target_id: str
    relation_type: Status
    note: str | None = None


def test_scalar_path_round_trip_flat_dataclass():
    model = ProjectionModel(
        name="flat",
        table="flat",
        result_type=FlatRecord,
        fields=(ScalarPath(("id",), "id"), ScalarPath(("title",), "title"),),
    )

    row = model.to_row(FlatRecord("r1", "Intro"))

    assert row == {"id": "r1", "title": "Intro"}
    assert model.from_row(row) == FlatRecord("r1", "Intro")


def test_scalar_path_round_trip_nested_dataclass():
    model = ProjectionModel(
        name="nested",
        table="nested",
        result_type=NestedRecord,
        fields=(
            ScalarPath(("id",), "id"),
            ScalarPath(("source", "origin", "type"), "source_origin_type"),
        ),
    )

    row = model.to_row(NestedRecord("r1", Source(Origin("paper"))))

    assert row["source_origin_type"] == "paper"
    assert model.from_row(row) == NestedRecord("r1", Source(Origin("paper")))


def test_enum_path_coerces_in_both_directions():
    model = ProjectionModel(
        name="status",
        table="status",
        result_type=StatusRecord,
        fields=(ScalarPath(("id",), "id"), EnumPath(("status",), "status", enum=Status),),
    )

    assert model.to_row(StatusRecord("r1", Status.READY))["status"] == "ready"
    assert model.from_row({"id": "r1", "status": "draft"}) == StatusRecord("r1", Status.DRAFT)


def test_json_path_round_trips_lists_and_dicts():
    payload = {"tags": ["a", "b"], "rank": 2}
    model = ProjectionModel(
        name="json",
        table="json",
        result_type=JsonRecord,
        fields=(ScalarPath(("id",), "id"), JsonPath(("payload",), "payload_json"),),
    )

    row = model.to_row(JsonRecord("r1", payload))

    assert row["payload_json"] == '{"rank":2,"tags":["a","b"]}'
    assert model.from_row(row) == JsonRecord("r1", payload)


def test_optional_missing_path_returns_none_by_default():
    model = ProjectionModel(
        name="optional",
        table="optional",
        result_type=OptionalRecord,
        fields=(ScalarPath(("id",), "id"), ScalarPath(("note",), "note"),),
    )

    assert model.from_row({"id": "r1"}) == OptionalRecord("r1", None)


def test_repeated_path_expands_into_child_rows_with_parent_keys():
    model = _parent_model()

    rows = model.child_rows(Parent("p1", (Link("c1", "target"), Link("c2", "support"))))

    assert rows == (
        ProjectionRow("parent_link", {"parent_id": "p1", "concept_id": "c1", "role": "target"}),
        ProjectionRow("parent_link", {"parent_id": "p1", "concept_id": "c2", "role": "support"}),
    )


def test_repeated_path_decodes_children_into_typed_tuple():
    model = _parent_model()

    result = model.from_row(
        {
            "id": "p1",
            "parent_link": (
                {"parent_id": "p1", "concept_id": "c1", "role": "target"},
                {"parent_id": "p1", "concept_id": "c2", "role": "support"},
            ),
        }
    )

    assert result == Parent("p1", (Link("c1", "target"), Link("c2", "support")))


def test_reference_path_emits_column_and_foreign_key():
    model = ProjectionModel(
        name="reference",
        table="reference",
        result_type=ReferenceRecord,
        fields=(
            ScalarPath(("id",), "id"),
            ReferencePath(("context_id",), "context_id", family="context"),
        ),
    )

    table = model.projection_tables()[0]

    assert "context_id" in table.column_names
    assert table.foreign_keys[0].schema_hash_material() == {
        "columns": ("context_id",),
        "ref_table": "context",
        "ref_columns": ("id",),
    }


def test_unknown_keys_raise_when_no_attribute_bucket_declared():
    model = ProjectionModel(
        name="strict",
        table="strict",
        result_type=FlatRecord,
        fields=(ScalarPath(("id",), "id"),),
    )

    with pytest.raises(KeyError, match="extra"):
        model.from_row({"id": "r1", "extra": "nope"})


def test_attribute_bucket_only_when_declared():
    model = ProjectionModel(
        name="bucket",
        table="bucket",
        result_type=AttributeRecord,
        fields=(ScalarPath(("id",), "id"),),
        attribute_bucket=("attributes",),
    )

    assert model.from_row({"id": "r1", "extra": 3}) == AttributeRecord("r1", {"extra": 3})


def test_coerce_accepts_result_type_or_mapping():
    model = ProjectionModel(
        name="flat",
        table="flat",
        result_type=FlatRecord,
        fields=(ScalarPath(("id",), "id"), ScalarPath(("title",), "title"),),
    )
    record = FlatRecord("r1", "Intro")

    assert model.coerce(record) is record
    assert model.coerce({"id": "r2", "title": "Next"}) == FlatRecord("r2", "Next")
    with pytest.raises(TypeError, match="FlatRecord or mapping"):
        model.coerce(["r3"])


def test_schema_hash_changes_when_path_changes_but_column_does_not():
    first = ProjectionModel(
        name="schema",
        table="schema",
        result_type=FlatRecord,
        fields=(ScalarPath(("id",), "id"),),
    )
    second = ProjectionModel(
        name="schema",
        table="schema",
        result_type=FlatRecord,
        fields=(ScalarPath(("identifier",), "id"),),
    )

    assert first.schema_hash_material() != second.schema_hash_material()


def test_schema_hash_unchanged_when_only_field_order_differs():
    first = ProjectionModel(
        name="schema",
        table="schema",
        result_type=FlatRecord,
        fields=(ScalarPath(("id",), "id"), ScalarPath(("title",), "title"),),
    )
    second = ProjectionModel(
        name="schema",
        table="schema",
        result_type=FlatRecord,
        fields=(ScalarPath(("title",), "title"), ScalarPath(("id",), "id"),),
    )

    assert first.schema_hash_material() == second.schema_hash_material()


def test_two_unrelated_models_share_zero_model_specific_code():
    flat = ProjectionModel(
        name="flat",
        table="flat",
        result_type=FlatRecord,
        fields=(ScalarPath(("id",), "id"),),
    )
    reference = ProjectionModel(
        name="reference",
        table="reference",
        result_type=ReferenceRecord,
        fields=(ScalarPath(("id",), "id"), ReferencePath(("context_id",), "context_id", family="context")),
    )

    assert flat.from_row({"id": "a"}) == FlatRecord("a")
    assert reference.from_row({"id": "b", "context_id": "ctx"}) == ReferenceRecord("b", "ctx")


def test_relationship_shaped_fixture_uses_generic_projection_model():
    model = ProjectionModel(
        name="relationship_fixture",
        table="relation_edge",
        result_type=RelationshipFixture,
        fields=(
            ScalarPath(("source_id",), "source_id"),
            ScalarPath(("target_id",), "target_id"),
            EnumPath(("relation_type",), "relation_type", enum=Status),
            ScalarPath(("note",), "note"),
        ),
    )

    row = {
        "source_id": "c1",
        "target_id": "c2",
        "relation_type": "ready",
        "note": None,
    }

    assert model.from_row(row) == RelationshipFixture("c1", "c2", Status.READY, None)
    assert model.to_row(RelationshipFixture("c1", "c2", Status.DRAFT, "edge")) == {
        "source_id": "c1",
        "target_id": "c2",
        "relation_type": "draft",
        "note": "edge",
    }


def _parent_model() -> ProjectionModel:
    return ProjectionModel(
        name="parent",
        table="parent",
        result_type=Parent,
        fields=(
            ScalarPath(("id",), "id"),
            RepeatedPath(
                path=("links",),
                table="parent_link",
                parent_fk="parent_id",
                item_type=Link,
                fields=(ScalarPath(("concept_id",), "concept_id"), ScalarPath(("role",), "role")),
            ),
        ),
    )

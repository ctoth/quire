from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pytest

from quire.projection_mapping import (
    EnumPath,
    JsonPath,
    ProjectionAttachedRows,
    ProjectionBinding,
    ProjectionCodec,
    ProjectionComponent,
    ProjectionDiscriminator,
    ProjectionInputKey,
    ProjectionJoin,
    ProjectionMetadata,
    ProjectionModel,
    ProjectionQueryPlan,
    ProjectionRenderView,
    ProjectionSelectedColumn,
    ReferencePath,
    ScalarPath,
)
from quire.projections import ProjectionColumn, ProjectionField, ProjectionIndex, ProjectionRow, ProjectionTable, json_decoder, json_encoder


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
    parent_id: str | None = None


@dataclass(frozen=True)
class Parent:
    id: str
    links: tuple[Link, ...] = ()


@dataclass(frozen=True)
class ReferenceRecord:
    id: str
    context_id: str | None = None


@dataclass(frozen=True)
class Citation:
    paper: str | None = None
    page: int | None = None


@dataclass(frozen=True)
class CitedRecord:
    id: str
    citation: Citation | None = None


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

    rows = model.child_rows(Parent("p1", (Link("c1", "target", "p1"), Link("c2", "support", "p1"))))

    assert rows == (
        ProjectionRow("parent_link", {"parent_id": "p1", "concept_id": "c1", "role": "target"}),
        ProjectionRow("parent_link", {"parent_id": "p1", "concept_id": "c2", "role": "support"}),
    )


def test_attached_rows_decodes_children_into_typed_tuple():
    model = _parent_model()

    result = model.from_row(
        {
            "id": "p1",
            "links": (
                {"parent_id": "p1", "concept_id": "c1", "role": "target"},
                {"parent_id": "p1", "concept_id": "c2", "role": "support"},
            ),
        }
    )

    assert result == Parent("p1", (Link("c1", "target", "p1"), Link("c2", "support", "p1")))


def test_attached_rows_schema_material_declares_attachment_boundary():
    model = ProjectionModel(
        name="parent",
        table="parent",
        result_type=Parent,
        fields=(
            ScalarPath(("id",), "id"),
            ProjectionAttachedRows(
                path=("links",),
                table="parent_link",
                parent_fk="parent_id",
                item_parent_path=("parent_id",),
                item_type=Link,
                fields=(ScalarPath(("concept_id",), "concept_id"), ScalarPath(("role",), "role")),
            ),
        ),
    )

    result = model.from_row(
        {
            "id": "p1",
            "links": ({"parent_id": "p1", "concept_id": "c1", "role": "target"},),
        }
    )

    assert result == Parent("p1", (Link("c1", "target", "p1"),))
    assert any(
        field.get("kind") == "ProjectionAttachedRows" and field.get("path") == ("links",)
        for field in model.schema_hash_material()["fields"]
    )


def test_projection_model_attaches_child_rows_by_declared_parent_path():
    model = _parent_model()

    rows = model.attach_child_rows(
        ({"id": "p1"}, {"id": "p2"}),
        {
            "parent_link": (
                {"parent_id": "p2", "concept_id": "c2", "role": "support"},
                {"parent_id": "p1", "concept_id": "c3", "role": "support"},
                {"parent_id": "p1", "concept_id": "c1", "role": "target"},
            )
        },
    )

    assert rows == (
        {
            "id": "p1",
            "links": (
                {"parent_id": "p1", "concept_id": "c1", "role": "target"},
                {"parent_id": "p1", "concept_id": "c3", "role": "support"},
            ),
        },
        {"id": "p2", "links": ({"parent_id": "p2", "concept_id": "c2", "role": "support"},)},
    )


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


def test_projection_component_round_trips_multi_column_value():
    model = ProjectionModel(
        name="citation",
        table="citation",
        result_type=CitedRecord,
        fields=(
            ScalarPath(("id",), "id"),
            ProjectionComponent(
                path=("citation",),
                bindings=(
                    ProjectionBinding(
                        ("paper",),
                        projection_column_owner=ProjectionColumn("paper", "TEXT"),
                    ),
                    ProjectionBinding(
                        ("page",),
                        projection_column_owner=ProjectionColumn("page", "INTEGER"),
                    ),
                ),
                encoder=lambda citation: (
                    {}
                    if citation is None
                    else {"paper": citation.paper, "page": citation.page}
                ),
                decoder=lambda row: (
                    None
                    if row.get("paper") is None and row.get("page") is None
                    else Citation(
                        None if row.get("paper") is None else str(row["paper"]),
                        None if row.get("page") is None else int(row["page"]),
                    )
                ),
            ),
        ),
    )

    row = model.to_row(CitedRecord("r1", Citation("paper-a", 7)))

    assert row == {"id": "r1", "paper": "paper-a", "page": 7}
    assert model.from_row(row) == CitedRecord("r1", Citation("paper-a", 7))
    assert model.from_row({"id": "r2"}) == CitedRecord("r2", None)


def test_unknown_keys_raise_without_declared_metadata():
    model = ProjectionModel(
        name="strict",
        table="strict",
        result_type=FlatRecord,
        fields=(ScalarPath(("id",), "id"),),
    )

    with pytest.raises(KeyError, match="extra"):
        model.from_row({"id": "r1", "extra": "nope"})


def test_projection_metadata_declares_allowed_extra_columns():
    model = ProjectionModel(
        name="metadata",
        table="metadata",
        result_type=AttributeRecord,
        fields=(
            ScalarPath(("id",), "id"),
            ProjectionMetadata(
                path=("attributes",),
                fields=(ScalarPath(("confidence",), "confidence"),),
                result_type=dict,
            ),
        ),
    )

    assert model.from_row({"id": "r1", "confidence": 0.75}) == AttributeRecord(
        "r1",
        {"confidence": 0.75},
    )
    assert model.to_row(AttributeRecord("r2", {"confidence": 0.5})) == {
        "id": "r2",
        "confidence": 0.5,
    }
    with pytest.raises(KeyError, match="extra"):
        model.from_row({"id": "r1", "confidence": 0.75, "extra": 3})


def test_projection_input_key_declares_non_materialized_row_input():
    model = ProjectionModel(
        name="input_key",
        table="input_key",
        result_type=FlatRecord,
        fields=(
            ScalarPath(("id",), "id"),
            ProjectionInputKey("display_name"),
        ),
    )

    assert model.from_row({"id": "r1", "display_name": "Rendered Name"}) == FlatRecord("r1")
    assert model.to_row(FlatRecord("r2")) == {"id": "r2"}
    assert model.to_mapping(FlatRecord("r2")) == {"id": "r2"}
    assert model.projection_tables()[0].column_names == ("id",)
    with pytest.raises(KeyError, match="other"):
        model.from_row({"id": "r1", "display_name": "Rendered Name", "other": "nope"})


def test_query_plan_declares_joined_columns_without_ignored_row_keys():
    core = ProjectionTable(
        "core",
        (
            ProjectionColumn("id", "TEXT", nullable=False),
            ProjectionColumn("source_slug", "TEXT"),
        ),
    )
    source = ProjectionTable(
        "source",
        (
            ProjectionColumn("slug", "TEXT", nullable=False),
            ProjectionColumn("source_id", "TEXT", nullable=False),
        ),
    )
    plan = ProjectionQueryPlan(
        name="core_source",
        base_table=core,
        base_alias="core",
        selections=(
            ProjectionSelectedColumn("core", core.column("id")),
            ProjectionSelectedColumn("source", source.column("source_id"), read_name="joined_source_id"),
        ),
        joins=(
            ProjectionJoin(
                table=source,
                alias="source",
                left_alias="core",
                left_column=core.column("source_slug"),
                right_column=source.column("slug"),
            ),
        ),
    )

    assert plan.select_sql() == (
        'SELECT\n'
        '            "core"."id",\n'
        '            "source"."source_id" AS "joined_source_id"\n'
        '        FROM "core" AS "core"\n'
        '        LEFT JOIN "source" AS "source" ON "source"."slug" = "core"."source_slug"'
    )
    assert plan.schema_hash_material()["joins"][0]["left_column"] == "source_slug"


def test_query_plan_declares_discriminator_predicates_and_row_values():
    edge = ProjectionTable(
        "relation_edge",
        (
            ProjectionColumn("source_kind", "TEXT", nullable=False),
            ProjectionColumn("source_id", "TEXT", nullable=False),
            ProjectionColumn("target_kind", "TEXT", nullable=False),
            ProjectionColumn("target_id", "TEXT", nullable=False),
        ),
    )
    source_claim = ProjectionDiscriminator(edge.column("source_kind"), "claim")
    target_claim = ProjectionDiscriminator(edge.column("target_kind"), "claim")
    plan = ProjectionQueryPlan(
        name="claim_stance",
        base_table=edge,
        base_alias="edge",
        selections=(
            ProjectionSelectedColumn("edge", edge.column("source_id"), read_name="claim_id"),
            ProjectionSelectedColumn("edge", edge.column("target_id"), read_name="target_claim_id"),
        ),
        discriminators=(source_claim, target_claim),
    )

    assert plan.select_sql("WHERE edge.source_id = ?") == (
        'SELECT\n'
        '            "edge"."source_id" AS "claim_id",\n'
        '            "edge"."target_id" AS "target_claim_id"\n'
        '        FROM "relation_edge" AS "edge" '
        'WHERE "edge"."source_kind" = \'claim\' AND "edge"."target_kind" = \'claim\' '
        'AND edge.source_id = ?'
    )
    assert source_claim.row_values() == {"source_kind": "claim"}
    assert plan.schema_hash_material()["discriminators"] == (
        {
            "kind": "ProjectionDiscriminator",
            "column": "source_kind",
            "value": "claim",
        },
        {
            "kind": "ProjectionDiscriminator",
            "column": "target_kind",
            "value": "claim",
        },
    )


def test_projection_render_view_renders_non_column_key_without_decoding_it():
    model = ProjectionModel(
        name="derived",
        table="derived",
        result_type=FlatRecord,
        fields=(
            ScalarPath(("id",), "id"),
            ScalarPath(("title",), "title"),
            ProjectionRenderView(source_path=("title",), output_key="label"),
        ),
    )

    assert model.to_row(FlatRecord("r1", "Intro")) == {"id": "r1", "title": "Intro"}
    assert model.to_mapping(FlatRecord("r1", "Intro")) == {
        "id": "r1",
        "title": "Intro",
        "label": "Intro",
    }
    assert model.from_row({"id": "r1", "title": "Intro", "label": "Ignored"}) == FlatRecord(
        "r1",
        "Intro",
    )
    assert "label" not in model.projection_tables()[0].column_names


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


def test_projection_model_emits_declared_table_metadata():
    real_codec = ProjectionCodec("real", "REAL")
    model = ProjectionModel(
        name="metadata",
        table="relation_edge",
        result_type=RelationshipFixture,
        fields=(
            ScalarPath(
                ("source_id",),
                "source_id",
                nullable=False,
                default_sql="''",
            ),
            ScalarPath(("target_id",), "target_id", nullable=False),
            ScalarPath(("relation_type",), "relation_type", nullable=False),
            ScalarPath(
                ("note",),
                "confidence",
                codec=real_codec,
                check_sql="confidence >= 0 AND confidence <= 1",
            ),
        ),
        indexes=(
            ProjectionIndex("idx_relation_edge_source", ("source_id", "target_id")),
        ),
        checks=("confidence IS NULL OR confidence >= 0",),
        if_not_exists=True,
    )

    table = model.projection_tables()[0]

    assert table.if_not_exists is True
    assert table.checks == ("confidence IS NULL OR confidence >= 0",)
    assert table.indexes == (
        ProjectionIndex("idx_relation_edge_source", ("source_id", "target_id")),
    )
    assert table.columns[0].ddl() == "\"source_id\" TEXT NOT NULL DEFAULT ''"
    assert table.columns[3].ddl() == '"confidence" REAL CHECK(confidence >= 0 AND confidence <= 1)'
    assert table.ddl_statements()[1] == (
        'CREATE INDEX IF NOT EXISTS "idx_relation_edge_source" '
        'ON "relation_edge"("source_id", "target_id")'
    )
    assert model.schema_hash_material()["checks"] == ("confidence IS NULL OR confidence >= 0",)


def test_projection_model_omits_noninsertable_columns_from_insert_sql():
    id_codec = ProjectionCodec("auto_id", "INTEGER PRIMARY KEY AUTOINCREMENT")
    model = ProjectionModel(
        name="autoincrement",
        table="autoincrement",
        result_type=FlatRecord,
        fields=(
            ScalarPath(("id",), "id", codec=id_codec, insertable=False),
            ScalarPath(("title",), "title"),
        ),
    )

    table = model.projection_tables()[0]

    assert table.columns[0].ddl() == '"id" INTEGER PRIMARY KEY AUTOINCREMENT'
    assert table.insert_sql() == 'INSERT INTO "autoincrement" ("title") VALUES (:title)'


def test_projection_binding_references_projection_field_owner():
    field = ProjectionField(
        "payload_json",
        "TEXT",
        nullable=False,
        encoder=json_encoder,
        decoder=json_decoder,
    )
    binding = ProjectionBinding(("payload",), field=field, missing="raise")

    assert binding.column_spec() == field.column()
    assert binding.encode_value({"payload": {"b": 2, "a": 1}}) == '{"a":1,"b":2}'
    assert binding.decode_value('{"a":1,"b":2}') == {"a": 1, "b": 2}
    assert binding.schema_hash_material() == {
        "kind": "ProjectionBinding",
        "path": ("payload",),
        "owner": {"kind": "ProjectionField", "name": "payload_json"},
        "read_name": None,
        "missing": "raise",
    }


def test_projection_binding_references_projection_column_owner():
    column = ProjectionColumn("score", "REAL", nullable=False)
    binding = ProjectionBinding(("confidence",), projection_column_owner=column)

    assert binding.column_spec() is column
    assert binding.column_name == "score"
    assert binding.encode_value({"confidence": 0.75}) == 0.75
    assert binding.schema_hash_material()["owner"] == {
        "kind": "ProjectionColumn",
        "name": "score",
    }


def test_projection_binding_rejects_ambiguous_or_missing_owner():
    field = ProjectionField("title", "TEXT")
    column = ProjectionColumn("title", "TEXT")

    with pytest.raises(ValueError, match="exactly one physical owner"):
        ProjectionBinding(("title",))
    with pytest.raises(ValueError, match="exactly one physical owner"):
        ProjectionBinding(("title",), field=field, projection_column_owner=column)


def test_projection_binding_decodes_declared_read_name_without_scalar_aliases():
    title = ProjectionField("title", "TEXT")
    model = ProjectionModel(
        name="alias",
        table="alias",
        result_type=FlatRecord,
        fields=(
            ScalarPath(("id",), "id"),
            ProjectionBinding(("title",), field=title, read_name="label"),
        ),
    )

    assert model.to_row(FlatRecord("r1", "Intro")) == {"id": "r1", "title": "Intro"}
    assert model.from_row({"id": "r1", "label": "Intro"}) == FlatRecord("r1", "Intro")
    binding_material = next(
        field
        for field in model.schema_hash_material()["fields"]
        if field["kind"] == "ProjectionBinding"
    )
    assert binding_material == {
        "kind": "ProjectionBinding",
        "path": ("title",),
        "owner": {"kind": "ProjectionField", "name": "title"},
        "read_name": "label",
        "missing": "none",
    }


def _parent_model() -> ProjectionModel:
    return ProjectionModel(
        name="parent",
        table="parent",
        result_type=Parent,
        fields=(
            ScalarPath(("id",), "id"),
            ProjectionAttachedRows(
                path=("links",),
                table="parent_link",
                parent_fk="parent_id",
                item_parent_path=("parent_id",),
                item_type=Link,
                order_by=("concept_id",),
                fields=(ScalarPath(("concept_id",), "concept_id"), ScalarPath(("role",), "role")),
            ),
        ),
    )

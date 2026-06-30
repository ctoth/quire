from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast, get_type_hints

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter, charter_catalog
from quire.families import FamilyDefinition
from quire.sqlalchemy_schema import build_sqlalchemy_schema
from quire.sqlalchemy_store import create_sqlalchemy_store, readonly_session, writable_session
from quire.versions import VersionId


class DemoFamily(str, Enum):
    DEMOS = "demos"


class DemoDoc(msgspec.Struct):
    id: str


class Inner(msgspec.Struct):
    value: int


@dataclass
class Demo:
    id: str
    items: tuple[Inner, ...] | None = None
    metadata: dict[str, Any] | None = None


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


def _charter(*fields: CharterField) -> FamilyCharter:
    return FamilyCharter(
        family=_minimal_family(),
        model=Demo,
        fields=fields,
    )


def test_nullable_nested_struct_tuple_round_trips_as_json_blob() -> None:
    charter = _charter(
        CharterField("id", str, primary_key=True, nullable=False),
        CharterField(
            "items",
            tuple[Inner, ...],
            parse_boundary="json",
            nullable=True,
        ),
    )
    document_type = charter.generated_document()
    document = document_type(id="demo", items=(Inner(1), Inner(2)))
    codec = charter.document_codec()

    payload = codec.payload(document)
    encoded = codec.encode(document)
    decoded = codec.decode(encoded, document_type, source="demo.yaml")

    assert get_type_hints(document_type)["items"] == tuple[Inner, ...] | None
    assert payload == {"id": "demo", "items": '[{"value":1},{"value":2}]'}
    assert decoded == document

    none_document = document_type(id="demo-none")
    none_payload = codec.payload(none_document)
    none_decoded = codec.decode(
        codec.encode(none_document),
        document_type,
        source="demo-none.yaml",
    )

    assert none_payload == {"id": "demo-none"}
    assert none_decoded == none_document
    assert cast(Any, none_decoded).items is None


def test_required_nested_struct_tuple_default_round_trips_as_json_blob() -> None:
    default_items = (Inner(3),)
    charter = _charter(
        CharterField("id", str, primary_key=True, nullable=False),
        CharterField(
            "items",
            tuple[Inner, ...],
            parse_boundary="json",
            nullable=False,
            default=default_items,
        ),
    )
    document_type = charter.generated_document()
    document = document_type(id="demo")
    codec = charter.document_codec()

    payload = codec.payload(document)
    decoded = codec.decode(codec.encode(document), document_type, source="demo.yaml")

    assert payload == {"id": "demo", "items": '[{"value":3}]'}
    assert decoded == document
    assert cast(Any, decoded).items == default_items


def test_dict_round_trips_as_json_blob() -> None:
    charter = _charter(
        CharterField("id", str, primary_key=True, nullable=False),
        CharterField(
            "metadata",
            dict[str, Any],
            parse_boundary="json",
            nullable=True,
        ),
    )
    document_type = charter.generated_document()
    document = document_type(id="demo", metadata={"score": 3, "tags": ["a", "b"]})
    codec = charter.document_codec()

    payload = codec.payload(document)
    decoded = codec.decode(codec.encode(document), document_type, source="demo.yaml")

    assert payload == {"id": "demo", "metadata": '{"score":3,"tags":["a","b"]}'}
    assert decoded == document


def test_json_blob_schema_projection_uses_str_python_type() -> None:
    field = CharterField(
        "items",
        tuple[Inner, ...],
        parse_boundary="json",
        nullable=True,
    )

    schema_field = field.to_schema_field()

    assert schema_field.python_type == "builtins.str"
    assert cast(Any, schema_field.sql_type).ddl_name == "TEXT"


def test_json_blob_sqlalchemy_round_trips_tuple_value(tmp_path: Path) -> None:
    charter = _charter(
        CharterField("id", str, primary_key=True, nullable=False),
        CharterField(
            "items",
            tuple[Inner, ...],
            parse_boundary="json",
            nullable=True,
        ),
    )
    schema = build_sqlalchemy_schema(charter_catalog(charter))
    store_path = tmp_path / "derived.sqlite"
    create_sqlalchemy_store(store_path, schema)

    with writable_session(store_path, schema) as session:
        session.add(Demo(id="demo", items=(Inner(1), Inner(2))))
        session.commit()

    with readonly_session(store_path, schema) as session:
        record = session.get(Demo, "demo")

    assert record is not None
    assert record.items == (Inner(1), Inner(2))


def test_json_blob_sqlalchemy_round_trips_none_value(tmp_path: Path) -> None:
    charter = _charter(
        CharterField("id", str, primary_key=True, nullable=False),
        CharterField(
            "items",
            tuple[Inner, ...],
            parse_boundary="json",
            nullable=True,
        ),
    )
    schema = build_sqlalchemy_schema(charter_catalog(charter))
    store_path = tmp_path / "derived.sqlite"
    create_sqlalchemy_store(store_path, schema)

    with writable_session(store_path, schema) as session:
        session.add(Demo(id="demo", items=None))
        session.commit()

    with readonly_session(store_path, schema) as session:
        record = session.get(Demo, "demo")

    assert record is not None
    assert record.items is None


def test_json_struct_default_factory_serializes_in_catalog(tmp_path: Path) -> None:
    """A json-boundary field whose default is a struct must not break the
    schema catalog payload (which is canonicalized to JSON for hashing and
    persisted via json.dumps). Regression for charter-derived schemas over
    families that carry a msgspec.Struct default_factory on a json field."""

    schema = build_sqlalchemy_schema(
        charter_catalog(
            _charter(
                CharterField("id", str, primary_key=True, nullable=False),
                CharterField(
                    "decision",
                    Inner,
                    nullable=False,
                    default=Inner(value=0),
                    parse_boundary="json",
                ),
            )
        )
    )
    # Catalog payload must be JSON-serializable (this is what create stores).
    assert isinstance(schema.catalog_hash, str)
    # create_sqlalchemy_store writes the catalog via json.dumps; must not raise.
    create_sqlalchemy_store(tmp_path / "derived.sqlite", schema)

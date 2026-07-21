from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated

import msgspec
import pytest
from sqlalchemy import select

from quire.charter_class import CharterDoc, charter, charter_field
from quire.charters import charter_catalog
from quire.sqlalchemy_schema import MessagePackValue, build_sqlalchemy_schema
from quire.sqlalchemy_store import (
    create_sqlalchemy_store,
    readonly_session,
    writable_session,
)


Scalar = str | bool | int | float


@charter(
    key="typed_scalar",
    name="typed_scalar",
    contract_version="2026.07.21",
    placement="typed_scalar",
    identity_field="name",
)
class TypedScalarDocument(CharterDoc):
    name: Annotated[str, charter_field(primary_key=True)]
    value: Annotated[
        Scalar | None,
        charter_field(nullable=True, storage_codec="messagepack"),
    ] = None


def _schema():
    return build_sqlalchemy_schema(charter_catalog(TypedScalarDocument.__charter__))


@pytest.mark.parametrize(
    ("value", "expected_type"),
    (
        ("red", str),
        (True, bool),
        (7, int),
        (7.5, float),
        (None, type(None)),
    ),
)
def test_messagepack_charter_field_round_trips_exact_runtime_type(
    tmp_path: Path,
    value: Scalar | None,
    expected_type: type[object],
) -> None:
    schema = _schema()
    store_path = tmp_path / "derived.sqlite"
    create_sqlalchemy_store(store_path, schema)
    row_type = schema.model("typed_scalar")

    with writable_session(store_path, schema) as session:
        session.add(row_type(name=expected_type.__name__, value=value))
        session.commit()

    with readonly_session(store_path, schema) as session:
        row = session.scalar(select(row_type))
        assert row is not None
        assert row.value == value
        assert type(row.value) is expected_type


def test_messagepack_codec_is_visible_in_schema_contract() -> None:
    field = _schema().schema_object("typed_scalar").fields[1]

    assert field.sql_type.storage_kind == "messagepack"
    assert field.sql_type.ddl_name == "BLOB"
    assert field.payload()["storage_codec"] == "messagepack"


def test_messagepack_codec_strictly_rejects_values_outside_declared_type() -> None:
    codec = MessagePackValue(Scalar | None)

    with pytest.raises(msgspec.ValidationError):
        codec.process_bind_param(["not", "a", "scalar"], object())


def test_encoded_messagepack_nil_is_not_a_second_absence_representation(
    tmp_path: Path,
) -> None:
    schema = _schema()
    store_path = tmp_path / "derived.sqlite"
    create_sqlalchemy_store(store_path, schema)
    with sqlite3.connect(store_path) as connection:
        connection.execute(
            "INSERT INTO typed_scalar (name, value) VALUES (?, ?)",
            ("corrupt", msgspec.msgpack.encode(None)),
        )

    row_type = schema.model("typed_scalar")
    with readonly_session(store_path, schema) as session:
        with pytest.raises(ValueError, match="encoded MessagePack nil"):
            session.scalar(select(row_type))

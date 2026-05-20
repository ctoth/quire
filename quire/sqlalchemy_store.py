from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, inspect, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from quire.sqlalchemy_schema import SqlAlchemySchema

__all__ = [
    "DerivedSession",
    "create_sqlalchemy_store",
    "readonly_session",
    "validate_sqlalchemy_store",
    "writable_session",
]

SCHEMA_CATALOG_TABLE = "quire_schema_catalog"
SCHEMA_CATALOG_KEY = "default"
SQLITE_BUSY_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class DerivedSession:
    session: Session
    schema: SqlAlchemySchema

    def __getattr__(self, name: str) -> Any:
        return getattr(self.session, name)

    def close(self) -> None:
        self.session.close()


def create_sqlalchemy_store(path: str | PathLike[str], schema: SqlAlchemySchema) -> None:
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    engine = _engine(path_obj, readonly=False)
    try:
        schema.metadata.create_all(engine)
        with engine.begin() as conn:
            _write_schema_catalog(conn, schema)
    finally:
        engine.dispose()


def validate_sqlalchemy_store(path: str | PathLike[str], schema: SqlAlchemySchema) -> None:
    engine = _engine(Path(path), readonly=True)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        for table_name, table in schema.tables.items():
            if table_name not in table_names:
                raise ValueError(f"Unsupported SQLAlchemy store: missing table {table_name!r}.")
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            expected_columns = set(table.c.keys())
            missing_columns = expected_columns - actual_columns
            if missing_columns:
                missing = ", ".join(sorted(missing_columns))
                raise ValueError(
                    f"Unsupported SQLAlchemy store: table {table_name!r} missing column(s) {missing}."
                )
        with engine.connect() as conn:
            actual_hash = _read_schema_hash(conn)
        if actual_hash != schema.catalog_hash:
            raise ValueError(
                "Unsupported SQLAlchemy store schema hash: "
                f"expected {schema.catalog_hash}, found {actual_hash}."
            )
    finally:
        engine.dispose()


@contextmanager
def writable_session(
    path: str | PathLike[str],
    schema: SqlAlchemySchema,
) -> Iterator[DerivedSession]:
    engine = _engine(Path(path), readonly=False)
    session = Session(engine)
    try:
        yield DerivedSession(session=session, schema=schema)
    finally:
        session.close()
        engine.dispose()


@contextmanager
def readonly_session(
    path: str | PathLike[str],
    schema: SqlAlchemySchema,
) -> Iterator[DerivedSession]:
    engine = _engine(Path(path), readonly=True)
    session = Session(engine)
    try:
        yield DerivedSession(session=session, schema=schema)
    finally:
        session.close()
        engine.dispose()


def _engine(path: Path, *, readonly: bool) -> Engine:
    engine = create_engine(_sqlite_url(path), future=True)

    @event.listens_for(engine, "connect")
    def configure(dbapi_connection: Any, connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA foreign_keys = ON")
            if readonly:
                cursor.execute("PRAGMA query_only = ON")
            else:
                cursor.execute("PRAGMA journal_mode = WAL")
        finally:
            cursor.close()

    return engine


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _write_schema_catalog(conn: Connection, schema: SqlAlchemySchema) -> None:
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_CATALOG_TABLE} (
                key TEXT PRIMARY KEY,
                schema_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA_CATALOG_TABLE} (key, schema_hash, payload_json)
            VALUES (:key, :schema_hash, :payload_json)
            ON CONFLICT(key) DO UPDATE SET
                schema_hash = excluded.schema_hash,
                payload_json = excluded.payload_json
            """
        ),
        {
            "key": SCHEMA_CATALOG_KEY,
            "schema_hash": schema.catalog_hash,
            "payload_json": json.dumps(
                schema.catalog.payload(),
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    )


def _read_schema_hash(conn: Connection) -> str:
    row = conn.execute(
        text(
            f"""
            SELECT schema_hash
            FROM {SCHEMA_CATALOG_TABLE}
            WHERE key = :key
            """
        ),
        {"key": SCHEMA_CATALOG_KEY},
    ).mappings().first()
    if row is None:
        raise ValueError("Unsupported SQLAlchemy store: missing schema catalog row.")
    return _schema_hash_from_row(row)


def _schema_hash_from_row(row: RowMapping) -> str:
    value = row["schema_hash"]
    if not isinstance(value, str):
        raise ValueError("Unsupported SQLAlchemy store: invalid schema catalog hash.")
    return value

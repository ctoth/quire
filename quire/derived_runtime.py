from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 30_000
DERIVED_STORE_META_KEY = "derived_store"


@dataclass(frozen=True)
class SqliteConnectionPolicy:
    busy_timeout_ms: int = DEFAULT_SQLITE_BUSY_TIMEOUT_MS
    foreign_keys: bool = True
    journal_mode: str | None = "WAL"
    query_only: bool = False


SQLITE_WRITE_POLICY = SqliteConnectionPolicy()
SQLITE_READONLY_POLICY = SqliteConnectionPolicy(journal_mode=None, query_only=True)

def configure_sqlite_connection(
    conn: sqlite3.Connection,
    policy: SqliteConnectionPolicy = SQLITE_WRITE_POLICY,
) -> sqlite3.Connection:
    conn.execute(f"PRAGMA busy_timeout = {policy.busy_timeout_ms}")
    if policy.foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    if policy.journal_mode is not None:
        conn.execute(f"PRAGMA journal_mode = {policy.journal_mode}")
    if policy.query_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


def connect_sqlite_store(
    path: str | PathLike[str],
    policy: SqliteConnectionPolicy = SQLITE_WRITE_POLICY,
) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    try:
        configure_sqlite_connection(conn, policy)
    except Exception:
        conn.close()
        raise
    return conn


def connect_sqlite_store_readonly(
    path: str | PathLike[str],
    policy: SqliteConnectionPolicy = SQLITE_READONLY_POLICY,
) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        configure_sqlite_connection(conn, policy)
    except Exception:
        conn.close()
        raise
    return conn


def create_derived_store_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL
        )
        """
    )


def write_derived_store_schema_metadata(
    conn: sqlite3.Connection,
    *,
    schema_version: int,
    key: str = DERIVED_STORE_META_KEY,
) -> None:
    create_derived_store_meta_table(conn)
    conn.execute(
        """
        INSERT INTO meta (key, schema_version)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET schema_version=excluded.schema_version
        """,
        (key, schema_version),
    )


def sqlite_table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def read_derived_store_schema_version(
    conn: sqlite3.Connection,
    *,
    key: str = DERIVED_STORE_META_KEY,
) -> int:
    if not sqlite_table_exists(conn, "meta"):
        raise ValueError("Unsupported derived store schema: missing table(s) meta.")
    row = conn.execute(
        "SELECT schema_version FROM meta WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        raise ValueError("Unsupported derived store schema: missing metadata row.")
    try:
        return int(row["schema_version"])
    except (IndexError, TypeError):
        return int(row[0])


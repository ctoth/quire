from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from quire.schema_ir import SchemaVectorCache
from quire.sqlalchemy_schema import SqlAlchemySchema

__all__ = [
    "EMBEDDING_MODEL_TABLE",
    "EmbeddingModelIdentity",
    "RestoreReport",
    "SqlAlchemyVecEntityStore",
    "SqlAlchemyVecRegistry",
    "SqlAlchemyVecSnapshotStore",
    "VecEntitySnapshot",
    "VecSnapshot",
    "create_vector_cache_schema",
    "is_missing_table_error",
    "load_sqlite_vec_extension",
    "validate_vector_cache_schema",
]

EMBEDDING_MODEL_TABLE = "embedding_model"


class EmbeddingModelIdentity(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def content_digest(self) -> str: ...

    @property
    def identity_hash(self) -> str: ...


@dataclass(frozen=True)
class VecEntitySnapshot:
    statuses: list[dict[str, Any]]
    vectors: dict[str, list[tuple[int, str, bytes]]]


@dataclass(frozen=True)
class VecSnapshot:
    models: list[dict[str, Any]]
    entities: dict[str, VecEntitySnapshot]


@dataclass
class RestoreReport:
    restored: int = 0
    stale: int = 0
    orphaned: int = 0


def load_sqlite_vec_extension(dbapi_connection: Any) -> None:
    import sqlite_vec

    dbapi_connection.enable_load_extension(True)
    sqlite_vec.load(dbapi_connection)


def is_missing_table_error(error: Exception) -> bool:
    return "no such table" in str(error)


def create_vector_cache_schema(conn: Connection, schema: SqlAlchemySchema) -> None:
    if not schema.vector_caches:
        return
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {_quote_identifier(EMBEDDING_MODEL_TABLE)} (
                model_identity_hash TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model_name TEXT NOT NULL,
                model_version TEXT NOT NULL DEFAULT '',
                content_digest TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    )
    for cache in schema.vector_caches.values():
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {_quote_identifier(cache.status_table_name)} (
                    model_identity_hash TEXT NOT NULL,
                    {_quote_identifier(cache.entity_id_field)} TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedded_at TEXT NOT NULL,
                    PRIMARY KEY (model_identity_hash, {_quote_identifier(cache.entity_id_field)})
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS {_quote_identifier(f"ix_{cache.status_table_name}_model")}
                ON {_quote_identifier(cache.status_table_name)} (model_identity_hash)
                """
            )
        )


def validate_vector_cache_schema(conn: Connection, schema: SqlAlchemySchema) -> None:
    if not schema.vector_caches:
        return
    required = {EMBEDDING_MODEL_TABLE}
    required.update(cache.status_table_name for cache in schema.vector_caches.values())
    rows = conn.execute(
        text(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'view')
            """
        )
    ).scalars()
    present = {str(row) for row in rows}
    missing = required - present
    if missing:
        joined = ", ".join(sorted(missing))
        raise ValueError(f"Unsupported SQLAlchemy store: missing vector cache table(s) {joined}.")


class SqlAlchemyVecRegistry:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get_registered_models(self) -> list[dict[str, Any]]:
        if not _table_exists(self._conn, EMBEDDING_MODEL_TABLE):
            return []
        rows = self._conn.execute(
            text(
                f"""
                SELECT
                    model_identity_hash,
                    provider,
                    model_name,
                    model_version,
                    content_digest,
                    dimensions,
                    created_at
                FROM {_quote_identifier(EMBEDDING_MODEL_TABLE)}
                """
            )
        ).mappings()
        return [dict(row) for row in rows]


class SqlAlchemyVecEntityStore:
    def __init__(self, conn: Connection, cache: SchemaVectorCache) -> None:
        self._conn = conn
        self.cache = cache

    def existing_content_hashes(
        self,
        model_identity: EmbeddingModelIdentity,
    ) -> dict[str, str]:
        if not _table_exists(self._conn, self.cache.status_table_name):
            return {}
        rows = self._conn.execute(
            text(
                f"""
                SELECT {_quote_identifier(self.cache.entity_id_field)}, content_hash
                FROM {_quote_identifier(self.cache.status_table_name)}
                WHERE model_identity_hash = :model_identity_hash
                """
            ),
            {"model_identity_hash": model_identity.identity_hash},
        ).mappings()
        return {
            str(row[self.cache.entity_id_field]): str(row["content_hash"])
            for row in rows
        }

    def prepare_model(
        self,
        model_identity: EmbeddingModelIdentity,
        created_at: str,
    ) -> None:
        self._conn.execute(
            text(
                f"""
                INSERT OR REPLACE INTO {_quote_identifier(EMBEDDING_MODEL_TABLE)}
                    (model_identity_hash, provider, model_name, model_version,
                     content_digest, dimensions, created_at)
                VALUES
                    (:model_identity_hash, :provider, :model_name, :model_version,
                     :content_digest, :dimensions, :created_at)
                """
            ),
            {
                "model_identity_hash": model_identity.identity_hash,
                "provider": model_identity.provider,
                "model_name": model_identity.model_name,
                "model_version": model_identity.model_version,
                "content_digest": model_identity.content_digest,
                "dimensions": self.cache.dimensions,
                "created_at": created_at,
            },
        )
        self.ensure_vec_table(model_identity.identity_hash)

    def ensure_vec_table(self, model_identity_hash: str) -> None:
        table_name = self._table_name(model_identity_hash)
        if _table_exists(self._conn, table_name):
            return
        self._conn.exec_driver_sql(
            f"CREATE VIRTUAL TABLE {_quote_identifier(table_name)} "
            f"USING vec0({_vec_column(self.cache.embedding_column)} float[{self.cache.dimensions}])"
        )

    def save_embedding(
        self,
        *,
        model_identity: EmbeddingModelIdentity,
        entity_id: str,
        seq: int,
        content_hash: str,
        vector_blob: bytes,
        embedded_at: str,
    ) -> None:
        table_name = self._table_name(model_identity.identity_hash)
        self._conn.exec_driver_sql(
            f"DELETE FROM {_quote_identifier(table_name)} WHERE rowid = ?",
            (seq,),
        )
        self._conn.exec_driver_sql(
            f"INSERT INTO {_quote_identifier(table_name)} "
            f"(rowid, {_quote_identifier(self.cache.embedding_column)}) VALUES (?, ?)",
            (seq, vector_blob),
        )
        self._conn.execute(
            text(
                f"""
                INSERT OR REPLACE INTO {_quote_identifier(self.cache.status_table_name)}
                    (model_identity_hash, {_quote_identifier(self.cache.entity_id_field)},
                     content_hash, embedded_at)
                VALUES
                    (:model_identity_hash, :entity_id, :content_hash, :embedded_at)
                """
            ),
            {
                "model_identity_hash": model_identity.identity_hash,
                "entity_id": entity_id,
                "content_hash": content_hash,
                "embedded_at": embedded_at,
            },
        )

    def vector_for(
        self,
        model_identity: EmbeddingModelIdentity,
        seq: int,
    ) -> bytes | None:
        table_name = self._table_name(model_identity.identity_hash)
        row = self._conn.exec_driver_sql(
            f"SELECT {_quote_identifier(self.cache.embedding_column)} "
            f"FROM {_quote_identifier(table_name)} WHERE rowid = ?",
            (seq,),
        ).first()
        if row is None:
            return None
        value = row[0]
        return value if isinstance(value, bytes) else bytes(value)

    def similar_entities(
        self,
        *,
        model_identity: EmbeddingModelIdentity,
        query_vector: bytes,
        k: int,
    ) -> list[dict[str, Any]]:
        table_name = self._table_name(model_identity.identity_hash)
        rows = self._conn.exec_driver_sql(
            f"""
            SELECT
                v.rowid,
                v.distance,
                status.{_quote_identifier(self.cache.entity_id_field)} AS entity_id
            FROM {_quote_identifier(table_name)} v
            JOIN {_quote_identifier(self.cache.status_table_name)} status
              ON status.model_identity_hash = ?
            JOIN {_quote_identifier(self.cache.family_name)} source
              ON source.{_quote_identifier(self.cache.source_seq_field)} = v.rowid
             AND source.{_quote_identifier(self.cache.entity_id_field)}
                 = status.{_quote_identifier(self.cache.entity_id_field)}
            WHERE v.{_quote_identifier(self.cache.embedding_column)} MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (model_identity.identity_hash, query_vector, k),
        ).mappings()
        return [dict(row) for row in rows]

    def _table_name(self, model_identity_hash: str) -> str:
        return _render_dynamic_name(
            self.cache.table,
            {
                "model_identity_hash": model_identity_hash,
                "dimensions": str(self.cache.dimensions),
            },
        )


class SqlAlchemyVecSnapshotStore:
    def __init__(
        self,
        conn: Connection,
        caches: Sequence[SchemaVectorCache],
    ) -> None:
        self._conn = conn
        self._caches = tuple(caches)

    def extract(self) -> VecSnapshot | None:
        if not _table_exists(self._conn, EMBEDDING_MODEL_TABLE):
            return None
        models = [
            dict(row)
            for row in self._conn.execute(
                text(f"SELECT * FROM {_quote_identifier(EMBEDDING_MODEL_TABLE)}")
            ).mappings()
        ]
        if not models:
            return None
        return VecSnapshot(
            models=models,
            entities={
                cache.name: VecEntitySnapshot(
                    statuses=self._extract_statuses(cache),
                    vectors=self._extract_vectors(cache, models),
                )
                for cache in self._caches
            },
        )

    def restore(self, snapshot: VecSnapshot) -> RestoreReport:
        report = RestoreReport()
        for cache in self._caches:
            entity_snapshot = snapshot.entities.get(cache.name)
            if entity_snapshot is not None:
                self._restore_entity_snapshot(snapshot.models, cache, entity_snapshot, report)
        return report

    def _extract_statuses(self, cache: SchemaVectorCache) -> list[dict[str, Any]]:
        if not _table_exists(self._conn, cache.status_table_name):
            return []
        return [
            dict(row)
            for row in self._conn.execute(
                text(f"SELECT * FROM {_quote_identifier(cache.status_table_name)}")
            ).mappings()
        ]

    def _extract_vectors(
        self,
        cache: SchemaVectorCache,
        models: list[dict[str, Any]],
    ) -> dict[str, list[tuple[int, str, bytes]]]:
        vectors: dict[str, list[tuple[int, str, bytes]]] = {}
        for model in models:
            model_identity_hash = str(model["model_identity_hash"])
            table_name = _render_dynamic_name(
                cache.table,
                {
                    "model_identity_hash": model_identity_hash,
                    "dimensions": str(model["dimensions"]),
                },
            )
            if not _table_exists(self._conn, table_name):
                vectors[model_identity_hash] = []
                continue
            rows = self._conn.exec_driver_sql(
                f"""
                SELECT
                    v.rowid,
                    status.{_quote_identifier(cache.entity_id_field)},
                    v.{_quote_identifier(cache.embedding_column)}
                FROM {_quote_identifier(table_name)} v
                JOIN {_quote_identifier(cache.status_table_name)} status
                  ON status.model_identity_hash = ?
                JOIN {_quote_identifier(cache.family_name)} source
                  ON source.{_quote_identifier(cache.source_seq_field)} = v.rowid
                 AND source.{_quote_identifier(cache.entity_id_field)}
                     = status.{_quote_identifier(cache.entity_id_field)}
                WHERE status.model_identity_hash = ?
                """,
                (model_identity_hash, model_identity_hash),
            )
            vectors[model_identity_hash] = [
                (int(row[0]), str(row[1]), _bytes(row[2]))
                for row in rows
            ]
        return vectors

    def _restore_entity_snapshot(
        self,
        models: list[dict[str, Any]],
        cache: SchemaVectorCache,
        snapshot: VecEntitySnapshot,
        report: RestoreReport,
    ) -> None:
        current_entities = self._current_entities(cache)
        status_lookup = {
            (
                str(status["model_identity_hash"]),
                str(status[cache.entity_id_field]),
            ): str(status["content_hash"])
            for status in snapshot.statuses
        }
        embedded_at_lookup = {
            (
                str(status["model_identity_hash"]),
                str(status[cache.entity_id_field]),
            ): str(status.get("embedded_at", ""))
            for status in snapshot.statuses
        }
        for model in models:
            model_identity_hash = str(model["model_identity_hash"])
            self._conn.execute(
                text(
                    f"""
                    INSERT OR REPLACE INTO {_quote_identifier(EMBEDDING_MODEL_TABLE)}
                        (model_identity_hash, provider, model_name, model_version,
                         content_digest, dimensions, created_at)
                    VALUES
                        (:model_identity_hash, :provider, :model_name, :model_version,
                         :content_digest, :dimensions, :created_at)
                    """
                ),
                dict(model),
            )
            table_name = _render_dynamic_name(
                cache.table,
                {
                    "model_identity_hash": model_identity_hash,
                    "dimensions": str(model["dimensions"]),
                },
            )
            if not _table_exists(self._conn, table_name):
                self._conn.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE {_quote_identifier(table_name)} "
                    f"USING vec0({_vec_column(cache.embedding_column)} "
                    f"float[{int(model['dimensions'])}])"
                )
            for _old_seq, entity_id, blob in snapshot.vectors.get(model_identity_hash, []):
                current = current_entities.get(entity_id)
                if current is None:
                    report.orphaned += 1
                    continue
                new_seq, current_hash = current
                if current_hash != status_lookup.get((model_identity_hash, entity_id), ""):
                    report.stale += 1
                    continue
                self._conn.exec_driver_sql(
                    f"DELETE FROM {_quote_identifier(table_name)} WHERE rowid = ?",
                    (new_seq,),
                )
                self._conn.exec_driver_sql(
                    f"INSERT INTO {_quote_identifier(table_name)} "
                    f"(rowid, {_quote_identifier(cache.embedding_column)}) VALUES (?, ?)",
                    (new_seq, blob),
                )
                self._conn.execute(
                    text(
                        f"""
                        INSERT OR REPLACE INTO {_quote_identifier(cache.status_table_name)}
                            (model_identity_hash, {_quote_identifier(cache.entity_id_field)},
                             content_hash, embedded_at)
                        VALUES
                            (:model_identity_hash, :entity_id, :content_hash, :embedded_at)
                        """
                    ),
                    {
                        "model_identity_hash": model_identity_hash,
                        "entity_id": entity_id,
                        "content_hash": current_hash,
                        "embedded_at": embedded_at_lookup.get((model_identity_hash, entity_id), ""),
                    },
                )
                report.restored += 1

    def _current_entities(self, cache: SchemaVectorCache) -> dict[str, tuple[int, str]]:
        if not _table_exists(self._conn, cache.family_name):
            return {}
        rows = self._conn.execute(
            text(
                f"""
                SELECT
                    {_quote_identifier(cache.entity_id_field)},
                    {_quote_identifier(cache.source_seq_field)},
                    {_quote_identifier(cache.source_content_hash_field)}
                FROM {_quote_identifier(cache.family_name)}
                """
            )
        )
        return {
            str(row[0]): (int(row[1]), str(row[2]))
            for row in rows
        }


def _table_exists(conn: Connection, table_name: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type IN ('table', 'view') AND name = :table_name
            """
        ),
        {"table_name": table_name},
    ).first()
    return row is not None


def _render_dynamic_name(template: str, bindings: dict[str, str]) -> str:
    return template.format(**bindings)


def _quote_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _vec_column(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum() or identifier[0].isdigit():
        raise ValueError(f"Unsupported vector column identifier {identifier!r}.")
    return identifier


def _bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    raise TypeError(f"expected vector blob bytes, got {type(value).__name__}")

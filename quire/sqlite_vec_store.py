from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol

from quire.projections import ProjectionColumn, ProjectionIndex, ProjectionTable, VecProjection, quote_identifier

__all__ = [
    "EMBEDDING_MODEL_PROJECTION",
    "EmbeddingModelIdentity",
    "RestoreReport",
    "SqliteVecEntityStore",
    "SqliteVecRegistry",
    "SqliteVecSnapshotStore",
    "VecEntitySnapshot",
    "VecEntityStoreSpec",
    "VecSnapshot",
    "embedding_status_projection",
    "ensure_embedding_tables",
    "is_missing_table_error",
    "rowid_vec_projection",
]


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
class VecEntityStoreSpec:
    name: str
    status_projection: ProjectionTable
    status_id_column: str
    vector_projection: VecProjection
    source_table: str
    source_id_column: str = "id"
    source_seq_column: str = "seq"
    source_content_hash_column: str = "content_hash"


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


EMBEDDING_MODEL_PROJECTION = ProjectionTable(
    name="embedding_model",
    columns=(
        ProjectionColumn("model_identity_hash", "TEXT", nullable=False, primary_key=True),
        ProjectionColumn("provider", "TEXT", nullable=False),
        ProjectionColumn("model_name", "TEXT", nullable=False),
        ProjectionColumn("model_version", "TEXT", nullable=False, default_sql="''"),
        ProjectionColumn("content_digest", "TEXT", nullable=False),
        ProjectionColumn("dimensions", "INTEGER", nullable=False),
        ProjectionColumn("created_at", "TEXT", nullable=False),
    ),
    if_not_exists=True,
)


def embedding_status_projection(
    *,
    name: str,
    entity_id_column: str,
    index_name: str,
) -> ProjectionTable:
    return ProjectionTable(
        name=name,
        columns=(
            ProjectionColumn("model_identity_hash", "TEXT", nullable=False),
            ProjectionColumn(entity_id_column, "TEXT", nullable=False),
            ProjectionColumn("content_hash", "TEXT", nullable=False),
            ProjectionColumn("embedded_at", "TEXT", nullable=False),
        ),
        primary_key=("model_identity_hash", entity_id_column),
        indexes=(ProjectionIndex(index_name, ("model_identity_hash",)),),
        if_not_exists=True,
    )


def rowid_vec_projection(table: str) -> VecProjection:
    return VecProjection(
        table=table,
        key_column=None,
        vector_column=ProjectionColumn("embedding", "float[{dimensions}]", nullable=False),
    )


def is_missing_table_error(error: sqlite3.OperationalError) -> bool:
    return "no such table" in str(error)


def ensure_embedding_tables(
    conn: sqlite3.Connection,
    specs: tuple[VecEntityStoreSpec, ...],
) -> None:
    for statement in EMBEDDING_MODEL_PROJECTION.ddl_statements():
        conn.execute(statement)
    for spec in specs:
        for statement in spec.status_projection.ddl_statements():
            conn.execute(statement)


class SqliteVecRegistry:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_registered_models(self) -> list[dict[str, Any]]:
        try:
            return [
                dict(row)
                for row in self._conn.execute(
                    """
                    SELECT
                        model_identity_hash,
                        provider,
                        model_name,
                        model_version,
                        content_digest,
                        dimensions,
                        created_at
                    FROM embedding_model
                    """
                ).fetchall()
            ]
        except sqlite3.OperationalError as error:
            if not is_missing_table_error(error):
                raise
            return []


class SqliteVecEntityStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        spec: VecEntityStoreSpec,
    ) -> None:
        self._conn = conn
        self.spec = spec

    def existing_content_hashes(
        self,
        model_identity: EmbeddingModelIdentity,
    ) -> dict[str, str]:
        existing: dict[str, str] = {}
        try:
            rows = self._conn.execute(
                f"SELECT {quote_identifier(self.spec.status_id_column)}, content_hash "
                f"FROM {quote_identifier(self.spec.status_projection.name)} "
                "WHERE model_identity_hash=?",
                (model_identity.identity_hash,),
            ).fetchall()
        except sqlite3.OperationalError as error:
            if not is_missing_table_error(error):
                raise
            return existing
        for row in rows:
            existing[str(row[self.spec.status_id_column])] = str(row["content_hash"])
        return existing

    def prepare_model(
        self,
        model_identity: EmbeddingModelIdentity,
        dimensions: int,
        created_at: str,
    ) -> None:
        EMBEDDING_MODEL_PROJECTION.insert_row(
            self._conn,
            {
                "model_identity_hash": model_identity.identity_hash,
                "provider": model_identity.provider,
                "model_name": model_identity.model_name,
                "model_version": model_identity.model_version,
                "content_digest": model_identity.content_digest,
                "dimensions": dimensions,
                "created_at": created_at,
            },
            or_replace=True,
        )
        self.ensure_vec_table(model_identity.identity_hash, dimensions)

    def ensure_vec_table(self, model_identity_hash: str, dimensions: int) -> None:
        bindings = self._bindings(model_identity_hash, dimensions)
        table_name = self.spec.vector_projection.projection_name(bindings)
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if row is not None:
            return
        for statement in self.spec.vector_projection.ddl_statements(bindings):
            self._conn.execute(statement)

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
        bindings = self._bindings(model_identity.identity_hash)
        self._conn.execute(
            self.spec.vector_projection.delete_rowid_sql(bindings),
            {"rowid": seq},
        )
        self._conn.execute(
            self.spec.vector_projection.insert_rowid_sql(bindings),
            {"rowid": seq, "embedding": vector_blob},
        )
        self.spec.status_projection.insert_row(
            self._conn,
            {
                "model_identity_hash": model_identity.identity_hash,
                self.spec.status_id_column: entity_id,
                "content_hash": content_hash,
                "embedded_at": embedded_at,
            },
            or_replace=True,
        )

    def vector_for(
        self,
        model_identity: EmbeddingModelIdentity,
        seq: int,
    ) -> bytes | None:
        table_name = self.spec.vector_projection.projection_name(
            self._bindings(model_identity.identity_hash)
        )
        row = self._conn.execute(
            f"SELECT embedding FROM {quote_identifier(table_name)} WHERE rowid = ?",
            (seq,),
        ).fetchone()
        return None if row is None else row["embedding"]

    def similar_entities(
        self,
        *,
        model_identity: EmbeddingModelIdentity,
        query_vector: bytes,
        k: int,
        join_source: str,
        join_columns: str,
    ) -> list[dict[str, Any]]:
        table_name = self.spec.vector_projection.projection_name(
            self._bindings(model_identity.identity_hash)
        )
        rows = self._conn.execute(
            f"""SELECT v.rowid, v.distance, {join_columns}
                FROM {quote_identifier(table_name)} v
                JOIN {join_source} c ON c.seq = v.rowid
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance""",
            (query_vector, k),
        ).fetchall()
        return [dict(row) for row in rows]

    def _bindings(
        self,
        model_identity_hash: str,
        dimensions: int = 0,
    ) -> dict[str, str]:
        return {
            "model_identity_hash": model_identity_hash,
            "dimensions": str(dimensions),
        }


class SqliteVecSnapshotStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        specs: tuple[VecEntityStoreSpec, ...],
    ) -> None:
        self._conn = conn
        self._specs = specs

    def extract(self) -> VecSnapshot | None:
        try:
            models = [
                dict(row)
                for row in self._conn.execute("SELECT * FROM embedding_model").fetchall()
            ]
        except sqlite3.OperationalError as error:
            if not is_missing_table_error(error):
                raise
            return None
        if not models:
            return None
        return VecSnapshot(
            models=models,
            entities={
                spec.name: VecEntitySnapshot(
                    statuses=self._extract_statuses(spec),
                    vectors=self._extract_vectors(spec, models),
                )
                for spec in self._specs
            },
        )

    def restore(self, snapshot: VecSnapshot) -> RestoreReport:
        ensure_embedding_tables(self._conn, self._specs)
        report = RestoreReport()
        for spec in self._specs:
            entity_snapshot = snapshot.entities.get(spec.name)
            if entity_snapshot is not None:
                self._restore_entity_snapshot(snapshot.models, spec, entity_snapshot, report)
        return report

    def _extract_statuses(self, spec: VecEntityStoreSpec) -> list[dict[str, Any]]:
        try:
            return [
                dict(row)
                for row in self._conn.execute(
                    f"SELECT * FROM {quote_identifier(spec.status_projection.name)}"
                ).fetchall()
            ]
        except sqlite3.OperationalError as error:
            if not is_missing_table_error(error):
                raise
            return []

    def _extract_vectors(
        self,
        spec: VecEntityStoreSpec,
        models: list[dict[str, Any]],
    ) -> dict[str, list[tuple[int, str, bytes]]]:
        vectors: dict[str, list[tuple[int, str, bytes]]] = {}
        for model in models:
            model_identity_hash = str(model["model_identity_hash"])
            table_name = spec.vector_projection.projection_name(
                {
                    "model_identity_hash": model_identity_hash,
                    "dimensions": str(model["dimensions"]),
                }
            )
            try:
                rows = self._conn.execute(
                    f"""SELECT v.rowid, status.{quote_identifier(spec.status_id_column)}, v.embedding
                        FROM {quote_identifier(table_name)} v
                        JOIN {quote_identifier(spec.status_projection.name)} status
                          ON status.model_identity_hash = ?
                        JOIN {quote_identifier(spec.source_table)} source
                          ON source.{quote_identifier(spec.source_seq_column)} = v.rowid
                         AND source.{quote_identifier(spec.source_id_column)}
                             = status.{quote_identifier(spec.status_id_column)}
                        WHERE status.model_identity_hash = ?""",
                    (model_identity_hash, model_identity_hash),
                ).fetchall()
            except sqlite3.OperationalError as error:
                if not is_missing_table_error(error):
                    raise
                rows = []
            vectors[model_identity_hash] = [
                (int(row[0]), str(row[1]), row[2])
                for row in rows
            ]
        return vectors

    def _restore_entity_snapshot(
        self,
        models: list[dict[str, Any]],
        spec: VecEntityStoreSpec,
        snapshot: VecEntitySnapshot,
        report: RestoreReport,
    ) -> None:
        current_entities = self._current_entities(spec)
        status_lookup = {
            (
                status["model_identity_hash"],
                status[spec.status_id_column],
            ): status["content_hash"]
            for status in snapshot.statuses
        }
        embedded_at_lookup = {
            (
                status["model_identity_hash"],
                status[spec.status_id_column],
            ): status.get("embedded_at", "")
            for status in snapshot.statuses
        }
        store = SqliteVecEntityStore(self._conn, spec)
        for model in models:
            model_identity_hash = str(model["model_identity_hash"])
            EMBEDDING_MODEL_PROJECTION.insert_row(
                self._conn,
                model,
                or_replace=True,
            )
            store.ensure_vec_table(model_identity_hash, int(model["dimensions"]))
            bindings = store._bindings(model_identity_hash, int(model["dimensions"]))
            status_insert_sql = spec.status_projection.insert_sql(or_replace=True)
            for _old_seq, entity_id, blob in snapshot.vectors.get(
                model_identity_hash,
                [],
            ):
                current = current_entities.get(entity_id)
                if current is None:
                    report.orphaned += 1
                    continue
                new_seq, current_hash = current
                if current_hash != status_lookup.get((model_identity_hash, entity_id), ""):
                    report.stale += 1
                    continue
                self._conn.execute(
                    spec.vector_projection.insert_rowid_sql(bindings),
                    {"rowid": new_seq, "embedding": blob},
                )
                self._conn.execute(
                    status_insert_sql,
                    {
                        "model_identity_hash": model_identity_hash,
                        spec.status_id_column: entity_id,
                        "content_hash": current_hash,
                        "embedded_at": embedded_at_lookup.get(
                            (model_identity_hash, entity_id),
                            "",
                        ),
                    },
                )
                report.restored += 1

    def _current_entities(self, spec: VecEntityStoreSpec) -> dict[str, tuple[int, str]]:
        try:
            rows = self._conn.execute(
                f"""SELECT
                        {quote_identifier(spec.source_id_column)},
                        {quote_identifier(spec.source_seq_column)},
                        {quote_identifier(spec.source_content_hash_column)}
                    FROM {quote_identifier(spec.source_table)}"""
            ).fetchall()
        except sqlite3.OperationalError as error:
            if not is_missing_table_error(error):
                raise
            return {}
        return {
            str(row[0]): (int(row[1]), str(row[2]))
            for row in rows
        }

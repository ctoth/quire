from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from quire.derived_store import DerivedStoreManager
from quire.projections import (
    FtsProjection,
    ProjectionColumn,
    ProjectionForeignKey,
    ProjectionIndex,
    ProjectionSchemaError,
    ProjectionTable,
    VecProjection,
    create_projection_schema,
    json_decoder,
    json_encoder,
    render_projection_name,
)


def _build_sqlite(path: Path, value: str = "ready") -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker (value) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def test_materialize_publishes_content_addressed_store_and_opens_readonly(tmp_path):
    manager = DerivedStoreManager(tmp_path / "derived")

    handle = manager.materialize(
        projection_id="propstore.world",
        source_commit="a" * 40,
        content_hash="schema-a",
        build=lambda path: _build_sqlite(path),
    )

    assert handle.path.is_file()
    assert handle.path.parent.name == "propstore.world"
    conn = handle.open_readonly()
    try:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "ready"
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO marker (value) VALUES ('write')")
    finally:
        conn.close()


def test_materialize_reuses_existing_cache_without_rebuilding(tmp_path):
    manager = DerivedStoreManager(tmp_path / "derived")
    calls = 0

    def build(path: Path) -> None:
        nonlocal calls
        calls += 1
        _build_sqlite(path, value=f"build-{calls}")

    first = manager.materialize(
        projection_id="propstore.world",
        source_commit="b" * 40,
        content_hash="schema-a",
        build=build,
    )
    second = manager.materialize(
        projection_id="propstore.world",
        source_commit="b" * 40,
        content_hash="schema-a",
        build=build,
    )

    assert first == second
    assert calls == 1


def test_materialize_cleans_failed_temp_store(tmp_path):
    manager = DerivedStoreManager(tmp_path / "derived")

    def build(path: Path) -> None:
        _build_sqlite(path)
        path.with_name(f"{path.name}-wal").write_bytes(b"wal")
        path.with_name(f"{path.name}-shm").write_bytes(b"shm")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        manager.materialize(
            projection_id="propstore.world",
            source_commit="c" * 40,
            content_hash="schema-a",
            build=build,
        )

    assert not list((tmp_path / "derived").glob("tmp/*"))


def test_gc_deletes_unkept_stores_and_temp_files(tmp_path):
    manager = DerivedStoreManager(tmp_path / "derived")
    keep = manager.materialize(
        projection_id="propstore.world",
        source_commit="d" * 40,
        content_hash="schema-a",
        build=lambda path: _build_sqlite(path, value="keep"),
    )
    drop = manager.materialize(
        projection_id="propstore.world",
        source_commit="e" * 40,
        content_hash="schema-a",
        build=lambda path: _build_sqlite(path, value="drop"),
    )
    temp = tmp_path / "derived" / "tmp" / "stale.sqlite"
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_bytes(b"temp")

    report = manager.gc(keep_cache_keys=(keep.cache_key,))

    assert keep.path.exists()
    assert not drop.path.exists()
    assert report.deleted_paths == (drop.path,)
    assert report.deleted_temp_paths == (temp,)
    assert not temp.exists()


def test_projection_table_validates_declared_columns_and_codecs():
    pages = ProjectionTable(
        name="pages",
        columns=(
            ProjectionColumn("id", "TEXT", nullable=False),
            ProjectionColumn(
                "metadata_json",
                "TEXT",
                encoder=json_encoder,
                decoder=json_decoder,
            ),
            ProjectionColumn("updated_at", "INTEGER", insertable=False),
        ),
        primary_key=("id",),
        indexes=(ProjectionIndex("idx_pages_updated", ("updated_at",)),),
    )

    encoded = pages.encode_row(
        {"id": "intro", "metadata_json": {"order": 1}, "updated_at": 12}
    )
    assert encoded == {
        "id": "intro",
        "metadata_json": '{"order":1}',
        "updated_at": 12,
    }
    decoded = pages.decode_row(encoded)

    assert decoded.values["metadata_json"] == {"order": 1}
    assert tuple(column.name for column in pages.insert_columns) == (
        "id",
        "metadata_json",
    )
    assert pages.schema_hash_material()["columns"][1]["codec"] == "json"


def test_projection_table_rejects_invalid_foreign_key_and_index_columns():
    with pytest.raises(ValueError, match="Foreign key references undeclared columns"):
        ProjectionTable(
            name="annotations",
            columns=(ProjectionColumn("id", "TEXT"),),
            foreign_keys=(
                ProjectionForeignKey(
                    columns=("missing_page_id",),
                    ref_table="pages",
                    ref_columns=("id",),
                ),
            ),
        )

    with pytest.raises(ValueError, match="references undeclared columns"):
        ProjectionTable(
            name="annotations",
            columns=(ProjectionColumn("id", "TEXT"),),
            indexes=(ProjectionIndex("idx_annotations_page", ("page_id",)),),
        )


def test_projection_schema_validates_names_and_hashes_declarations():
    pages = ProjectionTable(
        name="pages",
        columns=(ProjectionColumn("id", "TEXT", nullable=False),),
        primary_key=("id",),
    )
    search = FtsProjection(
        table="page_search",
        key_column="id",
        columns=("title", "body"),
        row_plan="page title/body text",
    )
    vectors = VecProjection(
        table="page_vec_{model}",
        key_column=ProjectionColumn("id", "TEXT"),
        vector_column=ProjectionColumn("embedding", "FLOAT[3]"),
    )

    schema = create_projection_schema(
        pages,
        search,
        vectors,
        metadata={"version": "one"},
    )

    assert schema.projection_names == ("pages", "page_search", "page_vec_{model}")
    assert schema.projection("pages") is pages
    assert search.population_plan() == "page title/body text"
    search.validate_search_columns(("title", "body"))
    assert render_projection_name("page_vec_{model}", {"model": "small_3"}) == "page_vec_small_3"
    assert '"version":"one"' in schema.schema_hash_material()


def test_projection_schema_rejects_duplicate_projection_names():
    first = ProjectionTable(
        name="pages",
        columns=(ProjectionColumn("id", "TEXT"),),
    )
    second = ProjectionTable(
        name="pages",
        columns=(ProjectionColumn("slug", "TEXT"),),
    )

    with pytest.raises(ValueError, match="duplicate projection names"):
        create_projection_schema(first, second)


def test_projection_schema_creates_tables_and_materializes_rows(tmp_path):
    db_path = tmp_path / "library.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        pages = ProjectionTable(
            name="pages",
            columns=(
                ProjectionColumn("id", "TEXT", nullable=False),
                ProjectionColumn("title", "TEXT", nullable=False),
                ProjectionColumn(
                    "metadata_json",
                    "TEXT",
                    encoder=json_encoder,
                    decoder=json_decoder,
                ),
            ),
            primary_key=("id",),
        )
        notes = ProjectionTable(
            name="notes",
            columns=(
                ProjectionColumn("id", "TEXT", nullable=False),
                ProjectionColumn("page_id", "TEXT", nullable=False),
                ProjectionColumn("body", "TEXT", nullable=False),
            ),
            primary_key=("id",),
            foreign_keys=(
                ProjectionForeignKey(
                    columns=("page_id",),
                    ref_table="pages",
                    ref_columns=("id",),
                ),
            ),
            indexes=(ProjectionIndex("idx_notes_page", ("page_id",)),),
        )
        schema = create_projection_schema(pages, notes)

        schema.create_all(conn)
        schema.validate_connection(conn)
        pages.insert_row(
            conn,
            {
                "id": "intro",
                "title": "Introduction",
                "metadata_json": {"rank": 1},
            },
        )
        notes.insert_row(
            conn,
            {"id": "n1", "page_id": "intro", "body": "Keep this."},
        )
        conn.commit()

        rows = pages.select_all(conn)

        assert rows[0].values == {
            "id": "intro",
            "title": "Introduction",
            "metadata_json": {"rank": 1},
        }
        assert notes.insert_sql() == (
            'INSERT INTO "notes" ("id", "page_id", "body") '
            "VALUES (:id, :page_id, :body)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            notes.insert_row(conn, {"id": "n2", "page_id": "missing", "body": "Nope."})
    finally:
        conn.close()


def test_projection_schema_validation_reports_missing_tables():
    schema = create_projection_schema(
        ProjectionTable(
            name="events",
            columns=(ProjectionColumn("id", "TEXT"),),
        )
    )
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ProjectionSchemaError, match="missing table"):
            schema.validate_connection(conn)
    finally:
        conn.close()

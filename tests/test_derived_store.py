from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

import pytest

from quire.derived_store import (
    DerivedStoreBuildDiagnostic,
    DerivedStoreBuildError,
    DerivedStoreManager,
    ProjectionBuildStep,
    digest_directory,
    derived_store_content_hash,
    materialize_sqlite_file,
    order_projection_steps,
    read_dependency_pins,
)
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
from quire.sqlite_vec_store import (
    SqliteVecEntityStore,
    VecEntityStoreSpec,
    embedding_status_projection,
    rowid_vec_projection,
)


def _serialize_float32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


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


def test_materialize_with_report_can_force_rebuild_existing_cache(tmp_path):
    manager = DerivedStoreManager(tmp_path / "derived")
    calls = 0

    def build(path: Path) -> None:
        nonlocal calls
        calls += 1
        _build_sqlite(path, value=f"build-{calls}")

    first = manager.materialize_with_report(
        projection_id="library.search",
        source_commit="b" * 40,
        content_hash="schema-a",
        build=build,
    )
    second = manager.materialize_with_report(
        projection_id="library.search",
        source_commit="b" * 40,
        content_hash="schema-a",
        build=build,
        force=True,
    )

    assert first.handle == second.handle
    assert first.built is True
    assert second.built is True
    assert calls == 2
    conn = second.handle.open_readonly()
    try:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "build-2"
    finally:
        conn.close()


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


def test_build_error_preserves_structured_diagnostics_and_cleans_temp_store(tmp_path):
    manager = DerivedStoreManager(tmp_path / "derived")
    diagnostic = DerivedStoreBuildDiagnostic(
        code="missing_source",
        message="source row missing",
        projection="pages",
        details={"id": "intro"},
    )

    def build(path: Path) -> None:
        _build_sqlite(path)
        raise DerivedStoreBuildError("projection build failed", (diagnostic,))

    with pytest.raises(DerivedStoreBuildError) as error:
        manager.materialize(
            projection_id="library.search",
            source_commit="f" * 40,
            content_hash="schema-a",
            build=build,
        )

    assert error.value.diagnostics == (diagnostic,)
    assert diagnostic.material()["details"] == {"id": "intro"}
    assert not list((tmp_path / "derived").glob("tmp/*"))


def test_materialize_sqlite_file_publishes_hash_and_skips_matching_output(tmp_path):
    output = tmp_path / "library.sqlite"
    calls = 0

    def build(path: Path) -> None:
        nonlocal calls
        calls += 1
        _build_sqlite(path, value=f"build-{calls}")

    first = materialize_sqlite_file(
        output,
        content_hash="content-a",
        build=build,
    )
    second = materialize_sqlite_file(
        output,
        content_hash="content-a",
        build=build,
    )

    assert first.built is True
    assert second.built is False
    assert calls == 1
    assert output.with_suffix(".hash").read_text() == "content-a"
    conn = sqlite3.connect(output)
    try:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "build-1"
    finally:
        conn.close()


def test_materialize_sqlite_file_preserves_existing_output_on_publish_failure(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "library.sqlite"
    materialize_sqlite_file(
        output,
        content_hash="content-a",
        build=lambda path: _build_sqlite(path, value="original"),
    )
    original_replace = Path.replace

    def fail_output_replace(self: Path, target: Path | str) -> Path:
        if Path(target) == output:
            raise RuntimeError("replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_output_replace)

    with pytest.raises(RuntimeError, match="replace failed"):
        materialize_sqlite_file(
            output,
            content_hash="content-b",
            build=lambda path: _build_sqlite(path, value="replacement"),
        )

    assert output.with_suffix(".hash").read_text() == "content-a"
    conn = sqlite3.connect(output)
    try:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "original"
    finally:
        conn.close()


def test_materialize_sqlite_file_can_publish_failed_build_when_output_missing(
    tmp_path,
):
    output = tmp_path / "library.sqlite"

    def build(path: Path) -> None:
        _build_sqlite(path, value="diagnostic")
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        materialize_sqlite_file(
            output,
            content_hash="content-a",
            build=build,
            publish_failure_when_missing=True,
        )

    assert output.exists()
    assert not output.with_suffix(".hash").exists()
    conn = sqlite3.connect(output)
    try:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "diagnostic"
    finally:
        conn.close()


def test_digest_directory_and_dependency_pins_are_generic(tmp_path):
    source = tmp_path / "schema"
    source.mkdir()
    (source / "b.txt").write_text("second")
    nested = source / "nested"
    nested.mkdir()
    (nested / "a.txt").write_text("first")
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(
        """
[[package]]
name = "alpha"
version = "1.2.3"
source = { registry = "https://example.invalid/simple" }

[[package]]
name = "beta"
version = "4.5.6"
""".strip()
    )

    first = digest_directory(source)
    second = digest_directory(source)
    pins = read_dependency_pins(lock_path, ("beta", "alpha"))

    assert first == second
    assert len(first) == 64
    assert pins == {
        "alpha": "1.2.3|{'registry': 'https://example.invalid/simple'}",
        "beta": "4.5.6|None",
    }


def test_derived_store_content_hash_and_projection_step_ordering_are_generic():
    first = derived_store_content_hash(
        projection_version="v1",
        schema_hash="schema",
        dependencies={"sqlite": "3"},
        extra_inputs={"families": ("pages", "notes")},
    )
    second = derived_store_content_hash(
        projection_version="v1",
        schema_hash="schema",
        dependencies={"sqlite": "3"},
        extra_inputs={"families": ("pages", "notes")},
    )
    ordered = order_projection_steps(
        (
            ProjectionBuildStep("search", depends_on=("pages",)),
            ProjectionBuildStep("pages"),
            ProjectionBuildStep("vectors", depends_on=("pages",)),
        )
    )

    assert first == second
    assert tuple(step.name for step in ordered) == ("pages", "search", "vectors")
    with pytest.raises(ValueError, match="cycle"):
        order_projection_steps(
            (
                ProjectionBuildStep("a", depends_on=("b",)),
                ProjectionBuildStep("b", depends_on=("a",)),
            )
        )


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


def test_projection_tables_insert_dataclass_rows():
    @dataclass(frozen=True)
    class PageProjectionRow:
        id: str
        title: str
        metadata_json: dict[str, int]

    conn = sqlite3.connect(":memory:")
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
        schema = create_projection_schema(pages)
        schema.create_all(conn)

        pages.insert_rows(
            conn,
            (
                PageProjectionRow("intro", "Introduction", {"rank": 1}),
                PageProjectionRow("api", "API", {"rank": 2}),
            ),
        )

        rows = pages.select_all(conn)
        assert [row.values["id"] for row in rows] == ["intro", "api"]
        assert rows[0].values["metadata_json"] == {"rank": 1}
    finally:
        conn.close()


def test_projection_tables_insert_generic_projection_rows():
    conn = sqlite3.connect(":memory:")
    try:
        pages = ProjectionTable(
            name="pages",
            columns=(
                ProjectionColumn("id", "TEXT", nullable=False),
                ProjectionColumn("title", "TEXT", nullable=False),
            ),
            primary_key=("id",),
        )
        schema = create_projection_schema(pages)
        schema.create_all(conn)

        pages.insert_row(
            conn,
            pages.row(id="intro", title="Introduction"),
        )

        rows = pages.select_all(conn)
        assert rows[0].values == {"id": "intro", "title": "Introduction"}
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


def test_fts_projection_materializes_and_queries_search_rows():
    conn = sqlite3.connect(":memory:")
    try:
        pages = ProjectionTable(
            name="pages",
            columns=(
                ProjectionColumn("id", "TEXT", nullable=False),
                ProjectionColumn("title", "TEXT", nullable=False),
                ProjectionColumn("body", "TEXT", nullable=False),
            ),
            primary_key=("id",),
        )
        page_search = FtsProjection(
            table="page_search",
            key_column="id",
            columns=("title", "body"),
            source_query='SELECT id, title, body FROM "pages"',
        )
        schema = create_projection_schema(pages, page_search)

        schema.create_all(conn)
        pages.insert_row(
            conn,
            {
                "id": "intro",
                "title": "Introduction",
                "body": "A quiet tour through the system.",
            },
        )
        page_search.populate_from_source_query(conn)
        conn.commit()

        rows = conn.execute(
            page_search.match_sql(("id", "title"), query_param="query"),
            {"query": "quiet"},
        ).fetchall()

        assert rows == [("intro", "Introduction")]
        assert page_search.population_sql() == (
            'INSERT INTO "page_search" ("id", "title", "body") '
            'SELECT id, title, body FROM "pages"'
        )
        with pytest.raises(ValueError, match="does not declare search column"):
            page_search.match_sql(("missing",))
    finally:
        conn.close()


def test_fts_projection_supports_direct_row_insert():
    conn = sqlite3.connect(":memory:")
    try:
        page_search = FtsProjection(
            table="page_search",
            key_column="id",
            columns=("title", "body"),
            row_plan="caller supplied page rows",
        )
        schema = create_projection_schema(page_search)
        schema.create_all(conn)

        page_search.insert_row(
            conn,
            page_search.row(
                id="api",
                title="API",
                body="Generated projection APIs",
            ),
        )

        rows = conn.execute(
            page_search.match_sql(("id",), query_param="query"),
            {"query": "projection"},
        ).fetchall()
        assert rows == [("api",)]
    finally:
        conn.close()


def test_vec_projection_materializes_rowids_and_searches_vectors():
    sqlite_vec = pytest.importorskip("sqlite_vec")
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    try:
        vectors = VecProjection(
            table="page_vec_{model}",
            key_column=None,
            vector_column=ProjectionColumn("embedding", "float[{dimensions}]", nullable=False),
        )
        schema = create_projection_schema(vectors)
        bindings = {"model": "small", "dimensions": "3"}

        schema.create_all(conn, bindings=bindings)
        schema.validate_connection(conn, bindings=bindings)
        vectors.insert_rowid(
            conn,
            {"embedding": _serialize_float32([0.1, 0.2, 0.3])},
            rowid=1,
            bindings=bindings,
        )
        vectors.insert_rowid(
            conn,
            {"embedding": _serialize_float32([0.9, 0.9, 0.9])},
            rowid=2,
            bindings=bindings,
        )

        rows = conn.execute(
            vectors.search_sql(bindings=bindings),
            {"query_vector": _serialize_float32([0.1, 0.2, 0.31]), "k": 1},
        ).fetchall()

        assert rows[0][0] == 1
        assert vectors.insert_rowid_sql(bindings) == (
            'INSERT INTO "page_vec_small" '
            '(rowid, "embedding") VALUES (:rowid, :embedding)'
        )
        vectors.delete_rowid(conn, rowid=1, bindings=bindings)
        assert conn.execute('SELECT rowid FROM "page_vec_small" WHERE rowid = 1').fetchone() is None
    finally:
        conn.close()


def test_sqlite_vec_store_spec_uses_projection_primitives_for_status_and_names():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        status = embedding_status_projection(
            name="page_embedding_status",
            entity_id_column="page_id",
            index_name="idx_page_embedding_status_model",
        )
        spec = VecEntityStoreSpec(
            name="pages",
            status_projection=status,
            status_id_column="page_id",
            vector_projection=rowid_vec_projection("page_vec_{model_identity_hash}"),
            source_table="page",
        )
        store = SqliteVecEntityStore(conn, spec)

        for statement in status.ddl_statements():
            conn.execute(statement)

        assert status.insert_sql(or_replace=True) == (
            'INSERT OR REPLACE INTO "page_embedding_status" '
            '("model_identity_hash", "page_id", "content_hash", "embedded_at") '
            "VALUES (:model_identity_hash, :page_id, :content_hash, :embedded_at)"
        )
        assert spec.vector_projection.projection_name(
            {"model_identity_hash": "abc123", "dimensions": "3"}
        ) == "page_vec_abc123"
        assert store.existing_content_hashes(
            type(
                "Identity",
                (),
                {"identity_hash": "missing"},
            )()
        ) == {}
    finally:
        conn.close()

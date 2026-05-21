from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from quire.derived_store import (
    DerivedStoreBuildDiagnostic,
    DerivedStoreBuildError,
    DerivedStoreManager,
    ProjectionBuildStep,
    derived_store_content_hash,
    digest_directory,
    materialize_sqlite_file,
    order_projection_steps,
    read_dependency_pins,
)
from quire.derived_runtime import (
    DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
    connect_sqlite_store,
    connect_sqlite_store_readonly,
    read_derived_store_schema_version,
    write_derived_store_schema_metadata,
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


def test_sqlite_runtime_policy_metadata_and_readonly_access(tmp_path):
    db_path = tmp_path / "runtime.sqlite"
    conn = connect_sqlite_store(db_path)
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= DEFAULT_SQLITE_BUSY_TIMEOUT_MS
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.execute(
            """
            CREATE TABLE pages (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL
            )
            """
        )
        write_derived_store_schema_metadata(conn, schema_version=7, key="library")
        conn.execute(
            "INSERT INTO pages (id, title) VALUES (?, ?)",
            ("intro", "Introduction"),
        )
        conn.commit()
    finally:
        conn.close()

    readonly = connect_sqlite_store_readonly(db_path)
    try:
        assert readonly.execute("PRAGMA query_only").fetchone()[0] == 1
        assert read_derived_store_schema_version(readonly, key="library") == 7
        assert readonly.execute("SELECT title FROM pages").fetchone()[0] == "Introduction"
        with pytest.raises(sqlite3.OperationalError):
            readonly.execute("INSERT INTO pages (id, title) VALUES ('x', 'X')")
    finally:
        readonly.close()

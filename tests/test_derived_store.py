from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from quire.derived_store import DerivedStoreManager


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

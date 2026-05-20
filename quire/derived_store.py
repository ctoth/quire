from __future__ import annotations

import os
import sqlite3
import tempfile
import tomllib
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from quire.hashing import canonical_json_sha256
from quire.derived_runtime import connect_sqlite_store_readonly

__all__ = [
    "DerivedStoreBuildDiagnostic",
    "DerivedStoreBuildError",
    "DerivedStoreBuilder",
    "DerivedStoreGcReport",
    "DerivedStoreHandle",
    "DerivedStoreManager",
    "DerivedStoreMaterialization",
    "ProjectionBuildStep",
    "SqliteFileMaterialization",
    "checkpoint_and_close_sqlite",
    "derived_store_content_hash",
    "digest_directory",
    "materialize_sqlite_file",
    "order_projection_steps",
    "read_dependency_pins",
]


DerivedStoreBuilder = Callable[[Path], None]


@dataclass(frozen=True)
class DerivedStoreBuildDiagnostic:
    code: str
    message: str
    severity: str = "error"
    projection: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def material(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "projection": self.projection,
            "details": dict(self.details),
        }


class DerivedStoreBuildError(RuntimeError):
    def __init__(
        self,
        message: str,
        diagnostics: Iterable[DerivedStoreBuildDiagnostic] = (),
    ) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)


@dataclass(frozen=True)
class ProjectionBuildStep:
    name: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class DerivedStoreHandle:
    projection_id: str
    source_commit: str
    content_hash: str
    cache_key: str
    path: Path

    def open_readonly(self) -> sqlite3.Connection:
        return connect_sqlite_store_readonly(self.path)

    def readonly_session(self, schema: Any) -> Any:
        from quire.sqlalchemy_store import readonly_session

        return readonly_session(self.path, schema)

    def writable_session(self, schema: Any) -> Any:
        from quire.sqlalchemy_store import writable_session

        return writable_session(self.path, schema)


@dataclass(frozen=True)
class DerivedStoreGcReport:
    deleted_paths: tuple[Path, ...] = ()
    deleted_temp_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class DerivedStoreMaterialization:
    handle: DerivedStoreHandle
    built: bool


@dataclass(frozen=True)
class SqliteFileMaterialization:
    path: Path
    built: bool


class DerivedStoreManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def materialize(
        self,
        *,
        projection_id: str,
        source_commit: str,
        content_hash: str,
        build: DerivedStoreBuilder,
        force: bool = False,
    ) -> DerivedStoreHandle:
        return self.materialize_with_report(
            projection_id=projection_id,
            source_commit=source_commit,
            content_hash=content_hash,
            build=build,
            force=force,
        ).handle

    def materialize_with_report(
        self,
        *,
        projection_id: str,
        source_commit: str,
        content_hash: str,
        build: DerivedStoreBuilder,
        force: bool = False,
    ) -> DerivedStoreMaterialization:
        cache_key = self.cache_key(
            projection_id=projection_id,
            source_commit=source_commit,
            content_hash=content_hash,
        )
        final_path = self.path_for_cache_key(projection_id, cache_key)
        handle = DerivedStoreHandle(
            projection_id=projection_id,
            source_commit=source_commit,
            content_hash=content_hash,
            cache_key=cache_key,
            path=final_path,
        )
        if not force and final_path.is_file():
            return DerivedStoreMaterialization(handle=handle, built=False)

        lock_path = self._lock_path(cache_key)
        with _exclusive_file_lock(lock_path):
            if not force and final_path.is_file():
                return DerivedStoreMaterialization(handle=handle, built=False)
            if force:
                _delete_file_family(final_path)

            temp_path = self._temp_path(projection_id, cache_key)
            try:
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                build(temp_path)
                if not temp_path.is_file():
                    raise FileNotFoundError(f"derived-store builder did not create {temp_path}")
                final_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temp_path, final_path)
                return DerivedStoreMaterialization(handle=handle, built=True)
            except Exception:
                _delete_file_family(temp_path)
                raise
            finally:
                _delete_file_family(temp_path)

    def open_readonly(self, handle: DerivedStoreHandle) -> sqlite3.Connection:
        return handle.open_readonly()

    def gc(
        self,
        *,
        keep_cache_keys: Iterable[str] = (),
    ) -> DerivedStoreGcReport:
        keep = {_path_segment(cache_key) for cache_key in keep_cache_keys}
        deleted: list[Path] = []
        deleted_temp: list[Path] = []
        stores_root = self.root / "stores"
        if stores_root.exists():
            for path in sorted(stores_root.glob("*/*.sqlite")):
                cache_key = path.stem
                if cache_key in keep:
                    continue
                _delete_file_family(path)
                deleted.append(path)
        temp_root = self.root / "tmp"
        if temp_root.exists():
            for path in sorted(temp_root.glob("*.sqlite")):
                _delete_file_family(path)
                deleted_temp.append(path)
        return DerivedStoreGcReport(
            deleted_paths=tuple(deleted),
            deleted_temp_paths=tuple(deleted_temp),
        )

    def cache_key(
        self,
        *,
        projection_id: str,
        source_commit: str,
        content_hash: str,
    ) -> str:
        return canonical_json_sha256(
            {
                "projection_id": projection_id,
                "source_commit": source_commit,
                "content_hash": content_hash,
            }
        )

    def path_for_cache_key(self, projection_id: str, cache_key: str) -> Path:
        return (
            self.root
            / "stores"
            / _path_segment(projection_id)
            / f"{_path_segment(cache_key)}.sqlite"
        )

    def _lock_path(self, cache_key: str) -> Path:
        return self.root / "locks" / f"{_path_segment(cache_key)}.lock"

    def _temp_path(self, projection_id: str, cache_key: str) -> Path:
        unique = uuid.uuid4().hex
        return (
            self.root
            / "tmp"
            / f"{_path_segment(projection_id)}-{_path_segment(cache_key)}-{unique}.sqlite"
        )


def derived_store_content_hash(
    *,
    projection_version: str,
    schema_hash: str,
    dependencies: Mapping[str, str] | None = None,
    extra_inputs: Mapping[str, Any] | None = None,
) -> str:
    return canonical_json_sha256(
        {
            "projection_version": projection_version,
            "schema_hash": schema_hash,
            "dependencies": {} if dependencies is None else dict(dependencies),
            "extra_inputs": {} if extra_inputs is None else dict(extra_inputs),
        }
    )


def digest_directory(path: Path) -> str:
    digest = sha256()
    if not path.exists():
        return ""
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def read_dependency_pins(
    lock_path: Path,
    dependency_names: Iterable[str],
) -> dict[str, str]:
    if not lock_path.exists():
        return {}
    selected = set(dependency_names)
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    pins: dict[str, str] = {}
    for package in lock.get("package", ()):
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        if name not in selected:
            continue
        version = str(package.get("version") or "")
        source = package.get("source")
        pins[str(name)] = f"{version}|{source!r}"
    return dict(sorted(pins.items()))


def checkpoint_and_close_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def materialize_sqlite_file(
    path: Path,
    *,
    content_hash: str | None,
    build: DerivedStoreBuilder,
    force: bool = False,
    publish_failure_when_missing: bool = False,
) -> SqliteFileMaterialization:
    if not force and content_hash is not None and path.exists():
        hash_path = path.with_suffix(".hash")
        if hash_path.exists() and hash_path.read_text().strip() == content_hash:
            return SqliteFileMaterialization(path=path, built=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _exclusive_file_lock(lock_path):
        if not force and content_hash is not None and path.exists():
            hash_path = path.with_suffix(".hash")
            if hash_path.exists() and hash_path.read_text().strip() == content_hash:
                return SqliteFileMaterialization(path=path, built=False)
        had_existing_file = path.exists()
        temp_path = _sibling_temp_path(path)
        temp_hash_path = temp_path.with_name(f"{temp_path.name}.hash")
        try:
            build(temp_path)
            if not temp_path.is_file():
                raise FileNotFoundError(f"SQLite builder did not create {temp_path}")
        except Exception:
            if publish_failure_when_missing and not had_existing_file and temp_path.exists():
                temp_path.replace(path)
            _delete_file_family(temp_path)
            temp_hash_path.unlink(missing_ok=True)
            raise
        if content_hash is not None:
            temp_hash_path.write_text(content_hash)
        try:
            temp_path.replace(path)
            if content_hash is not None:
                temp_hash_path.replace(path.with_suffix(".hash"))
        except Exception:
            _delete_file_family(temp_path)
            temp_hash_path.unlink(missing_ok=True)
            raise
        finally:
            _delete_file_family(temp_path)
            temp_hash_path.unlink(missing_ok=True)
    return SqliteFileMaterialization(path=path, built=True)


def order_projection_steps(
    steps: Iterable[ProjectionBuildStep],
) -> tuple[ProjectionBuildStep, ...]:
    by_name: dict[str, ProjectionBuildStep] = {}
    for step in steps:
        if step.name in by_name:
            raise ValueError(f"duplicate projection build step {step.name!r}")
        by_name[step.name] = step

    ordered: list[ProjectionBuildStep] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"cycle in projection build steps at {name!r}")
        try:
            step = by_name[name]
        except KeyError:
            raise ValueError(f"unknown projection build step dependency {name!r}") from None
        visiting.add(name)
        for dependency in step.depends_on:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(step)

    for name in by_name:
        visit(name)
    return tuple(ordered)


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _delete_file_family(path: Path) -> None:
    for candidate in (path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")):
        if candidate.exists():
            candidate.unlink()
    if path.parent.name == "tmp":
        _remove_empty_parent(path.parent)


def _remove_empty_parent(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return


def _sibling_temp_path(path: Path) -> Path:
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(fd)
    temp_path = Path(temp_name)
    temp_path.unlink()
    return temp_path


def _path_segment(value: str) -> str:
    encoded = []
    for char in value:
        if char.isalnum() or char in ("-", "_", "."):
            encoded.append(char)
        else:
            encoded.append(f"_{ord(char):02x}")
    segment = "".join(encoded).strip(".")
    return segment or "derived"

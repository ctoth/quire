from __future__ import annotations

import tempfile
from pathlib import Path

import msgspec
import pytest

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.versions import VersionId


BENCHMARK_VERSION = VersionId("2026.04.25", allow_placeholder=False)
SMALL_COMMIT_COUNT = 200
TRANSACTION_SAVE_COUNT = 1000
SEEDED_LOAD_COUNT = 1000
LOAD_OPERATION_COUNT = 5000
SCAN_DOC_COUNT = 1000


class Owner:
    branch = "master"


class DemoDoc(msgspec.Struct):
    name: str
    value: int


def _make_backend(kind: str) -> tuple[GitStore, tempfile.TemporaryDirectory[str] | None]:
    if kind == "memory":
        return GitStore.init_memory(), None
    if kind == "filesystem":
        temp_dir = tempfile.TemporaryDirectory()
        return GitStore.init(Path(temp_dir.name) / "repo"), temp_dir
    raise ValueError(f"unknown backend kind: {kind}")


def _family() -> ArtifactFamily[Owner, str, DemoDoc]:
    return ArtifactFamily(
        name="demo",
        contract_version=BENCHMARK_VERSION,
        doc_type=DemoDoc,
        placement=FlatYamlPlacement("demo", str),
    )


def _seed_family(
    store: DocumentFamilyStore[Owner],
    family: ArtifactFamily[Owner, str, DemoDoc],
    count: int,
) -> None:
    with store.transact(message=f"seed {count} docs") as transaction:
        for index in range(count):
            ref = f"doc-{index:05d}"
            transaction.save(family, ref, DemoDoc(name=ref, value=index))


@pytest.mark.benchmark(group="gitstore_small_commits")
@pytest.mark.parametrize("backend_kind", ["memory", "filesystem"], ids=["memory", "filesystem"])
def test_benchmark_gitstore_small_commits(benchmark, backend_kind: str):
    def run() -> str:
        store, temp_dir = _make_backend(backend_kind)
        try:
            commit = ""
            for index in range(SMALL_COMMIT_COUNT):
                commit = store.commit_files(
                    {f"docs/doc-{index:05d}.yaml": f"name: doc-{index}\n".encode("utf-8")},
                    f"commit {index}",
                )
            return commit
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    commit = benchmark(run)
    assert len(commit) == 40


@pytest.mark.benchmark(group="family_transaction")
@pytest.mark.parametrize("backend_kind", ["memory", "filesystem"], ids=["memory", "filesystem"])
def test_benchmark_family_transaction(benchmark, backend_kind: str):
    def run() -> int:
        backend, temp_dir = _make_backend(backend_kind)
        try:
            family = _family()
            store = DocumentFamilyStore(owner=Owner(), backend=backend)
            _seed_family(store, family, TRANSACTION_SAVE_COUNT)
            return len(list(store.iter(family)))
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    count = benchmark(run)
    assert count == TRANSACTION_SAVE_COUNT


@pytest.mark.benchmark(group="family_loads")
@pytest.mark.parametrize("backend_kind", ["memory", "filesystem"], ids=["memory", "filesystem"])
def test_benchmark_family_loads(benchmark, backend_kind: str):
    def run() -> DemoDoc | None:
        backend, temp_dir = _make_backend(backend_kind)
        try:
            family = _family()
            store = DocumentFamilyStore(owner=Owner(), backend=backend)
            _seed_family(store, family, SEEDED_LOAD_COUNT)
            loaded = None
            for index in range(LOAD_OPERATION_COUNT):
                ref = f"doc-{index % SEEDED_LOAD_COUNT:05d}"
                loaded = store.load(family, ref)
            return loaded
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    loaded = benchmark(run)
    assert loaded == DemoDoc(name=f"doc-{(LOAD_OPERATION_COUNT - 1) % SEEDED_LOAD_COUNT:05d}", value=(LOAD_OPERATION_COUNT - 1) % SEEDED_LOAD_COUNT)


@pytest.mark.benchmark(group="family_scan")
@pytest.mark.parametrize("backend_kind", ["memory", "filesystem"], ids=["memory", "filesystem"])
def test_benchmark_family_scan_iter_and_require(benchmark, backend_kind: str):
    def run() -> int:
        backend, temp_dir = _make_backend(backend_kind)
        try:
            family = _family()
            store = DocumentFamilyStore(owner=Owner(), backend=backend)
            _seed_family(store, family, SCAN_DOC_COUNT)
            total = 0
            for ref in store.iter(family):
                total += store.require(family, ref).value
            return total
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    total = benchmark(run)
    assert total == sum(range(SCAN_DOC_COUNT))


@pytest.mark.benchmark(group="family_scan")
@pytest.mark.parametrize("backend_kind", ["memory", "filesystem"], ids=["memory", "filesystem"])
def test_benchmark_family_scan_iter_handles(benchmark, backend_kind: str):
    def run() -> int:
        backend, temp_dir = _make_backend(backend_kind)
        try:
            family = _family()
            store = DocumentFamilyStore(owner=Owner(), backend=backend)
            _seed_family(store, family, SCAN_DOC_COUNT)
            total = 0
            for handle in store.iter_handles(family):
                total += handle.document.value
            return total
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    total = benchmark(run)
    assert total == sum(range(SCAN_DOC_COUNT))


@pytest.mark.benchmark(group="family_scan")
@pytest.mark.parametrize("backend_kind", ["memory", "filesystem"], ids=["memory", "filesystem"])
def test_benchmark_family_scan_pinned_iter_and_require(benchmark, backend_kind: str):
    def run() -> int:
        backend, temp_dir = _make_backend(backend_kind)
        try:
            family = _family()
            store = DocumentFamilyStore(owner=Owner(), backend=backend)
            _seed_family(store, family, SCAN_DOC_COUNT)
            pinned_branch, pinned_commit = store.pin(family)
            total = 0
            for ref in store.iter(family, branch=pinned_branch, commit=pinned_commit):
                total += store.require(family, ref, branch=pinned_branch, commit=pinned_commit).value
            return total
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    total = benchmark(run)
    assert total == sum(range(SCAN_DOC_COUNT))

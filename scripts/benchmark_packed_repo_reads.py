from __future__ import annotations

import shutil
import statistics
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, cast

import msgspec

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.versions import VersionId


PROFILE_VERSION = VersionId("2026.04.25", allow_placeholder=False)
REPEATED_SEED_COUNT = 1000
REPEATED_LOAD_COUNT = 5000
UNIQUE_LOAD_COUNT = 5000
SCAN_DOC_COUNT = 1000
ROUNDS = 5


class Owner:
    branch = "master"


class DemoDoc(msgspec.Struct):
    name: str
    value: int


@dataclass(frozen=True)
class Measurement:
    name: str
    samples: tuple[float, ...]

    @property
    def median(self) -> float:
        return statistics.median(self.samples)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.samples)


def make_family() -> ArtifactFamily[Owner, str, DemoDoc]:
    return ArtifactFamily(
        name="demo",
        contract_version=PROFILE_VERSION,
        doc_type=DemoDoc,
        placement=FlatYamlPlacement("demo", str),
    )


def close_backend_repo(backend: object) -> None:
    cast(Any, backend).raw_repo.close()


def seed_family(
    root: Path,
    *,
    count: int,
) -> str:
    backend = GitStore.init(root)
    try:
        family = make_family()
        store = DocumentFamilyStore(owner=Owner(), backend=backend)
        with store.transact(message=f"seed {count} docs") as transaction:
            for index in range(count):
                ref = f"doc-{index:05d}"
                transaction.save(family, ref, DemoDoc(name=ref, value=index))
        commit = backend.branch_sha("master")
        if commit is None:
            raise RuntimeError("seed did not create a master head")
        return commit
    finally:
        cast(Any, backend.raw_repo).close()


def count_loose_objects(root: Path) -> int:
    backend = GitStore.open(root)
    try:
        object_store = cast(Any, backend.raw_repo.object_store)
        return sum(1 for _ in object_store._iter_loose_objects())
    finally:
        cast(Any, backend.raw_repo).close()


def count_pack_files(root: Path) -> int:
    pack_dir = root / ".git" / "objects" / "pack"
    if not pack_dir.is_dir():
        return 0
    return len(list(pack_dir.glob("*.pack")))


def pack_repo(root: Path) -> int:
    backend = GitStore.open(root)
    try:
        object_store = cast(Any, backend.raw_repo.object_store)
        packed = object_store.pack_loose_objects()
        object_store._update_pack_cache()
        return packed
    finally:
        cast(Any, backend.raw_repo).close()


def open_store(root: Path) -> tuple[DocumentFamilyStore[Owner], ArtifactFamily[Owner, str, DemoDoc]]:
    backend = GitStore.open(root)
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    return store, make_family()


def repeated_point_loads(root: Path) -> None:
    store, family = open_store(root)
    try:
        pinned_branch, pinned_commit = store.pin(family)
        loaded = None
        for index in range(REPEATED_LOAD_COUNT):
            ref = f"doc-{index % REPEATED_SEED_COUNT:05d}"
            loaded = store.load(family, ref, branch=pinned_branch, commit=pinned_commit)
        expected = DemoDoc(
            name=f"doc-{(REPEATED_LOAD_COUNT - 1) % REPEATED_SEED_COUNT:05d}",
            value=(REPEATED_LOAD_COUNT - 1) % REPEATED_SEED_COUNT,
        )
        if loaded != expected:
            raise RuntimeError(f"unexpected repeated-load result: {loaded!r}")
    finally:
        close_backend_repo(store._require_backend())


def unique_point_loads(root: Path) -> None:
    store, family = open_store(root)
    try:
        pinned_branch, pinned_commit = store.pin(family)
        loaded = None
        for index in range(UNIQUE_LOAD_COUNT):
            ref = f"doc-{index:05d}"
            loaded = store.load(family, ref, branch=pinned_branch, commit=pinned_commit)
        expected = DemoDoc(name=f"doc-{UNIQUE_LOAD_COUNT - 1:05d}", value=UNIQUE_LOAD_COUNT - 1)
        if loaded != expected:
            raise RuntimeError(f"unexpected unique-load result: {loaded!r}")
    finally:
        close_backend_repo(store._require_backend())


def family_scan_iter_handles(root: Path) -> None:
    store, family = open_store(root)
    try:
        total = 0
        for handle in store.iter_handles(family):
            total += handle.document.value
        expected = sum(range(SCAN_DOC_COUNT))
        if total != expected:
            raise RuntimeError(f"unexpected iter_handles total: {total}")
    finally:
        close_backend_repo(store._require_backend())


def family_scan_pinned_iter_and_require(root: Path) -> None:
    store, family = open_store(root)
    try:
        pinned = store.pin(family)
        total = 0
        for ref in store.iter(family, branch=pinned[0], commit=pinned[1]):
            total += store.require(family, ref, branch=pinned[0], commit=pinned[1]).value
        expected = sum(range(SCAN_DOC_COUNT))
        if total != expected:
            raise RuntimeError(f"unexpected pinned scan total: {total}")
    finally:
        close_backend_repo(store._require_backend())


def measure(name: str, root: Path, workload: Callable[[Path], None]) -> Measurement:
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = perf_counter()
        workload(root)
        samples.append(perf_counter() - started)
    return Measurement(name=name, samples=tuple(samples))


def print_measurement(label: str, measurement: Measurement, *, ops: int) -> None:
    print(
        f"{label} {measurement.name}: "
        f"median={measurement.median:.4f}s mean={measurement.mean:.4f}s "
        f"ms_per_op={(measurement.median / ops) * 1000.0:.4f} "
        f"samples={[round(sample, 4) for sample in measurement.samples]}"
    )


def main() -> None:
    workloads = (
        ("repeated_point_loads", REPEATED_LOAD_COUNT, repeated_point_loads, REPEATED_SEED_COUNT),
        ("unique_point_loads", UNIQUE_LOAD_COUNT, unique_point_loads, UNIQUE_LOAD_COUNT),
        ("family_scan_iter_handles", SCAN_DOC_COUNT, family_scan_iter_handles, SCAN_DOC_COUNT),
        ("family_scan_pinned_iter_and_require", SCAN_DOC_COUNT, family_scan_pinned_iter_and_require, SCAN_DOC_COUNT),
    )

    temp_root = Path(tempfile.mkdtemp(prefix="quire-packed-bench-"))
    try:
        for workload_name, ops, workload, seed_count in workloads:
            root = temp_root / workload_name
            seed_family(root, count=seed_count)
            loose_before = count_loose_objects(root)
            pack_files_before = count_pack_files(root)
            loose_measurement = measure(workload_name, root, workload)

            packed_objects = pack_repo(root)
            loose_after = count_loose_objects(root)
            pack_files_after = count_pack_files(root)
            packed_measurement = measure(workload_name, root, workload)

            print(
                f"{workload_name}: "
                f"packed_objects={packed_objects} "
                f"loose_before={loose_before} loose_after={loose_after} "
                f"packs_before={pack_files_before} packs_after={pack_files_after}"
            )
            print_measurement("loose", loose_measurement, ops=ops)
            print_measurement("packed", packed_measurement, ops=ops)
            print(
                f"speedup {workload_name}: "
                f"{loose_measurement.median / packed_measurement.median:.2f}x"
            )
            print()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()

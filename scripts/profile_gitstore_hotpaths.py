from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
import tempfile

import msgspec

import quire.git_store as git_store_module
from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.versions import VersionId


PROFILE_VERSION = VersionId("2026.04.25", allow_placeholder=False)
LOAD_SEED_COUNT = 1000
LOAD_OPERATION_COUNT = 5000
COMMIT_COUNT = 200


class Owner:
    branch = "master"


class DemoDoc(msgspec.Struct):
    name: str
    value: int


@dataclass
class Stat:
    calls: int = 0
    seconds: float = 0.0


class CallStats:
    def __init__(self) -> None:
        self._stats: dict[str, Stat] = {}

    def record(self, name: str, elapsed: float) -> None:
        stat = self._stats.setdefault(name, Stat())
        stat.calls += 1
        stat.seconds += elapsed

    def items(self) -> Iterator[tuple[str, Stat]]:
        yield from self._stats.items()


def make_family() -> ArtifactFamily[Owner, str, DemoDoc]:
    return ArtifactFamily(
        name="demo",
        contract_version=PROFILE_VERSION,
        doc_type=DemoDoc,
        placement=FlatYamlPlacement("demo", str),
    )


def make_filesystem_store() -> tuple[GitStore, tempfile.TemporaryDirectory[str]]:
    temp_dir = tempfile.TemporaryDirectory()
    return GitStore.init(Path(temp_dir.name) / "repo"), temp_dir


def seed_family(
    store: DocumentFamilyStore[Owner],
    family: ArtifactFamily[Owner, str, DemoDoc],
    count: int,
) -> None:
    with store.transact(message=f"seed {count} docs") as transaction:
        for index in range(count):
            ref = f"doc-{index:05d}"
            transaction.save(family, ref, DemoDoc(name=ref, value=index))


@contextmanager
def instrument_gitstore(stats: CallStats) -> Iterator[None]:
    originals: list[tuple[object, str, Callable[..., object]]] = []

    def wrap(target: object, name: str) -> None:
        original = getattr(target, name)

        def wrapped(*args, **kwargs):
            started = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                stats.record(name, perf_counter() - started)

        originals.append((target, name, original))
        setattr(target, name, wrapped)

    wrap(GitStore, "_get_tree")
    wrap(GitStore, "_walk_tree")
    wrap(GitStore, "_apply_tree_changes")
    wrap(git_store_module, "_repo_object")
    try:
        yield
    finally:
        for target, name, original in reversed(originals):
            setattr(target, name, original)


def run_family_load_profile() -> None:
    backend, temp_dir = make_filesystem_store()
    try:
        family = make_family()
        store = DocumentFamilyStore(owner=Owner(), backend=backend)
        seed_family(store, family, LOAD_SEED_COUNT)

        stats = CallStats()
        started = perf_counter()
        with instrument_gitstore(stats):
            loaded = None
            for index in range(LOAD_OPERATION_COUNT):
                ref = f"doc-{index % LOAD_SEED_COUNT:05d}"
                loaded = store.load(family, ref)
        elapsed = perf_counter() - started

        print(
            f"family_loads filesystem total={elapsed:.4f}s "
            f"ops={LOAD_OPERATION_COUNT} ms_per_load={(elapsed / LOAD_OPERATION_COUNT) * 1000.0:.4f}"
        )
        if loaded is None:
            raise RuntimeError("load profile did not load any documents")
        print_stats(stats, total_seconds=elapsed)
    finally:
        temp_dir.cleanup()


def run_small_commit_profile() -> None:
    backend, temp_dir = make_filesystem_store()
    try:
        stats = CallStats()
        started = perf_counter()
        with instrument_gitstore(stats):
            commit = ""
            for index in range(COMMIT_COUNT):
                commit = backend.commit_files(
                    {f"docs/doc-{index:05d}.yaml": f"name: doc-{index}\n".encode("utf-8")},
                    f"commit {index}",
                )
        elapsed = perf_counter() - started

        print(
            f"gitstore_small_commits filesystem total={elapsed:.4f}s "
            f"ops={COMMIT_COUNT} ms_per_commit={(elapsed / COMMIT_COUNT) * 1000.0:.4f}"
        )
        if len(commit) != 40:
            raise RuntimeError("commit profile did not produce a commit sha")
        print_stats(stats, total_seconds=elapsed)
    finally:
        temp_dir.cleanup()


def print_stats(stats: CallStats, *, total_seconds: float) -> None:
    print("instrumented breakdown:")
    for name, stat in sorted(stats.items(), key=lambda item: item[1].seconds, reverse=True):
        share = 0.0 if total_seconds == 0.0 else (stat.seconds / total_seconds) * 100.0
        print(
            f"  {name:20} calls={stat.calls:7d} "
            f"total={stat.seconds:9.4f}s share={share:6.2f}% "
            f"avg_ms={(stat.seconds / stat.calls) * 1000.0:8.4f}"
        )
    print()


def main() -> None:
    run_family_load_profile()
    run_small_commit_profile()


if __name__ == "__main__":
    main()

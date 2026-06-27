from __future__ import annotations

import builtins
import cProfile
import inspect
import io
import pstats
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import msgspec

import quire.git_store as git_store_module
from dulwich import objects as dulwich_objects
from dulwich import pack as dulwich_pack
from dulwich.object_store import DiskObjectStore
from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.versions import VersionId


PROFILE_VERSION = VersionId("2026.04.25", allow_placeholder=False)
UNIQUE_LOAD_COUNT = 5000
TOP_CPROFILE_ROWS = 25


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


def describe_callable(name: str, target: object) -> str:
    module = getattr(target, "__module__", type(target).__module__)
    if inspect.isbuiltin(target):
        kind = "builtin/native"
    elif inspect.isfunction(target):
        kind = "python"
    elif inspect.ismethod(target):
        kind = "bound-method"
    else:
        kind = type(target).__name__
    return f"{name}: module={module} kind={kind} repr={target!r}"


@contextmanager
def instrument_calls(stats: CallStats) -> Iterator[None]:
    originals: list[tuple[object, str, object]] = []

    def wrap_function(target: object, name: str) -> None:
        original = getattr(target, name)

        def wrapped(*args, **kwargs):
            started = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                stats.record(name, perf_counter() - started)

        originals.append((target, name, original))
        setattr(target, name, wrapped)

    def wrap_method(target: type[object], name: str) -> None:
        original = getattr(target, name)

        def wrapped(self, *args, **kwargs):
            started = perf_counter()
            try:
                return original(self, *args, **kwargs)
            finally:
                stats.record(name, perf_counter() - started)

        originals.append((target, name, original))
        setattr(target, name, wrapped)

    def wrap_classmethod(target: type[object], name: str) -> None:
        descriptor = target.__dict__[name]
        original = descriptor.__func__

        def wrapped(cls, *args, **kwargs):
            started = perf_counter()
            try:
                return original(cls, *args, **kwargs)
            finally:
                stats.record(name, perf_counter() - started)

        originals.append((target, name, descriptor))
        setattr(target, name, classmethod(wrapped))

    wrap_function(git_store_module, "_repo_object")
    wrap_method(GitStore, "_walk_tree")
    wrap_method(DiskObjectStore, "_get_loose_object")
    wrap_classmethod(dulwich_objects.ShaFile, "from_path")
    wrap_function(dulwich_objects, "_decompress")
    wrap_function(dulwich_objects, "parse_tree")

    original_open = builtins.open

    def wrapped_open(*args, **kwargs):
        started = perf_counter()
        try:
            return original_open(*args, **kwargs)
        finally:
            stats.record("builtins.open", perf_counter() - started)

    originals.append((builtins, "open", original_open))
    builtins.open = wrapped_open

    try:
        yield
    finally:
        for target, name, original in reversed(originals):
            setattr(target, name, original)


def run_unique_family_load_profile() -> tuple[CallStats, str]:
    backend, temp_dir = make_filesystem_store()
    try:
        family = make_family()
        store = DocumentFamilyStore(owner=Owner(), backend=backend)
        seed_family(store, family, UNIQUE_LOAD_COUNT)

        stats = CallStats()
        profiler = cProfile.Profile()
        with instrument_calls(stats):
            profiler.enable()
            loaded = None
            for index in range(UNIQUE_LOAD_COUNT):
                ref = f"doc-{index:05d}"
                loaded = store.load(family, ref)
            profiler.disable()

        if loaded != DemoDoc(name=f"doc-{UNIQUE_LOAD_COUNT - 1:05d}", value=UNIQUE_LOAD_COUNT - 1):
            raise RuntimeError("profile workload did not load the expected final document")

        rendered = io.StringIO()
        profile_stats = pstats.Stats(profiler, stream=rendered)
        profile_stats.sort_stats(pstats.SortKey.CUMULATIVE)
        profile_stats.print_stats(TOP_CPROFILE_ROWS)
        return stats, rendered.getvalue()
    finally:
        temp_dir.cleanup()


def print_runtime_bindings() -> None:
    print("runtime bindings:")
    print(describe_callable("dulwich.objects.parse_tree", dulwich_objects.parse_tree))
    print(describe_callable("dulwich.objects.sorted_tree_items", dulwich_objects.sorted_tree_items))
    print(describe_callable("dulwich.pack.apply_delta", dulwich_pack.apply_delta))
    print(describe_callable("dulwich.pack.bisect_find_sha", dulwich_pack.bisect_find_sha))
    print(describe_callable("dulwich.pack.create_delta", dulwich_pack.create_delta))
    print(describe_callable("dulwich.objects.ShaFile.from_path", dulwich_objects.ShaFile.from_path))
    print()


def print_instrumented_stats(stats: CallStats) -> None:
    total_seconds = sum(stat.seconds for _, stat in stats.items())
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
    print_runtime_bindings()
    started = perf_counter()
    stats, cprofile_output = run_unique_family_load_profile()
    elapsed = perf_counter() - started
    print(
        "unique family loads: "
        f"total={elapsed:.4f}s ops={UNIQUE_LOAD_COUNT} "
        f"ms_per_load={(elapsed / UNIQUE_LOAD_COUNT) * 1000.0:.4f}"
    )
    print()
    print_instrumented_stats(stats)
    print("cProfile top cumulative:")
    print(cprofile_output.rstrip())


if __name__ == "__main__":
    main()

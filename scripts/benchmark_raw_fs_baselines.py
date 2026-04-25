from __future__ import annotations

import statistics
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import msgspec


DOC_COUNT = 5000
REPEATED_SEED_COUNT = 1000
REPEATED_LOAD_COUNT = 5000
UNIQUE_LOAD_COUNT = 5000
ROUNDS = 5


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


def seed_docs(root: Path, *, count: int) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        path = root / f"doc-{index:05d}.yaml"
        path.write_bytes(msgspec.yaml.encode({"name": f"doc-{index:05d}", "value": index}))
        paths.append(path)
    return paths


def repeated_read_bytes(paths: list[Path]) -> None:
    loaded = None
    for index in range(REPEATED_LOAD_COUNT):
        loaded = paths[index % REPEATED_SEED_COUNT].read_bytes()
    expected = msgspec.yaml.encode(
        {
            "name": f"doc-{(REPEATED_LOAD_COUNT - 1) % REPEATED_SEED_COUNT:05d}",
            "value": (REPEATED_LOAD_COUNT - 1) % REPEATED_SEED_COUNT,
        }
    )
    if loaded != expected:
        raise RuntimeError("unexpected repeated raw bytes result")


def repeated_read_and_decode(paths: list[Path]) -> None:
    loaded = None
    for index in range(REPEATED_LOAD_COUNT):
        loaded = msgspec.yaml.decode(paths[index % REPEATED_SEED_COUNT].read_bytes(), type=DemoDoc)
    expected = DemoDoc(
        name=f"doc-{(REPEATED_LOAD_COUNT - 1) % REPEATED_SEED_COUNT:05d}",
        value=(REPEATED_LOAD_COUNT - 1) % REPEATED_SEED_COUNT,
    )
    if loaded != expected:
        raise RuntimeError("unexpected repeated raw decode result")


def unique_read_bytes(paths: list[Path]) -> None:
    loaded = None
    for index in range(UNIQUE_LOAD_COUNT):
        loaded = paths[index].read_bytes()
    expected = msgspec.yaml.encode({"name": f"doc-{UNIQUE_LOAD_COUNT - 1:05d}", "value": UNIQUE_LOAD_COUNT - 1})
    if loaded != expected:
        raise RuntimeError("unexpected unique raw bytes result")


def unique_read_and_decode(paths: list[Path]) -> None:
    loaded = None
    for index in range(UNIQUE_LOAD_COUNT):
        loaded = msgspec.yaml.decode(paths[index].read_bytes(), type=DemoDoc)
    expected = DemoDoc(name=f"doc-{UNIQUE_LOAD_COUNT - 1:05d}", value=UNIQUE_LOAD_COUNT - 1)
    if loaded != expected:
        raise RuntimeError("unexpected unique raw decode result")


def repeated_decode_from_memory(payloads: list[bytes]) -> None:
    loaded = None
    for index in range(REPEATED_LOAD_COUNT):
        loaded = msgspec.yaml.decode(payloads[index % REPEATED_SEED_COUNT], type=DemoDoc)
    expected = DemoDoc(
        name=f"doc-{(REPEATED_LOAD_COUNT - 1) % REPEATED_SEED_COUNT:05d}",
        value=(REPEATED_LOAD_COUNT - 1) % REPEATED_SEED_COUNT,
    )
    if loaded != expected:
        raise RuntimeError("unexpected repeated memory decode result")


def unique_decode_from_memory(payloads: list[bytes]) -> None:
    loaded = None
    for index in range(UNIQUE_LOAD_COUNT):
        loaded = msgspec.yaml.decode(payloads[index], type=DemoDoc)
    expected = DemoDoc(name=f"doc-{UNIQUE_LOAD_COUNT - 1:05d}", value=UNIQUE_LOAD_COUNT - 1)
    if loaded != expected:
        raise RuntimeError("unexpected unique memory decode result")


def measure(name: str, func) -> Measurement:
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = perf_counter()
        func()
        samples.append(perf_counter() - started)
    return Measurement(name=name, samples=tuple(samples))


def print_measurement(measurement: Measurement, *, ops: int) -> None:
    print(
        f"{measurement.name}: "
        f"median={measurement.median:.4f}s mean={measurement.mean:.4f}s "
        f"ms_per_op={(measurement.median / ops) * 1000.0:.4f} "
        f"samples={[round(sample, 4) for sample in measurement.samples]}"
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="quire-raw-fs-bench-") as temp_dir:
        docs_root = Path(temp_dir) / "docs"
        paths = seed_docs(docs_root, count=DOC_COUNT)
        payloads = [path.read_bytes() for path in paths]

        measurements = (
            measure("raw_fs_repeated_read_bytes", lambda: repeated_read_bytes(paths)),
            measure("raw_fs_repeated_read_and_decode", lambda: repeated_read_and_decode(paths)),
            measure("raw_fs_unique_read_bytes", lambda: unique_read_bytes(paths)),
            measure("raw_fs_unique_read_and_decode", lambda: unique_read_and_decode(paths)),
            measure("memory_repeated_decode", lambda: repeated_decode_from_memory(payloads)),
            measure("memory_unique_decode", lambda: unique_decode_from_memory(payloads)),
        )

        for measurement in measurements:
            ops = REPEATED_LOAD_COUNT if "repeated" in measurement.name else UNIQUE_LOAD_COUNT
            print_measurement(measurement, ops=ops)


if __name__ == "__main__":
    main()

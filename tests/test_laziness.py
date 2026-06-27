"""Hypothesis property tests pinning the laziness contract of public ``iter_*`` APIs.

Quire's AGENTS.md mandates iterator-first APIs: enumeration must be lazy. These tests
guard against regressions where a generator is accidentally replaced with eager
materialization (``return list(...)``, ``return tuple(...)`` etc.) by counting how many
git objects are loaded between iterator construction and pulling the first element.

Counted primitive: ``GitStore._cached_object`` — the load hook used by
``GitStore.iter_subtree_files`` (which transitively powers ``DocumentFamilyStore.iter_handles``
via ``FlatYamlPlacement.iter_artifacts``). See ``quire/git_store.py:1413`` and
``quire/git_store.py:518``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import msgspec
from hypothesis import given, settings
from hypothesis import strategies as st

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.families import BoundFamily
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.contracts import contract_version


class _DemoDocument(msgspec.Struct):
    name: str


@dataclass(frozen=True)
class _Owner:
    branch: str = "master"


def _demo_family() -> ArtifactFamily[_Owner, str, _DemoDocument]:
    return ArtifactFamily(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        doc_type=_DemoDocument,
        placement=FlatYamlPlacement("demo", str),
    )


class LoadCounter:
    """Context manager that counts calls to ``GitStore._cached_object``.

    Monkeypatches the instance method on entry; restores on exit.
    """

    def __init__(self, store: GitStore) -> None:
        self.store = store
        self.count = 0
        self._orig = store._cached_object

    def __enter__(self) -> "LoadCounter":
        orig = self._orig

        def counted(object_id: bytes, *args: Any, **kwargs: Any):
            self.count += 1
            return orig(object_id, *args, **kwargs)

        # Bind on the instance, shadowing the bound method from the class.
        self.store._cached_object = counted  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            del self.store._cached_object  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback: restore explicit reference.
            self.store._cached_object = self._orig  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Target 1: GitStore.iter_subtree_files
# ---------------------------------------------------------------------------


@given(total=st.integers(min_value=10, max_value=50))
@settings(deadline=None, max_examples=25)
def test_iter_subtree_files_is_lazy(total: int) -> None:
    store = GitStore.init_memory()
    files = {f"docs/file_{i:03d}.txt": f"content {i}".encode() for i in range(total)}
    store.commit_files(files, "seed")

    with LoadCounter(store) as counter:
        iterator = store.iter_subtree_files("docs")
        first = next(iterator)
        # We must successfully pull one element.
        assert first[0].startswith("file_")
        # And the loader must NOT have walked the whole subtree to do it.
        assert counter.count < total, (
            f"iter_subtree_files loaded {counter.count} objects to pull 1 element of {total}; "
            "not lazy"
        )


# ---------------------------------------------------------------------------
# Target 2: DocumentFamilyStore.iter_handles
# ---------------------------------------------------------------------------


@given(total=st.integers(min_value=10, max_value=30))
@settings(deadline=None, max_examples=15)
def test_document_family_store_iter_handles_is_lazy(total: int) -> None:
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=_Owner(), backend=backend)
    family = _demo_family()

    for i in range(total):
        store.save(family, f"item_{i:03d}", _DemoDocument(f"alpha-{i}"), message=f"save {i}")

    with LoadCounter(backend) as counter:
        iterator = store.iter_handles(family)
        first = next(iterator)
        assert first.document.name.startswith("alpha-")
        assert counter.count < total, (
            f"DocumentFamilyStore.iter_handles loaded {counter.count} objects to pull 1 "
            f"handle of {total}; not lazy"
        )


# ---------------------------------------------------------------------------
# Target 3: BoundFamily.iter_handles (passthrough)
# ---------------------------------------------------------------------------


@given(total=st.integers(min_value=10, max_value=30))
@settings(deadline=None, max_examples=15)
def test_bound_family_iter_handles_is_lazy(total: int) -> None:
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=_Owner(), backend=backend)
    family = _demo_family()

    for i in range(total):
        store.save(family, f"item_{i:03d}", _DemoDocument(f"alpha-{i}"), message=f"save {i}")

    bound: BoundFamily[_Owner, str, _DemoDocument] = BoundFamily(store=store, family=family)

    with LoadCounter(backend) as counter:
        iterator = bound.iter_handles()
        first = next(iterator)
        assert first.document.name.startswith("alpha-")
        assert counter.count < total, (
            f"BoundFamily.iter_handles loaded {counter.count} objects to pull 1 handle of "
            f"{total}; not lazy"
        )

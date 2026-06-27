from __future__ import annotations

from dataclasses import dataclass

import msgspec
import pytest

from quire.artifacts import (
    ArtifactAddress,
    ArtifactFamily,
    BlobRefPlacement,
    FlatYamlPlacement,
    PathArtifactLocator,
    RefBlobLocator,
)
from quire.families import FamilyDefinition, FamilyRegistry
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.refs import RefName, singleton_ref_type
from quire.contracts import contract_version


class SchemaDocument(msgspec.Struct):
    body: str


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


SchemaRef = singleton_ref_type("SchemaRef", module=__name__)
SCHEMA_REF_NAME = RefName("refs/quire-test/schema")
VERSION = contract_version("2026.05.29")


def _schema_placement() -> BlobRefPlacement[Owner, object]:
    return BlobRefPlacement(SCHEMA_REF_NAME, ref_factory=SchemaRef)


def _schema_family() -> ArtifactFamily[Owner, object, SchemaDocument]:
    return ArtifactFamily(
        name="schema",
        contract_version=VERSION,
        doc_type=SchemaDocument,
        placement=_schema_placement(),
    )


# --- RefBlobLocator ---------------------------------------------------------


def test_ref_blob_locator_contract_body_carries_ref_name():
    locator = RefBlobLocator(SCHEMA_REF_NAME)
    assert locator.to_contract_body() == {
        "kind": "blob_ref",
        "ref": "refs/quire-test/schema",
    }


def test_ref_blob_address_is_branchless():
    placement = _schema_placement()
    address = placement.address_for(Owner(), SchemaRef())
    assert address.branch is None
    assert isinstance(address.locator, RefBlobLocator)
    assert address.locator.ref == SCHEMA_REF_NAME


def test_ref_blob_address_require_path_still_raises():
    address = ArtifactAddress(branch=None, locator=RefBlobLocator(SCHEMA_REF_NAME))
    with pytest.raises(TypeError):
        address.require_path()


def test_path_address_unaffected_require_path():
    address = ArtifactAddress(branch="master", locator=PathArtifactLocator("a/b.yaml"))
    assert address.require_path() == "a/b.yaml"


# --- BlobRefPlacement -------------------------------------------------------


def test_blob_ref_placement_contract_body_is_stable_and_serializable():
    placement = _schema_placement()
    body = placement.contract_body()
    assert body == {"kind": "blob_ref", "ref": "refs/quire-test/schema"}
    # round-trips through msgspec JSON (proves serializable / stable shape)
    assert msgspec.json.decode(msgspec.json.encode(body)) == body


def test_blob_ref_placement_iter_refs_yields_single_singleton():
    placement = _schema_placement()
    refs = list(placement.iter_refs(Owner(), None))
    assert refs == [SchemaRef()]


def test_blob_ref_placement_ref_recovery():
    placement = _schema_placement()
    assert placement.ref_from_locator(RefBlobLocator(SCHEMA_REF_NAME)) == SchemaRef()
    assert placement.ref_from_loaded(object()) == SchemaRef()


# --- Family store round-trip on a fresh, branchless store -------------------


def test_ref_backed_family_round_trips_with_no_branch_and_no_tree():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    family = _schema_family()

    # No branch, no commit, no tree exists yet.
    assert backend.branch_sha("master") is None

    store.save(family, SchemaRef(), SchemaDocument("hello"), message="write schema")

    # Still no branch/tree — the write landed on the blob-ref, not a branch tree.
    assert backend.branch_sha("master") is None
    assert backend.log(max_count=10) == []

    loaded = store.load(family, SchemaRef())
    assert loaded == SchemaDocument("hello")


def test_ref_backed_family_reads_the_loose_blob_directly():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    family = _schema_family()
    store.save(family, SchemaRef(), SchemaDocument("payload"), message="write schema")

    raw = backend.read_blob_ref(SCHEMA_REF_NAME)
    assert raw is not None
    assert b"payload" in raw


def test_ref_backed_family_load_returns_none_when_absent():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _schema_family()
    assert store.load(family, SchemaRef()) is None


def test_ref_backed_family_exists_reflects_blob_presence():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _schema_family()
    assert store.exists(family, SchemaRef()) is False
    store.save(family, SchemaRef(), SchemaDocument("x"), message="write")
    assert store.exists(family, SchemaRef()) is True


def test_ref_backed_family_overwrite_replaces_blob():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _schema_family()
    store.save(family, SchemaRef(), SchemaDocument("v1"), message="v1")
    store.save(family, SchemaRef(), SchemaDocument("v2"), message="v2")
    assert store.load(family, SchemaRef()) == SchemaDocument("v2")


# --- Registry: bind needs no read; cold load on demand; tree-scan absence ---


def _registry_with_schema() -> FamilyRegistry[Owner, str]:
    schema_def = FamilyDefinition(
        key="schema",
        name="schema",
        contract_version=VERSION,
        artifact_family=_schema_family(),
        metadata={"semantic": False},
    )
    demo_def = FamilyDefinition(
        key="demo",
        name="demo",
        contract_version=VERSION,
        artifact_family=ArtifactFamily(
            name="demo",
            contract_version=VERSION,
            doc_type=SchemaDocument,
            placement=FlatYamlPlacement("demo", str),
        ),
    )
    return FamilyRegistry(
        name="test-registry",
        contract_version=VERSION,
        families=(schema_def, demo_def),
    )


def test_bind_performs_no_content_read_even_when_ref_absent():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    registry = _registry_with_schema()

    # Binding must succeed without the ref existing (no content read on bind).
    bound = registry.bind(Owner(), store)
    assert bound is not None
    # The ref still does not exist; load is on-demand.
    assert backend.read_blob_ref(SCHEMA_REF_NAME) is None


def test_ref_backed_family_loads_via_registry_accessor_cold():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    registry = _registry_with_schema()
    bound = registry.bind(Owner(), store)

    bound.schema.save(SchemaRef(), SchemaDocument("cold"), message="write schema")

    # Branch-independent: no branch/tree was created.
    assert backend.branch_sha("master") is None
    assert bound.schema.load(SchemaRef()) == SchemaDocument("cold")


def test_ref_backed_family_absent_from_tree_path_scan():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    registry = _registry_with_schema()
    bound = registry.bind(Owner(), store)

    # Write a normal tree-file family doc and the ref-backed schema.
    bound.demo.save("alpha", SchemaDocument("a"), message="write demo")
    bound.schema.save(SchemaRef(), SchemaDocument("schema-body"), message="write schema")

    # A tree-path scan over the demo namespace surfaces only the tree file.
    demo_handles = list(bound.demo.iter_handles())
    paths = [handle.address.require_path() for handle in demo_handles]
    assert paths == ["demo/alpha.yaml"]
    assert all("schema" not in path for path in paths)

    # The schema ref content is intact and isolated.
    assert bound.schema.load(SchemaRef()) == SchemaDocument("schema-body")


def test_registry_mixing_ref_and_tree_families_does_not_break():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    registry = _registry_with_schema()
    bound = registry.bind(Owner(), store)

    bound.demo.save("alpha", SchemaDocument("a"), message="demo")
    bound.demo.save("beta", SchemaDocument("b"), message="demo")
    bound.schema.save(SchemaRef(), SchemaDocument("s"), message="schema")

    assert {handle.ref for handle in bound.demo.iter_handles()} == {"alpha", "beta"}
    assert bound.schema.load(SchemaRef()) == SchemaDocument("s")

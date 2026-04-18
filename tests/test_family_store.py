from __future__ import annotations

from dataclasses import dataclass

import msgspec
import pytest

from quire.artifacts import (
    ArtifactContext,
    ArtifactFamily,
    BranchPlacement,
    FlatYamlPlacement,
    ReadOnlyDocumentStoreBackend,
)
from quire.family_store import DocumentFamilyStore, DocumentStoreBackend
from quire.git_store import GitStore
from quire.versions import VersionId


class DemoDocument(msgspec.Struct):
    name: str


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


@dataclass(frozen=True)
class BranchRef:
    branch: str
    name: str


def _demo_family(
    *,
    normalize_for_write=None,
    validate_for_write=None,
    encode_document=None,
) -> ArtifactFamily[Owner, str, DemoDocument]:
    return ArtifactFamily(
        name="demo",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement("demo", str),
        normalize_for_write=normalize_for_write,
        validate_for_write=validate_for_write,
        encode_document=encode_document,
    )


def test_family_store_saves_and_loads_typed_document():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()

    commit = store.save(family, "example", DemoDocument("alpha"), message="save demo")

    assert len(commit) == 40
    assert store.require(family, "example") == DemoDocument("alpha")


def test_prepare_runs_normalize_validate_then_encode():
    events: list[str] = []

    def normalize(
        context: ArtifactContext[Owner, str],
        document: DemoDocument,
        _store: DocumentFamilyStore[Owner],
    ) -> DemoDocument:
        events.append(f"normalize:{context.require_path()}:{document.name}")
        return DemoDocument(f"{document.name}-normalized")

    def validate(
        _context: ArtifactContext[Owner, str],
        document: DemoDocument,
        _store: DocumentFamilyStore[Owner],
    ) -> None:
        events.append(f"validate:{document.name}")

    def encode(document: DemoDocument) -> bytes:
        events.append(f"encode:{document.name}")
        return msgspec.yaml.encode({"name": document.name})

    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family(
        normalize_for_write=normalize,
        validate_for_write=validate,
        encode_document=encode,
    )

    prepared = store.prepare(family, "example", DemoDocument("alpha"))

    assert prepared.document == DemoDocument("alpha-normalized")
    assert prepared.address.require_path() == "demo/example.yaml"
    assert events == [
        "normalize:demo/example.yaml:alpha",
        "validate:alpha-normalized",
        "encode:alpha-normalized",
    ]


def test_prepare_has_no_git_side_effects():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    family = _demo_family()

    prepared = store.prepare(family, "example", DemoDocument("alpha"))

    assert prepared.content
    assert backend.branch_sha("master") is None
    assert backend.log(max_count=10) == []


def test_document_store_backend_extends_read_only_backend_protocol():
    assert ReadOnlyDocumentStoreBackend in DocumentStoreBackend.__mro__


def test_delete_removes_document_from_branch():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()
    store.save(family, "example", DemoDocument("alpha"), message="save demo")

    store.delete(family, "example", message="delete demo")

    assert store.load(family, "example") is None


def test_transaction_writes_multiple_documents_in_one_commit():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    family = _demo_family()

    with store.transact(message="save two") as transaction:
        transaction.save(family, "one", DemoDocument("one"))
        transaction.save(family, "two", DemoDocument("two"))

    assert store.require(family, "one") == DemoDocument("one")
    assert store.require(family, "two") == DemoDocument("two")
    assert len(backend.log(max_count=10)) == 1


def test_transaction_rejects_cross_branch_writes():
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    family = ArtifactFamily[Owner, BranchRef, DemoDocument](
        name="branching",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement(
            "demo",
            lambda name: BranchRef("master", name),
            ref_field="name",
            branch=BranchPlacement(policy="template", template="{stem}", ref_field="branch"),
        ),
    )

    with pytest.raises(ValueError, match="Transaction branch mismatch"):
        with store.transact(message="cross branch") as transaction:
            transaction.save(family, BranchRef("master", "one"), DemoDocument("one"))
            transaction.save(family, BranchRef("other", "two"), DemoDocument("two"))


def test_move_deletes_old_path_and_writes_new_path():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()
    store.save(family, "old", DemoDocument("old"), message="save old")

    store.move(family, "old", "new", DemoDocument("new"), message="move demo")

    assert store.load(family, "old") is None
    assert store.require(family, "new") == DemoDocument("new")


def test_custom_codecs_override_defaults():
    family = ArtifactFamily(
        name="custom",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement("custom", str, extension=".txt"),
        encode_document=lambda document: f"name={document.name}".encode("utf-8"),
        decode_bytes=lambda payload, _source: DemoDocument(payload.decode("utf-8").split("=", 1)[1]),
        render_document=lambda document: f"name={document.name}",
        document_payload=lambda document: {"custom_name": document.name},
    )
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())

    store.save(family, "example", DemoDocument("alpha"), message="save custom")

    assert store.require(family, "example") == DemoDocument("alpha")
    assert store.render(DemoDocument("beta"), family) == "name=beta"
    assert store.payload(DemoDocument("beta"), family) == {"custom_name": "beta"}


def test_unsupported_family_operations_fail_clearly():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = ArtifactFamily(
        name="minimal",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement("minimal", str),
    )

    with pytest.raises(ValueError, match="expected minimal"):
        store.ref_from_path(family, "other/example.yaml")

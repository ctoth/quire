from __future__ import annotations

from dataclasses import dataclass

import msgspec
import pytest

from quire.artifacts import (
    ArtifactContext,
    ArtifactFamily,
    BranchPlacement,
    FlatYamlPlacement,
    HashScatteredYamlPlacement,
    ReadOnlyDocumentStoreBackend,
)
from quire.documents import DocumentCodec
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


def test_family_store_exists_checks_address_without_decoding():
    def decode_bytes(_raw: bytes, _source: str) -> DemoDocument:
        raise AssertionError("exists must not decode documents")

    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = ArtifactFamily[Owner, str, DemoDocument](
        name="demo",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement("demo", str),
        decode_bytes=decode_bytes,
    )
    store.save(family, "example", DemoDocument("alpha"), message="save demo")

    assert store.exists(family, "example") is True
    assert store.exists(family, "missing") is False
    with pytest.raises(AssertionError, match="must not decode"):
        store.load(family, "example")


def test_family_store_exists_respects_branch_and_commit():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()

    first = store.save(family, "alpha", DemoDocument("alpha"), message="save alpha")
    store.save(family, "beta", DemoDocument("beta"), message="save beta")
    store.save(family, "branch-only", DemoDocument("gamma"), message="save branch", branch="other")

    assert store.exists(family, "alpha", commit=first) is True
    assert store.exists(family, "beta", commit=first) is False
    assert store.exists(family, "branch-only") is False
    assert store.exists(family, "branch-only", branch="other") is True


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


def test_iter_handles_streams_flat_family_documents():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()
    store.save(family, "alpha", DemoDocument("alpha"), message="save alpha")
    store.save(family, "beta", DemoDocument("beta"), message="save beta")

    handles = list(store.iter_handles(family))

    assert [(handle.ref, handle.document.name) for handle in handles] == [
        ("alpha", "alpha"),
        ("beta", "beta"),
    ]
    assert [handle.address.require_path() for handle in handles] == [
        "demo/alpha.yaml",
        "demo/beta.yaml",
    ]


def test_iter_handles_respects_pinned_commit_for_flat_family():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()
    first = store.save(family, "alpha", DemoDocument("alpha"), message="save alpha")
    store.save(family, "beta", DemoDocument("beta"), message="save beta")

    handles = list(store.iter_handles(family, commit=first))

    assert [(handle.ref, handle.document.name) for handle in handles] == [("alpha", "alpha")]
    assert handles[0].address.commit == first


def test_iter_handles_supports_hash_scattered_encoded_refs():
    backend = GitStore.init_memory()
    family = ArtifactFamily[Owner, str, DemoDocument](
        name="hashy",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        placement=HashScatteredYamlPlacement(
            "hashy",
            str,
            codec="colon_to_double_underscore",
            filename_mode="encoded_ref",
        ),
    )
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    store.save(family, "claim:a", DemoDocument("claim:a"), message="save a")
    store.save(family, "claim:b", DemoDocument("claim:b"), message="save b")

    handles = list(store.iter_handles(family))

    assert sorted((handle.ref, handle.document.name) for handle in handles) == [
        ("claim:a", "claim:a"),
        ("claim:b", "claim:b"),
    ]


def test_store_pin_resolves_branch_head_once_for_iter_and_require():
    backend = GitStore.init_memory()
    calls: list[str] = []

    def counting_branch_head(resolved_backend: DocumentStoreBackend, branch: str) -> str | None:
        calls.append(branch)
        return resolved_backend.branch_sha(branch)

    store = DocumentFamilyStore(
        owner=Owner(),
        backend=backend,
        branch_head=counting_branch_head,
    )
    family = _demo_family()
    store.save(family, "alpha", DemoDocument("alpha"), message="save alpha")
    store.save(family, "beta", DemoDocument("beta"), message="save beta")

    pinned_branch, pinned_commit = store.pin(family)
    refs = list(store.iter(family, branch=pinned_branch, commit=pinned_commit))
    loaded = [store.require(family, ref, branch=pinned_branch, commit=pinned_commit) for ref in refs]

    assert pinned_branch == "master"
    assert pinned_commit is not None
    assert [document.name for document in loaded] == ["alpha", "beta"]
    assert calls == ["master"]


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


def test_transaction_move_and_save_order_preserves_later_operations():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()
    store.save(family, "old", DemoDocument("old"), message="save old")

    with store.transact(message="move then replace old") as transaction:
        transaction.move(family, "old", "new", DemoDocument("new"))
        transaction.save(family, "old", DemoDocument("replacement"))

    assert store.require(family, "old") == DemoDocument("replacement")
    assert store.require(family, "new") == DemoDocument("new")


def test_transaction_save_then_move_removes_staged_old_path():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()

    with store.transact(message="save then move") as transaction:
        transaction.save(family, "old", DemoDocument("old"))
        transaction.move(family, "old", "new", DemoDocument("new"))

    assert store.load(family, "old") is None
    assert store.require(family, "new") == DemoDocument("new")


def test_transaction_delete_then_save_restores_path():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()
    store.save(family, "old", DemoDocument("old"), message="save old")

    with store.transact(message="delete then save") as transaction:
        transaction.delete(family, "old")
        transaction.save(family, "old", DemoDocument("replacement"))

    assert store.require(family, "old") == DemoDocument("replacement")


def test_transaction_move_with_same_ref_does_not_stage_delete():
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    family = _demo_family()
    store.save(family, "same", DemoDocument("old"), message="save old")

    with store.transact(message="same path move") as transaction:
        transaction.move(family, "same", "same", DemoDocument("updated"))

    assert store.require(family, "same") == DemoDocument("updated")


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


def test_store_uses_single_document_codec_for_default_operations():
    events: list[str] = []
    codec = DocumentCodec(
        convert_document=lambda payload, document_type, *, source: (
            events.append(f"convert:{source}") or document_type(**payload)
        ),
        decode_document=lambda payload, document_type, *, source: (
            events.append(f"decode:{source}") or document_type(payload.decode("utf-8").split("=", 1)[1])
        ),
        encode_document=lambda document: (
            events.append(f"encode:{document.name}") or f"name={document.name}".encode("utf-8")
        ),
        render_document=lambda document: events.append(f"render:{document.name}") or f"name={document.name}",
        document_to_payload=lambda document: events.append(f"payload:{document.name}") or {"name": document.name},
    )
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory(), codec=codec)
    family = _demo_family()

    store.save(family, "alpha", DemoDocument("alpha"), message="save alpha")

    assert store.require(family, "alpha") == DemoDocument("alpha")
    assert store.coerce(family, {"name": "beta"}, source="input") == DemoDocument("beta")
    assert store.render(DemoDocument("gamma")) == "name=gamma"
    assert store.payload(DemoDocument("delta")) == {"name": "delta"}
    assert events == [
        "encode:alpha",
        "decode:master:demo/alpha.yaml",
        "convert:input",
        "render:gamma",
        "payload:delta",
    ]


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

from __future__ import annotations

from dataclasses import dataclass

import msgspec
import pytest

from quire.artifacts import ArtifactContext, ArtifactFamily, ResolvedArtifact
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.versions import VersionId


class DemoDocument(msgspec.Struct):
    name: str


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


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
        resolve_ref=lambda owner, ref: ResolvedArtifact(
            branch=owner.branch,
            relpath=f"demo/{ref}.yaml",
        ),
        normalize_for_write=normalize_for_write,
        validate_for_write=validate_for_write,
        encode_document=encode_document,
        list_refs=lambda _owner, _branch, _commit: ["example"],
        ref_from_path=lambda path: str(path).replace("\\", "/").removeprefix("demo/").removesuffix(".yaml"),
        ref_from_loaded=lambda loaded: loaded.name,
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
        events.append(f"normalize:{context.relpath}:{document.name}")
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
    assert events == [
        "normalize:demo/example.yaml:alpha",
        "validate:alpha-normalized",
        "encode:alpha-normalized",
    ]


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
    family = ArtifactFamily(
        name="branching",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        resolve_ref=lambda _owner, ref: ResolvedArtifact(
            branch=ref.split(":", 1)[0],
            relpath=f"demo/{ref}.yaml",
        ),
    )

    with pytest.raises(ValueError, match="Transaction branch mismatch"):
        with store.transact(message="cross branch") as transaction:
            transaction.save(family, "master:one", DemoDocument("one"))
            transaction.save(family, "other:two", DemoDocument("two"))


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
        resolve_ref=lambda owner, ref: ResolvedArtifact(
            branch=owner.branch,
            relpath=f"custom/{ref}.txt",
        ),
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
        resolve_ref=lambda owner, ref: ResolvedArtifact(
            branch=owner.branch,
            relpath=f"minimal/{ref}.yaml",
        ),
    )

    with pytest.raises(TypeError, match="path-derived refs"):
        store.ref_from_path(family, "minimal/example.yaml")
    with pytest.raises(TypeError, match="loaded-object refs"):
        store.ref_from_loaded(family, DemoDocument("example"))
    with pytest.raises(TypeError, match="does not support listing"):
        store.list(family)

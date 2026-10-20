from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import permutations
from typing import Annotated

import msgspec
import pytest

from quire.charter_class import CharterDoc, charter, charter_field
from quire.artifacts import FlatYamlPlacement
from quire.charters import registry_from_charters
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.family_transport import FamilyInstanceMap, UnsupportedFamilyTransportError
from quire.references import ForeignKeySpec, ForeignKeyValidationError
from quire.versions import VersionId


@charter(key="item", name="item", contract_version="2026.10.20", placement="items", identity_field="item_id")
class Item(CharterDoc):
    item_id: str


def _identity(document: Link) -> Link:
    return msgspec.structs.replace(document, link_id=f"link:{document.item}")


@dataclass(frozen=True)
class LinkRef:
    link_id: str


@charter(key="link", name="link", contract_version="2026.10.20",
         placement=FlatYamlPlacement("links", LinkRef, ref_field="link_id"),
         identity_field="link_id", transport_identity=_identity)
class Link(CharterDoc):
    link_id: str
    item: Annotated[str, charter_field(foreign_key=ForeignKeySpec(
        "item", VersionId("2026.10.20"), "link", "item", "item", "item_id",
    ))]
    related: Annotated[tuple[str, ...], charter_field(foreign_key=ForeignKeySpec(
        "related", VersionId("2026.10.20"), "link", "related[]", "link", "link_id",
        required=False, many=True,
    ))] = ()
    evidence: str = "authored"


def _registry():
    return registry_from_charters(Item.__charter__, Link.__charter__, name="self-test",
                                  contract_version=VersionId("2026.10.20"))


def _instance(documents: tuple[Link, ...]) -> FamilyInstanceMap:
    registry = _registry()
    return FamilyInstanceMap("pinned", {
        "item": registry.by_name("item").reference_index_from_records(tuple(Item(i) for i in ("a", "b", "c"))),
        "link": registry.by_name("link").reference_index_from_records(documents),
    })


@pytest.mark.parametrize("order", tuple(permutations(range(3))))
@pytest.mark.parametrize("moved", [("a",), ("a", "c")])
def test_cyclic_batch_is_order_independent_and_moves_legacy_referrers(order, moved) -> None:
    originals = (
        Link("old-a", "a", ("old-b",), "first"),
        Link("old-b", "b", ("old-a",), "second"),
        Link("old-c", "c", ("old-b",), "third"),
    )
    documents = tuple(originals[i] for i in order)
    instance = _instance(originals)
    for source in moved:
        instance.assign("item", source, source + "-image")
    results = instance.transport_family(_registry().by_name("link"), documents)
    image = {document.link_id: f"link:{document.item}{'-image' if document.item in moved else ''}"
             for document in originals}
    assert results == tuple(msgspec.structs.replace(
        document, link_id=image[document.link_id],
        item=document.item + "-image" if document.item in moved else document.item,
        related=tuple(image[target] for target in document.related),
    ) for document in documents)
    assert {(i.source_id, i.target_id) for i in instance.iter_images() if i.family == "link"} == set(image.items())
    assert originals[1].related == ("old-a",)


def test_self_loop_is_preserved_without_recursive_identity() -> None:
    document = Link("old-a", "a", ("old-a",))
    instance = _instance((document,))
    instance.assign("item", "a", "new-a")
    assert instance.transport_family(_registry().by_name("link"), (document,)) == (
        Link("link:new-a", "new-a", ("link:new-a",)),
    )


def test_unchanged_cyclic_batch_keeps_original_objects_and_ids() -> None:
    documents = (Link("old-a", "a", ("old-b",)), Link("old-b", "b", ("old-a",)))
    instance = _instance(documents)
    results = instance.transport_family(_registry().by_name("link"), documents)
    assert all(result is original for result, original in zip(results, documents, strict=True))
    assert tuple(instance.iter_images()) == ()


def test_dangling_source_self_reference_is_not_silently_kept() -> None:
    document = Link("old-a", "a", ("missing",))
    with pytest.raises(ForeignKeyValidationError, match="does not resolve"):
        _instance((document,)).transport_family(_registry().by_name("link"), (document,))


def test_batch_without_identity_operation_rejects_changes() -> None:
    document = Link("old-a", "a", ("old-a",))
    instance = _instance((document,))
    instance.assign("item", "a", "new-a")
    with pytest.raises(UnsupportedFamilyTransportError, match="identity operation"):
        instance.transport_family(replace(_registry().by_name("link"), transport_identity=None), (document,))


def test_identity_depending_on_rewritten_self_reference_is_not_guessed() -> None:
    document = Link("old-a", "a", ("old-a",))
    instance = _instance((document,))
    instance.assign("item", "a", "new-a")
    family = replace(_registry().by_name("link"), transport_identity=lambda doc: msgspec.structs.replace(
        doc, link_id=f"{doc.item}:{doc.related[0]}",
    ))
    with pytest.raises(ValueError, match="conflicting images"):
        instance.transport_family(family, (document,))


def test_transported_document_uses_declared_typed_ref_for_preparation() -> None:
    @dataclass(frozen=True)
    class Owner:
        branch: str = "master"

    family = _registry().by_name("link")
    document = Link("old-a", "a", ("old-a",), "original evidence")
    instance = _instance((document,))
    instance.assign("item", "a", "new-a")
    result, = instance.transport_family(family, (document,))
    ref = family.ref_from_document(result)
    assert ref == LinkRef("link:new-a")
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    prepared = store.prepare(family.artifact_family, ref, result)
    assert prepared.ref == ref
    assert prepared.address.require_path() == "links/link:new-a.yaml"
    assert prepared.document == result
    assert result.evidence == document.evidence


def test_generic_batch_still_allows_noninjective_images_without_family_policy() -> None:
    documents = (Link("old-a", "a", ("old-b",)), Link("old-b", "a", ("old-a",)))
    instance = _instance(documents)
    instance.assign("item", "a", "new-a")
    results = instance.transport_family(_registry().by_name("link"), documents)
    assert results == (Link("link:new-a", "new-a", ("link:new-a",)),) * 2


def test_family_admission_receives_originals_and_complete_images_before_coalescence() -> None:
    documents = (Link("old-a", "a", ("old-b",)), Link("old-b", "a", ("old-a",)))
    observed = []

    def reject_collapse(originals, images):
        observed.append((tuple(originals), tuple(images)))
        raise ValueError("family rejects collapse")

    family = replace(_registry().by_name("link"), validate_transport=reject_collapse)
    assert family.contract_body()["validate_transport"].endswith(".reject_collapse")
    instance = _instance(documents)
    instance.assign("item", "a", "new-a")
    with pytest.raises(ValueError, match="family rejects collapse"):
        instance.transport_family(family, documents)
    assert observed == [(documents, (Link("link:new-a", "new-a", ("link:new-a",)),) * 2)]

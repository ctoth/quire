from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import msgspec
import pytest

from quire.charter_class import CharterDoc, charter, charter_field
from quire.charters import registry_from_charters
from quire.family_transport import FamilyInstanceMap, UnsupportedFamilyTransportError
from quire.references import ForeignKeySpec, ForeignKeyValidationError, map_foreign_keys
from quire.versions import VersionId


def _fk(name: str, field: str) -> ForeignKeySpec:
    return ForeignKeySpec(name, VersionId("2026.10.20"), "edge", field, "node", "node_id")


@charter(key="node", name="node", contract_version="2026.10.20", placement="nodes", identity_field="node_id")
class Node(CharterDoc):
    node_id: str


def _edge_identity(document: Edge) -> Edge:
    return msgspec.structs.replace(document, edge_id=f"{document.source}->{document.target}")


@charter(
    key="edge", name="edge", contract_version="2026.10.20", placement="edges",
    identity_field="edge_id", transport_identity=_edge_identity,
)
class Edge(CharterDoc):
    edge_id: str
    source: Annotated[str, charter_field(foreign_key=_fk("source", "source"))]
    target: Annotated[str, charter_field(foreign_key=_fk("target", "target"))]
    evidence: str = "original evidence"


def _registry():
    return registry_from_charters(Node.__charter__, Edge.__charter__, name="test", contract_version=VersionId("2026.10.20"))


def _instance(commit: str) -> FamilyInstanceMap:
    registry = _registry()
    return FamilyInstanceMap(commit, {
        "node": registry.by_name("node").reference_index_from_records((Node("s"), Node("c"))),
        "edge": registry.by_name("edge").reference_index_from_records((Edge("e", "s", "c"),)),
    })


def test_images_are_commit_qualified_and_preserve_correlated_endpoints() -> None:
    family = _registry().by_name("edge")
    edge = Edge("e", "s", "c")
    left, right = _instance("commit-left"), _instance("commit-right")
    left.assign("node", "s", "s-left")
    left.assign("node", "c", "c-left")
    right.assign("node", "s", "s-right")
    right.assign("node", "c", "c-right")
    results = (left.transport(family, edge), right.transport(family, edge))
    assert {(e.source, e.target) for e in results} == {("s-left", "c-left"), ("s-right", "c-right")}
    assert {e.edge_id for e in results} == {"s-left->c-left", "s-right->c-right"}
    assert all(e.evidence == edge.evidence for e in results)
    images = [i for m in (left, right) for i in m.iter_images() if i.family == "edge"]
    assert {(i.source_commit, i.source_id, i.target_id) for i in images} == {
        ("commit-left", "e", "s-left->c-left"), ("commit-right", "e", "s-right->c-right")
    }
    assert edge == Edge("e", "s", "c")


def test_unchanged_occurrence_keeps_original_identity_and_object() -> None:
    edge = Edge("e", "s", "c")
    assert _instance("commit").transport(_registry().by_name("edge"), edge) is edge


def test_one_occurrence_cannot_have_conflicting_images() -> None:
    instance = _instance("commit")
    instance.assign("node", "c", "c-left")
    with pytest.raises(ValueError, match="conflicting images"):
        instance.assign("node", "c", "c-right")


def test_unknown_source_occurrence_cannot_be_assigned_an_image() -> None:
    with pytest.raises(ForeignKeyValidationError, match="unknown source occurrence"):
        _instance("commit").assign("node", "missing", "new")


def test_invalid_source_reference_is_rejected_before_transport() -> None:
    with pytest.raises(ForeignKeyValidationError, match="does not resolve"):
        _instance("commit").transport(_registry().by_name("edge"), Edge("e", "missing", "c"))


def test_family_without_transport_identity_rejects_reference_changes() -> None:
    from dataclasses import replace

    instance = _instance("commit")
    instance.assign("node", "c", "c-new")
    family = replace(_registry().by_name("edge"), transport_identity=None)
    with pytest.raises(UnsupportedFamilyTransportError, match="identity operation"):
        instance.transport(family, Edge("e", "s", "c"))


def test_identity_operation_cannot_undo_the_reference_map() -> None:
    from dataclasses import replace

    instance = _instance("commit")
    instance.assign("node", "c", "c-new")
    family = replace(
        _registry().by_name("edge"),
        transport_identity=lambda document: msgspec.structs.replace(document, target="c"),
    )
    with pytest.raises(ValueError, match="changed transported foreign keys"):
        instance.transport(family, Edge("e", "s", "c"))


class Bundle(msgspec.Struct, frozen=True):
    members: tuple[str, ...]
    principal: str

    def __post_init__(self) -> None:
        if self.principal not in self.members:
            raise ValueError("principal must belong to members")


def test_members_and_principal_are_replaced_atomically() -> None:
    bundle = Bundle(("c", "s"), "c")
    specs = (_fk("members", "members[]"), _fk("principal", "principal"))
    result = map_foreign_keys(bundle, specs, lambda _spec, value: value + "-left")
    assert result == Bundle(("c-left", "s-left"), "c-left")
    assert bundle == Bundle(("c", "s"), "c")


@dataclass(frozen=True)
class Nested:
    target: str | None


class NestedDocument(msgspec.Struct, frozen=True):
    items: tuple[Nested, ...]
    label: str


def test_nested_sequence_paths_share_the_reader_grammar() -> None:
    document = NestedDocument((Nested("c"), Nested(None)), "unchanged")
    result = map_foreign_keys(document, (_fk("nested", "items[].target"),), lambda _s, v: v + "-new")
    assert result == NestedDocument((Nested("c-new"), Nested(None)), "unchanged")
    assert document.items == (Nested("c"), Nested(None))


def test_overlapping_paths_are_not_silently_overwritten() -> None:
    with pytest.raises(ValueError, match="overlapping"):
        map_foreign_keys(Edge("e", "s", "c"), (_fk("a", "target"), _fk("b", "target")), lambda _s, v: v)


def test_complete_instance_validation_rejects_dangling_result() -> None:
    registry = _registry()
    registry.validate_records({"node": (Node("s"), Node("c")), "edge": (Edge("e", "s", "c"),)})
    with pytest.raises(ForeignKeyValidationError, match="does not resolve"):
        registry.validate_records({"node": (Node("s"),), "edge": (Edge("e", "s", "c"),)})


def test_identity_callback_is_part_of_the_family_contract() -> None:
    assert _registry().by_name("edge").contract_body()["transport_identity"].endswith("._edge_identity")

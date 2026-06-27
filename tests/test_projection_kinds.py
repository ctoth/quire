"""Tests for the registrable projection-kind registry."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest

from quire.charters import CharterField
from quire.projection_kinds import (
    ProjectionKind,
    iter_projection_kinds,
    projection_kind,
    register_projection_kind,
    unregister_projection_kind,
)


class _MarkerKind:
    """A trivial kind: applies to fields carrying ``metadata['mark']``."""

    name = "test-marker"

    def applies(self, field: CharterField) -> bool:
        return bool(field.metadata.get("mark"))

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        return {"mark": field.metadata["mark"]}


@pytest.fixture
def marker() -> Iterator[_MarkerKind]:
    kind = _MarkerKind()
    register_projection_kind(kind)
    yield kind
    unregister_projection_kind(kind.name)


def test_registered_kind_is_iterated(marker: _MarkerKind) -> None:
    assert marker in list(iter_projection_kinds())


def test_lookup_by_name(marker: _MarkerKind) -> None:
    assert projection_kind("test-marker") is marker


def test_unknown_kind_lookup_raises() -> None:
    with pytest.raises(KeyError):
        projection_kind("does-not-exist")


def test_iteration_is_name_sorted() -> None:
    class _A:
        name = "aaa-kind"

        def applies(self, field: CharterField) -> bool:
            return False

        def schema_payload(self, field: CharterField) -> Mapping[str, object]:
            return {}

    class _Z:
        name = "zzz-kind"

        def applies(self, field: CharterField) -> bool:
            return False

        def schema_payload(self, field: CharterField) -> Mapping[str, object]:
            return {}

    a, z = _A(), _Z()
    register_projection_kind(z)
    register_projection_kind(a)
    try:
        names = [kind.name for kind in iter_projection_kinds()]
        assert names.index("aaa-kind") < names.index("zzz-kind")
    finally:
        unregister_projection_kind(a.name)
        unregister_projection_kind(z.name)


def test_reregistering_same_object_is_noop(marker: _MarkerKind) -> None:
    register_projection_kind(marker)  # must not raise
    assert projection_kind("test-marker") is marker


def test_registering_different_object_same_name_raises(marker: _MarkerKind) -> None:
    class _Clash:
        name = "test-marker"

        def applies(self, field: CharterField) -> bool:
            return False

        def schema_payload(self, field: CharterField) -> Mapping[str, object]:
            return {}

    with pytest.raises(ValueError, match="already registered"):
        register_projection_kind(_Clash())


def test_applies_and_payload_drive_off_field(marker: _MarkerKind) -> None:
    marked = CharterField("f", str, metadata={"mark": "yes"})
    plain = CharterField("g", str)
    assert marker.applies(marked) is True
    assert marker.applies(plain) is False
    assert marker.schema_payload(marked) == {"mark": "yes"}


def test_marker_kind_satisfies_protocol(marker: _MarkerKind) -> None:
    assert isinstance(marker, ProjectionKind)

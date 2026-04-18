from __future__ import annotations

import pytest

from quire.refs import single_field_ref_type, singleton_ref_type


def test_single_field_ref_type_creates_immutable_named_key() -> None:
    SourceRef = single_field_ref_type("SourceRef", "name", module=__name__)

    ref = SourceRef("paper_a")

    assert ref.name == "paper_a"
    assert type(ref).__name__ == "SourceRef"
    assert type(ref).__module__ == __name__
    with pytest.raises(Exception):
        ref.name = "paper_b"


def test_singleton_ref_type_creates_immutable_zero_field_key() -> None:
    MergeRef = singleton_ref_type("MergeRef", module=__name__)

    assert MergeRef() == MergeRef()
    assert type(MergeRef()).__name__ == "MergeRef"


def test_ref_type_factories_reject_invalid_identifiers() -> None:
    with pytest.raises(ValueError):
        single_field_ref_type("not valid", "name")
    with pytest.raises(ValueError):
        single_field_ref_type("SourceRef", "not valid")
    with pytest.raises(ValueError):
        singleton_ref_type("not valid")

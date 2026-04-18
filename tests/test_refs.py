from __future__ import annotations

import pytest

from quire.notes import NotesRef
from quire.refs import RefName, single_field_ref_type, singleton_ref_type


def test_ref_name_accepts_valid_refs() -> None:
    assert RefName("refs/heads/main").value == "refs/heads/main"
    assert RefName("refs/quire/branch-meta/source%2Fpaper-a").as_bytes() == (
        b"refs/quire/branch-meta/source%2Fpaper-a"
    )


@pytest.mark.parametrize(
    "value",
    (
        "refs/heads/feature..bad",
        "refs/heads/feature@{bad",
        "refs/heads/feature.lock",
        "refs/heads/-feature",
        "refs/heads/feature.",
        "refs/heads/feature name",
        "refs/heads/feature\nname",
    ),
)
def test_ref_name_rejects_invalid_git_refnames(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid ref name"):
        RefName(value)


def test_notes_ref_stores_validated_ref_name() -> None:
    ref = NotesRef("refs/notes/quire")

    assert ref.ref_name == RefName("refs/notes/quire")
    assert ref.as_ref() == ref.ref_name
    assert ref.as_bytes() == b"refs/notes/quire"


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

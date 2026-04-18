from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

import pytest

from quire.references import (
    CrossFamilyReferenceIndex,
    ForeignKeySpec,
    ReferenceIndex,
    build_reference_lookup,
)
from quire.versions import VersionId


@dataclass(frozen=True)
class Record:
    artifact_id: str
    aliases: tuple[str, ...] = ()


def _index() -> ReferenceIndex[object]:
    records = {
        "concept:1": Record("concept:1", ("F0", "frequency")),
        "concept:2": Record("concept:2", ("F0", "pitch")),
        "concept:3": Record("concept:3", ("pressure",)),
    }
    lookup = build_reference_lookup(
        records.values(),
        target_id=lambda record: record.artifact_id,
        keys=lambda record: record.aliases,
    )
    return ReferenceIndex(
        family="concept",
        records_by_id=MappingProxyType(records),
        lookup=lookup,
    )


def test_reference_index_resolves_unique_keys_and_ids() -> None:
    index = _index()

    assert index.resolve_id("concept:1") == "concept:1"
    assert index.resolve_id("pressure") == "concept:3"
    assert index.exists("pressure")


def test_reference_index_reports_ambiguous_keys_without_guessing() -> None:
    resolution = _index().resolve("F0")

    assert resolution is not None
    assert not resolution.found
    assert resolution.ambiguous
    assert resolution.target_kind == "concept"
    assert resolution.ambiguous_candidates == ("concept:1", "concept:2")


def test_cross_family_index_fails_for_unknown_family() -> None:
    families = CrossFamilyReferenceIndex(families={"concept": _index()})

    with pytest.raises(KeyError, match="unknown reference family"):
        families.exists("claim", "claim:1")


def test_foreign_key_spec_contract_body_is_stable() -> None:
    spec = ForeignKeySpec(
        name="claim_concept",
        contract_version=VersionId("2026.04.20"),
        source_family="claim",
        source_field="concept",
        target_family="concept",
        required=True,
        many=False,
    )

    assert spec.contract_body() == {
        "source_family": "claim",
        "source_field": "concept",
        "target_family": "concept",
        "required": True,
        "many": False,
    }

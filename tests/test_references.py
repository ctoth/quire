from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

import msgspec
import pytest

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.families import FamilyDefinition, FamilyRegistry
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
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


class ConceptDoc(msgspec.Struct):
    artifact_id: str
    aliases: tuple[str, ...] = ()


class ClaimDoc(msgspec.Struct):
    artifact_id: str
    concept: str


class DemoFamily(str, Enum):
    CONCEPTS = "concepts"
    CLAIMS = "claims"


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


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


def test_cross_family_reference_index_integrates_with_bound_family_registry() -> None:
    concepts = ArtifactFamily(
        name="concepts",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=ConceptDoc,
        placement=FlatYamlPlacement("concepts", str),
    )
    claims = ArtifactFamily(
        name="claims",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=ClaimDoc,
        placement=FlatYamlPlacement("claims", str),
    )
    registry = FamilyRegistry(
        name="demo",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        families=(
            FamilyDefinition(
                key=DemoFamily.CONCEPTS,
                name="concepts",
                contract_version=VersionId("2026.04.18", allow_placeholder=False),
                artifact_family=concepts,
            ),
            FamilyDefinition(
                key=DemoFamily.CLAIMS,
                name="claims",
                contract_version=VersionId("2026.04.18", allow_placeholder=False),
                artifact_family=claims,
                foreign_keys=(
                    ForeignKeySpec(
                        name="claim_concept",
                        contract_version=VersionId("2026.04.18", allow_placeholder=False),
                        source_family="claims",
                        source_field="concept",
                        target_family="concepts",
                    ),
                ),
            ),
        ),
    )
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    with bound.transact(message="seed families") as transaction:
        transaction.concepts.save(
            "concept:mass",
            ConceptDoc("concept:mass", ("mass", "m")),
        )
        transaction.claims.save(
            "claim:1",
            ClaimDoc("claim:1", "mass"),
        )

    concept_records = {
        ref: bound.concepts.require(ref)
        for ref in bound.concepts.iter()
    }
    concept_lookup = build_reference_lookup(
        concept_records.values(),
        target_id=lambda concept: concept.artifact_id,
        keys=lambda concept: concept.aliases,
    )
    cross_family = CrossFamilyReferenceIndex(
        families={
            "concepts": ReferenceIndex(
                family="concepts",
                records_by_id=concept_records,
                lookup=concept_lookup,
            ),
        },
    )
    claim = bound.claims.require("claim:1")

    assert cross_family.resolve_id("concepts", claim.concept) == "concept:mass"

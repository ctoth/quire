from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

import msgspec
import pytest
from hypothesis import given, strategies as st

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.families import FamilyDefinition, FamilyRegistry
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.references import (
    AmbiguousReferenceError,
    CrossFamilyReferenceIndex,
    FamilyReferenceIndex,
    ForeignKeySpec,
    ForeignKeyValidationError,
    MissingReferenceError,
    ReferenceKey,
    ReferenceIndex,
    build_reference_lookup,
    validate_foreign_key,
)
from quire.versions import VersionId


@dataclass(frozen=True)
class Record:
    artifact_id: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class LogicalId:
    namespace: str
    value: str


@dataclass(frozen=True)
class RichRecord:
    artifact_id: str
    logical_ids: tuple[LogicalId, ...] = ()
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


def test_reference_index_resolve_id_signals_ambiguous_keys() -> None:
    index = _index()

    with pytest.raises(AmbiguousReferenceError) as exc_info:
        index.resolve_id("F0")

    assert exc_info.value.reference == "F0"
    assert exc_info.value.candidates == ("concept:1", "concept:2")
    assert index.exists("F0") is False


def test_family_reference_index_resolves_declarative_field_and_format_keys() -> None:
    records = (
        RichRecord(
            artifact_id="claim:1",
            logical_ids=(
                LogicalId(namespace="paper", value="c1"),
                LogicalId(namespace="paper", value="main"),
            ),
            aliases=("alpha",),
        ),
        RichRecord(
            artifact_id="claim:2",
            logical_ids=(LogicalId(namespace="paper", value="c2"),),
        ),
    )

    index = FamilyReferenceIndex.from_records(
        records,
        artifact_id=lambda record: record.artifact_id,
        keys=(
            ReferenceKey.field("artifact_id"),
            ReferenceKey.field("logical_ids[].value"),
            ReferenceKey.format("{namespace}:{value}", from_field="logical_ids[]"),
            lambda record: record.aliases,
        ),
    )

    assert index.require_id("claim:1") == "claim:1"
    assert index.require_id("c1") == "claim:1"
    assert index.require_id("paper:main") == "claim:1"
    assert index.resolve_id("alpha") == "claim:1"
    assert index.resolve_id("missing") is None
    with pytest.raises(MissingReferenceError) as exc_info:
        index.require_id("missing")
    assert exc_info.value.reference == "missing"


def test_reference_key_rejects_malformed_field_paths_at_declaration_time() -> None:
    with pytest.raises(ValueError, match="field path"):
        ReferenceKey.field("logical_ids[].")


def test_family_reference_index_reports_duplicate_key_ambiguity_at_build_time() -> None:
    records = (
        RichRecord("claim:1", aliases=("shared",)),
        RichRecord("claim:2", aliases=("shared",)),
    )

    with pytest.raises(AmbiguousReferenceError) as exc_info:
        FamilyReferenceIndex.from_records(
            records,
            artifact_id=lambda record: record.artifact_id,
            keys=(lambda record: record.aliases,),
        )

    assert exc_info.value.reference == "shared"
    assert exc_info.value.candidates == ("claim:1", "claim:2")


def test_family_reference_index_deduplicates_repeated_keys_for_same_artifact() -> None:
    index = FamilyReferenceIndex.from_records(
        (RichRecord("claim:1", aliases=("same", "same")),),
        artifact_id=lambda record: record.artifact_id,
        keys=(ReferenceKey.field("artifact_id"), lambda record: record.aliases),
    )

    assert index.require_id("same") == "claim:1"


def test_family_reference_index_rejects_alias_colliding_with_another_artifact_id() -> None:
    records = (
        RichRecord("claim:1"),
        RichRecord("claim:2", aliases=("claim:1",)),
    )

    with pytest.raises(AmbiguousReferenceError) as exc_info:
        FamilyReferenceIndex.from_records(
            records,
            artifact_id=lambda record: record.artifact_id,
            keys=(lambda record: record.aliases,),
        )

    assert exc_info.value.reference == "claim:1"
    assert exc_info.value.candidates == ("claim:1", "claim:2")


@given(
    st.dictionaries(
        keys=st.from_regex(r"id[0-9]{1,4}", fullmatch=True),
        values=st.lists(
            st.from_regex(r"alias[0-9]{1,4}", fullmatch=True),
            min_size=1,
            max_size=3,
            unique=True,
        ),
        min_size=1,
        max_size=20,
    ).filter(lambda mapping: len({alias for aliases in mapping.values() for alias in aliases}) == sum(len(aliases) for aliases in mapping.values())),
)
def test_family_reference_index_resolves_generated_unique_aliases(alias_map: dict[str, list[str]]) -> None:
    records = tuple(RichRecord(artifact_id, aliases=tuple(aliases)) for artifact_id, aliases in alias_map.items())

    index = FamilyReferenceIndex.from_records(
        records,
        artifact_id=lambda record: record.artifact_id,
        keys=(lambda record: record.aliases,),
    )

    for artifact_id, aliases in alias_map.items():
        assert index.require_id(artifact_id) == artifact_id
        for alias in aliases:
            assert index.require_id(alias) == artifact_id


@given(
    first_id=st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=1,
        max_size=12,
    ).filter(lambda value: value != "shared"),
    second_id=st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=1,
        max_size=12,
    ).filter(lambda value: value != "shared"),
)
def test_family_reference_index_rejects_generated_duplicate_aliases(first_id: str, second_id: str) -> None:
    if first_id == second_id:
        return
    records = (
        RichRecord(first_id, aliases=("shared",)),
        RichRecord(second_id, aliases=("shared",)),
    )

    with pytest.raises(AmbiguousReferenceError):
        FamilyReferenceIndex.from_records(
            records,
            artifact_id=lambda record: record.artifact_id,
            keys=(lambda record: record.aliases,),
        )


@given(
    owner_id=st.from_regex(r"id[0-9]{1,4}", fullmatch=True),
    alias_owner_id=st.from_regex(r"other[0-9]{1,4}", fullmatch=True),
)
def test_family_reference_index_rejects_generated_alias_artifact_id_collisions(
    owner_id: str,
    alias_owner_id: str,
) -> None:
    records = (
        RichRecord(owner_id),
        RichRecord(alias_owner_id, aliases=(owner_id,)),
    )

    with pytest.raises(AmbiguousReferenceError):
        FamilyReferenceIndex.from_records(
            records,
            artifact_id=lambda record: record.artifact_id,
            keys=(lambda record: record.aliases,),
        )


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


def test_foreign_key_validation_enforces_required_and_target_existence() -> None:
    spec = ForeignKeySpec(
        name="claim_concept",
        contract_version=VersionId("2026.04.20"),
        source_family="claim",
        source_field="concept",
        target_family="concept",
    )
    index = _index()

    assert validate_foreign_key(spec, {"concept": "pressure"}, index) == ("concept:3",)

    with pytest.raises(ForeignKeyValidationError, match="required foreign key"):
        validate_foreign_key(spec, {}, index)

    with pytest.raises(ForeignKeyValidationError, match="does not resolve"):
        validate_foreign_key(spec, {"concept": "missing"}, index)


def test_foreign_key_validation_enforces_many_cardinality() -> None:
    spec = ForeignKeySpec(
        name="claim_concept",
        contract_version=VersionId("2026.04.20"),
        source_family="claim",
        source_field="concepts[]",
        target_family="concept",
        many=False,
    )

    with pytest.raises(ForeignKeyValidationError, match="expected one value"):
        validate_foreign_key(spec, {"concepts": ["pressure", "concept:1"]}, _index())


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

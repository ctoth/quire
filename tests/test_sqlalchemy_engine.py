from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import (
    CharterField,
    CharterRelationship,
    FamilyCharter,
    charter_catalog,
)
from quire.families import FamilyDefinition
from quire.references import ForeignKeySpec
from quire.sqlalchemy_schema import build_sqlalchemy_schema
from quire.sqlalchemy_store import (
    create_sqlalchemy_store,
    readonly_session,
    validate_sqlalchemy_store,
    writable_session,
)
from quire.versions import VersionId


@dataclass(frozen=True)
class SourceTrust:
    score: float
    note: str


class ClaimStatus(str, Enum):
    DRAFT = "draft"
    ACCEPTED = "accepted"


class Source:
    def __init__(self, id: str, metadata: str, trust: SourceTrust) -> None:
        self.id = id
        self.metadata = metadata
        self.trust = trust


class Concept:
    def __init__(self, id: str, label: str) -> None:
        self.id = id
        self.label = label


class Claim:
    def __init__(self, id: str, source_id: str, text: str, status: ClaimStatus) -> None:
        self.id = id
        self.source_id = source_id
        self.text = text
        self.status = status


class ClaimConceptLink:
    def __init__(
        self,
        claim_id: str,
        concept_id: str,
        role: str,
        ordinal: int,
        binding_name: str,
    ) -> None:
        self.claim_id = claim_id
        self.concept_id = concept_id
        self.role = role
        self.ordinal = ordinal
        self.binding_name = binding_name


def test_generated_tables_catalog_and_mappings_round_trip(tmp_path: Path) -> None:
    schema = build_sqlalchemy_schema(_catalog())

    source_table = schema.table("sources")
    assert "metadata" in source_table.c
    assert "ix_sources_metadata" in {index.name for index in source_table.indexes}
    assert {column.name for column in schema.table("claims").foreign_keys} == {"source_id"}
    assert {
        constraint.name
        for constraint in schema.table("claim_concept_links").constraints
        if constraint.name
    } >= {"fk_claim_link_claim", "fk_claim_link_concept"}

    store_path = tmp_path / "derived.sqlite"
    create_sqlalchemy_store(store_path, schema)
    validate_sqlalchemy_store(store_path, schema)

    with writable_session(store_path, schema) as session:
        source = Source("source:1", "reserved field survives", SourceTrust(0.9, "curated"))
        concept = Concept("concept:mass", "Mass")
        claim = Claim("claim:1", "source:1", "Mass is invariant.", ClaimStatus.ACCEPTED)
        link = ClaimConceptLink("claim:1", "concept:mass", "subject", 0, "m")
        session.add_all((source, concept, claim, link))
        session.commit()

    with readonly_session(store_path, schema) as session:
        claim = session.get(Claim, "claim:1")
        assert claim is not None
        assert claim.status is ClaimStatus.ACCEPTED
        assert claim.source.metadata == "reserved field survives"
        assert claim.source.trust == SourceTrust(0.9, "curated")
        assert claim.concept_links[0].concept.label == "Mass"
        assert claim.concept_links[0].binding_name == "m"

        session.add(Concept("concept:force", "Force"))
        with pytest.raises(OperationalError):
            session.commit()


def test_schema_catalog_validation_detects_missing_columns(tmp_path: Path) -> None:
    schema = build_sqlalchemy_schema(_catalog())
    store_path = tmp_path / "broken.sqlite"
    create_sqlalchemy_store(store_path, schema)

    with writable_session(store_path, schema) as session:
        session.execute(schema.table("sources").delete())
        session.execute(schema.table("sources").drop(schema.metadata))

    with pytest.raises(ValueError, match="missing table"):
        validate_sqlalchemy_store(store_path, schema)


def test_schema_hash_changes_when_charter_shape_changes() -> None:
    base = build_sqlalchemy_schema(_catalog())
    changed = build_sqlalchemy_schema(
        charter_catalog(
            *_charters(
                source_extra_field=CharterField("publisher", str, nullable=True),
            )
        )
    )

    assert base.catalog_hash != changed.catalog_hash


def _catalog() -> Any:
    return charter_catalog(*_charters())


def _charters(
    *,
    source_extra_field: CharterField | None = None,
) -> tuple[FamilyCharter, ...]:
    sources = _family("sources", Source)
    claims = _family("claims", Claim)
    concepts = _family("concepts", Concept)
    links = _family("claim_concept_links", ClaimConceptLink)

    source_fields = [
        CharterField("id", str, primary_key=True, nullable=False),
        CharterField("metadata", str, nullable=False, index=True),
        CharterField("trust", SourceTrust, nullable=False, json_value_object=True),
    ]
    if source_extra_field is not None:
        source_fields.append(source_extra_field)

    return (
        FamilyCharter(
            family=sources,
            model=Source,
            fields=tuple(source_fields),
            relationships=(
                CharterRelationship(
                    "claims",
                    target_family="claims",
                    foreign_key="source_id",
                    back_populates="source",
                ),
            ),
        ),
        FamilyCharter(
            family=concepts,
            model=Concept,
            fields=(
                CharterField("id", str, primary_key=True, nullable=False),
                CharterField("label", str, nullable=False, unique=True, search=True),
            ),
            relationships=(
                CharterRelationship(
                    "claim_links",
                    target_family="claim_concept_links",
                    foreign_key="concept_id",
                    back_populates="concept",
                ),
            ),
        ),
        FamilyCharter(
            family=claims,
            model=Claim,
            fields=(
                CharterField("id", str, primary_key=True, nullable=False),
                CharterField(
                    "source_id",
                    str,
                    nullable=False,
                    foreign_key=_foreign_key("claim_source", "claims", "source_id", "sources"),
                ),
                CharterField("text", str, nullable=False, search=True),
                CharterField("status", ClaimStatus, nullable=False),
            ),
            relationships=(
                CharterRelationship(
                    "source",
                    target_family="sources",
                    foreign_key="source_id",
                    back_populates="claims",
                    uselist=False,
                ),
                CharterRelationship(
                    "concept_links",
                    target_family="claim_concept_links",
                    foreign_key="claim_id",
                    back_populates="claim",
                    association_object=True,
                ),
            ),
        ),
        FamilyCharter(
            family=links,
            model=ClaimConceptLink,
            fields=(
                CharterField(
                    "claim_id",
                    str,
                    primary_key=True,
                    nullable=False,
                    foreign_key=_foreign_key("claim_link_claim", "claim_concept_links", "claim_id", "claims"),
                ),
                CharterField(
                    "concept_id",
                    str,
                    primary_key=True,
                    nullable=False,
                    foreign_key=_foreign_key(
                        "claim_link_concept",
                        "claim_concept_links",
                        "concept_id",
                        "concepts",
                    ),
                ),
                CharterField("role", str, nullable=False),
                CharterField("ordinal", int, nullable=False),
                CharterField("binding_name", str, nullable=False),
            ),
            relationships=(
                CharterRelationship(
                    "claim",
                    target_family="claims",
                    foreign_key="claim_id",
                    back_populates="concept_links",
                    uselist=False,
                ),
                CharterRelationship(
                    "concept",
                    target_family="concepts",
                    foreign_key="concept_id",
                    back_populates="claim_links",
                    uselist=False,
                ),
            ),
        ),
    )


def _family(name: str, model: type[object]) -> FamilyDefinition[Any, Any, Any, Any]:
    return FamilyDefinition(
        key=name,
        name=name,
        contract_version=VersionId("2026.05.18", allow_placeholder=False),
        artifact_family=ArtifactFamily(
            name=name,
            contract_version=VersionId("2026.05.18", allow_placeholder=False),
            doc_type=model,
            placement=FlatYamlPlacement(name, str),
        ),
        identity_field="id",
    )


def _foreign_key(
    name: str,
    source_family: str,
    source_field: str,
    target_family: str,
) -> ForeignKeySpec:
    return ForeignKeySpec(
        name=name,
        contract_version=VersionId("2026.05.18", allow_placeholder=False),
        source_family=source_family,
        source_field=source_field,
        target_family=target_family,
    )

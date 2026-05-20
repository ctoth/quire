from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import (
    CharterField,
    CharterFtsIndex,
    CharterRelationship,
    CharterVectorCache,
    FamilyCharter,
    charter_catalog,
)
from quire.families import FamilyDefinition
from quire.references import ForeignKeySpec
from quire.sqlalchemy_schema import build_sqlalchemy_schema
from quire.sqlalchemy_store import (
    create_sqlalchemy_store,
    populate_fts_index,
    readonly_session,
    search_fts_index,
    validate_sqlalchemy_store,
    writable_session,
)
from quire.sqlite_vec_store import (
    SqlAlchemyVecEntityStore,
    SqlAlchemyVecRegistry,
    SqlAlchemyVecSnapshotStore,
)
from quire.versions import VersionId


def _serialize_float32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


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


class SearchConcept:
    def __init__(
        self,
        id: str,
        label: str,
        symbol: str,
        aliases: str,
        normalized_text: str,
    ) -> None:
        self.id = id
        self.label = label
        self.symbol = symbol
        self.aliases = aliases
        self.normalized_text = normalized_text


class SearchClaim:
    def __init__(
        self,
        id: str,
        text_payload: str,
        equation_text: str,
        provenance_text: str,
        rendered_text: str,
    ) -> None:
        self.id = id
        self.text_payload = text_payload
        self.equation_text = equation_text
        self.provenance_text = provenance_text
        self.rendered_text = rendered_text


class JoinedSearchClaim:
    def __init__(self, id: str, seq: int) -> None:
        self.id = id
        self.seq = seq


class JoinedSearchClaimTextPayload:
    def __init__(
        self,
        claim_id: str,
        statement: str,
        conditions_cel: str,
        expression: str,
    ) -> None:
        self.claim_id = claim_id
        self.statement = statement
        self.conditions_cel = conditions_cel
        self.expression = expression


class AliasWithoutDatabasePrimaryKey:
    def __init__(self, owner_id: str, alias_name: str, source: str) -> None:
        self.owner_id = owner_id
        self.alias_name = alias_name
        self.source = source


class VectorEntity:
    def __init__(self, id: str, seq: int, content_hash: str, text: str) -> None:
        self.id = id
        self.seq = seq
        self.content_hash = content_hash
        self.text = text


@dataclass(frozen=True)
class DemoEmbeddingIdentity:
    provider: str = "demo"
    model_name: str = "demo-model"
    model_version: str = "1"
    content_digest: str = "demo-content"
    identity_hash: str = "demo-hash"


def test_generated_tables_catalog_and_mappings_round_trip(tmp_path: Path) -> None:
    schema = build_sqlalchemy_schema(_catalog())

    source_table = schema.table("sources")
    assert "metadata" in source_table.c
    assert "ix_sources_metadata" in {index.name for index in source_table.indexes}
    assert {foreign_key.parent.name for foreign_key in schema.table("claims").foreign_keys} == {"source_id"}
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
        session.execute(text("DROP TABLE sources"))
        session.commit()

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


def test_mapper_supports_tables_without_database_primary_keys(tmp_path: Path) -> None:
    schema = build_sqlalchemy_schema(_no_database_primary_key_catalog())
    alias_table = schema.table("alias_without_database_primary_key")
    assert not alias_table.primary_key.columns

    store_path = tmp_path / "no-primary-key.sqlite"
    create_sqlalchemy_store(store_path, schema)
    with writable_session(store_path, schema) as session:
        session.add_all(
            (
                AliasWithoutDatabasePrimaryKey("concept:mass", "mass", "label"),
                AliasWithoutDatabasePrimaryKey("concept:mass", "m", "symbol"),
            )
        )
        session.commit()

    with readonly_session(store_path, schema) as session:
        rows = (
            session.query(AliasWithoutDatabasePrimaryKey)
            .order_by(text("owner_id"), text("alias_name"))
            .all()
        )

    assert [(row.owner_id, row.alias_name, row.source) for row in rows] == [
        ("concept:mass", "m", "symbol"),
        ("concept:mass", "mass", "label"),
    ]


def test_fts_declarations_create_populate_and_query_with_sessions(tmp_path: Path) -> None:
    schema = build_sqlalchemy_schema(_search_catalog())
    store_path = tmp_path / "search.sqlite"
    create_sqlalchemy_store(store_path, schema)
    validate_sqlalchemy_store(store_path, schema)

    with writable_session(store_path, schema) as session:
        session.add_all(
            (
                SearchConcept(
                    "concept:mass",
                    "Mass",
                    "m",
                    "inertial mass gravitational mass",
                    "measure of matter resistance to acceleration",
                ),
                SearchConcept(
                    "concept:force",
                    "Force",
                    "F",
                    "interaction push pull",
                    "cause of acceleration",
                ),
                SearchClaim(
                    "claim:newton-2",
                    "Force equals mass times acceleration.",
                    "F = m a",
                    "Newton mechanics source",
                    "A net force accelerates mass.",
                ),
                SearchClaim(
                    "claim:energy",
                    "Energy is conserved in an isolated system.",
                    "dE/dt = 0",
                    "conservation law source",
                    "Total energy remains constant.",
                ),
            )
        )
        session.commit()
        populate_fts_index(session, "concept_search")
        populate_fts_index(session, "claim_search")
        session.commit()

    with readonly_session(store_path, schema) as session:
        concept_hits = search_fts_index(session, "concept_search", "inertial")
        claim_hits = search_fts_index(session, "claim_search", "accelerates")

    assert [(hit.entity_id, isinstance(hit.rank, float)) for hit in concept_hits] == [
        ("concept:mass", True)
    ]
    assert [(hit.entity_id, isinstance(hit.rank, float)) for hit in claim_hits] == [
        ("claim:newton-2", True)
    ]


def test_fts_source_query_can_populate_joined_index_with_custom_key(tmp_path: Path) -> None:
    schema = build_sqlalchemy_schema(_joined_search_catalog())
    store_path = tmp_path / "joined-search.sqlite"
    create_sqlalchemy_store(store_path, schema)
    validate_sqlalchemy_store(store_path, schema)

    with writable_session(store_path, schema) as session:
        session.add_all(
            (
                JoinedSearchClaim("claim:gravity", 1),
                JoinedSearchClaimTextPayload(
                    "claim:gravity",
                    "Gravity curves spacetime.",
                    '["relativity", "orbit"]',
                    "G m_1 m_2 / r^2",
                ),
                JoinedSearchClaim("claim:energy", 2),
                JoinedSearchClaimTextPayload(
                    "claim:energy",
                    "Energy is conserved.",
                    '["conservation"]',
                    "dE/dt = 0",
                ),
            )
        )
        session.commit()
        populate_fts_index(session, "claim_search_joined")
        session.commit()

    with readonly_session(store_path, schema) as session:
        statement_hits = search_fts_index(session, "claim_search_joined", "spacetime")
        condition_hits = search_fts_index(session, "claim_search_joined", "orbit")

    assert [hit.entity_id for hit in statement_hits] == ["claim:gravity"]
    assert [hit.entity_id for hit in condition_hits] == ["claim:gravity"]


def test_vector_cache_create_insert_search_snapshot_and_restore(tmp_path: Path) -> None:
    schema = build_sqlalchemy_schema(_vector_catalog())
    cache = schema.vector_cache("entity_embeddings")
    identity = DemoEmbeddingIdentity()
    store_path = tmp_path / "vectors.sqlite"
    create_sqlalchemy_store(store_path, schema)
    validate_sqlalchemy_store(store_path, schema)

    with writable_session(store_path, schema) as session:
        session.add_all(
            (
                VectorEntity("entity:near", 1, "hash-near", "near text"),
                VectorEntity("entity:far", 2, "hash-far", "far text"),
            )
        )
        session.commit()
        vector_store = SqlAlchemyVecEntityStore(session.session.connection(), cache)
        vector_store.prepare_model(identity, created_at="2026-05-20T00:00:00Z")
        vector_store.save_embedding(
            model_identity=identity,
            entity_id="entity:near",
            seq=1,
            content_hash="hash-near",
            vector_blob=_serialize_float32([0.1, 0.2, 0.3]),
            embedded_at="2026-05-20T00:00:01Z",
        )
        vector_store.save_embedding(
            model_identity=identity,
            entity_id="entity:far",
            seq=2,
            content_hash="hash-far",
            vector_blob=_serialize_float32([0.9, 0.9, 0.9]),
            embedded_at="2026-05-20T00:00:02Z",
        )
        assert vector_store.vector_for(identity, 1) == _serialize_float32([0.1, 0.2, 0.3])
        session.commit()

    with readonly_session(store_path, schema) as session:
        vector_store = SqlAlchemyVecEntityStore(session.session.connection(), cache)
        rows = vector_store.similar_entities(
            model_identity=identity,
            query_vector=_serialize_float32([0.1, 0.2, 0.31]),
            k=1,
        )
        snapshot = SqlAlchemyVecSnapshotStore(
            session.session.connection(),
            tuple(schema.vector_caches.values()),
        ).extract()
        models = SqlAlchemyVecRegistry(session.session.connection()).get_registered_models()

    assert rows[0]["entity_id"] == "entity:near"
    assert snapshot is not None
    assert models[0]["model_identity_hash"] == identity.identity_hash

    restored_path = tmp_path / "restored.sqlite"
    create_sqlalchemy_store(restored_path, schema)
    with writable_session(restored_path, schema) as session:
        session.add_all(
            (
                VectorEntity("entity:near", 10, "hash-near", "near text"),
                VectorEntity("entity:far", 20, "hash-far", "far text"),
            )
        )
        session.commit()
        report = SqlAlchemyVecSnapshotStore(
            session.session.connection(),
            tuple(schema.vector_caches.values()),
        ).restore(snapshot)
        session.commit()

    assert report.restored == 2
    with readonly_session(restored_path, schema) as session:
        vector_store = SqlAlchemyVecEntityStore(session.session.connection(), cache)
        rows = vector_store.similar_entities(
            model_identity=identity,
            query_vector=_serialize_float32([0.1, 0.2, 0.31]),
            k=1,
        )
    assert rows[0]["entity_id"] == "entity:near"


def _catalog() -> Any:
    return charter_catalog(*_charters())


def _search_catalog() -> Any:
    concepts = _family("search_concepts", SearchConcept)
    claims = _family("search_claims", SearchClaim)
    return charter_catalog(
        FamilyCharter(
            family=concepts,
            model=SearchConcept,
            fields=(
                CharterField("id", str, primary_key=True, nullable=False),
                CharterField("label", str, nullable=False),
                CharterField("symbol", str, nullable=False),
                CharterField("aliases", str, nullable=False),
                CharterField("normalized_text", str, nullable=False),
            ),
            fts_indexes=(
                CharterFtsIndex(
                    "concept_search",
                    entity_id_field="id",
                    fields=("label", "symbol", "aliases", "normalized_text"),
                    tokenize="porter unicode61",
                ),
            ),
        ),
        FamilyCharter(
            family=claims,
            model=SearchClaim,
            fields=(
                CharterField("id", str, primary_key=True, nullable=False),
                CharterField("text_payload", str, nullable=False),
                CharterField("equation_text", str, nullable=False),
                CharterField("provenance_text", str, nullable=False),
                CharterField("rendered_text", str, nullable=False),
            ),
            fts_indexes=(
                CharterFtsIndex(
                    "claim_search",
                    entity_id_field="id",
                    fields=(
                        "text_payload",
                        "equation_text",
                        "provenance_text",
                        "rendered_text",
                    ),
                    tokenize="porter unicode61",
                ),
            ),
        ),
    )


def _joined_search_catalog() -> Any:
    claims = _family("joined_search_claims", JoinedSearchClaim)
    payloads = _family(
        "joined_search_claim_text_payload",
        JoinedSearchClaimTextPayload,
    )
    return charter_catalog(
        FamilyCharter(
            family=claims,
            model=JoinedSearchClaim,
            fields=(
                CharterField("id", str, primary_key=True, nullable=False),
                CharterField("seq", int, nullable=False),
            ),
            fts_indexes=(
                CharterFtsIndex(
                    "claim_search_joined",
                    entity_id_field="claim_id",
                    fields=("statement", "conditions", "expression"),
                    source_query="""
                        SELECT
                            c.id AS claim_id,
                            COALESCE(t.statement, '') AS statement,
                            COALESCE(
                                (
                                    SELECT group_concat(value, ' ')
                                    FROM json_each(t.conditions_cel)
                                ),
                                ''
                            ) AS conditions,
                            COALESCE(t.expression, '') AS expression
                        FROM joined_search_claims c
                        JOIN joined_search_claim_text_payload t ON t.claim_id = c.id
                        ORDER BY c.seq
                    """,
                ),
            ),
        ),
        FamilyCharter(
            family=payloads,
            model=JoinedSearchClaimTextPayload,
            fields=(
                CharterField("claim_id", str, primary_key=True, nullable=False),
                CharterField("statement", str, nullable=False),
                CharterField("conditions_cel", str, nullable=False),
                CharterField("expression", str, nullable=False),
            ),
        ),
    )


def _no_database_primary_key_catalog() -> Any:
    aliases = _family(
        "alias_without_database_primary_key",
        AliasWithoutDatabasePrimaryKey,
    )
    return charter_catalog(
        FamilyCharter(
            family=aliases,
            model=AliasWithoutDatabasePrimaryKey,
            fields=(
                CharterField("owner_id", str, nullable=False),
                CharterField("alias_name", str, nullable=False),
                CharterField("source", str, nullable=False),
            ),
        )
    )


def _vector_catalog() -> Any:
    entities = _family("vector_entities", VectorEntity)
    return charter_catalog(
        FamilyCharter(
            family=entities,
            model=VectorEntity,
            fields=(
                CharterField("id", str, primary_key=True, nullable=False),
                CharterField("seq", int, nullable=False, unique=True),
                CharterField("content_hash", str, nullable=False),
                CharterField("text", str, nullable=False),
            ),
            vector_caches=(
                CharterVectorCache(
                    "entity_embeddings",
                    table="entity_vec_{model_identity_hash}",
                    dimensions=3,
                    entity_id_field="id",
                    source_seq_field="seq",
                    source_content_hash_field="content_hash",
                ),
            ),
        )
    )


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

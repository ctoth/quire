from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    MetaData,
    Table,
    Text,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, registry, relationship
from sqlalchemy.types import TypeDecorator

from quire.derived_store import DerivedStoreManager


class GenericEnumText(TypeDecorator[Enum]):
    impl = Text
    cache_ok = True

    def __init__(self, enum_type: type[Enum]) -> None:
        super().__init__()
        self.enum_type = enum_type

    def process_bind_param(self, value: object, dialect: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_type):
            return str(value.value)
        return str(self.enum_type(value).value)

    def process_result_value(self, value: object, dialect: object) -> Enum | None:
        if value is None:
            return None
        return self.enum_type(value)


class GenericJsonValue(TypeDecorator[Any]):
    impl = Text
    cache_ok = True

    def __init__(self, value_type: type[Any] | None = None) -> None:
        super().__init__()
        self.value_type = value_type

    def process_bind_param(self, value: object, dialect: object) -> str | None:
        if value is None:
            return None
        import json

        payload = asdict(value) if is_dataclass(value) else value
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def process_result_value(self, value: object, dialect: object) -> object | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"expected JSON text, got {type(value).__name__}")
        import json

        payload = json.loads(value)
        if self.value_type is None:
            return payload
        return self.value_type(**payload)


class SourceOrigin(Enum):
    PAPER = "paper"
    DATASET = "dataset"


class ClaimConceptRole(Enum):
    OUTPUT = "output"
    INPUT = "input"


@dataclass(frozen=True)
class SourceTrust:
    score: float
    method: str


@dataclass
class Source:
    id: str
    metadata: dict[str, object]
    origin: SourceOrigin
    trust: SourceTrust


class Claim:
    def __init__(self, id: str, source_id: str, text: str) -> None:
        self.id = id
        self.source_id = source_id
        self.text = text
        self.concept_links: list[ClaimConceptLink] = []


class Concept:
    def __init__(self, id: str, label: str) -> None:
        self.id = id
        self.label = label
        self.claim_links: list[ClaimConceptLink] = []


@dataclass
class ClaimConceptLink:
    claim_id: str
    concept_id: str
    role: ClaimConceptRole
    ordinal: int
    binding_name: str | None = None


@dataclass(frozen=True)
class ProofField:
    name: str
    python_type: type[Any]
    primary_key: bool = False
    nullable: bool = True
    foreign_key: str | None = None
    json_value_type: type[Any] | None = None
    enum_type: type[Enum] | None = None


@dataclass(frozen=True)
class ProofTable:
    name: str
    model: type[Any]
    fields: tuple[ProofField, ...]
    indexes: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _table_from_ir(metadata: MetaData, declaration: ProofTable) -> Table:
    columns: list[Column[Any]] = []
    for field_def in declaration.fields:
        sql_type: object
        if field_def.json_value_type is not None:
            sql_type = GenericJsonValue(field_def.json_value_type)
        elif field_def.enum_type is not None:
            sql_type = GenericEnumText(field_def.enum_type)
        else:
            sql_type = Text() if field_def.python_type is str else field_def.python_type
        args: list[object] = [field_def.name, sql_type]
        if field_def.foreign_key is not None:
            args.append(ForeignKey(field_def.foreign_key))
        columns.append(
            Column(
                *args,
                primary_key=field_def.primary_key,
                nullable=field_def.nullable,
                info={"python_type": field_def.python_type.__name__},
            )
        )
    table = Table(declaration.name, metadata, *columns)
    for index_name, column_names in declaration.indexes:
        Index(index_name, *(table.c[name] for name in column_names))
    return table


def _map_proof_model(
    mapper_registry: registry,
    declaration: ProofTable,
    table: Table,
    **properties: object,
) -> None:
    mapper_registry.map_imperatively(declaration.model, table, properties=properties)


def _proof_declarations() -> tuple[ProofTable, ...]:
    return (
        ProofTable(
            name="source",
            model=Source,
            fields=(
                ProofField("id", str, primary_key=True, nullable=False),
                ProofField("metadata", dict, nullable=False, json_value_type=dict),
                ProofField("origin", SourceOrigin, nullable=False, enum_type=SourceOrigin),
                ProofField("trust", SourceTrust, nullable=False, json_value_type=SourceTrust),
            ),
        ),
        ProofTable(
            name="claim",
            model=Claim,
            fields=(
                ProofField("id", str, primary_key=True, nullable=False),
                ProofField("source_id", str, nullable=False, foreign_key="source.id"),
                ProofField("text", str, nullable=False),
            ),
            indexes=(("idx_claim_source", ("source_id",)),),
        ),
        ProofTable(
            name="concept",
            model=Concept,
            fields=(
                ProofField("id", str, primary_key=True, nullable=False),
                ProofField("label", str, nullable=False),
            ),
        ),
        ProofTable(
            name="claim_concept_link",
            model=ClaimConceptLink,
            fields=(
                ProofField("claim_id", str, primary_key=True, nullable=False, foreign_key="claim.id"),
                ProofField("concept_id", str, primary_key=True, nullable=False, foreign_key="concept.id"),
                ProofField("role", ClaimConceptRole, nullable=False, enum_type=ClaimConceptRole),
                ProofField("ordinal", int, nullable=False),
                ProofField("binding_name", str),
            ),
        ),
    )


def _mapped_engine() -> tuple[Engine, registry, dict[str, Table]]:
    mapper_registry = registry()
    metadata = mapper_registry.metadata
    declarations = _proof_declarations()
    tables = {declaration.name: _table_from_ir(metadata, declaration) for declaration in declarations}

    _map_proof_model(
        mapper_registry,
        declarations[0],
        tables["source"],
        claims=relationship(Claim, back_populates="source"),
    )
    _map_proof_model(
        mapper_registry,
        declarations[1],
        tables["claim"],
        source=relationship(Source, back_populates="claims"),
        concept_links=relationship(
            ClaimConceptLink,
            back_populates="claim",
            cascade="all, delete-orphan",
        ),
    )
    _map_proof_model(
        mapper_registry,
        declarations[2],
        tables["concept"],
        claim_links=relationship(ClaimConceptLink, back_populates="concept"),
    )
    _map_proof_model(
        mapper_registry,
        declarations[3],
        tables["claim_concept_link"],
        claim=relationship(Claim, back_populates="concept_links"),
        concept=relationship(Concept, back_populates="claim_links"),
    )

    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    return engine, mapper_registry, tables


def test_imperative_mapping_generated_tables_reserved_metadata_relationships_and_values() -> None:
    engine, mapper_registry, tables = _mapped_engine()
    try:
        source = Source(
            id="src:one",
            metadata={"title": "Proof Source", "rank": 1},
            origin=SourceOrigin.PAPER,
            trust=SourceTrust(score=0.92, method="reviewed"),
        )
        claim = Claim(id="claim:one", source_id=source.id, text="Water boils near 100 C.")
        concept = Concept(id="concept:water", label="water")
        link = ClaimConceptLink(
            claim_id=claim.id,
            concept_id=concept.id,
            role=ClaimConceptRole.OUTPUT,
            ordinal=0,
            binding_name="subject",
        )
        claim.concept_links.append(link)

        with Session(engine) as session:
            session.add_all([source, claim, concept])
            session.commit()

        inspector = inspect(engine)
        assert "source" in inspector.get_table_names()
        assert "metadata" in {column["name"] for column in inspector.get_columns("source")}
        assert "idx_claim_source" in {index["name"] for index in inspector.get_indexes("claim")}
        assert tables["source"].c["metadata"].info == {"python_type": "dict"}

        with Session(engine) as session:
            loaded = session.scalars(select(Source).where(Source.id == "src:one")).one()
            loaded_claim = session.scalars(select(Claim).where(Claim.id == "claim:one")).one()

        assert loaded.metadata == {"rank": 1, "title": "Proof Source"}
        assert loaded.origin is SourceOrigin.PAPER
        assert loaded.trust == SourceTrust(score=0.92, method="reviewed")
        assert loaded_claim.source is loaded
        assert loaded_claim.concept_links[0].concept.label == "water"
        assert loaded_claim.concept_links[0].role is ClaimConceptRole.OUTPUT
        assert loaded_claim.concept_links[0].ordinal == 0
        assert loaded_claim.concept_links[0].binding_name == "subject"
        assert hasattr(loaded_claim, "_sa_instance_state")
        assert SourceTrust.__dataclass_params__.frozen is True
        assert not Source.__dataclass_params__.frozen
    finally:
        mapper_registry.dispose()


def test_mapping_uses_registry_map_imperatively(monkeypatch: pytest.MonkeyPatch) -> None:
    mapper_registry = registry()
    metadata = mapper_registry.metadata
    declaration = _proof_declarations()[0]
    table = _table_from_ir(metadata, declaration)
    calls: list[tuple[type[Any], Table]] = []
    original = mapper_registry.map_imperatively

    def spy(model: type[Any], mapped_table: Table, **kwargs: object) -> object:
        calls.append((model, mapped_table))
        return original(model, mapped_table, **kwargs)

    monkeypatch.setattr(mapper_registry, "map_imperatively", spy)
    try:
        _map_proof_model(mapper_registry, declaration, table)
        assert calls == [(Source, table)]
    finally:
        mapper_registry.dispose()


def _build_sqlalchemy_store(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE marker (id TEXT PRIMARY KEY, value TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO marker (id, value) VALUES ('proof', 'ready')"))
    engine.dispose()


def _readonly_engine_from_path(path: Path) -> Engine:
    def connect_readonly() -> sqlite3.Connection:
        return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)

    return create_engine("sqlite://", creator=connect_readonly)


def test_readonly_sqlalchemy_session_opens_from_derived_store_handle(tmp_path: Path) -> None:
    manager = DerivedStoreManager(tmp_path / "derived")
    handle = manager.materialize(
        projection_id="proof.sqlalchemy",
        source_commit="a" * 40,
        content_hash="schema-proof",
        build=_build_sqlalchemy_store,
    )
    engine = _readonly_engine_from_path(handle.path)
    try:
        with Session(engine) as session:
            assert session.execute(text("SELECT value FROM marker WHERE id = 'proof'")).scalar_one() == "ready"
            with pytest.raises(OperationalError):
                session.execute(text("INSERT INTO marker (id, value) VALUES ('write', 'no')"))
    finally:
        engine.dispose()

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

import msgspec
import pytest
from hypothesis import given, strategies as st

from quire.artifacts import (
    ArtifactContext,
    ArtifactFamily,
    BranchPlacement,
    FixedFilePlacement,
    FlatYamlPlacement,
    HashScatteredYamlPlacement,
    NestedFlatYamlPlacement,
    SingletonFilePlacement,
    SubdirFixedFilePlacement,
)
from quire.contracts import check_contract_manifest
from quire.families import FamilyDeclaration, FamilyDefinition, FamilyIdentityPolicy, FamilyRegistry, _duplicates
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore, HeadMismatchError
from quire.references import AmbiguousReferenceError, ForeignKeySpec, ForeignKeyValidationError, ReferenceKey
from quire.versions import VersionId
from quire.contracts import contract_version


class DemoDocument(msgspec.Struct):
    name: str


class SerializedDemoDocument(msgspec.Struct):
    name: str


class IdentifiedDocument(msgspec.Struct):
    artifact_id: str
    aliases: tuple[str, ...] = ()


class ClaimWithConceptDocument(msgspec.Struct):
    artifact_id: str
    concept: str | None = None


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


@dataclass(frozen=True)
class DemoRef:
    name: str


@dataclass(frozen=True)
class NestedDemoRef:
    group: str
    name: str


class DemoFamily(str, Enum):
    CLAIMS = "claims"
    CONCEPTS = "concepts"
    NOTES = "notes"


def _artifact_family(name: str, namespace: str) -> ArtifactFamily[Owner, str, DemoDocument]:
    return ArtifactFamily(
        name=name,
        contract_version=contract_version("2026.04.18"),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement(namespace, str),
    )


def _identified_artifact_family(name: str, namespace: str) -> ArtifactFamily[Owner, str, IdentifiedDocument]:
    return ArtifactFamily(
        name=name,
        contract_version=contract_version("2026.04.18"),
        doc_type=IdentifiedDocument,
        placement=FlatYamlPlacement(namespace, str),
    )


def _claim_with_concept_family(name: str, namespace: str) -> ArtifactFamily[Owner, str, ClaimWithConceptDocument]:
    return ArtifactFamily(
        name=name,
        contract_version=contract_version("2026.04.18"),
        doc_type=ClaimWithConceptDocument,
        placement=FlatYamlPlacement(namespace, str),
    )


def _reference_registry(*, required: bool = True) -> FamilyRegistry[Owner, DemoFamily]:
    concepts = FamilyDefinition(
        key=DemoFamily.CONCEPTS,
        name="concepts",
        contract_version=contract_version("2026.04.18"),
        artifact_family=_identified_artifact_family("concepts_artifact", "concepts"),
        identity_field="artifact_id",
        reference_keys=(ReferenceKey.field("aliases[]"),),
    )
    claims = FamilyDefinition(
        key=DemoFamily.CLAIMS,
        name="claims",
        contract_version=contract_version("2026.04.18"),
        artifact_family=_claim_with_concept_family("claims_artifact", "claims"),
        identity_field="artifact_id",
        foreign_keys=(
            ForeignKeySpec(
                name="claim_concept",
                contract_version=contract_version("2026.04.18"),
                source_family="claims",
                source_field="concept",
                target_family="concepts",
                required=required,
            ),
        ),
    )
    return FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(claims, concepts),
    )


def _family_definition(
    key: DemoFamily,
    name: str,
    namespace: str,
    *,
    accessor: str | None = None,
    foreign_keys: tuple[ForeignKeySpec, ...] = (),
    identity_policy: FamilyIdentityPolicy | None = None,
    identity_field: str | None = None,
    reference_keys: tuple[ReferenceKey, ...] = (),
    metadata: dict[str, object] | None = None,
) -> FamilyDefinition[Owner, DemoFamily, str, DemoDocument]:
    return FamilyDefinition(
        key=key,
        name=name,
        accessor=accessor,
        contract_version=contract_version("2026.04.18"),
        artifact_family=_artifact_family(f"{name}_artifact", namespace),
        foreign_keys=foreign_keys,
        identity_policy=identity_policy,
        identity_field=identity_field,
        reference_keys=reference_keys,
        metadata=metadata,
    )


def _registry(
    *,
    claims_namespace: str = "claims",
    foreign_keys: tuple[ForeignKeySpec, ...] = (),
) -> FamilyRegistry[Owner, DemoFamily]:
    return FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(
            _family_definition(
                DemoFamily.CLAIMS,
                "claims",
                claims_namespace,
                foreign_keys=foreign_keys,
            ),
            _family_definition(DemoFamily.CONCEPTS, "concepts", "concepts"),
        ),
    )


def test_family_declaration_builds_common_placement_shapes_like_explicit_definitions() -> None:
    version = contract_version("2026.04.18")
    branch = BranchPlacement(policy="fixed", fixed_branch="archive")
    placements = (
        FlatYamlPlacement("books", DemoRef, ref_field="name", branch=branch),
        FlatYamlPlacement(
            "aliases",
            DemoRef,
            ref_field="name",
            codec="colon_to_double_underscore",
        ),
        HashScatteredYamlPlacement(
            "articles",
            DemoRef,
            ref_field="name",
            codec="base64url",
            filename_mode="encoded_ref",
        ),
        FixedFilePlacement[Owner, str]("catalog.yaml", branch=branch),
        SubdirFixedFilePlacement(
            namespace="bundles",
            filename="document.yaml",
            ref_factory=DemoRef,
            ref_field="name",
        ),
        NestedFlatYamlPlacement(
            namespace="events",
            ref_factory=NestedDemoRef,
            dir_ref_field="group",
            stem_ref_field="name",
        ),
        SingletonFilePlacement[Owner, str](
            "manifests/catalog.yaml",
            ref_factory=lambda: "catalog",
            branch=branch,
        ),
    )

    for index, placement in enumerate(placements):
        declaration = FamilyDeclaration(
            key=f"family-{index}",
            name=f"family_{index}",
            contract_version=version,
            artifact_name=f"artifact_{index}",
            doc_type=DemoDocument,
            placement=cast(Any, placement),
        )
        explicit = FamilyDefinition(
            key=f"family-{index}",
            name=f"family_{index}",
            contract_version=version,
            artifact_family=ArtifactFamily(
                name=f"artifact_{index}",
                contract_version=version,
                doc_type=DemoDocument,
                placement=cast(Any, placement),
            ),
        )

        assert declaration.to_definition().contract_body() == explicit.contract_body()


def test_family_declaration_passes_callbacks_and_definition_metadata_through() -> None:
    version = contract_version("2026.04.18")

    def coerce_payload(payload: object, source: str) -> SerializedDemoDocument:
        return SerializedDemoDocument(f"{source}:{payload}")

    def decode_bytes(content: bytes, source: str) -> SerializedDemoDocument:
        return SerializedDemoDocument(f"{source}:{content.decode()}")

    def encode_document(document: SerializedDemoDocument) -> bytes:
        return document.name.encode()

    def render_document(document: SerializedDemoDocument) -> str:
        return document.name

    def document_payload(document: SerializedDemoDocument) -> object:
        return {"name": document.name}

    def normalize_for_write(
        context: ArtifactContext[Owner, DemoRef],
        document: SerializedDemoDocument,
        existing: object,
    ) -> SerializedDemoDocument:
        del context, existing
        return document

    def validate_for_write(
        context: ArtifactContext[Owner, DemoRef],
        document: SerializedDemoDocument,
        existing: object,
    ) -> None:
        del context, document, existing

    foreign_key = ForeignKeySpec(
        name="book_author",
        contract_version=version,
        source_family="books",
        source_field="author",
        target_family="authors",
    )
    reference_key = ReferenceKey.field("aliases[]")
    identity_policy = FamilyIdentityPolicy(
        artifact_id_function="demo.book_id",
        version_id_function="demo.book_version",
        canonical_payload_function="demo.book_payload",
    )

    definition = FamilyDeclaration(
        key="books",
        name="books",
        contract_version=version,
        artifact_name="book_artifact",
        doc_type=SerializedDemoDocument,
        placement=FlatYamlPlacement("books", DemoRef, ref_field="name"),
        accessor="library_books",
        coerce_payload=coerce_payload,
        decode_bytes=decode_bytes,
        encode_document=encode_document,
        render_document=render_document,
        document_payload=document_payload,
        normalize_for_write=normalize_for_write,
        validate_for_write=validate_for_write,
        scan_type=SerializedDemoDocument,
        foreign_keys=(foreign_key,),
        identity_policy=identity_policy,
        identity_field="artifact_id",
        reference_keys=(reference_key,),
        metadata={"category": "catalog"},
    ).to_definition()

    assert definition.accessor_name == "library_books"
    assert definition.foreign_keys == (foreign_key,)
    assert definition.identity_policy == identity_policy
    assert definition.identity_field == "artifact_id"
    assert definition.reference_keys == (reference_key,)
    assert definition.metadata == {"category": "catalog"}
    assert definition.artifact_family.coerce_payload is coerce_payload
    assert definition.artifact_family.decode_bytes is decode_bytes
    assert definition.artifact_family.encode_document is encode_document
    assert definition.artifact_family.render_document is render_document
    assert definition.artifact_family.document_payload is document_payload
    assert definition.artifact_family.normalize_for_write is normalize_for_write
    assert definition.artifact_family.validate_for_write is validate_for_write
    assert definition.artifact_family.scan_type is SerializedDemoDocument


def test_registry_rejects_missing_versions() -> None:
    with pytest.raises(ValueError, match="requires an explicit VersionId"):
        FamilyRegistry(
            name="demo",
            contract_version=None,  # type: ignore[arg-type]
            families=(),
        )

    with pytest.raises(ValueError, match="requires an explicit VersionId"):
        FamilyDefinition(
            key=DemoFamily.CLAIMS,
            name="claims",
            contract_version=None,  # type: ignore[arg-type]
            artifact_family=_artifact_family("claims_artifact", "claims"),
        )


def test_family_contract_slots_reject_placeholder_versions() -> None:
    with pytest.raises(ValueError, match="Contract versions must use YYYY.MM.DD"):
        FamilyRegistry(
            name="demo",
            contract_version=VersionId("draft"),
            families=(),
        )

    with pytest.raises(ValueError, match="Contract versions must use YYYY.MM.DD"):
        FamilyDefinition(
            key=DemoFamily.CLAIMS,
            name="claims",
            contract_version=VersionId("draft"),
            artifact_family=_artifact_family("claims_artifact", "claims"),
        )


def test_family_definition_contract_includes_reference_metadata() -> None:
    definition = _family_definition(
        DemoFamily.CLAIMS,
        "claims",
        "claims",
        identity_field="artifact_id",
        reference_keys=(
            ReferenceKey.field("artifact_id"),
            ReferenceKey.field("aliases[]"),
        ),
    )

    assert definition.contract_body()["identity_field"] == "artifact_id"
    assert definition.contract_body()["reference_keys"] == (
        {"kind": "field", "field": "artifact_id"},
        {"kind": "field", "field": "aliases[]"},
    )


def test_family_definition_rejects_invalid_reference_metadata() -> None:
    with pytest.raises(ValueError, match="identity field"):
        _family_definition(
            DemoFamily.CLAIMS,
            "claims",
            "claims",
            identity_field="",
        )

    with pytest.raises(ValueError, match="reference keys require an identity field"):
        _family_definition(
            DemoFamily.CLAIMS,
            "claims",
            "claims",
            reference_keys=(ReferenceKey.field("aliases[]"),),
        )


def test_bound_family_builds_reference_index_from_declared_keys() -> None:
    family = FamilyDefinition(
        key=DemoFamily.CONCEPTS,
        name="concepts",
        contract_version=contract_version("2026.04.18"),
        artifact_family=_identified_artifact_family("concepts_artifact", "concepts"),
        identity_field="artifact_id",
        reference_keys=(
            ReferenceKey.field("artifact_id"),
            ReferenceKey.field("aliases[]"),
        ),
    )
    registry = FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(family,),
    )
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)
    bound.concepts.save(
        "concept:mass",
        IdentifiedDocument("concept:mass", ("mass", "m")),
        message="save concept",
    )

    index = bound.concepts.reference_index()

    assert index.require_id("concept:mass") == "concept:mass"
    assert index.require_id("mass") == "concept:mass"


@given(st.dictionaries(keys=st.from_regex(r"id[0-9]{1,4}", fullmatch=True), values=st.just(()), min_size=1))
def test_family_reference_index_resolves_generated_identity_fields(generated: dict[str, tuple[str, ...]]) -> None:
    family = FamilyDefinition(
        key=DemoFamily.CONCEPTS,
        name="concepts",
        contract_version=contract_version("2026.04.18"),
        artifact_family=_identified_artifact_family("concepts_artifact", "concepts"),
        identity_field="artifact_id",
    )

    index = family.reference_index_from_records(
        tuple(
            IdentifiedDocument(artifact_id, aliases)
            for artifact_id, aliases in generated.items()
        )
    )

    for artifact_id in generated:
        assert index.require_id(artifact_id) == artifact_id


def test_family_reference_index_reports_extra_key_ambiguity_without_changing_identity_resolution() -> None:
    family = FamilyDefinition(
        key=DemoFamily.CONCEPTS,
        name="concepts",
        contract_version=contract_version("2026.04.18"),
        artifact_family=_identified_artifact_family("concepts_artifact", "concepts"),
        identity_field="artifact_id",
        reference_keys=(ReferenceKey.field("aliases[]"),),
    )

    with pytest.raises(AmbiguousReferenceError) as exc_info:
        family.reference_index_from_records(
            (
                IdentifiedDocument("concept:1", ("shared",)),
                IdentifiedDocument("concept:2", ("shared",)),
            )
        )

    assert exc_info.value.reference == "shared"


def test_registry_rejects_duplicate_keys_names_and_accessors() -> None:
    claims = _family_definition(DemoFamily.CLAIMS, "claims", "claims")

    with pytest.raises(ValueError, match="duplicate family keys"):
        FamilyRegistry(
            name="demo",
            contract_version=contract_version("2026.04.18"),
            families=(
                claims,
                _family_definition(DemoFamily.CLAIMS, "other", "other"),
            ),
        )

    with pytest.raises(ValueError, match="duplicate family names"):
        FamilyRegistry(
            name="demo",
            contract_version=contract_version("2026.04.18"),
            families=(
                claims,
                _family_definition(DemoFamily.CONCEPTS, "claims", "other"),
            ),
        )

    with pytest.raises(ValueError, match="duplicate family accessors"):
        FamilyRegistry(
            name="demo",
            contract_version=contract_version("2026.04.18"),
            families=(
                _family_definition(DemoFamily.CLAIMS, "claims", "claims", accessor="rows"),
                _family_definition(DemoFamily.CONCEPTS, "concepts", "concepts", accessor="rows"),
            ),
        )


def test_family_definition_metadata_value_reads_missing_metadata_as_empty() -> None:
    family = _family_definition(
        DemoFamily.CLAIMS,
        "claims",
        "claims",
        metadata={"category": "records", "rank": 20},
    )
    without_metadata = _family_definition(DemoFamily.NOTES, "notes", "notes")

    assert family.metadata_value("category") == "records"
    assert family.metadata_value("rank", default=100) == 20
    assert family.metadata_value("missing", default=100) == 100
    assert without_metadata.metadata_value("category") is None
    assert without_metadata.metadata_value("category", default="unknown") == "unknown"


def test_registry_selects_families_by_predicate_and_metadata() -> None:
    registry = FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(
            _family_definition(
                DemoFamily.CLAIMS,
                "claims",
                "claims",
                metadata={"category": "records", "rank": 20},
            ),
            _family_definition(
                DemoFamily.CONCEPTS,
                "concepts",
                "concepts",
                metadata={"category": "records", "rank": 10},
            ),
            _family_definition(
                DemoFamily.NOTES,
                "notes",
                "notes",
                metadata={"category": "support", "rank": 30},
            ),
        ),
    )

    assert tuple(
        family.name
        for family in registry.select(
            lambda family: cast(int, family.metadata_value("rank", default=100)) < 30
        )
    ) == (
        "claims",
        "concepts",
    )
    assert tuple(family.name for family in registry.select_by_metadata("category", "records")) == (
        "claims",
        "concepts",
    )
    assert tuple(registry.select_by_metadata("category", "missing")) == ()


def test_registry_by_metadata_requires_exactly_one_family() -> None:
    registry = FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(
            _family_definition(
                DemoFamily.CLAIMS,
                "claims",
                "claims",
                metadata={"category": "records", "root": "claims"},
            ),
            _family_definition(
                DemoFamily.CONCEPTS,
                "concepts",
                "concepts",
                metadata={"category": "records", "root": "concepts"},
            ),
        ),
    )

    assert registry.by_metadata("root", "claims").name == "claims"
    with pytest.raises(KeyError, match="no family metadata"):
        registry.by_metadata("root", "missing")
    with pytest.raises(ValueError, match="multiple families"):
        registry.by_metadata("category", "records")


def test_family_definition_and_registry_resolve_storage_roots_from_placements() -> None:
    registry = FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(
            _family_definition(DemoFamily.CLAIMS, "claims", "books"),
            _family_definition(DemoFamily.CONCEPTS, "concepts", "concepts"),
        ),
    )

    assert registry.by_name("claims").storage_root() == "books"
    assert registry.by_storage_root("books").name == "claims"
    assert registry.family_for_path("books/example.yaml").name == "claims"
    assert registry.family_for_path("concepts\\mass.yaml").name == "concepts"
    with pytest.raises(KeyError, match="unknown storage root"):
        registry.by_storage_root("missing")
    with pytest.raises(KeyError, match="unknown storage root"):
        registry.family_for_path("missing/example.yaml")


def test_registry_root_lookup_rejects_non_namespace_placements() -> None:
    fixed_family: ArtifactFamily[Owner, str, DemoDocument] = ArtifactFamily(
        name="notes_artifact",
        contract_version=contract_version("2026.04.18"),
        doc_type=DemoDocument,
        placement=FixedFilePlacement(filename="notes.yaml"),
    )
    fixed_definition = FamilyDefinition(
        key=DemoFamily.NOTES,
        name="notes",
        contract_version=contract_version("2026.04.18"),
        artifact_family=fixed_family,
    )

    registry = FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(fixed_definition,),
    )

    with pytest.raises(ValueError, match="fixed-file placement"):
        registry.by_storage_root("notes")


def test_registry_can_skip_foreign_key_closure_for_query_views() -> None:
    foreign_key = ForeignKeySpec(
        name="claim_concept",
        contract_version=contract_version("2026.04.18"),
        source_family="claims",
        source_field="concept",
        target_family="concepts",
    )
    registry = FamilyRegistry(
        name="query-view",
        contract_version=contract_version("2026.04.18"),
        families=(
            _family_definition(
                DemoFamily.CLAIMS,
                "claims",
                "books",
                foreign_keys=(foreign_key,),
            ),
        ),
        validate_foreign_keys=False,
    )

    assert registry.family_for_path("books/example.yaml").name == "claims"


def test_duplicate_detection_does_not_rescan_collected_duplicates() -> None:
    class CountingKey:
        comparisons = 0

        def __init__(self, value: str) -> None:
            self.value = value

        def __hash__(self) -> int:
            return hash(self.value)

        def __eq__(self, other: object) -> bool:
            CountingKey.comparisons += 1
            return isinstance(other, CountingKey) and self.value == other.value

    values = tuple(
        key
        for index in range(50)
        for key in (CountingKey(str(index)), CountingKey(str(index)))
    )

    duplicates = _duplicates(values)

    assert [cast(CountingKey, item).value for item in duplicates] == [str(index) for index in range(50)]
    assert CountingKey.comparisons < 150


def test_bound_registry_exposes_family_operations_by_attribute_key_and_name() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    commit = bound.claims.save("paper", DemoDocument("alpha"), message="save claim")

    assert len(commit) == 40
    assert bound.claims.exists("paper") is True
    assert bound.claims.exists("missing") is False
    assert list(bound.claims.iter_refs()) == ["paper"]
    assert bound.by_key(DemoFamily.CLAIMS).require("paper") == DemoDocument("alpha")
    assert bound.by_name("claims").require_handle("paper").address.require_path() == "claims/paper.yaml"

    bound.claims.move("paper", "renamed", DemoDocument("beta"), message="move claim")
    assert bound.claims.load("paper") is None
    assert bound.claims.require("renamed") == DemoDocument("beta")

    bound.claims.delete("renamed", message="delete claim")
    assert list(bound.claims.iter_refs()) == []


def test_bound_family_exposes_address_coercion_render_and_payload() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    document = bound.claims.coerce({"name": "alpha"}, source="input")

    assert document == DemoDocument("alpha")
    assert bound.claims.address("paper").require_path() == "claims/paper.yaml"
    assert bound.claims.payload(document) == {"name": "alpha"}
    assert "name: alpha" in bound.claims.render(document)


def test_bound_family_iter_handles_exposes_loaded_handles() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)
    bound.claims.save("paper", DemoDocument("alpha"), message="save claim")

    handles = list(bound.claims.iter_handles())

    assert [(handle.ref, handle.document.name) for handle in handles] == [("paper", "alpha")]
    assert handles[0].address.require_path() == "claims/paper.yaml"


def test_bound_family_pin_freezes_commit_for_iter_and_require() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)
    bound.claims.save("paper", DemoDocument("alpha"), message="save claim")

    pinned = bound.claims.pin()

    bound.claims.save("other", DemoDocument("beta"), message="save another claim")

    assert pinned.commit is not None
    assert list(pinned.iter_refs()) == ["paper"]
    assert pinned.exists("paper") is True
    assert pinned.exists("other") is False
    assert pinned.require("paper") == DemoDocument("alpha")
    assert pinned.address("paper").commit == pinned.commit


def test_bound_family_forwards_expected_head_checks() -> None:
    registry = _registry()
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)

    first = bound.claims.save("paper", DemoDocument("alpha"), message="first")
    bound.claims.save("paper", DemoDocument("beta"), message="second")

    with pytest.raises(HeadMismatchError):
        bound.claims.save(
            "paper",
            DemoDocument("gamma"),
            message="stale save",
            expected_head=first,
        )

    with pytest.raises(HeadMismatchError):
        with bound.transact(message="stale transaction", expected_head=first) as transaction:
            transaction.claims.save("other", DemoDocument("delta"))


def test_transaction_head_check_is_named_as_advisory() -> None:
    registry = _registry()
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)

    bound.claims.save("paper", DemoDocument("alpha"), message="seed")
    head_before = backend.branch_sha("master")
    assert head_before is not None

    # Creating the transaction with a stale expected_head must NOT raise:
    # the head check is advisory (lazy), not preemptive.
    transaction = bound.transact(message="advisory commit", expected_head="0" * 40)

    # The mismatch only surfaces when the commit is actually attempted.
    with pytest.raises(HeadMismatchError):
        with transaction as txn:
            txn.claims.save("other", DemoDocument("beta"))

    # And the branch head must not have advanced.
    assert backend.branch_sha("master") == head_before


def test_bound_registry_transaction_writes_multiple_families() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    with bound.transact(message="save rows") as transaction:
        transaction.claims.save("claim-one", DemoDocument("alpha"))
        transaction.concepts.save("concept-one", DemoDocument("beta"))

    assert transaction.commit_sha is not None
    assert bound.claims.require("claim-one") == DemoDocument("alpha")
    assert bound.concepts.require("concept-one") == DemoDocument("beta")


def test_head_bound_transaction_families_transact_uses_captured_head(monkeypatch) -> None:
    registry = _registry()
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)
    first = bound.claims.save("paper", DemoDocument("alpha"), message="seed")
    calls: list[tuple[str | None, str | None]] = []
    original_commit_batch = backend.commit_batch

    def recording_commit_batch(
        adds,
        deletes,
        message,
        *,
        branch=None,
        expected_head=None,
    ):
        calls.append((branch, expected_head))
        return original_commit_batch(
            adds,
            deletes,
            message,
            branch=branch,
            expected_head=expected_head,
        )

    monkeypatch.setattr(backend, "commit_batch", recording_commit_batch)

    with backend.head_bound_transaction("master") as transaction:
        with transaction.families_transact(bound, message="save rows") as families:
            families.claims.save("claim-one", DemoDocument("beta"))
            families.concepts.save("concept-one", DemoDocument("gamma"))

    assert calls == [("master", first)]
    assert bound.claims.require("claim-one") == DemoDocument("beta")
    assert bound.concepts.require("concept-one") == DemoDocument("gamma")


def test_head_bound_transaction_family_binding_pins_writes_to_captured_branch() -> None:
    family = ArtifactFamily[Owner, str, DemoDocument](
        name="other",
        contract_version=contract_version("2026.04.18"),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement(
            "other",
            str,
            branch=BranchPlacement(policy="fixed", fixed_branch="other"),
        ),
    )
    definition = FamilyDefinition(
        key=DemoFamily.NOTES,
        name="other",
        contract_version=contract_version("2026.04.18"),
        artifact_family=family,
    )
    registry = FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(definition,),
    )
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)
    backend.commit_files({"seed.txt": b"seed"}, "seed", branch="master")

    with backend.head_bound_transaction("master") as transaction:
        with transaction.families_transact(bound, message="captured branch") as families:
            families.other.save("paper", DemoDocument("alpha"))

    assert backend.branch_sha("other") is None
    assert store.require(family, "paper", branch="master") == DemoDocument("alpha")


def test_bound_family_save_validates_declared_foreign_keys_before_commit() -> None:
    registry = _reference_registry()
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)
    bound.concepts.save(
        "concept:mass",
        IdentifiedDocument("concept:mass", ("mass",)),
        message="save concept",
    )

    bound.claims.save(
        "claim:1",
        ClaimWithConceptDocument("claim:1", "mass"),
        message="save valid claim",
    )

    assert bound.claims.require("claim:1").concept == "mass"
    head = backend.branch_sha("master")
    with pytest.raises(ForeignKeyValidationError, match="does not resolve"):
        bound.claims.save(
            "claim:2",
            ClaimWithConceptDocument("claim:2", "missing"),
            message="save invalid claim",
        )
    assert backend.branch_sha("master") == head


def test_registry_validation_reads_the_publication_head(monkeypatch) -> None:
    registry = _reference_registry()
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)
    bound.concepts.save(
        "concept:mass",
        IdentifiedDocument("concept:mass", ("mass",)),
        message="save concept",
    )
    publication_head = backend.branch_sha("master")
    observed_commits: list[str | None] = []
    original_iter_handles = store.iter_handles

    def recording_iter_handles(family, *, branch=None, commit=None):
        observed_commits.append(commit)
        return original_iter_handles(family, branch=branch, commit=commit)

    monkeypatch.setattr(store, "iter_handles", recording_iter_handles)

    bound.claims.save(
        "claim:1",
        ClaimWithConceptDocument("claim:1", "mass"),
        message="save claim",
    )

    assert observed_commits
    assert set(observed_commits) == {publication_head}


def test_registry_write_rejects_branch_advance_after_validation(monkeypatch) -> None:
    registry = _reference_registry()
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)
    bound.concepts.save(
        "concept:mass",
        IdentifiedDocument("concept:mass", ("mass",)),
        message="save concept",
    )
    original_commit_batch = backend.commit_batch
    raced = False

    def racing_commit_batch(adds, deletes, message, *, branch=None, expected_head=None):
        nonlocal raced
        if not raced:
            raced = True
            original_commit_batch(
                {"race.txt": b"advanced"},
                [],
                "concurrent write",
                branch=branch,
            )
        return original_commit_batch(
            adds,
            deletes,
            message,
            branch=branch,
            expected_head=expected_head,
        )

    monkeypatch.setattr(backend, "commit_batch", racing_commit_batch)

    with pytest.raises(HeadMismatchError):
        bound.claims.save(
            "claim:1",
            ClaimWithConceptDocument("claim:1", "mass"),
            message="save claim",
        )

    assert bound.claims.load("claim:1") is None


def test_registry_transaction_rejects_branch_advance_after_validation(monkeypatch) -> None:
    registry = _reference_registry()
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)
    bound.concepts.save(
        "concept:mass",
        IdentifiedDocument("concept:mass", ("mass",)),
        message="save concept",
    )
    original_commit_batch = backend.commit_batch
    raced = False

    def racing_commit_batch(adds, deletes, message, *, branch=None, expected_head=None):
        nonlocal raced
        if not raced:
            raced = True
            original_commit_batch(
                {"race.txt": b"advanced"},
                [],
                "concurrent write",
                branch=branch,
            )
        return original_commit_batch(
            adds,
            deletes,
            message,
            branch=branch,
            expected_head=expected_head,
        )

    monkeypatch.setattr(backend, "commit_batch", racing_commit_batch)

    with pytest.raises(HeadMismatchError):
        with bound.transact(message="save claim") as transaction:
            transaction.claims.save(
                "claim:1",
                ClaimWithConceptDocument("claim:1", "mass"),
            )

    assert bound.claims.load("claim:1") is None


def test_optional_foreign_key_allows_omission_but_rejects_present_missing_value() -> None:
    registry = _reference_registry(required=False)
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    bound.claims.save(
        "claim:1",
        ClaimWithConceptDocument("claim:1", None),
        message="save omitted optional fk",
    )

    with pytest.raises(ForeignKeyValidationError, match="does not resolve"):
        bound.claims.save(
            "claim:2",
            ClaimWithConceptDocument("claim:2", "missing"),
            message="save invalid optional fk",
        )


def test_bound_transaction_validates_foreign_keys_against_post_transaction_state() -> None:
    registry = _reference_registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    with bound.transact(message="save graph") as transaction:
        transaction.claims.save(
            "claim:1",
            ClaimWithConceptDocument("claim:1", "mass"),
        )
        transaction.concepts.save(
            "concept:mass",
            IdentifiedDocument("concept:mass", ("mass",)),
        )

    assert bound.claims.require("claim:1").concept == "mass"


def test_bound_family_delete_rejects_dangling_dependents() -> None:
    registry = _reference_registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)
    with bound.transact(message="save graph") as transaction:
        transaction.concepts.save(
            "concept:mass",
            IdentifiedDocument("concept:mass", ("mass",)),
        )
        transaction.claims.save(
            "claim:1",
            ClaimWithConceptDocument("claim:1", "mass"),
        )

    with pytest.raises(ForeignKeyValidationError, match="does not resolve"):
        bound.concepts.delete("concept:mass", message="delete referenced concept")

    assert bound.concepts.exists("concept:mass")


def test_unrelated_family_save_does_not_validate_separate_foreign_key_graph() -> None:
    claims = FamilyDefinition(
        key=DemoFamily.CLAIMS,
        name="claims",
        contract_version=contract_version("2026.04.18"),
        artifact_family=_claim_with_concept_family("claims_artifact", "claims"),
        identity_field="artifact_id",
        foreign_keys=(
            ForeignKeySpec(
                name="claim_concept",
                contract_version=contract_version("2026.04.18"),
                source_family="claims",
                source_field="concept",
                target_family="concepts",
            ),
        ),
    )
    concepts = FamilyDefinition(
        key=DemoFamily.CONCEPTS,
        name="concepts",
        contract_version=contract_version("2026.04.18"),
        artifact_family=_identified_artifact_family("concepts_artifact", "concepts"),
        identity_field="artifact_id",
    )
    notes = _family_definition(DemoFamily.NOTES, "notes", "notes")
    registry = FamilyRegistry(
        name="demo",
        contract_version=contract_version("2026.04.18"),
        families=(claims, concepts, notes),
    )
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)
    store.save(
        claims.artifact_family,
        "claim:bad",
        ClaimWithConceptDocument("claim:bad", "missing"),
        message="seed invalid graph outside registry validation",
    )

    bound.notes.save("note", DemoDocument("unrelated"), message="save unrelated")

    assert bound.notes.require("note") == DemoDocument("unrelated")


def test_bound_registry_resolves_by_artifact_family_and_recovers_ref_from_path() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)
    claims_family = registry.by_key(DemoFamily.CLAIMS).artifact_family

    bound.by_artifact_family(claims_family).save("paper", DemoDocument("alpha"), message="save")

    assert bound.by_artifact_family(claims_family).require("paper") == DemoDocument("alpha")
    assert bound.by_artifact_family(claims_family).ref_from_path("claims/paper.yaml") == "paper"


def test_bound_transaction_supports_key_and_name_lookup() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    with bound.transact(message="save rows") as transaction:
        transaction.by_key(DemoFamily.CLAIMS).save("claim-one", DemoDocument("alpha"))
        transaction.by_name("concepts").save("concept-one", DemoDocument("beta"))

    assert bound.claims.require("claim-one") == DemoDocument("alpha")
    assert bound.concepts.require("concept-one") == DemoDocument("beta")


def test_bound_transaction_resolves_by_artifact_family() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)
    claims_family = registry.by_key(DemoFamily.CLAIMS).artifact_family

    with bound.transact(message="save row") as transaction:
        ref = transaction.by_artifact_family(claims_family).ref_from_path("claims/paper.yaml")
        transaction.by_artifact_family(claims_family).save(ref, DemoDocument("alpha"))

    assert bound.claims.require("paper") == DemoDocument("alpha")


def test_contract_manifest_contains_registry_and_family_versions() -> None:
    registry = _registry()
    manifest = registry.contract_manifest(package_name="demo", package_version="2026.04.18")
    payload = manifest.to_payload()

    assert payload["registry"] == {
        "name": "demo",
        "contract_version": "2026.04.18",
    }
    entries = {entry.key: entry for entry in manifest.contracts}
    assert entries["family-registry:demo"].contract_version == VersionId("2026.04.18")
    assert entries["family:claims"].contract_version == VersionId("2026.04.18")


def test_family_identity_policy_is_contract_surface() -> None:
    family = _family_definition(
        DemoFamily.CLAIMS,
        "claims",
        "claims",
        identity_policy=FamilyIdentityPolicy(
            artifact_id_function="demo.identity.claim_artifact_id",
            version_id_function="demo.identity.claim_version_id",
            canonical_payload_function="demo.identity.claim_canonical_payload",
            normalize_payload_function="demo.identity.normalize_claim",
            logical_id_fields=("logical_ids",),
            version_excluded_fields=("artifact_id", "version_id"),
            source_local_fields=("id",),
        ),
    )

    assert family.contract_body()["identity_policy"] == {
        "artifact_id_function": "demo.identity.claim_artifact_id",
        "version_id_function": "demo.identity.claim_version_id",
        "canonical_payload_function": "demo.identity.claim_canonical_payload",
        "normalize_payload_function": "demo.identity.normalize_claim",
        "logical_id_fields": ("logical_ids",),
        "version_excluded_fields": ("artifact_id", "version_id"),
        "source_local_fields": ("id",),
    }


def test_contract_manifest_changes_when_family_surface_changes() -> None:
    baseline = _registry().contract_manifest(package_name="demo", package_version="2026.04.18")
    moved = _registry(claims_namespace="claim_rows").contract_manifest(
        package_name="demo",
        package_version="2026.04.18",
    )
    with_fk = _registry(
        foreign_keys=(
            ForeignKeySpec(
                name="claim_concept",
                contract_version=contract_version("2026.04.18"),
                source_family="claims",
                source_field="concept",
                target_family="concepts",
            ),
        ),
    ).contract_manifest(package_name="demo", package_version="2026.04.18")

    assert baseline.to_yaml() != moved.to_yaml()
    assert baseline.to_yaml() != with_fk.to_yaml()
    with pytest.raises(Exception, match="Contract body changed"):
        check_contract_manifest(baseline, moved)

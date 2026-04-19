from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import msgspec
import pytest

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.contracts import check_contract_manifest
from quire.families import FamilyDefinition, FamilyIdentityPolicy, FamilyRegistry, _duplicates
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitStore
from quire.references import ForeignKeySpec
from quire.versions import VersionId


class DemoDocument(msgspec.Struct):
    name: str


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


class DemoFamily(str, Enum):
    CLAIMS = "claims"
    CONCEPTS = "concepts"


def _artifact_family(name: str, namespace: str) -> ArtifactFamily[Owner, str, DemoDocument]:
    return ArtifactFamily(
        name=name,
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement(namespace, str),
    )


def _family_definition(
    key: DemoFamily,
    name: str,
    namespace: str,
    *,
    accessor: str | None = None,
    foreign_keys: tuple[ForeignKeySpec, ...] = (),
    identity_policy: FamilyIdentityPolicy | None = None,
) -> FamilyDefinition[Owner, DemoFamily, str, DemoDocument]:
    return FamilyDefinition(
        key=key,
        name=name,
        accessor=accessor,
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        artifact_family=_artifact_family(f"{name}_artifact", namespace),
        foreign_keys=foreign_keys,
        identity_policy=identity_policy,
    )


def _registry(
    *,
    claims_namespace: str = "claims",
    foreign_keys: tuple[ForeignKeySpec, ...] = (),
) -> FamilyRegistry[Owner, DemoFamily]:
    return FamilyRegistry(
        name="demo",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
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


def test_registry_rejects_duplicate_keys_names_and_accessors() -> None:
    claims = _family_definition(DemoFamily.CLAIMS, "claims", "claims")

    with pytest.raises(ValueError, match="duplicate family keys"):
        FamilyRegistry(
            name="demo",
            contract_version=VersionId("2026.04.18", allow_placeholder=False),
            families=(
                claims,
                _family_definition(DemoFamily.CLAIMS, "other", "other"),
            ),
        )

    with pytest.raises(ValueError, match="duplicate family names"):
        FamilyRegistry(
            name="demo",
            contract_version=VersionId("2026.04.18", allow_placeholder=False),
            families=(
                claims,
                _family_definition(DemoFamily.CONCEPTS, "claims", "other"),
            ),
        )

    with pytest.raises(ValueError, match="duplicate family accessors"):
        FamilyRegistry(
            name="demo",
            contract_version=VersionId("2026.04.18", allow_placeholder=False),
            families=(
                _family_definition(DemoFamily.CLAIMS, "claims", "claims", accessor="rows"),
                _family_definition(DemoFamily.CONCEPTS, "concepts", "concepts", accessor="rows"),
            ),
        )


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

    assert [item.value for item in duplicates] == [str(index) for index in range(50)]
    assert CountingKey.comparisons < 150


def test_bound_registry_exposes_family_operations_by_attribute_key_and_name() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    commit = bound.claims.save("paper", DemoDocument("alpha"), message="save claim")

    assert len(commit) == 40
    assert list(bound.claims.iter()) == ["paper"]
    assert bound.by_key(DemoFamily.CLAIMS).require("paper") == DemoDocument("alpha")
    assert bound.by_name("claims").require_handle("paper").address.require_path() == "claims/paper.yaml"

    bound.claims.move("paper", "renamed", DemoDocument("beta"), message="move claim")
    assert bound.claims.load("paper") is None
    assert bound.claims.require("renamed") == DemoDocument("beta")

    bound.claims.delete("renamed", message="delete claim")
    assert list(bound.claims.iter()) == []


def test_bound_family_exposes_address_coercion_render_and_payload() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    document = bound.claims.coerce({"name": "alpha"}, source="input")

    assert document == DemoDocument("alpha")
    assert bound.claims.address("paper").require_path() == "claims/paper.yaml"
    assert bound.claims.payload(document) == {"name": "alpha"}
    assert "name: alpha" in bound.claims.render(document)


def test_bound_family_forwards_expected_head_checks() -> None:
    registry = _registry()
    backend = GitStore.init_memory()
    store = DocumentFamilyStore(owner=Owner(), backend=backend)
    bound = registry.bind(store.owner, store)

    first = bound.claims.save("paper", DemoDocument("alpha"), message="first")
    bound.claims.save("paper", DemoDocument("beta"), message="second")

    with pytest.raises(ValueError, match="head mismatch"):
        bound.claims.save(
            "paper",
            DemoDocument("gamma"),
            message="stale save",
            expected_head=first,
        )

    with pytest.raises(ValueError, match="head mismatch"):
        with bound.transact(message="stale transaction", expected_head=first) as transaction:
            transaction.claims.save("other", DemoDocument("delta"))


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
                contract_version=VersionId("2026.04.18", allow_placeholder=False),
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

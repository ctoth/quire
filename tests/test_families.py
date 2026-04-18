from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import msgspec
import pytest

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.contracts import check_contract_manifest
from quire.families import FamilyDefinition, FamilyRegistry
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
) -> FamilyDefinition[Owner, DemoFamily, str, DemoDocument]:
    return FamilyDefinition(
        key=key,
        name=name,
        accessor=accessor,
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        artifact_family=_artifact_family(f"{name}_artifact", namespace),
        foreign_keys=foreign_keys,
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


def test_bound_registry_exposes_family_operations_by_attribute_key_and_name() -> None:
    registry = _registry()
    store = DocumentFamilyStore(owner=Owner(), backend=GitStore.init_memory())
    bound = registry.bind(store.owner, store)

    commit = bound.claims.save("paper", DemoDocument("alpha"), message="save claim")

    assert len(commit) == 40
    assert bound.claims.list() == ["paper"]
    assert bound.by_key(DemoFamily.CLAIMS).require("paper") == DemoDocument("alpha")
    assert bound.by_name("claims").require_handle("paper").address.require_path() == "claims/paper.yaml"

    bound.claims.move("paper", "renamed", DemoDocument("beta"), message="move claim")
    assert bound.claims.load("paper") is None
    assert bound.claims.require("renamed") == DemoDocument("beta")

    bound.claims.delete("renamed", message="delete claim")
    assert bound.claims.list() == []


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


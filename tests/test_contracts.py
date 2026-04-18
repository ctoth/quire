from __future__ import annotations

import pytest

from quire.contracts import (
    CompatibilityMarker,
    ContractEntry,
    ContractManifest,
    ContractManifestError,
    check_contract_manifest,
)
from quire.versions import VersionId


def _manifest(*, version: str = "2026.04.18", field_type: str = "str") -> ContractManifest:
    return ContractManifest(
        format_version=1,
        package_name="demo",
        package_version="0.1.0",
        contracts=(
            ContractEntry(
                kind="document_schema",
                name="demo-document",
                contract_version=VersionId(version),
                body={
                    "fields": (
                        {"name": "name", "type": field_type, "required": True},
                    ),
                },
            ),
        ),
    )


def test_contract_manifest_round_trips_stably():
    manifest = _manifest()

    encoded = manifest.to_yaml()

    assert ContractManifest.from_yaml(encoded) == manifest
    assert ContractManifest.from_yaml(encoded).to_yaml() == encoded


def test_changed_contract_body_requires_version_bump_or_marker():
    previous = _manifest(version="2026.04.18", field_type="str")
    current = _manifest(version="2026.04.18", field_type="int")

    with pytest.raises(ContractManifestError, match="demo-document"):
        check_contract_manifest(previous, current)


def test_changed_contract_body_accepts_version_bump():
    previous = _manifest(version="2026.04.18", field_type="str")
    current = _manifest(version="2026.04.19", field_type="int")

    report = check_contract_manifest(previous, current)

    assert report.bumped == ("document_schema:demo-document",)
    assert report.failed == ()


def test_changed_contract_body_accepts_compatibility_marker():
    previous = _manifest(version="2026.04.18", field_type="str")
    current = ContractManifest(
        format_version=1,
        package_name="demo",
        package_version="0.1.0",
        contracts=(
            ContractEntry(
                kind="document_schema",
                name="demo-document",
                contract_version=VersionId("2026.04.18"),
                body={
                    "fields": (
                        {"name": "name", "type": "int", "required": True},
                    ),
                },
            ),
        ),
        compatible_changes=(
            CompatibilityMarker(
                contract="document_schema:demo-document",
                contract_version=VersionId("2026.04.18"),
                reason="The stored shape is unchanged; display type was narrowed.",
            ),
        ),
    )

    report = check_contract_manifest(previous, current)

    assert report.compatible == ("document_schema:demo-document",)
    assert report.failed == ()


def test_placeholder_contract_versions_are_rejected_after_baseline():
    with pytest.raises(ValueError):
        VersionId("1.0", allow_placeholder=False)

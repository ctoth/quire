from __future__ import annotations

from dataclasses import dataclass

import msgspec
import pytest

from quire.contracts import (
    CompatibilityMarker,
    ContractEntry,
    ContractManifest,
    ContractManifestError,
    check_contract_manifest,
)
from quire.versions import VersionId


@dataclass(frozen=True)
class DemoMetadata:
    name: str
    enabled: bool = True


class DemoStruct(msgspec.Struct):
    name: str
    count: int


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


def test_non_placeholder_contract_versions_require_zero_padded_calendar_dates():
    assert VersionId("2026.04.09", allow_placeholder=False) < VersionId(
        "2026.04.18",
        allow_placeholder=False,
    )

    for invalid in ("2026.04.9", "2026.4.09", "draft", "tbd", "1.0"):
        with pytest.raises(ValueError, match="Contract versions must use YYYY.MM.DD"):
            VersionId(invalid, allow_placeholder=False)


def test_contract_manifest_from_yaml_rejects_invalid_versions():
    payload = b"""
format_version: 1
package:
  name: demo
  version: 0.1.0
registry:
  name: demo
  contract_version: draft
contracts:
  - kind: document_schema
    name: demo-document
    contract_version: tbd
    body: {}
"""

    with pytest.raises(ValueError, match="Contract versions must use YYYY.MM.DD"):
        ContractManifest.from_yaml(payload)


def test_contract_manifest_equality_is_not_sensitive_to_entry_order():
    first = ContractEntry(
        kind="document_schema",
        name="alpha",
        contract_version=VersionId("2026.04.18"),
        body={"field": "name"},
    )
    second = ContractEntry(
        kind="document_schema",
        name="beta",
        contract_version=VersionId("2026.04.18"),
        body={"field": "title"},
    )

    assert ContractManifest(
        format_version=1,
        package_name="demo",
        package_version="0.1.0",
        contracts=(second, first),
    ) == ContractManifest(
        format_version=1,
        package_name="demo",
        package_version="0.1.0",
        contracts=(first, second),
    )


def test_contract_body_normalizes_dataclasses_and_msgspec_structs():
    entry = ContractEntry(
        kind="document_schema",
        name="demo-document",
        contract_version=VersionId("2026.04.18"),
        body={
            "metadata": DemoMetadata("alpha"),
            "struct": DemoStruct("beta", 3),
        },
    )

    assert entry.body == {
        "metadata": {"enabled": True, "name": "alpha"},
        "struct": {"count": 3, "name": "beta"},
    }

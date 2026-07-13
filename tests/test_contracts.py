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
    contract_version,
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


def test_placeholder_contract_versions_are_rejected():
    with pytest.raises(ValueError, match="Placeholder contract version is not allowed"):
        contract_version("1.0")


def test_contract_version_requires_zero_padded_calendar_dates():
    assert contract_version("2026.04.09") == VersionId("2026.04.09")

    for invalid in ("2026.04.9", "2026.4.09", "draft", "tbd"):
        with pytest.raises(ValueError, match="Contract versions must use YYYY.MM.DD"):
            contract_version(invalid)


def test_contract_version_accepts_a_same_day_revision():
    # A contract body can legitimately change twice in one day. Without a
    # same-day counter the author must either fabricate a future date or
    # falsely mark a breaking change compatible, so the grammar carries it.
    assert contract_version("2026.07.13.1") == VersionId("2026.07.13.1")
    assert contract_version("2026.07.13.12") == VersionId("2026.07.13.12")
    assert contract_version("2026.07.13") != contract_version("2026.07.13.1")


def test_contract_same_day_revision_has_one_spelling():
    # A bare date IS revision 0, so ".0" and leading zeros are rejected —
    # otherwise one version would have several spellings.
    for invalid in ("2026.07.13.0", "2026.07.13.01"):
        with pytest.raises(ValueError, match="same-day revision"):
            contract_version(invalid)

    with pytest.raises(ValueError, match="Contract versions must use YYYY.MM.DD"):
        contract_version("2026.07.13.x")


def test_version_id_is_opaque():
    # VersionId carries an arbitrary non-empty token; declaration policy lives in
    # contract_version(), not in VersionId.
    assert VersionId("draft").value == "draft"
    assert str(VersionId("anything at all")) == "anything at all"
    # VersionId defines no ordering, so a comparison falls back to the default
    # object behaviour and raises.
    with pytest.raises(TypeError):
        VersionId("2026.04.09") < VersionId("2026.04.18")  # type: ignore[operator]
    with pytest.raises(ValueError, match="cannot be empty"):
        VersionId("   ")


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


def test_contract_body_sorts_sets_by_normalized_payload() -> None:
    @dataclass(frozen=True)
    class ReorderedMetadata:
        z_value: str
        a_value: str

    entry = ContractEntry(
        kind="document_schema",
        name="demo-document",
        contract_version=VersionId("2026.04.18"),
        body={
            "items": {
                ReorderedMetadata(z_value="alpha", a_value="zulu"),
                ReorderedMetadata(z_value="zulu", a_value="alpha"),
            },
        },
    )

    assert entry.body["items"] == [
        {"a_value": "alpha", "z_value": "zulu"},
        {"a_value": "zulu", "z_value": "alpha"},
    ]


def test_contract_body_rejects_unsupported_payload_objects() -> None:
    class UnsupportedPayload:
        pass

    with pytest.raises(TypeError, match="Unsupported contract payload"):
        ContractEntry(
            kind="document_schema",
            name="demo-document",
            contract_version=VersionId("2026.04.18"),
            body={"metadata": UnsupportedPayload()},
        )


def _registry_manifest(
    *,
    registry_version: str = "2026.04.18",
    entry_version: str = "2026.04.18",
    registry_name: str = "demo",
    entry_name: str = "demo",
    field_type: str = "str",
) -> ContractManifest:
    return ContractManifest(
        format_version=1,
        package_name="demo",
        package_version="0.1.0",
        registry_name=registry_name,
        registry_contract_version=VersionId(registry_version),
        contracts=(
            ContractEntry(
                kind="family-registry",
                name=entry_name,
                contract_version=VersionId(entry_version),
                body={
                    "families": (
                        {
                            "name": "claims",
                            "key": "claims",
                            "contract_version": "2026.04.18",
                            "field_type": field_type,
                        },
                    ),
                },
            ),
        ),
    )


def test_registry_manifest_requires_matching_registry_contract_entry():
    with pytest.raises(ValueError, match="registry contract_version must match"):
        _registry_manifest(registry_version="2026.04.19", entry_version="2026.04.18")

    with pytest.raises(ValueError, match="registry entry family-registry:demo"):
        _registry_manifest(registry_name="demo", entry_name="other")


def test_registry_body_drift_requires_registry_version_bump():
    previous = _registry_manifest(registry_version="2026.04.18", entry_version="2026.04.18")
    current = _registry_manifest(
        registry_version="2026.04.18",
        entry_version="2026.04.18",
        field_type="int",
    )

    with pytest.raises(ContractManifestError, match="family-registry:demo"):
        check_contract_manifest(previous, current)


def test_registry_body_drift_with_registry_version_bump_reports_bumped():
    previous = _registry_manifest(registry_version="2026.04.18", entry_version="2026.04.18")
    current = _registry_manifest(
        registry_version="2026.04.19",
        entry_version="2026.04.19",
        field_type="int",
    )

    report = check_contract_manifest(previous, current)

    assert report.bumped == ("family-registry:demo",)

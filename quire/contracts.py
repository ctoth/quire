from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from quire.canonical import canonical_json_text, normalize_payload
from quire.versions import VersionId

_PLACEHOLDER_CONTRACT_VERSIONS = frozenset({"0", "0.0", "0.1", "1", "1.0"})
_CALENDAR_VERSION_RE = re.compile(r"^(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})$")


def contract_version(value: str) -> VersionId:
    """Validate a contract/family declaration version and wrap it in a VersionId.

    Contract and family declarations require a zero-padded ``YYYY.MM.DD``
    calendar version and reject placeholder tokens. VersionId itself is opaque;
    this declaration-time policy lives here, not in VersionId.
    """
    normalized = value.strip()
    if normalized in _PLACEHOLDER_CONTRACT_VERSIONS:
        raise ValueError(f"Placeholder contract version is not allowed: {normalized}")
    match = _CALENDAR_VERSION_RE.match(normalized)
    if match is None:
        raise ValueError("Contract versions must use YYYY.MM.DD")
    try:
        date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError as exc:
        raise ValueError("Contract versions must use YYYY.MM.DD") from exc
    return VersionId(normalized)


class ContractManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ContractEntry:
    kind: str
    name: str
    contract_version: VersionId
    body: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", _normalize_payload(self.body))

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "contract_version": str(self.contract_version),
            "body": _normalize_payload(self.body),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ContractEntry:
        return cls(
            kind=str(payload["kind"]),
            name=str(payload["name"]),
            contract_version=contract_version(str(payload["contract_version"])),
            body=_normalize_payload(dict(payload["body"])),
        )


@dataclass(frozen=True)
class CompatibilityMarker:
    contract: str
    contract_version: VersionId
    reason: str

    def to_payload(self) -> dict[str, str]:
        return {
            "contract": self.contract,
            "contract_version": str(self.contract_version),
            "reason": self.reason,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CompatibilityMarker:
        return cls(
            contract=str(payload["contract"]),
            contract_version=contract_version(str(payload["contract_version"])),
            reason=str(payload["reason"]),
        )


@dataclass(frozen=True)
class ContractManifest:
    format_version: int
    package_name: str
    package_version: str
    contracts: tuple[ContractEntry, ...]
    registry_name: str | None = None
    registry_contract_version: VersionId | None = None
    compatible_changes: tuple[CompatibilityMarker, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contracts",
            tuple(sorted(self.contracts, key=lambda entry: entry.key)),
        )
        object.__setattr__(
            self,
            "compatible_changes",
            tuple(
                sorted(
                    self.compatible_changes,
                    key=lambda item: (item.contract, str(item.contract_version), item.reason),
                )
            ),
        )
        keys = [entry.key for entry in self.contracts]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"Duplicate contracts: {', '.join(duplicates)}")
        if self.registry_name is not None or self.registry_contract_version is not None:
            if self.registry_name is None or self.registry_contract_version is None:
                raise ValueError("registry name and contract_version must be declared together")
            registry_key = f"family-registry:{self.registry_name}"
            registry_entry = next(
                (entry for entry in self.contracts if entry.key == registry_key),
                None,
            )
            if registry_entry is None:
                raise ValueError(f"registry entry {registry_key} is required")
            if registry_entry.contract_version != self.registry_contract_version:
                raise ValueError(
                    "registry contract_version must match "
                    f"{registry_key} contract_version"
                )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format_version": self.format_version,
            "package": {
                "name": self.package_name,
                "version": self.package_version,
            },
            "contracts": [entry.to_payload() for entry in sorted(self.contracts, key=lambda item: item.key)],
        }
        if self.registry_name is not None or self.registry_contract_version is not None:
            payload["registry"] = {
                "name": self.registry_name,
                "contract_version": (
                    None
                    if self.registry_contract_version is None
                    else str(self.registry_contract_version)
                ),
            }
        if self.compatible_changes:
            payload["compatible_changes"] = [
                marker.to_payload()
                for marker in sorted(
                    self.compatible_changes,
                    key=lambda item: (item.contract, str(item.contract_version), item.reason),
                )
            ]
        return payload

    def to_yaml(self) -> bytes:
        return yaml.safe_dump(
            self.to_payload(),
            allow_unicode=False,
            sort_keys=False,
        ).encode("utf-8")

    @classmethod
    def from_yaml(cls, payload: bytes | str) -> ContractManifest:
        loaded = yaml.safe_load(payload) or {}
        package = loaded.get("package") or {}
        registry = loaded.get("registry") or {}
        registry_version = registry.get("contract_version")
        return cls(
            format_version=int(loaded["format_version"]),
            package_name=str(package["name"]),
            package_version=str(package["version"]),
            registry_name=registry.get("name"),
            registry_contract_version=(
                None
                if registry_version is None
                else contract_version(str(registry_version))
            ),
            contracts=tuple(
                ContractEntry.from_payload(dict(entry))
                for entry in loaded.get("contracts", ())
            ),
            compatible_changes=tuple(
                CompatibilityMarker.from_payload(dict(marker))
                for marker in loaded.get("compatible_changes", ())
            ),
        )


@dataclass(frozen=True)
class ContractCheckReport:
    bumped: tuple[str, ...] = ()
    compatible: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


def check_contract_manifest(
    previous: ContractManifest,
    current: ContractManifest,
) -> ContractCheckReport:
    previous_entries = {entry.key: entry for entry in previous.contracts}
    current_entries = {entry.key: entry for entry in current.contracts}
    markers = {
        (marker.contract, str(marker.contract_version))
        for marker in current.compatible_changes
    }

    bumped: list[str] = []
    compatible: list[str] = []
    failed: list[str] = []
    unchanged: list[str] = []

    for key in sorted(previous_entries.keys() & current_entries.keys()):
        old = previous_entries[key]
        new = current_entries[key]
        if old.body == new.body and old.contract_version == new.contract_version:
            unchanged.append(key)
            continue
        if old.contract_version != new.contract_version:
            bumped.append(key)
            continue
        if (key, str(new.contract_version)) in markers:
            compatible.append(key)
            continue
        failed.append(key)

    report = ContractCheckReport(
        bumped=tuple(bumped),
        compatible=tuple(compatible),
        failed=tuple(failed),
        unchanged=tuple(unchanged),
        added=tuple(sorted(current_entries.keys() - previous_entries.keys())),
        removed=tuple(sorted(previous_entries.keys() - current_entries.keys())),
    )
    if report.failed:
        raise ContractManifestError(
            "Contract body changed without version bump or compatibility marker: "
            + ", ".join(report.failed)
        )
    return report


def _normalize_payload(value: Any) -> Any:
    try:
        return normalize_payload(value)
    except TypeError as exc:
        message = str(exc).replace("canonical", "contract")
        raise TypeError(message) from exc


def _normalized_sort_key(value: Any) -> str:
    return canonical_json_text(value)

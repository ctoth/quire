from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

import msgspec
import yaml

from quire.versions import VersionId


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
            contract_version=VersionId(str(payload["contract_version"]), allow_placeholder=False),
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
            contract_version=VersionId(str(payload["contract_version"]), allow_placeholder=False),
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
                else VersionId(str(registry_version), allow_placeholder=False)
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
    if isinstance(value, VersionId):
        return str(value)
    if isinstance(value, msgspec.Struct):
        return _normalize_payload(msgspec.to_builtins(value))
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize_payload(asdict(value))
    if isinstance(value, (set, frozenset)):
        normalized_items = [_normalize_payload(item) for item in value]
        return sorted(normalized_items, key=_normalized_sort_key)
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"Unsupported contract payload dict key: {key!r}")
            normalized[key] = _normalize_payload(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, tuple):
        return [_normalize_payload(item) for item in value]
    if isinstance(value, list):
        return [_normalize_payload(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"Unsupported contract payload value: {value!r}")


def _normalized_sort_key(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )

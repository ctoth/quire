from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

import msgspec

from quire.versions import VersionId


def normalize_payload(value: Any) -> Any:
    """Normalize domain payloads into canonical JSON-compatible values."""
    if isinstance(value, VersionId):
        return str(value)
    if isinstance(value, msgspec.Struct):
        return normalize_payload(msgspec.to_builtins(value))
    if is_dataclass(value) and not isinstance(value, type):
        return normalize_payload(asdict(value))
    if isinstance(value, (set, frozenset)):
        normalized_items = [normalize_payload(item) for item in value]
        return sorted(normalized_items, key=canonical_json_text)
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"Unsupported canonical payload dict key: {key!r}")
            normalized[key] = normalize_payload(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, tuple):
        return [normalize_payload(item) for item in value]
    if isinstance(value, list):
        return [normalize_payload(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"Unsupported canonical payload value: {value!r}")


def canonical_json_text(payload: Any) -> str:
    """Return compact, stable JSON for a normalized domain payload."""
    return json.dumps(
        normalize_payload(payload),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_json_bytes(payload: Any) -> bytes:
    """Return UTF-8 bytes for the canonical JSON representation."""
    return canonical_json_text(payload).encode("utf-8")

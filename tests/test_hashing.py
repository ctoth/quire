from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

import msgspec
import pytest

from quire.contracts import _normalize_payload
from quire.hashing import canonical_json_bytes, canonical_json_sha256
from quire.versions import VersionId


class StructPayload(msgspec.Struct, frozen=True):
    name: str
    values: tuple[int, ...]


@dataclass(frozen=True)
class DataclassPayload:
    name: str
    tags: frozenset[str]


def test_canonical_json_bytes_sorts_keys_and_uses_compact_utf8() -> None:
    left = {"b": [2, 1], "a": {"text": "\u00e9"}}
    right = {"a": {"text": "\u00e9"}, "b": [2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_json_bytes(left) == b'{"a":{"text":"\xc3\xa9"},"b":[2,1]}'


def test_canonical_json_sha256_uses_canonical_bytes() -> None:
    payload = {"b": 2, "a": 1}
    expected = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()

    assert canonical_json_sha256(payload) == f"sha256:{expected}"


def test_canonical_json_bytes_uses_contract_payload_normalization() -> None:
    payload = {
        "dataclass": DataclassPayload(name="entry", tags=frozenset({"z", "a"})),
        "items": {"b", "a"},
        "struct": StructPayload(name="node", values=(2, 1)),
        "tuple": ("x", 1),
        "version": VersionId("2026.04.27", allow_placeholder=False),
    }
    normalized = _normalize_payload(payload)

    assert canonical_json_bytes(payload) == canonical_json_bytes(normalized)
    assert canonical_json_sha256(payload) == canonical_json_sha256(normalized)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_bytes_rejects_non_json_float_values(value: float) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_json_bytes({"value": value})

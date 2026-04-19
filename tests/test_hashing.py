from __future__ import annotations

import hashlib

from quire.hashing import canonical_json_bytes, canonical_json_sha256


def test_canonical_json_bytes_sorts_keys_and_uses_compact_utf8() -> None:
    left = {"b": [2, 1], "a": {"text": "\u00e9"}}
    right = {"a": {"text": "\u00e9"}, "b": [2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_json_bytes(left) == b'{"a":{"text":"\xc3\xa9"},"b":[2,1]}'


def test_canonical_json_sha256_uses_canonical_bytes() -> None:
    payload = {"b": 2, "a": 1}
    expected = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()

    assert canonical_json_sha256(payload) == f"sha256:{expected}"

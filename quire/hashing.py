from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(payload: Any) -> bytes:
    """Encode JSON-compatible payloads with stable, compact ordering."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_json_sha256(payload: Any) -> str:
    """Return ``sha256:<hex>`` for a stable JSON representation."""
    return f"sha256:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"

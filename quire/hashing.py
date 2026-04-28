from __future__ import annotations

import hashlib
from typing import Any

from quire.canonical import canonical_json_bytes as _canonical_json_bytes


def canonical_json_bytes(payload: Any) -> bytes:
    """Encode supported domain payloads with stable, compact ordering."""
    return _canonical_json_bytes(payload)


def canonical_json_sha256(payload: Any) -> str:
    """Return ``sha256:<hex>`` for a stable JSON representation."""
    return f"sha256:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"

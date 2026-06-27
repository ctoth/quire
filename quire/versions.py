from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VersionId:
    value: str

    def __init__(self, value: str) -> None:
        normalized = value.strip()
        if not normalized:
            raise ValueError("VersionId cannot be empty")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

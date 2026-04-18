from __future__ import annotations

from dataclasses import dataclass

_PLACEHOLDER_VALUES = frozenset({"0", "0.0", "0.1", "1", "1.0"})


@dataclass(frozen=True, order=True)
class VersionId:
    value: str

    def __init__(self, value: str, *, allow_placeholder: bool = True) -> None:
        normalized = value.strip()
        if not normalized:
            raise ValueError("VersionId cannot be empty")
        if not allow_placeholder and normalized in _PLACEHOLDER_VALUES:
            raise ValueError(f"Placeholder contract version is not allowed: {normalized}")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

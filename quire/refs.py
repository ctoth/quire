from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RefName:
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if value != self.value or not value:
            raise ValueError(f"Invalid ref name: {self.value!r}")
        if not value.startswith("refs/"):
            raise ValueError(f"Ref name must start with 'refs/': {value!r}")
        if "\\" in value or "//" in value or value.endswith("/"):
            raise ValueError(f"Invalid ref name: {value!r}")
        object.__setattr__(self, "value", value)

    def as_bytes(self) -> bytes:
        return self.value.encode("utf-8")

    def __str__(self) -> str:
        return self.value

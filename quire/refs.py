from __future__ import annotations

from dataclasses import dataclass, make_dataclass
from typing import Any


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


def single_field_ref_type(
    type_name: str,
    field_name: str,
    *,
    module: str | None = None,
) -> type[Any]:
    """Create an immutable family ref type with one string key field."""
    if not type_name or not type_name.isidentifier():
        raise ValueError(f"invalid ref type name: {type_name!r}")
    if not field_name or not field_name.isidentifier():
        raise ValueError(f"invalid ref field name: {field_name!r}")
    ref_type = make_dataclass(type_name, [(field_name, str)], frozen=True)
    if module is not None:
        ref_type.__module__ = module
    return ref_type


def singleton_ref_type(
    type_name: str,
    *,
    module: str | None = None,
) -> type[Any]:
    """Create an immutable family ref type for singleton artifacts."""
    if not type_name or not type_name.isidentifier():
        raise ValueError(f"invalid ref type name: {type_name!r}")
    ref_type = make_dataclass(type_name, [], frozen=True)
    if module is not None:
        ref_type.__module__ = module
    return ref_type

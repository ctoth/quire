from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


def python_type_path(python_type: type[Any]) -> str:
    return f"{python_type.__module__}.{python_type.__qualname__}"


@dataclass(frozen=True)
class SchemaForeignKey:
    name: str
    source_family: str
    source_field: str
    target_family: str
    target_field: str = "id"
    required: bool = True
    many: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "many": self.many,
            "name": self.name,
            "required": self.required,
            "source_family": self.source_family,
            "source_field": self.source_field,
            "target_family": self.target_family,
            "target_field": self.target_field,
        }


@dataclass(frozen=True)
class SchemaField:
    name: str
    python_type: str
    sql_type: object
    nullable: bool = True
    primary_key: bool = False
    foreign_key: SchemaForeignKey | None = None
    index: bool = False
    unique: bool = False
    generated: bool = False
    default: object | None = None
    default_sql: str | None = None
    json_value_object: bool = False
    enum_values: tuple[str, ...] = ()
    search: bool = False
    vector_dimensions: int | None = None
    source_local_only: bool = False
    canonical_only: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        return {
            "canonical_only": self.canonical_only,
            "default": self.default,
            "default_sql": self.default_sql,
            "enum_values": self.enum_values,
            "foreign_key": None if self.foreign_key is None else self.foreign_key.payload(),
            "generated": self.generated,
            "index": self.index,
            "json_value_object": self.json_value_object,
            "metadata": dict(sorted(self.metadata.items())),
            "name": self.name,
            "nullable": self.nullable,
            "primary_key": self.primary_key,
            "python_type": self.python_type,
            "search": self.search,
            "source_local_only": self.source_local_only,
            "sql_type": _payload(self.sql_type),
            "unique": self.unique,
            "vector_dimensions": self.vector_dimensions,
        }


@dataclass(frozen=True)
class SchemaIndex:
    name: str
    fields: tuple[str, ...]
    unique: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "fields": self.fields,
            "name": self.name,
            "unique": self.unique,
        }


@dataclass(frozen=True)
class SchemaObject:
    name: str
    family_name: str
    artifact_family_name: str
    artifact_contract_version: str
    model_path: str
    fields: tuple[SchemaField, ...]
    lifecycle_states: tuple[str, ...] = ()
    indexes: tuple[SchemaIndex, ...] = ()
    semantic_metadata: Mapping[str, object] = field(default_factory=dict)

    def field(self, name: str) -> SchemaField:
        for schema_field in self.fields:
            if schema_field.name == name:
                return schema_field
        raise KeyError(f"unknown schema field {name!r} on {self.name!r}")

    def payload(self) -> dict[str, object]:
        return {
            "family": {
                "artifact_contract_version": self.artifact_contract_version,
                "artifact_family": self.artifact_family_name,
                "name": self.family_name,
            },
            "fields": tuple(field.payload() for field in _sort_by_name(self.fields)),
            "indexes": tuple(index.payload() for index in _sort_by_name(self.indexes)),
            "lifecycle_states": self.lifecycle_states,
            "model": self.model_path,
            "name": self.name,
            "semantic_metadata": dict(sorted(self.semantic_metadata.items())),
        }


def _payload(value: object) -> object:
    payload = getattr(value, "payload", None)
    if callable(payload):
        return payload()
    return value


def _sort_by_name(items: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(sorted(items, key=lambda item: item.name))

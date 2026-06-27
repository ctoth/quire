from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import UnionType
from typing import Union, cast, get_args, get_origin

from quire.schema_ir import python_type_path


@dataclass(frozen=True)
class SqlTypeSpec:
    storage_kind: str
    ddl_name: str
    sqlalchemy_type: str
    python_type: str
    enum_values: tuple[str, ...] = ()
    value_object_type: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "ddl_name": self.ddl_name,
            "enum_values": self.enum_values,
            "python_type": self.python_type,
            "sqlalchemy_type": self.sqlalchemy_type,
            "storage_kind": self.storage_kind,
            "value_object_type": self.value_object_type,
        }


def python_type_to_sql(
    python_type: object,
    *,
    json_value_object: bool = False,
    enum_type: type[Enum] | None = None,
) -> SqlTypeSpec:
    python_type = optional_inner_type(python_type)
    if json_value_object:
        return SqlTypeSpec(
            storage_kind="json",
            ddl_name="TEXT",
            sqlalchemy_type="JsonValueObject",
            python_type=python_type_path(python_type),
            value_object_type=python_type_path(python_type),
        )
    if enum_type is not None:
        resolved_enum = enum_type
    elif isinstance(python_type, type) and issubclass(python_type, Enum):
        resolved_enum = cast(type[Enum], python_type)
    else:
        resolved_enum = None
    if resolved_enum is not None:
        return SqlTypeSpec(
            storage_kind="enum",
            ddl_name="TEXT",
            sqlalchemy_type="EnumText",
            python_type=python_type_path(python_type),
            enum_values=tuple(str(member.value) for member in resolved_enum),
        )
    if python_type is str:
        return _scalar("text", "TEXT", "Text", python_type)
    if python_type is int:
        return _scalar("integer", "INTEGER", "Integer", python_type)
    if python_type is float:
        return _scalar("real", "REAL", "Float", python_type)
    if python_type is bool:
        return _scalar("boolean", "BOOLEAN", "Boolean", python_type)
    if python_type is bytes:
        return _scalar("blob", "BLOB", "LargeBinary", python_type)
    return _scalar("text", "TEXT", "Text", python_type)


def optional_inner_type(python_type: object) -> object:
    args = _union_args(python_type)
    if args is None:
        return python_type
    non_none_args = tuple(arg for arg in args if arg is not type(None))
    if len(non_none_args) == 1 and len(non_none_args) != len(args):
        return non_none_args[0]
    return python_type


def is_optional_type(python_type: object) -> bool:
    return optional_inner_type(python_type) is not python_type


def _union_args(python_type: object) -> tuple[object, ...] | None:
    origin = get_origin(python_type)
    if origin is Union or origin is UnionType or isinstance(python_type, UnionType):
        return get_args(python_type)
    return None


def _scalar(storage_kind: str, ddl_name: str, sqlalchemy_type: str, python_type: object) -> SqlTypeSpec:
    return SqlTypeSpec(
        storage_kind=storage_kind,
        ddl_name=ddl_name,
        sqlalchemy_type=sqlalchemy_type,
        python_type=python_type_path(python_type),
    )

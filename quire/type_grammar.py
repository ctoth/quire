"""Lossless ``{kind, ...}`` type-grammar for Python type objects.

This module replaces the lossy :func:`quire.schema_ir.python_type_path`
boundary, which collapses type objects to strings via ``repr()`` and
``module.qualname`` joins and therefore cannot be parsed back into the original
type object. A ``Literal['rule', 'exception']`` round-tripped through
``python_type_path`` becomes the *string* ``"typing.Literal['rule', 'exception']"``;
``str | None`` becomes the *string* ``"...str | None"`` with its argument
structure flattened into text.

Here every type object is emitted as a JSON-serializable structural node and can
be reconstructed exactly. The invariant the spike proves is::

    node_to_type(type_to_node(t)) == t

for every type ``t`` in the supported grammar, and JSON survival::

    node_to_type(json.loads(json.dumps(type_to_node(t)))) == t

The grammar fails *loud*: a type it cannot structurally represent raises a
``TypeError`` naming the offender rather than degrading to ``repr()``. A silent
lossy fallback is exactly the bug this module exists to eliminate.

Supported kinds:

* ``{"kind": "none"}`` — ``type(None)`` / ``NoneType``.
* ``{"kind": "any"}`` — ``typing.Any``.
* ``{"kind": "ellipsis"}`` — ``Ellipsis`` (``...``), as it appears in
  ``tuple[X, ...]``.
* ``{"kind": "name", "module": ..., "qualname": ...}`` — a plain ``type``
  (builtins live in module ``builtins``; module-level classes carry their own
  ``__module__``).
* ``{"kind": "newtype", "module": ..., "qualname": ...}`` — a
  ``typing.NewType('Name', Supertype)``. A module-level NewType has stable
  identity, so it round-trips by import (same resolver path as ``name``).
* ``{"kind": "literal", "values": [...]}`` — ``Literal[...]``. Each value is a
  JSON primitive (str / int / bool / ``None``) or, for an enum member,
  ``{"enum": {"module": ..., "qualname": ..., "name": ...}}``. Order preserved.
* ``{"kind": "union", "args": [node, ...]}`` — ``Union[...]``, ``X | Y``
  (``types.UnionType``) and ``Optional[X]`` (== ``X | None``). Order preserved.
* ``{"kind": "generic", "origin": node, "args": [node, ...]}`` — a
  parameterized generic such as ``tuple[str, ...]``, ``frozenset[str]``,
  ``Mapping[str, object]``, ``list[X]``, ``dict[K, V]``. ``origin`` is the
  unsubscripted origin as a ``name`` node.
"""

from __future__ import annotations

import enum
import functools
import importlib
import operator
from types import UnionType
from typing import Any, Literal, NewType, Union, get_args, get_origin

__all__ = ["type_to_node", "node_to_type"]

# JSON primitive literal values. ``bool`` is intentionally listed separately
# even though it is a subclass of ``int``: JSON preserves the ``true``/``false``
# vs integer distinction, so ``Literal[True]`` and ``Literal[1]`` survive a JSON
# round-trip as distinct types.
_LiteralPrimitive = (str, bool, int, type(None))


def _name_node(t: type) -> dict[str, object]:
    return {"kind": "name", "module": t.__module__, "qualname": t.__qualname__}


def _literal_value_node(value: object) -> object:
    if isinstance(value, enum.Enum):
        member_cls = type(value)
        return {
            "enum": {
                "module": member_cls.__module__,
                "qualname": member_cls.__qualname__,
                "name": value.name,
            }
        }
    # ``bool`` first: it is a subclass of ``int`` and of nothing else we care
    # about, but the explicit tuple already orders bool before int.
    if isinstance(value, _LiteralPrimitive):
        return value
    raise TypeError(
        f"Cannot structurally represent Literal value {value!r} "
        f"(type {type(value)!r}); only JSON primitives and enum members are supported"
    )


def type_to_node(t: object) -> dict[str, object]:
    """Emit a JSON-serializable ``{"kind": ...}`` node for a type object.

    Raises :class:`TypeError` for any type it cannot structurally represent —
    never falls back to ``repr()``.
    """
    if t is type(None):
        return {"kind": "none"}
    if t is Any:
        return {"kind": "any"}
    if t is Ellipsis:
        return {"kind": "ellipsis"}

    origin = get_origin(t)

    # Literal[...] — preserve value order.
    if origin is Literal:
        return {
            "kind": "literal",
            "values": [_literal_value_node(v) for v in get_args(t)],
        }

    # Unions: typing.Union, X | Y (types.UnionType), Optional[X] (== X | None).
    if origin is Union or origin is UnionType or isinstance(t, UnionType):
        return {
            "kind": "union",
            "args": [type_to_node(arg) for arg in get_args(t)],
        }

    # Parameterized generics: origin is a plain type, args present.
    if origin is not None:
        if not isinstance(origin, type):
            raise TypeError(
                f"Cannot structurally represent generic with non-type origin "
                f"{origin!r} (from {t!r})"
            )
        return {
            "kind": "generic",
            "origin": _name_node(origin),
            "args": [type_to_node(arg) for arg in get_args(t)],
        }

    # NewType: a module-level ``NewType('Name', Supertype)`` has stable identity
    # by import, so identity-by-import is the correct round-trip (same resolver
    # path as the ``name`` kind). On Python >= 3.10 ``typing.NewType`` is a class,
    # so ``isinstance(t, NewType)`` is the clean check. Checked explicitly and
    # early because a NewType is NOT a ``type`` and ``get_origin`` returns None,
    # so neither the generic branch nor the plain-type branch would catch it.
    if isinstance(t, NewType):
        return {"kind": "newtype", "module": t.__module__, "qualname": t.__qualname__}

    # Plain type: builtin or module-level class.
    if isinstance(t, type):
        return _name_node(t)

    raise TypeError(
        f"Cannot structurally represent type object {t!r} (type {type(t)!r}); "
        f"no supported kind matches — refusing to degrade to repr()"
    )


def _resolve_name(module: str, qualname: str) -> object:
    obj: object = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def _node_to_literal_value(value: object) -> object:
    if isinstance(value, dict) and "enum" in value:
        spec = value["enum"]
        if not isinstance(spec, dict):
            raise ValueError(f"Malformed enum literal node: {value!r}")
        member_cls = _resolve_name(str(spec["module"]), str(spec["qualname"]))
        if not (isinstance(member_cls, type) and issubclass(member_cls, enum.Enum)):
            raise ValueError(
                f"enum literal node resolved to non-enum {member_cls!r}"
            )
        return member_cls[str(spec["name"])]
    if isinstance(value, _LiteralPrimitive):
        return value
    raise ValueError(f"Malformed literal value node: {value!r}")


def node_to_type(node: dict[str, object]) -> object:
    """Reconstruct the original type object from a structural node.

    Inverse of :func:`type_to_node`. Resolves ``name`` / ``enum`` nodes by
    :func:`importlib.import_module` plus a ``getattr`` walk over the dotted
    qualname.
    """
    if not isinstance(node, dict):
        raise ValueError(f"Expected a node dict, got {node!r}")
    kind = node.get("kind")

    if kind == "none":
        return type(None)
    if kind == "any":
        return Any
    if kind == "ellipsis":
        return Ellipsis
    if kind == "name":
        return _resolve_name(str(node["module"]), str(node["qualname"]))
    if kind == "newtype":
        # A module-level NewType has stable identity, so resolving by import +
        # getattr-walk returns the same NewType object and ``X == X`` holds by
        # identity — exactly the ``name`` resolver path.
        return _resolve_name(str(node["module"]), str(node["qualname"]))
    if kind == "literal":
        values = node["values"]
        if not isinstance(values, list):
            raise ValueError(f"literal node values must be a list: {node!r}")
        reconstructed = tuple(_node_to_literal_value(v) for v in values)
        return Literal[reconstructed]
    if kind == "union":
        args = node["args"]
        if not isinstance(args, list):
            raise ValueError(f"union node args must be a list: {node!r}")
        members = [node_to_type(arg) for arg in args]
        if not members:
            raise ValueError(f"union node has no args: {node!r}")
        # functools.reduce(operator.or_, members) yields a UnionType that
        # compares equal to typing.Union[...] / Optional[...] / X | Y for the
        # same ordered members (verified: Optional[str] == str | None ==
        # Union[str, None]). Order is preserved from the node.
        return functools.reduce(operator.or_, members)
    if kind == "generic":
        origin_node = node["origin"]
        if not isinstance(origin_node, dict):
            raise ValueError(f"generic node origin must be a node dict: {node!r}")
        origin = node_to_type(origin_node)
        args = node["args"]
        if not isinstance(args, list):
            raise ValueError(f"generic node args must be a list: {node!r}")
        reconstructed_args = tuple(node_to_type(arg) for arg in args)
        return origin[reconstructed_args]  # type: ignore[index]

    raise ValueError(f"Unknown node kind {kind!r} in {node!r}")

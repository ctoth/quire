"""Tests for the lossless ``{kind, ...}`` type-grammar.

The core invariant is structural round-trip losslessness::

    node_to_type(type_to_node(t)) == t

plus JSON survival of the node for enum-free types::

    node_to_type(json.loads(json.dumps(type_to_node(t)))) == t

The explicit cases below are the real drift cases that motivated the spike
(``Literal['rule', 'exception']``, ``str | None``, ``tuple[str, ...]``, ...).
The hypothesis property test asserts the invariant for arbitrarily nested
generated types.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Mapping
from typing import Any, Literal, Optional

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quire.type_grammar import node_to_type, type_to_node


class Flavor(enum.Enum):
    RULE = "rule"
    EXCEPTION = "exception"


# The explicit drift cases that motivated the grammar. Enum-free so they also
# exercise the JSON round-trip below.
_EXPLICIT_CASES: list[object] = [
    Literal["rule", "exception"],
    str | None,
    int | None,
    Optional[str],
    tuple[str, ...],
    frozenset[str],
    Mapping[str, object],
    int,
    str,
    bool,
    type(None),
    Any,
    Optional[Literal["a", "b"]],  # == Literal['a', 'b'] | None
]


@pytest.mark.parametrize("typ", _EXPLICIT_CASES)
def test_explicit_cases_round_trip(typ: object) -> None:
    """Every motivating drift case round-trips structurally and is JSON-serializable."""
    node = type_to_node(typ)
    # The node is JSON-serializable.
    json.dumps(node)
    assert node_to_type(node) == typ


@pytest.mark.parametrize("typ", _EXPLICIT_CASES)
def test_explicit_cases_survive_json(typ: object) -> None:
    """The node survives a full JSON serialize/deserialize round-trip."""
    node = type_to_node(typ)
    revived = node_to_type(json.loads(json.dumps(node)))
    assert revived == typ


def test_enum_literal_round_trips() -> None:
    """A Literal over enum members reconstructs to the same Literal."""
    typ = Literal[Flavor.RULE, Flavor.EXCEPTION]
    node = type_to_node(typ)
    # Enum nodes are dicts but still JSON-serializable.
    json.dumps(node)
    assert node_to_type(node) == typ
    # And the JSON round-trip survives too.
    assert node_to_type(json.loads(json.dumps(node))) == typ


def test_kind_tags_are_as_specified() -> None:
    """Spot-check the emitted kind tags for the headline cases."""
    assert type_to_node(type(None)) == {"kind": "none"}
    assert type_to_node(Any) == {"kind": "any"}
    assert type_to_node(...) == {"kind": "ellipsis"}
    assert type_to_node(int) == {
        "kind": "name",
        "module": "builtins",
        "qualname": "int",
    }
    lit = type_to_node(Literal["rule", "exception"])
    assert lit == {"kind": "literal", "values": ["rule", "exception"]}
    union = type_to_node(str | None)
    assert union["kind"] == "union"
    tup = type_to_node(tuple[str, ...])
    assert tup["kind"] == "generic"
    assert tup["origin"] == {"kind": "name", "module": "builtins", "qualname": "tuple"}
    tup_args = tup["args"]
    assert isinstance(tup_args, list)
    assert tup_args[-1] == {"kind": "ellipsis"}


def test_fail_loud_on_lambda() -> None:
    """An unrepresentable object raises naming the offender, never repr() fallback."""
    with pytest.raises(TypeError) as excinfo:
        type_to_node(lambda x: x)
    assert "lambda" in str(excinfo.value).lower() or "function" in str(excinfo.value).lower()


def test_fail_loud_on_unrepresentable_literal_value() -> None:
    """A Literal carrying a non-primitive, non-enum value fails loud."""
    # bytes is not a JSON primitive nor an enum member -> must raise.
    with pytest.raises(TypeError):
        type_to_node(Literal[b"x"])  # type: ignore[valid-type]


# ---------------------------------------------------------------------------
# Property test: arbitrary nested types must round-trip losslessly.
# ---------------------------------------------------------------------------

_PRIMITIVES: list[object] = [int, str, bool, float, bytes, type(None)]

_literal_values = st.one_of(
    st.text(max_size=5),
    st.integers(min_value=-5, max_value=5),
)


def _types_strategy() -> st.SearchStrategy[object]:
    primitives = st.sampled_from(_PRIMITIVES)

    literals = st.lists(_literal_values, min_size=1, max_size=4, unique=True).map(
        lambda vals: Literal[tuple(vals)]
    )

    leaves = st.one_of(primitives, literals)

    def extend(children: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
        unions = st.lists(children, min_size=2, max_size=3).map(
            lambda members: __import__("functools").reduce(
                __import__("operator").or_, members
            )
        )
        tuple_ellipsis = children.map(lambda c: tuple[(c, ...)])
        frozensets = children.map(lambda c: frozenset[c])
        lists = children.map(lambda c: list[c])
        mappings = st.tuples(children, children).map(lambda kv: Mapping[kv[0], kv[1]])
        dicts = st.tuples(children, children).map(lambda kv: dict[kv[0], kv[1]])
        return st.one_of(unions, tuple_ellipsis, frozensets, lists, mappings, dicts)

    return st.recursive(leaves, extend, max_leaves=8)


@given(typ=_types_strategy())
def test_round_trip_property(typ: object) -> None:
    """node_to_type(type_to_node(t)) == t for arbitrary nested types."""
    assert node_to_type(type_to_node(typ)) == typ

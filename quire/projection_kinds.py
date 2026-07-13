"""Registrable projection kinds.

A *projection kind* decides, per charter field, whether that field participates
in some projection and what that projection contributes to the schema contract.

Historically each projection (sql index/unique, foreign keys, graph node, graph
edge, artifact dependency, ...) was a hardcoded ``if field.<flag>`` branch
duplicated across the charter -> schema -> consumer stack. That made adding a new
kind (``view``, ``hyperedge``, ``opinion``, ``embedding-text``) a six-site edit.

This registry inverts that: a kind registers itself and the consumer loops
iterate ``iter_projection_kinds()`` instead of testing fixed flags. Built-in
kinds register here; downstream packages register new kinds without editing
:class:`~quire.charters.CharterField` or the consumers.

The protocol is intentionally minimal. ``name`` + ``applies`` + ``schema_payload``
are what every consumer needs in common (iteration, dispatch, deterministic
contract hashing). Output-producing methods (column specs for the SQL consumer,
per-record emission for the graph consumer) are declared by the consumer-specific
sub-protocols that live next to the projection outputs they produce, so this
module stays free of import cycles with :mod:`quire.projections`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from quire.charters import CharterField


@runtime_checkable
class ProjectionKind(Protocol):
    """A registrable projection kind.

    ``name`` is the stable identifier used both as the registry key and as the
    key under which :meth:`schema_payload` output is folded into a field's
    contract body, so it must be deterministic and unique.
    """

    @property
    def name(self) -> str: ...

    def applies(self, field: CharterField) -> bool:
        """Whether ``field`` opts into this projection kind."""
        ...

    def schema_payload(self, field: CharterField) -> Mapping[str, object]:
        """Deterministic contract-body contribution for an applying ``field``.

        Folded into :meth:`quire.schema_ir.SchemaField.payload` so that adding or
        changing a kind's participation is visible to ``check_contract_manifest``.
        Only called when :meth:`applies` returned ``True``.
        """
        ...


_REGISTRY: dict[str, ProjectionKind] = {}


def register_projection_kind(kind: ProjectionKind) -> ProjectionKind:
    """Register ``kind`` by its ``name``. Returns it (usable as a decorator).

    Re-registering the identical object is a no-op; registering a different
    object under an existing name is an error (no silent shadowing).
    """

    name = kind.name
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not kind:
        raise ValueError(f"projection kind {name!r} is already registered")
    _REGISTRY[name] = kind
    return kind


def iter_projection_kinds() -> Iterator[ProjectionKind]:
    """Yield registered kinds in deterministic (name-sorted) order."""

    for name in sorted(_REGISTRY):
        yield _REGISTRY[name]


def projection_kind(name: str) -> ProjectionKind:
    """Return the kind registered under ``name`` or raise ``KeyError``."""

    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown projection kind {name!r}") from None


def unregister_projection_kind(name: str) -> None:
    """Remove ``name`` from the registry. Test/teardown seam; idempotent."""

    _REGISTRY.pop(name, None)

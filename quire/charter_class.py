"""Declarative charter-class shape.

The authored declarative class *is* the typed document (a ``msgspec.Struct``
subclass). Its attributes are exactly the document fields, named as the document
names. Per-field charter metadata travels in ``Annotated[T, charter_field(...)]``
markers; charter-level metadata travels in the ``@charter(...)`` decorator
arguments. The decorator runs ``get_type_hints(cls, include_extras=True)``,
builds a :class:`~quire.charters.FamilyCharter` (reusing the existing builder
internals so the derived charter is behaviour-identical to a hand-written one),
and attaches it as ``cls.__charter__``.

This module never reimplements the document projection rules: it lowers the
declarative class into the same :class:`~quire.charters.CharterField` /
:class:`~quire.charters.FamilyCharter` objects the hand-written builder produces,
so ``generated_document`` / ``document_codec`` / ``to_schema_object`` are driven
by the SAME code path (``quire.charters``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import (
    Annotated,
    Any,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

import msgspec

from quire.artifacts import ArtifactFamily, ArtifactPlacementPolicy, FlatYamlPlacement
from quire.charters import (
    CharterField,
    CharterFtsIndex,
    CharterIndex,
    CharterPolymorphicModel,
    CharterRelationship,
    CharterVectorCache,
    FamilyCharter,
    FamilyModel,
)
from quire.documents.batch import DocumentBatchSpec
from quire.families import FamilyDefinition
from quire.lifecycle import FamilyState, FamilyTransition
from quire.references import ForeignKeySpec
from quire.sql_types import is_optional_type, optional_inner_type
from quire.versions import VersionId


__all__ = [
    "CharterDoc",
    "charter",
    "charter_field",
    "column",
    "ColumnSpec",
    "CharterFieldSpec",
]


# ---------------------------------------------------------------------------
# CharterDoc base
# ---------------------------------------------------------------------------


class CharterDoc(msgspec.Struct, forbid_unknown_fields=True):
    """Thin ``msgspec.Struct`` base for declarative charter documents.

    A declarative charter class subclasses this; its annotated attributes are
    the document fields. ``forbid_unknown_fields=True`` mirrors the generated
    document produced by :meth:`FamilyCharter.generated_document`.
    """


# ---------------------------------------------------------------------------
# Per-field metadata marker
# ---------------------------------------------------------------------------


_UNSET: Any = object()


@dataclass(frozen=True)
class CharterFieldSpec:
    """Per-field charter metadata carried inside ``Annotated[T, ...]``.

    ``msgspec`` ignores unknown ``Annotated`` metadata, so the annotation stays a
    plain typed field for the document while ``@charter`` recovers this marker via
    ``get_type_hints(include_extras=True)`` to build the matching
    :class:`~quire.charters.CharterField`.
    """

    column_name: str | None = None
    nullable: bool | None = None
    json: bool = False
    artifact: bool = False
    primary_key: bool = False
    foreign_key: ForeignKeySpec | None = None
    foreign_keys: tuple[ForeignKeySpec, ...] = ()
    enum_type: type[Enum] | None = None
    default_sql: str | None = None
    order: int | None = None
    index: bool = False
    unique: bool = False
    generated: bool = False
    json_value_object: bool = False
    search: bool = False
    vector_dimensions: int | None = None
    source_local_only: bool = False
    canonical_only: bool = False
    artifact_name: str | None = None
    artifact_dependency: bool = False
    graph_node_label: bool = False
    graph_metadata: bool = False
    graph_edge: bool = False
    graph_edge_kind: str | None = None
    graph_edge_source_field: str | None = None
    graph_edge_source_family: str | None = None
    local_id: bool = False
    local_id_policy: str | None = None
    contract_version: VersionId | None = None
    states: frozenset[str] | None = None
    metadata: Mapping[str, object] = _dc_field(default_factory=dict)


def charter_field(
    *,
    column_name: str | None = None,
    nullable: bool | None = None,
    json: bool = False,
    artifact: bool = False,
    primary_key: bool = False,
    foreign_key: ForeignKeySpec | None = None,
    foreign_keys: tuple[ForeignKeySpec, ...] = (),
    enum_type: type[Enum] | None = None,
    default_sql: str | None = None,
    order: int | None = None,
    index: bool = False,
    unique: bool = False,
    generated: bool = False,
    json_value_object: bool = False,
    search: bool = False,
    vector_dimensions: int | None = None,
    source_local_only: bool = False,
    canonical_only: bool = False,
    artifact_name: str | None = None,
    artifact_dependency: bool = False,
    graph_node_label: bool = False,
    graph_metadata: bool = False,
    graph_edge: bool = False,
    graph_edge_kind: str | None = None,
    graph_edge_source_field: str | None = None,
    graph_edge_source_family: str | None = None,
    local_id: bool = False,
    local_id_policy: str | None = None,
    contract_version: VersionId | None = None,
    states: frozenset[str] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> CharterFieldSpec:
    """Build a per-field charter metadata marker for use inside ``Annotated``.

    ``column_name`` renames the storage column relative to the document field
    name (the class attribute). ``json=True`` stores the column as a JSON string
    while the document type stays the annotated nested type. ``nullable`` (when
    passed) overrides the annotation-inferred column nullability and reproduces
    the explicit-nullable document-optionality rule of the hand-written builder.
    """

    return CharterFieldSpec(
        column_name=column_name,
        nullable=nullable,
        json=json,
        artifact=artifact,
        primary_key=primary_key,
        foreign_key=foreign_key,
        foreign_keys=foreign_keys,
        enum_type=enum_type,
        default_sql=default_sql,
        order=order,
        index=index,
        unique=unique,
        generated=generated,
        json_value_object=json_value_object,
        search=search,
        vector_dimensions=vector_dimensions,
        source_local_only=source_local_only,
        canonical_only=canonical_only,
        artifact_name=artifact_name,
        artifact_dependency=artifact_dependency,
        graph_node_label=graph_node_label,
        graph_metadata=graph_metadata,
        graph_edge=graph_edge,
        graph_edge_kind=graph_edge_kind,
        graph_edge_source_field=graph_edge_source_field,
        graph_edge_source_family=graph_edge_source_family,
        local_id=local_id,
        local_id_policy=local_id_policy,
        contract_version=contract_version,
        states=states,
        metadata={} if metadata is None else metadata,
    )


# ---------------------------------------------------------------------------
# Storage-only column spec (document=False)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnSpec:
    """A storage-only column (``document=False``) declared in ``@charter(extra_columns=...)``.

    These are absent from the typed document (so they are not class attributes)
    but exist in the schema / SQLAlchemy model. Primary keys and FK-only columns
    live here.
    """

    name: str
    python_type: object
    nullable: bool | None = None
    json: bool = False
    primary_key: bool = False
    foreign_key: ForeignKeySpec | None = None
    foreign_keys: tuple[ForeignKeySpec, ...] = ()
    enum_type: type[Enum] | None = None
    default: object | None = None
    default_sql: str | None = None
    generated: bool = False
    index: bool = False
    unique: bool = False
    json_value_object: bool = False
    search: bool = False
    vector_dimensions: int | None = None
    source_local_only: bool = False
    canonical_only: bool = False
    artifact: bool = False
    artifact_name: str | None = None
    artifact_dependency: bool = False
    graph_node_label: bool = False
    graph_metadata: bool = False
    graph_edge: bool = False
    graph_edge_kind: str | None = None
    graph_edge_source_field: str | None = None
    graph_edge_source_family: str | None = None
    local_id: bool = False
    local_id_policy: str | None = None
    contract_version: VersionId | None = None
    states: frozenset[str] | None = None
    metadata: Mapping[str, object] = _dc_field(default_factory=dict)


def column(
    name: str,
    python_type: object,
    *,
    nullable: bool | None = None,
    json: bool = False,
    primary_key: bool = False,
    foreign_key: ForeignKeySpec | None = None,
    foreign_keys: tuple[ForeignKeySpec, ...] = (),
    enum_type: type[Enum] | None = None,
    default: object | None = None,
    default_sql: str | None = None,
    generated: bool = False,
    index: bool = False,
    unique: bool = False,
    json_value_object: bool = False,
    search: bool = False,
    vector_dimensions: int | None = None,
    source_local_only: bool = False,
    canonical_only: bool = False,
    artifact: bool = False,
    artifact_name: str | None = None,
    artifact_dependency: bool = False,
    graph_node_label: bool = False,
    graph_metadata: bool = False,
    graph_edge: bool = False,
    graph_edge_kind: str | None = None,
    graph_edge_source_field: str | None = None,
    graph_edge_source_family: str | None = None,
    local_id: bool = False,
    local_id_policy: str | None = None,
    contract_version: VersionId | None = None,
    states: frozenset[str] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> ColumnSpec:
    """Declare a storage-only column (``document=False``) for ``@charter(extra_columns=...)``."""

    return ColumnSpec(
        name=name,
        python_type=python_type,
        nullable=nullable,
        json=json,
        primary_key=primary_key,
        foreign_key=foreign_key,
        foreign_keys=foreign_keys,
        enum_type=enum_type,
        default=default,
        default_sql=default_sql,
        generated=generated,
        index=index,
        unique=unique,
        json_value_object=json_value_object,
        search=search,
        vector_dimensions=vector_dimensions,
        source_local_only=source_local_only,
        canonical_only=canonical_only,
        artifact=artifact,
        artifact_name=artifact_name,
        artifact_dependency=artifact_dependency,
        graph_node_label=graph_node_label,
        graph_metadata=graph_metadata,
        graph_edge=graph_edge,
        graph_edge_kind=graph_edge_kind,
        graph_edge_source_field=graph_edge_source_field,
        graph_edge_source_family=graph_edge_source_family,
        local_id=local_id,
        local_id_policy=local_id_policy,
        contract_version=contract_version,
        states=states,
        metadata={} if metadata is None else metadata,
    )


# ---------------------------------------------------------------------------
# Annotation lowering helpers
# ---------------------------------------------------------------------------


def _split_annotation(hint: object) -> tuple[object, CharterFieldSpec | None]:
    """Return ``(python_type, spec)`` for a (possibly ``Annotated``) type hint.

    The ``python_type`` is the *document* type used by msgspec (the annotation
    with any ``Annotated[...]`` wrapper stripped). The spec is the first
    :class:`CharterFieldSpec` found in the annotation metadata, if any.
    """

    if get_origin(hint) is Annotated:
        args = get_args(hint)
        base = args[0]
        spec: CharterFieldSpec | None = None
        for extra in args[1:]:
            if isinstance(extra, CharterFieldSpec):
                spec = extra
                break
        return base, spec
    return hint, None


def _resolve_enum_type(
    explicit: type[Enum] | None,
    python_type: object,
    is_json: bool,
) -> type[Enum] | None:
    """Pick the ``enum_type`` to pass to :class:`CharterField`.

    Matches the hand-written convention: callers pass ``enum_type=<Enum>`` when
    the field is an enum. The declarative shape derives it from the annotation
    when it is an ``Enum`` subclass and the field is not a JSON blob (json blobs
    force ``enum_type=None`` downstream anyway, but we keep it ``None`` to match
    the hand-written charters which never pass enum_type on json fields).
    """

    if explicit is not None:
        return explicit
    if is_json:
        return None
    if isinstance(python_type, type) and issubclass(python_type, Enum):
        return cast("type[Enum]", python_type)
    return None


def _resolve_nullable(spec_nullable: bool | None, python_type: object) -> bool:
    """Resolve the explicit column nullability for a declarative field.

    The hand-written propstore charters always pass ``nullable=`` explicitly
    (``nullable=False`` for required NON-NULL columns, ``nullable=True`` for
    optional ones). The declarative shape reproduces this by ALWAYS setting
    ``nullable`` explicitly, derived from the annotation when not overridden:

    - ``charter_field(nullable=...)`` given -> that value (override).
    - annotation ``T | None`` -> ``True`` (nullable column, optional doc field).
    - annotation ``T``        -> ``False`` (NON-NULL column, required doc field).

    Setting it explicitly (never leaving it to the ``CharterField`` default) is
    load-bearing: ``_document_python_type`` / ``_field_defaults_to_none`` only
    treat a field as optional when ``_nullable_explicit`` is set, so the derived
    document shape matches the hand-written one exactly.
    """

    if spec_nullable is not None:
        return spec_nullable
    return is_optional_type(python_type)


def _charter_field_from_attribute(
    name: str,
    hint: object,
    default: object,
) -> CharterField:
    """Lower one declarative class attribute into a :class:`CharterField`.

    ``name`` is the document field name (the class attribute). ``hint`` is its
    type hint (possibly ``Annotated``). ``default`` is the msgspec field default
    (``_UNSET`` when the field is required).

    The generated-document field order follows the charter projection's natural
    declaration-index sort, and the CharterField tuple is built in class-attribute
    order, so the document order equals the class declaration order. Author the
    class required-fields-first (which msgspec also requires). When a field must
    sort to a non-natural position to match a hand-written charter, set its
    ``charter_field(order=...)`` explicitly (mirroring ``document_order=``).
    """

    annotation_type, spec = _split_annotation(hint)
    spec = spec or CharterFieldSpec()

    is_json = spec.json
    column_name = spec.column_name or name
    document_name = name if spec.column_name is not None else None

    nullable = _resolve_nullable(spec.nullable, annotation_type)

    # Normalize an optional annotation (`T | None`) to the inner type `T` plus an
    # explicit `nullable=True`. The hand-written propstore charters author
    # optional fields exactly this way (`python_type=T, nullable=True`), and the
    # explicit-nullable rule in `_document_python_type` reconstructs the `T | None`
    # *document* type identically while the storage column python_type stays `T`.
    # This is the resolution of design risk #2 (explicit-vs-default nullability).
    column_python_type = optional_inner_type(annotation_type)

    field_default = None if default is _UNSET else default

    return CharterField(
        column_name,
        column_python_type,
        nullable=nullable,
        primary_key=spec.primary_key,
        foreign_key=spec.foreign_key,
        foreign_keys=spec.foreign_keys,
        index=spec.index,
        unique=spec.unique,
        generated=spec.generated,
        default=field_default,
        default_sql=spec.default_sql,
        json_value_object=spec.json_value_object,
        enum_type=_resolve_enum_type(spec.enum_type, column_python_type, is_json),
        search=spec.search,
        vector_dimensions=spec.vector_dimensions,
        source_local_only=spec.source_local_only,
        canonical_only=spec.canonical_only,
        document=True,
        document_name=document_name,
        document_order=spec.order,
        states=spec.states,
        artifact=spec.artifact,
        artifact_name=spec.artifact_name,
        artifact_dependency=spec.artifact_dependency,
        graph_node_label=spec.graph_node_label,
        graph_metadata=spec.graph_metadata,
        graph_edge=spec.graph_edge,
        graph_edge_kind=spec.graph_edge_kind,
        graph_edge_source_field=spec.graph_edge_source_field,
        graph_edge_source_family=spec.graph_edge_source_family,
        local_id=spec.local_id,
        local_id_policy=spec.local_id_policy,
        contract_version=spec.contract_version,
        parse_boundary="json" if is_json else None,
        metadata=spec.metadata,
    )


def _charter_field_from_column(spec: ColumnSpec) -> CharterField:
    """Lower a storage-only :class:`ColumnSpec` into a ``document=False`` :class:`CharterField`."""

    nullable = _resolve_nullable(spec.nullable, spec.python_type)

    return CharterField(
        spec.name,
        spec.python_type,
        nullable=nullable,
        primary_key=spec.primary_key,
        foreign_key=spec.foreign_key,
        foreign_keys=spec.foreign_keys,
        index=spec.index,
        unique=spec.unique,
        generated=spec.generated,
        default=spec.default,
        default_sql=spec.default_sql,
        json_value_object=spec.json_value_object,
        enum_type=_resolve_enum_type(spec.enum_type, spec.python_type, spec.json),
        search=spec.search,
        vector_dimensions=spec.vector_dimensions,
        source_local_only=spec.source_local_only,
        canonical_only=spec.canonical_only,
        document=False,
        artifact=spec.artifact,
        artifact_name=spec.artifact_name,
        artifact_dependency=spec.artifact_dependency,
        graph_node_label=spec.graph_node_label,
        graph_metadata=spec.graph_metadata,
        graph_edge=spec.graph_edge,
        graph_edge_kind=spec.graph_edge_kind,
        graph_edge_source_field=spec.graph_edge_source_field,
        graph_edge_source_family=spec.graph_edge_source_family,
        local_id=spec.local_id,
        local_id_policy=spec.local_id_policy,
        contract_version=spec.contract_version,
        parse_boundary="json" if spec.json else None,
        metadata=spec.metadata,
    )


# ---------------------------------------------------------------------------
# The @charter decorator
# ---------------------------------------------------------------------------


_T = TypeVar("_T", bound=type)


def charter(
    *,
    key: object,
    name: str,
    contract_version: str | VersionId,
    placement: str | ArtifactPlacementPolicy[Any, Any],
    identity_field: str | None = None,
    semantic: str | None = None,
    artifact_family_name: str | None = None,
    accessor: str | None = None,
    family_foreign_keys: tuple[ForeignKeySpec, ...] = (),
    reference_keys: tuple[Any, ...] = (),
    family_metadata: Mapping[str, object] | None = None,
    semantic_metadata: Mapping[str, object] | None = None,
    extra_columns: tuple[ColumnSpec, ...] = (),
    indexes: tuple[CharterIndex, ...] = (),
    fts: tuple[CharterFtsIndex, ...] = (),
    vector_caches: tuple[CharterVectorCache, ...] = (),
    relationships: tuple[CharterRelationship, ...] = (),
    polymorphic_on: str | None = None,
    polymorphic_identity: str | None = None,
    polymorphic_models: tuple[CharterPolymorphicModel, ...] = (),
    states: tuple[FamilyState, ...] = (),
    transitions: tuple[FamilyTransition, ...] = (),
    validators: tuple[Callable[[msgspec.Struct], None], ...] = (),
    batch: Callable[[type[msgspec.Struct]], DocumentBatchSpec[Any]]
    | DocumentBatchSpec[Any]
    | tuple[
        Callable[[type[msgspec.Struct]], DocumentBatchSpec[Any]] | DocumentBatchSpec[Any],
        ...,
    ] = (),
    document_contract_version: VersionId | None = None,
    model_mixin: type | None = None,
    model_name: str | None = None,
) -> Callable[[_T], _T]:
    """Class decorator that derives a :class:`FamilyCharter` from a document class.

    The decorated class must subclass :class:`CharterDoc` (or another
    ``msgspec.Struct``). Its annotated attributes become the document fields; the
    decorator reads them via ``get_type_hints(cls, include_extras=True)``, builds
    the matching :class:`CharterField` tuple (reusing the existing builder), and
    constructs a :class:`FamilyCharter` reusing all of ``quire.charters``.

    The resulting charter is attached to the class as ``__charter__``. A
    SQLAlchemy-mappable model is generated (design Option (i)): an empty
    :class:`FamilyModel` subclass, optionally inheriting ``model_mixin`` for the
    families that carry row behaviour.
    """

    version = (
        contract_version
        if isinstance(contract_version, VersionId)
        else VersionId(contract_version, allow_placeholder=False)
    )
    resolved_placement: ArtifactPlacementPolicy[Any, Any] = (
        FlatYamlPlacement(placement, str)
        if isinstance(placement, str)
        else placement
    )
    resolved_artifact_name = artifact_family_name or name

    def decorate(cls: _T) -> _T:
        # The class IS the document. Recover Annotated[...] metadata via type
        # hints; recover field ordering + defaults via msgspec's own field
        # introspection (a required msgspec field exposes a slot descriptor via
        # getattr, NOT the absence of a default, so msgspec.structs.fields is the
        # authoritative source for defaults).
        hints = get_type_hints(cls, include_extras=True)
        document_fields: list[CharterField] = []
        for info in msgspec.structs.fields(cast("type[msgspec.Struct]", cls)):
            hint = hints.get(info.name)
            if hint is None:
                continue
            if info.default is not msgspec.NODEFAULT:
                default: object = info.default
            elif info.default_factory is not msgspec.NODEFAULT:
                default = info.default_factory()
            else:
                default = _UNSET
            document_fields.append(
                _charter_field_from_attribute(info.name, hint, default)
            )

        column_fields = [_charter_field_from_column(spec) for spec in extra_columns]
        all_fields = tuple(document_fields) + tuple(column_fields)

        # Generate the SQLAlchemy-mappable model (design Option (i)).
        bases: tuple[type, ...] = (
            (model_mixin, FamilyModel) if model_mixin is not None else (FamilyModel,)
        )
        generated_model_name = model_name or f"{cls.__name__}Model"
        model = type(generated_model_name, bases, {})
        model.__module__ = cls.__module__
        model.__qualname__ = generated_model_name

        family = FamilyDefinition(
            key=key,
            name=name,
            contract_version=version,
            artifact_family=ArtifactFamily(
                name=resolved_artifact_name,
                contract_version=version,
                doc_type=model,
                placement=resolved_placement,
            ),
            accessor=accessor,
            foreign_keys=family_foreign_keys,
            identity_field=identity_field,
            reference_keys=cast("tuple[Any, ...]", reference_keys),
            metadata=family_metadata,
        )

        resolved_semantic_metadata: Mapping[str, object]
        if semantic_metadata is not None:
            resolved_semantic_metadata = semantic_metadata
        elif semantic is not None:
            resolved_semantic_metadata = {"semantic": semantic}
        else:
            resolved_semantic_metadata = {}

        family_charter = FamilyCharter(
            family=family,
            model=model,
            fields=all_fields,
            states=states,
            transitions=transitions,
            indexes=indexes,
            fts_indexes=fts,
            vector_caches=vector_caches,
            relationships=relationships,
            polymorphic_on=polymorphic_on,
            polymorphic_identity=polymorphic_identity,
            polymorphic_models=polymorphic_models,
            document_contract_version=document_contract_version,
            semantic_metadata=resolved_semantic_metadata,
            validators=validators,
        )

        # Batch specs reference the family's own generated document. Accept a
        # factory (called with the generated document type) or a ready spec, and
        # attach them the same way hand-written declarations do.
        batch_items = batch if isinstance(batch, tuple) else (batch,) if batch else ()
        if batch_items:
            generated_document = family_charter.generated_document()
            resolved_specs: list[DocumentBatchSpec[Any]] = []
            for item in batch_items:
                if isinstance(item, DocumentBatchSpec):
                    resolved_specs.append(item)
                else:
                    resolved_specs.append(
                        cast("DocumentBatchSpec[Any]", item(generated_document))
                    )
            object.__setattr__(family_charter, "batch_specs", tuple(resolved_specs))

        cls.__charter__ = family_charter  # type: ignore[attr-defined]
        cls.__charter_model__ = model  # type: ignore[attr-defined]
        return cls

    return decorate

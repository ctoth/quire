from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Protocol, TypeAlias, TypeVar

from quire.versions import VersionId

TRecord = TypeVar("TRecord")
TExtractRecord = TypeVar("TExtractRecord", contravariant=True)


class AmbiguousReferenceError(ValueError):
    def __init__(self, reference: str, candidates: tuple[str, ...]) -> None:
        super().__init__(
            f"Ambiguous reference {reference!r}: "
            + ", ".join(candidates)
        )
        self.reference = reference
        self.candidates = candidates


class MissingReferenceError(ValueError):
    def __init__(self, reference: str) -> None:
        super().__init__(f"Missing reference {reference!r}")
        self.reference = reference


class ForeignKeyValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ReferenceResolution:
    raw_text: str
    target_family: str
    resolved_id: str | None
    matched_by: str | None = None
    matched_text: str | None = None
    ambiguous_candidates: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.resolved_id is not None

    @property
    def ambiguous(self) -> bool:
        return self.resolved_id is None and bool(self.ambiguous_candidates)

    @property
    def target_kind(self) -> str:
        return self.target_family


@dataclass(frozen=True)
class ForeignKeySpec:
    name: str
    contract_version: VersionId
    source_family: str
    source_field: str
    target_family: str
    target_field: str = "id"
    required: bool = True
    many: bool = False

    def contract_body(self) -> dict[str, object]:
        return {
            "source_family": self.source_family,
            "source_field": self.source_field,
            "target_family": self.target_family,
            "target_field": self.target_field,
            "required": self.required,
            "many": self.many,
        }


def validate_foreign_key(
    spec: ForeignKeySpec,
    record: object,
    target_index: ReferenceIndex[object],
) -> tuple[str, ...]:
    values = _field_values(record, spec.source_field)
    if not values:
        if spec.required:
            raise ForeignKeyValidationError(
                f"required foreign key {spec.name!r} is missing {spec.source_field!r}"
            )
        return ()
    if not spec.many and len(values) > 1:
        raise ForeignKeyValidationError(
            f"foreign key {spec.name!r} expected one value, got {len(values)}"
        )

    resolved: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise ForeignKeyValidationError(
                f"foreign key {spec.name!r} value is not a non-empty string: {value!r}"
            )
        try:
            target_id = target_index.resolve_id(value)
        except AmbiguousReferenceError as exc:
            raise ForeignKeyValidationError(
                f"foreign key {spec.name!r} is ambiguous: {value!r}"
            ) from exc
        if target_id is None:
            raise ForeignKeyValidationError(
                f"foreign key {spec.name!r} value {value!r} does not resolve"
            )
        resolved.append(target_id)
    return tuple(resolved)


def _field_values(record: object, source_field: str) -> tuple[object, ...]:
    values: tuple[object, ...] = (record,)
    for raw_part in source_field.split("."):
        many = raw_part.endswith("[]")
        part = raw_part[:-2] if many else raw_part
        next_values: list[object] = []
        for value in values:
            child = _field_child(value, part)
            if child is None:
                continue
            if many:
                next_values.extend(_iter_many_field(child))
            else:
                next_values.append(child)
        values = tuple(next_values)
    return tuple(value for value in values if value is not None and value != "")


def _field_child(value: object, field_name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _iter_many_field(value: object) -> Sequence[object]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def extend_reference_lookup(
    lookup: dict[str, list[str]],
    key: str | None,
    target_id: str,
) -> None:
    if not isinstance(key, str) or not key:
        return
    values = lookup.setdefault(key, [])
    if target_id not in values:
        values.append(target_id)


def finalize_reference_lookup(
    lookup: dict[str, list[str]],
) -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType({key: tuple(values) for key, values in lookup.items()})


def build_reference_lookup(
    records: Iterable[TRecord],
    *,
    target_id: Callable[[TRecord], str | None],
    keys: Callable[[TRecord], Iterable[str | None]],
) -> Mapping[str, tuple[str, ...]]:
    lookup: dict[str, list[str]] = {}
    for record in records:
        resolved_id = target_id(record)
        if not resolved_id:
            continue
        extend_reference_lookup(lookup, resolved_id, resolved_id)
        for key in keys(record):
            extend_reference_lookup(lookup, key, resolved_id)
    return finalize_reference_lookup(lookup)


class ReferenceKeyExtractor(Protocol[TExtractRecord]):
    def __call__(self, record: TExtractRecord) -> Iterable[str | None] | str | None:
        ...


ReferenceKeySpec: TypeAlias = "ReferenceKey | ReferenceKeyExtractor[TRecord]"


@dataclass(frozen=True)
class ReferenceKey:
    field_path: str | None = None
    template: str | None = None
    from_field: str | None = None

    @classmethod
    def field(cls, field_path: str) -> ReferenceKey:
        _validate_field_path(field_path)
        return cls(field_path=field_path)

    @classmethod
    def format(cls, template: str, *, from_field: str) -> ReferenceKey:
        if not template:
            raise ValueError("reference key format template cannot be empty")
        _validate_field_path(from_field)
        return cls(template=template, from_field=from_field)

    def __call__(self, record: object) -> tuple[str, ...]:
        if self.field_path is not None:
            return _string_values(_field_values(record, self.field_path))
        if self.template is not None and self.from_field is not None:
            return tuple(
                value
                for item in _field_values(record, self.from_field)
                if (value := self._format_item(item)) is not None
            )
        raise ValueError("invalid reference key declaration")

    def contract_body(self) -> dict[str, str]:
        if self.field_path is not None:
            return {"kind": "field", "field": self.field_path}
        if self.template is not None and self.from_field is not None:
            return {
                "kind": "format",
                "template": self.template,
                "from_field": self.from_field,
            }
        raise ValueError("invalid reference key declaration")

    def _format_item(self, item: object) -> str | None:
        assert self.template is not None
        values = _format_mapping(item)
        try:
            formatted = self.template.format_map(values)
        except KeyError as exc:
            raise ValueError(f"reference key format field is missing: {exc.args[0]!r}") from exc
        if not formatted:
            return None
        return formatted


@dataclass(frozen=True)
class FamilyReferenceIndex(Generic[TRecord]):
    family: str
    records_by_id: Mapping[str, TRecord]
    lookup: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_records(
        cls,
        records: Iterable[TRecord],
        *,
        artifact_id: Callable[[TRecord], str | None],
        keys: Iterable[ReferenceKeySpec[TRecord]] = (),
        family: str = "",
    ) -> FamilyReferenceIndex[TRecord]:
        records_by_id: dict[str, TRecord] = {}
        lookup: dict[str, list[str]] = {}
        for record in records:
            resolved_id = artifact_id(record)
            if not isinstance(resolved_id, str) or not resolved_id:
                continue
            records_by_id.setdefault(resolved_id, record)
            extend_reference_lookup(lookup, resolved_id, resolved_id)
            for key in keys:
                for value in _key_values(key, record):
                    extend_reference_lookup(lookup, value, resolved_id)
        finalized = finalize_reference_lookup(lookup)
        for reference, candidates in finalized.items():
            if len(candidates) > 1:
                raise AmbiguousReferenceError(reference, candidates)
        return cls(
            family=family,
            records_by_id=MappingProxyType(records_by_id),
            lookup=finalized,
        )

    def resolve_id(self, reference: object) -> str | None:
        return ReferenceIndex(
            family=self.family,
            records_by_id=self.records_by_id,
            lookup=self.lookup,
        ).resolve_id(reference)

    def require_id(self, reference: str) -> str:
        resolved = self.resolve_id(reference)
        if resolved is None:
            raise MissingReferenceError(reference)
        return resolved

    def exists(self, reference: object) -> bool:
        return self.resolve_id(reference) is not None

    def ids(self) -> tuple[str, ...]:
        return tuple(self.records_by_id)

    def resolve(
        self,
        reference: object,
        *,
        match_kind: Callable[[str, str, TRecord | None], tuple[str | None, str | None]] | None = None,
    ) -> ReferenceResolution | None:
        return ReferenceIndex(
            family=self.family,
            records_by_id=self.records_by_id,
            lookup=self.lookup,
        ).resolve(reference, match_kind=match_kind)


@dataclass(frozen=True)
class ReferenceIndex(Generic[TRecord]):
    family: str
    records_by_id: Mapping[str, TRecord]
    lookup: Mapping[str, tuple[str, ...]]

    def resolve_id(self, reference: object) -> str | None:
        if not isinstance(reference, str) or not reference:
            return None
        candidates = self.lookup.get(reference, ())
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise AmbiguousReferenceError(reference, tuple(candidates))
        if reference in self.records_by_id:
            return reference
        return None

    def exists(self, reference: object) -> bool:
        try:
            return self.resolve_id(reference) is not None
        except AmbiguousReferenceError:
            return False

    def resolve(
        self,
        reference: object,
        *,
        match_kind: Callable[[str, str, TRecord | None], tuple[str | None, str | None]] | None = None,
    ) -> ReferenceResolution | None:
        if not isinstance(reference, str) or not reference:
            return None
        candidates = self.lookup.get(reference, ())
        if len(candidates) == 1:
            resolved_id = candidates[0]
            record = self.records_by_id.get(resolved_id)
            matched_by, matched_text = (
                (None, None)
                if match_kind is None
                else match_kind(reference, resolved_id, record)
            )
            return ReferenceResolution(
                raw_text=reference,
                target_family=self.family,
                resolved_id=resolved_id,
                matched_by=matched_by,
                matched_text=matched_text,
            )
        return ReferenceResolution(
            raw_text=reference,
            target_family=self.family,
            resolved_id=None,
            ambiguous_candidates=tuple(candidates),
        )


@dataclass(frozen=True)
class CrossFamilyReferenceIndex:
    families: Mapping[str, ReferenceIndex[object]]

    def family(self, name: str) -> ReferenceIndex[object]:
        try:
            return self.families[name]
        except KeyError as exc:
            raise KeyError(f"unknown reference family: {name}") from exc

    def resolve(self, target_family: str, reference: object) -> ReferenceResolution | None:
        return self.family(target_family).resolve(reference)

    def resolve_id(self, target_family: str, reference: object) -> str | None:
        return self.family(target_family).resolve_id(reference)

    def exists(self, target_family: str, reference: object) -> bool:
        return self.family(target_family).exists(reference)


def _validate_field_path(field_path: str) -> None:
    if not field_path:
        raise ValueError("reference key field path cannot be empty")
    for raw_part in field_path.split("."):
        if not raw_part:
            raise ValueError(f"invalid reference key field path: {field_path!r}")
        part = raw_part[:-2] if raw_part.endswith("[]") else raw_part
        if not part or "[]" in part:
            raise ValueError(f"invalid reference key field path: {field_path!r}")


def _string_values(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(value for value in values if isinstance(value, str) and value)


def _format_mapping(item: object) -> Mapping[str, object]:
    if isinstance(item, Mapping):
        return item
    names = tuple(
        name
        for name in dir(item)
        if not name.startswith("_") and not callable(getattr(item, name))
    )
    return MappingProxyType({name: getattr(item, name) for name in names})


def _key_values(key: ReferenceKeySpec[TRecord], record: TRecord) -> tuple[str, ...]:
    raw_values = key(record)
    if raw_values is None:
        return ()
    if isinstance(raw_values, str):
        return (raw_values,) if raw_values else ()
    return _string_values(raw_values)

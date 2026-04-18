from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar

from quire.versions import VersionId

TRecord = TypeVar("TRecord")


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
    required: bool = True
    many: bool = False

    def contract_body(self) -> dict[str, object]:
        return {
            "source_family": self.source_family,
            "source_field": self.source_field,
            "target_family": self.target_family,
            "required": self.required,
            "many": self.many,
        }


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
        if reference in self.records_by_id:
            return reference
        return None

    def exists(self, reference: object) -> bool:
        return self.resolve_id(reference) is not None

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

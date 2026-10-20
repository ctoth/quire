"""Single-valued instance maps over pinned family occurrences.

The source commit qualifies every image; equal handles in different source
instances do not imply equal result images. Family owners supply reidentity
after reference transport. Generic transport never invents an ID or expands a
reference into alternatives.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeVar

import msgspec

from quire.families import FamilyDefinition
from quire.references import (
    FamilyReferenceIndex,
    ForeignKeySpec,
    ForeignKeyValidationError,
    ReferenceKey,
    map_foreign_keys,
    validate_foreign_key,
)

TDoc = TypeVar("TDoc")


class UnsupportedFamilyTransportError(ValueError):
    """Reference changes need an identity operation the family has not supplied."""


class FamilyReferenceImage(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    source_commit: str
    family: str
    source_id: str
    target_id: str


@dataclass
class FamilyInstanceMap:
    source_commit: str
    source_indexes: Mapping[str, FamilyReferenceIndex[object]]
    _images: dict[str, dict[str, str]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.source_commit:
            raise ValueError("an instance map requires a pinned source commit")
        self.source_indexes = MappingProxyType(dict(self.source_indexes))

    def assign(self, family: str, source_id: str, target_id: str) -> None:
        """Assign one image; a second, conflicting image is not a function."""
        if source_id not in self.source_indexes[family].records_by_id:
            raise ForeignKeyValidationError(f"unknown source occurrence: {family} {source_id!r}")
        if not target_id:
            raise ValueError("an occurrence image must be a non-empty identity")
        images = self._images.setdefault(family, {})
        previous = images.get(source_id)
        if previous is not None and previous != target_id:
            raise ValueError(f"conflicting images for {self.source_commit} {family} {source_id!r}")
        images[source_id] = target_id

    def _reference_image(self, spec: ForeignKeySpec, reference: str) -> str:
        source_id = self.source_indexes[spec.target_family].require_id(reference)
        # The map is the identity on known, unassigned source occurrences.
        return self._images.get(spec.target_family, {}).get(source_id, source_id)

    def transport(
        self,
        family: FamilyDefinition[Any, Any, Any, TDoc],
        document: TDoc,
    ) -> TDoc:
        """Transport one real occurrence and register its family-owned identity."""
        for spec in family.foreign_keys:
            validate_foreign_key(spec, document, self.source_indexes[spec.target_family])
        transported = map_foreign_keys(document, family.foreign_keys, self._reference_image)
        if transported is document:
            return document
        if family.transport_identity is None:
            raise UnsupportedFamilyTransportError(
                f"family {family.name!r} has no identity operation for reference transport"
            )
        result = family.transport_identity(transported)
        for spec in family.foreign_keys:
            reference = ReferenceKey.field(spec.source_field)
            if reference(result) != reference(transported):
                raise ValueError("family identity operation changed transported foreign keys")
        if family.identity_field is None:
            raise ValueError(f"family {family.name!r} has no identity field")
        identity = ReferenceKey.field(family.identity_field)
        old_ids = identity(document)
        new_ids = identity(result)
        if len(old_ids) != 1 or len(new_ids) != 1:
            raise ValueError("family transport requires a single source and result identity")
        self.assign(family.name, old_ids[0], new_ids[0])
        return result

    def iter_images(self) -> Iterator[FamilyReferenceImage]:
        """Yield assigned occurrence images, including unchanged explicit images."""
        for family, images in sorted(self._images.items()):
            for source_id, target_id in sorted(images.items()):
                yield FamilyReferenceImage(self.source_commit, family, source_id, target_id)

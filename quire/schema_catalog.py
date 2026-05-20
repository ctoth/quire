from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from quire.hashing import canonical_json_sha256
from quire.schema_ir import SchemaObject


@dataclass(frozen=True)
class SchemaCatalog:
    objects: tuple[SchemaObject, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        return {
            "metadata": dict(sorted(self.metadata.items())),
            "objects": tuple(
                schema_object.payload()
                for schema_object in sorted(self.objects, key=lambda item: item.name)
            ),
        }

    def schema_hash(self) -> str:
        return canonical_json_sha256(self.payload())

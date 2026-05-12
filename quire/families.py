from __future__ import annotations

from collections.abc import Hashable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from quire.artifacts import ArtifactAddress, ArtifactFamily, ArtifactHandle, PreparedArtifact
from quire.contracts import CompatibilityMarker, ContractEntry, ContractManifest
from quire.family_store import DocumentFamilyStore, DocumentFamilyTransaction
from quire.references import FamilyReferenceIndex, ForeignKeySpec, ReferenceKey
from quire.versions import VersionId

TOwner = TypeVar("TOwner")
TKey = TypeVar("TKey", bound=Hashable)
TRef = TypeVar("TRef")
TDoc = TypeVar("TDoc")


def _require_version(value: object, *, label: str) -> VersionId:
    if not isinstance(value, VersionId):
        raise ValueError(f"{label} requires an explicit VersionId")
    VersionId(str(value), allow_placeholder=False)
    return value


def _key_contract_value(key: object) -> str:
    value = getattr(key, "value", key)
    if isinstance(value, str):
        return value
    name = getattr(key, "name", None)
    if isinstance(name, str):
        return name
    return str(key)


@dataclass(frozen=True)
class FamilyIdentityPolicy:
    artifact_id_function: str | None = None
    version_id_function: str | None = None
    canonical_payload_function: str | None = None
    normalize_payload_function: str | None = None
    logical_id_fields: tuple[str, ...] = ()
    version_excluded_fields: tuple[str, ...] = ()
    source_local_fields: tuple[str, ...] = ()

    def contract_body(self) -> dict[str, object]:
        return {
            "artifact_id_function": self.artifact_id_function,
            "version_id_function": self.version_id_function,
            "canonical_payload_function": self.canonical_payload_function,
            "normalize_payload_function": self.normalize_payload_function,
            "logical_id_fields": self.logical_id_fields,
            "version_excluded_fields": self.version_excluded_fields,
            "source_local_fields": self.source_local_fields,
        }


@dataclass(frozen=True)
class FamilyDefinition(Generic[TOwner, TKey, TRef, TDoc]):
    key: TKey
    name: str
    contract_version: VersionId
    artifact_family: ArtifactFamily[TOwner, TRef, TDoc]
    accessor: str | None = None
    foreign_keys: tuple[ForeignKeySpec, ...] = ()
    identity_policy: FamilyIdentityPolicy | None = None
    identity_field: str | None = None
    reference_keys: tuple[ReferenceKey, ...] = ()
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("family name cannot be empty")
        _require_version(self.contract_version, label=f"family {self.name!r}")
        _require_version(
            self.artifact_family.contract_version,
            label=f"artifact family {self.artifact_family.name!r}",
        )
        if self.accessor is not None and not self.accessor.isidentifier():
            raise ValueError(f"family accessor must be a Python identifier: {self.accessor!r}")
        if self.identity_field is not None and not self.identity_field:
            raise ValueError("family identity field cannot be empty")
        if self.reference_keys and self.identity_field is None:
            raise ValueError("family reference keys require an identity field")

    @property
    def accessor_name(self) -> str:
        return self.accessor or self.name

    def reference_index_from_records(
        self,
        records: Sequence[TDoc],
    ) -> FamilyReferenceIndex[TDoc]:
        if self.identity_field is None:
            raise ValueError(f"family {self.name!r} has no identity field")
        identity_key = ReferenceKey.field(self.identity_field)
        return FamilyReferenceIndex.from_records(
            records,
            family=self.name,
            artifact_id=lambda record: next(iter(identity_key(record)), None),
            keys=self.reference_keys,
        )

    def contract_body(self) -> dict[str, object]:
        body: dict[str, object] = {
            "accessor": self.accessor_name,
            "artifact_family": self.artifact_family.name,
            "artifact_family_contract_version": str(self.artifact_family.contract_version),
            "artifact_family_contract": self.artifact_family.contract_body(),
            "foreign_keys": tuple(spec.contract_body() for spec in self.foreign_keys),
            "key": _key_contract_value(self.key),
        }
        if self.metadata:
            body["metadata"] = dict(self.metadata)
        if self.identity_policy is not None:
            body["identity_policy"] = self.identity_policy.contract_body()
        if self.identity_field is not None:
            body["identity_field"] = self.identity_field
        if self.reference_keys:
            body["reference_keys"] = tuple(key.contract_body() for key in self.reference_keys)
        return body


@dataclass(frozen=True)
class FamilyRegistry(Generic[TOwner, TKey]):
    name: str
    contract_version: VersionId
    families: tuple[FamilyDefinition[TOwner, TKey, Any, Any], ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("family registry name cannot be empty")
        _require_version(self.contract_version, label=f"family registry {self.name!r}")
        keys = [family.key for family in self.families]
        names = [family.name for family in self.families]
        accessors = [family.accessor_name for family in self.families]
        duplicate_keys = _duplicates(keys)
        duplicate_names = _duplicates(names)
        duplicate_accessors = _duplicates(accessors)
        if duplicate_keys:
            raise ValueError(f"duplicate family keys: {', '.join(map(str, duplicate_keys))}")
        if duplicate_names:
            raise ValueError(f"duplicate family names: {', '.join(map(str, duplicate_names))}")
        if duplicate_accessors:
            raise ValueError(f"duplicate family accessors: {', '.join(map(str, duplicate_accessors))}")

    def by_key(self, key: TKey) -> FamilyDefinition[TOwner, TKey, Any, Any]:
        for family in self.families:
            if family.key == key:
                return family
        raise KeyError(f"unknown family key: {key!r}")

    def by_name(self, name: str) -> FamilyDefinition[TOwner, TKey, Any, Any]:
        for family in self.families:
            if family.name == name:
                return family
        raise KeyError(f"unknown family name: {name}")

    def by_artifact_family(
        self,
        artifact_family: ArtifactFamily[TOwner, Any, Any],
    ) -> FamilyDefinition[TOwner, TKey, Any, Any]:
        for family in self.families:
            if family.artifact_family == artifact_family:
                return family
        raise KeyError(f"unknown artifact family: {artifact_family.name}")

    def by_accessor(self, accessor: str) -> FamilyDefinition[TOwner, TKey, Any, Any]:
        for family in self.families:
            if family.accessor_name == accessor:
                return family
        raise AttributeError(accessor)

    def names(self) -> tuple[str, ...]:
        return tuple(family.name for family in self.families)

    def keys(self) -> tuple[TKey, ...]:
        return tuple(family.key for family in self.families)

    def bind(self, owner: TOwner, store: DocumentFamilyStore[TOwner]) -> BoundFamilyRegistry[TOwner, TKey]:
        return BoundFamilyRegistry(owner=owner, store=store, registry=self)

    def contract_body(self) -> dict[str, object]:
        return {
            "families": tuple(
                {
                    "contract_version": str(family.contract_version),
                    "key": _key_contract_value(family.key),
                    "name": family.name,
                }
                for family in self.families
            )
        }

    def contract_entries(self) -> tuple[ContractEntry, ...]:
        entries = [
            ContractEntry(
                kind="family-registry",
                name=self.name,
                contract_version=self.contract_version,
                body=self.contract_body(),
            )
        ]
        entries.extend(
            ContractEntry(
                kind="family",
                name=family.name,
                contract_version=family.contract_version,
                body=family.contract_body(),
            )
            for family in self.families
        )
        return tuple(entries)

    def contract_manifest(
        self,
        *,
        package_name: str,
        package_version: str,
        format_version: int = 1,
        compatible_changes: Sequence[CompatibilityMarker] = (),
    ) -> ContractManifest:
        return ContractManifest(
            format_version=format_version,
            package_name=package_name,
            package_version=package_version,
            registry_name=self.name,
            registry_contract_version=self.contract_version,
            contracts=self.contract_entries(),
            compatible_changes=tuple(compatible_changes),
        )


@dataclass(frozen=True)
class BoundFamilyRegistry(Generic[TOwner, TKey]):
    owner: TOwner
    store: DocumentFamilyStore[TOwner]
    registry: FamilyRegistry[TOwner, TKey]

    def by_key(self, key: TKey) -> BoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_key(key)
        return BoundFamily(self.store, definition.artifact_family, definition)

    def by_name(self, name: str) -> BoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_name(name)
        return BoundFamily(self.store, definition.artifact_family, definition)

    def by_artifact_family(
        self,
        artifact_family: ArtifactFamily[TOwner, Any, Any],
    ) -> BoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_artifact_family(artifact_family)
        return BoundFamily(self.store, definition.artifact_family, definition)

    def transact(
        self,
        *,
        message: str,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> BoundFamilyTransaction[TOwner, TKey]:
        return BoundFamilyTransaction(
            transaction=self.store.transact(
                message=message,
                branch=branch,
                expected_head=expected_head,
            ),
            registry=self.registry,
        )

    def __getattr__(self, name: str) -> BoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_accessor(name)
        return BoundFamily(self.store, definition.artifact_family, definition)


@dataclass(frozen=True)
class BoundFamily(Generic[TOwner, TRef, TDoc]):
    store: DocumentFamilyStore[TOwner]
    family: ArtifactFamily[TOwner, TRef, TDoc]
    definition: FamilyDefinition[TOwner, Any, TRef, TDoc] | None = None

    def address(
        self,
        ref: TRef,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> ArtifactAddress:
        return self.store.address(self.family, ref, branch=branch, commit=commit)

    def ref_from_path(self, path: str) -> TRef:
        return self.store.ref_from_path(self.family, path)

    def ref_from_loaded(self, loaded: object) -> TRef:
        return self.store.ref_from_loaded(self.family, loaded)

    def coerce(self, payload: object, *, source: str) -> TDoc:
        return self.store.coerce(self.family, payload, source=source)

    def render(self, document: TDoc) -> str:
        return self.store.render(document, family=self.family)

    def payload(self, document: TDoc) -> object:
        return self.store.payload(document, family=self.family)

    def iter(self, *, branch: str | None = None, commit: str | None = None) -> Iterator[TRef]:
        return self.store.iter(self.family, branch=branch, commit=commit)

    def pin(
        self,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> PinnedBoundFamily[TOwner, TRef, TDoc]:
        target_branch, target_commit = self.store.pin(self.family, branch=branch, commit=commit)
        return PinnedBoundFamily(
            store=self.store,
            family=self.family,
            branch=target_branch,
            commit=target_commit,
        )

    def iter_handles(
        self,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> Iterator[ArtifactHandle[TOwner, TRef, TDoc]]:
        return self.store.iter_handles(self.family, branch=branch, commit=commit)

    def reference_index(
        self,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> FamilyReferenceIndex[TDoc]:
        if self.definition is None:
            raise ValueError(f"family {self.family.name!r} has no family definition")
        return self.definition.reference_index_from_records(
            tuple(handle.document for handle in self.iter_handles(branch=branch, commit=commit))
        )

    def load(self, ref: TRef, *, commit: str | None = None) -> TDoc | None:
        return self.store.load(self.family, ref, commit=commit)

    def exists(
        self,
        ref: TRef,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> bool:
        return self.store.exists(self.family, ref, branch=branch, commit=commit)

    def require(self, ref: TRef, *, commit: str | None = None) -> TDoc:
        return self.store.require(self.family, ref, commit=commit)

    def handle(self, ref: TRef, *, commit: str | None = None) -> ArtifactHandle[TOwner, TRef, TDoc] | None:
        return self.store.handle(self.family, ref, commit=commit)

    def require_handle(self, ref: TRef, *, commit: str | None = None) -> ArtifactHandle[TOwner, TRef, TDoc]:
        return self.store.require_handle(self.family, ref, commit=commit)

    def prepare(
        self,
        ref: TRef,
        doc: TDoc,
        *,
        branch: str | None = None,
    ) -> PreparedArtifact[TOwner, TRef, TDoc]:
        return self.store.prepare(self.family, ref, doc, branch=branch)

    def save(
        self,
        ref: TRef,
        doc: TDoc,
        *,
        message: str,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        return self.store.save(
            self.family,
            ref,
            doc,
            message=message,
            branch=branch,
            expected_head=expected_head,
        )

    def delete(
        self,
        ref: TRef,
        *,
        message: str,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        return self.store.delete(
            self.family,
            ref,
            message=message,
            branch=branch,
            expected_head=expected_head,
        )

    def move(
        self,
        old_ref: TRef,
        new_ref: TRef,
        doc: TDoc,
        *,
        message: str,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        return self.store.move(
            self.family,
            old_ref,
            new_ref,
            doc,
            message=message,
            branch=branch,
            expected_head=expected_head,
        )


@dataclass(frozen=True)
class PinnedBoundFamily(Generic[TOwner, TRef, TDoc]):
    store: DocumentFamilyStore[TOwner]
    family: ArtifactFamily[TOwner, TRef, TDoc]
    branch: str
    commit: str | None

    def address(self, ref: TRef) -> ArtifactAddress:
        return self.store.address(self.family, ref, branch=self.branch, commit=self.commit)

    def iter(self) -> Iterator[TRef]:
        return self.store.iter(self.family, branch=self.branch, commit=self.commit)

    def iter_handles(self) -> Iterator[ArtifactHandle[TOwner, TRef, TDoc]]:
        return self.store.iter_handles(self.family, branch=self.branch, commit=self.commit)

    def load(self, ref: TRef) -> TDoc | None:
        return self.store.load(self.family, ref, branch=self.branch, commit=self.commit)

    def exists(self, ref: TRef) -> bool:
        return self.store.exists(self.family, ref, branch=self.branch, commit=self.commit)

    def require(self, ref: TRef) -> TDoc:
        return self.store.require(self.family, ref, branch=self.branch, commit=self.commit)

    def handle(self, ref: TRef) -> ArtifactHandle[TOwner, TRef, TDoc] | None:
        return self.store.handle(self.family, ref, branch=self.branch, commit=self.commit)

    def require_handle(self, ref: TRef) -> ArtifactHandle[TOwner, TRef, TDoc]:
        return self.store.require_handle(self.family, ref, branch=self.branch, commit=self.commit)


@dataclass(frozen=True)
class BoundFamilyTransaction(Generic[TOwner, TKey]):
    transaction: DocumentFamilyTransaction[TOwner]
    registry: FamilyRegistry[TOwner, TKey]

    @property
    def commit_sha(self) -> str | None:
        return self.transaction.commit_sha

    @property
    def owner(self) -> TOwner:
        return self.transaction.owner

    def by_key(self, key: TKey) -> TransactionalBoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_key(key)
        return TransactionalBoundFamily(self.transaction, definition.artifact_family)

    def by_name(self, name: str) -> TransactionalBoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_name(name)
        return TransactionalBoundFamily(self.transaction, definition.artifact_family)

    def by_artifact_family(
        self,
        artifact_family: ArtifactFamily[TOwner, Any, Any],
    ) -> TransactionalBoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_artifact_family(artifact_family)
        return TransactionalBoundFamily(self.transaction, definition.artifact_family)

    def commit(self) -> str:
        return self.transaction.commit()

    def __enter__(self) -> BoundFamilyTransaction[TOwner, TKey]:
        self.transaction.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return self.transaction.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> TransactionalBoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_accessor(name)
        return TransactionalBoundFamily(self.transaction, definition.artifact_family)


@dataclass(frozen=True)
class TransactionalBoundFamily(Generic[TOwner, TRef, TDoc]):
    transaction: DocumentFamilyTransaction[TOwner]
    family: ArtifactFamily[TOwner, TRef, TDoc]

    def coerce(self, payload: object, *, source: str) -> TDoc:
        return self.transaction.coerce(self.family, payload, source=source)

    def ref_from_path(self, path: str) -> TRef:
        return self.transaction.store.ref_from_path(self.family, path)

    def payload(self, document: TDoc) -> object:
        return self.transaction.store.payload(document, family=self.family)

    def save(self, ref: TRef, doc: TDoc) -> None:
        self.transaction.save(self.family, ref, doc)

    def delete(self, ref: TRef) -> None:
        self.transaction.delete(self.family, ref)

    def move(self, old_ref: TRef, new_ref: TRef, doc: TDoc) -> None:
        self.transaction.move(self.family, old_ref, new_ref, doc)


def _duplicates(values: Sequence[object]) -> list[object]:
    seen: set[object] = set()
    duplicate_values: set[object] = set()
    duplicates: list[object] = []
    for value in values:
        if value in seen and value not in duplicate_values:
            duplicates.append(value)
            duplicate_values.add(value)
        seen.add(value)
    return duplicates

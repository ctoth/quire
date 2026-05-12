from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from quire.artifacts import ArtifactAddress, ArtifactFamily, ArtifactHandle, PreparedArtifact
from quire.contracts import CompatibilityMarker, ContractEntry, ContractManifest
from quire.family_store import DocumentFamilyStore, DocumentFamilyTransaction, address_path
from quire.references import FamilyReferenceIndex, ForeignKeySpec, ReferenceKey, validate_foreign_key
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

    def metadata_value(self, key: str, default: object = None) -> object:
        if self.metadata is None:
            return default
        return self.metadata.get(key, default)

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
        name_set = set(names)
        for family in self.families:
            for spec in family.foreign_keys:
                if spec.source_family != family.name:
                    raise ValueError(
                        f"foreign key {spec.name!r} source family {spec.source_family!r} "
                        f"does not match family {family.name!r}"
                    )
                if spec.target_family not in name_set:
                    raise ValueError(
                        f"foreign key {spec.name!r} target family is unknown: {spec.target_family!r}"
                    )

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

    def select(
        self,
        predicate: Callable[[FamilyDefinition[TOwner, TKey, Any, Any]], bool],
    ) -> tuple[FamilyDefinition[TOwner, TKey, Any, Any], ...]:
        return tuple(family for family in self.families if predicate(family))

    def select_by_metadata(
        self,
        key: str,
        value: object,
    ) -> tuple[FamilyDefinition[TOwner, TKey, Any, Any], ...]:
        return self.select(lambda family: family.metadata_value(key) == value)

    def by_metadata(
        self,
        key: str,
        value: object,
    ) -> FamilyDefinition[TOwner, TKey, Any, Any]:
        matches = self.select_by_metadata(key, value)
        if not matches:
            raise KeyError(f"no family metadata {key!r}={value!r}")
        if len(matches) > 1:
            names = ", ".join(family.name for family in matches)
            raise ValueError(f"multiple families match metadata {key!r}={value!r}: {names}")
        return matches[0]

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
        return BoundFamily(self.store, definition.artifact_family, definition, self.registry)

    def by_name(self, name: str) -> BoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_name(name)
        return BoundFamily(self.store, definition.artifact_family, definition, self.registry)

    def by_artifact_family(
        self,
        artifact_family: ArtifactFamily[TOwner, Any, Any],
    ) -> BoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_artifact_family(artifact_family)
        return BoundFamily(self.store, definition.artifact_family, definition, self.registry)

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
        return BoundFamily(self.store, definition.artifact_family, definition, self.registry)


@dataclass(frozen=True)
class BoundFamily(Generic[TOwner, TRef, TDoc]):
    store: DocumentFamilyStore[TOwner]
    family: ArtifactFamily[TOwner, TRef, TDoc]
    definition: FamilyDefinition[TOwner, Any, TRef, TDoc] | None = None
    registry: FamilyRegistry[TOwner, Any] | None = None

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
        prepared = self.store.prepare(self.family, ref, doc, branch=branch)
        if self.definition is not None and self.registry is not None:
            _validate_registry_post_state(
                self.store,
                self.registry,
                branch=prepared.branch,
                saves=((self.definition, ref, prepared.document),),
            )
        backend = self.store._require_backend()
        return backend.commit_batch(
            adds={address_path(prepared.address): prepared.content},
            deletes=[],
            message=message,
            branch=prepared.branch,
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
        address = self.store.address(self.family, ref, branch=branch)
        target_branch = branch or address.branch
        if self.definition is not None and self.registry is not None:
            _validate_registry_post_state(
                self.store,
                self.registry,
                branch=target_branch,
                deletes=((self.definition, ref),),
            )
        backend = self.store._require_backend()
        return backend.commit_batch(
            adds={},
            deletes=[address_path(address)],
            message=message,
            branch=target_branch,
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
        with self.store.transact(message=message, branch=branch, expected_head=expected_head) as transaction:
            transaction.move(self.family, old_ref, new_ref, doc)
        return cast(str, transaction.commit_sha)


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
    _saves: list[tuple[FamilyDefinition[TOwner, TKey, Any, Any], object, object]] = field(default_factory=list)
    _deletes: list[tuple[FamilyDefinition[TOwner, TKey, Any, Any], object]] = field(default_factory=list)

    @property
    def commit_sha(self) -> str | None:
        return self.transaction.commit_sha

    @property
    def owner(self) -> TOwner:
        return self.transaction.owner

    def by_key(self, key: TKey) -> TransactionalBoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_key(key)
        return TransactionalBoundFamily(self, definition)

    def by_name(self, name: str) -> TransactionalBoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_name(name)
        return TransactionalBoundFamily(self, definition)

    def by_artifact_family(
        self,
        artifact_family: ArtifactFamily[TOwner, Any, Any],
    ) -> TransactionalBoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_artifact_family(artifact_family)
        return TransactionalBoundFamily(self, definition)

    def commit(self) -> str:
        target_branch = self.transaction.branch
        if target_branch is not None:
            _validate_registry_post_state(
                self.transaction.store,
                self.registry,
                branch=target_branch,
                saves=tuple(self._saves),
                deletes=tuple(self._deletes),
            )
        return self.transaction.commit()

    def __enter__(self) -> BoundFamilyTransaction[TOwner, TKey]:
        self.transaction.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None and self.transaction.commit_sha is None and (self._saves or self._deletes):
            self.commit()
            return None
        return self.transaction.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> TransactionalBoundFamily[TOwner, Any, Any]:
        definition = self.registry.by_accessor(name)
        return TransactionalBoundFamily(self, definition)


@dataclass(frozen=True)
class TransactionalBoundFamily(Generic[TOwner, TRef, TDoc]):
    bound_transaction: BoundFamilyTransaction[TOwner, Any]
    definition: FamilyDefinition[TOwner, Any, TRef, TDoc]

    @property
    def transaction(self) -> DocumentFamilyTransaction[TOwner]:
        return self.bound_transaction.transaction

    @property
    def family(self) -> ArtifactFamily[TOwner, TRef, TDoc]:
        return self.definition.artifact_family

    def coerce(self, payload: object, *, source: str) -> TDoc:
        return self.transaction.coerce(self.family, payload, source=source)

    def ref_from_path(self, path: str) -> TRef:
        return self.transaction.store.ref_from_path(self.family, path)

    def payload(self, document: TDoc) -> object:
        return self.transaction.store.payload(document, family=self.family)

    def save(self, ref: TRef, doc: TDoc) -> None:
        self.transaction.save(self.family, ref, doc)
        self.bound_transaction._saves.append((self.definition, ref, doc))
        self.bound_transaction._deletes[:] = [
            item
            for item in self.bound_transaction._deletes
            if item != (self.definition, ref)
        ]

    def delete(self, ref: TRef) -> None:
        self.transaction.delete(self.family, ref)
        self.bound_transaction._deletes.append((self.definition, ref))
        self.bound_transaction._saves[:] = [
            item
            for item in self.bound_transaction._saves
            if item[0] != self.definition or item[1] != ref
        ]

    def move(self, old_ref: TRef, new_ref: TRef, doc: TDoc) -> None:
        self.transaction.move(self.family, old_ref, new_ref, doc)
        self.bound_transaction._deletes.append((self.definition, old_ref))
        self.bound_transaction._saves.append((self.definition, new_ref, doc))


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


def _validate_registry_post_state(
    store: DocumentFamilyStore[TOwner],
    registry: FamilyRegistry[TOwner, Any],
    *,
    branch: str,
    saves: Sequence[tuple[FamilyDefinition[TOwner, Any, Any, Any], object, object]] = (),
    deletes: Sequence[tuple[FamilyDefinition[TOwner, Any, Any, Any], object]] = (),
) -> None:
    changed_names: set[str] = set()
    for definition, _ref, _document in saves:
        changed_names.add(definition.name)
    for definition, _ref in deletes:
        changed_names.add(definition.name)
    relevant_names: set[str] = set()
    for definition in registry.families:
        for spec in definition.foreign_keys:
            if spec.source_family in changed_names or spec.target_family in changed_names:
                relevant_names.add(spec.source_family)
                relevant_names.add(spec.target_family)
    if not relevant_names:
        return

    records_by_family: dict[str, dict[object, object]] = {}
    for definition in registry.families:
        if definition.name not in relevant_names:
            continue
        records_by_family[definition.name] = {
            handle.ref: handle.document
            for handle in store.iter_handles(definition.artifact_family, branch=branch)
        }
    for definition, ref in deletes:
        if definition.name not in records_by_family:
            continue
        records_by_family[definition.name].pop(ref, None)
    for definition, ref, document in saves:
        if definition.name not in records_by_family:
            continue
        records_by_family[definition.name][ref] = document

    indexes = {
        definition.name: definition.reference_index_from_records(tuple(records_by_family[definition.name].values()))
        for definition in registry.families
        if definition.name in records_by_family and definition.identity_field is not None
    }
    for definition in registry.families:
        if definition.name not in records_by_family:
            continue
        for spec in definition.foreign_keys:
            if spec.target_family not in indexes:
                continue
            target_index = indexes[spec.target_family]
            for record in records_by_family[definition.name].values():
                validate_foreign_key(spec, record, target_index)  # type: ignore[arg-type]

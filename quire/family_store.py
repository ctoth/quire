from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, Protocol, TypeVar, cast

from quire.artifacts import (
    ArtifactAddress,
    ArtifactContext,
    ArtifactFamily,
    ArtifactHandle,
    BranchPlacement,
    PathArtifactLocator,
    PreparedArtifact,
    ReadOnlyDocumentStoreBackend,
    ScannedArtifact,
    TDoc,
    TRef,
)
from quire.git_store import HeadMismatchError
from quire.documents.codecs import (
    DEFAULT_DOCUMENT_CODEC,
    DocumentCodec,
)

TOwner = TypeVar("TOwner")


class DocumentStoreBackend(ReadOnlyDocumentStoreBackend, Protocol):
    def commit_batch(
        self,
        adds: Mapping[str | Path, bytes],
        deletes: Sequence[str | Path],
        message: str,
        *,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        ...


BranchHeadResolver = Callable[[DocumentStoreBackend, str], str | None]


def normalized_path(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def address_path(address: ArtifactAddress) -> str:
    if not isinstance(address.locator, PathArtifactLocator):
        raise TypeError(f"document family store only supports path locators, got {type(address.locator).__name__}")
    return normalized_path(address.locator.path)


def default_branch_head(backend: DocumentStoreBackend, branch: str) -> str | None:
    return backend.branch_sha(branch)


@dataclass
class DocumentFamilyStore(Generic[TOwner]):
    owner: TOwner
    backend: DocumentStoreBackend | None
    branch_head: BranchHeadResolver = default_branch_head
    codec: DocumentCodec = DEFAULT_DOCUMENT_CODEC

    def address(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> ArtifactAddress:
        address = family.address_for(self.owner, ref)
        target_branch = branch or address.branch
        if commit is None and target_branch == address.branch:
            return address
        return ArtifactAddress(branch=target_branch, locator=address.locator, commit=commit)

    def ref_from_path(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        path: str | Path,
    ) -> TRef:
        return family.placement.ref_from_locator(PathArtifactLocator(normalized_path(path)))

    def ref_from_loaded(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        loaded: object,
    ) -> TRef:
        return family.placement.ref_from_loaded(loaded)

    def coerce(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        payload: object,
        *,
        source: str,
    ) -> TDoc:
        if family.coerce_payload is not None:
            return family.coerce_payload(payload, source)
        return self.codec.convert(payload, family.doc_type, source=source)

    def render(self, document: object, family: ArtifactFamily[object, object, object] | None = None) -> str:
        if family is not None and family.render_document is not None:
            return family.render_document(document)
        return self.codec.render(document)

    def payload(self, document: object, family: ArtifactFamily[object, object, object] | None = None) -> object:
        if family is not None and family.document_payload is not None:
            return family.document_payload(document)
        return self.codec.payload(document)

    def prepare(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        doc: TDoc,
        *,
        branch: str | None = None,
    ) -> PreparedArtifact[TOwner, TRef, TDoc]:
        address = self.address(family, ref)
        target_branch = branch or address.branch
        context = ArtifactContext(
            repo=self.owner,
            ref=ref,
            branch=target_branch,
            address=ArtifactAddress(
                branch=target_branch,
                locator=address.locator,
                commit=address.commit,
            ),
        )
        normalized = doc
        if family.normalize_for_write is not None:
            normalized = family.normalize_for_write(context, normalized, self)
        if family.validate_for_write is not None:
            family.validate_for_write(context, normalized, self)
        encoder = family.encode_document or self.codec.encode
        return PreparedArtifact(
            family=family,
            ref=ref,
            address=address,
            branch=target_branch,
            document=normalized,
            content=encoder(normalized),
        )

    def load(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> TDoc | None:
        backend = self._require_backend()
        address = self.address(family, ref, branch=branch, commit=commit)
        target_commit = commit or address.commit
        if target_commit is None:
            target_commit = self.branch_head(backend, address.branch)
            if target_commit is None:
                return None
        path = address_path(address)
        try:
            raw = backend.read_file(path, commit=target_commit)
        except FileNotFoundError:
            return None
        source = f"{address.branch}:{path}"
        if family.decode_bytes is not None:
            return family.decode_bytes(raw, source)
        return self.codec.decode(raw, family.doc_type, source=source)

    def exists(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> bool:
        backend = self._require_backend()
        address = self.address(family, ref, branch=branch, commit=commit)
        target_commit = commit or address.commit
        if target_commit is None:
            target_commit = self.branch_head(backend, address.branch)
            if target_commit is None:
                return False
        return backend.exists(address_path(address), commit=target_commit) is not None

    def handle(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> ArtifactHandle[TOwner, TRef, TDoc] | None:
        document = self.load(family, ref, branch=branch, commit=commit)
        if document is None:
            return None
        return ArtifactHandle(
            family=family,
            ref=ref,
            address=self.address(family, ref, branch=branch, commit=commit),
            document=document,
        )

    def iter_handles(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> Iterator[ArtifactHandle[TOwner, TRef, TDoc]]:
        backend = self._require_backend()
        for scanned in family.placement.iter_artifacts(
            self.owner,
            backend,
            branch=branch,
            commit=commit,
        ):
            yield ArtifactHandle(
                family=family,
                ref=scanned.ref,
                address=scanned.address,
                document=self._decode_scanned_artifact(family, scanned),
            )

    def require_handle(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> ArtifactHandle[TOwner, TRef, TDoc]:
        handle = self.handle(family, ref, branch=branch, commit=commit)
        if handle is None:
            address = self.address(family, ref, branch=branch, commit=commit)
            raise FileNotFoundError(f"{family.name}: {address.branch}:{address_path(address)}")
        return handle

    def require(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> TDoc:
        return self.require_handle(family, ref, branch=branch, commit=commit).document

    def save(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        doc: TDoc,
        *,
        message: str,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        backend = self._require_backend()
        prepared = self.prepare(family, ref, doc, branch=branch)
        return backend.commit_batch(
            adds={address_path(prepared.address): prepared.content},
            deletes=[],
            message=message,
            branch=prepared.branch,
            expected_head=expected_head,
        )

    def move(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        old_ref: TRef,
        new_ref: TRef,
        doc: TDoc,
        *,
        message: str,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self.transact(message=message, branch=branch, expected_head=expected_head) as transaction:
            transaction.move(family, old_ref, new_ref, doc)
        return cast(str, transaction.commit_sha)

    def delete(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
        *,
        message: str,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        backend = self._require_backend()
        address = self.address(family, ref)
        target_branch = branch or address.branch
        return backend.commit_batch(
            adds={},
            deletes=[address_path(address)],
            message=message,
            branch=target_branch,
            expected_head=expected_head,
        )

    def iter(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> Iterator[TRef]:
        return family.placement.iter_refs(
            self.owner,
            self.backend,
            branch=branch,
            commit=commit,
        )

    def transact(
        self,
        *,
        message: str,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> DocumentFamilyTransaction[TOwner]:
        return DocumentFamilyTransaction(
            store=self,
            message=message,
            branch=branch,
            expected_head=expected_head,
        )

    def pin(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> tuple[str, str | None]:
        target_branch = branch
        if target_branch is None:
            branch_policy = getattr(family.placement, "branch", None)
            if not isinstance(branch_policy, BranchPlacement):
                raise TypeError(
                    "family pinning requires a placement with a branch policy or an explicit branch"
                )
            try:
                target_branch = branch_policy.branch_name(self.owner)
            except ValueError as exc:
                raise ValueError("family pinning requires an explicit branch for this placement") from exc
        if commit is not None:
            return target_branch, commit
        backend = self._require_backend()
        return target_branch, self.branch_head(backend, target_branch)

    def _require_backend(self) -> DocumentStoreBackend:
        if self.backend is None:
            raise ValueError("document family operations require a git-backed repository")
        return self.backend

    def _decode_scanned_artifact(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        scanned: ScannedArtifact[TRef],
    ) -> TDoc:
        path = address_path(scanned.address)
        source = f"{scanned.address.branch}:{path}"
        if family.decode_bytes is not None:
            return family.decode_bytes(scanned.content, source)
        return self.codec.decode(scanned.content, family.doc_type, source=source)


@dataclass
class DocumentFamilyTransaction(Generic[TOwner]):
    store: DocumentFamilyStore[TOwner]
    message: str
    branch: str | None = None
    expected_head: str | None = None
    _adds: dict[str, bytes] = field(default_factory=dict)
    _deletes: set[str] = field(default_factory=set)
    _commit_sha: str | None = None
    _explicit_branch: bool = field(init=False)

    def __post_init__(self) -> None:
        self._explicit_branch = self.branch is not None

    @property
    def commit_sha(self) -> str | None:
        return self._commit_sha

    @property
    def owner(self) -> TOwner:
        return self.store.owner

    def coerce(self, family: ArtifactFamily[TOwner, TRef, TDoc], payload: object, *, source: str) -> TDoc:
        return self.store.coerce(family, payload, source=source)

    def payload(self, document: object) -> object:
        return self.store.payload(document)

    def __enter__(self) -> DocumentFamilyTransaction[TOwner]:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None and self._commit_sha is None and (self._adds or self._deletes):
            self.commit()

    def save(self, family: ArtifactFamily[TOwner, TRef, TDoc], ref: TRef, doc: TDoc) -> None:
        self._ensure_open()
        self._advisory_head_check()
        prepared = self.store.prepare(family, ref, doc, branch=self.branch)
        if self.branch is None:
            self.branch = prepared.branch
        elif not self._explicit_branch and prepared.address.branch != self.branch:
            raise ValueError(
                f"Transaction branch mismatch: expected {self.branch!r}, got {prepared.address.branch!r}"
            )
        path = address_path(prepared.address)
        self._adds[path] = prepared.content
        self._deletes.discard(path)

    def delete(self, family: ArtifactFamily[TOwner, TRef, TDoc], ref: TRef) -> None:
        self._ensure_open()
        self._advisory_head_check()
        _, address = self._addressed_target(family, ref)
        path = address_path(address)
        self._deletes.add(path)
        self._adds.pop(path, None)

    def move(self, family: ArtifactFamily[TOwner, TRef, TDoc], old_ref: TRef, new_ref: TRef, doc: TDoc) -> None:
        self._ensure_open()
        self._advisory_head_check()
        self.save(family, new_ref, doc)
        old_branch, old_address = self._addressed_target(family, old_ref)
        new_branch, new_address = self._addressed_target(family, new_ref)
        if old_branch != new_branch:
            raise ValueError(
                f"Transaction branch mismatch for move: expected {new_branch!r}, got {old_branch!r}"
            )
        old_path = address_path(old_address)
        new_path = address_path(new_address)
        if old_path != new_path:
            self._deletes.add(old_path)
            self._adds.pop(old_path, None)

    def commit(self) -> str:
        if self._commit_sha is not None:
            return self._commit_sha
        self._advisory_head_check()
        backend = self.store._require_backend()
        if self.branch is None:
            raise ValueError("artifact transaction has no target branch")
        self._commit_sha = backend.commit_batch(
            adds=cast(Mapping[str | Path, bytes], self._adds),
            deletes=sorted(self._deletes),
            message=self.message,
            branch=self.branch,
            expected_head=self.expected_head,
        )
        return self._commit_sha

    def _advisory_head_check(self) -> None:
        """Fail early when the branch is already stale before commit CAS."""
        if self.expected_head is None or self.branch is None:
            return
        backend = self.store.backend
        if backend is None:
            return
        current = self.store.branch_head(backend, self.branch)
        if current is not None and current != self.expected_head:
            raise HeadMismatchError(
                branch=self.branch,
                expected_head=self.expected_head,
                actual_head=current,
            )

    def _addressed_target(
        self,
        family: ArtifactFamily[TOwner, TRef, TDoc],
        ref: TRef,
    ) -> tuple[str, ArtifactAddress]:
        address = family.address_for(self.owner, ref)
        branch = self.branch or address.branch
        if self.branch is None:
            self.branch = branch
        elif branch != self.branch:
            raise ValueError(f"Transaction branch mismatch: expected {self.branch!r}, got {branch!r}")
        return branch, address

    def _ensure_open(self) -> None:
        if self._commit_sha is not None:
            raise ValueError("artifact transaction is already committed")

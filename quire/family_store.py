from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, Protocol, TypeVar, cast

from quire.artifacts import (
    ArtifactContext,
    ArtifactFamily,
    ArtifactHandle,
    PreparedArtifact,
    ResolvedArtifact,
    TDoc,
    TRef,
)
from quire.documents.codecs import (
    convert_document,
    decode_document,
    document_to_payload,
    encode_document,
    render_document,
)

TOwner = TypeVar("TOwner")


class DocumentStoreBackend(Protocol):
    def read_file(self, path: str | Path, commit: str | None = None) -> bytes:
        ...

    def commit_batch(
        self,
        adds: Mapping[str | Path, bytes],
        deletes: Sequence[str | Path],
        message: str,
        *,
        branch: str | None = None,
    ) -> str:
        ...

    def branch_sha(self, name: str) -> str | None:
        ...


BranchHeadResolver = Callable[[DocumentStoreBackend, str], str | None]


def normalized_path(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def default_branch_head(backend: DocumentStoreBackend, branch: str) -> str | None:
    return backend.branch_sha(branch)


def _convert_document(payload: object, document_type: type[TDoc], source: str) -> TDoc:
    return convert_document(payload, document_type, source=source)


def _decode_document(payload: bytes, document_type: type[TDoc], source: str) -> TDoc:
    return decode_document(payload, document_type, source=source)


@dataclass
class DocumentFamilyStore(Generic[TOwner]):
    owner: TOwner
    backend: DocumentStoreBackend | None
    branch_head: BranchHeadResolver = default_branch_head
    convert_document: Callable[[object, type[TDoc], str], TDoc] = _convert_document
    decode_document: Callable[[bytes, type[TDoc], str], TDoc] = _decode_document
    encode_document: Callable[[object], bytes] = encode_document
    render_document_value: Callable[[object], str] = render_document
    document_to_payload: Callable[[object], object] = document_to_payload

    def resolve(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
        *,
        commit: str | None = None,
    ) -> ResolvedArtifact:
        resolved = family.resolve_ref(self.owner, ref)
        if commit is None:
            return resolved
        return ResolvedArtifact(branch=resolved.branch, relpath=resolved.relpath, commit=commit)

    def ref_from_path(
        self,
        family: ArtifactFamily[TRef, TDoc],
        path: str | Path,
    ) -> TRef:
        if family.ref_from_path is None:
            raise TypeError(f"{family.name} does not support path-derived refs")
        return family.ref_from_path(path)

    def ref_from_loaded(
        self,
        family: ArtifactFamily[TRef, TDoc],
        loaded: object,
    ) -> TRef:
        if family.ref_from_loaded is None:
            raise TypeError(f"{family.name} does not support loaded-object refs")
        return family.ref_from_loaded(loaded)

    def coerce(
        self,
        family: ArtifactFamily[TRef, TDoc],
        payload: object,
        *,
        source: str,
    ) -> TDoc:
        if family.coerce_payload is not None:
            return family.coerce_payload(payload, source)
        return self.convert_document(payload, family.doc_type, source)

    def render(self, document: object, family: ArtifactFamily[object, object] | None = None) -> str:
        if family is not None and family.render_document is not None:
            return family.render_document(document)
        return self.render_document_value(document)

    def payload(self, document: object, family: ArtifactFamily[object, object] | None = None) -> object:
        if family is not None and family.document_payload is not None:
            return family.document_payload(document)
        return self.document_to_payload(document)

    def prepare(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
        doc: TDoc,
        *,
        branch: str | None = None,
    ) -> PreparedArtifact[TRef, TDoc]:
        resolved = self.resolve(family, ref)
        target_branch = branch or resolved.branch
        context = ArtifactContext(
            repo=self.owner,
            ref=ref,
            branch=target_branch,
            relpath=resolved.relpath,
        )
        normalized = doc
        if family.normalize_for_write is not None:
            normalized = family.normalize_for_write(context, normalized, self)
        if family.validate_for_write is not None:
            family.validate_for_write(context, normalized, self)
        encoder = family.encode_document or self.encode_document
        return PreparedArtifact(
            family=family,
            ref=ref,
            resolved=resolved,
            branch=target_branch,
            document=normalized,
            content=encoder(normalized),
        )

    def load(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
        *,
        commit: str | None = None,
    ) -> TDoc | None:
        backend = self._require_backend()
        resolved = self.resolve(family, ref, commit=commit)
        target_commit = commit or resolved.commit
        if target_commit is None:
            target_commit = self.branch_head(backend, resolved.branch)
            if target_commit is None:
                return None
        try:
            raw = backend.read_file(resolved.relpath, commit=target_commit)
        except FileNotFoundError:
            return None
        source = f"{resolved.branch}:{normalized_path(resolved.relpath)}"
        if family.decode_bytes is not None:
            return family.decode_bytes(raw, source)
        return self.decode_document(raw, family.doc_type, source)

    def handle(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
        *,
        commit: str | None = None,
    ) -> ArtifactHandle[TRef, TDoc] | None:
        document = self.load(family, ref, commit=commit)
        if document is None:
            return None
        return ArtifactHandle(
            family=family,
            ref=ref,
            resolved=self.resolve(family, ref, commit=commit),
            document=document,
        )

    def require_handle(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
        *,
        commit: str | None = None,
    ) -> ArtifactHandle[TRef, TDoc]:
        handle = self.handle(family, ref, commit=commit)
        if handle is None:
            resolved = self.resolve(family, ref, commit=commit)
            raise FileNotFoundError(f"{family.name}: {resolved.branch}:{normalized_path(resolved.relpath)}")
        return handle

    def require(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
        *,
        commit: str | None = None,
    ) -> TDoc:
        return self.require_handle(family, ref, commit=commit).document

    def save(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
        doc: TDoc,
        *,
        message: str,
        branch: str | None = None,
    ) -> str:
        backend = self._require_backend()
        prepared = self.prepare(family, ref, doc, branch=branch)
        return backend.commit_batch(
            adds={normalized_path(prepared.resolved.relpath): prepared.content},
            deletes=[],
            message=message,
            branch=prepared.branch,
        )

    def move(
        self,
        family: ArtifactFamily[TRef, TDoc],
        old_ref: TRef,
        new_ref: TRef,
        doc: TDoc,
        *,
        message: str,
        branch: str | None = None,
    ) -> str:
        with self.transact(message=message, branch=branch) as transaction:
            transaction.move(family, old_ref, new_ref, doc)
        if transaction.commit_sha is None:
            raise ValueError("artifact move did not produce a commit")
        return transaction.commit_sha

    def delete(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
        *,
        message: str,
        branch: str | None = None,
    ) -> str:
        backend = self._require_backend()
        resolved = self.resolve(family, ref)
        target_branch = branch or resolved.branch
        return backend.commit_batch(
            adds={},
            deletes=[normalized_path(resolved.relpath)],
            message=message,
            branch=target_branch,
        )

    def list(
        self,
        family: ArtifactFamily[TRef, TDoc],
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> list[TRef]:
        if family.list_refs is None:
            raise TypeError(f"{family.name} does not support listing")
        return family.list_refs(self.owner, branch, commit)

    def transact(
        self,
        *,
        message: str,
        branch: str | None = None,
    ) -> DocumentFamilyTransaction[TOwner]:
        return DocumentFamilyTransaction(store=self, message=message, branch=branch)

    def _require_backend(self) -> DocumentStoreBackend:
        if self.backend is None:
            raise ValueError("document family operations require a git-backed repository")
        return self.backend


@dataclass
class DocumentFamilyTransaction(Generic[TOwner]):
    store: DocumentFamilyStore[TOwner]
    message: str
    branch: str | None = None
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

    def coerce(self, family: ArtifactFamily[TRef, TDoc], payload: object, *, source: str) -> TDoc:
        return self.store.coerce(family, payload, source=source)

    def payload(self, document: object) -> object:
        return self.store.payload(document)

    def __enter__(self) -> DocumentFamilyTransaction[TOwner]:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None and self._commit_sha is None and (self._adds or self._deletes):
            self.commit()

    def save(self, family: ArtifactFamily[TRef, TDoc], ref: TRef, doc: TDoc) -> None:
        self._ensure_open()
        prepared = self.store.prepare(family, ref, doc, branch=self.branch)
        if self.branch is None:
            self.branch = prepared.branch
        elif not self._explicit_branch and prepared.resolved.branch != self.branch:
            raise ValueError(
                f"Transaction branch mismatch: expected {self.branch!r}, got {prepared.resolved.branch!r}"
            )
        elif prepared.branch != self.branch:
            raise ValueError(f"Transaction branch mismatch: expected {self.branch!r}, got {prepared.branch!r}")
        relpath = normalized_path(prepared.resolved.relpath)
        self._adds[relpath] = prepared.content
        self._deletes.discard(relpath)

    def delete(self, family: ArtifactFamily[TRef, TDoc], ref: TRef) -> None:
        self._ensure_open()
        _, resolved = self._resolved_target(family, ref)
        relpath = normalized_path(resolved.relpath)
        self._deletes.add(relpath)
        self._adds.pop(relpath, None)

    def move(self, family: ArtifactFamily[TRef, TDoc], old_ref: TRef, new_ref: TRef, doc: TDoc) -> None:
        self._ensure_open()
        self.save(family, new_ref, doc)
        old_branch, old_resolved = self._resolved_target(family, old_ref)
        new_branch, _ = self._resolved_target(family, new_ref)
        if old_branch != new_branch:
            raise ValueError(
                f"Transaction branch mismatch for move: expected {new_branch!r}, got {old_branch!r}"
            )
        old_relpath = normalized_path(old_resolved.relpath)
        new_relpath = normalized_path(family.resolve_ref(self.owner, new_ref).relpath)
        if old_relpath != new_relpath:
            self._deletes.add(old_relpath)
            self._adds.pop(old_relpath, None)

    def commit(self) -> str:
        if self._commit_sha is not None:
            return self._commit_sha
        backend = self.store._require_backend()
        if self.branch is None:
            raise ValueError("artifact transaction has no target branch")
        self._commit_sha = backend.commit_batch(
            adds=cast(Mapping[str | Path, bytes], self._adds),
            deletes=sorted(self._deletes),
            message=self.message,
            branch=self.branch,
        )
        return self._commit_sha

    def _resolved_target(
        self,
        family: ArtifactFamily[TRef, TDoc],
        ref: TRef,
    ) -> tuple[str, ResolvedArtifact]:
        resolved = family.resolve_ref(self.owner, ref)
        branch = self.branch or resolved.branch
        if self.branch is None:
            self.branch = branch
        elif branch != self.branch:
            raise ValueError(f"Transaction branch mismatch: expected {self.branch!r}, got {branch!r}")
        return branch, resolved

    def _ensure_open(self) -> None:
        if self._commit_sha is not None:
            raise ValueError("artifact transaction is already committed")

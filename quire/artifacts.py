from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

import msgspec

from quire.versions import VersionId

TRef = TypeVar("TRef")
TDoc = TypeVar("TDoc")
TOwner = TypeVar("TOwner")


@dataclass(frozen=True)
class ResolvedArtifact:
    branch: str
    relpath: str
    commit: str | None = None


@dataclass(frozen=True)
class ArtifactContext(Generic[TOwner, TRef]):
    repo: TOwner
    ref: TRef
    branch: str
    relpath: str


@dataclass(frozen=True)
class ArtifactHandle(Generic[TOwner, TRef, TDoc]):
    family: ArtifactFamily[TOwner, TRef, TDoc]
    ref: TRef
    resolved: ResolvedArtifact
    document: TDoc


@dataclass(frozen=True)
class PreparedArtifact(Generic[TOwner, TRef, TDoc]):
    family: ArtifactFamily[TOwner, TRef, TDoc]
    ref: TRef
    resolved: ResolvedArtifact
    branch: str
    document: TDoc
    content: bytes


@dataclass(frozen=True)
class ArtifactFamily(Generic[TOwner, TRef, TDoc]):
    name: str
    contract_version: VersionId
    doc_type: type[TDoc]
    resolve_ref: Callable[[TOwner, TRef], ResolvedArtifact]
    coerce_payload: Callable[[object, str], TDoc] | None = None
    decode_bytes: Callable[[bytes, str], TDoc] | None = None
    encode_document: Callable[[TDoc], bytes] | None = None
    render_document: Callable[[TDoc], str] | None = None
    document_payload: Callable[[TDoc], object] | None = None
    normalize_for_write: Callable[[ArtifactContext[TOwner, TRef], TDoc, Any], TDoc] | None = None
    validate_for_write: Callable[[ArtifactContext[TOwner, TRef], TDoc, Any], None] | None = None
    list_refs: Callable[[TOwner, str | None, str | None], list[TRef]] | None = None
    ref_from_path: Callable[[str | Path], TRef] | None = None
    ref_from_loaded: Callable[[Any], TRef] | None = None
    scan_type: type[msgspec.Struct] | None = None

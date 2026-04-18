from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

import msgspec

from quire.versions import VersionId

TRef = TypeVar("TRef")
TDoc = TypeVar("TDoc")


@dataclass(frozen=True)
class ResolvedArtifact:
    branch: str
    relpath: str
    commit: str | None = None


@dataclass(frozen=True)
class ArtifactContext(Generic[TRef]):
    repo: Any
    ref: TRef
    branch: str
    relpath: str


@dataclass(frozen=True)
class ArtifactHandle(Generic[TRef, TDoc]):
    family: ArtifactFamily[TRef, TDoc]
    ref: TRef
    resolved: ResolvedArtifact
    document: TDoc


@dataclass(frozen=True)
class PreparedArtifact(Generic[TRef, TDoc]):
    family: ArtifactFamily[TRef, TDoc]
    ref: TRef
    resolved: ResolvedArtifact
    branch: str
    document: TDoc
    content: bytes


@dataclass(frozen=True)
class ArtifactFamily(Generic[TRef, TDoc]):
    name: str
    contract_version: VersionId
    doc_type: type[TDoc]
    resolve_ref: Callable[[Any, TRef], ResolvedArtifact]
    coerce_payload: Callable[[object, str], TDoc] | None = None
    decode_bytes: Callable[[bytes, str], TDoc] | None = None
    encode_document: Callable[[TDoc], bytes] | None = None
    render_document: Callable[[TDoc], str] | None = None
    document_payload: Callable[[TDoc], object] | None = None
    normalize_for_write: Callable[[ArtifactContext[TRef], TDoc, Any], TDoc] | None = None
    validate_for_write: Callable[[ArtifactContext[TRef], TDoc, Any], None] | None = None
    list_refs: Callable[[Any, str | None, str | None], list[TRef]] | None = None
    ref_from_path: Callable[[str | Path], TRef] | None = None
    ref_from_loaded: Callable[[Any], TRef] | None = None
    scan_type: type[msgspec.Struct] | None = None

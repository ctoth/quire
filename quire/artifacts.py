from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Literal, Protocol, TypeAlias, TypeVar, runtime_checkable

import msgspec

from quire.versions import VersionId

TRef = TypeVar("TRef")
TDoc = TypeVar("TDoc")
TOwner = TypeVar("TOwner")

BranchPolicy: TypeAlias = Literal["owner", "primary", "current", "fixed", "template"]
ReversibleRefCodec: TypeAlias = Literal["identity", "stem", "colon_to_double_underscore"]
OneWayRefCodec: TypeAlias = Literal["slug", "safe_slug"]
RefCodec: TypeAlias = ReversibleRefCodec | OneWayRefCodec
HashScatteredFilenameMode: TypeAlias = Literal["digest", "encoded_ref"]

_BRANCH_POLICIES = frozenset({"owner", "primary", "current", "fixed", "template"})
_REVERSIBLE_REF_CODECS = frozenset({"identity", "stem", "colon_to_double_underscore"})
_REF_CODECS = _REVERSIBLE_REF_CODECS | frozenset({"slug", "safe_slug"})
_HASH_SCATTERED_FILENAME_MODES = frozenset({"digest", "encoded_ref"})


def _normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip().lower())
    cleaned = cleaned.strip("_-")
    return cleaned or "artifact"


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in value.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "artifact"


def _ref_value(ref: object, field: str) -> str:
    if field == "self":
        value = ref
    else:
        value = getattr(ref, field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ref field {field!r} must be a non-empty string")
    return value


def encode_ref_value(value: str, codec: str) -> str:
    if codec in {"identity", "stem"}:
        return value
    if codec == "colon_to_double_underscore":
        return value.replace(":", "__")
    if codec == "slug":
        return _slug(value)
    if codec == "safe_slug":
        return _safe_slug(value)
    raise ValueError(f"unknown ref codec: {codec}")


def decode_ref_value(value: str, codec: str) -> str:
    if codec in {"identity", "stem"}:
        return value
    if codec == "colon_to_double_underscore":
        return value.replace("__", ":")
    if codec in {"slug", "safe_slug"}:
        raise ValueError("slug codec is not reversible")
    raise ValueError(f"unknown ref codec: {codec}")


@runtime_checkable
class PrimaryBranchOwner(Protocol):
    """Owner shape for placements that need a primary branch name."""

    def primary_branch_name(self) -> str:
        ...


@runtime_checkable
class CurrentBranchOwner(Protocol):
    """Owner shape for placements that can expose a current branch name."""

    def current_branch_name(self) -> str | None:
        ...


def _require_ref_codec(codec: str) -> None:
    if codec not in _REF_CODECS:
        raise ValueError(f"unknown ref codec: {codec}")


def _require_reversible_ref_codec(codec: str) -> None:
    _require_ref_codec(codec)
    if codec not in _REVERSIBLE_REF_CODECS:
        raise ValueError(f"{codec!r} requires a reversible ref codec")


def _owner_primary_branch(owner: object) -> str:
    if isinstance(owner, PrimaryBranchOwner):
        return str(owner.primary_branch_name())
    snapshot = getattr(owner, "snapshot", None)
    if isinstance(snapshot, PrimaryBranchOwner):
        return str(snapshot.primary_branch_name())
    git = getattr(owner, "git", None)
    if isinstance(git, PrimaryBranchOwner):
        return str(git.primary_branch_name())
    raise ValueError("primary branch policy requires an owner with primary_branch_name")


def _owner_current_branch(owner: object) -> str:
    if isinstance(owner, CurrentBranchOwner):
        current = owner.current_branch_name()
        if current:
            return str(current)
    snapshot = getattr(owner, "snapshot", None)
    if isinstance(snapshot, CurrentBranchOwner):
        current = snapshot.current_branch_name()
        if current:
            return str(current)
    git = getattr(owner, "git", None)
    if isinstance(git, CurrentBranchOwner):
        current = git.current_branch_name()
        if current:
            return str(current)
    return _owner_primary_branch(owner)


@runtime_checkable
class ArtifactLocator(Protocol):
    def to_contract_body(self) -> dict[str, object]:
        ...


@dataclass(frozen=True)
class PathArtifactLocator:
    path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _normalize_path(self.path))

    def to_contract_body(self) -> dict[str, object]:
        return {"kind": "path"}


@dataclass(frozen=True)
class ArtifactAddress:
    branch: str
    locator: ArtifactLocator
    commit: str | None = None

    def require_path(self) -> str:
        if not isinstance(self.locator, PathArtifactLocator):
            raise TypeError(f"artifact address is not path-backed: {type(self.locator).__name__}")
        return self.locator.path


@runtime_checkable
class DocumentStoreBackend(Protocol):
    def read_file(self, path: str | Path, commit: str | None = None) -> bytes:
        ...

    def list_dir_entries(
        self,
        subdir: str | Path,
        commit: str | None = None,
    ) -> list[tuple[str, bool]]:
        ...

    def branch_sha(self, name: str) -> str | None:
        ...


@dataclass(frozen=True)
class BranchPlacement:
    policy: BranchPolicy = "owner"
    fixed_branch: str | None = None
    template: str | None = None
    ref_field: str = "name"
    codec: RefCodec = "stem"

    def __post_init__(self) -> None:
        if self.policy not in _BRANCH_POLICIES:
            raise ValueError(f"unknown branch policy: {self.policy}")
        _require_ref_codec(self.codec)

    def branch_name(self, owner: object, ref: object | None = None) -> str:
        if self.policy == "owner":
            branch = getattr(owner, "branch", None)
            if isinstance(branch, str) and branch:
                return branch
            return _owner_current_branch(owner)
        if self.policy == "primary":
            return _owner_primary_branch(owner)
        if self.policy == "current":
            return _owner_current_branch(owner)
        if self.policy == "fixed":
            if not self.fixed_branch:
                raise ValueError("fixed branch policy requires fixed_branch")
            return self.fixed_branch
        if self.policy == "template":
            if ref is None:
                raise ValueError("template branch policy requires a ref")
            if not self.template:
                raise ValueError("template branch policy requires template")
            value = encode_ref_value(_ref_value(ref, self.ref_field), self.codec)
            return self.template.format(value=value, stem=value)
        raise ValueError(f"unknown branch policy: {self.policy}")

    def contract_body(self) -> dict[str, object]:
        body: dict[str, object] = {"policy": self.policy}
        if self.fixed_branch is not None:
            body["fixed_branch"] = self.fixed_branch
        if self.template is not None:
            body["template"] = self.template
            body["ref_field"] = self.ref_field
            body["codec"] = self.codec
        return body


@runtime_checkable
class ArtifactPlacementPolicy(Protocol[TOwner, TRef]):
    def address_for(self, owner: TOwner, ref: TRef) -> ArtifactAddress:
        ...

    def list_refs(
        self,
        owner: TOwner,
        backend: DocumentStoreBackend | None,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> list[TRef]:
        ...

    def ref_from_locator(self, locator: ArtifactLocator) -> TRef:
        ...

    def ref_from_loaded(self, loaded: object) -> TRef:
        ...

    def contract_body(self) -> dict[str, object]:
        ...


@dataclass(frozen=True)
class FlatYamlPlacement(Generic[TOwner, TRef]):
    namespace: str
    ref_factory: Callable[[str], TRef]
    ref_field: str = "self"
    extension: str = ".yaml"
    codec: ReversibleRefCodec = "stem"
    branch: BranchPlacement = BranchPlacement()

    def __post_init__(self) -> None:
        _require_reversible_ref_codec(self.codec)

    def address_for(self, owner: TOwner, ref: TRef) -> ArtifactAddress:
        stem = encode_ref_value(_ref_value(ref, self.ref_field), self.codec)
        return ArtifactAddress(
            branch=self.branch.branch_name(owner, ref),
            locator=PathArtifactLocator(f"{self.namespace}/{stem}{self.extension}"),
        )

    def list_refs(
        self,
        owner: TOwner,
        backend: DocumentStoreBackend | None,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> list[TRef]:
        if backend is None:
            raise ValueError("listing path-backed artifacts requires a backend")
        target_commit = commit
        if target_commit is None:
            target_commit = backend.branch_sha(branch or self.branch.branch_name(owner))
            if target_commit is None:
                return []
        refs: list[TRef] = []
        for name, is_dir in backend.list_dir_entries(self.namespace, commit=target_commit):
            if is_dir or not name.endswith(self.extension):
                continue
            stem = name.removesuffix(self.extension)
            refs.append(self.ref_factory(decode_ref_value(stem, self.codec)))
        return refs

    def ref_from_locator(self, locator: ArtifactLocator) -> TRef:
        if not isinstance(locator, PathArtifactLocator):
            raise TypeError("flat YAML placement only supports path locators")
        prefix = f"{self.namespace}/"
        path = _normalize_path(locator.path)
        if not path.startswith(prefix) or not path.endswith(self.extension):
            raise ValueError(f"expected {self.namespace}/*{self.extension}, got {path!r}")
        tail = path.removeprefix(prefix)
        if "/" in tail:
            raise ValueError(f"expected direct child under {self.namespace}, got {path!r}")
        return self.ref_factory(decode_ref_value(tail.removesuffix(self.extension), self.codec))

    def ref_from_loaded(self, loaded: object) -> TRef:
        source_path = getattr(loaded, "source_path", None)
        if source_path is None:
            raise ValueError("loaded artifact does not expose source_path")
        rendered = source_path.as_posix() if hasattr(source_path, "as_posix") else str(source_path)
        marker = f"{self.namespace}/"
        normalized = rendered.replace("\\", "/")
        if not normalized.startswith(marker):
            index = normalized.rfind(f"/{marker}")
            if index < 0:
                raise ValueError(f"loaded source path is not under {self.namespace}: {rendered!r}")
            normalized = normalized[index + 1:]
        return self.ref_from_locator(PathArtifactLocator(normalized))

    def contract_body(self) -> dict[str, object]:
        return {
            "kind": "flat-yaml",
            "namespace": self.namespace,
            "extension": self.extension,
            "ref_field": self.ref_field,
            "codec": self.codec,
            "branch": self.branch.contract_body(),
        }


@dataclass(frozen=True)
class HashScatteredYamlPlacement(Generic[TOwner, TRef]):
    namespace: str
    ref_factory: Callable[[str], TRef]
    ref_field: str = "self"
    extension: str = ".yaml"
    codec: RefCodec = "stem"
    hash_algorithm: str = "sha256"
    fanout: tuple[int, ...] = (2, 2)
    filename_mode: HashScatteredFilenameMode = "digest"
    branch: BranchPlacement = BranchPlacement()

    def __post_init__(self) -> None:
        _require_ref_codec(self.codec)
        if self.filename_mode not in _HASH_SCATTERED_FILENAME_MODES:
            raise ValueError(f"unknown hash-scattered filename_mode: {self.filename_mode}")
        if self.filename_mode == "encoded_ref":
            _require_reversible_ref_codec(self.codec)

    def address_for(self, owner: TOwner, ref: TRef) -> ArtifactAddress:
        encoded = encode_ref_value(_ref_value(ref, self.ref_field), self.codec)
        digest = self._digest(encoded)
        dirs = "/".join(self._fanout_segments(digest))
        filename = digest if self.filename_mode == "digest" else encoded
        rel = f"{self.namespace}/{dirs}/{filename}{self.extension}" if dirs else f"{self.namespace}/{filename}{self.extension}"
        return ArtifactAddress(
            branch=self.branch.branch_name(owner, ref),
            locator=PathArtifactLocator(rel),
        )

    def list_refs(
        self,
        owner: TOwner,
        backend: DocumentStoreBackend | None,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> list[TRef]:
        if self.filename_mode != "encoded_ref":
            raise TypeError("opaque hash-scattered placement requires an index or loaded-document ref recovery")
        if backend is None:
            raise ValueError("listing path-backed artifacts requires a backend")
        target_commit = commit
        if target_commit is None:
            target_commit = backend.branch_sha(branch or self.branch.branch_name(owner))
            if target_commit is None:
                return []
        return self._list_encoded_refs(backend, self.namespace, target_commit)

    def ref_from_locator(self, locator: ArtifactLocator) -> TRef:
        if self.filename_mode != "encoded_ref":
            raise TypeError("opaque hash-scattered placement cannot recover refs from paths")
        if not isinstance(locator, PathArtifactLocator):
            raise TypeError("hash-scattered YAML placement only supports path locators")
        path = _normalize_path(locator.path)
        if not path.startswith(f"{self.namespace}/") or not path.endswith(self.extension):
            raise ValueError(f"expected {self.namespace}/.../*{self.extension}, got {path!r}")
        stem = Path(path).name.removesuffix(self.extension)
        return self.ref_factory(decode_ref_value(stem, self.codec))

    def ref_from_loaded(self, loaded: object) -> TRef:
        value = getattr(loaded, self.ref_field, None)
        if isinstance(value, str) and value:
            return self.ref_factory(value)
        document = getattr(loaded, "document", None)
        if document is not None:
            value = getattr(document, self.ref_field, None)
            if isinstance(value, str) and value:
                return self.ref_factory(value)
        return self.ref_from_locator(PathArtifactLocator(getattr(loaded, "source_path")))

    def contract_body(self) -> dict[str, object]:
        return {
            "kind": "hash-scattered-yaml",
            "namespace": self.namespace,
            "extension": self.extension,
            "ref_field": self.ref_field,
            "codec": self.codec,
            "hash_algorithm": self.hash_algorithm,
            "fanout": self.fanout,
            "filename_mode": self.filename_mode,
            "branch": self.branch.contract_body(),
        }

    def _digest(self, encoded: str) -> str:
        try:
            hasher = hashlib.new(self.hash_algorithm)
        except ValueError as exc:
            raise ValueError(f"unknown hash algorithm: {self.hash_algorithm}") from exc
        hasher.update(encoded.encode("utf-8"))
        return hasher.hexdigest()

    def _fanout_segments(self, digest: str) -> tuple[str, ...]:
        offset = 0
        segments: list[str] = []
        for width in self.fanout:
            if width <= 0:
                raise ValueError("fanout widths must be positive")
            segments.append(digest[offset: offset + width])
            offset += width
        return tuple(segments)

    def _list_encoded_refs(
        self,
        backend: DocumentStoreBackend,
        prefix: str,
        commit: str,
    ) -> list[TRef]:
        refs: list[TRef] = []
        for name, is_dir in backend.list_dir_entries(prefix, commit=commit):
            child = f"{prefix}/{name}"
            if is_dir:
                refs.extend(self._list_encoded_refs(backend, child, commit))
            elif name.endswith(self.extension):
                stem = name.removesuffix(self.extension)
                refs.append(self.ref_factory(decode_ref_value(stem, self.codec)))
        return refs


@dataclass(frozen=True)
class FixedFilePlacement(Generic[TOwner, TRef]):
    filename: str
    branch: BranchPlacement = BranchPlacement()

    def address_for(self, owner: TOwner, ref: TRef) -> ArtifactAddress:
        return ArtifactAddress(
            branch=self.branch.branch_name(owner, ref),
            locator=PathArtifactLocator(self.filename),
        )

    def list_refs(
        self,
        owner: TOwner,
        backend: DocumentStoreBackend | None,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> list[TRef]:
        raise TypeError("fixed-file placement cannot enumerate refs without an external source")

    def ref_from_locator(self, locator: ArtifactLocator) -> TRef:
        raise TypeError("fixed-file placement cannot recover refs from locators")

    def ref_from_loaded(self, loaded: object) -> TRef:
        raise TypeError("fixed-file placement cannot recover refs from loaded documents")

    def contract_body(self) -> dict[str, object]:
        return {
            "kind": "fixed-file",
            "filename": self.filename,
            "branch": self.branch.contract_body(),
        }


@dataclass(frozen=True)
class TemplateFilePlacement(Generic[TOwner, TRef]):
    template: str
    ref_field: str = "self"
    codec: RefCodec = "stem"
    branch: BranchPlacement = BranchPlacement()

    def __post_init__(self) -> None:
        _require_ref_codec(self.codec)

    def address_for(self, owner: TOwner, ref: TRef) -> ArtifactAddress:
        value = encode_ref_value(_ref_value(ref, self.ref_field), self.codec)
        return ArtifactAddress(
            branch=self.branch.branch_name(owner, ref),
            locator=PathArtifactLocator(self.template.format(value=value, stem=value)),
        )

    def list_refs(
        self,
        owner: TOwner,
        backend: DocumentStoreBackend | None,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> list[TRef]:
        raise TypeError("template-file placement cannot enumerate refs without a parser")

    def ref_from_locator(self, locator: ArtifactLocator) -> TRef:
        raise TypeError("template-file placement cannot recover refs from locators")

    def ref_from_loaded(self, loaded: object) -> TRef:
        raise TypeError("template-file placement cannot recover refs from loaded documents")

    def contract_body(self) -> dict[str, object]:
        return {
            "kind": "template-file",
            "template": self.template,
            "ref_field": self.ref_field,
            "codec": self.codec,
            "branch": self.branch.contract_body(),
        }


@dataclass(frozen=True)
class SingletonFilePlacement(Generic[TOwner, TRef]):
    filename: str
    ref_factory: Callable[[], TRef]
    branch: BranchPlacement = BranchPlacement()

    def address_for(self, owner: TOwner, ref: TRef) -> ArtifactAddress:
        return ArtifactAddress(
            branch=self.branch.branch_name(owner, ref),
            locator=PathArtifactLocator(self.filename),
        )

    def list_refs(
        self,
        owner: TOwner,
        backend: DocumentStoreBackend | None,
        *,
        branch: str | None = None,
        commit: str | None = None,
    ) -> list[TRef]:
        return [self.ref_factory()]

    def ref_from_locator(self, locator: ArtifactLocator) -> TRef:
        if not isinstance(locator, PathArtifactLocator):
            raise TypeError("singleton placement only supports path locators")
        if _normalize_path(locator.path) != _normalize_path(self.filename):
            raise ValueError(f"expected {self.filename!r}")
        return self.ref_factory()

    def ref_from_loaded(self, loaded: object) -> TRef:
        return self.ref_factory()

    def contract_body(self) -> dict[str, object]:
        return {
            "kind": "singleton-file",
            "filename": self.filename,
            "branch": self.branch.contract_body(),
        }


@dataclass(frozen=True)
class ArtifactContext(Generic[TOwner, TRef]):
    repo: TOwner
    ref: TRef
    branch: str
    address: ArtifactAddress

    def require_path(self) -> str:
        return self.address.require_path()


@dataclass(frozen=True)
class ArtifactHandle(Generic[TOwner, TRef, TDoc]):
    family: ArtifactFamily[TOwner, TRef, TDoc]
    ref: TRef
    address: ArtifactAddress
    document: TDoc


@dataclass(frozen=True)
class PreparedArtifact(Generic[TOwner, TRef, TDoc]):
    family: ArtifactFamily[TOwner, TRef, TDoc]
    ref: TRef
    address: ArtifactAddress
    branch: str
    document: TDoc
    content: bytes


@dataclass(frozen=True)
class ArtifactFamily(Generic[TOwner, TRef, TDoc]):
    name: str
    contract_version: VersionId
    doc_type: type[TDoc]
    placement: ArtifactPlacementPolicy[TOwner, TRef]
    coerce_payload: Callable[[object, str], TDoc] | None = None
    decode_bytes: Callable[[bytes, str], TDoc] | None = None
    encode_document: Callable[[TDoc], bytes] | None = None
    render_document: Callable[[TDoc], str] | None = None
    document_payload: Callable[[TDoc], object] | None = None
    normalize_for_write: Callable[[ArtifactContext[TOwner, TRef], TDoc, Any], TDoc] | None = None
    validate_for_write: Callable[[ArtifactContext[TOwner, TRef], TDoc, Any], None] | None = None
    scan_type: type[msgspec.Struct] | None = None

    def address_for(self, owner: TOwner, ref: TRef) -> ArtifactAddress:
        return self.placement.address_for(owner, ref)

    def contract_body(self) -> dict[str, object]:
        return {
            "doc_type": f"{self.doc_type.__module__}.{self.doc_type.__qualname__}",
            "placement": self.placement.contract_body(),
        }

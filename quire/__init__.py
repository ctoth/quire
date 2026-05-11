from __future__ import annotations

from quire import documents
from quire.artifacts import (
    ArtifactFamily,
    ArtifactHandle,
    BranchPlacement,
    FixedFilePlacement,
    FlatYamlPlacement,
    HashScatteredYamlPlacement,
    IndexRequiredError,
    SingletonFilePlacement,
    TemplateFilePlacement,
    UnscannablePlacementError,
    encode_ref_value,
)
from quire.contracts import ContractEntry, ContractManifest, ContractManifestError, check_contract_manifest
from quire.family_store import DocumentFamilyStore
from quire.git_store import GitGcReport, GitStore, GitStorePolicy, HeadMismatchError
from quire.refs import RefName, single_field_ref_type, singleton_ref_type
from quire.notes import NotesRef, read_git_note, write_git_note
from quire.references import (
    AmbiguousReferenceError,
    CrossFamilyReferenceIndex,
    ForeignKeySpec,
    ForeignKeyValidationError,
    ReferenceIndex,
    ReferenceResolution,
    build_reference_lookup,
    extend_reference_lookup,
    finalize_reference_lookup,
    validate_foreign_key,
)
from quire.tree_path import FilesystemTreePath, GitTreePath, TreePath, coerce_tree_path
from quire.versions import VersionId
from quire.families import (
    BoundFamily,
    BoundFamilyRegistry,
    BoundFamilyTransaction,
    FamilyDefinition,
    FamilyIdentityPolicy,
    FamilyRegistry,
    TransactionalBoundFamily,
)
from quire.hashing import canonical_json_bytes, canonical_json_sha256

__all__ = [
    "AmbiguousReferenceError",
    "ArtifactFamily",
    "ArtifactHandle",
    "BranchPlacement",
    "BoundFamily",
    "BoundFamilyRegistry",
    "BoundFamilyTransaction",
    "ContractEntry",
    "ContractManifest",
    "ContractManifestError",
    "CrossFamilyReferenceIndex",
    "DocumentFamilyStore",
    "FamilyDefinition",
    "FamilyIdentityPolicy",
    "FamilyRegistry",
    "FilesystemTreePath",
    "FixedFilePlacement",
    "FlatYamlPlacement",
    "ForeignKeySpec",
    "ForeignKeyValidationError",
    "GitGcReport",
    "GitStore",
    "GitStorePolicy",
    "GitTreePath",
    "HashScatteredYamlPlacement",
    "HeadMismatchError",
    "IndexRequiredError",
    "NotesRef",
    "RefName",
    "ReferenceIndex",
    "ReferenceResolution",
    "SingletonFilePlacement",
    "TemplateFilePlacement",
    "TransactionalBoundFamily",
    "TreePath",
    "UnscannablePlacementError",
    "VersionId",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "check_contract_manifest",
    "coerce_tree_path",
    "documents",
    "build_reference_lookup",
    "encode_ref_value",
    "extend_reference_lookup",
    "finalize_reference_lookup",
    "read_git_note",
    "single_field_ref_type",
    "singleton_ref_type",
    "validate_foreign_key",
    "write_git_note",
]

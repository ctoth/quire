from __future__ import annotations

from quire import documents
from quire.artifacts import (
    ArtifactFamily,
    BranchPlacement,
    FixedFilePlacement,
    FlatYamlPlacement,
    HashScatteredYamlPlacement,
    IndexRequiredError,
    SingletonFilePlacement,
    TemplateFilePlacement,
    UnscannablePlacementError,
)
from quire.contracts import ContractManifest, ContractManifestError, check_contract_manifest
from quire.git_store import GitGcReport, GitStore, GitStorePolicy
from quire.refs import RefName, single_field_ref_type, singleton_ref_type
from quire.notes import NotesRef
from quire.references import (
    AmbiguousReferenceError,
    CrossFamilyReferenceIndex,
    ForeignKeySpec,
    ForeignKeyValidationError,
    ReferenceIndex,
    ReferenceResolution,
    validate_foreign_key,
)
from quire.tree_path import FilesystemTreePath, GitTreePath, TreePath
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
    "BranchPlacement",
    "BoundFamily",
    "BoundFamilyRegistry",
    "BoundFamilyTransaction",
    "ContractManifest",
    "ContractManifestError",
    "CrossFamilyReferenceIndex",
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
    "documents",
    "single_field_ref_type",
    "singleton_ref_type",
    "validate_foreign_key",
]

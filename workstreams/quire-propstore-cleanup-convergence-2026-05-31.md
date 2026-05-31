# Quire Propstore Cleanup Convergence Workstream - 2026-05-31

Status: ready for execution
Owner boundary: Quire owns generic Git, typed document, family, reference,
schema, and derived-store mechanics. Propstore owns domain families, semantic
policy, command workflows, embedding model identity, and app-facing materialized
views.

## Requested Outcome

Fix the current Quire cleanup/refactor issues against how Quire is actually used
by `../propstore`, using current source review as the authority. Old review
files are navigation aids only. Completion requires deletion of the old Quire
production surfaces, Propstore caller updates where the surface is consumed,
zero-hit old-path production search gates, and passing runtime gates.

## Current Evidence

Quire branch at planning time: `declarative-charter-shape`.

Propstore branch at planning time: `master`.

Current-source review covered these Quire production modules:

- `quire/canonical.py`
- `quire/hashing.py`
- `quire/versions.py`
- `quire/contracts.py`
- `quire/artifacts.py`
- `quire/families.py`
- `quire/family_store.py`
- `quire/git_store.py`
- `quire/tree_path.py`
- `quire/documents/schema.py`
- `quire/documents/batch.py`
- `quire/references.py`
- `quire/sqlite_vec_store.py`
- `quire/schema_ir.py`
- `quire/type_grammar.py`
- `quire/sqlalchemy_store.py`
- `quire/sqlalchemy_schema.py`
- `quire/derived_store.py`
- `quire/derived_runtime.py`
- `quire/projections.py`
- `quire/lifecycle.py`
- `quire/refs.py`
- `quire/notes.py`

Propstore usage review covered the consumer owners for the affected Quire
surfaces:

- `../propstore/propstore/repository.py`
- `../propstore/propstore/families/registry.py`
- `../propstore/propstore/app/repository_history.py`
- `../propstore/propstore/merge/merge_commit.py`
- `../propstore/propstore/support_revision/projection.py`
- `../propstore/propstore/families/concepts/stages.py`
- `../propstore/propstore/families/contexts/__init__.py`
- `../propstore/propstore/families/embeddings/declaration.py`
- `../propstore/propstore/families/claims/sidecar_runtime.py`
- `../propstore/propstore/families/concepts/sidecar_runtime.py`
- `../propstore/propstore/world/model.py`

Stale review finding rejected by current evidence:

- `projection_mapping.py`, `ProjectionField`, and `ProjectionTable` are not
  present in current Quire production or tests.

## Target Architecture

- Canonical payload normalization and hashing have one Quire owner:
  `quire.canonical`.
- `VersionId` is an opaque value object. Contract policy validation lives in
  contract/family declaration code, not in `VersionId` parsing or ordering.
- Unbounded Git, family, document, reference, and sqlite-vec scans expose
  iterator-first APIs. Callers materialize explicitly at app/report boundaries.
- Git tree artifact classification uses loaded object type, not raw mode checks.
- Registry-bound family writes are the single high-level family mutation surface
  for FK validation. Lower-level document-family storage remains generic
  storage mechanics only.
- Reference resolution exposes one canonical target-family vocabulary.
- Propstore keeps semantic policy, embedding model identity, and UI/result list
  materialization outside Quire.

## Forbidden Surfaces

These production surfaces must not survive under a wrapper, alias, fallback,
renamed helper, re-export-only module, or dual-path compatibility spelling:

- `quire.hashing`
- `quire.hashing.canonical_json_bytes`
- `quire.hashing.canonical_json_sha256`
- `VersionId.__lt__`
- calendric parsing inside `VersionId`
- `GitStore.log`
- `GitStore.flat_tree_entries`
- `GitStore.commit_parent_shas`
- raw mode-based artifact classification in Git tree APIs
- `BoundFamily.iter`
- `PinnedBoundFamily.iter`
- `DocumentFamilyStore.iter`
- `load_document_dir`
- `load_document_batch_dir`
- `SqlAlchemyVecRegistry.get_registered_models`
- `SqlAlchemyVecEntityStore.similar_entities`
- `ReferenceResolution.target_kind`
- `FamilyReferenceIndex.ids`
- reflective `ReferenceKey.format` mapping over arbitrary object attributes

## Global Search Gates

Run from `C:\Users\Q\code\quire` after each relevant slice:

```powershell
rg -n -F "from quire.hashing" quire tests
rg -n -F "quire.hashing" quire tests
rg -n -F "def __lt__" quire/versions.py
rg -n -F "_parse_calendar_version" quire tests
rg -n -F "def log(" quire/git_store.py
rg -n -F ".log(" tests
rg -n -F "def flat_tree_entries" quire/git_store.py
rg -n -F "flat_tree_entries" quire tests
rg -n -F "def commit_parent_shas" quire/git_store.py
rg -n -F "commit_parent_shas" quire tests
rg -n -F "def iter(" quire/families.py quire/family_store.py
rg -n -F "load_document_dir" quire tests
rg -n -F "load_document_batch_dir" quire tests
rg -n -F "get_registered_models" quire tests
rg -n -F "similar_entities" quire tests
rg -n -F "target_kind" quire tests
rg -n -F "def ids(" quire/references.py
```

Run from `C:\Users\Q\code\propstore` for cross-repo slices:

```powershell
rg -n -F "from quire.hashing" propstore tests
rg -n -F "quire.hashing" propstore tests
rg -n -F ".log(" propstore tests
rg -n -F "flat_tree_entries" propstore tests
rg -n -F "commit_parent_shas" propstore tests
rg -n "\.families\.[A-Za-z0-9_]+\.iter\(" propstore tests
rg -n -F "load_document_dir" propstore tests
rg -n -F "load_document_batch_dir" propstore tests
rg -n -F "get_registered_models" propstore tests
rg -n -F "similar_entities" propstore tests
rg -n -F ".target_kind" propstore tests
```

Each nonzero production hit is an active work item for the current slice.
Test-only hits are retained only when they assert absence or exercise the new
surface.

## Global Runtime Gates

Quire focused gates:

```powershell
uv run pytest tests/test_hashing.py tests/test_contracts.py tests/test_versions.py
uv run pytest tests/test_git_store.py tests/test_git_properties.py tests/test_laziness.py
uv run pytest tests/test_documents.py tests/test_document_batches.py tests/test_family_store.py tests/test_families.py
uv run pytest tests/test_references.py tests/test_sqlalchemy_engine.py
uv run pytest
```

Propstore focused gates after cross-repo API cuts:

```powershell
uv run pytest tests/test_git_backend.py tests/test_repo_branch.py tests/test_merge_classifier.py tests/test_worldline_revision_merge_parent_evidence.py
uv run pytest tests/test_artifact_store.py tests/test_semantic_family_registry.py tests/test_source_promotion_alignment.py
uv run pytest tests/test_document_schema.py tests/test_world_query.py
uv run pytest
```

Metric gate:

- No performance metric gate is active for this workstream.

## Execution Rules

- Before each implementation slice, verify branch and path-limited dirty state
  in Quire and Propstore.
- Delete the old Quire production surface first.
- Use failures and search hits as the caller work queue.
- Update Propstore in the same slice when Propstore consumes the deleted Quire
  surface.
- Do not add compatibility aliases, fallback readers, dual APIs, wrappers, or
  renamed copies of deleted surfaces.
- Commit each kept slice atomically with path-limited `git add` and
  `git commit -m "..." -- <explicit paths>`.
- After each focused or full-suite gate, reread this workstream and continue
  with the next unchecked phase.

## Phase 1 - Canonical Hashing Owner

Final state:

- `canonical_json_bytes` and `canonical_json_sha256` live in `quire.canonical`.
- Top-level `quire` exports continue to expose those functions from the canonical
  owner.
- `quire/hashing.py` is deleted.

Deletion targets:

- `quire/hashing.py`
- Quire imports from `quire.hashing`
- Quire tests importing `quire.hashing`

Known current Quire callers:

- `quire/derived_store.py`
- `quire/projections.py`
- `quire/schema_catalog.py`
- `quire/__init__.py`
- `tests/test_hashing.py`

Known Propstore status:

- Propstore imports canonical hashing from top-level `quire`, not
  `quire.hashing`.

Search gates:

```powershell
rg -n -F "quire.hashing" quire tests
rg -n -F "from quire.hashing" quire tests
rg -n -F "canonical_json_sha256" quire/canonical.py quire/__init__.py tests/test_hashing.py
```

Runtime gates:

```powershell
uv run pytest tests/test_hashing.py tests/test_contracts.py
```

## Phase 2 - Opaque VersionId

Final state:

- `VersionId` validates only that the value is a usable opaque version token.
- Contract/family declaration code owns any non-placeholder policy checks.
- Version ordering is absent.

Deletion targets:

- `VersionId.__lt__`
- `_parse_calendar_version`
- tests asserting calendar date parsing or ordering through `VersionId`

Known current Quire surfaces:

- `quire/versions.py`
- `quire/contracts.py`
- `quire/families.py`
- `quire/charter_class.py`

Known Propstore status:

- Propstore constructs `VersionId(..., allow_placeholder=False)` in family and
  contract declarations.
- Current Propstore search found no ordering dependency.

Search gates:

```powershell
rg -n -F "def __lt__" quire/versions.py
rg -n -F "_parse_calendar_version" quire tests
rg -n -F "allow_placeholder=False" quire tests ../propstore/propstore ../propstore/tests
```

Runtime gates:

```powershell
uv run pytest tests/test_versions.py tests/test_contracts.py tests/test_families.py tests/test_charter_class_features.py
```

## Phase 3 - Iterator-First Git History And Tree APIs

Final state:

- `GitStore.iter_log_entries(...)` yields log entries.
- `GitStore.iter_flat_tree_entries(...)` yields path/object-id entries.
- `GitStore.iter_commit_parent_shas(...)` yields parent SHAs.
- Quire and Propstore callers explicitly materialize with `list`, `dict`, or
  `tuple`.

Deletion targets:

- `GitStore.log`
- `GitStore.flat_tree_entries`
- `GitStore.commit_parent_shas`

Known Propstore production callers:

- `propstore/app/repository_history.py`
- `propstore/app/merge.py`
- `propstore/merge/merge_commit.py`
- `propstore/support_revision/projection.py`

Known Propstore test callers:

- `tests/test_git_backend.py`
- `tests/test_repo_branch.py`
- `tests/test_merge_classifier.py`
- `tests/test_worldline_revision_merge_parent_evidence.py`
- `tests/test_cli.py`
- `tests/test_init.py`
- `tests/test_import_repo.py`
- `tests/test_project_init.py`
- `tests/test_quire_consumer_contracts.py`

Search gates:

```powershell
rg -n -F "def log(" quire/git_store.py
rg -n -F ".log(" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "flat_tree_entries" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "commit_parent_shas" quire tests ../propstore/propstore ../propstore/tests
```

Runtime gates:

```powershell
uv run pytest tests/test_git_store.py tests/test_git_properties.py tests/test_laziness.py
cd ..\propstore
uv run pytest tests/test_git_backend.py tests/test_repo_branch.py tests/test_merge_classifier.py tests/test_worldline_revision_merge_parent_evidence.py
```

## Phase 4 - Loaded Git Tree Entry Classification

Final state:

- One Quire internal classifier loads the Git object and classifies entries as
  file or directory from object type.
- Submodule commit entries are not document artifacts.
- `GitTreePath.is_file`, `GitTreePath.is_dir`, `GitStore.iter_dir_entries`,
  artifact scans, and existence checks agree.

Deletion targets:

- raw `mode & 0o040000` and `mode & 0o100000` classification in production
  tree/document APIs
- tuple-shaped existence results that expose raw mode as caller semantics

Known Quire surfaces:

- `quire/git_store.py`
- `quire/tree_path.py`
- `quire/artifacts.py`
- `quire/family_store.py`

Search gates:

```powershell
rg -n "0o040000|0o100000" quire
rg -n -F "tuple[int, str]" quire
rg -n -F "exists(" quire/artifacts.py quire/family_store.py quire/git_store.py quire/tree_path.py
```

Runtime gates:

```powershell
uv run pytest tests/test_git_store.py tests/test_git_properties.py tests/test_artifacts.py tests/test_family_store.py
```

## Phase 5 - Family Scan Naming And Registry-Bound Mutation Ownership

Final state:

- Family reference scans use `iter_refs`.
- Document materializing scans use `iter_handles`.
- Registry-bound family APIs remain the high-level mutation path that performs
  FK validation.
- Low-level `DocumentFamilyStore` contains generic storage mechanics and no
  duplicated high-level registry policy.

Deletion targets:

- `BoundFamily.iter`
- `PinnedBoundFamily.iter`
- `DocumentFamilyStore.iter`
- duplicated high-level save/delete/move commit logic in bound family wrappers

Known Propstore production callers:

- `propstore/app/repository_history.py`
- `propstore/app/worldlines.py`
- `propstore/grounding/inspection.py`
- `propstore/compiler/workflows.py`
- `propstore/families/predicates/lifecycle.py`
- `propstore/families/stances/lifecycle.py`
- `propstore/families/rules/lifecycle.py`
- `propstore/source/concepts.py`

Search gates:

```powershell
rg -n -F "def iter(" quire/families.py quire/family_store.py
rg -n "\.families\.[A-Za-z0-9_]+\.iter\(" ../propstore/propstore ../propstore/tests
rg -n "\.iter\(" quire tests
```

Runtime gates:

```powershell
uv run pytest tests/test_family_store.py tests/test_families.py tests/test_laziness.py tests/test_references.py
cd ..\propstore
uv run pytest tests/test_artifact_store.py tests/test_semantic_family_registry.py tests/test_source_promotion_alignment.py
```

## Phase 6 - Iterator-First Document Directory Loading

Final state:

- `iter_document_dir` yields loaded documents.
- `iter_document_batch_dir` yields batch-loaded documents.
- Propstore loaders materialize at domain/app boundaries.

Deletion targets:

- `load_document_dir`
- `load_document_batch_dir`

Known Propstore production callers:

- `propstore/families/contexts/__init__.py`
- `propstore/families/concepts/stages.py`

Search gates:

```powershell
rg -n -F "load_document_dir" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "load_document_batch_dir" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "iter_document_dir" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "iter_document_batch_dir" quire tests ../propstore/propstore ../propstore/tests
```

Runtime gates:

```powershell
uv run pytest tests/test_documents.py tests/test_document_batches.py
cd ..\propstore
uv run pytest tests/test_document_schema.py tests/test_validator.py tests/test_world_query.py
```

## Phase 7 - Iterator-First sqlite-vec Store APIs

Final state:

- `SqlAlchemyVecRegistry.iter_registered_models` yields model rows.
- `SqlAlchemyVecEntityStore.iter_similar_entities` yields similarity rows.
- Propstore embedding APIs choose materialized list results at the Propstore
  boundary.

Deletion targets:

- `SqlAlchemyVecRegistry.get_registered_models`
- `SqlAlchemyVecEntityStore.similar_entities`

Known Propstore production callers:

- `propstore/families/embeddings/declaration.py`
- `propstore/families/claims/sidecar_runtime.py`
- `propstore/families/concepts/sidecar_runtime.py`
- `propstore/world/model.py`

Search gates:

```powershell
rg -n -F "get_registered_models" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "similar_entities" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "iter_registered_models" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "iter_similar_entities" quire tests ../propstore/propstore ../propstore/tests
```

Runtime gates:

```powershell
uv run pytest tests/test_sqlalchemy_engine.py
cd ..\propstore
uv run pytest tests/test_world_query.py tests/test_relate_perspective_isolation.py
```

## Phase 8 - Reference Surface Consolidation

Final state:

- `ReferenceResolution.target_family` is the only target-family property.
- `FamilyReferenceIndex.iter_ids` is iterator-first.
- `ReferenceKey.format` accepts explicit mapping-like payloads from declared
  reference-key sources, not arbitrary reflected object attributes.

Deletion targets:

- `ReferenceResolution.target_kind`
- `FamilyReferenceIndex.ids`
- reflective `_format_mapping` over arbitrary object attributes

Known Propstore status:

- Current search found no Propstore use of Quire
  `ReferenceResolution.target_kind`.
- Propstore uses `ReferenceKey.format` in concept, claim, and source reference
  declarations.

Search gates:

```powershell
rg -n -F ".target_kind" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "def ids(" quire/references.py
rg -n -F ".ids()" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "_format_mapping" quire/references.py tests
```

Runtime gates:

```powershell
uv run pytest tests/test_references.py tests/test_families.py
cd ..\propstore
uv run pytest tests/test_source_promotion_alignment.py tests/test_world_query.py
```

## Final Gates

Run from Quire:

```powershell
uv run pytest
```

Run from Propstore after Propstore caller edits:

```powershell
cd ..\propstore
uv run pytest
```

Final search gates:

```powershell
cd C:\Users\Q\code\quire
rg -n -F "quire.hashing" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "flat_tree_entries" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "commit_parent_shas" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "load_document_dir" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "load_document_batch_dir" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "get_registered_models" quire tests ../propstore/propstore ../propstore/tests
rg -n -F "similar_entities" quire tests ../propstore/propstore ../propstore/tests
rg -n -F ".target_kind" quire tests ../propstore/propstore ../propstore/tests
```

Completion evidence:

- Every phase above has a recorded commit.
- Every forbidden production surface is absent or recorded as a non-production
  assertion of absence.
- Quire full suite passes.
- Propstore focused gates pass after each cross-repo cut.
- Propstore full suite passes after all cross-repo cuts.

## Iteration Log

### Iteration 0 - Workstream Creation

Slice read:

- Current Quire production code listed in `Current Evidence`
- Current Propstore consumer owners listed in `Current Evidence`

Surfaces:

- Cleanup/refactor plan artifact
  - Disposition: keep
  - Owner after cleanup: `workstreams/quire-propstore-cleanup-convergence-2026-05-31.md`
  - Action: created executable fixed-point workstream from current-source review
  - Evidence: branch/status checks and current-source searches performed before
    file creation

Gate results:

- Not run: implementation gates start at Phase 1

Commit:

- Creation commit for this workstream file

Next slice:

- Phase 1 - Canonical Hashing Owner

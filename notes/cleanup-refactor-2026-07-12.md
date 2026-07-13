# Cleanup Refactor Fixed-Point Log - 2026-07-12

Target architecture:
- Quire's Git/document substrate preserves atomic typed storage invariants.
- Each derived SQL schema owns its mapper registry and mapped classes.
- Every advertised family write capability has matching CAS and validation semantics.
- Registry validation and publication operate against one exact branch head.
- SQL and derived-store capabilities have an explicit package and dependency boundary.
- Project documentation, metadata, CI, and release records describe the shipped surface.

Forbidden surfaces:
- Process-global SQLAlchemy mapper resets.
- Family methods that accept but ignore `expected_head`.
- Registry-backed writes that bypass declared foreign-key validation.
- Validation scans whose source head is not the head used for publication.
- Compatibility wrappers, aliases, fallback paths, or dual old/new APIs.
- Propstore semantics or stale Propstore documentation in Quire.

Search gates:
- `rg -n "clear_mappers|mapper-reset caveat|Single live schema" quire tests README.md docs ../propstore/propstore ../propstore/tests`
- `rg -n "RefBlobLocator|write_blob_ref|delete_ref|expected_head" quire tests`
- `rg -n "_validate_registry_post_state|branch_sha|expected_head" quire tests`
- `rg -n "propstore|pks " quire docs README.md`

Runtime gates:
- `uv run pytest`
- `uv run pyright quire`
- `uv run ruff check quire tests`
- Propstore focused tests through `scripts/run_logged_pytest.ps1` when a slice reaches it.
- `uv run pyright propstore` after Propstore integration changes.

## Iteration 1 - `SQLAlchemy mapper ownership`

Slice read:
- `quire/sqlalchemy_schema.py`
- `tests/test_sqlalchemy_engine.py`
- `propstore/derived_schema.py`
- Propstore callers of `SqlAlchemySchema.model`

Surfaces:
- `build_sqlalchemy_schema -> clear_mappers`
  - Disposition: delete
  - Owner after cleanup: no owner; process-global mapper mutation is forbidden.
  - Action: map schema-local subclasses in the schema's own registry.
  - Evidence: Propstore queries obtain mapped classes from `schema.model`; authored classes remain behavior bases.
- Authored model classes
  - Disposition: keep as unmapped domain behavior owners.
  - Owner after cleanup: family/charter declarations.
  - Action: schema-local mapped classes inherit from them.

Gate results:
- Pass: `rg -n "clear_mappers" quire tests README.md docs` returned no hits.
- Pass: `uv run pytest tests/test_sqlalchemy_engine.py tests/test_charter_codegen_json_blob.py tests/test_charter_codegen_nullable.py tests/test_charter_class_features.py -q` -> `36 passed`.
- Pass: `uv run pytest -q` -> `490 passed, 12 deselected`.
- Pass: `uv run pyright quire` -> `0 errors`.
- Pass: `uv run ruff check quire tests`.
- Pass: focused Propstore integration with local Quire -> `42 passed`; log `logs/test-runs/quire-mapper-ownership-20260712-214912.log`.

Commit:
- `Remove global SQLAlchemy mapper resets` (this iteration's commit).

Next slice:
- Ref-backed write capability convergence.

## Iteration 2 - `Ref-backed write capability convergence`

Slice read:
- `quire/artifacts.py` (`BlobRefPlacement`)
- `quire/git_store.py` ref and blob-ref primitives
- `quire/family_store.py` backend contract and direct family writes
- `quire/families.py` registry-bound writes and validation
- `tests/test_ref_backed_family.py`
- Propstore blob-ref and `expected_head` callers

Surfaces:
- `GitStore.write_blob_ref` unconditional publication
  - Disposition: rewrite
  - Owner after cleanup: `GitStore` generic ref CAS mechanics.
  - Action: capture the current ref, honor an explicit expectation, and CAS-publish the new blob.
- `GitStore.delete_ref` unconditional deletion
  - Disposition: rewrite
  - Owner after cleanup: `GitStore` generic ref CAS mechanics.
  - Action: honor an explicit expectation and CAS-delete the captured ref.
- Registry-bound `RefBlobLocator` early returns
  - Disposition: delete
  - Owner after cleanup: registry post-state validation followed by the locator-specific writer.
  - Action: validate declared FKs before ref publication just as path-backed writes do.
- A second ref-specific family expectation parameter
  - Disposition: delete from the design; it does not exist and will not be introduced.
  - Evidence: the existing explicit expectation is an object ID in both branch and blob-ref storage; backend dispatch owns the locator-specific CAS.

Search gates:
- No `RefBlobLocator` write return may precede registry validation.
- No `write_blob_ref` or `delete_ref` publication may use unconditional `_ref_set`/`_ref_delete`.

Gate results:
- Pass: new red tests demonstrated that ref-backed `save` and `delete` ignored stale expectations and registry-bound ref writes bypassed FK validation.
- Pass: `uv run pytest tests/test_ref_backed_family.py -q` -> `19 passed` after the implementation.
- Pass: `uv run pytest tests/test_families.py tests/test_family_store.py tests/test_git_store.py tests/test_ref_backed_family.py -q` -> `127 passed`.
- Pass: `uv run pyright quire` -> `0 errors`.
- Pass: `uv run ruff check quire tests/test_ref_backed_family.py`.
- Pass: `git diff --check`.
- Pass: ref publication search shows all family-level `write_blob_ref` and `delete_ref` calls forward the explicit expectation.

Commit:
- `Make ref-backed family writes honest` (this iteration's commit).

Next slice:
- Exact-head binding for registry validation and branch publication.

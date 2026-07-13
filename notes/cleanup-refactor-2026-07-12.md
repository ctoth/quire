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

## Iteration 3 - `Exact-head registry validation`

Slice read:
- `quire/families.py` bound single-write and transaction validation
- `quire/family_store.py` branch-head resolution and transaction publication
- `quire/git_store.py` commit CAS behavior
- `tests/test_families.py` expected-head and FK validation coverage

Surfaces:
- Branch-relative registry scans before publication
  - Disposition: rewrite
  - Owner after cleanup: `_validate_registry_post_state` reads an explicit commit snapshot.
  - Action: capture the branch head once, validate every relevant family at that commit, and publish with the captured SHA as `expected_head`.
- Caller-supplied stale expectation
  - Disposition: keep as the controlling snapshot.
  - Owner after cleanup: the caller's explicit `expected_head` remains authoritative and the final Git CAS rejects staleness.
- Separate validation and publication heads
  - Disposition: delete from the execution path.

Search gates:
- Every branch-backed `_validate_registry_post_state` call supplies the commit used as the publication expectation.
- Registry validation scans use `commit=...`, not a fresh branch-tip resolution per family.

Gate results:
- Pass: red tests showed validation scans resolving `commit=None` and a branch advance after validation being accepted.
- Pass: validation now reads every relevant family at one captured commit and publication uses that commit as `expected_head`.
- Pass: injected branch advances after validation are rejected for both single writes and registry transactions.
- Pass: `uv run pytest tests/test_families.py tests/test_family_store.py tests/test_git_store.py tests/test_ref_backed_family.py -q` -> `130 passed`.
- Pass: `uv run pyright quire` -> `0 errors`.
- Pass: `uv run ruff check quire tests/test_families.py tests/test_ref_backed_family.py`.

Commit:
- `Bind registry validation to publication head` (this iteration's commit).

Next slice:
- Quire core versus SQL/derived capability boundary convergence.

## Iteration 4 - `Explicit SQL and vector capability boundary`

Slice read:
- `pyproject.toml` and `uv.lock`
- `quire/__init__.py`
- SQL, vector, derived-store, and projection module imports
- Quire and Propstore import sites

Surfaces:
- SQLAlchemy, SQLAlchemy FTS5, and sqlite-vec as unconditional Quire dependencies
  - Disposition: move out of core.
  - Owner after cleanup: explicit `sql` and `vector` installation extras; the dev group installs the complete test capability set.
- SQL and vector implementation objects re-exported from `quire.__init__`
  - Disposition: delete.
  - Owner after cleanup: `quire.sqlalchemy_schema`, `quire.sqlalchemy_store`, and `quire.sqlite_vec_store` are explicit capability modules.
- Generic derived-store mechanics
  - Disposition: keep in Quire core.
  - Evidence: the manager/runtime own generic cache, SQLite-file, and schema-metadata mechanics and use late SQL imports only when SQL sessions are requested.

Search gates:
- `quire/__init__.py` imports no SQLAlchemy or sqlite-vec capability module.
- Core project dependencies contain no SQLAlchemy or sqlite-vec distribution.
- Propstore uses explicit Quire capability modules and declares the dependencies it consumes.

Gate results:
- Pass: boundary tests failed before the change because no `sql` extra existed and importing `quire` loaded SQLAlchemy.
- Pass: `uv run pytest tests/test_package_boundary.py -q` -> `2 passed`.
- Pass: `uv run pytest tests/test_charters_schema_ir.py tests/test_sqlalchemy_engine.py tests/test_derived_store.py -q` -> `36 passed`.
- Pass: `uv run pyright quire tests/test_package_boundary.py` -> `0 errors`.
- Pass: `uv run ruff check quire tests/test_package_boundary.py`.
- Pass: `uv run pytest -q` -> `498 passed, 12 deselected`.
- Propstore dependency integration pending immediately after this Quire commit.

Commit:
- `Make SQL and vector capabilities explicit` (this iteration's Quire commit).

Next slice:
- Documentation, metadata, CI, and changelog convergence.

## Iteration 5 - `Release and maintenance surface convergence`

Slice read:
- `README.md`
- `docs/git-backend.md`
- `CHANGELOG.md`
- `pyproject.toml` and `uv.lock`
- repository CI configuration

Surfaces:
- README installation and example commands
  - Disposition: rewrite stale calls and document core, SQL, and vector installs.
  - Owner after cleanup: README is the package entry point; explicit capability modules own SQL/vector imports.
- Propstore-specific `docs/git-backend.md` inside Quire
  - Disposition: delete.
  - Evidence: it names removed Propstore files and eager `list_*` APIs, contradicting both Quire's storage boundary and iterator-first API.
- CI configuration
  - Disposition: add the package's actual locked install, lint, type, test, and build gates.
- Release history
  - Disposition: add an Unreleased record for the shipped behavior changes.

Search gates:
- User-facing docs contain no `VersionId(..., allow_placeholder=...)`, `bound.*.iter()`, stale `list_*` storage APIs, or Propstore ownership claims.
- CI runs the ordinary non-benchmark test suite and a package build from the lockfile.
- README names explicit SQL/vector installation and module boundaries.

Gate results:
- Pass: stale user-facing `allow_placeholder`, `.iter()`, `list_dir`, `list_yaml`, `list_branches`, and Propstore backend ownership searches return no hits.
- Pass: README documents core, `sql`, and `vector` installs plus their owning modules.
- Pass: `uv run pytest tests/test_package_boundary.py tests/test_ref_backed_family.py tests/test_families.py -q` -> `61 passed`.
- Pass: `uv run ruff check quire tests`.
- Pass: `uv run pyright quire` -> `0 errors` with the locked Pyright dependency.
- Pass: `uv build` produced the source distribution and wheel.
- Pass: `uv lock --check`.
- Fixed-point correction: removed remaining Propstore names, command names, and domain examples from Quire implementation commentary; the application-semantic search now returns zero hits.

Commit:
- `Align release and maintenance surfaces` (this iteration's commit).

Next slice:
- Final Quire and Propstore acceptance gates plus fixed-point searches.

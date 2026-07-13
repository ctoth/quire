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

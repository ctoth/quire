# Changelog

## Unreleased

- Reorganized the README around reader entry points and added generic guides for architecture, document families, charters, and derived schemas.
- Split performance benchmarks from the ordinary test suite while retaining an explicit benchmark command.
- Replaced process-global SQLAlchemy mapper resets with schema-local mapped classes and registries.
- Added compare-and-swap publication and deletion for ref-backed family artifacts.
- Applied registry foreign-key validation to ref-backed writes instead of bypassing it.
- Pinned registry validation scans and branch publication to the same captured commit.
- Split SQLAlchemy and sqlite-vec support into explicit `sql` and `vector` installation extras and capability modules.
- Removed stale Propstore-specific backend documentation from the generic Quire package.
- Added locked lint, typecheck, test, and package-build CI gates.

## 0.2.0 - 2026-04-27

- Unified canonical JSON hashing with contract payload normalization and rejected non-JSON float values.
- Clarified advisory transaction head checks and tightened stale-head write hygiene.
- Added filesystem-level mutation locking for on-disk repositories.
- Added dry-run unreachable object reporting with `GitStore.gc`.
- Enforced strict contract-version slots for family registries and definitions.
- Added ambiguity-signaling reference resolution and foreign-key validation helpers.
- Added explicit placement scan/index-required errors.
- Switched `merge_base` to Dulwich's native merge-base implementation.
- Promoted the propstore-used quire symbols into the public package surface.

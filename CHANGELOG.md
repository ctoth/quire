# Changelog

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

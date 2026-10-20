# Commit-qualified family instance transport

## Goal
Provide the generic typed FK transport required by Propstore #303 in a separately scoped Quire draft. No Propstore semantics, default push, merge or shared environment mutation. Existing #320 is merged and remains unchanged.

## State
- Read global and Quire AGENTS.md/CLAUDE.md, existing family/reference/charter/validation APIs and the earlier source-backed literature report. Isolated branch `feat/family-occurrence-transport-303`, base `d1daeed8`.
- Parent authorizes working graph transport, separately reviewed generic owner support, focused and integrated verification before publication.
- Implemented source-commit-qualified maps, canonical image witnesses, atomic nested FK rewriting, family-owned reidentity registration and the shared final-instance validator. Existing transaction admission delegates to that same validator.
- Edited-file verification: 12 passed; `logs/test-runs/family-transport-identity-20261019-204427.log`. Integrated consumer verification: 18 passed in the separate Propstore worktree; `logs/test-runs/merge-graph-transport-bundles-20261019-204724.log`. The consumer exercises actual Git authoring/merge/readback and final registry FK validation with this source on PYTHONPATH and the existing exact-pin environment, without installing anything.

## Decisions and why
- Smallest generic addition: a commit-qualified `FamilyInstanceMap` that owns single-valued occurrence images, applies charter-owned FK paths atomically, invokes a typed family-owned reidentity callback only when references change, and exposes canonical image witnesses for provenance. Approximately 150–200 production lines plus focused tests. No Cartesian expansion or identity derivation belongs here.
- Extend existing `FamilyDefinition`/declaration and declarative charter with the callback; keep path parsing in the existing references owner, shared by reader and atomic writer. Do not recreate per-family field maps in Propstore.
- Active cleanup-refactor control surface. Reference path parsing is an IO/metadata capability already owned by references.py; consolidate its parser rather than adding a second grammar. Families/charters are canonical declarations (keep/extend typed metadata). Existing validators remain authoritative; transport must validate source references and must not bypass final validation.
- Support real typed containers through their own replacement operation. Apply all changed sibling fields in one replacement so membership/principal invariants never see a half-written document.

## Surprises
- Initial worktree command omitted `git -C` from a projectless cwd and failed before mutation. Corrected explicit repository targets created both isolated worktrees successfully.
- Initial fixture version `test.v1` was invalid under the existing contract parser; corrected to date-shaped contract versions. All 12 tests now pass.

## Next step
Commit/publish this separately scoped draft and read its CI, then consume its exact reachable commit in the Propstore draft. Do not merge or mark ready. Review any CI failures against their actual evidence; never modify another worker's branch or shared environment.

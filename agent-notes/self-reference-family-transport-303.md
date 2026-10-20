# Self-reference family batch transport (#303 consumer)

## Goal
Extend existing occurrence transport with an explicit finite batch operation so self-reference targets' family-owned identities are known before rewriting references. No consumer semantics or identity invented in Quire.

## State
- Isolated branch `feat/self-reference-family-transport-303`, base/default `03f7173cd2cc0eb72cd595498ac762f92708bc8c`. Main checkout dirty files preserved; read global and project AGENTS/CLAUDE, existing transport note, family_transport.py, reference metadata/rewriter, charter callback and tests.
- Consumer source proof: Propstore coreference IDs hash supports only; attacks are a separate relation updated without changing IDs. Missing self-FK metadata and sequential row transport prevent complete attack transport. Dung 1995 Definition 2 p.326 models attacks as a binary relation over abstract arguments; Schultz/Wisnesky 2015 p.11 requires the image map commute with reference operations. Primary page images read, no extracted text.
- Implemented 56 added generic lines plus owner tests, still uncommitted/unpublished. Focused new-file run PASSED: **17 passed in 1.06s**, `logs/test-runs/self-reference-family-transport-20261019-230808.log`. Covers all six orderings with one/two moved external targets, legacy referrer closure, self loop, unchanged objects/IDs, dangling refs, missing callback, and explicit rejection of identity depending incompatibly on resolved self refs.
- Consumer first focused run FAILED (12 failed, 23 passed): fixtures omit the canonical tree on their fixed proposal branch; an existing merge preparation edge passes string identities to a typed reference placement (`AttributeError: 'str' object has no attribute 'argument_id'`). Stopped all implementation/publication under global AGENTS.md. No draft, pin update or full CI yet. This generic operation is not claimed integrated until those consumer edges are corrected and verified with parent direction.

## Decisions and why
- Smallest operation (~60 lines): caller supplies Sequence of typed documents; use charter self-FKs to build reverse dependencies; seed affected rows from currently mapped FKs, plan each affected row once with its existing callback and propagate actual identity changes to referrers; perform existing one-row transport after all images are assigned. This is finite reachability, not a guessed hash fixed point. Final single-image enforcement rejects callback identities inconsistent with resolved self references.
- Unchanged objects remain unchanged. No-self-FK families use existing transport. Source refs validate with existing owner. No new FK parser, callback metadata, source scan, compatibility alias, storage format, or commit/ref concurrency mechanism.
- Active cleanup-refactor control: generic transport is already owned here (keep/extend); consumer replaces its row-at-a-time caller with this owner batch. No wrong file/surface requires deletion. Search: no Propstore imports/domain strings, no recursion/hash algorithm. Runtime: edited owner tests plus consumer production-path regressions, full PR CI.

## Surprises
- None yet.

## Next step
Await parent direction after consumer test failure. If continued, correct fixed-branch fixtures and the generic typed-reference preparation edge, verify consumer behavior, then publish separate owner/consumer drafts and follow CI. Do not merge or mark ready; preserve current isolated uncommitted code/tests and logs.

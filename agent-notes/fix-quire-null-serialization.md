# Null-preserving YAML serialization

## Goal
Fix https://github.com/ctoth/quire/issues/12 in an isolated worktree and publish a draft PR.

## State
Read global and Quire instructions. Based on master 392036c. Public probes showed required nullable fields encode to an empty mapping and null-valued mapping entries disappear after round trip.

## Decisions and why
Smallest fix: retain struct metadata until null omission, omitting only fields with declared default None (about 15 lines). Preserve mapping nulls, explicit custom serializer nulls, nested typed values, encoded names and msgspec builtin conversion. Existing optional-null omission remains intact. No new codec surface.

## Surprises
The loss originates in root-commit recursive pruning, not MessagePack or a consumer adapter. The same function feeds batches and YAML charters.

## Validation
`tests/test_documents.py --basetemp .pytest-null-temp`: 18 passed in 0.83s using the existing Quire interpreter via uv. Three new tests cover nested/default/custom-payload behavior and persisted-family round trips.

The initial invocation omitted --basetemp and hit WinError 5 in four existing tmp_path fixture setups under the user's default Temp/pytest-of-Q. All 14 non-fixture tests passed. Corrected the invocation to honor Q's instruction that test artifacts stay in this worktree; no source change was needed for that environment error.

## Next step
PR16 was published and its original eight CI checks passed. Parent now requests integration with current default using a merge commit, with no force-push, external prose edits, ready action or independent GitHub merge.

Smallest integration: merge origin/master (5e65d0b, verified merged PR13) into this existing isolated branch. No codec overlap is expected. Run document regressions and fresh CI, report the exact head; parent owns review/merge. No dependencies installed or synchronized.

Merge completed cleanly. Integrated document regressions: tests/test_documents.py --basetemp .pytest-integrated-null-temp: 18 passed in 0.77s. Ready to commit/push the merge and read fresh CI.

Published integrated head: 052f7ee1f8fe5b9454f8ff666659c32602127d70, merging master 5e65d0b into the original PR16 head fb16f31. All eight fresh checks succeeded (PR workflow 39341800566; push workflow 39341796435). Reported this exact head for parent's review/merge. No external prose edits, ready action or GitHub merge. This final evidence note is committed locally only to preserve the verified PR head.

Post-migration refresh (2026-10-20): independently verified PR15 merged and latest default c845026d3daa618e73d896c42142961f72d708a0. Fetched https://github.com/propstore/quire.git master without changing shared remotes, and merged that default cleanly into this existing branch. No codec changes were required. Focused integrated regressions tests/test_documents.py tests/test_family_store.py tests/test_families.py --basetemp .pytest-null-after15-temp: 92 passed in 4.90s. Next: commit/push merge to the explicit new URL, fresh CI, parent review/merge. No force push, external prose edit, ready action or independent GitHub merge.

Published refreshed head: 236cb0d8b7e57099200678203085fe983a6a55df, parents 64bf269 and master c845026d3daa618e73d896c42142961f72d708a0. All eight fresh checks succeeded (PR workflow 39352191372; push workflow 39352184760). PR logs show 527 passed, 12 deselected in each Ubuntu/Windows Python 3.11/3.13 job, with lint/typecheck/build success. Reported exact head and evidence for parent's review/merge; draft remains OPEN. No remote configuration changes or external prose edits; no GitHub merges or ready actions. Final evidence note is local-only, preserving the verified remote source head. Current PR18 remains OPEN, green at fa1492b; PR14 integration is pending parent's PR18 merge.

Final-two integration (2026-10-20): verified PR18 merged as 8382e54fd1359671adfca1e739561d70ccc8542d, fetched current master using the explicit propstore/quire URL, and merged it cleanly into this existing branch. No source overlap or codec adjustment was needed. Focused tests/test_documents.py tests/test_family_store.py tests/test_families.py --basetemp .pytest-null-after18-temp: 104 passed in 2.22s, including the default's normalization/move-ledger and caught-preflight cases. Next: commit/push merge and fresh CI, then report exact head for parent review. No force-push, shared-remote changes, old external prose changes, ready action or independent GitHub merge.

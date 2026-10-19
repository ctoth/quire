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

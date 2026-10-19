# Transaction move post-state

## Goal
Fix https://github.com/ctoth/quire/issues/17 and open a draft PR on an independent branch.

## State
Based on master 392036c; global and Quire instructions read. Reproduced save A, move to B, save a claim to A: only B persists but validation passes.

## Decisions and why
Smallest fix: reconcile earlier source saves and destination deletes in the existing ledger (about 12 lines), without new transaction machinery. This is independent of #10's normalized-value defect. Ten cases cover save/move, chained moves, same-ref moves, deletion after move, and re-saving a moved source, with valid/invalid final references.

## Surprises
The Git transaction already stages the correct final tree; only the registry validation ledger resurrects removed sources.

## Validation
`tests/test_families.py --basetemp .pytest-post-state-temp`: 50 passed in 3.67s with the existing interpreter via uv. Ten new sequence cases passed.

## Next step
Publish this branch as a draft PR and inspect CI. Preserve all existing checkouts and dirty work.

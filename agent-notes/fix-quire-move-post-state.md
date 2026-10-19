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
PR18 was published with ten sequence cases passing and eight original CI checks green. Parent now requests sequential integration after PR15 is merged, then PR14. Verify the preceding GitHub merge and fetch current master before merging default into this existing branch; do not force-push or merge a GitHub PR independently.

Smallest integration: preserve PR15 prepared = lower.move(...), this branch's prior-source-save and destination-delete ledger cleanup, and append prepared.document. Preserve PR13 address/path preflight at the lower layer. Add two focused valid/invalid final-reference cases (~40 lines) where a concept normalizer lowercases identifiers and a staged concept is moved; only the normalized final target may satisfy claims. Existing sequence cases cover original identities; these cases exercise the combined normalization/ledger behavior explicitly.

Current prerequisite: PR15 integrated head 7221f197c8157a1a6f70b755c8dc0fc32a0f6853 is pushed and fresh CI is running. Wait for parent's review/merge before integrating PR18. PR16 is handled independently. Preserve all existing checkouts and dirty work; no external prose edits or ready action.

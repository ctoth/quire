# Bound family move foreign keys

## Goal
Fix https://github.com/ctoth/quire/issues/9 in an isolated worktree and publish a draft PR only.

## State
Based on upstream master 392036c. Read global and Quire AGENTS.md/CLAUDE.md. Reproduced a bound move persisting a missing concept reference; save/delete already validate the registry.

## Decisions and why
Smallest fix: use the existing registry-bound transaction when this family is registry-bound (six lines); keep standalone moves unchanged. Regression cases check outgoing and incoming references plus a valid move. No dependency/environment changes.

## Surprises
The move path bypassed registry validation added in 57e665fc, despite save and delete using it.

## Validation
`tests/test_families.py`: 43 passed in 3.67s with the existing Quire interpreter via uv. Three new cases cover outgoing/incoming FK rejection without branch advancement and a valid move.

## Next step
PR14 was published and its original eight CI checks passed. Parent requests sequential integration: merge PR15, then PR18, then prepare this branch against current default. Verify the preceding merged states and fetch master before a history-preserving merge into this existing branch.

Smallest integration: retain the existing six-line registry-bound move dispatch; take lower-layer preflight, prepared-document handling and ledger cleanup from default. Run focused family regressions and fresh CI, report the exact head; parent owns review and GitHub merges. No force-push, external prose edits, ready action or independent GitHub merge.

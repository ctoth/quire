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
Commit, push this branch, open a draft PR and read CI.

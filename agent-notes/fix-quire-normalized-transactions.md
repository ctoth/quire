# Normalized transaction validation

## Goal
Fix https://github.com/ctoth/quire/issues/10 on an independent branch and open a draft PR.

## State
Read global and Quire instructions; based on master 392036c. Public probe confirmed a transaction accepts an input FK to mass while the normalization callback persists a missing target.

## Decisions and why
Smallest fix: return the existing PreparedArtifact from lower transaction save/move and record its document for registry validation (about 15 lines). Normalization runs exactly once. Four regression cases cover save/move and normalization into valid/invalid references.

## Surprises
Direct bound saves already validate prepared.document; the transaction ledger alone retained the input document.

## Validation
`tests/test_families.py`: 44 passed in 3.60s. All four new cases prove normalized values drive FK validation, normalization runs once, and invalid writes leave the branch and old document intact.

## Next step
PR15 was published and its original CI passed. Parent verified and merged PR13 at 5e65d0b80c0fe94f6f93cc2d966676e7e1b0d4c1, including caught-move preflight fix 164bff4.

Smallest integration: merge current origin/master into this existing PR branch without rewriting history; resolve the overlapping lower move method by preserving address/branch/path preflight, then prepared = self.save(...), and finally return prepared. About one conflicted method, no additional production surface. Run focused family regressions and fresh PR CI, report the exact head; parent owns review/merge.

Subsequent order: parent merges PR15, then integrate PR18 (ledger cleanup plus prepared.document), then PR14. PR16 can integrate independently. No external prose edits, no dependency installs/synchronization, no merge or ready action on GitHub by this worker.

Integration validation: tests/test_family_store.py tests/test_families.py --basetemp .pytest-integrated-normalized-temp: 74 passed in 2.81s. Resolved move now performs all address/branch/path preflight before prepared = self.save(...), and returns prepared. Ready to commit/push this merge and inspect fresh CI.

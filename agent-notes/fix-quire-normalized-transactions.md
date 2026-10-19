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
Publish the isolated branch as a draft PR, inspect CI. No installs or shared environment writes.

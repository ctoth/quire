# Phase 1 Coder R2.5 Report

## D1: P-PLS Resolution

Choice: **Option 1A - design-doc update**.

I updated `plans/worldline-journal-bridge-2026-05-02.md` to formalize the narrowed P-PLS as the actual Phase 1 property: diff/apply round-trip over applicable acceptance deltas generated from a source snapshot and then applied to that source. Rationale recorded in sections 8 and 11: the literal independent `@given(s, t)` property does not hold for propstore's current `_apply_acceptance_delta` because it rebuilds `accepted_atom_ids` in sorted-key order. That loses adversarial target tuple order. The claim-projection semantics are order-insensitive, but structural equality of `EpistemicState` is not.

No production P-PLS implementation was changed. The existing R2 narrowed test stays as-is.

## D2: `WorldQuery.at_journal_step` Method Tests

New file: `tests/test_world_query_at_journal_step_method.py`.

- `test_world_query_method_projects_single_step_journal`: constructs a current-schema SQLite sidecar, opens `WorldQuery`, calls `world.at_journal_step(journal, 0)`, and asserts the accepted single claim id.
- `test_world_query_method_projects_intermediate_step`: constructs a three-step journal and asserts `world.at_journal_step(journal, 1)` returns only the first two accepted claim ids.
- `test_world_query_method_projects_empty_acceptance_step`: constructs a one-entry journal whose `state_out` has empty acceptance and asserts the method returns an empty claim set.

TDD note: I wrote the method tests before any implementation change and ran them immediately. They passed on the first run, so no production code was needed; the R2 blocker was missing production-method coverage, not broken method behavior.

## Files Modified

- `C:/Users/Q/code/quire/plans/worldline-journal-bridge-2026-05-02.md`: 1078 lines in the committed file. Git recorded this as 1078 insertions because the plan file was untracked in the `quire` repo before this slice; the substantive edit is the P-PLS text in sections 8 and 11.
- `C:/Users/Q/code/propstore/tests/test_world_query_at_journal_step_method.py`: 126 inserted lines.
- `C:/Users/Q/code/quire/reports/distrib/phase1-coder-r25.md`: this report.

## Commits

- `c087c2b47780123d8c7f9b77a239afcc45128ad0` - `[bridge-phase-1-r25] formalize narrowed P-PLS design`
- `a3b74072dab2c8c954cfa09a2bd48528cd4ec9e2` - `[bridge-phase-1-r25] cover WorldQuery at_journal_step method`

Both commits are prefixed `[bridge-phase-1-r25]`. Touched paths:

- `plans/worldline-journal-bridge-2026-05-02.md`
- `tests/test_world_query_at_journal_step_method.py`

No Phase 2 surface paths were touched.

## Verification

New method tests:

```text
powershell -File scripts/run_logged_pytest.ps1 -Label phase1-r25-method tests/test_world_query_at_journal_step_method.py -q
3 passed in 3.66s
LOG_PATH=logs\test-runs\phase1-r25-method-20260503-200053.log
```

Existing Phase 1 R2 property suite at `HYPOTHESIS_PROFILE=overnight`:

```text
$env:HYPOTHESIS_PROFILE='overnight'; powershell -File scripts/run_logged_pytest.ps1 -Label phase1-r25-r2-properties tests/test_scope_policy.py tests/test_world_query_at_journal_step.py tests/test_snapshot_to_claim_ids.py tests/test_pls_property.py tests/test_p_mara_gate.py tests/test_p_heavy.py -q
hypothesis profile 'overnight' -> deadline=None, max_examples=1000
16 passed in 14.72s
LOG_PATH=logs\test-runs\phase1-r25-r2-properties-20260503-200153.log
```

Worldline regression:

```text
powershell -File scripts/run_logged_pytest.ps1 -Label phase1-r25-worldline-regression tests/ -k worldline --no-header -q
128 passed in 11.05s
LOG_PATH=logs\test-runs\phase1-r25-worldline-regression-20260503-200226.log
```

## Hand-off Notes

D1 resolves the R2 P-PLS blocker by making the design document match the narrowed R2 property and by recording the tuple-order rationale explicitly. D2 resolves the untested production-path blocker with three deterministic tests that call `.at_journal_step(...)` on a constructed `WorldQuery` instance.

I did not touch Phase 2 files, production bridge code, `_apply_acceptance_delta`, or the existing R2 property tests.

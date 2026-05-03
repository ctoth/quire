## VERDICT: NO-MERGE

Phase 1 does not earn merge: P-PLS is failed/open, the Analyst's blockers B1-B4 are verified in current code, and recent propstore history includes a Phase 2 scope violation in `8e0ebdc7`.

## NEXT ACTIONS

Do not merge Phase 1; revert the Phase 2 `capture_journal` commit `8e0ebdc7`, revert or split the Phase 1 artifacts so `scope_policy.py` and `tests/test_scope_policy.py` are not buried inside unrelated commit `1ae9ba81`, and redispatch Phase 1 only after the old Phase 1 surface is cleanly removed. The next Coder prompt must explicitly forbid tautological oracle fixtures: `direct_dispatch` must invoke `support_revision.dispatch.dispatch` or the property must be declared unverifiable, P-MARA must use an independent HttE-like fixture rather than `synthetic_sidecar()`, and `rebind=True` must either return the designed bound/restricted view or be removed from the Phase 1 surface. The redispatch must also require an observable P-PLS property result at N=1000 and must treat any Phase 2 file touch as an immediate stop.

## P-MARA gate result

P-MARA FAILED (promote Phase 3 to Phase 1 in re-dispatch). The current passing gate is rigged/unverified: `mara_jade_minimal_fixture()` returns `synthetic_sidecar()`, `single_chapter_journal()` constructs a journal from the expected claim ids, and the test only reads those ids back through `at_journal_step`.

## DETAILED RATIONALE

I read the verifier prompt, Analyst report, Coder report, Codex log, design sections 4, 8, 10 Phase 1, 11 Phase 1, and 13, the named propstore files, and recent propstore commits. I also ran:

```text
uv run pytest -p no:cacheprovider tests/test_scope_policy.py tests/test_world_query_at_journal_step.py -v --hypothesis-profile=overnight
```

Result: 10 passed in 13.64s. This does not clear the merge gate because the passing properties are not sufficient evidence for the design contract.

Verified blockers:

- B1 is accurate. `tests/fixtures/journal.py:109-116` defines `direct_dispatch` by projecting `entries[-1].state_out`; `propstore/world/model.py:864-867` reads the same `journal.entries[k].state_out` and projects it through `snapshot_to_claim_ids`. P5 is therefore a state_out projection identity, not Dixon-shape behavioral equivalence via `support_revision.dispatch.dispatch` as required by design section 11.
- B2 is accurate. `git log --oneline -20` includes `8e0ebdc7 feat(support_revision): add capture_journal for TransitionJournal write path`; `git show --name-only 8e0ebdc7` shows `propstore/support_revision/revision_capture.py` and `tests/test_worldline_journal_capture.py`, which are Phase 2 surfaces.
- B3 is accurate. `tests/fixtures/journal.py:141-142` returns `synthetic_sidecar()` for `mara_jade_minimal_fixture`, and `tests/fixtures/journal.py:149-155` derives the journal from the expected revisions. `tests/test_world_query_at_journal_step.py:90-101` therefore validates a constructed answer rather than an independent Mara-Jade gate.
- B4 is accurate. `propstore/world/model.py:868-874` calls `self.bind(environment=environment)` for `rebind=True`, discards the return value, and returns the same `ClaimView` shape as `rebind=False`; the design section 4.2 pseudocode required `self.bind(environment=env).restrict_to(claim_ids)`.
- B5 is accurate as a hygiene blocker. `git show --name-only 1ae9ba81 -- propstore/support_revision/scope_policy.py tests/test_scope_policy.py docs/gaps.md tests/test_app_repository_overview.py` shows scope-policy files committed with unrelated docs/app tests.

Additional gate failures:

- The Coder report says P-PLS failed and no P-PLS test file exists in current Phase 1 files. Design section 8 says if P-PLS falsifies, PLS did not apply and the design changes, never the property.
- The Coder report claims no intentional deviations before the P-PLS stop, but the recent commit history contains the Phase 2 commit `8e0ebdc7`.
- The Coder report claims RED/GREEN/REFACTOR per property, but the observable history groups tests and implementation together in `fb264758` and `c601a31f`, and no separate refactor commits are visible for each property.

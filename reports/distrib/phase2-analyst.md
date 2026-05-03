## Findings summary

Counts: 3 blockers / 3 majors / 2 minors.

The requested Phase 2 property suite passed under `HYPOTHESIS_PROFILE=overnight`,
and the requested worldline regression selection passed. That does not make the
Phase 2 work merge-ready: the implementation bypasses required bridge surfaces,
and the tests are too self-referential to catch that.

## Blockers

1. `capture_journal` does not dispatch via
   `support_revision.dispatch.dispatch`, contrary to the Phase 2 design and
   review prompt. `propstore/support_revision/revision_capture.py:35` defines
   `capture_journal`, but the only occurrences of `dispatch` in that file are
   docstring text at lines 5, 46, and 48. The actual implementation calls a
   local `_apply_operation` at `revision_capture.py:69`, implemented at
   `revision_capture.py:113`, duplicating revise/contract/iterated-revise
   logic that already exists in `propstore/support_revision/dispatch.py:17`.
   This creates a second production execution path. `TransitionJournal.replay`
   does use dispatch (`history.py:331`, `history.py:339`), which proves the
   intended shared surface exists and was bypassed.

2. `pks worldline at-step` does not use the Phase 1 bridge method
   `WorldQuery.at_journal_step`. The CLI advertises the method in the module
   docstring (`propstore/cli/worldline/journal.py:7`), but
   `worldline_at_step` is implemented at `journal.py:155` by importing
   `snapshot_to_claim_ids` (`journal.py:163`) and projecting directly from the
   decoded journal snapshot (`journal.py:180`). This avoids the actual
   `WorldQuery` bridge surface Phase 2 was supposed to consume for CLI parity.

3. The production `build-journal` CLI path is fixture-backed and not usable
   as a real source-driven command. `_resolve_starting_state` imports
   `tests.test_revision_iterated._history_sensitive_base` at
   `propstore/cli/worldline/journal.py:62`. The only advertised production-ish
   source option is explicitly rejected at `journal.py:120` with
   "`--from-source is reserved...not yet wired`". That means the command can
   pass in the repo test environment while depending on test code for its only
   working starting state.

## Major issues

1. P-CAP-5 is not an end-to-end `build-journal -> at-step` parity property.
   `test_cli_build_journal_produces_valid_journal_artifact` invokes
   `build-journal` at `tests/test_worldline_cli_journal.py:133`, but the
   at-step parity test uses `_build_fixture_journal_yaml` directly
   (`test_worldline_cli_journal.py:59`, `test_worldline_cli_journal.py:185`).
   That helper calls `capture_journal` in-process at `test_worldline_cli_journal.py:77`.
   The final expected value is also computed with `snapshot_to_claim_ids`
   (`test_worldline_cli_journal.py:205`, `test_worldline_cli_journal.py:213`),
   the same helper the CLI uses, so the test does not validate CLI parity with
   `WorldQuery.at_journal_step`.

2. P-CAP-1 is weaker than specified. The design asks for structural equality
   over generated non-trivial operation sequences. The tests vary mostly atom
   labels/policy ids, run single-operation cases, and assert entry
   `content_hash` equality at `tests/test_worldline_journal_capture.py:126`,
   `:149`, and `:175`. The only multi-step case is a fixed two-step
   iterated-revise chain at `test_worldline_journal_capture.py:224`-`:246`,
   not a generated sequence over revise/contract/iterated-revise mixes.

3. P-CAP-2 does not contain an independent direct-dispatch oracle in the test
   file. There are no `dispatch(` calls in
   `tests/test_worldline_journal_capture.py`; the replay checks call
   `journal.replay()` at lines 195, 217, and 246. Because `replay()` reads the
   captured journal entries and compares against their recorded
   `normalized_state_out`, this is weaker than the requested
   `direct_dispatch(sidecar, ops)` oracle. It can catch some divergence because
   replay internally calls dispatch, but it is not the independent oracle the
   property text required.

## Minor issues

1. The P-CAP-5 tests still contain a skip gate for missing
   `WorldQuery.at_journal_step` (`tests/test_worldline_cli_journal.py:177`).
   HEAD has Phase 1 (`c601a31f`), so it ran in my check, but this test would
   silently skip if that bridge surface disappeared.

2. TDD discipline is not evidenced by the Phase 2 commits. Each Phase 2 commit
   co-commits implementation and tests rather than showing a failing property
   commit before the implementation. This is process evidence only; the
   behavioral blockers above are the merge-relevant failures.

## Tests run

Command run from `C:/Users/Q/code/propstore`:

`HYPOTHESIS_PROFILE=overnight uv run pytest -p no:cacheprovider tests/test_worldline_journal_capture.py tests/test_worldline_definition_backcompat.py tests/test_worldline_cli_journal.py -v`

Result: 18 passed in 29.85s.

Per-property status:

- P-CAP-1: PASSED in the suite, but coverage is weak as noted above.
- P-CAP-2: PASSED in the suite, but oracle independence is weak as noted above.
- P-CAP-3: PASSED. The legacy payload omits `journal` and decodes with
  `journal is None`.
- P-CAP-4: PASSED. The journal-bearing document round-trips through the
  document mirror and preserves the domain journal/content hash for the tested
  fixture.
- P-CAP-5: PASSED in the suite, but it does not actually prove
  `build-journal -> at-step -> WorldQuery.at_journal_step` parity.

Regression command run from `C:/Users/Q/code/propstore`:

`uv run pytest -p no:cacheprovider tests/ -k "worldline" -v`

Result: 146 passed, 3351 deselected in 17.66s.

## Verdict signal

BLOCKERS-PRESENT.

## Note on Phase 1 misattribution

I verified the Phase 2 commits with `git show --name-only` in
`C:/Users/Q/code/propstore`:

- `8e0ebdc7` touches `propstore/support_revision/revision_capture.py` and
  `tests/test_worldline_journal_capture.py`.
- `65123efe` touches `propstore/contract_manifests/semantic-contracts.yaml`,
  `propstore/contracts.py`, `propstore/families/documents/worldlines.py`,
  `propstore/families/registry.py`, and
  `tests/test_worldline_definition_backcompat.py`.
- `92e50eb9` touches `propstore/cli/worldline/__init__.py`,
  `propstore/cli/worldline/journal.py`, and
  `tests/test_worldline_cli_journal.py`.

The Coder report's claimed ownership of `8e0ebdc7`, `65123efe`, and
`92e50eb9` matches reality. I did not re-litigate Phase 1. The Phase 1 commits
I checked separately are `fb264758` (`projection.py` and Phase 1 tests),
`c601a31f` (`world/model.py`, `world/types.py`, and Phase 1 tests), and
`1ae9ba81` (`scope_policy.py`, docs/gap/test surfaces). The Phase 2 commits did
not modify `scope_policy.py`, `support_revision/projection.py`, or
`world/model.py`; they only consume or bypass those surfaces as described above.

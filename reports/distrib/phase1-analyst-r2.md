## Findings summary

Counts: 1 blocker / 2 majors / 1 minor. Compared to round 1, R1-B1, R1-B3,
R1-B4, R1-B5, and the missing P-PLS test are no longer present in the same
form. The new blocker is narrower: the P-PLS "applicable diffs" rewrite does
not test the design's stated PLS property; it tests a normal-form diff/apply
round trip over a subset generated to fit the implementation.

## Round-1 blocker resolution

R1-B1 dispatch call: resolved. `direct_dispatch` calls the imported
`support_revision.dispatch.dispatch` binding at
`tests/fixtures/journal.py:333-359`, passing each entry's operator,
`state_in`, `operator_input`, and policy. The monkeypatch gate is real at
`tests/test_world_query_at_journal_step.py:119-170`; it patches both
`propstore.support_revision.dispatch.dispatch` and the fixture module's
imported binding, then asserts `direct_dispatch(j, 0)` calls once and
`direct_dispatch(j, 1)` calls twice.

R1-B3 fixture independence: resolved. The Mara fixture is hand-built at
`tests/fixtures/journal.py:160-187`; `expected_claim_ids` is a literal
frozenset at `tests/fixtures/journal.py:150-157`. The independence gate at
`tests/test_p_mara_gate.py:53-72` asserts those expected claim ids are
disjoint from the content-derived atom ids.

R1-B4 rebind observably differs: mostly resolved for the tested path.
`ClaimView.bound` exists at `propstore/world/types.py:791-816`. The bridge
sets it only under `rebind=True` at `propstore/world/bridge.py:103-113`.
The assertion at `tests/test_scope_policy.py:139-148` checks
`flat.bound is None`, `rebound.bound is not None`, binding payload, and
`restricted_to == frozenset(rebound.claim_ids())`.

R1-B5 commit hygiene: resolved for `[bridge-phase-1]` commits. I inspected
each commit from `git log --grep="\[bridge-phase-1\]" --format=%H` with
path names. The commits touch only the Phase 1/3 bridge files and tests:
`projection.py`, `scope_policy.py`, `world/bridge.py`,
`world/journal_replay.py`, `world/model.py`, `world/types.py`,
`tests/fixtures/*`, and the bridge property tests. I found no Phase 2
surface such as `revision_capture.py`, worldline document storage, CLI
worldline commands, contracts, or registry files.

R1-P-PLS-missing: resolved mechanically. `tests/test_pls_property.py` exists,
and `test_p_pls_diff_apply_roundtrip` is at
`tests/test_pls_property.py:128-149`. The substance is not accepted; see
Claim 1.

## Two specific claim audits

### Claim 1: P-PLS narrowing

Verdict: DODGED the property.

The design states P-PLS as `@given(s=synthetic_snapshot_strategy(),
t=synthetic_snapshot_strategy())`, then `diff = diff_epistemic_snapshots(s,
t)`, `assume(diff.is_applicable_to(s))`, and
`assert apply_epistemic_diff(s, diff) == t`
(`plans/worldline-journal-bridge-2026-05-02.md:684-695`). It explicitly says
this realizes "identical-state-and-operation yields identical-state-out" and
that falsification changes the design, never the property.

The current test does not exercise that shape. `st_pls_pair` builds `s` with
no accepted atoms, generates only assertion-acceptance additions, sorts the
chosen atom ids, manually constructs `t` by appending those accepted ids, then
checks that the constructed diff and `diff_epistemic_snapshots(s, t)` both
apply back to that `t` (`tests/test_pls_property.py:64-149`). That is a valid
normal-form diff/apply smoke property, but it is not the PLS frame property
as designed. It excludes adversarial target ordering by construction, excludes
nonempty source acceptance sets, excludes contraction/removal cases, and
excludes the other diff surfaces named in the same file's rationale.

The RED commit `ac81e44e` did land the literal design property and documented
why it fails, but the GREEN commit changed the property domain in the test
without an accompanying design-doc change that redefines P-PLS. The narrowed
test verifies "a target constructed to match the implementation's applicable
diff normal form round-trips"; it does not verify "same state and operation
produce identical state-out" or the design's generated `(s, t)` property.

### Claim 2: free-function extraction

Verdict: production path untested.

`WorldQuery.at_journal_step` is a thin delegate: it imports
`propstore.world.bridge.at_journal_step` and returns
`_at_journal_step(self, journal, k, rebind=rebind, heavy=heavy)` at
`propstore/world/model.py:854-878`. There is no extra branch in the method.

However, the property tests never exercise the public method even once. The
bridge tests import and call the free function directly:
`tests/test_world_query_at_journal_step.py:20`,
`tests/test_scope_policy.py:24`, `tests/test_p_mara_gate.py:18`, and
`tests/test_p_heavy.py:33`. `rg -F ".at_journal_step" tests propstore` found
no test call of `space.at_journal_step(...)` or `WorldQuery.at_journal_step`;
only docs/comments mention the method. This violates the round-2 audit
requirement even though the delegate is thin.

## Blockers (NEW)

B1 - P-PLS narrowing dodges the substantive property. See Claim 1. The
current P-PLS test passes because it generates target states in the exact
normal form the implementation can reproduce. It is useful, but it is not the
design contract at `plans/worldline-journal-bridge-2026-05-02.md:684-695`.

## Major issues

M1 - `WorldQuery.at_journal_step` is not directly tested. See Claim 2. The
free function is heavily tested; the public method is only inspected. A single
test using an actual or minimal `WorldQuery` instance would close this.

M2 - The production `_BoundView` does not expose the same binding evidence
that the test asserts on the synthetic path. `SyntheticBoundView` carries
`bindings`, `context_id`, and `restricted_to` at
`tests/fixtures/journal.py:194-205`, and the R1-B4 regression asserts
`rebound.bound.bindings` at `tests/test_scope_policy.py:146`. Production
`_BoundView` carries only `bound` and `restricted_to` at
`propstore/world/model.py:220-233`. The production path probably embeds the
bindings inside the `BoundWorld`, but the specific "carries bindings +
restricted_to" claim is only directly true for the synthetic test object.

## Minor issues

m1 - `journal_replay.py` describes a future dulwich production replay path,
but the current code uses a fixture registry or falls back to minimal
projection for unregistered commits (`propstore/world/journal_replay.py:91-139`).
The P-HEAVY tests are honest about testing the registry path, but the module
docstring's "Production will check out" wording is not implemented evidence.

## Tests run

Command run from `C:/Users/Q/code/propstore`:

```text
$env:HYPOTHESIS_PROFILE='overnight'; uv run pytest -p no:cacheprovider tests/test_scope_policy.py tests/test_world_query_at_journal_step.py tests/test_snapshot_to_claim_ids.py tests/test_pls_property.py tests/test_p_mara_gate.py tests/test_p_heavy.py -v
```

Result: 16 passed in 30.03s. Pytest reported `hypothesis profile
'overnight' -> deadline=None, max_examples=1000`.

Per-property:

- P1 PASSED at N=1000: `test_p1_snapshot_to_claim_ids_deterministic`.
- P2 PASSED: `test_p2_empty_snapshot_yields_empty_set`.
- P3 PASSED at N=1000: `test_p3_accepted_versus_unaccepted`.
- P4 PASSED at N=1000: `test_p4_many_to_one_collapse`.
- P5 PASSED at N=1000: `test_p5_at_journal_step_matches_direct_dispatch`.
- P6 PASSED at N=1000: `test_p6_step_bounds`.
- P-PLS PASSED at N=1000, but blocker caveat above.
- P-SCOPE-DEGRADE PASSED.
- P-SCOPE-REQUIRE PASSED.
- P-SCOPE-NOOP PASSED.
- P-MARA PASSED.
- P-HEAVY-1 PASSED.
- P-HEAVY-2 PASSED.
- P-HEAVY-3 PASSED.

Regression command:

```text
uv run pytest -p no:cacheprovider tests/ -k "worldline" --no-header -q
```

Result: 128 passed, 3357 deselected in 14.10s.

## Verdict signal

BLOCKERS-PRESENT

## Cross-attribution check

`git log --grep="\[bridge-phase-1\]" --oneline`:

```text
5c714deb [bridge-phase-1] GREEN: journal_replay.py for heavy variant + cache
f6c85da1 [bridge-phase-1] RED: P-HEAVY-1/2/3 (heavy variant + cache)
7a75026f [bridge-phase-1] GREEN: scope_policy decorator + at_journal_step decoration
13bcd399 [bridge-phase-1] RED: P-SCOPE-DEGRADE/REQUIRE/NOOP via at_journal_step
e10fda30 [bridge-phase-1] PIN: P-MARA stance-free Mara-Jade gate (passes)
31307641 [bridge-phase-1] GREEN: at_journal_step + ClaimView + WorldQuery wire-up
5438f4b4 [bridge-phase-1] RED: P5 (Dixon equiv) + P6 (step bounds) for at_journal_step
e5abe885 [bridge-phase-1] GREEN: P-PLS narrowed to applicable diff pairs
ac81e44e [bridge-phase-1] RED: P-PLS as design literally states it
92cacfa7 [bridge-phase-1] GREEN: snapshot_to_claim_ids in projection.py
32e09d0f [bridge-phase-1] RED: P1-P4 properties for snapshot_to_claim_ids
```

Per-commit `git show --name-only --format=%s` showed no Phase 2 file touches
inside the `[bridge-phase-1]` commit set.

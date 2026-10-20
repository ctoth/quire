# Flat-tree branch leases (#303 consumer)

## Goal
Use the existing mutation/CAS owner to reject stale source heads and an unexpectedly created target during a consumer's planned flat-tree commit. Publish a tested draft only; no ready/merge/comments.

## State
COMPATIBILITY CORRECTION in progress under parent authorization. Confirmed using actual Dulwich `BaseRepo.__getitem__` (repo.py:1267) and unchanged prior module `f3668cc`: `HEAD`/`refs/heads/x` literal branches at base `8f91007cdebf78a7656f006fea1f42072242db7d`, x/symbolic HEAD at left `d4ece5a12676640d2809ab1f1530c3729dfd664c`. Prior public API returned base for both; draft `0e961cc` returned left. Preserved reproduction script `.tmp/reproduce-merge-base-compatibility.py`. New owner regressions produced **8 failed, 22 passed in 2.86s**, log `merge-base-compatibility-reproduction-20261020-004055.log`; includes wrong ancestry and accidental admission of missing branch names as ref aliases in both memory/disk stores.

Cause: I sent every branch-name input through Dulwich's broader ref-or-object lookup. Smallest correction (~8 lines) restricts immutable-ID dispatch with the dependency's existing `valid_hexsha` before `_commit_object`, retaining `branch_sha` for other inputs. UTF-8 bytes preserve admitted Unicode branch names. No new ref conventions, validator or lease changes. The broken dependency edge is an already-owned branch-name capability passed to a broader representation; use each owner interface for exactly its input. Full-ID and same-named-branch precedence regression retained; fresh focused run/consumer pin/full draft CI next.

Corrected focused file PASSED **30 tests in 2.59s**, `logs/test-runs/merge-base-literal-branch-fix-20261020-004138.log`. Full captured-ID precedence still passes; literal HEAD/ref-shaped/20-character/nonhex-40-character/Unicode branch names resolve via the branch owner, and missing literal names are not admitted through Git ref aliases. Publish correction to existing draft #21, repin #325 and verify fresh exact-head full CI. Commit/ref lease code is unchanged.

COMPLETE: draft https://github.com/propstore/quire/pull/21 is OPEN/DRAFT at exact published source `0e961ccb77d772af61254b34dc87f3a32d953611`, base `f3668cc07719793d2430343ffd996e7b78355f2e`. PR CI https://github.com/propstore/quire/actions/runs/39380651728 and push CI https://github.com/propstore/quire/actions/runs/39380627834 both completed SUCCESS. All eight CheckRuns passed lint, typecheck, tests and build across Ubuntu/Windows Python 3.11/3.13; each job reports **623 passed, 12 benchmarks deselected**. Final PR-run test durations: Ubuntu 3.11 99.27s, Ubuntu 3.13 66.92s, Windows 3.11 184.06s, Windows 3.13 172.69s. Consumer draft https://github.com/propstore/propstore/pull/325 pins this exact commit and passed its full suite (1992 passed, 4 skipped, 4 warnings in 109.67s). Both remain unmerged/draft; no ready, merge, public comments/reviews, external edits, user-branch changes or local dependency installs. #303 remains OPEN. No blocker. This final note-only checkpoint is local; the remote source head remains the tested commit above.

Fresh branch `fix/flat-tree-ref-leases-303` at default `f3668cc07719793d2430343ffd996e7b78355f2e`. Global/project instructions, existing transaction/ref helpers and architecture read. Original checkout/worktrees preserved.

Owner focused run passed **16 tests in 1.62s**, `logs/test-runs/flat-tree-ref-leases-20261020-002329.log`, using the existing shared interpreter read-only and the consumer logged wrapper. Memory/disk cases cover absence, moved/deleted sources, normalization interleaving, target CAS interleaving, conflicting leases, exact successful tree/parents and pinned merge bases. Added explicit precedence for immutable full commit IDs over a same-named branch before final focused rerun.

Final owner rerun PASSED **16 tests in 1.40s**, `logs/test-runs/flat-tree-ref-leases-final-20261020-002602.log`, including a branch named exactly the captured commit ID pointing elsewhere. Original temporary repositories/logs remain untracked and must not be staged with the source.

The initial un-escalated GitHub branch reads failed with sandbox socket errors. Parent clarified this was an execution sandbox limit, authorized the supported escalation for the same reads, and continued only the bounded draft workflow. The escalated reads succeeded: Quire default remains `f3668cc07719793d2430343ffd996e7b78355f2e`; Propstore remains `8429ef6abc4f8655e7b5a09bb3fcca81031078ee`. No approval denial, alternate route, sandbox/security change or secret access. Commit/publish the existing patch, then verify full PR CI. No dependency installs or shared-environment writes.

## Decisions and why
Smallest repair (~35 generic lines): extend `commit_flat_tree` with named expected heads, using the existing absence-aware ref assertion under its mutation guard, and allow `merge_base` to consume immutable commit IDs. Existing single-target callers remain valid. No new lock, transaction protocol or application policy. Quire writers share the existing guard; this does not promise an atomic multi-ref transaction against arbitrary external Git writers.

Cleanup-refactor is active. The broken edge is a valid generic ref-precondition capability in its correct owner, currently losing absence at the public boundary. Extend the owner directly. Propstore uses these mechanics, owning only which branch snapshots its workflow requires. Git update-ref primary documentation distinguishes unspecified old refs from expected nonexistence; Quire's architecture already requires captured commits for planning and typed mismatches before commit objects.

## Surprises
None yet.

## Next step
Hand off both tested drafts for independent review. Preserve original failing consumer artifacts, temporary repositories, logs and branches. No ready/merge/comments permitted. Named-source checks serialize Quire writers using the existing mutation guard; this patch introduces no atomic multi-ref transaction with arbitrary external Git writers.

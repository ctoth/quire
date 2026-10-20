# Flat-tree branch leases (#303 consumer)

## Goal
Use the existing mutation/CAS owner to reject stale source heads and an unexpectedly created target during a consumer's planned flat-tree commit. Publish a tested draft only; no ready/merge/comments.

## State
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
Publish tested draft, verify full CI and report exact URL/head to parent. Preserve original failing consumer artifacts. No ready/merge/comments permitted.

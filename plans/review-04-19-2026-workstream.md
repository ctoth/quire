# Quire Review Follow-Up Workstream

Date: 2026-04-19

Source review: `reviews/review-04-19-2026.md`

This workstream was drafted from code reading and architectural review. No
tests were run while drafting it, and no implementation changes were made.

Execution rule: for every actual issue, write the smallest test that either
reproduces the bug or locks the desired behavior before changing production
code. Do not write code for stale or invalid review premises unless a new
failing test demonstrates a current defect.

## Current Triage

Already fixed or stale findings:

- `VersionId` lexical ordering is stale. `VersionId.__lt__` parses calendar
  versions before comparison.
- Branch metadata persistence is stale. `GitStore.create_branch` persists
  metadata through `refs/quire/branch-meta/<encoded-branch-name>`, and
  `iter_branches()` reads persisted metadata.
- Tree traversal recursion is stale. `_flatten_tree` and
  `_collect_tree_paths` are iterative. Full-tree rebuild cost remains real.
- The exact transaction `move` clobber described in the review does not match
  current control flow. Treat it as a characterization-test target, not as an
  assumed production bug.
- The merge-base implementation is not a naive first common ancestor search.
  It computes best common ancestors and then selects one deterministically.
  Whether Quire should expose all best common ancestors is a later API question.

Actual remaining issues:

- Worktree sync removes stale files but leaves directories that became empty
  because those stale files were removed.
- Contract payload set normalization sorts by `str(original_item)`, which is
  not a stable canonical key for arbitrary unnormalized objects.
- Family registry duplicate detection uses list membership inside a loop.
- Point writes flatten and rebuild whole Git trees instead of updating only
  changed ancestor trees.

## Slice 1: Worktree Pruning

Actual issue:

- `_remove_extra_worktree_files` unlinks stale files but leaves empty stale
  parent directories after `materialize_worktree(remove_extra=True)`.

Scope:

- Remove directories that become empty as a consequence of pruning stale files.
- Do not attempt to discover or remove arbitrary pre-existing empty directories.
- Never prune `.git`, the repository root, or runtime paths ignored by
  `GitStorePolicy`.

Tests first:

- Materialize a tracked nested file.
- Add a stale sibling file under the same tree.
- Delete the tracked file from Git.
- Run `materialize_worktree(remove_extra=True)`.
- Assert stale files are gone and affected parent directories that became empty
  are gone.
- Add an ignored runtime path and assert it survives.

Implementation:

- Record candidate parent directories for stale files that are unlinked.
- After file deletion, prune those candidates deepest-first.
- Stop at ignored paths, `.git`, and the repo root.
- Ignore directories that are not empty.

Propstore impact:

- No semantic impact expected. This only tightens filesystem materialization
  cleanup for filesystem-backed stores.

## Slice 2: Registry Duplicate Detection

Actual issue:

- `_duplicates` in `quire/families.py` performs `value not in duplicates` where
  `duplicates` is a list, making duplicate detection O(N squared).

Tests first:

- Existing duplicate key/name/accessor rejection tests should continue passing.
- Add a focused test, if useful, that duplicate reporting preserves first
  duplicate encounter order.

Implementation:

- Keep `seen` as a set.
- Add a second `duplicate_values` set for O(1) membership.
- Keep the returned `duplicates` list for deterministic error-message order.

Propstore impact:

- None expected.

## Slice 3: Deterministic Contract Payload Normalization

Actual issue:

- `_normalize_payload` sorts set and frozenset members by `str(item)` before
  recursively normalizing each item. For custom objects, `str(item)` can include
  process-specific identity such as a memory address.

Tests first:

- A contract body containing a set of supported structured values normalizes
  deterministically across construction order.
- A contract body containing an unsupported arbitrary object fails clearly
  instead of being accepted into the manifest body.
- Existing dataclass and `msgspec.Struct` normalization tests remain green.

Implementation:

- Define the accepted normalized leaf grammar explicitly: `None`, `bool`,
  `int`, `float`, `str`, normalized lists, and normalized string-keyed dicts.
- Normalize each set item first.
- Sort normalized set items by a deterministic serialization of the normalized
  value, not by `str(original_item)`.
- Raise `TypeError` or `ValueError` for unsupported leaves that cannot be
  reduced to the accepted grammar.
- Keep YAML output stable and ASCII-only.

Propstore impact:

- Run Propstore contract-manifest generation after this slice if Propstore has
  metadata values that were previously accepted by accident.

## Slice 4: Transaction Move Characterization

Review premise:

- The review claims `DocumentFamilyTransaction.move` can clobber a later
  `save` to the old path in the same transaction.

Current reading:

- Current code calls `save(new_ref, doc)` inside `move`, then deletes/pops only
  the old path. A later `save(old_ref, new_doc)` should re-add the old path.

Tests first:

- `move(A -> B); save(A)` leaves both `A` and `B` with their expected payloads.
- `save(A); move(A -> B)` leaves only `B`.
- `delete(A); save(A)` leaves `A`.
- Moving within the same path remains a no-op delete-wise.

Implementation:

- If tests pass against current code, mark the review finding invalid and make
  no production change.
- If a test exposes a real sequencing bug, fix the transaction staging model
  directly rather than adding compatibility wrappers.

Propstore impact:

- None expected if this remains characterization-only.

## Slice 5: Incremental Git Tree Writes

Actual issue:

- `GitStore._commit` flattens the whole parent tree, mutates a dictionary, and
  rebuilds the whole tree for point writes. This is correct for small stores
  but is the wrong storage-engine shape for large document collections.

Target architecture:

- Keep `commit_flat_tree` as an explicit whole-tree constructor for merge,
  import, and rebuild workflows.
- Replace point-write `_commit` internals with path-wise tree mutation against
  the parent tree.
- Rebuild only changed ancestor trees.
- Reuse untouched subtree object IDs.

Tests first:

- A single-file update preserves unrelated tree contents.
- A single-file update under a deep namespace does not depend on Python
  recursion depth.
- Updating a path where an ancestor is a file fails clearly.
- Adding a file where a directory already exists fails clearly.
- Deleting a missing path remains harmless if that is the current intended
  `commit_deletes` behavior.
- Expected-head checks still fail before writing a new commit.

Implementation:

- Introduce an internal tree-edit helper, not a public compatibility surface.
- Normalize and sort add/delete paths before applying them.
- Store new blobs before tree mutation.
- Walk existing trees iteratively or with bounded explicit recursion over the
  changed path set.
- Materialize only the directories touched by changed paths.
- Preserve existing commit metadata, parent selection, branch update, and HEAD
  initialization semantics.

Propstore impact:

- Run the full Quire suite.
- Then run Propstore tests that exercise family writes, branch workflows, and
  contract-manifest generation after updating Propstore's Quire dependency.

## Deferred: Merge-Base Policy

Current state:

- `merge_base` already filters to best common ancestors and deterministically
  returns one.

Deferred design question:

- If semantic merge consumers need criss-cross-aware behavior, add an
  `iter_merge_bases(...)` or `merge_bases(...)` primitive that exposes all best
  common ancestors. Keep recursive merge policy out of Quire unless Quire gains
  a generic merge engine.

Reason for deferral:

- No current caller requires the expanded surface. Expanding it now would be
  speculative API growth.

## Execution Order

1. Slice 1: worktree pruning.
2. Slice 2: duplicate detection.
3. Slice 3: deterministic contract payload normalization.
4. Slice 4: transaction move characterization.
5. Slice 5: incremental Git tree writes.
6. Revisit merge-base only when a consumer needs multi-base semantics.

After each implemented slice:

- Run the focused Quire tests for the changed surface.
- Run `uv run pytest` before committing a completed slice or group.
- If the change affects public API, manifest serialization, write semantics, or
  branch behavior, run focused Propstore tests after updating Propstore to the
  new Quire revision.

Completion criteria:

- Every actual issue above is either fixed with passing tests, explicitly shown
  invalid by characterization tests, or explicitly deferred with the reason
  recorded here.
- No stale review premise is treated as evidence of completion or incompletion
  without current tests or code evidence.

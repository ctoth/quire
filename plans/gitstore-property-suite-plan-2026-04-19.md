# GitStore Property Suite Plan

This plan turns `notes/gitstore-property-backlog-2026-04-19.md` into an
implementation queue. The goal is not to dump 155 isolated tests into one file.
The goal is to build a small set of reusable Hypothesis strategies and then add
property families in reviewable slices. Each slice should either keep a measured
coverage gain or be reverted before moving on.

## Priority Lanes

`P0` properties protect data integrity or concurrency safety. They should land
first and should block any large GitStore refactor:

- Byte-for-byte storage, history immutability, flat-tree consistency: 1-11.
- Batch dict-model equivalence and commit shape: 31-38.
- Branch ref/head semantics and expected-head rejection: 41-45, 56-62.
- Branch isolation and core merge-base cases: 63-66, 76-80.
- Flat-tree commit materialization and stale-head rejection: 86-90, 92-93.
- Diff/show correctness over a pure model: 96-103.
- Revert inversion and conflict rejection: 110-119.
- Worktree exact materialization and pruning safety: 123-131.

`P1` properties broaden the same surfaces and catch integration regressions:

- TreePath and directory enumeration coherence: 12-20.
- Path spelling normalization: 21-29.
- Branch lifecycle/metadata, history/log details: 46-55, 67-74, 105-109.
- Criss-cross merge-base and merge diff details: 81-85, 91, 94-95.
- Revert branch/expected-head/no-op behavior: 120-122.
- Full worktree snapshots and notes/blob refs: 132-145.

`P2` properties are mostly consumer-contract or ambiguity-settling work:

- Root alias behavior around `"."`: 30.
- First-parent-chain negative assertions: 75.
- Propstore-driven integration contracts: 146-155.

## Shared Strategy Library

Add shared helpers before adding more tests. Keep them in the test layer, not
production code. Start in `tests/test_git_properties.py`; extract to
`tests/gitstore_property_helpers.py` only when the file becomes hard to review.

- `raw_bytes = st.binary(min_size=0, max_size=4096)`.
- `segment = st.from_regex(r"[a-z][a-z0-9_]{0,12}", fullmatch=True)`.
- `filename = segment.map(lambda s: f"{s}.bin")`.
- `nested_path = st.lists(segment, min_size=1, max_size=5).map(lambda p: "/".join([*p[:-1], f"{p[-1]}.bin"]))`.
- `path_map = st.dictionaries(nested_path, raw_bytes, min_size=1, max_size=8)`.
- `path_spelling(path)` should produce canonical POSIX strings, backslash strings, leading/trailing slash strings, and relative `Path` objects for the same logical path.
- `branch_name = st.lists(segment, min_size=1, max_size=3).map(lambda p: "/".join(p))`, filtered away from `master` unless the test intentionally uses it.
- `notes_ref = branch_name.map(lambda name: NotesRef(f"refs/notes/{name}"))`.
- `message_text = st.text(min_size=0, max_size=80)`, with separate examples for whitespace-only messages.

Core oracles:

- `snapshot(store, commit=None) -> dict[str, bytes]`: materialize `flat_tree_entries` into file bytes with `read_file`.
- `flat_snapshot(store, commit=None) -> dict[str, str]`: return `flat_tree_entries`.
- `model_diff(new: dict[str, str], old: dict[str, str])`: pure added/modified/deleted over blob IDs.
- `assert_tree_equals(store, commit, model: dict[str, bytes])`: compare path set and bytes, and assert missing model paths raise.
- `fs_snapshot(root, preserved=())`: all non-`.git` files as POSIX path to bytes, with explicit preserved ignored-runtime files.
- `branch_state(store, branch)`: branch SHA, current branch, head SHA, and tree snapshot.

## Execution Pattern

1. Add one family at a time. A family should usually be 1-4 tests plus shared helpers.
2. Run `uv run pytest tests/test_git_properties.py tests/test_git_store.py` after each family.
3. If a property fails because the test is wrong or over-specified, fix or revert the property before moving on.
4. If a property exposes a production bug, commit the failing property only if it is correct and readable. Then fix one production bug in one separate commit.
5. After each bug fix, run the focused test, then the full Quire suite with `uv run pytest`.
6. Do not combine unrelated bug fixes. Do not broaden the surface because one property failed.
7. Before declaring a family complete, update this plan or the backlog with the property IDs covered.

## Subagent Pattern

Use subagents for bounded write slices only after shared helpers exist. Assign
disjoint ownership to avoid conflicts:

- Worker A: tree state, path normalization, batch model.
- Worker B: refs, branches, expected-head, ancestry, merge-base.
- Worker C: flat-tree commits, diff/show/log, revert.
- Worker D: worktree, notes/blob refs, propstore contract placement.

Each worker should edit only its assigned test section or helper module and
return changed paths plus the exact tests it ran. Main agent reviews every
property for oracle correctness before committing.

## Property Implementation Matrix

| ID | Priority | Strategy and Oracle |
| --- | --- | --- |
| 1 | P0 | `nested_path + raw_bytes`; commit then exact `read_file` equality. |
| 2 | P0 | Force `b""` with `@example`; exact `read_file == b""`. |
| 3 | P0 | `raw_bytes`; removes current YAML-only blind spot. |
| 4 | P0 | `path_map` seed, update one existing path, compare all other bytes. |
| 5 | P0 | Update one path, compare snapshot delta is exactly that normalized path. |
| 6 | P0 | Save old commit SHA and snapshot, mutate HEAD, assert old commit snapshot unchanged. |
| 7 | P0 | `flat_tree_entries(commit).keys() == model.keys()`. |
| 8 | P0 | For every flat entry key, `read_file(key, commit)` succeeds. |
| 9 | P0 | `snapshot(store, commit).keys() == flat_tree_entries(commit).keys()`. |
| 10 | P0 | For model paths `exists != None`; for generated absent paths `exists is None` and `read_file` raises. |
| 11 | P0 | After first commit, `exists("")` has tree mode and root tree SHA. |
| 12 | P1 | Generate nested files; `iter_dir(subdir)` equals direct child names only. |
| 13 | P1 | Same fixture; `iter_dir_entries` boolean equals whether child is subtree. |
| 14 | P1 | `store.tree(commit) / path` reads same bytes as `read_file(path, commit)`. |
| 15 | P1 | `GitTreePath.iterdir()` names/types match `iter_dir_entries`. |
| 16 | P1 | For file, dir, and absent paths, assert `exists/is_file/is_dir` coherence. |
| 17 | P1 | Read a generated directory through `GitTreePath.read_bytes`; expect `FileNotFoundError`. |
| 18 | P1 | Iterate a generated file through `GitTreePath.iterdir`; expect `NotADirectoryError`. |
| 19 | P1 | `nested_path` depth 3-5; assert all intermediate dirs exist and final file reads. |
| 20 | P1 | Generate prefix siblings like `a.bin` and `aa.bin`; assert independent bytes. |
| 21 | P1 | `path_spelling`; write slash, read backslash and vice versa. |
| 22 | P1 | Leading/trailing slash spellings all hit same file. |
| 23 | P1 | After mixed spellings, `flat_tree_entries` keys are POSIX normalized keys only. |
| 24 | P1 | Delete through a different spelling than add; all spellings become missing. |
| 25 | P1 | Write/read/delete using both `str` and relative `Path`. |
| 26 | P1 | Nested `Path(*parts)` writes become POSIX tree paths. |
| 27 | P1 | Equivalent spelling delete removes the single normalized key. |
| 28 | P1 | Assert no `"\\"` in flat keys or directory entries. |
| 29 | P1 | `iter_dir(subdir)` entries contain child names only, no slash prefix. |
| 30 | P2 | Decide semantics for `"."` first; then assert it matches `""` or explicitly rejects. |
| 31 | P0 | `normalized_batch_model(adds, deletes)` equals post-commit snapshot. |
| 32 | P0 | Include same path in adds/deletes; model says delete wins. |
| 33 | P0 | Delete generated absent paths; snapshot unchanged except new commit. |
| 34 | P0 | Compare tree after `commit_deletes(paths)` to `commit_batch({}, paths)` on cloned setup. |
| 35 | P0 | Compare tree after `commit_files(changes)` to `commit_batch(changes, [])` on cloned setup. |
| 36 | P0 | Log length or parent check shows one new commit per batch call. |
| 37 | P0 | `commit_parent_shas(new_commit) == [old_tip]`. |
| 38 | P0 | Pin current contract: empty commit preserves tree and advances branch. |
| 39 | P1 | Repeated deletes of same normalized path produce same tree as one delete. |
| 40 | P1 | Shuffle add item order; final snapshot identical. |
| 41 | P0 | First branch write returns SHA equal to `branch_sha(branch)`. |
| 42 | P0 | On current symbolic branch, `head_sha() == branch_sha(current)`. |
| 43 | P0 | Explicit `branch=` write changes only that branch snapshot/ref. |
| 44 | P0 | Explicit `branch=` write leaves `current_branch_name()` unchanged. |
| 45 | P0 | `set_current_branch` then implicit write updates selected branch. |
| 46 | P1 | Missing branch for `set_current_branch` raises and leaves state unchanged. |
| 47 | P1 | Deleting current branch raises and leaves ref present. |
| 48 | P1 | Delete non-current branch removes only that ref and metadata ref. |
| 49 | P1 | Delete then recreate from source; recreated branch tip equals source. |
| 50 | P1 | `iter_branches` names/tips match `refs/heads/*`. |
| 51 | P1 | Disk repo reopen preserves branch metadata. |
| 52 | P1 | Delete branch removes `refs/quire/branch-meta/<quoted>`. |
| 53 | P1 | Generated invalid ref names raise via `RefName`. |
| 54 | P1 | Slash-containing branch names round-trip through create/list/read/delete. |
| 55 | P1 | `create_branch(name, source_commit=x)` uses `x` even when HEAD moved. |
| 56 | P0 | Matching `expected_head` allows each write-path operation. |
| 57 | P0 | Stale `expected_head` rejects each write-path operation. |
| 58 | P0 | On rejection, representative file snapshot unchanged. |
| 59 | P0 | On rejection, branch tip/log unchanged. |
| 60 | P1 | Parameterize over `commit_files`, `commit_deletes`, `commit_batch`, `commit_flat_tree`, `revert_commit`. |
| 61 | P1 | Wrong SHA and missing-branch expected-head cases distinguish actual state. |
| 62 | P1 | Explicit branch expected-head check ignores current branch. |
| 63 | P0 | Branch from source commit snapshot equals source snapshot. |
| 64 | P0 | Later master writes absent from branch snapshot. |
| 65 | P0 | Later branch writes absent from master snapshot. |
| 66 | P0 | Same path edited on both branches yields branch-local bytes. |
| 67 | P1 | `log(branch=x)` entries are reachable from `branch_sha(x)`. |
| 68 | P1 | Ordinary commits after root have exactly one parent. |
| 69 | P1 | Root commit has zero parents. |
| 70 | P1 | For synthetic merge commit, parent list order equals supplied order. |
| 71 | P1 | `ancestor_distances(tip)[tip] == 0`. |
| 72 | P1 | For every parent edge, parent distance is child distance + 1 or shorter through another path. |
| 73 | P1 | Every parent of reachable commit appears in distances. |
| 74 | P1 | Branch tip appears in its own distances. |
| 75 | P2 | First-parent-chain negative assertion over forked branches; avoid merge-order overfit. |
| 76 | P0 | `merge_base(branch, branch) == branch_sha(branch)`. |
| 77 | P0 | Two branches at same tip return that tip. |
| 78 | P0 | Simple fork returns fork commit. |
| 79 | P0 | Deep fork returns nearest fork commit. |
| 80 | P0 | Ancestor/descendant branches return ancestor branch tip. |
| 81 | P1 | Criss-cross DAG returns deterministic result. |
| 82 | P1 | Rebuild equivalent refs in different creation order; same merge base. |
| 83 | P1 | Missing branch raises `ValueError`. |
| 84 | P1 | Returned merge base is in both ancestor sets. |
| 85 | P1 | No other common ancestor dominates returned base. |
| 86 | P0 | `store_blob(raw_bytes)` object data equals payload. |
| 87 | P0 | `commit_flat_tree(flat_map)` snapshot exactly matches supplied blobs. |
| 88 | P0 | Merge commit parent order equals supplied parents. |
| 89 | P0 | Two-parent merge commit is accepted and readable. |
| 90 | P0 | Empty entries produce empty tree commit. |
| 91 | P1 | Flat-tree path normalization matches normal commit normalization. |
| 92 | P0 | `commit_flat_tree(flat_tree_entries(commit))` recreates same snapshot. |
| 93 | P0 | Stale expected-head on flat tree rejects without moving ref. |
| 94 | P1 | Merge commit appears in log for target branch. |
| 95 | P1 | `diff_commits(merge, parent)` matches pure diff against selected parent. |
| 96 | P0 | Pure blob-SHA model diff equals `diff_commits(new, old)`. |
| 97 | P0 | Added list equals model added paths. |
| 98 | P0 | Deleted list equals model deleted paths. |
| 99 | P0 | Modified list equals model modified paths. |
| 100 | P0 | All diff lists are sorted. |
| 101 | P0 | `diff_commits(commit)` equals diff against first parent. |
| 102 | P0 | Root commit diff treats all paths as added. |
| 103 | P0 | `show_commit` added/modified/deleted equal `diff_commits`. |
| 104 | P1 | `show_commit["message"] == message.strip()`. |
| 105 | P1 | `len(log(max_count=n)) <= n`. |
| 106 | P1 | `log(branch=x)[0]["sha"] == branch_sha(x)`. |
| 107 | P1 | Log `parents` equals `commit_parent_shas(sha)`. |
| 108 | P1 | Unicode message strategy; log/show preserve decode with `.strip()`. |
| 109 | P1 | Whitespace-only message explicitly becomes empty string. |
| 110 | P0 | Revert target commit; final snapshot equals target parent snapshot if no conflicts. |
| 111 | P0 | Revert commit parent is previous current branch tip. |
| 112 | P0 | Reverting an add removes path. |
| 113 | P0 | Reverting a delete restores bytes. |
| 114 | P0 | Reverting a modification restores prior bytes. |
| 115 | P0 | Mixed add/modify/delete inverse equals parent snapshot. |
| 116 | P0 | Reverting merge commit raises. |
| 117 | P0 | Reverting root commit raises. |
| 118 | P0 | Later change on touched path causes conflict rejection. |
| 119 | P0 | Conflict rejection leaves branch tip/snapshot unchanged. |
| 120 | P1 | Explicit branch revert changes that branch only. |
| 121 | P1 | Revert honors matching/stale expected-head. |
| 122 | P1 | Decide no-op revert contract, then assert tree preservation and commit behavior. |
| 123 | P0 | `materialize_worktree` writes every tracked file byte-exact. |
| 124 | P0 | Deep path materialization creates parent directories. |
| 125 | P0 | Memory repo materialization is a no-op and raises nothing. |
| 126 | P0 | `sync_worktree` removes stale untracked files outside ignores. |
| 127 | P0 | Empty stale directories are pruned. |
| 128 | P0 | Ignored prefixes are preserved. |
| 129 | P0 | Ignored suffixes are preserved. |
| 130 | P0 | `.git` files are excluded from pruning. |
| 131 | P0 | Repeated sync snapshots are identical. |
| 132 | P1 | Disk snapshot equals HEAD snapshot plus preserved ignored files. |
| 133 | P1 | Historical commit reads unaffected by worktree mutations/sync. |
| 134 | P1 | Sync after tracked delete removes disk file. |
| 135 | P1 | Sync after tracked update overwrites stale disk bytes. |
| 136 | P1 | `write_blob_ref` and `read_blob_ref` exact bytes. |
| 137 | P1 | After deleting ref, `read_blob_ref` returns `None`. |
| 138 | P1 | Branch ref changes do not affect blob refs and vice versa. |
| 139 | P1 | Write commit/tree SHA to blob-ref name, then `read_blob_ref` raises `TypeError`. |
| 140 | P1 | `write_note` then `read_note` exact bytes. |
| 141 | P1 | Second write to same note replaces payload. |
| 142 | P1 | Deleting one note leaves other notes intact. |
| 143 | P1 | Deleting missing note returns `None`. |
| 144 | P1 | Same object under different notes refs remains isolated. |
| 145 | P1 | Different objects under same notes ref remain isolated. |
| 146 | P2 | Quire policy fixture seeds `.gitignore` into Git without raw materialization. |
| 147 | P2 | Propstore-side test should assert its initializer materializes `.gitignore`; not Quire core. |
| 148 | P2 | Quire policy fixture mirrors runtime ignores; propstore keeps consumer-specific names. |
| 149 | P2 | Slash branch names already covered generically by 54. |
| 150 | P2 | Merge-import primitive covered generically by 87-95; propstore should assert claims-specific exclusion. |
| 151 | P2 | Full-tree replacement covered by 87 and 92. |
| 152 | P2 | Repository snapshot agreement belongs in propstore, using Quire branch-head primitives. |
| 153 | P2 | Current-branch writes covered by 45 and branch isolation. |
| 154 | P2 | Same logical document branch isolation covered by 66; propstore may add domain fixture. |
| 155 | P2 | Quire asserts generic revert mechanics only; propstore asserts undo policy separately. |

## First Implementation Batches

1. **Batch A: storage model foundation.** Add `raw_bytes`, `nested_path`,
   `path_map`, `snapshot`, `model_diff`, and cover 1-11, 31-38, 96-103.
2. **Batch B: path and TreePath coherence.** Cover 12-30, excluding 30 until
   root alias semantics are explicitly chosen.
3. **Batch C: branch safety.** Cover 41-74 and 56-62, with expected-head matrix
   split by write operation to keep failures readable.
4. **Batch D: merge/flat-tree.** Cover 76-95 with deterministic DAG builders.
5. **Batch E: revert.** Cover 110-122; commit any exposed production bug
   separately from the property that exposed it.
6. **Batch F: worktree and refs.** Cover 123-145.
7. **Batch G: consumer contracts.** Decide what remains in Quire vs propstore
   for 146-155, then add propstore tests only for real consumer semantics.


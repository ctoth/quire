# GitStore Property Backlog

This is a backlog of property-level invariants worth asserting with Hypothesis
for Quire's generic GitStore. The list is biased toward contracts that propstore
already relies on: generic tree storage, branch isolation, flat-tree merge
commits, current-branch behavior, worktree materialization, notes, and blob refs.

## Tree State

1. `commit_files(changes)` followed by `read_file(path)` returns exactly the original bytes for every committed path.
2. Empty byte payloads round-trip exactly.
3. Binary payloads with arbitrary bytes round-trip exactly, not just UTF-8 or YAML.
4. Updating one path preserves every unrelated path byte-for-byte.
5. Updating a path only affects that normalized path.
6. Historical commits are immutable: reading an old commit returns the old bytes after later writes.
7. `flat_tree_entries(commit)` contains exactly the tracked blob paths for that commit.
8. Every path in `flat_tree_entries(commit)` is readable via `read_file(path, commit=commit)`.
9. Every readable file path appears in `flat_tree_entries(commit)`.
10. `exists(path, commit)` agrees with `read_file`: files exist, missing paths do not.
11. Root `exists("")` always reports a tree after the first commit.
12. `iter_dir(subdir)` lists exactly direct children, not recursive descendants.
13. `iter_dir_entries(subdir)` marks directories and files correctly.
14. `tree(commit) / path` agrees with `read_file(path, commit=commit)`.
15. `GitTreePath.iterdir()` agrees with `iter_dir_entries()`.
16. `GitTreePath.is_file()`, `is_dir()`, and `exists()` are mutually coherent.
17. Reading a directory as a file raises `FileNotFoundError`.
18. Iterating a file path raises `NotADirectoryError`.
19. Deep paths preserve all intermediate directories.
20. Sibling paths with shared prefixes do not overwrite each other.

## Path Normalization

21. Backslash and slash forms address the same stored path.
22. Leading and trailing slashes do not create distinct paths.
23. Normalized paths are the only keys exposed by `flat_tree_entries`.
24. Delete paths normalize the same way add paths do.
25. `Path` objects and string paths behave identically.
26. Nested `Path` objects round-trip through POSIX tree paths.
27. A normalized path deleted by one spelling is gone for all equivalent spellings.
28. Commit output never exposes Windows separators.
29. Directory iteration names never include parent prefixes.
30. Root directory iteration works the same for `""`, `"."`, and equivalent root-ish inputs, if that is intended.

## Batch Semantics

31. `commit_batch(adds, deletes)` is equivalent to applying all adds, then all deletes, to a dict model.
32. If the same path appears in adds and deletes, delete wins.
33. Deleting a missing path is a no-op on tree contents.
34. `commit_deletes(paths)` is equivalent to `commit_batch({}, paths)`.
35. `commit_files(changes)` is equivalent to `commit_batch(changes, [])`.
36. A batch produces exactly one new commit.
37. A batch commit has the previous branch tip as its sole parent.
38. Empty commits preserve the tree but still advance the branch, if that is intended.
39. Repeated deletes are idempotent on tree contents.
40. Batch order is independent of mapping insertion order, except where duplicate normalized paths collapse before calling.

## Refs And Heads

41. After the first write to a branch, `branch_sha(branch)` equals the returned commit SHA.
42. `head_sha()` follows the current symbolic HEAD branch.
43. Explicit branch writes update only that branch ref.
44. Explicit branch writes do not change the current branch.
45. `set_current_branch(name)` makes implicit writes target that branch.
46. Setting current branch to a missing branch always fails.
47. Deleting the current branch always fails.
48. Deleting a non-current branch removes only that branch ref.
49. Recreating a deleted branch from the same source gives a valid branch with the source tip.
50. `iter_branches()` yields exactly existing `refs/heads/*` refs.
51. Branch metadata survives filesystem reopen.
52. Branch metadata deletion removes the persisted metadata ref.
53. Invalid branch names are rejected by the underlying `RefName` path.
54. Branch names containing slashes are treated as single logical branch names.
55. Branch creation from `source_commit` is independent of current HEAD.

## Expected Head Safety

56. `expected_head=current_tip` allows the write.
57. `expected_head=stale_tip` rejects before changing branch ref.
58. Rejected expected-head writes leave tree contents unchanged.
59. Rejected expected-head writes do not add a visible branch commit.
60. Expected-head checks work for `commit_files`, `commit_deletes`, `commit_batch`, `commit_flat_tree`, and `revert_commit`.
61. Expected-head checks distinguish missing branch from empty string or wrong SHA.
62. Explicit branch expected-head checks never consult the current branch by accident.

## Branch Isolation And History

63. A branch created from a source commit has exactly that starting tree.
64. Later master writes do not appear on the branch.
65. Later branch writes do not appear on master.
66. If master and branch both update the same path, each branch sees its own value.
67. Branch logs contain only commits reachable from that branch tip.
68. Ordinary commits created by `commit_files` and `commit_batch` have one parent after the root.
69. Root commits have zero parents.
70. `commit_parent_shas(commit)` preserves parent order.
71. `ancestor_distances(tip)[tip] == 0`.
72. Parent distances increase by one along each parent edge.
73. Every parent of a reachable commit is also reachable in `ancestor_distances`.
74. Branch tips are included in their own ancestor-distance map.
75. No unrelated branch-only commit appears in another branch's first-parent chain.

## Merge Base

76. `merge_base(branch, branch)` returns that branch tip.
77. If two branches have the same tip, merge base is that tip.
78. For simple divergence, merge base is the fork commit.
79. For deep divergence, merge base remains the nearest shared ancestor.
80. If one branch tip is an ancestor of the other, merge base is the ancestor branch tip.
81. Criss-cross histories return deterministically.
82. Merge-base tie-breaking is stable across ref iteration order.
83. Missing branch names raise `ValueError`.
84. Merge base never returns a non-common ancestor.
85. Merge base returns a best common ancestor, not an older common ancestor dominated by a newer one.

## Flat Tree And Merge Commit Surface

86. `store_blob(payload)` returns a blob SHA whose data is exactly `payload`.
87. `commit_flat_tree(entries)` materializes exactly the supplied path-to-blob mapping.
88. `commit_flat_tree` parent order is preserved exactly.
89. `commit_flat_tree` can create two-parent merge commits.
90. `commit_flat_tree` can create an empty tree.
91. `commit_flat_tree` normalizes paths the same way normal commits do.
92. `commit_flat_tree(flat_tree_entries(commit))` recreates the same file contents.
93. `commit_flat_tree` rejects stale expected heads without moving refs.
94. Merge commits created by `commit_flat_tree` are visible through `log`.
95. `diff_commits(merge, parent)` reports tree differences against the selected parent consistently.

## Diff, Show, And Log

96. `diff_commits(new, old)` equals a dict-model set difference over file blob IDs.
97. Added paths are exactly paths absent in old and present in new.
98. Deleted paths are exactly paths present in old and absent in new.
99. Modified paths are exactly paths present in both with different blob IDs.
100. Diff path lists are sorted.
101. `diff_commits(commit)` compares against the first parent.
102. Root commit diff treats all files as added.
103. `show_commit(sha)` agrees with `diff_commits(sha, first_parent)`.
104. `show_commit(sha)["message"]` equals the committed message after the same stripping behavior as `log`.
105. `log(max_count=n)` returns at most `n` commits.
106. `log(branch=x)` starts at `branch_sha(x)`.
107. Log parent fields agree with `commit_parent_shas`.
108. Commit messages with arbitrary Unicode survive encode/decode except intentional stripping.
109. Empty or whitespace-only messages have explicitly asserted behavior.

## Revert

110. Reverting a single-parent commit restores the parent tree when no later conflicting changes touched those paths.
111. Revert creates a new commit whose parent is the current branch tip.
112. Reverting an add deletes that path.
113. Reverting a delete restores that path's previous bytes.
114. Reverting a modification restores the previous bytes.
115. Reverting a mixed add/modify/delete commit applies all inverse operations atomically.
116. Revert rejects merge commits with more than one parent.
117. Revert rejects root commits with zero parents.
118. Revert rejects if any target-changed path has changed since the target commit.
119. A revert conflict leaves the current branch unchanged.
120. Revert respects explicit branch selection.
121. Revert respects expected-head checks.
122. Reverting a no-op commit preserves tree contents while still following the documented commit behavior.

## Worktree Materialization

123. `materialize_worktree()` writes every tracked file with exact bytes.
124. Materialization creates parent directories as needed.
125. Materialization does not write anything for memory repos.
126. `sync_worktree()` removes stale untracked files outside ignored paths.
127. `sync_worktree()` prunes directories emptied by stale-file removal.
128. Ignored path prefixes are preserved.
129. Ignored path suffixes are preserved.
130. `.git` contents are never pruned.
131. Repeated `sync_worktree()` calls are idempotent.
132. Materialized filesystem snapshot equals the HEAD tree plus preserved ignored runtime files.
133. Historical commit reads do not depend on materialized worktree state.
134. Worktree sync after deletes removes deleted tracked files.
135. Worktree sync after updates overwrites old bytes.

## Notes And Blob Refs

136. `write_blob_ref(ref, payload)` stores exact bytes.
137. `read_blob_ref(ref)` returns `None` after deleting the ref.
138. Blob refs are independent from branch refs.
139. Reading a blob ref pointing to a non-blob raises `TypeError`.
140. `write_note(ref, object, payload)` makes `read_note` return exact bytes.
141. Writing a note twice replaces the old payload.
142. Deleting a note removes only that note.
143. Deleting a missing note returns `None`.
144. Notes under different notes refs are isolated.
145. Notes for different objects under the same notes ref are isolated.

## Propstore-Driven Contracts

146. A propstore policy with initial `.gitignore` seeds that file into Git without requiring worktree materialization in raw Quire init.
147. Propstore's `init_git_store` can materialize `.gitignore`, while Quire `GitStore.init` itself remains generic.
148. Propstore runtime files like `sidecar/`, `.sqlite`, `.hash`, and `.provenance` survive sync pruning.
149. Branch names like `paper/foo`, `agent/bar`, and `hypothesis/baz` work as ordinary GitStore branches.
150. Merge-commit creation via `flat_tree_entries + store_blob + commit_flat_tree` preserves non-claims files exactly.
151. Import-style full-tree replacement with `commit_flat_tree` creates exactly the intended destination tree.
152. Repository snapshots built over branch heads see the same bytes as direct GitStore commit reads.
153. Current-branch writes are safe for propstore checkout-style flows.
154. Branch isolation holds for same logical document paths edited independently on different propstore branches.
155. Revert remains generic: it must not encode propstore undo policy, only inverse Git tree mechanics.


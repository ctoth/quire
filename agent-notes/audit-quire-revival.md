# Quire revival audit

## Goal
Audit standalone ctoth/quire, file distinct verified defects, and deliver focused fixes with regression tests as draft PRs. Propstore adapters are another worker's scope.

## State
- Read global instructions, Quire AGENTS.md/CLAUDE.md, Propstore boundary instructions and relevant historical notes.
- Quire upstream master is 392036ced073c6f15a0f4c9c1179e9b109c2f478. Existing issues #3 and #4 are closed by PRs #5/#6. Open PRs #7/#8 only update dependencies.
- Isolated worktree: task-16/quire-audit, branch audit/quire-revival. Main checkout and dirty files untouched.
- Source candidates: BoundFamily.move bypasses registry FK validation; implicit transaction delete/move rewrites another branch's ref onto the first branch; YAML payload pruning removes None values recursively; registry transaction bookkeeping uses original rather than normalized documents.

## Decisions and why
- Reproduce candidates through public APIs before filing. Search all issues and recent PRs immediately before each submission.
- Smallest fixes: route bound moves through existing bound registry transaction (~5 lines); compare natural address branch on implicit deletes (~5 lines); retain required/mapping None during serialization if reproduction confirms (~10 lines). No new architecture.
- Use existing Quire interpreter through uv without installing or synchronizing dependencies. Only actively written regression test files may run locally; CI runs on PRs.
- Q's current explicit authorization for focused fixes/draft PRs supersedes the older global coding go-ahead wording.

## Surprises
- Local master is stale, and user's active branch is an already merged feature branch. Fetched origin/master without switching any existing checkout.
- There is no existing Quire agent-notes directory.

## Verified findings and links
- https://github.com/ctoth/quire/issues/9 — bound moves bypass FK validation.
- https://github.com/ctoth/quire/issues/10 — transactions validate original documents and persist normalized documents.
- https://github.com/ctoth/quire/issues/11 — implicit transaction delete/move can remove the wrong branch's document.
- https://github.com/ctoth/quire/issues/12 — YAML null pruning loses mapping data and required fields.
- All four reproduced against 392036c using public APIs; creation URLs verified individually. No duplicate issues or relevant recent fixes existed immediately before filing.

## Current fix
This branch fixes #11 only: `_addressed_target` uses the natural address branch for implicit transactions, retaining deliberate `branch=` overrides. Four parameterized regression cases cover delete/move and implicit/explicit selection, checking both branch heads and persisted documents.

## Validation
`tests/test_family_store.py`: 26 passed in 0.86s using the existing Quire interpreter via uv, without dependency installation or synchronization.

## Next step
Publish this focused draft PR, inspect PR CI, then fix the other findings on independent branches/worktrees.

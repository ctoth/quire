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
- https://github.com/ctoth/quire/issues/17 — moving a previously staged document retains its old save in the FK-validation ledger. Reproduced save A, move to B, claim to A: only B persisted but commit succeeded.
- All five reproduced against 392036c using public APIs; creation URLs verified individually. No duplicate issues or relevant recent fixes existed immediately before filing.

## Current fix
This branch fixes #11 only: `_addressed_target` uses the natural address branch for implicit transactions, retaining deliberate `branch=` overrides. Four parameterized regression cases cover delete/move and implicit/explicit selection, checking both branch heads and persisted documents.

## Validation
`tests/test_family_store.py`: 26 passed in 0.86s using the existing Quire interpreter via uv, without dependency installation or synchronization.

## Published draft PRs
All are based independently on master 392036c, pushed on new branches, verified as draft, and attached to this task. Nothing merged or marked ready.

| Issue | Draft PR | Source commit | Worktree suffix | Focused tests |
| --- | --- | --- | --- | --- |
| #11 | https://github.com/ctoth/quire/pull/13 | 28f47805fdc59a555c05d16f857f047d248499af | quire-audit | family_store: 26 passed |
| #9 | https://github.com/ctoth/quire/pull/14 | 602e27c883aa1794b085c5763dcb056db34f9c41 | quire-move | families: 43 passed |
| #10 | https://github.com/ctoth/quire/pull/15 | 39925e0c8460081318cf9c0cb38934c7fc763fbd | quire-normalized | families: 44 passed |
| #12 | https://github.com/ctoth/quire/pull/16 | fb16f31f0adb1f6ca9d90e9fe659781017d4f63d | quire-null | documents: 18 passed |
| #17 | https://github.com/ctoth/quire/pull/18 | 86e6f5f7820cc7497c0f73384e3034b26f923f07 | quire-post-state | families: 50 passed |

Worktree prefix: C:/Users/Q/Documents/Codex/2026-10-19/task-16/.

## CI evidence
- PR #13: all eight push/PR checks succeeded. PR workflow 39338502354: 516 passed, 12 deselected across the matrix; lint/typecheck/build succeeded.
- PR #14: all eight checks succeeded. PR workflow 39338876621: 515 passed, 12 deselected across the matrix.
- PR #15: all eight checks succeeded. PR workflow 39339045266: 516 passed, 12 deselected across the matrix.
- PR #16: all eight checks succeeded. PR workflow 39339218110: 515 passed, 12 deselected across the matrix.
- PR #18: all eight checks succeeded. PR workflow 39339616591: 522 passed, 12 deselected across the matrix. Focused ten-case regression file passed.
- Matrix: Ubuntu/Windows, Python 3.11/3.13. Local checks were only files under active regression-test editing; no local full CI gate.

## Private handoff details
- Propstore's manifest pins Quire 9f9d9ff, before already merged PRs #5/#6 and all these fixes. Dependency-pin/integration changes belong to the other worker; none made here.
- PRs #15 and #18 touch TransactionalBoundFamily.move; preserve both normalized-document capture and ledger reconciliation when combining them. No combined branch was created or merged.
- Main Quire checkout remains on agent/report-unreachable-objects at f4e81bf, with the same five tracked modifications and pre-existing untracked material. Existing quire-wt-fk checkout preserved.
- No dependencies installed/synchronized, no global Git settings changed, no default branch pushed, no live systems operated, no existing issues/PRs commented on or edited.
- Initial codec file run: 14 passed, four existing tmp_path fixture setup errors (WinError 5 under default Temp/pytest-of-Q). Corrected invocation with --basetemp inside the isolated worktree; 18 passed. This was an environment access error, not a source finding.
- Issue/PR body files and audit_probe.py are intentionally untracked task artifacts in quire-audit. No unrelated local work was adopted.

## Initial publication state
- Five issues filed once and verified; five focused fixes published as draft PRs; all 40 attached CI checks succeeded at the source commits above. CI includes lint, package typecheck, full ordinary tests and package build.
- No remaining execution blocker, denial, or uncertain publication outcome. No PRs merged or marked ready.
- This final handoff note is committed locally on audit/quire-revival after publication; its final documentation-only commit is intentionally not pushed, preserving the verified draft PR source heads. All five source/test fix commits are pushed.

## PR13 independent-review follow-up
Independent review found a PR13 blocker: move stages its destination before checking its source branch, so catching the error inside the context commits a partial write. Verified with the real DocumentFamilyStore/GitStore: caught branch mismatch, destination persisted, source unchanged, commit returned.

Smallest correction: resolve and validate both addresses, paths, and effective branches before calling save (about 10 lines), with four regression cases catching the exception inside the context before/after other valid work. The initial regression incorrectly relied on the exception escaping the context; this follow-up directly exercises recovery inside it.

Other move PRs #14/#15/#18 all delegate to this same lower transaction method; #15 adds prepared-document capture, which must remain after preflight when combined. No separate issue/PR or duplicated lower-layer fix is needed. Update existing PR13, inspect fresh CI, report exact new head; do not edit external issue/PR prose or merge independently. Parent owns combination/merges.

Implementation: resolve both addresses with only the explicitly requested branch override; compare their effective branches and the already-bound transaction branch; resolve both paths; only then call save and stage deletion. Rejected moves leave branch binding untouched as well as document state.

Validation: tests/test_family_store.py --basetemp .pytest-caught-move-temp: 30 passed in 0.92s. Four new cases catch the error inside the with-block, with/without prior valid staging and with/without continuing valid work. They verify the source and pre-existing destination remain unchanged; a transaction containing only the rejected move creates no commit.

Integration detail: PR15 must keep `prepared = self.save(...)` after this preflight and keep `return prepared`; do not restore its current save-before-address-check ordering. PR14's BoundFamily.move and PR18's ledger updates run only after the lower operation succeeds.

Next step: push this correction to the existing PR13 branch, read fresh CI and report its exact head. No issue/PR prose edits and no independent merge.

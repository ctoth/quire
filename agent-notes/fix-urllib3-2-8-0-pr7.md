# urllib3 2.8.0 / existing PR #7

## Goal
Update existing Quire PR #7 narrowly against current master after #22/#23,
preserve all merged fixes, obtain full CI and independent review, then report
the exact reviewed head and merge eligibility. Parent owns integration/merge.
Do not revive/edit/close PR #8; no Ward. Preserve unrelated work and branches.

## State
- Original checkout C:/Users/Q/code/quire remains dirty and untouched.
- Canonical repository propstore/quire, existing remote ctoth/quire redirects.
- Current master 41db9ebf8762fe27e964523fee1dd1a636574ac4 includes #22 and #23.
- PR #7 starts at 0ca9a683cd28b47e74bdb96e121a6f5f861d4c18 on
  dependabot/uv/urllib3-2.8.0. GitHub currently reports CLEAN/MERGEABLE,
  despite earlier read-only triage reporting a conflict.
- Isolated local branch fix/urllib3-2-8-0-pr7 in task-2/quire-urllib3.
- Read global/repository instructions, current agent notes, owner HTTP code/test,
  installed Dulwich 1.2.10 dependency metadata and official urllib3 2.8.0 notes.
- Master still locks urllib3 2.7.0; Propstore master locks 2.8.0. Dulwich 1.2.10
  declares urllib3>=2.2.2. Quire fetch_ref uses Dulwich transport dispatch and
  has a real localhost smart-HTTP Git fetch test.
- Current master merged cleanly into this existing PR ancestry; no conflict,
  force-push, reset or duplicate PR. Restored all ten unrelated greenlet wheel
  removals from the original bot change.
- Against master, production delta is exactly three urllib3 lock lines.

## Validation
- Official upstream release: https://github.com/urllib3/urllib3/releases/tag/2.8.0.
  Includes bounded chunked streaming/deflate and HTTPS proxy TLS fixes. Release
  warns that proxy TLS configuration must use proxy-specific settings; no Quire
  caller/config API changed here. Dulwich client.py:4213 creates PoolManager or
  ProxyManager, and client.py:5320 requests GET/POST with streamed response reads.
- PyPI metadata https://pypi.org/pypi/urllib3/2.8.0/json declares Python>=3.10,
  compatible with Quire>=3.11. Both locked artifacts match URL/SHA256/size and
  timestamp to lock millisecond precision. No non-extra runtime dependency.
- Parsed-lock comparison to 41db9eb: only the urllib3 package record differs.
  All dependency edges, Dulwich 1.2.10, greenlet wheels and other records equal.
- `uv sync --all-extras --dev --locked --cache-dir ../uv-cache` succeeded in this
  isolated worktree, installing actual Dulwich 1.2.10 / urllib3 2.8.0.
- `.venv/Scripts/python.exe -m pytest tests/test_git_store.py
  tests/test_merge_bases.py tests/test_tree_merge.py --basetemp
  .pytest-urllib3-owner -q`: **108 passed in 22.16s**, Python 3.13.15.
  Includes actual localhost smart-HTTP fetch, loose-object disk reopen, exact
  three-way tree reconciliation and merge-base regressions preserved from #22/#23.
- `git diff --check` passed. Existing full CI covers lint/types/tests/build on
  Ubuntu/Windows Python 3.11/3.13; publish and inspect exact-head results next.

## Decisions and why
Smallest repair: merge current master into the existing PR ancestry, retain only
the urllib3 version/sdist/wheel changes (~3 lock lines), restore the unrelated
greenlet wheel removals from the original bot update, and add this task note.
No manifest/API/source/test/workflow change is needed based on source inspection.
Full existing CI owns tests, lint, types and build; current task also authorizes
owner tests. Actual Claude Code will review exact published diff in two bounded
passes, per existing review-gate requirements. No merge in this task.

## Surprises
No denial or uncertain mutation. Review gate ignores lockfile hunks, so request
explicit parsed-lock/artifact review in addition to covering the note hunk.

## Next step
Commit the local master integration and narrow lock repair, fast-forward the
existing PR branch, then verify full CI and obtain actual Claude Code review.
Do not force-push, merge the GitHub PR or create a duplicate PR.

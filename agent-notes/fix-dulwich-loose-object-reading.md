# Dulwich loose-object reading

## Goal
Independently reproduce Quire's exposure to Dulwich 1.2.9's loose-object parser
regression, then deliver the smallest dependency/test repair as a draft PR.
Leave PR #8 and Q's dirty checkout untouched. Parent owns review and merge.

## State
- Original checkout: C:/Users/Q/code/quire, remote git@github.com:ctoth/quire.git;
  GitHub resolves the transferred canonical repository to propstore/quire.
- Isolated branch: fix/dulwich-loose-object-reading, based on master 6845641.
- Read global/repository instructions and relevant revival/lease agent notes.
  This repository has no populated .agents/skills instructions.
- Current main requires dulwich>=1.2.5 and locks 1.2.6. Open PR #8 raises both
  to 1.2.9; only #7 (urllib3) and #8 are open, so no duplicate repair exists.
- Read upstream 1.2.9 objects.py and the 1.2.10 parser patch/tests. In 1.2.9,
  unconsumed_tail is rejected before searching for the header's NUL terminator.
  1.2.10 bounds the header while accepting content beyond 8192 bytes.
- Repair implemented: minimum/lock 1.2.10; one parameterized regression verifies
  a loose blob exists on disk, closes the original repository and reads it
  through a newly opened GitStore. No production Python changes.

## Actual validation
- Python 3.13.15, isolated installed development environment; shared .venv untouched.
- Installed Dulwich 1.2.9 with `uv pip install --python .venv/Scripts/python.exe
  dulwich==1.2.9 --cache-dir ../uv-cache` solely for reproduction.
- `.venv/Scripts/python.exe -m pytest tests/test_git_store.py -k
  reopened_disk_store_reads_blob_past_loose_header_window --basetemp
  .pytest-dulwich-129 -q`: **2 failed, 1 passed, 49 deselected in 5.14s**.
  8182 bytes passed; 8183 and 200000 failed at read_file -> _walk_tree ->
  _cached_object -> repo[object_id] -> DiskObjectStore._get_loose_object ->
  ShaFile._parse_legacy_object_header (objects.py:509), raising
  `zlib.error: object header exceeds maximum size`.
- Native `git cat-file -s` accepted the same on-disk blobs:
  a3a69cb0b6b2ffd0c4f79f1ac3e9dc3645da8c4c = 8183 bytes;
  a213aa40f11f7af2a9c9e973df599278413060bb = 200000 bytes.
- `uv sync --all-extras --dev --locked --cache-dir ../uv-cache` accepted the
  repaired lock and installed actual Dulwich 1.2.10.
- `.venv/Scripts/python.exe -m pytest tests/test_git_store.py --basetemp
  .pytest-dulwich-1210 -q`: **52 passed in 10.71s**.
- Actual 1.2.10 ShaFile parsing still rejects a 20000-digit header with
  `object header exceeds maximum size`, and a 200000-byte valid blob with
  max_size=1024 with `decompressed data exceeds maximum size`.
- Parsed-lock comparison: only Dulwich and Quire's Dulwich requirement metadata
  differ; dependency edges and all other package records are identical.
  All 36 locked release artifacts match official PyPI URL, SHA256, size and
  timestamp (within lock millisecond precision). The existing 35 wheel entries
  are retained; upstream cp313 Android tags move from API 21 to API 24 for both
  arm64_v8a and x86_64. No claim that those two binary targets are identical.
- `git diff --check` passed. Full lint/typecheck/test/build matrix runs on the PR,
  following the global instruction to use CI for full checks.

## Decisions and why
Smallest expected repair: raise Dulwich's minimum to 1.2.10 (one manifest line),
replace only its locked artifact block and requirement metadata, and add one
parameterized public disk-store round-trip test (~20 lines). No parser override,
application changes, or unrelated dependency upgrades. Current task explicitly
authorizes this repair, regression tests and full relevant checks.

## Surprises
Sandbox blocks network sockets by default; supported escalated GitHub reads and
fetch succeeded. No approval denial or uncertain mutation occurred.
- Initial artifact-verification probes wrongly expected exact timestamp string
  precision and then every PyPI wheel to be locked. PyPI has microseconds and
  additional targets beyond the existing lock. Corrected the comparison to
  validate each retained artifact and report the actual target changes above;
  no artifact hash/URL/size discrepancy or unrelated lock mutation was found.

## Next step
Commit the four intended files, publish a draft, verify full CI and report the
exact head for parent's independent review. Do not merge or mark ready.

# Quire Review Tightening Workstream

Date: 2026-04-18

Source review: `reviews/review-04-18-2026.md`

This workstream starts from a stable gate:

- Quire full suite passed: `uv run pytest` -> `52 passed`.
- Propstore full logged suite passed:
  `logs\test-runs\full-final-propstore-family-cleanup-20260418-170617.log`
  -> `2530 passed`.
- Quire tracked work was committed through `308a3b3`.
- Propstore tracked work was committed and pushed through `f14e247`.

The execution rule for every actual issue is tests first: add a test that
demonstrates the issue or locks the intended behavior, watch it fail when the
current code is wrong, then make it pass. Keep underinvested features; do not
delete a capability simply because it is currently thin.

## Slice 1: Contract Version Integrity

Actual issues:

- `VersionId` currently orders lexically.
- `allow_placeholder=False` blocks a small denylist instead of requiring a
  valid contract-version shape.
- Manifest YAML loading accepts versions through a looser path than
  programmatic declarations.
- Manifest equality is order-sensitive for unsorted contract tuples.
- Contract body normalization does not reject or normalize dataclasses and
  `msgspec.Struct` values.

Tests first:

- `VersionId("2026.04.09") < VersionId("2026.04.18")`.
- Unpadded dates, `draft`, and `tbd` are rejected when placeholders are not
  allowed.
- `ContractManifest.from_yaml(...)` rejects invalid contract and registry
  versions.
- Two manifests with the same entries in different orders compare equal.
- Contract body payloads containing dataclasses or `msgspec.Struct` normalize
  to stable builtins.

Implementation:

- Make the contract-version grammar explicit. Current Quire and Propstore
  contract versions are calendar versions of the form `YYYY.MM.DD`; keep that
  as the strict non-placeholder format.
- Replace dataclass lexical ordering with parsed calendar ordering.
- Normalize `ContractManifest.contracts` and `compatible_changes` in
  `__post_init__`.
- Normalize dataclasses and msgspec structs in `_normalize_payload`.

Propstore impact:

- Propstore already uses zero-padded date contract versions. Run focused
  contract/registry tests after changing Quire, then update Propstore's Quire
  dependency after Quire is committed and pushed.

## Slice 2: Registry-Level Contract Drift

Actual issue:

- `check_contract_manifest` only compares entries from `contracts`; it does not
  explicitly check the manifest-level registry body/version pair or surface the
  registry drift as such.

Tests first:

- Changing registry-level metadata or registry contract version without the
  corresponding registry entry bump fails clearly.
- Changing registry metadata with a registry contract-version bump reports as
  bumped.
- Existing family body drift still fails at `family:<name>`.

Implementation:

- Treat the registry declaration as a first-class checked contract entry.
  The generated `family-registry:<name>` entry remains the canonical body, but
  `check_contract_manifest` must also verify consistency between the manifest
  `registry` block and that entry.
- Preserve `CompatibilityMarker` as the explicit escape hatch.

Propstore impact:

- Regenerate and compare Propstore's semantic contract manifest if Quire's
  manifest serialization changes.

## Slice 3: Owner Protocol And Finite Policy Types

Actual issues:

- Owner branch resolution is an undocumented duck-typed protocol.
- Policy, codec, and filename mode values are unbounded strings.

Tests first:

- A minimal owner implementing `primary_branch_name` / `current_branch_name`
  satisfies branch resolution.
- Invalid branch policy, filename mode, and non-reversible codec in reversible
  placements fail at placement construction.
- Slug and safe-slug remain valid for template placements and branch templates.

Implementation:

- Add documented `OwnerLike` / `OwnerContainerLike` protocols or a small
  helper object that centralizes owner branch resolution.
- Introduce `Literal` aliases for branch policy, reversible ref codec,
  one-way ref codec, ref codec, and hash filename mode.
- Add `__post_init__` validation to placements and `BranchPlacement`.

Propstore impact:

- Propstore uses reversible flat placements and slug/safe-slug templates. It
  should require no semantic change, but run Propstore family registry tests.

## Slice 4: Placement Ref Recovery

Actual issues:

- `FlatYamlPlacement.ref_from_loaded` recovers refs by searching for the last
  repeated namespace segment in a path instead of anchoring to
  `LoadedDocument.knowledge_root`.
- `HashScatteredYamlPlacement` needs explicit round-trip coverage for
  `filename_mode="encoded_ref"` through loaded documents.

Tests first:

- A loaded document with a source path containing a duplicate namespace segment
  recovers the ref relative to `knowledge_root`, not by last-string search.
- Hash-scattered `encoded_ref` recovers from a loaded document path.
- Opaque hash-scattered digest mode still refuses path-based recovery unless
  the document itself carries the ref field.

Implementation:

- Use `knowledge_root` when available to derive a relative path.
- Fall back to the existing boundary behavior only when no root is available.

Propstore impact:

- Propstore source import and sidecar loaders should continue passing because
  they now operate through family documents.

## Slice 5: Transaction And Backend Cleanup

Actual issues:

- `DocumentFamilyTransaction.save` has unreachable branch-mismatch code.
- `DocumentFamilyStore.move` has a defensive postcondition that cannot fire.
- Quire defines two unrelated `DocumentStoreBackend` protocols for read-only
  and read-write backends.
- `prepare` should be explicitly side-effect-free.

Tests first:

- `prepare` does not add objects or commits.
- Cross-branch transaction mismatch remains enforced by the reachable check.
- Type/protocol surface has one read-only backend protocol and one read-write
  protocol inheriting from it.

Implementation:

- Delete unreachable branches and dead postconditions.
- Split protocols as `ReadOnlyDocumentStoreBackend` and
  `DocumentStoreBackend(ReadOnlyDocumentStoreBackend)`.

Propstore impact:

- No runtime behavior change expected. Run Propstore import and family tests
  after the Quire dependency update.

## Slice 6: Branch Metadata Persistence

Actual issue:

- `GitStore.create_branch` writes branch metadata only into process-local
  memory, so `GitStore.open(...)` loses parent/created metadata.

Tests first:

- Create a branch in a filesystem-backed repo, reopen it, and verify
  `list_branches()` preserves `parent_branch` and nonzero `created_at`.
- Memory-backed branch metadata still works.

Implementation:

- Persist branch metadata in git itself, using a Quire-owned ref namespace such
  as `refs/quire/branch-meta/<encoded-branch-name>` that points at a JSON blob.
- Read persisted metadata in `list_branches()`.
- Delete persisted metadata when deleting a branch.

Propstore impact:

- None expected, but run Propstore tests that exercise branch workflows if any
  public behavior changes.

## Slice 7: Git Ref And Notes Tightening

Actual issues:

- `RefName` validation is weaker than git's actual refname rules.
- `NotesRef` validates through a temporary `RefName` instead of holding one.

Tests first:

- Reject `..`, `@{`, trailing `.lock`, leading dash path components, trailing
  dots, spaces/control characters, and malformed ref names.
- `NotesRef` stores a `RefName` internally while preserving string behavior.

Implementation:

- Tighten `RefName.__post_init__` according to the relevant git refname rules
  Quire can cheaply enforce.
- Change `NotesRef` to hold a `RefName` or expose a `ref_name` property backed
  by one.

Propstore impact:

- Search Propstore for generated refs before landing. Update any invalid
  Quire-owned refs rather than loosening validation.

## Slice 8: Cross-Family Reference Integration

Actual issue:

- Foreign-key and cross-family reference primitives exist but lack an
  end-to-end test against `BoundFamilyRegistry`.

Tests first:

- Build a small registry with concepts and claims, save both families through
  `BoundFamilyRegistry`, build `ReferenceIndex` values from bound-family
  documents, and resolve a claim's concept reference through
  `CrossFamilyReferenceIndex`.

Implementation:

- Add small helper(s) only if the test reveals missing generic machinery.
- Do not move Propstore claim-reference semantics into Quire; Quire owns the
  generic FK/index primitives only.

Propstore impact:

- This should inform, not replace, Propstore's richer claim-reference resolver.

## Slice 9: Merge Plumbing Contract

Actual issue:

- `merge_base`, `ancestor_distances`, and parent inspection are real git
  plumbing features but are underdocumented and undertested for criss-cross
  merge histories.

Tests first:

- Construct a criss-cross history with `commit_flat_tree` and verify
  `merge_base` returns one deterministic best common ancestor.
- Verify parent-distance behavior on merge commits.

Implementation:

- Keep the features.
- Document that Quire exposes low-level graph plumbing for downstream semantic
  merge code, while still not providing porcelain merge resolution.
- Tighten algorithm behavior only if the criss-cross test exposes a wrong
  result.

Propstore impact:

- Propstore may rely on this for semantic merge workflows; run merge-focused
  Propstore tests after any behavior change.

## Slice 10: Codec Surface Consolidation

Design issue with real maintenance risk:

- Codec behavior is split between optional family hooks and default
  `DocumentFamilyStore` callables.

Tests first:

- Existing family-level custom codecs still override default YAML/msgspec
  behavior.
- Default codec behavior can be represented by one explicit codec object.

Implementation:

- Introduce a `DocumentCodec` protocol/object and move the default
  encode/decode/render/payload/convert operations into one object.
- Keep family-specific behavior as an explicit family codec or small override
  object. Do not delete Propstore's concept-specific normalization; it belongs
  to the concept family declaration.

Propstore impact:

- Propstore source notes, metadata, and concept documents use custom codec
  callbacks today. Update them in the same slice if the public API changes.

## Slice 11: Performance Boundaries

Actual concern:

- Commits rebuild full trees from flat dictionaries, and tree flatten/build
  helpers recurse. This is acceptable for current tests but not for large
  semantic stores.

Tests first:

- Add regression tests for deep paths that avoid recursion-limit failure at a
  practical depth.
- Add a test that a single-file update preserves unrelated tree contents.

Implementation:

- If a small iterative flatten/build replacement is straightforward, land it.
- If path-update tree surgery is larger than this review tightening pass,
  write an explicit TODO tied to the test coverage and keep the behavior
  correct.

Propstore impact:

- None expected unless tree update semantics change.

## Execution Order

Execute slices in order. After each Quire slice:

1. Run the targeted Quire tests for that slice.
2. Run `uv run pytest` in Quire after each substantial group.
3. If public API, manifest serialization, ref validation, or placement behavior
   changed, update Propstore's Quire dependency and run focused Propstore tests.
4. Commit frequently after green tests.

Do not start from the review's "smaller items" by polishing names. Fix the
contract and placement correctness issues first because Propstore depends on
them as persisted ABI guarantees.

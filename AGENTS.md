# Quire Working Principles

Quire is the generic typed Git/document substrate. It must stay independent of
propstore-specific semantics.

## Storage Boundary

- Quire owns generic Git object/ref/note mechanics, typed tree access, document
  codecs, artifact family declarations, family transactions, and storage-format
  contracts.
- Quire must not import propstore or learn about propstore concepts, claims,
  source promotion, sidecars, command names, or undo policy semantics.
- Propstore and other consumers own their application commands, domain schemas,
  semantic families, workflows, and user-facing policies.

## Iterator-First APIs

- Any quire API that enumerates refs, branches, tree entries, artifacts, notes,
  commits, or other potentially unbounded storage surfaces must be lazy by
  default.
- Prefer `iter_*` names and `Iterator[...]` returns.
- Do not add `list_*` APIs or eager `.list()` family APIs.
- Callers that need materialized collections must make that explicit with
  `list(...)`, `tuple(...)`, `sorted(...)`, or another local collection step.
- Do not hide scans behind point-operation names.

## Transaction Safety

- Generic write APIs should support explicit expected-head checks where a
  branch tip may move between planning and commit.
- Branch-head mismatches must fail before writing a new commit.
- Multi-artifact transactions should retain a single target branch and fail on
  accidental cross-branch writes.

## Git Object Semantics

- Tree-entry existence checks must agree with tree walking.
- Use the repository object-loading helper for object IDs before classifying an
  entry. Do not infer file/directory semantics from raw tree tuples alone.
- Non-file tree entries, including submodule commit entries, are not document
  artifacts unless the typed tree walker would expose them.

## Value Objects

- Value objects should follow Python comparison contracts. Comparisons against
  unsupported types must return `NotImplemented`, not raise or guess.
- `VersionId` is an opaque version identifier, not a calendar parser surface.
  Do not add parsing helpers unless a generic storage contract requires them.

## Revert Support

- Quire may provide generic Git revert/inverse-commit primitives.
- Quire revert code must operate on commits, trees, refs, paths, and object IDs
  only. It must not encode application-specific undo policy.
- Application layers decide whether a command is undoable, compensatable,
  rebuildable, or non-undoable.

## No Compatibility Bridges

- When replacing a generic interface, delete the old production surface and
  update callers.
- Do not add aliases, fallback wrappers, or dual paths unless an external
  compatibility target is explicitly required.

# Quire Working Principles

Follow `AGENTS.md` in this repository.

The short version:

- Quire is a generic typed Git/document substrate, not a propstore application
  layer.
- Enumeration APIs must be lazy and named `iter_*`; do not add `list_*` or eager
  `.list()` surfaces.
- Writers that depend on a branch tip should accept expected-head checks and
  fail before committing if the branch moved.
- Tree-entry existence checks must use repository object loading and agree with
  tree walking; raw tree tuple modes are not enough.
- Value object comparisons, including `VersionId`, return `NotImplemented` for
  unsupported operand types.
- Generic revert support belongs in quire only at the Git/tree/ref level.
- Application command policy, undo policy, command journals, and semantic
  workflows belong in consumers such as propstore.
- Storage artifact identity and cross-family references belong in Quire family
  declarations and FK validation, not ad hoc consumer maps.
- Replace interfaces directly; do not keep compatibility aliases or bridge
  wrappers.

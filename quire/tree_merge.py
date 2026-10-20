"""Base-relative reconciliation of exact, keyed Git tree-entry versions."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass


@dataclass
class TreeMergeConflict(ValueError):
    path: str
    base_sha: str | None
    left_sha: str | None
    right_sha: str | None

    def __str__(self) -> str:
        return (
            f"tree merge conflict at {self.path!r}: base {self.base_sha}, "
            f"left {self.left_sha}, right {self.right_sha}"
        )


def iter_merged_tree_entries(
    base: Mapping[str, str],
    left: Mapping[str, str],
    right: Mapping[str, str],
) -> Iterator[tuple[str, str]]:
    """Reconcile immutable snapshots, treating a missing path as absence.

    Propagate a one-sided change relative to the common ancestor. Equal current
    object IDs converge; different concurrent changes conflict. Paths and object
    IDs remain exact, with final tree-shape validation owned by the publisher.
    """
    for path in sorted(base.keys() | left.keys() | right.keys()):
        ancestor, ours, theirs = base.get(path), left.get(path), right.get(path)
        if ours == theirs:
            selected = ours
        elif ours == ancestor:
            selected = theirs
        elif theirs == ancestor:
            selected = ours
        else:
            raise TreeMergeConflict(path, ancestor, ours, theirs)
        if selected is not None:
            yield path, selected

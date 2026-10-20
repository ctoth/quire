from __future__ import annotations

import pytest

from quire.git_store import GitStore


ALIASES = [
    ("data/ni:///hash.yaml", "data/ni:/hash.yaml", "data/ni:/hash.yaml"),
    ("data/./item.yaml", "data/item.yaml", "data/item.yaml"),
    ("data\\item.yaml", "/data/item.yaml/", "data/item.yaml"),
]


@pytest.mark.parametrize(("first", "second", "canonical"), ALIASES)
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("existing_target", [False, True])
def test_flat_tree_rejects_different_blobs_at_one_physical_path(
    first: str, second: str, canonical: str, reverse: bool, existing_target: bool,
) -> None:
    store = GitStore.init_memory()
    parent = store.commit_files({"kept.txt": b"original"}, "source", branch="source")
    if existing_target:
        store.create_branch("result", source_commit=parent)
    before = store.branch_sha("result")
    pairs = [(first, store.store_blob(b"first evidence")), (second, store.store_blob(b"second evidence"))]
    if reverse:
        pairs.reverse()
    with pytest.raises(ValueError, match="different blobs for normalized path") as caught:
        store.commit_flat_tree(dict(pairs), "collision", parents=[parent], branch="result", expected_head=before)
    assert canonical in str(caught.value)
    assert store.branch_sha("result") == before
    assert store.branch_sha("source") == parent
    assert store.read_file("kept.txt", commit=parent) == b"original"


@pytest.mark.parametrize(("first", "second", "canonical"), ALIASES)
def test_flat_tree_coalesces_equal_blobs_at_one_physical_path(
    first: str, second: str, canonical: str,
) -> None:
    store = GitStore.init_memory()
    blob = store.store_blob(b"same evidence")
    commit = store.commit_flat_tree({first: blob, second: blob}, "equal images", parents=[], branch="result")
    assert tuple(store.iter_flat_tree_entries(commit)) == ((canonical, blob),)
    assert store.read_file(canonical, commit=commit) == b"same evidence"

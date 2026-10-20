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


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("existing_target", [False, True])
@pytest.mark.parametrize("same_blob", [False, True])
def test_flat_tree_rejects_file_directory_prefix_collisions(
    reverse: bool, existing_target: bool, same_blob: bool,
) -> None:
    store = GitStore.init_memory()
    parent = store.commit_files({"kept.txt": b"original"}, "source", branch="source")
    if existing_target:
        store.create_branch("result", source_commit=parent)
    before = store.branch_sha("result")
    file_blob = store.store_blob(b"file evidence")
    child_blob = file_blob if same_blob else store.store_blob(b"child evidence")
    pairs = [("data/item.yaml", file_blob), ("data/item.yaml/child.yaml", child_blob)]
    if reverse:
        pairs.reverse()
    try:
        commit = store.commit_flat_tree(dict(pairs), "prefix collision", parents=[parent], branch="result", expected_head=before)
    except ValueError as exc:
        assert "path conflict at data/item.yaml" in str(exc)
        assert store.branch_sha("result") == before
    else:
        actual = tuple(store.iter_flat_tree_entries(commit))
        pytest.fail(f"prefix collision advanced ref to {commit}; actual committed entries={actual!r}")
    assert store.branch_sha("source") == parent
    assert store.read_file("kept.txt", commit=parent) == b"original"


@pytest.mark.parametrize("reverse", [False, True])
def test_flat_tree_accepts_legitimate_neighboring_paths(reverse: bool) -> None:
    store = GitStore.init_memory()
    pairs = [
        ("data/item.yaml", store.store_blob(b"file evidence")),
        ("data/item.yaml-neighbor/child.yaml", store.store_blob(b"neighbor evidence")),
        ("data/item.yaml2", store.store_blob(b"another neighbor")),
    ]
    if reverse:
        pairs.reverse()
    commit = store.commit_flat_tree(dict(pairs), "neighbors", parents=[], branch="result")
    assert dict(store.iter_flat_tree_entries(commit)) == dict(pairs)

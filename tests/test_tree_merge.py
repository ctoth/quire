import pytest

from quire.git_store import GitStore
from quire.tree_merge import TreeMergeConflict, iter_merged_tree_entries


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("base", "left", "right", "expected", "conflict"),
    [
        (None, None, None, None, False),
        ("base", "base", "base", "base", False),
        (None, "new", None, "new", False),
        (None, "new", "new", "new", False),
        (None, "ours", "theirs", None, True),
        ("base", None, "base", None, False),
        ("base", None, None, None, False),
        ("base", "new", "base", "new", False),
        ("base", "new", "new", "new", False),
        ("base", "ours", "theirs", None, True),
        ("base", "new", None, None, True),
    ],
)
def test_base_relative_path_states_are_symmetric(base, left, right, expected, conflict, reverse):
    if reverse:
        left, right = right, left
    snapshots = tuple({} if value is None else {"nested/item.yaml": value} for value in (base, left, right))
    before = tuple(dict(snapshot) for snapshot in snapshots)
    if conflict:
        with pytest.raises(TreeMergeConflict) as caught:
            dict(iter_merged_tree_entries(*snapshots))
        assert caught.value.path == "nested/item.yaml"
        assert (caught.value.base_sha, caught.value.left_sha, caught.value.right_sha) == (base, left, right)
    else:
        merged = dict(iter_merged_tree_entries(*snapshots))
        assert merged == ({} if expected is None else {"nested/item.yaml": expected})
    assert snapshots == before


def test_enumeration_is_lazy_and_order_is_independent_of_insertion():
    iterator = iter_merged_tree_entries({}, {"bad": "ours"}, {"bad": "theirs"})
    with pytest.raises(TreeMergeConflict):
        next(iterator)
    assert tuple(iter_merged_tree_entries({}, {"z": "z", "a": "a"}, {"m": "m"})) == (("a", "a"), ("m", "m"), ("z", "z"))


def test_real_tree_merge_retains_history_and_publishes_exact_selected_versions():
    store = GitStore.init_memory()
    base = store.commit_files({"keep": b"same", "remove": b"old", "update": b"old"}, "base")
    store.create_branch("peer", source_commit=base)
    left = store.commit_batch({"left": b"left", "update": b"new"}, ["remove"], "left")
    right = store.commit_files({"right": b"right"}, "right", branch="peer")
    entries = dict(iter_merged_tree_entries(*(dict(store.iter_flat_tree_entries(commit)) for commit in (base, left, right))))
    merged = store.commit_flat_tree(entries, "merge", parents=[left, right], branch="result", expected_heads={"master": left, "peer": right, "result": None})
    assert {path: store.read_file(path, commit=merged) for path in entries} == {"keep": b"same", "update": b"new", "left": b"left", "right": b"right"}
    assert store.read_file("remove", commit=base) == b"old"
    assert store.exists("remove", commit=merged) is None
    assert list(store.iter_commit_parent_shas(merged)) == [left, right]
    assert (store.branch_sha("master"), store.branch_sha("peer")) == (left, right)


def test_real_modify_delete_conflict_has_exact_object_witnesses_without_writes():
    store = GitStore.init_memory()
    base = store.commit_files({"item": b"base"}, "base")
    store.create_branch("peer", source_commit=base)
    left = store.commit_files({"item": b"changed"}, "modify")
    right = store.commit_deletes(["item"], "delete", branch="peer")
    before = set(store.raw_repo.object_store)
    with pytest.raises(TreeMergeConflict) as caught:
        dict(iter_merged_tree_entries(*(dict(store.iter_flat_tree_entries(commit)) for commit in (base, left, right))))
    assert caught.value.path == "item"
    assert caught.value.base_sha == dict(store.iter_flat_tree_entries(base))["item"]
    assert caught.value.left_sha == dict(store.iter_flat_tree_entries(left))["item"]
    assert caught.value.right_sha is None
    assert set(store.raw_repo.object_store) == before


def test_selected_entries_still_pass_existing_tree_prefix_admission():
    store = GitStore.init_memory()
    base = store.commit_files({"keep": b"same"}, "base")
    store.create_branch("peer", source_commit=base)
    left = store.commit_files({"data": b"file"}, "left")
    right = store.commit_files({"data/child": b"child"}, "right", branch="peer")
    entries = dict(iter_merged_tree_entries(*(dict(store.iter_flat_tree_entries(commit)) for commit in (base, left, right))))
    before = set(store.raw_repo.object_store)
    with pytest.raises(ValueError, match="path conflict"):
        store.commit_flat_tree(entries, "invalid shape", parents=[left, right], branch="result", expected_heads={"result": None})
    assert store.branch_sha("result") is None
    assert set(store.raw_repo.object_store) == before

"""Named branch expectations retain absence and guard captured source snapshots."""

import pytest

from quire import git_store
from quire.git_store import GitStore, HeadMismatchError


@pytest.fixture(params=["memory", "disk"])
def store(request, tmp_path):
    return GitStore.init_memory() if request.param == "memory" else GitStore.init(tmp_path / "repo")


def test_absent_target_lease_rejects_a_new_branch_before_objects(store):
    parent = store.commit_files({"kept": b"parent"}, "parent")
    concurrent = store.commit_files({"other": b"concurrent"}, "concurrent", branch="result")
    before = set(store.raw_repo.object_store)
    with pytest.raises(HeadMismatchError) as caught:
        store.commit_flat_tree({}, "stale", parents=[parent], branch="result", expected_heads={"result": None})
    assert caught.value.branch == "result"
    assert caught.value.expected_head is None
    assert caught.value.actual_head == concurrent
    assert store.branch_sha("result") == concurrent
    assert set(store.raw_repo.object_store) == before


@pytest.mark.parametrize("deleted", [False, True])
def test_source_lease_rejects_moved_or_deleted_source_before_objects(store, deleted):
    parent = store.commit_files({"kept": b"parent"}, "parent")
    store.create_branch("source", source_commit=parent)
    if deleted:
        store.delete_branch("source")
        actual = None
    else:
        actual = store.commit_files({"late": b"late"}, "late", branch="source")
    before = set(store.raw_repo.object_store)
    with pytest.raises(HeadMismatchError) as caught:
        store.commit_flat_tree({}, "stale", parents=[parent], branch="result", expected_heads={"source": parent, "result": None})
    assert caught.value.branch == "source"
    assert caught.value.expected_head == parent
    assert caught.value.actual_head == actual
    assert store.branch_sha("result") is None
    assert set(store.raw_repo.object_store) == before


def test_source_lease_is_rechecked_after_entry_normalization(store):
    parent = store.commit_files({"kept": b"parent"}, "parent")
    store.create_branch("source", source_commit=parent)
    blob = store.store_blob(b"candidate")
    before = set()
    actual = None

    class RacingEntries(dict):
        def items(self):
            nonlocal before, actual
            yield from super().items()
            actual = store.commit_files({"late": b"late"}, "late", branch="source")
            before = set(store.raw_repo.object_store)

    with pytest.raises(HeadMismatchError) as caught:
        store.commit_flat_tree(RacingEntries({"candidate": blob}), "stale", parents=[parent], branch="result", expected_heads={"source": parent, "result": None})
    assert caught.value.actual_head == actual
    assert store.branch_sha("result") is None
    assert set(store.raw_repo.object_store) == before


def test_target_creation_during_compare_and_swap_cannot_be_overwritten(store, monkeypatch):
    parent = store.commit_files({"kept": b"parent"}, "parent")
    original = git_store._ref_set_if_equals

    def interleave(refs, name, old, new):
        if name == b"refs/heads/result":
            assert original(refs, name, None, parent.encode("ascii"))
        return original(refs, name, old, new)

    monkeypatch.setattr(git_store, "_ref_set_if_equals", interleave)
    with pytest.raises(HeadMismatchError) as caught:
        store.commit_flat_tree({}, "stale", parents=[parent], branch="result", expected_heads={"result": None})
    assert caught.value.expected_head is None
    assert caught.value.actual_head == parent
    assert store.branch_sha("result") == parent


def test_valid_leases_allow_exact_tree_and_parent_publication(store):
    left = store.commit_files({"left": b"left"}, "left")
    store.create_branch("peer", source_commit=left)
    right = store.commit_files({"right": b"right"}, "right", branch="peer")
    entries = dict(store.iter_flat_tree_entries(right))
    merged = store.commit_flat_tree(entries, "merge", parents=[left, right], branch="result", expected_heads={"master": left, "peer": right, "result": None})
    assert store.branch_sha("result") == merged
    assert dict(store.iter_flat_tree_entries(merged)) == entries
    assert list(store.iter_commit_parent_shas(merged)) == [left, right]
    assert (store.branch_sha("master"), store.branch_sha("peer")) == (left, right)


def test_conflicting_target_expectations_reject_before_objects(store):
    parent = store.commit_files({"kept": b"parent"}, "parent")
    before = set(store.raw_repo.object_store)
    with pytest.raises(ValueError, match="conflicting expected heads"):
        store.commit_flat_tree({}, "invalid", parents=[parent], expected_head=parent, expected_heads={"master": None})
    assert set(store.raw_repo.object_store) == before


def test_merge_base_accepts_captured_commits_after_refs_move(store):
    base = store.commit_files({"base": b"base"}, "base")
    store.create_branch("peer", source_commit=base)
    left = store.commit_files({"left": b"left"}, "left")
    right = store.commit_files({"right": b"right"}, "right", branch="peer")
    store.commit_files({"later": b"later"}, "later")
    assert store.merge_base(left, right) == base
    assert store.merge_base("master", right) == base
    assert store.merge_base(left, left) == left
    store.create_branch(left, source_commit=right)
    assert store.merge_base(left, left) == left
    with pytest.raises(ValueError, match="does not exist"):
        store.merge_base("missing", right)

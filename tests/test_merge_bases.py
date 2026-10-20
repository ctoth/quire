"""Content planners must be able to observe every best common ancestor."""

import pytest

from quire import git_store
from quire.git_store import GitStore


@pytest.fixture(params=["memory", "disk"])
def store(request, tmp_path):
    return GitStore.init_memory() if request.param == "memory" else GitStore.init(tmp_path / "repo")


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("use_names", [False, True])
def test_all_criss_cross_bases_are_visible_without_writes(store, reverse, use_names):
    origin = store.commit_files({"item": b"O"}, "origin")
    store.create_branch("left", source_commit=origin)
    store.create_branch("right", source_commit=origin)
    a = store.commit_files({"item": b"A"}, "A", branch="left")
    b = store.commit_files({"item": b"B"}, "B", branch="right")
    left = store.commit_flat_tree(dict(store.iter_flat_tree_entries(a)), "retain A", parents=[a, b], branch="left")
    right = store.commit_flat_tree(dict(store.iter_flat_tree_entries(b)), "retain B", parents=[b, a], branch="right")
    inputs = ("left", "right") if use_names else (left, right)
    if reverse:
        inputs = inputs[::-1]
    refs_before = store.raw_repo.refs.as_dict()
    objects_before = set(store.raw_repo.object_store)
    assert tuple(store.iter_merge_bases(*inputs)) == tuple(sorted((a, b)))
    assert store.merge_base(*inputs) == min(a, b)
    assert store.raw_repo.refs.as_dict() == refs_before
    assert set(store.raw_repo.object_store) == objects_before


def test_unique_bases_respect_pinned_ids_and_literal_branch_names(store):
    origin = store.commit_files({"item": b"O"}, "origin")
    store.create_branch("HEAD", source_commit=origin)
    left = store.commit_files({"item": b"A"}, "A")
    store.create_branch(left, source_commit=origin)
    assert tuple(store.iter_merge_bases(left, left)) == (left,)
    assert tuple(store.iter_merge_bases("HEAD", left)) == (origin,)
    assert tuple(store.iter_merge_bases("master", left)) == (left,)
    store.commit_files({"item": b"later"}, "later")
    assert tuple(store.iter_merge_bases(left, left)) == (left,)


def test_unrelated_roots_have_no_bases_and_point_query_still_refuses(store):
    left = store.commit_flat_tree({"item": store.store_blob(b"A")}, "A", parents=[], branch="left")
    right = store.commit_flat_tree({"item": store.store_blob(b"B")}, "B", parents=[], branch="right")
    assert tuple(store.iter_merge_bases(left, right)) == ()
    with pytest.raises(ValueError, match="No common ancestor"):
        store.merge_base(left, right)


def test_unknown_input_is_rejected_during_enumeration(store):
    base = store.commit_files({"item": b"O"}, "origin")
    iterator = store.iter_merge_bases("missing", base)
    with pytest.raises(ValueError, match="does not exist"):
        next(iterator)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("timestamps", [(100, 100, 100, 100), (100, 100, 100, 101), (300, 100, 200, 400), (100, 200, 300, 400)])
def test_inferior_candidate_is_removed_by_ancestry_not_clock(store, monkeypatch, reverse, timestamps):
    clock = timestamps[0]
    monkeypatch.setattr(git_store.time, "time", lambda: clock)
    for attempt in range(128):
        clock = timestamps[0]
        p2 = store.commit_files({"item": b"P2"}, f"P2 {attempt}")
        clock = timestamps[1]
        p1 = store.commit_files({"item": b"P1"}, f"P1 {attempt}")
        clock = timestamps[2]
        q = store.commit_files({"item": b"Q"}, f"Q {attempt}")
        if p2 < q < p1:
            break
    else:
        pytest.fail("fixed-time commit messages did not produce the regression SHA ordering")
    clock = timestamps[3]
    entries = dict(store.iter_flat_tree_entries(q))
    left = store.commit_flat_tree(entries, "L", parents=[q, p2], branch="left")
    right = store.commit_flat_tree(entries, "R", parents=[q, p2], branch="right")
    inputs = (right, left) if reverse else (left, right)
    refs_before = store.raw_repo.refs.as_dict()
    objects_before = set(store.raw_repo.object_store)
    assert p2 in store.ancestor_distances(q)
    assert tuple(store.iter_merge_bases(*inputs)) == (q,)
    assert store.merge_base(*inputs) == q
    assert store.raw_repo.refs.as_dict() == refs_before
    assert set(store.raw_repo.object_store) == objects_before

from __future__ import annotations

from typing import Any, cast

import pytest
from dulwich.objects import Blob, Commit
from dulwich.repo import MemoryRepo

from quire.git_store import GitStore, GitStorePolicy
from quire.notes import NotesRef, read_git_note, remove_git_note, write_git_note
from quire.refs import RefName


def test_init_seeds_gitignore_without_materializing_worktree(tmp_path):
    root = tmp_path / "repo"
    store = GitStore.init(root, policy=GitStorePolicy(initial_files={".gitignore": b"cache/\n"}))

    assert (root / ".git").is_dir()
    assert not (root / ".gitignore").exists()
    assert store.read_file(".gitignore") == b"cache/\n"


def test_commit_and_read_are_object_store_operations(tmp_path):
    root = tmp_path / "repo"
    store = GitStore.init(root)

    sha = store.commit_files({"docs/example.yaml": b"name: demo\n"}, "add example")

    assert len(sha) == 40
    assert store.read_file("docs/example.yaml") == b"name: demo\n"
    assert not (root / "docs" / "example.yaml").exists()


def test_deep_tree_paths_do_not_depend_on_python_recursion_limit():
    store = GitStore.init_memory()
    deep_path = "/".join(f"d{index}" for index in range(1100)) + "/leaf.txt"

    commit = store.commit_files({deep_path: b"leaf"}, "add deep leaf")

    assert store.read_file(deep_path, commit=commit) == b"leaf"
    assert store.flat_tree_entries(commit)[deep_path]


def test_single_file_update_preserves_unrelated_tree_contents():
    store = GitStore.init_memory()
    first = store.commit_files(
        {
            "a/one.txt": b"one",
            "b/two.txt": b"two",
        },
        "seed",
    )

    second = store.commit_files({"a/one.txt": b"changed"}, "update one")

    assert store.read_file("a/one.txt", commit=second) == b"changed"
    assert store.read_file("b/two.txt", commit=second) == b"two"
    assert store.read_file("a/one.txt", commit=first) == b"one"


def test_materialize_worktree_can_remove_stale_files_and_preserve_runtime_paths(tmp_path):
    root = tmp_path / "repo"
    store = GitStore.init(
        root,
        policy=GitStorePolicy(
            ignored_path_prefixes=("sidecar/",),
            ignored_path_suffixes=(".sqlite", ".hash"),
        ),
    )
    store.commit_files({"docs/example.yaml": b"name: demo\n"}, "add example")
    store.materialize_worktree(remove_extra=True)

    assert (root / "docs" / "example.yaml").read_bytes() == b"name: demo\n"

    sidecar = root / "sidecar" / "cache.sqlite"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b"cache")
    stale = root / "docs" / "stale.yaml"
    stale.write_bytes(b"old")

    store.commit_deletes(["docs/example.yaml"], "remove example")
    store.materialize_worktree(remove_extra=True)

    assert not (root / "docs" / "example.yaml").exists()
    assert not stale.exists()
    assert sidecar.read_bytes() == b"cache"


def test_diff_and_show_commit_report_tree_changes():
    store = GitStore.init_memory()
    first = store.commit_files({"a.txt": b"one", "b.txt": b"two"}, "first")
    second = store.commit_batch({"a.txt": b"changed", "c.txt": b"three"}, ["b.txt"], "second")

    assert store.diff_commits(second, first) == {
        "added": ["c.txt"],
        "modified": ["a.txt"],
        "deleted": ["b.txt"],
    }

    shown = store.show_commit(second)
    assert shown["sha"] == second
    assert shown["message"] == "second"
    assert shown["added"] == ["c.txt"]
    assert shown["modified"] == ["a.txt"]
    assert shown["deleted"] == ["b.txt"]


def test_refs_read_write_delete_round_trip():
    store = GitStore.init_memory()
    sha = store.commit_files({"a.txt": b"a"}, "add a")
    ref = RefName("refs/generated/example")

    store.write_ref(ref, sha)

    assert store.read_ref(ref) == sha
    store.delete_ref(ref)
    assert store.read_ref(ref) is None


def test_blob_refs_store_derived_index_payloads():
    store = GitStore.init_memory()
    ref = RefName("refs/quire/indexes/example")

    blob_sha = store.write_blob_ref(ref, b"8\n")

    assert store.read_ref(ref) == blob_sha
    assert store.read_blob_ref(ref) == b"8\n"
    store.delete_ref(ref)
    assert store.read_blob_ref(ref) is None


def test_flat_tree_entries_and_commit_flat_tree_create_merge_commit():
    store = GitStore.init_memory()
    left = store.commit_files({"a.txt": b"left"}, "left", branch="left")
    right = store.commit_files({"b.txt": b"right"}, "right", branch="right")

    entries = store.flat_tree_entries(right)
    entries.update(store.flat_tree_entries(left))
    entries["merged.txt"] = store.store_blob(b"merged")

    merge = store.commit_flat_tree(
        entries,
        "merge branches",
        parents=[left, right],
        branch="merged",
    )

    merge_obj = store.raw_repo[merge.encode("ascii")]
    assert isinstance(merge_obj, Commit)
    assert merge_obj.parents == [left.encode("ascii"), right.encode("ascii")]
    assert store.branch_sha("merged") == merge
    assert store.read_file("a.txt", commit=merge) == b"left"
    assert store.read_file("b.txt", commit=merge) == b"right"
    assert store.read_file("merged.txt", commit=merge) == b"merged"


def test_merge_base_is_deterministic_for_criss_cross_history():
    store = GitStore.init_memory()
    base = store.commit_files({"base.txt": b"base"}, "base")
    store.create_branch("left", source_commit=base)
    store.create_branch("right", source_commit=base)
    left = store.commit_files({"left.txt": b"left"}, "left", branch="left")
    right = store.commit_files({"right.txt": b"right"}, "right", branch="right")

    left_entries = store.flat_tree_entries(left)
    left_entries.update(store.flat_tree_entries(right))
    left_entries["left-merge.txt"] = store.store_blob(b"left merge")
    left_merge = store.commit_flat_tree(
        left_entries,
        "merge right into left",
        parents=[left, right],
        branch="left",
    )

    right_entries = store.flat_tree_entries(right)
    right_entries.update(store.flat_tree_entries(left))
    right_entries["right-merge.txt"] = store.store_blob(b"right merge")
    right_merge = store.commit_flat_tree(
        right_entries,
        "merge left into right",
        parents=[right, left],
        branch="right",
    )

    assert store.commit_parent_shas(left_merge) == [left, right]
    assert store.commit_parent_shas(right_merge) == [right, left]
    assert store.ancestor_distances(left_merge)[right] == 1
    assert store.ancestor_distances(right_merge)[left] == 1
    assert store.merge_base("left", "right") == min(left, right)


def test_branch_operations_track_refs_and_merge_base():
    store = GitStore.init_memory()
    base = store.commit_files({"base.txt": b"base"}, "base")

    assert store.create_branch("left") == base
    assert store.create_branch("right", source_commit=base) == base

    left = store.commit_files({"left.txt": b"left"}, "left", branch="left")
    right = store.commit_files({"right.txt": b"right"}, "right", branch="right")

    branches = {branch.name: branch for branch in store.iter_branches()}
    assert branches["left"].tip_sha == left
    assert branches["left"].parent_branch == "master"
    assert branches["right"].tip_sha == right
    assert store.commit_parent_shas(left) == [base]
    assert store.merge_base("left", "right") == base

    store.set_current_branch("left")
    assert store.current_branch_name() == "left"

    store.delete_branch("right")
    assert store.branch_sha("right") is None


def test_branch_metadata_survives_reopening_filesystem_repo(tmp_path):
    root = tmp_path / "repo"
    store = GitStore.init(root)
    base = store.commit_files({"base.txt": b"base"}, "base")

    assert store.create_branch("feature") == base

    reopened = GitStore.open(root)
    branches = {branch.name: branch for branch in reopened.iter_branches()}

    assert branches["feature"].tip_sha == base
    assert branches["feature"].parent_branch == "master"
    assert branches["feature"].created_at > 0


def test_commit_batch_rejects_moved_branch_head():
    store = GitStore.init_memory()
    first = store.commit_files({"a.txt": b"one"}, "first")
    store.commit_files({"a.txt": b"two"}, "second")

    with pytest.raises(ValueError, match="head mismatch"):
        store.commit_batch(
            {"b.txt": b"three"},
            [],
            "stale write",
            expected_head=first,
        )


def test_commit_flat_tree_rejects_moved_branch_head():
    store = GitStore.init_memory()
    first = store.commit_files({"a.txt": b"one"}, "first")
    second = store.commit_files({"a.txt": b"two"}, "second")
    blob_sha = store.store_blob(b"flat")

    with pytest.raises(ValueError, match="head mismatch"):
        store.commit_flat_tree(
            {"b.txt": blob_sha},
            "stale flat tree",
            parents=[first],
            expected_head=first,
        )

    assert store.head_sha() == second
    with pytest.raises(FileNotFoundError):
        store.read_file("b.txt")


def test_revert_commit_creates_inverse_commit():
    store = GitStore.init_memory()
    first = store.commit_files(
        {
            "keep.txt": b"keep",
            "modify.txt": b"before",
            "delete.txt": b"delete",
        },
        "first",
    )
    second = store.commit_batch(
        {
            "modify.txt": b"after",
            "add.txt": b"add",
        },
        ["delete.txt"],
        "second",
    )

    reverted = store.revert_commit(second)

    assert store.commit_parent_shas(reverted) == [second]
    assert store.read_file("keep.txt") == b"keep"
    assert store.read_file("modify.txt") == b"before"
    assert store.read_file("delete.txt") == b"delete"
    with pytest.raises(FileNotFoundError):
        store.read_file("add.txt")
    assert first in store.ancestor_distances(reverted)


def test_revert_commit_rejects_conflicting_later_change():
    store = GitStore.init_memory()
    store.commit_files({"a.txt": b"one"}, "first")
    second = store.commit_files({"a.txt": b"two"}, "second")
    store.commit_files({"a.txt": b"three"}, "third")

    with pytest.raises(ValueError, match="has changed"):
        store.revert_commit(second)


def test_delete_branch_removes_persisted_branch_metadata():
    store = GitStore.init_memory()
    store.commit_files({"base.txt": b"base"}, "base")
    store.create_branch("feature")

    store.delete_branch("feature")

    assert store.read_blob_ref(RefName("refs/quire/branch-meta/feature")) is None


def test_notes_round_trip_against_arbitrary_notes_ref():
    store = GitStore.init_memory()
    blob = Blob.from_string(b"payload")
    store.raw_repo.object_store.add_object(blob)
    notes_ref = NotesRef("refs/notes/quire-test")

    note_commit = store.write_note(notes_ref, blob.id.decode("ascii"), b"note payload")

    assert note_commit == store.read_ref(RefName("refs/notes/quire-test"))
    assert store.read_note(notes_ref, blob.id.decode("ascii")) == b"note payload"
    store.delete_note(notes_ref, blob.id.decode("ascii"))
    assert store.read_note(notes_ref, blob.id.decode("ascii")) is None


def test_free_note_helpers_work_with_plain_dulwich_repo():
    repo = MemoryRepo()
    blob = Blob.from_string(b"payload")
    repo.object_store.add_object(blob)
    notes_ref = NotesRef("refs/notes/helper-test")

    note_commit = write_git_note(
        repo,
        notes_ref,
        blob.id,
        b"helper payload",
        author=b"tester <tester@example.com>",
        message=b"Record helper note",
    )

    assert repo.refs[cast(Any, notes_ref.as_bytes())] == note_commit
    assert read_git_note(repo, notes_ref, blob.id) == b"helper payload"
    assert remove_git_note(repo, notes_ref, blob.id) is not None
    assert read_git_note(repo, notes_ref, blob.id) is None

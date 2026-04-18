from __future__ import annotations

from dulwich.objects import Blob
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

    assert repo.refs[notes_ref.as_bytes()] == note_commit
    assert read_git_note(repo, notes_ref, blob.id) == b"helper payload"
    assert remove_git_note(repo, notes_ref, blob.id) is not None
    assert read_git_note(repo, notes_ref, blob.id) is None

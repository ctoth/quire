from __future__ import annotations

from dulwich.objects import Blob

from quire.git_store import GitStore, GitStorePolicy
from quire.notes import NotesRef
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


def test_refs_read_write_delete_round_trip():
    store = GitStore.init_memory()
    sha = store.commit_files({"a.txt": b"a"}, "add a")
    ref = RefName("refs/generated/example")

    store.write_ref(ref, sha)

    assert store.read_ref(ref) == sha
    store.delete_ref(ref)
    assert store.read_ref(ref) is None


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

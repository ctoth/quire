from __future__ import annotations

import multiprocessing as mp
import threading
from pathlib import Path
from typing import Any, cast
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
from dulwich.errors import NotGitRepository
from dulwich.server import DictBackend
from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import MemoryRepo
from dulwich.web import make_wsgi_chain

import quire.git_store as git_store
from quire.git_store import GitStore, GitStorePolicy, HeadMismatchError, MaterializeConflictError
from quire.notes import NotesRef, read_git_note, remove_git_note, write_git_note
from quire.refs import RefName


def _multiprocess_commit_worker(
    root: str,
    worker: int,
    count: int,
    queue: mp.Queue,
) -> None:
    try:
        store = GitStore.open(Path(root))
        for index in range(count):
            store.commit_files(
                {f"workers/{worker}/{index}.txt": f"{worker}:{index}".encode("ascii")},
                f"worker {worker} commit {index}",
            )
        queue.put(("ok", worker))
    except Exception as exc:
        queue.put(("error", worker, type(exc).__name__, str(exc)))


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


def test_head_bound_transaction_captures_head_and_passes_expected_head(monkeypatch):
    store = GitStore.init_memory()
    first = store.commit_files({"seed.txt": b"seed"}, "seed")
    calls: list[tuple[str | None, str | None]] = []
    original_commit_batch = store.commit_batch

    def recording_commit_batch(
        adds,
        deletes,
        message,
        *,
        branch=None,
        expected_head=None,
    ):
        calls.append((branch, expected_head))
        return original_commit_batch(
            adds,
            deletes,
            message,
            branch=branch,
            expected_head=expected_head,
        )

    monkeypatch.setattr(store, "commit_batch", recording_commit_batch)

    with store.head_bound_transaction("master") as transaction:
        commit = transaction.commit_batch({"next.txt": b"next"}, [], "next")

    assert len(commit) == 40
    assert calls == [("master", first)]


def test_head_bound_transaction_rejects_stale_head_with_typed_error():
    store = GitStore.init_memory()
    first = store.commit_files({"seed.txt": b"seed"}, "seed")

    with store.head_bound_transaction("master") as transaction:
        store.commit_files({"raced.txt": b"raced"}, "raced")
        with pytest.raises(HeadMismatchError) as excinfo:
            transaction.commit_batch({"next.txt": b"next"}, [], "next")

    assert excinfo.value.branch == "master"
    assert excinfo.value.expected_head == first
    assert excinfo.value.actual_head == store.branch_sha("master")


def test_head_bound_transaction_assert_current_rejects_moved_head():
    store = GitStore.init_memory()
    first = store.commit_files({"seed.txt": b"seed"}, "seed")

    with store.head_bound_transaction("master") as transaction:
        transaction.assert_current()
        store.commit_files({"raced.txt": b"raced"}, "raced")
        with pytest.raises(HeadMismatchError) as excinfo:
            transaction.assert_current()

    assert excinfo.value.branch == "master"
    assert excinfo.value.expected_head == first
    assert excinfo.value.actual_head == store.branch_sha("master")


def test_head_bound_transaction_post_commit_hooks_run_after_successful_commit():
    store = GitStore.init_memory()
    store.commit_files({"seed.txt": b"seed"}, "seed")
    calls: list[str] = []

    with store.head_bound_transaction("master") as transaction:
        transaction.after_commit(calls.append)
        commit = transaction.commit_batch({"next.txt": b"next"}, [], "next")
        assert calls == []

    assert calls == [commit]


def test_head_bound_transaction_without_commit_does_not_run_hooks():
    store = GitStore.init_memory()
    store.commit_files({"seed.txt": b"seed"}, "seed")
    calls: list[str] = []

    with store.head_bound_transaction("master") as transaction:
        transaction.after_commit(calls.append)

    assert calls == []


def test_head_bound_transaction_exception_clears_pending_hooks():
    store = GitStore.init_memory()
    store.commit_files({"seed.txt": b"seed"}, "seed")
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="boom"):
        with store.head_bound_transaction("master") as transaction:
            transaction.after_commit(calls.append)
            raise RuntimeError("boom")

    assert calls == []


def test_deep_tree_paths_do_not_depend_on_python_recursion_limit():
    store = GitStore.init_memory()
    deep_path = "/".join(f"d{index}" for index in range(1100)) + "/leaf.txt"

    commit = store.commit_files({deep_path: b"leaf"}, "add deep leaf")

    assert store.read_file(deep_path, commit=commit) == b"leaf"
    assert dict(store.iter_flat_tree_entries(commit))[deep_path]


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


def test_filesystem_reads_follow_new_head_after_cached_read(tmp_path):
    root = tmp_path / "repo"
    store = GitStore.init(root)
    first = store.commit_files({"docs/example.yaml": b"name: first\n"}, "seed")

    assert store.read_file("docs/example.yaml") == b"name: first\n"

    second = store.commit_files({"docs/example.yaml": b"name: second\n"}, "update")

    assert store.read_file("docs/example.yaml") == b"name: second\n"
    assert store.read_file("docs/example.yaml", commit=first) == b"name: first\n"
    assert store.read_file("docs/example.yaml", commit=second) == b"name: second\n"


def test_iter_subtree_files_walks_nested_files_once():
    store = GitStore.init_memory()
    commit = store.commit_files(
        {
            "docs/root.yaml": b"root",
            "docs/nested/one.yaml": b"one",
            "docs/nested/deeper/two.yaml": b"two",
            "other/skip.yaml": b"skip",
        },
        "seed subtree",
    )

    assert list(store.iter_subtree_files("docs", commit=commit)) == [
        ("nested/deeper/two.yaml", b"two"),
        ("nested/one.yaml", b"one"),
        ("root.yaml", b"root"),
    ]
    assert list(store.iter_subtree_files("missing", commit=commit)) == []


def test_commit_rejects_writing_child_under_existing_file():
    store = GitStore.init_memory()
    store.commit_files({"docs": b"file"}, "add file")

    with pytest.raises(ValueError, match="path conflict"):
        store.commit_files({"docs/example.txt": b"child"}, "add child")


def test_commit_rejects_replacing_existing_directory_with_file():
    store = GitStore.init_memory()
    store.commit_files({"docs/example.txt": b"child"}, "add child")

    with pytest.raises(ValueError, match="path conflict"):
        store.commit_files({"docs": b"file"}, "replace directory")


def test_commit_delete_missing_path_remains_harmless():
    store = GitStore.init_memory()
    first = store.commit_files({"docs/example.txt": b"child"}, "add child")

    second = store.commit_deletes(["docs/missing.txt"], "delete missing")

    assert store.read_file("docs/example.txt", commit=second) == b"child"
    assert list(store.iter_commit_parent_shas(second)) == [first]


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


def test_materialize_commit_to_explicit_root_reports_writes_and_skips(tmp_path):
    store = GitStore.init_memory()
    commit = store.commit_files(
        {
            "books/a.txt": b"a",
            "notes/b.txt": b"b",
        },
        "seed",
    )
    root = tmp_path / "checkout"

    first = store.materialize(commit=commit, root=root)
    second = store.materialize(commit=commit, root=root)

    assert first.written_paths == ("books/a.txt", "notes/b.txt")
    assert first.skipped_paths == ()
    assert second.written_paths == ()
    assert second.skipped_paths == ("books/a.txt", "notes/b.txt")
    assert (root / "books" / "a.txt").read_bytes() == b"a"
    assert (root / "notes" / "b.txt").read_bytes() == b"b"


def test_materialize_refuses_to_overwrite_local_edits_unless_forced(tmp_path):
    store = GitStore.init_memory()
    commit = store.commit_files({"docs/a.txt": b"canonical"}, "seed")
    root = tmp_path / "checkout"
    edited = root / "docs" / "a.txt"
    edited.parent.mkdir(parents=True)
    edited.write_bytes(b"local edit")

    with pytest.raises(MaterializeConflictError) as excinfo:
        store.materialize(commit=commit, root=root)

    assert excinfo.value.conflict_paths == ("docs/a.txt",)
    assert "docs/a.txt" in str(excinfo.value)
    assert edited.read_bytes() == b"local edit"

    forced = store.materialize(commit=commit, root=root, force=True)

    assert forced.written_paths == ("docs/a.txt",)
    assert edited.read_bytes() == b"canonical"


def test_materialize_clean_roots_delete_stale_files_and_skip_ignored_paths(tmp_path):
    store = GitStore.init_memory()
    commit = store.commit_files({"docs/keep.txt": b"keep"}, "seed")
    root = tmp_path / "checkout"
    stale = root / "docs" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")
    outside = root / "outside" / "stale.txt"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"outside")
    ignored = root / "cache" / "runtime.sqlite"
    ignored.parent.mkdir(parents=True)
    ignored.write_bytes(b"cache")
    git_head = root / ".git" / "HEAD"
    git_head.parent.mkdir(parents=True)
    git_head.write_bytes(b"ref: refs/heads/master\n")

    report = store.materialize(
        commit=commit,
        root=root,
        clean=True,
        clean_roots=("docs", "cache"),
        ignored_path=lambda relpath: relpath.startswith("cache/"),
    )

    assert report.written_paths == ("docs/keep.txt",)
    assert report.deleted_paths == ("docs/stale.txt",)
    assert not stale.exists()
    assert outside.read_bytes() == b"outside"
    assert ignored.read_bytes() == b"cache"
    assert git_head.read_bytes() == b"ref: refs/heads/master\n"


def test_materialize_missing_branch_reports_empty_branch_failure(tmp_path):
    store = GitStore.init_memory()

    with pytest.raises(ValueError, match="Branch 'missing' has no commit"):
        store.materialize(branch="missing", root=tmp_path / "checkout")


def test_iter_tree_files_can_walk_selected_roots():
    store = GitStore.init_memory()
    commit = store.commit_files(
        {
            "books/a.txt": b"a",
            "notes/b.txt": b"b",
            "other/c.txt": b"c",
        },
        "seed",
    )

    files = tuple(store.iter_tree_files(commit=commit, roots=("books", "notes")))

    assert [(file.relpath, file.content) for file in files] == [
        ("books/a.txt", b"a"),
        ("notes/b.txt", b"b"),
    ]


def test_materialize_worktree_prunes_directories_emptied_by_stale_file_removal(tmp_path):
    root = tmp_path / "repo"
    store = GitStore.init(
        root,
        policy=GitStorePolicy(ignored_path_prefixes=("runtime/",)),
    )
    store.commit_files({"docs/nested/example.yaml": b"name: demo\n"}, "add example")
    store.materialize_worktree(remove_extra=True)

    stale = root / "docs" / "nested" / "stale.yaml"
    stale.write_bytes(b"old")
    runtime = root / "runtime" / "cache" / "state.txt"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"runtime")

    store.commit_deletes(["docs/nested/example.yaml"], "remove example")
    store.materialize_worktree(remove_extra=True)

    assert not stale.exists()
    assert not (root / "docs" / "nested").exists()
    assert not (root / "docs").exists()
    assert runtime.read_bytes() == b"runtime"


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


@pytest.mark.parametrize("destination_kind", ["disk", "memory"])
def test_fetch_ref_fetches_only_selected_commit_closure_and_advances(
    tmp_path,
    destination_kind,
):
    source_root = tmp_path / "source"
    source = GitStore.init(source_root)
    first = source.commit_files({"docs/example.txt": b"first"}, "first")
    other = source.commit_files(
        {"private.txt": b"not selected"},
        "other",
        branch="other",
    )
    source_refs = dict(source.raw_repo.refs.as_dict())

    if destination_kind == "disk":
        destination = GitStore.init(tmp_path / "destination")
    else:
        destination = GitStore.init_memory()
    unrelated = destination.commit_files({"local.txt": b"local"}, "local")
    tracking_ref = RefName("refs/remotes/source/master")

    fetched = destination.fetch_ref(
        str(source_root / ".git"),
        RefName("refs/heads/master"),
        tracking_ref,
        expected_local=None,
    )

    assert fetched == first
    assert destination.read_ref(tracking_ref) == first
    assert destination.read_ref(RefName("refs/heads/master")) == unrelated
    assert destination.read_file("docs/example.txt", commit=fetched) == b"first"
    assert [(item.relpath, item.content) for item in destination.iter_tree_files(commit=fetched)] == [
        ("docs/example.txt", b"first")
    ]
    assert other.encode("ascii") not in destination.raw_repo.object_store
    assert dict(source.raw_repo.refs.as_dict()) == source_refs

    second = source.commit_files({"docs/example.txt": b"second"}, "second")
    advanced = destination.fetch_ref(
        str(source_root / ".git"),
        RefName("refs/heads/master"),
        tracking_ref,
        expected_local=first,
    )

    assert advanced == second
    assert destination.read_ref(tracking_ref) == second
    assert destination.read_file("docs/example.txt", commit=advanced) == b"second"


def test_fetch_ref_failures_leave_local_refs_unchanged(tmp_path):
    source_root = tmp_path / "source"
    source = GitStore.init(source_root)
    source.commit_files({"remote.txt": b"remote"}, "remote")
    source.write_blob_ref(RefName("refs/invalid/blob"), b"not a commit")

    destination = GitStore.init_memory()
    local = destination.commit_files({"local.txt": b"local"}, "local")
    tracking_ref = RefName("refs/remotes/source/master")
    destination.write_ref(tracking_ref, local)
    refs_before = dict(destination.raw_repo.refs.as_dict())

    with pytest.raises(HeadMismatchError):
        destination.fetch_ref(
            str(source_root / ".git"),
            RefName("refs/heads/master"),
            tracking_ref,
            expected_local=None,
        )
    assert dict(destination.raw_repo.refs.as_dict()) == refs_before

    with pytest.raises(ValueError, match="not advertised"):
        destination.fetch_ref(
            str(source_root / ".git"),
            RefName("refs/heads/missing"),
            RefName("refs/remotes/source/missing"),
            expected_local=None,
        )
    assert dict(destination.raw_repo.refs.as_dict()) == refs_before

    with pytest.raises(TypeError, match="Expected commit object"):
        destination.fetch_ref(
            str(source_root / ".git"),
            RefName("refs/invalid/blob"),
            RefName("refs/remotes/source/invalid"),
            expected_local=None,
        )
    assert dict(destination.raw_repo.refs.as_dict()) == refs_before

    with pytest.raises(NotGitRepository):
        destination.fetch_ref(
            str(tmp_path / "not-a-repository"),
            RefName("refs/heads/master"),
            RefName("refs/remotes/source/broken"),
            expected_local=None,
        )
    assert dict(destination.raw_repo.refs.as_dict()) == refs_before


def test_fetch_ref_uses_smart_http_transport(tmp_path):
    source_root = tmp_path / "source"
    source = GitStore.init(source_root)
    source_sha = source.commit_files({"network.txt": b"over http"}, "network")
    application = make_wsgi_chain(DictBackend({"/source.git": source.raw_repo}))

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            return

    server = make_server("127.0.0.1", 0, application, handler_class=QuietHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        destination = GitStore.init_memory()
        tracking_ref = RefName("refs/remotes/http/master")
        fetched = destination.fetch_ref(
            f"http://127.0.0.1:{server.server_port}/source.git",
            RefName("refs/heads/master"),
            tracking_ref,
            expected_local=None,
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert fetched == source_sha
    assert destination.read_ref(tracking_ref) == source_sha
    assert destination.read_file("network.txt", commit=fetched) == b"over http"


def test_expected_head_ref_update_is_compare_and_swap(monkeypatch):
    store = GitStore.init_memory()
    first = store.commit_files({"base.txt": b"base"}, "base")
    racing = store.commit_files({"race.txt": b"race"}, "race", branch="race")
    branch_ref = b"refs/heads/master"
    observed = first.encode("ascii")
    raced = False
    original_ref_get = git_store._ref_get
    original_ref_set = git_store._ref_set

    def race_after_expected_head_read(refs: object, ref_name: bytes) -> bytes | None:
        nonlocal raced
        result = original_ref_get(refs, ref_name)
        if not raced and ref_name == branch_ref and result == observed:
            raced = True
            original_ref_set(refs, ref_name, racing.encode("ascii"))
        return result

    monkeypatch.setattr(git_store, "_ref_get", race_after_expected_head_read)

    with pytest.raises(HeadMismatchError) as excinfo:
        store.commit_files({"stale.txt": b"stale"}, "stale", expected_head=first)

    assert excinfo.value.branch == "master"
    assert excinfo.value.expected_head == first
    assert excinfo.value.actual_head == racing
    assert store.branch_sha("master") == racing


def test_expected_head_race_does_not_write_unreachable_objects(monkeypatch):
    store = GitStore.init_memory()
    first = store.commit_files({"base.txt": b"base"}, "base")
    racing = store.commit_files({"race.txt": b"race"}, "race", branch="race")
    branch_ref = b"refs/heads/master"
    observed = first.encode("ascii")
    raced = False
    original_ref_get = git_store._ref_get
    original_ref_set = git_store._ref_set
    objects_before = set(store.raw_repo.object_store)

    def race_after_expected_head_read(refs: object, ref_name: bytes) -> bytes | None:
        nonlocal raced
        result = original_ref_get(refs, ref_name)
        if not raced and ref_name == branch_ref and result == observed:
            raced = True
            original_ref_set(refs, ref_name, racing.encode("ascii"))
        return result

    monkeypatch.setattr(git_store, "_ref_get", race_after_expected_head_read)

    with pytest.raises(HeadMismatchError):
        store.commit_files({"stale.txt": b"stale"}, "stale", expected_head=first)

    assert store.branch_sha("master") == racing
    assert set(store.raw_repo.object_store) == objects_before


def test_multiprocess_writers_are_serialized_by_filesystem_lock(tmp_path):
    root = tmp_path / "repo"
    GitStore.init(root).commit_files({"base.txt": b"base"}, "base")
    context = mp.get_context("spawn")
    queue: mp.Queue = context.Queue()
    worker_count = 4
    commits_per_worker = 12
    processes = [
        context.Process(
            target=_multiprocess_commit_worker,
            args=(str(root), worker, commits_per_worker, queue),
        )
        for worker in range(worker_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)

    assert [process.exitcode for process in processes] == [0] * worker_count
    results = [queue.get(timeout=5) for _ in processes]
    assert sorted(results) == [("ok", worker) for worker in range(worker_count)]

    reopened = GitStore.open(root)
    for worker in range(worker_count):
        for index in range(commits_per_worker):
            assert reopened.read_file(f"workers/{worker}/{index}.txt") == (
                f"{worker}:{index}".encode("ascii")
            )


def test_blob_refs_store_derived_index_payloads():
    store = GitStore.init_memory()
    ref = RefName("refs/quire/indexes/example")

    blob_sha = store.write_blob_ref(ref, b"8\n")

    assert store.read_ref(ref) == blob_sha
    assert store.read_blob_ref(ref) == b"8\n"
    store.delete_ref(ref)
    assert store.read_blob_ref(ref) is None


def test_gc_dry_run_reports_unreachable_objects() -> None:
    store = GitStore.init_memory()
    store.commit_files({"base.txt": b"base"}, "base")
    orphan = store.store_blob(b"orphan")

    report = store.gc(dry_run=True)

    assert orphan in report.orphan_shas
    assert report.orphan_objects == 1
    assert report.total_objects > report.reachable_objects


def test_flat_tree_entries_and_commit_flat_tree_create_merge_commit():
    store = GitStore.init_memory()
    left = store.commit_files({"a.txt": b"left"}, "left", branch="left")
    right = store.commit_files({"b.txt": b"right"}, "right", branch="right")

    entries = dict(store.iter_flat_tree_entries(right))
    entries.update(dict(store.iter_flat_tree_entries(left)))
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


def test_exists_agrees_with_walk_tree_for_non_file_tree_entry():
    store = GitStore.init_memory()
    parent_sha = store.commit_files({"base.txt": b"base"}, "base")
    parent = store.raw_repo[parent_sha.encode("ascii")]
    assert isinstance(parent, Commit)

    tree = Tree()
    tree.add(b"submodule", 0o160000, parent.id)
    store.raw_repo.object_store.add_object(tree)

    commit = Commit()
    commit.tree = tree.id
    commit.author = b"Quire <quire@example.com>"
    commit.committer = b"Quire <quire@example.com>"
    commit.encoding = b"UTF-8"
    commit.message = b"commit-backed tree entry"
    commit.commit_time = 0
    commit.author_time = 0
    commit.commit_timezone = 0
    commit.author_timezone = 0
    commit.parents = [parent.id]
    store.raw_repo.object_store.add_object(commit)
    store.write_ref(RefName("refs/heads/master"), commit.id)

    tree_obj = store._get_tree()
    assert store._walk_tree(tree_obj, ("submodule",)) is None
    assert store.exists("submodule") is None


def test_merge_base_is_deterministic_for_criss_cross_history():
    store = GitStore.init_memory()
    base = store.commit_files({"base.txt": b"base"}, "base")
    store.create_branch("left", source_commit=base)
    store.create_branch("right", source_commit=base)
    left = store.commit_files({"left.txt": b"left"}, "left", branch="left")
    right = store.commit_files({"right.txt": b"right"}, "right", branch="right")

    left_entries = dict(store.iter_flat_tree_entries(left))
    left_entries.update(dict(store.iter_flat_tree_entries(right)))
    left_entries["left-merge.txt"] = store.store_blob(b"left merge")
    left_merge = store.commit_flat_tree(
        left_entries,
        "merge right into left",
        parents=[left, right],
        branch="left",
    )

    right_entries = dict(store.iter_flat_tree_entries(right))
    right_entries.update(dict(store.iter_flat_tree_entries(left)))
    right_entries["right-merge.txt"] = store.store_blob(b"right merge")
    right_merge = store.commit_flat_tree(
        right_entries,
        "merge left into right",
        parents=[right, left],
        branch="right",
    )

    assert list(store.iter_commit_parent_shas(left_merge)) == [left, right]
    assert list(store.iter_commit_parent_shas(right_merge)) == [right, left]
    assert store.ancestor_distances(left_merge)[right] == 1
    assert store.ancestor_distances(right_merge)[left] == 1
    assert store.merge_base("left", "right") == min(left, right)


def test_merge_base_does_not_depend_on_repeated_ancestor_distance_walk(monkeypatch):
    store = GitStore.init_memory()
    base = store.commit_files({"base.txt": b"base"}, "base")
    store.create_branch("left", source_commit=base)
    store.create_branch("right", source_commit=base)
    store.commit_files({"left.txt": b"left"}, "left", branch="left")
    store.commit_files({"right.txt": b"right"}, "right", branch="right")

    def fail_repeated_walk(start_sha: str) -> dict[str, int]:
        raise AssertionError(f"ancestor_distances called for {start_sha}")

    monkeypatch.setattr(store, "ancestor_distances", fail_repeated_walk)

    assert store.merge_base("left", "right") == base


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
    assert list(store.iter_commit_parent_shas(left)) == [base]
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

    with pytest.raises(HeadMismatchError):
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

    with pytest.raises(HeadMismatchError):
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

    assert list(store.iter_commit_parent_shas(reverted)) == [second]
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


def test_sync_worktree_refreshes_on_disk_index_to_match_head(tmp_path):
    """sync_worktree must leave the on-disk dulwich index in sync with HEAD.

    Regression for the phantom-deletion pattern: after a commit via quire's
    backend, ``git status`` (and dulwich's ``porcelain.status``) would report
    every file under HEAD as staged-deleted because the index was empty while
    HEAD's tree had entries. A subsequent plain ``git commit`` would then
    silently wipe all those files.
    """
    from dulwich import porcelain

    root = tmp_path / "repo"
    store = GitStore.init(root)
    store.commit_files({"a.txt": b"hello", "b/c.txt": b"nested"}, "seed")

    store.sync_worktree()

    status = porcelain.status(str(root))
    assert dict(status.staged) == {"add": [], "delete": [], "modify": []}
    assert list(status.unstaged) == []
    assert list(status.untracked) == []


def test_materialize_worktree_refreshes_on_disk_index_to_match_head(tmp_path):
    from dulwich import porcelain

    root = tmp_path / "repo"
    store = GitStore.init(root)
    store.commit_files({"a.txt": b"hello", "b/c.txt": b"nested"}, "seed")

    store.materialize_worktree()

    status = porcelain.status(str(root))
    assert dict(status.staged) == {"add": [], "delete": [], "modify": []}
    assert list(status.unstaged) == []
    assert list(status.untracked) == []


def test_sync_worktree_refreshes_index_after_subsequent_commits(tmp_path):
    """Index must stay in sync across multiple commits that add and delete."""
    from dulwich import porcelain

    root = tmp_path / "repo"
    store = GitStore.init(root)
    store.commit_files({"keep.txt": b"keep"}, "first")
    store.sync_worktree()
    store.commit_batch(
        adds={"keep.txt": b"updated", "new.txt": b"new"},
        deletes=[],
        message="second",
    )
    store.sync_worktree()

    status = porcelain.status(str(root))
    assert dict(status.staged) == {"add": [], "delete": [], "modify": []}
    assert list(status.unstaged) == []
    assert list(status.untracked) == []


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

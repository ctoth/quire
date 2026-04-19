"""Hypothesis properties for GitStore's generic git substrate."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from quire.git_store import GitStore, GitStorePolicy
from quire.tree_path import FilesystemTreePath, GitTreePath

_TEST_POLICY = GitStorePolicy(
    initial_files={".gitignore": b"runtime/\n*.cache\n"},
    initial_commit_message="Initialize test repository",
    ignored_path_prefixes=("runtime/",),
    ignored_path_suffixes=(".cache",),
)
_INITIAL_MODEL = {".gitignore": b"runtime/\n*.cache\n"}

raw_bytes = st.binary(min_size=0, max_size=4096)
segment = st.from_regex(r"[a-z][a-z0-9_]{0,12}", fullmatch=True)
nested_path = st.lists(segment, min_size=1, max_size=5).map(
    lambda parts: "/".join([*parts[:-1], f"{parts[-1]}.bin"])
)
path_map = st.dictionaries(nested_path, raw_bytes, min_size=1, max_size=8)

yaml_bytes = st.dictionaries(
    st.text(st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=20),
    st.one_of(
        st.integers(min_value=-1000, max_value=1000),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
        st.text(min_size=0, max_size=50),
        st.booleans(),
    ),
    min_size=1,
    max_size=10,
).map(lambda data: yaml.dump(data, default_flow_style=False).encode("utf-8"))

valid_subdir = st.sampled_from(["artifacts", "documents", "indexes", "refs", "snapshots"])

valid_filename = st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True).map(
    lambda name: f"{name}.yaml"
)

valid_path = st.tuples(valid_subdir, valid_filename).map(lambda parts: f"{parts[0]}/{parts[1]}")


def _make_repo() -> GitStore:
    return GitStore.init_memory(policy=_TEST_POLICY)


def _make_disk_repo() -> tuple[GitStore, Path, tempfile.TemporaryDirectory[str]]:
    tmpdir = tempfile.TemporaryDirectory()
    root = Path(tmpdir.name) / "repository"
    repo = GitStore.init(root, policy=_TEST_POLICY)
    repo.sync_worktree()
    return repo, root, tmpdir


def _snapshot(store: GitStore, commit: str | None = None) -> dict[str, bytes]:
    return {
        path: store.read_file(path, commit=commit)
        for path in sorted(store.flat_tree_entries(commit))
    }


def _flat_snapshot(store: GitStore, commit: str | None = None) -> dict[str, str]:
    return store.flat_tree_entries(commit)


def _model_diff(new: dict[str, str], old: dict[str, str]) -> dict[str, list[str]]:
    return {
        "added": sorted(path for path in new if path not in old),
        "modified": sorted(path for path in new if path in old and new[path] != old[path]),
        "deleted": sorted(path for path in old if path not in new),
    }


def _commit_model(store: GitStore, files: dict[str, bytes], message: str = "seed") -> str:
    commit = store.commit_files(files, message)
    assert _snapshot(store, commit) == {**_INITIAL_MODEL, **files}
    return commit


def _missing_path(existing_paths: set[str]) -> str:
    candidate = "missing.bin"
    index = 0
    while candidate in existing_paths:
        index += 1
        candidate = f"missing-{index}.bin"
    return candidate


@settings(deadline=None)
@given(path=valid_path, content=yaml_bytes)
def test_roundtrip_preservation(path: str, content: bytes) -> None:
    repo = _make_repo()

    repo.commit_files({path: content}, f"add {path}")

    assert repo.read_file(path) == content


@settings(deadline=None)
@example(path="empty.bin", content=b"")
@given(path=nested_path, content=raw_bytes)
def test_roundtrip_preserves_arbitrary_bytes(path: str, content: bytes) -> None:
    repo = _make_repo()

    repo.commit_files({path: content}, f"add {path}")

    assert repo.read_file(path) == content


@settings(deadline=None)
@given(files=path_map, replacement=raw_bytes)
def test_update_isolation_and_history_immutability(files: dict[str, bytes], replacement: bytes) -> None:
    repo = _make_repo()
    first = _commit_model(repo, files)
    before = _snapshot(repo, first)
    update_path = sorted(files)[0]

    second = repo.commit_files({update_path: replacement}, "update one")

    after = _snapshot(repo, second)
    expected_after = {**before, update_path: replacement}
    assert after == expected_after
    assert _snapshot(repo, first) == before
    changed = {path for path in after if before.get(path) != after.get(path)}
    assert changed <= {update_path}


@settings(deadline=None)
@given(files=path_map)
def test_flat_tree_entries_and_exists_match_snapshot(files: dict[str, bytes]) -> None:
    repo = _make_repo()
    commit = _commit_model(repo, files)

    flat = _flat_snapshot(repo, commit)
    snap = _snapshot(repo, commit)

    assert set(flat) == set(snap)
    assert set(snap) == set(_INITIAL_MODEL) | set(files)
    for path, expected in snap.items():
        exists = repo.exists(path, commit=commit)
        assert exists is not None
        assert exists[1] == flat[path]
        assert repo.read_file(path, commit=commit) == expected

    root = repo.exists("", commit=commit)
    assert root is not None
    assert root[0] & 0o040000

    missing = _missing_path(set(snap))
    assert repo.exists(missing, commit=commit) is None
    with pytest.raises(FileNotFoundError):
        repo.read_file(missing, commit=commit)


@settings(deadline=None)
@given(subdir=valid_subdir, filenames=st.lists(valid_filename, min_size=1, max_size=5, unique=True))
def test_listing_completeness(subdir: str, filenames: list[str]) -> None:
    repo = _make_repo()
    content = b"x: 1\n"
    changes: dict[str | Path, bytes] = {f"{subdir}/{filename}": content for filename in filenames}

    repo.commit_files(changes, f"add files to {subdir}")

    listed = list(repo.iter_dir(subdir))
    for filename in filenames:
        assert filename in listed


@settings(deadline=None)
@given(path=valid_path, content=yaml_bytes)
def test_delete_semantics(path: str, content: bytes) -> None:
    repo = _make_repo()

    repo.commit_files({path: content}, f"add {path}")
    repo.commit_deletes([path], f"delete {path}")

    with pytest.raises(FileNotFoundError):
        repo.read_file(path)


@settings(deadline=None)
@given(
    add_path=valid_path,
    add_content=yaml_bytes,
    del_path=valid_path,
    del_content=yaml_bytes,
)
def test_batch_atomicity(
    add_path: str,
    add_content: bytes,
    del_path: str,
    del_content: bytes,
) -> None:
    repo = _make_repo()
    repo.commit_files({del_path: del_content}, "setup")
    initial_count = len(repo.log(max_count=10000))

    repo.commit_batch(adds={add_path: add_content}, deletes=[del_path], message="batch op")
    new_count = len(repo.log(max_count=10000))

    assert new_count == initial_count + 1
    if add_path == del_path:
        with pytest.raises(FileNotFoundError):
            repo.read_file(add_path)
    else:
        assert repo.read_file(add_path) == add_content
        with pytest.raises(FileNotFoundError):
            repo.read_file(del_path)


def _batch_model(
    current: dict[str, bytes],
    adds: dict[str, bytes],
    deletes: list[str],
) -> dict[str, bytes]:
    result = dict(current)
    result.update(adds)
    for path in deletes:
        result.pop(path, None)
    return result


@settings(deadline=None)
@given(seed=path_map, adds=path_map, delete_missing=st.booleans())
def test_batch_matches_dict_model(seed: dict[str, bytes], adds: dict[str, bytes], delete_missing: bool) -> None:
    repo = _make_repo()
    base = _commit_model(repo, seed)
    current = _snapshot(repo, base)
    delete_path = sorted(seed)[0]
    deletes = [delete_path, delete_path]
    if delete_missing:
        deletes.append(_missing_path(set(current) | set(adds)))
    old_tip = repo.head_sha()

    batch = repo.commit_batch(adds, deletes, "batch")

    assert _snapshot(repo, batch) == _batch_model(current, adds, deletes)
    assert repo.commit_parent_shas(batch) == [old_tip]
    assert len(repo.log(max_count=10000)) == 3


@settings(deadline=None)
@given(seed=path_map, changes=path_map)
def test_commit_files_and_batch_adds_have_same_tree(seed: dict[str, bytes], changes: dict[str, bytes]) -> None:
    files_repo = _make_repo()
    batch_repo = _make_repo()
    _commit_model(files_repo, seed)
    _commit_model(batch_repo, seed)

    files_commit = files_repo.commit_files(changes, "files")
    batch_commit = batch_repo.commit_batch(changes, [], "batch")

    assert _snapshot(files_repo, files_commit) == _snapshot(batch_repo, batch_commit)


@settings(deadline=None)
@given(seed=path_map)
def test_commit_deletes_and_batch_deletes_have_same_tree(seed: dict[str, bytes]) -> None:
    deletes = [sorted(seed)[0], _missing_path(set(seed) | set(_INITIAL_MODEL))]
    delete_repo = _make_repo()
    batch_repo = _make_repo()
    _commit_model(delete_repo, seed)
    _commit_model(batch_repo, seed)

    delete_commit = delete_repo.commit_deletes(deletes, "delete")
    batch_commit = batch_repo.commit_batch({}, deletes, "batch delete")

    assert _snapshot(delete_repo, delete_commit) == _snapshot(batch_repo, batch_commit)


@settings(deadline=None)
@given(seed=path_map)
def test_empty_batch_preserves_tree_and_advances_branch(seed: dict[str, bytes]) -> None:
    repo = _make_repo()
    base = _commit_model(repo, seed)
    before = _snapshot(repo, base)

    empty = repo.commit_batch({}, [], "empty")

    assert empty != base
    assert repo.head_sha() == empty
    assert repo.commit_parent_shas(empty) == [base]
    assert _snapshot(repo, empty) == before


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(path=valid_path, content=yaml_bytes)
def test_worktree_fidelity(path: str, content: bytes) -> None:
    repo, root, tmpdir = _make_disk_repo()
    with tmpdir:
        repo.commit_files({path: content}, f"add {path}")

        repo.sync_worktree()

        disk_path = root / path.replace("/", os.sep)
        assert disk_path.exists()
        assert disk_path.read_bytes() == content


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(subdir=valid_subdir, filename=valid_filename, content=yaml_bytes)
def test_tree_path_equivalence(subdir: str, filename: str, content: bytes) -> None:
    repo, root, tmpdir = _make_disk_repo()
    with tmpdir:
        path = f"{subdir}/{filename}"
        repo.commit_files({path: content}, f"add {path}")

        repo.sync_worktree()

        git_tree = GitTreePath(repo) / subdir
        fs_tree = FilesystemTreePath(root) / subdir
        git_entries = {
            (entry.stem, entry.read_bytes())
            for entry in git_tree.iterdir()
            if entry.is_file() and entry.suffix == ".yaml"
        }
        fs_entries = {
            (entry.stem, entry.read_bytes())
            for entry in fs_tree.iterdir()
            if entry.is_file() and entry.suffix == ".yaml"
        }
        assert git_entries == fs_entries


@settings(deadline=None)
@given(paths=st.lists(valid_path, min_size=2, max_size=5, unique=True))
def test_history_monotonicity(paths: list[str]) -> None:
    repo = _make_repo()
    shas = [repo.head_sha()]

    for index, path in enumerate(paths):
        repo.commit_files({path: f"v: {index}\n".encode()}, f"commit {index}")
        sha = repo.head_sha()
        assert sha not in shas
        shas.append(sha)

    expected = 1 + len(paths)
    history = repo.log(max_count=expected + 1)
    assert len(history) == expected


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(path=valid_path, content=yaml_bytes)
def test_idempotent_sync(path: str, content: bytes) -> None:
    repo, root, tmpdir = _make_disk_repo()
    with tmpdir:
        repo.commit_files({path: content}, f"add {path}")

        repo.sync_worktree()
        first_snapshot = _snapshot_dir(root)

        repo.sync_worktree()
        second_snapshot = _snapshot_dir(root)

        assert first_snapshot == second_snapshot


def _snapshot_dir(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        relpath = file_path.relative_to(root).as_posix()
        if relpath == ".git" or relpath.startswith(".git/"):
            continue
        result[relpath] = file_path.read_bytes()
    return result


@settings(deadline=None)
@given(seed=path_map, adds=path_map, replacement=raw_bytes)
def test_diff_commits_matches_blob_model(
    seed: dict[str, bytes],
    adds: dict[str, bytes],
    replacement: bytes,
) -> None:
    repo = _make_repo()
    first = _commit_model(repo, seed)
    modified_path = sorted(seed)[0]
    deleted_path = sorted(seed)[-1]

    second = repo.commit_batch(
        {**adds, modified_path: replacement},
        [deleted_path],
        "change",
    )

    expected = _model_diff(_flat_snapshot(repo, second), _flat_snapshot(repo, first))
    actual = repo.diff_commits(second, first)
    assert actual == expected
    assert actual["added"] == sorted(actual["added"])
    assert actual["modified"] == sorted(actual["modified"])
    assert actual["deleted"] == sorted(actual["deleted"])


@settings(deadline=None)
@given(seed=path_map, adds=path_map, replacement=raw_bytes)
def test_default_diff_and_show_commit_use_first_parent(
    seed: dict[str, bytes],
    adds: dict[str, bytes],
    replacement: bytes,
) -> None:
    repo = _make_repo()
    first = _commit_model(repo, seed)
    modified_path = sorted(seed)[0]
    second = repo.commit_batch({**adds, modified_path: replacement}, [], " second ")

    assert repo.diff_commits(second) == repo.diff_commits(second, first)
    shown = repo.show_commit(second)
    expected = repo.diff_commits(second, first)
    assert shown["sha"] == second
    assert shown["message"] == "second"
    assert shown["added"] == expected["added"]
    assert shown["modified"] == expected["modified"]
    assert shown["deleted"] == expected["deleted"]


@settings(deadline=None)
@given(files=path_map)
def test_root_commit_diff_reports_all_files_added(files: dict[str, bytes]) -> None:
    repo = GitStore.init_memory()

    root = repo.commit_files(files, "root")

    assert repo.commit_parent_shas(root) == []
    assert repo.diff_commits(root) == {
        "added": sorted(files),
        "modified": [],
        "deleted": [],
    }


@settings(deadline=None)
@given(
    path=valid_path,
    content=yaml_bytes,
    message=st.text(
        st.characters(whitelist_categories=("L", "N", "Z"), whitelist_characters=" -_:."),
        min_size=1,
        max_size=80,
    ),
)
def test_commit_message_preservation(path: str, content: bytes, message: str) -> None:
    repo = _make_repo()

    repo.commit_files({path: content}, message)

    history = repo.log(max_count=1)
    assert history[0]["message"] == message.strip()


@settings(deadline=None)
@given(subdir=valid_subdir, name=valid_filename, content=yaml_bytes)
def test_path_normalization(subdir: str, name: str, content: bytes) -> None:
    repo = _make_repo()
    posix_path = f"{subdir}/{name}"
    backslash_path = f"{subdir}\\{name}"

    repo.commit_files({backslash_path: content}, "add with backslash")

    assert repo.read_file(posix_path) == content
    assert repo.read_file(backslash_path) == content


class GitStoreMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.model: dict[str, bytes] = {}
        self.commit_count = 0
        self.repo: GitStore | None = None

    @initialize()
    def init_repo(self) -> None:
        self.repo = _make_repo()
        self.commit_count = 1
        self.model = {}

    @rule(path=valid_path, content=yaml_bytes)
    def commit_file(self, path: str, content: bytes) -> None:
        assert self.repo is not None
        self.repo.commit_files({path: content}, f"add {path}")
        self.model[path] = content
        self.commit_count += 1

    @rule(data=st.data())
    def delete_file(self, data: st.DataObject) -> None:
        if not self.model:
            return
        assert self.repo is not None
        path = data.draw(st.sampled_from(sorted(self.model.keys())))
        self.repo.commit_deletes([path], f"delete {path}")
        del self.model[path]
        self.commit_count += 1

    @invariant()
    def reads_match_model(self) -> None:
        if self.repo is None:
            return
        for path, expected in self.model.items():
            assert self.repo.read_file(path) == expected

    @invariant()
    def deleted_files_gone(self) -> None:
        if self.repo is None:
            return
        for subdir in ["artifacts", "documents", "indexes", "refs", "snapshots"]:
            names = list(self.repo.iter_dir(subdir))
            for name in names:
                full_path = f"{subdir}/{name}"
                assert full_path in self.model

    @invariant()
    def history_length(self) -> None:
        if self.repo is None:
            return
        history = self.repo.log(max_count=10000)
        assert len(history) == self.commit_count


TestGitStore = GitStoreMachine.TestCase
TestGitStore.settings = settings(
    stateful_step_count=10,
    deadline=None,
)

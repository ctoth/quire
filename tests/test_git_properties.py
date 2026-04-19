"""Hypothesis properties for GitStore's generic git substrate."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
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


@settings(deadline=None)
@given(path=valid_path, content=yaml_bytes)
def test_roundtrip_preservation(path: str, content: bytes) -> None:
    repo = _make_repo()

    repo.commit_files({path: content}, f"add {path}")

    assert repo.read_file(path) == content


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


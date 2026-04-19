"""Hypothesis properties for GitStore's generic git substrate."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml
from dulwich.objects import Blob
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from quire.git_store import GitStore, GitStorePolicy
from quire.refs import RefName
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
branch_name = st.lists(segment, min_size=1, max_size=3).map("/".join).filter(lambda name: name != "master")
branch_pair = st.tuples(branch_name, branch_name).filter(lambda names: names[0] != names[1])

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


def _best_common_ancestors(store: GitStore, left: str, right: str) -> set[str]:
    left_distances = store.ancestor_distances(left)
    right_distances = store.ancestor_distances(right)
    common = set(left_distances) & set(right_distances)
    return {
        candidate
        for candidate in common
        if not any(
            other != candidate and candidate in store.ancestor_distances(other)
            for other in common
        )
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


def _directory_children(paths: set[str]) -> dict[str, dict[str, bool]]:
    children: dict[str, dict[str, bool]] = {"": {}}
    for path in paths:
        parts = path.split("/")
        for index, part in enumerate(parts):
            directory = "/".join(parts[:index])
            child_path = "/".join(parts[: index + 1])
            is_dir = index < len(parts) - 1
            children.setdefault(directory, {})
            children[directory][part] = children[directory].get(part, False) or is_dir
            if is_dir:
                children.setdefault(child_path, {})
    return children


def _path_spellings(path: str) -> list[str | Path]:
    backslash = path.replace("/", "\\")
    return [
        path,
        f"/{path}",
        f"{path}/",
        backslash,
        Path(*path.split("/")),
    ]


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
@given(files=path_map)
def test_iter_dir_reports_direct_children_and_entry_types(files: dict[str, bytes]) -> None:
    repo = _make_repo()
    commit = _commit_model(repo, files)
    children = _directory_children(set(_INITIAL_MODEL) | set(files))

    for directory, expected_children in children.items():
        assert list(repo.iter_dir(directory, commit=commit)) == sorted(expected_children)
        assert list(repo.iter_dir_entries(directory, commit=commit)) == [
            (name, expected_children[name]) for name in sorted(expected_children)
        ]
        for name in repo.iter_dir(directory, commit=commit):
            assert "/" not in name
            assert "\\" not in name


@settings(deadline=None)
@given(files=path_map)
def test_git_tree_path_state_matches_store(files: dict[str, bytes]) -> None:
    repo = _make_repo()
    commit = _commit_model(repo, files)
    tree = repo.tree(commit)
    children = _directory_children(set(_INITIAL_MODEL) | set(files))

    for path, expected in _snapshot(repo, commit).items():
        node = tree / path
        assert node.exists()
        assert node.is_file()
        assert not node.is_dir()
        assert node.read_bytes() == expected
        assert node.read_text(encoding="latin-1") == expected.decode("latin-1")
        with pytest.raises(NotADirectoryError):
            list(node.iterdir())

    for directory, expected_children in children.items():
        node = tree if not directory else tree / directory
        assert node.exists()
        assert node.is_dir()
        assert not node.is_file()
        assert sorted(child.name for child in node.iterdir()) == sorted(expected_children)
        with pytest.raises(FileNotFoundError):
            node.read_bytes()

    missing = tree / _missing_path(set(files) | set(_INITIAL_MODEL))
    assert not missing.exists()
    assert not missing.is_file()
    assert not missing.is_dir()


@settings(deadline=None)
@given(prefix=segment, left=raw_bytes, right=raw_bytes)
def test_prefix_sibling_paths_remain_independent(prefix: str, left: bytes, right: bytes) -> None:
    repo = _make_repo()
    left_path = f"{prefix}.bin"
    right_path = f"{prefix}{prefix}.bin"
    if left_path == right_path:
        right_path = f"{prefix}x.bin"

    commit = repo.commit_files({left_path: left, right_path: right}, "add siblings")

    assert repo.read_file(left_path, commit=commit) == left
    assert repo.read_file(right_path, commit=commit) == right


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


def _assert_stale_head_rejected(
    repo: GitStore,
    stale: str,
    operation: str,
    *,
    target_commit: str | None = None,
) -> None:
    before_tip = repo.head_sha()
    before_snapshot = _snapshot(repo)
    assert before_tip is not None

    if operation == "commit_files":
        call = lambda: repo.commit_files({"stale-add.bin": b"stale"}, "stale", expected_head=stale)
    elif operation == "commit_deletes":
        call = lambda: repo.commit_deletes(["base.bin"], "stale", expected_head=stale)
    elif operation == "commit_batch":
        call = lambda: repo.commit_batch({"stale-batch.bin": b"stale"}, ["base.bin"], "stale", expected_head=stale)
    elif operation == "commit_flat_tree":
        blob = repo.store_blob(b"flat")
        call = lambda: repo.commit_flat_tree({"flat.bin": blob}, "stale", parents=[before_tip], expected_head=stale)
    elif operation == "revert_commit":
        assert target_commit is not None
        call = lambda: repo.revert_commit(target_commit, expected_head=stale)
    else:
        raise AssertionError(f"unknown operation: {operation}")

    with pytest.raises(ValueError, match="head mismatch"):
        call()

    assert repo.head_sha() == before_tip
    assert _snapshot(repo) == before_snapshot


@pytest.mark.parametrize(
    "operation",
    ["commit_files", "commit_deletes", "commit_batch", "commit_flat_tree", "revert_commit"],
)
@settings(deadline=None)
@given(content=raw_bytes)
def test_expected_head_guards_write_operations(operation: str, content: bytes) -> None:
    repo = _make_repo()
    stale = repo.commit_files({"base.bin": b"base"}, "base")
    target = repo.commit_files({"base.bin": content}, "target")
    current = repo.commit_files({"other.bin": b"current"}, "current")

    _assert_stale_head_rejected(repo, stale, operation, target_commit=target)

    if operation == "commit_files":
        accepted = repo.commit_files({"accepted.bin": b"ok"}, "accepted", expected_head=current)
    elif operation == "commit_deletes":
        accepted = repo.commit_deletes(["other.bin"], "accepted", expected_head=current)
    elif operation == "commit_batch":
        accepted = repo.commit_batch({"accepted.bin": b"ok"}, ["other.bin"], "accepted", expected_head=current)
    elif operation == "commit_flat_tree":
        entries = repo.flat_tree_entries(current)
        entries["accepted.bin"] = repo.store_blob(b"ok")
        accepted = repo.commit_flat_tree(entries, "accepted", parents=[current], expected_head=current)
    else:
        accepted = repo.revert_commit(target, expected_head=current)

    assert repo.head_sha() == accepted
    assert repo.commit_parent_shas(accepted) == [current]


@settings(deadline=None)
@given(branch=branch_name)
def test_expected_head_uses_explicit_branch_not_current_branch(branch: str) -> None:
    repo = _make_repo()
    base = repo.commit_files({"base.bin": b"base"}, "base")
    repo.create_branch(branch, source_commit=base)
    branch_tip = repo.commit_files({"branch.bin": b"branch"}, "branch", branch=branch)
    master_tip = repo.commit_files({"master.bin": b"master"}, "master")

    with pytest.raises(ValueError, match="head mismatch"):
        repo.commit_files({"bad.bin": b"bad"}, "bad", branch=branch, expected_head=master_tip)

    accepted = repo.commit_files({"ok.bin": b"ok"}, "ok", branch=branch, expected_head=branch_tip)
    assert repo.branch_sha(branch) == accepted
    assert repo.branch_sha("master") == master_tip


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


@settings(deadline=None)
@given(branch=branch_name, files=path_map, current_content=raw_bytes)
def test_branch_ref_and_current_head_semantics(
    branch: str,
    files: dict[str, bytes],
    current_content: bytes,
) -> None:
    repo = _make_repo()
    master_tip = repo.head_sha()
    assert master_tip is not None

    assert repo.create_branch(branch) == master_tip
    branch_commit = repo.commit_files(files, "branch write", branch=branch)

    assert repo.branch_sha(branch) == branch_commit
    assert repo.current_branch_name() == "master"
    assert repo.head_sha() == master_tip
    assert _snapshot(repo, master_tip) == _INITIAL_MODEL
    assert _snapshot(repo, branch_commit) == {**_INITIAL_MODEL, **files}

    repo.set_current_branch(branch)
    implicit_path = _missing_path(set(_INITIAL_MODEL) | set(files))
    implicit_commit = repo.commit_files({implicit_path: current_content}, "implicit branch write")

    assert repo.current_branch_name() == branch
    assert repo.head_sha() == implicit_commit
    assert repo.branch_sha(branch) == implicit_commit
    assert repo.branch_sha("master") == master_tip
    assert repo.read_file(implicit_path) == current_content


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(branch=branch_name)
def test_branch_lifecycle_and_metadata_properties(branch: str) -> None:
    repo, root, tmpdir = _make_disk_repo()
    with tmpdir:
        base = repo.head_sha()
        assert base is not None

        with pytest.raises(ValueError):
            repo.set_current_branch(branch)

        assert repo.create_branch(branch, source_commit=base) == base
        reopened = GitStore.open(root, policy=_TEST_POLICY)
        branches = {item.name: item for item in reopened.iter_branches()}
        assert branches[branch].tip_sha == base
        assert branches[branch].created_at > 0

        repo.set_current_branch(branch)
        with pytest.raises(ValueError):
            repo.delete_branch(branch)
        assert repo.branch_sha(branch) == base

        repo.set_current_branch("master")
        repo.delete_branch(branch)
        assert repo.branch_sha(branch) is None
        meta_ref = RefName(f"refs/quire/branch-meta/{quote(branch, safe='')}")
        assert repo.read_blob_ref(meta_ref) is None

        assert repo.create_branch(branch, source_commit=base) == base
        assert repo.branch_sha(branch) == base


@settings(deadline=None)
@given(branch=branch_name, files=path_map, master_content=raw_bytes, branch_content=raw_bytes)
def test_branch_isolation_and_ancestry_properties(
    branch: str,
    files: dict[str, bytes],
    master_content: bytes,
    branch_content: bytes,
) -> None:
    repo = _make_repo()
    base = _commit_model(repo, files)
    assert repo.create_branch(branch, source_commit=base) == base
    shared_path = sorted(files)[0]

    master = repo.commit_files({shared_path: master_content}, "master edit")
    branch_tip = repo.commit_files({shared_path: branch_content}, "branch edit", branch=branch)

    assert repo.read_file(shared_path, commit=master) == master_content
    assert repo.read_file(shared_path, commit=branch_tip) == branch_content
    assert repo.merge_base("master", branch) == base

    for commit in (master, branch_tip):
        distances = repo.ancestor_distances(commit)
        assert distances[commit] == 0
        for parent in repo.commit_parent_shas(commit):
            assert parent in distances
            assert distances[parent] <= distances[commit] + 1
        assert repo.commit_parent_shas(commit) == [base]

    branch_log = repo.log(max_count=20, branch=branch)
    assert branch_log[0]["sha"] == branch_tip
    for entry in branch_log:
        assert entry["sha"] in repo.ancestor_distances(branch_tip)
        assert entry["parents"] == repo.commit_parent_shas(str(entry["sha"]))


@settings(deadline=None)
@given(names=branch_pair, left_steps=st.integers(min_value=0, max_value=3), right_steps=st.integers(min_value=0, max_value=3))
def test_merge_base_core_shapes(names: tuple[str, str], left_steps: int, right_steps: int) -> None:
    left_branch, right_branch = names
    repo = _make_repo()
    base = repo.commit_files({"base.bin": b"base"}, "base")
    repo.create_branch(left_branch, source_commit=base)
    repo.create_branch(right_branch, source_commit=base)

    left_tip = base
    for index in range(left_steps):
        left_tip = repo.commit_files({f"left-{index}.bin": bytes([index])}, f"left {index}", branch=left_branch)
    right_tip = base
    for index in range(right_steps):
        right_tip = repo.commit_files({f"right-{index}.bin": bytes([index])}, f"right {index}", branch=right_branch)

    assert repo.merge_base(left_branch, left_branch) == left_tip
    assert repo.merge_base(left_branch, right_branch) == base
    assert repo.merge_base(right_branch, left_branch) == base
    assert repo.merge_base("master", left_branch) == base
    assert repo.merge_base("master", right_branch) == base

    result = repo.merge_base(left_branch, right_branch)
    assert result in repo.ancestor_distances(left_tip)
    assert result in repo.ancestor_distances(right_tip)
    assert result in _best_common_ancestors(repo, left_tip, right_tip)


@settings(deadline=None)
@given(names=branch_pair, payload=raw_bytes)
def test_merge_base_criss_cross_tie_break_is_deterministic(names: tuple[str, str], payload: bytes) -> None:
    left_branch, right_branch = names
    repo = _make_repo()
    base = repo.commit_files({"base.bin": b"base"}, "base")
    repo.create_branch(left_branch, source_commit=base)
    repo.create_branch(right_branch, source_commit=base)
    left = repo.commit_files({"left.bin": payload}, "left", branch=left_branch)
    right = repo.commit_files({"right.bin": payload}, "right", branch=right_branch)

    left_entries = repo.flat_tree_entries(left)
    left_entries.update(repo.flat_tree_entries(right))
    left_merge = repo.commit_flat_tree(left_entries, "left merge", parents=[left, right], branch=left_branch)
    right_entries = repo.flat_tree_entries(right)
    right_entries.update(repo.flat_tree_entries(left))
    right_merge = repo.commit_flat_tree(right_entries, "right merge", parents=[right, left], branch=right_branch)

    assert repo.commit_parent_shas(left_merge) == [left, right]
    assert repo.commit_parent_shas(right_merge) == [right, left]
    assert repo.merge_base(left_branch, right_branch) == min(left, right)


@settings(deadline=None)
@given(files=path_map)
def test_store_blob_and_commit_flat_tree_materialize_exact_entries(files: dict[str, bytes]) -> None:
    repo = _make_repo()
    entries = {path: repo.store_blob(content) for path, content in files.items()}
    for path, blob_sha in entries.items():
        blob = repo.raw_repo[blob_sha.encode("ascii")]
        assert isinstance(blob, Blob)
        assert blob.data == files[path]

    flat = repo.commit_flat_tree(entries, "flat", parents=[], branch="flat")
    empty = repo.commit_flat_tree({}, "empty", parents=[], branch="empty")

    assert _snapshot(repo, flat) == files
    assert _snapshot(repo, empty) == {}
    assert repo.commit_parent_shas(flat) == []
    assert repo.commit_parent_shas(empty) == []


@settings(deadline=None)
@given(files=path_map)
def test_commit_flat_tree_round_trips_flat_tree_entries(files: dict[str, bytes]) -> None:
    repo = _make_repo()
    source = _commit_model(repo, files)

    recreated = repo.commit_flat_tree(
        repo.flat_tree_entries(source),
        "recreate",
        parents=[source],
        branch="recreated",
    )

    assert _snapshot(repo, recreated) == _snapshot(repo, source)
    assert repo.commit_parent_shas(recreated) == [source]


@settings(deadline=None)
@given(left_files=path_map, right_files=path_map, merge_content=raw_bytes)
def test_flat_tree_merge_commit_surface(
    left_files: dict[str, bytes],
    right_files: dict[str, bytes],
    merge_content: bytes,
) -> None:
    repo = _make_repo()
    left = repo.commit_files(left_files, "left", branch="left")
    right = repo.commit_files(right_files, "right", branch="right")
    entries = repo.flat_tree_entries(right)
    entries.update(repo.flat_tree_entries(left))
    entries["merge.bin"] = repo.store_blob(merge_content)

    merge = repo.commit_flat_tree(entries, "merge", parents=[left, right], branch="merged")

    assert repo.commit_parent_shas(merge) == [left, right]
    assert repo.branch_sha("merged") == merge
    assert repo.log(max_count=1, branch="merged")[0]["sha"] == merge
    assert _flat_snapshot(repo, merge) == entries
    assert repo.diff_commits(merge, left) == _model_diff(_flat_snapshot(repo, merge), _flat_snapshot(repo, left))
    assert repo.diff_commits(merge, right) == _model_diff(_flat_snapshot(repo, merge), _flat_snapshot(repo, right))


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


@settings(deadline=None)
@given(path=nested_path, content=raw_bytes)
def test_path_spelling_matrix_targets_one_normalized_file(path: str, content: bytes) -> None:
    for spelling in _path_spellings(path):
        repo = _make_repo()
        commit = repo.commit_files({spelling: content}, "add spelling")

        for equivalent in _path_spellings(path):
            assert repo.read_file(equivalent, commit=commit) == content

        flat_keys = set(repo.flat_tree_entries(commit))
        assert path in flat_keys
        assert all("\\" not in key for key in flat_keys)


@settings(deadline=None)
@given(path=nested_path, content=raw_bytes)
def test_delete_uses_same_path_normalization_as_add(path: str, content: bytes) -> None:
    repo = _make_repo()
    repo.commit_files({_path_spellings(path)[0]: content}, "add")

    commit = repo.commit_deletes([_path_spellings(path)[-1]], "delete")

    assert path not in repo.flat_tree_entries(commit)
    for spelling in _path_spellings(path):
        with pytest.raises(FileNotFoundError):
            repo.read_file(spelling, commit=commit)


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

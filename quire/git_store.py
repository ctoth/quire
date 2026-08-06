from __future__ import annotations

import os
import json
import threading
import time
from contextlib import contextmanager, nullcontext
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import quote

from dulwich.client import get_transport_and_path
from dulwich.index import build_index_from_tree
from dulwich.graph import find_merge_base
from dulwich.objects import Blob, Commit, ObjectID, Tree
from dulwich.refs import Ref
from dulwich.repo import BaseRepo, MemoryRepo, Repo

from quire.notes import NotesRef, read_git_note, remove_git_note, write_git_note
from quire.refs import RefName
from quire.tree_path import GitTreePath


@dataclass(frozen=True)
class GitStorePolicy:
    author: bytes = b"Quire <quire@example.com>"
    primary_branch: str = "master"
    initial_files: Mapping[str, bytes] = field(default_factory=dict)
    initial_commit_message: str = "Initialize repository"
    ignored_path_prefixes: tuple[str, ...] = ()
    ignored_path_suffixes: tuple[str, ...] = ()

    def ignores_path(self, relpath: str) -> bool:
        normalized = relpath.replace("\\", "/")
        return normalized.startswith(self.ignored_path_prefixes) or normalized.endswith(
            self.ignored_path_suffixes
        )


@dataclass(frozen=True)
class GitBranch:
    name: str
    tip_sha: str
    parent_branch: str = ""
    created_at: int = 0


@dataclass(frozen=True)
class TreeFile:
    relpath: str
    content: bytes


@dataclass(frozen=True)
class MaterializeReport:
    written_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    conflict_paths: tuple[str, ...] = ()


class HeadMismatchError(ValueError):
    def __init__(
        self,
        *,
        branch: str,
        expected_head: str | None,
        actual_head: str | None,
    ) -> None:
        super().__init__(
            f"Branch {branch!r} head mismatch: "
            f"expected {expected_head}, got {actual_head}"
        )
        self.branch = branch
        self.expected_head = expected_head
        self.actual_head = actual_head


class MaterializeConflictError(ValueError):
    def __init__(self, conflict_paths: Sequence[str]) -> None:
        paths = tuple(sorted(conflict_paths))
        super().__init__(f"Materialize would overwrite local edits: {', '.join(paths)}")
        self.conflict_paths = paths


def _normalize_path(path: str | Path) -> str:
    normalized = str(path).replace("\\", "/").strip("/")
    if normalized == ".":
        return ""
    return normalized


def _tree_add(tree: Any, name: bytes, mode: int, object_id: bytes) -> None:
    tree.add(name, mode, object_id)


def _ref_get(refs: Any, ref_name: bytes) -> bytes | None:
    try:
        return refs[ref_name]
    except KeyError:
        return None


def _ref_set(refs: Any, ref_name: bytes, object_id: bytes) -> None:
    old_ref = _ref_get(refs, ref_name)
    if not _ref_set_if_equals(refs, ref_name, old_ref, object_id):
        actual = _ref_get(refs, ref_name)
        raise ValueError(
            f"Ref {ref_name.decode('utf-8', errors='replace')!r} changed: "
            f"expected {_format_ref_value(old_ref)}, got {_format_ref_value(actual)}"
        )


def _ref_delete(refs: Any, ref_name: bytes) -> None:
    old_ref = _ref_get(refs, ref_name)
    if old_ref is not None and not _ref_delete_if_equals(refs, ref_name, old_ref):
        actual = _ref_get(refs, ref_name)
        raise ValueError(
            f"Ref {ref_name.decode('utf-8', errors='replace')!r} changed: "
            f"expected {_format_ref_value(old_ref)}, got {_format_ref_value(actual)}"
        )


def _ref_set_if_equals(refs: Any, ref_name: bytes, old_ref: bytes | None, object_id: bytes) -> bool:
    if old_ref is None:
        return bool(refs.add_if_new(ref_name, object_id))
    return bool(refs.set_if_equals(ref_name, old_ref, object_id))


def _ref_delete_if_equals(refs: Any, ref_name: bytes, old_ref: bytes) -> bool:
    return bool(refs.remove_if_equals(ref_name, old_ref))


def _format_ref_value(value: bytes | None) -> str | None:
    if value is None:
        return None
    return value.decode("ascii")


def _assert_ref_equals(refs: Any, branch_name: str, branch_ref: bytes, expected: bytes | None) -> bytes | None:
    actual = _ref_get(refs, branch_ref)
    if actual != expected:
        raise HeadMismatchError(
            branch=branch_name,
            expected_head=_format_ref_value(expected),
            actual_head=_format_ref_value(actual),
        )
    return actual


def _symref_get(refs: Any, ref_name: bytes) -> bytes | None:
    return refs.get_symrefs().get(ref_name)


def _set_symbolic_ref(refs: Any, name: bytes, target: bytes) -> None:
    refs.set_symbolic_ref(name, target)


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _repo_object(repo: BaseRepo, object_id: bytes) -> Blob | Tree | Commit:
    obj = repo[object_id]
    if isinstance(obj, (Blob, Tree, Commit)):
        return obj
    raise TypeError(f"Unexpected git object type: {type(obj).__name__}")


def _commit_object(repo: BaseRepo, object_id: bytes) -> Commit:
    obj = _repo_object(repo, object_id)
    if isinstance(obj, Commit):
        return obj
    raise TypeError(f"Expected commit object, got {type(obj).__name__}")


def _tree_object(repo: BaseRepo, object_id: bytes) -> Tree:
    obj = _repo_object(repo, object_id)
    if isinstance(obj, Tree):
        return obj
    raise TypeError(f"Expected tree object, got {type(obj).__name__}")


def _branch_meta_ref(name: str) -> RefName:
    return RefName(f"refs/quire/branch-meta/{quote(name, safe='')}")


class HeadBoundTransaction:
    def __init__(self, store: GitStore, branch: str | None = None) -> None:
        self.store = store
        self.branch = store._resolve_write_branch_name(branch)
        self.expected_head: str | None = None
        self._commit_sha: str | None = None
        self._after_commit: list[Callable[[str], None]] = []
        self._mutation_guard: Any | None = None
        self._entered = False
        self._closed = False

    @property
    def commit_sha(self) -> str | None:
        return self._commit_sha

    def __enter__(self) -> HeadBoundTransaction:
        if self._entered:
            raise ValueError("head-bound transaction is already entered")
        guard = self.store._mutation_guard()
        guard.__enter__()
        self._mutation_guard = guard
        self.expected_head = self.store.branch_sha(self.branch)
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None and self._commit_sha is not None:
                for callback in tuple(self._after_commit):
                    callback(self._commit_sha)
        finally:
            self._after_commit.clear()
            self._closed = True
            guard = self._mutation_guard
            self._mutation_guard = None
            if guard is not None:
                guard.__exit__(exc_type, exc, tb)

    def after_commit(self, callback: Callable[[str], None]) -> None:
        self._ensure_open()
        self._after_commit.append(callback)

    def assert_current(self) -> None:
        self._ensure_open()
        actual_head = self.store.branch_sha(self.branch)
        if actual_head != self.expected_head:
            raise HeadMismatchError(
                branch=self.branch,
                expected_head=self.expected_head,
                actual_head=actual_head,
            )

    def commit_files(
        self,
        changes: Mapping[Any, bytes],
        message: str,
    ) -> str:
        return self.commit_batch(changes, [], message)

    def commit_deletes(
        self,
        paths: Sequence[str | Path],
        message: str,
    ) -> str:
        return self.commit_batch({}, paths, message)

    def commit_batch(
        self,
        adds: Mapping[Any, bytes],
        deletes: Sequence[str | Path],
        message: str,
    ) -> str:
        self._ensure_open()
        if self._commit_sha is not None:
            raise ValueError("head-bound transaction is already committed")
        commit_sha = self.store.commit_batch(
            adds,
            deletes,
            message,
            branch=self.branch,
            expected_head=self.expected_head,
        )
        self._commit_sha = commit_sha
        return commit_sha

    def families_transact(self, families: Any, *, message: str) -> _HeadBoundFamilyTransaction:
        self._ensure_open()
        return _HeadBoundFamilyTransaction(self, families, message)

    def _record_commit(self, commit_sha: str | None) -> None:
        if commit_sha is None:
            return
        if self._commit_sha is not None and self._commit_sha != commit_sha:
            raise ValueError("head-bound transaction is already committed")
        self._commit_sha = commit_sha

    def _ensure_open(self) -> None:
        if not self._entered:
            raise ValueError("head-bound transaction has not been entered")
        if self._closed:
            raise ValueError("head-bound transaction is closed")


class _HeadBoundFamilyTransaction:
    def __init__(self, transaction: HeadBoundTransaction, families: Any, message: str) -> None:
        self.transaction = transaction
        self.families = families
        self.message = message
        self._family_transaction: Any = None

    def __enter__(self) -> Any:
        self._family_transaction = self.families.transact(
            message=self.message,
            branch=self.transaction.branch,
            expected_head=self.transaction.expected_head,
        )
        return self._family_transaction.__enter__()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._family_transaction is None:
            return None
        result = self._family_transaction.__exit__(exc_type, exc, tb)
        if exc_type is None:
            self.transaction._record_commit(self._family_transaction.commit_sha)
        return result


class GitStore:
    _OBJECT_CACHE_LIMIT = 8192

    def __init__(
        self,
        dulwich_repo: BaseRepo,
        root: Path | None = None,
        *,
        policy: GitStorePolicy | None = None,
    ) -> None:
        self._repo = dulwich_repo
        self._root = root
        self._policy = policy or GitStorePolicy()
        self._branch_meta: dict[str, dict[str, str | int]] = {}
        self._object_cache: OrderedDict[bytes, Blob | Tree | Commit] = OrderedDict()
        self._mutation_lock = threading.RLock()
        self._mutation_depth = 0

    @property
    def raw_repo(self) -> BaseRepo:
        return self._repo

    @property
    def root(self) -> Path | None:
        return self._root

    @classmethod
    def init(cls, root: Path, *, policy: GitStorePolicy | None = None) -> GitStore:
        root.mkdir(parents=True, exist_ok=True)
        resolved_policy = policy or GitStorePolicy()
        store = cls(Repo.init_bare(str(root / ".git"), mkdir=True), root, policy=resolved_policy)
        if resolved_policy.initial_files:
            initial_files = cast("dict[str | Path, bytes]", dict(resolved_policy.initial_files))
            store.commit_files(initial_files, resolved_policy.initial_commit_message)
        return store

    @classmethod
    def init_memory(cls, *, policy: GitStorePolicy | None = None) -> GitStore:
        resolved_policy = policy or GitStorePolicy()
        store = cls(MemoryRepo(), policy=resolved_policy)
        if resolved_policy.initial_files:
            initial_files = cast("dict[str | Path, bytes]", dict(resolved_policy.initial_files))
            store.commit_files(initial_files, resolved_policy.initial_commit_message)
        return store

    @classmethod
    def open(cls, root: Path, *, policy: GitStorePolicy | None = None) -> GitStore:
        control_dir = root / ".git"
        if control_dir.is_dir() and (control_dir / "HEAD").is_file() and (control_dir / "objects").is_dir():
            return cls(Repo(str(control_dir)), root, policy=policy)
        return cls(Repo(str(root)), root, policy=policy)

    @staticmethod
    def is_repo(root: Path) -> bool:
        control_dir = root / ".git"
        return (
            control_dir.is_dir()
            and (control_dir / "HEAD").is_file()
            and (control_dir / "objects").is_dir()
        )

    def primary_branch_name(self) -> str:
        return self._policy.primary_branch

    def head_bound_transaction(self, branch: str | None = None) -> HeadBoundTransaction:
        return HeadBoundTransaction(self, branch)

    @contextmanager
    def mutation_guard(self) -> Iterator[None]:
        """Public re-entrant guard for a sequence of repository mutations.

        Holds the store's process/file mutation lock for the duration of the
        ``with`` block (re-entrant within a single store instance), so callers
        outside the store can bracket a multi-step write without reaching for the
        private guard.
        """

        with self._mutation_guard():
            yield

    @contextmanager
    def _mutation_guard(self) -> Iterator[None]:
        with self._mutation_lock:
            if self._mutation_depth > 0:
                self._mutation_depth += 1
                try:
                    yield
                finally:
                    self._mutation_depth -= 1
                return

            self._mutation_depth = 1
            try:
                lock_path = self._mutation_lock_path()
                if lock_path is None:
                    yield
                else:
                    with _exclusive_file_lock(lock_path):
                        yield
            finally:
                self._mutation_depth = 0

    def _mutation_lock_path(self) -> Path | None:
        if self._root is None or not isinstance(self._repo, Repo):
            return None
        control_dir = self._root / ".git"
        if control_dir.is_dir():
            return control_dir / "quire.lock"
        return self._root / "quire.lock"

    def current_branch_name(self) -> str | None:
        head_target = _symref_get(self._repo.refs, b"HEAD")
        if head_target is None or not head_target.startswith(b"refs/heads/"):
            return None
        return head_target[len(b"refs/heads/"):].decode("utf-8")

    def set_current_branch(self, name: str) -> None:
        branch_ref = RefName(f"refs/heads/{name}")
        if self.read_ref(branch_ref) is None:
            raise ValueError(f"Branch {name!r} does not exist")
        _set_symbolic_ref(self._repo.refs, b"HEAD", branch_ref.as_bytes())

    def tree(self, commit: str | None = None) -> GitTreePath:
        return GitTreePath(self, commit=commit)

    def exists(self, path: str | Path, commit: str | None = None) -> tuple[int, str] | None:
        path = _normalize_path(path)
        tree = self._get_tree(commit)
        if tree is None:
            return None
        if not path:
            return 0o040000, tree.id.decode("ascii")
        
        parts = PurePosixPath(path).parts
        obj = tree
        mode = 0o040000
        for part in parts:
            if not isinstance(obj, Tree):
                return None
            try:
                mode, sha = obj[part.encode("utf-8")]
            except KeyError:
                return None
            next_obj = self._cached_object(sha)
            if not isinstance(next_obj, (Blob, Tree)):
                return None
            obj = next_obj
        
        return mode, obj.id.decode("ascii")

    def read_file(self, path: str | Path, commit: str | None = None) -> bytes:
        path = _normalize_path(path)
        tree = self._get_tree(commit)
        obj = self._walk_tree(tree, PurePosixPath(path).parts)
        if obj is None or not isinstance(obj, Blob):
            raise FileNotFoundError(path)
        return obj.data

    def object_at(self, path: str | Path, commit: str | None = None) -> Blob | Tree | None:
        path = _normalize_path(path)
        tree = self._get_tree(commit)
        if tree is None:
            return None
        return self._walk_tree(tree, PurePosixPath(path).parts)

    def is_dir(self, path: str | Path, commit: str | None = None) -> bool:
        return isinstance(self.object_at(path, commit=commit), Tree)

    def is_file(self, path: str | Path, commit: str | None = None) -> bool:
        return isinstance(self.object_at(path, commit=commit), Blob)

    def iter_dir(self, subdir: str | Path, commit: str | None = None) -> Iterator[str]:
        subtree = self._subtree(subdir, commit=commit)
        if subtree is None:
            return
        entries = sorted(
            subtree.items(),
            key=lambda entry: entry.path,
        )
        for entry in entries:
            yield entry.path.decode("utf-8")

    def iter_subtree_files(
        self,
        subdir: str | Path,
        commit: str | None = None,
    ) -> Iterator[tuple[str, bytes]]:
        subtree = self._subtree(subdir, commit=commit)
        if subtree is None:
            return
        stack: list[tuple[str, Iterator[Any]]] = [("", iter(sorted(subtree.items(), key=lambda entry: entry.path)))]
        while stack:
            current_prefix, entries = stack[-1]
            try:
                entry = next(entries)
            except StopIteration:
                stack.pop()
                continue
            name = entry.path.decode("utf-8")
            relpath = f"{current_prefix}/{name}" if current_prefix else name
            obj = self._cached_object(entry.sha)
            if isinstance(obj, Tree):
                stack.append((relpath, iter(sorted(obj.items(), key=lambda child: child.path))))
            elif isinstance(obj, Blob):
                yield relpath, obj.data

    def iter_tree_files(
        self,
        *,
        commit: str | None = None,
        roots: Sequence[str | Path] = (),
    ) -> Iterator[TreeFile]:
        selected_roots = tuple(_normalize_path(root) for root in roots)
        if not selected_roots:
            selected_roots = ("",)
        for root in selected_roots:
            for relpath, content in self.iter_subtree_files(root, commit=commit):
                full_path = f"{root}/{relpath}" if root else relpath
                yield TreeFile(full_path, content)

    def iter_dir_entries(
        self,
        subdir: str | Path,
        commit: str | None = None,
    ) -> Iterator[tuple[str, bool]]:
        subtree = self._subtree(subdir, commit=commit)
        if subtree is None:
            return
        entries = sorted(subtree.items(), key=lambda entry: entry.path)
        for entry in entries:
            yield (
                entry.path.decode("utf-8"),
                isinstance(self._cached_object(entry.sha), Tree),
            )

    def commit_files(
        self,
        changes: Mapping[Any, bytes],
        message: str,
        *,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self._mutation_guard():
            return self._commit(adds=changes, deletes=(), message=message, branch=branch, expected_head=expected_head)

    def commit_deletes(
        self,
        paths: Sequence[str | Path],
        message: str,
        *,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self._mutation_guard():
            return self._commit(adds={}, deletes=paths, message=message, branch=branch, expected_head=expected_head)

    def commit_batch(
        self,
        adds: Mapping[Any, bytes],
        deletes: Sequence[str | Path],
        message: str,
        *,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self._mutation_guard():
            return self._commit(adds=adds, deletes=deletes, message=message, branch=branch, expected_head=expected_head)

    def iter_flat_tree_entries(self, commit: str | None = None) -> Iterator[tuple[str, str]]:
        tree = self._get_tree(commit)
        if tree is None:
            return
        stack: list[tuple[str, Tree]] = [("", tree)]
        while stack:
            current_prefix, current_tree = stack.pop()
            for entry in reversed(list(current_tree.items())):
                name = entry.path.decode("utf-8")
                path = f"{current_prefix}/{name}" if current_prefix else name
                obj = self._cached_object(entry.sha)
                if isinstance(obj, Tree):
                    stack.append((path, obj))
                elif isinstance(obj, Blob):
                    yield path, entry.sha.decode("ascii")

    def store_blob(self, payload: bytes) -> str:
        with self._mutation_guard():
            blob = Blob.from_string(payload)
            self._repo.object_store.add_object(blob)
            self._remember_object(blob)
            return blob.id.decode("ascii")

    def iter_unreachable_object_ids(self) -> Iterator[str]:
        """Yield object IDs that are not reachable from any ref."""
        reachable: set[bytes] = set()
        stack = list(self._repo.refs.as_dict().values())
        while stack:
            object_id = stack.pop()
            if object_id in reachable:
                continue
            reachable.add(object_id)
            obj = _repo_object(self._repo, object_id)
            if isinstance(obj, Commit):
                stack.append(obj.tree)
                stack.extend(obj.parents)
            elif isinstance(obj, Tree):
                stack.extend(entry.sha for entry in obj.items())

        for object_id in self._repo.object_store:
            raw_object_id = cast(bytes, object_id)
            if raw_object_id not in reachable:
                yield raw_object_id.decode("ascii")

    def commit_flat_tree(
        self,
        entries: Mapping[str, str | bytes],
        message: str,
        *,
        parents: Sequence[str | bytes],
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self._mutation_guard():
            branch_name = self._resolve_write_branch_name(branch)
            branch_ref = f"refs/heads/{branch_name}".encode()
            current_head = _ref_get(self._repo.refs, branch_ref)
            if expected_head is not None:
                _assert_ref_equals(self._repo.refs, branch_name, branch_ref, expected_head.encode("ascii"))

            normalized_entries = {
                _normalize_path(path): sha if isinstance(sha, bytes) else sha.encode("ascii")
                for path, sha in entries.items()
            }
            _assert_ref_equals(self._repo.refs, branch_name, branch_ref, current_head)
            root_tree = self._build_tree_from_flat(normalized_entries)

            commit = Commit()
            commit.tree = root_tree.id
            commit.author = self._policy.author
            commit.committer = self._policy.author
            commit.encoding = b"UTF-8"
            commit.message = message.encode("utf-8")
            now = int(time.time())
            commit.commit_time = now
            commit.author_time = now
            commit.commit_timezone = 0
            commit.author_timezone = 0
            commit.parents = [
                ObjectID(parent if isinstance(parent, bytes) else parent.encode("ascii"))
                for parent in parents
            ]
            self._repo.object_store.add_object(commit)
            self._remember_object(root_tree)
            self._remember_object(commit)

            if not _ref_set_if_equals(self._repo.refs, branch_ref, current_head, commit.id):
                actual_head = _ref_get(self._repo.refs, branch_ref)
                raise HeadMismatchError(
                    branch=branch_name,
                    expected_head=_format_ref_value(current_head),
                    actual_head=_format_ref_value(actual_head),
                )
            if _symref_get(self._repo.refs, b"HEAD") is None and self.head_sha() is None:
                _set_symbolic_ref(self._repo.refs, b"HEAD", branch_ref)
            return commit.id.decode("ascii")

    def head_sha(self) -> str | None:
        try:
            return self._repo.head().decode("ascii")
        except KeyError:
            return None

    def branch_sha(self, name: str) -> str | None:
        return self.read_ref(RefName(f"refs/heads/{name}"))

    def create_branch(self, name: str, source_commit: str | None = None) -> str:
        with self._mutation_guard():
            ref = RefName(f"refs/heads/{name}")
            if self.read_ref(ref) is not None:
                raise ValueError(f"Branch {name!r} already exists")

            parent_branch = ""
            if source_commit is None:
                current_branch = self.current_branch_name()
                if current_branch is not None:
                    current_ref = self.branch_sha(current_branch)
                    if current_ref is None:
                        raise ValueError(f"Current branch {current_branch!r} has no tip")
                    tip_sha = current_ref
                    parent_branch = current_branch
                else:
                    tip_sha = self.head_sha()
                    if tip_sha is None:
                        raise ValueError("Repository has no commits")
            else:
                tip_sha = source_commit

            if not _ref_set_if_equals(self._repo.refs, ref.as_bytes(), None, tip_sha.encode("ascii")):
                raise ValueError(f"Branch {name!r} already exists")
            created_at = int(time.time())
            meta = {
                "parent_branch": parent_branch,
                "created_at": created_at,
            }
            self._branch_meta[name] = meta
            self.write_blob_ref(
                _branch_meta_ref(name),
                json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
            return tip_sha

    def delete_branch(self, name: str) -> None:
        with self._mutation_guard():
            if self.current_branch_name() == name:
                raise ValueError("Cannot delete current HEAD branch")
            ref = RefName(f"refs/heads/{name}")
            if self.read_ref(ref) is None:
                raise ValueError(f"Branch {name!r} does not exist")
            self.delete_ref(ref)
            self.delete_ref(_branch_meta_ref(name))
            self._branch_meta.pop(name, None)

    def iter_branches(self) -> Iterator[GitBranch]:
        prefix = b"refs/heads/"
        for ref_bytes, sha_bytes in sorted(self._repo.refs.as_dict().items()):
            if not ref_bytes.startswith(prefix):
                continue
            name = ref_bytes[len(prefix):].decode("utf-8")
            meta = self._read_branch_meta(name)
            parent_branch = meta.get("parent_branch", "")
            created_at = meta.get("created_at", 0)
            yield GitBranch(
                name=name,
                tip_sha=sha_bytes.decode("ascii"),
                parent_branch=parent_branch if isinstance(parent_branch, str) else "",
                created_at=created_at if isinstance(created_at, int) else 0,
            )

    def iter_commit_parent_shas(self, commit: str) -> Iterator[str]:
        commit_obj = self._commit_object(commit.encode("ascii"))
        for parent in commit_obj.parents:
            yield parent.decode("ascii")

    def revert_commit(
        self,
        commit_sha: str,
        *,
        message: str | None = None,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        target_commit = self._commit_object(commit_sha.encode("ascii"))
        if len(target_commit.parents) != 1:
            raise ValueError("revert_commit requires a single-parent commit")

        parent_sha = target_commit.parents[0]
        parent_tree = self._tree_object(self._commit_object(parent_sha).tree)
        target_tree = self._tree_object(target_commit.tree)

        parent_entries: dict[str, bytes] = {}
        target_entries: dict[str, bytes] = {}
        self._flatten_tree(parent_tree, "", parent_entries)
        self._flatten_tree(target_tree, "", target_entries)

        branch_name = self._resolve_write_branch_name(branch)
        branch_ref = f"refs/heads/{branch_name}".encode()
        current_head = _ref_get(self._repo.refs, branch_ref)
        if current_head is None:
            raise ValueError(f"Branch {branch_name!r} has no commits")
        if expected_head is not None:
            _assert_ref_equals(self._repo.refs, branch_name, branch_ref, expected_head.encode("ascii"))
        current_tree = self._tree_object(self._commit_object(current_head).tree)
        current_entries: dict[str, bytes] = {}
        self._flatten_tree(current_tree, "", current_entries)

        changed_paths = sorted(set(parent_entries) | set(target_entries))
        changed_paths = [
            path
            for path in changed_paths
            if parent_entries.get(path) != target_entries.get(path)
        ]

        adds: dict[str | Path, bytes] = {}
        deletes: list[str | Path] = []
        for path in changed_paths:
            target_blob = target_entries.get(path)
            current_blob = current_entries.get(path)
            if current_blob != target_blob:
                raise ValueError(f"Cannot revert {commit_sha}: path {path!r} has changed")
            parent_blob = parent_entries.get(path)
            if parent_blob is None:
                deletes.append(path)
                continue
            blob = self._cached_object(parent_blob)
            if not isinstance(blob, Blob):
                raise TypeError(f"Expected blob for {path!r}, got {type(blob).__name__}")
            adds[path] = blob.data

        with self._mutation_guard():
            return self._commit(
                adds=adds,
                deletes=deletes,
                message=message or f"Revert {commit_sha}",
                branch=branch_name,
                expected_head=current_head.decode("ascii"),
            )

    def ancestor_distances(self, start_sha: str) -> dict[str, int]:
        distances: dict[str, int] = {start_sha: 0}
        queue: deque[str] = deque([start_sha])
        while queue:
            current = queue.popleft()
            current_distance = distances[current]
            for parent_sha in self.iter_commit_parent_shas(current):
                next_distance = current_distance + 1
                previous = distances.get(parent_sha)
                if previous is None or next_distance < previous:
                    distances[parent_sha] = next_distance
                    queue.append(parent_sha)
        return distances

    def merge_base(self, branch_a: str, branch_b: str) -> str:
        sha_a = self.branch_sha(branch_a)
        sha_b = self.branch_sha(branch_b)
        if sha_a is None:
            raise ValueError(f"Branch {branch_a!r} does not exist")
        if sha_b is None:
            raise ValueError(f"Branch {branch_b!r} does not exist")
        if sha_a == sha_b:
            return sha_a

        merge_bases = find_merge_base(
            self._repo,
            cast(Any, [sha_a.encode("ascii"), sha_b.encode("ascii")]),
        )
        if not merge_bases:
            raise ValueError(f"No common ancestor between {branch_a!r} and {branch_b!r}")
        return min(merge_base.decode("ascii") for merge_base in merge_bases)

    def read_ref(self, ref: RefName) -> str | None:
        sha = _ref_get(self._repo.refs, ref.as_bytes())
        if sha is None:
            return None
        return sha.decode("ascii")

    def fetch_ref(
        self,
        location: str,
        remote_ref: RefName,
        local_ref: RefName,
        *,
        expected_local: str | None,
    ) -> str:
        """Fetch one advertised ref closure and publish it locally under CAS."""

        remote_ref_bytes = Ref(remote_ref.as_bytes())
        local_ref_bytes = local_ref.as_bytes()
        expected_bytes = expected_local.encode("ascii") if expected_local is not None else None

        with self._mutation_guard():
            _assert_ref_equals(
                self._repo.refs,
                local_ref.value,
                local_ref_bytes,
                expected_bytes,
            )
            advertised_target: ObjectID | None = None

            def determine_wants(
                refs: Mapping[Ref, ObjectID],
                depth: int | None = None,
            ) -> list[ObjectID]:
                del depth
                nonlocal advertised_target
                advertised_target = refs.get(remote_ref_bytes)
                if advertised_target is None:
                    raise ValueError(f"Remote ref {remote_ref.value!r} was not advertised")
                return [advertised_target]

            client, path = get_transport_and_path(location, operation="pull")
            result = client.fetch(
                path,
                self._repo,
                determine_wants=determine_wants,
                ref_prefix=[remote_ref_bytes],
            )
            fetched_target = result.refs.get(remote_ref_bytes)
            if fetched_target is None:
                raise ValueError(f"Remote ref {remote_ref.value!r} was not advertised")
            if advertised_target != fetched_target:
                raise ValueError(f"Remote ref {remote_ref.value!r} changed during fetch")
            _commit_object(self._repo, fetched_target)

            if not _ref_set_if_equals(
                self._repo.refs,
                local_ref_bytes,
                expected_bytes,
                fetched_target,
            ):
                actual = _ref_get(self._repo.refs, local_ref_bytes)
                raise HeadMismatchError(
                    branch=local_ref.value,
                    expected_head=_format_ref_value(expected_bytes),
                    actual_head=_format_ref_value(actual),
                )
            return fetched_target.decode("ascii")

    def write_ref(self, ref: RefName, object_id: str | bytes) -> None:
        with self._mutation_guard():
            sha = object_id if isinstance(object_id, bytes) else object_id.encode("ascii")
            _ref_set(self._repo.refs, ref.as_bytes(), sha)

    def delete_ref(self, ref: RefName, *, expected_ref: str | None = None) -> None:
        with self._mutation_guard():
            ref_name = ref.as_bytes()
            current = _ref_get(self._repo.refs, ref_name)
            if expected_ref is not None:
                current = _assert_ref_equals(
                    self._repo.refs,
                    ref.value,
                    ref_name,
                    expected_ref.encode("ascii"),
                )
            if current is None:
                return
            if not _ref_delete_if_equals(self._repo.refs, ref_name, current):
                actual = _ref_get(self._repo.refs, ref_name)
                raise HeadMismatchError(
                    branch=ref.value,
                    expected_head=_format_ref_value(current),
                    actual_head=_format_ref_value(actual),
                )

    def write_blob_ref(
        self,
        ref: RefName,
        payload: bytes,
        *,
        expected_ref: str | None = None,
    ) -> str:
        with self._mutation_guard():
            ref_name = ref.as_bytes()
            current = _ref_get(self._repo.refs, ref_name)
            if expected_ref is not None:
                current = _assert_ref_equals(
                    self._repo.refs,
                    ref.value,
                    ref_name,
                    expected_ref.encode("ascii"),
                )
            blob = Blob.from_string(payload)
            self._repo.object_store.add_object(blob)
            if not _ref_set_if_equals(self._repo.refs, ref_name, current, blob.id):
                actual = _ref_get(self._repo.refs, ref_name)
                raise HeadMismatchError(
                    branch=ref.value,
                    expected_head=_format_ref_value(current),
                    actual_head=_format_ref_value(actual),
                )
            return blob.id.decode("ascii")

    def read_blob_ref(self, ref: RefName) -> bytes | None:
        sha = self.read_ref(ref)
        if sha is None:
            return None
        obj = self._cached_object(sha.encode("ascii"))
        if not isinstance(obj, Blob):
            raise TypeError(f"Expected blob object at {ref}, got {type(obj).__name__}")
        return obj.data

    def write_note(self, ref: NotesRef, object_sha: str | bytes, payload: bytes) -> str:
        with self._mutation_guard():
            note_commit = write_git_note(
                self._repo,
                ref,
                object_sha,
                payload,
                author=self._policy.author,
                committer=self._policy.author,
                message=b"Write note",
            )
            return note_commit.decode("ascii")

    def read_note(self, ref: NotesRef, object_sha: str | bytes) -> bytes | None:
        return read_git_note(self._repo, ref, object_sha)

    def delete_note(self, ref: NotesRef, object_sha: str | bytes) -> str | None:
        with self._mutation_guard():
            note_commit = remove_git_note(
                self._repo,
                ref,
                object_sha,
                author=self._policy.author,
                committer=self._policy.author,
                message=b"Delete note",
            )
            if note_commit is None:
                return None
            return note_commit.decode("ascii")

    def iter_log(self, max_count: int = 50, *, branch: str | None = None) -> Iterator[dict[str, object]]:
        branch_name = self._resolve_read_branch_name(branch)
        if branch_name is None:
            return
        tip = _ref_get(self._repo.refs, f"refs/heads/{branch_name}".encode())
        if tip is None:
            return
        for entry in self._repo.get_walker(include=cast(Any, [tip]), max_entries=max_count):
            commit = entry.commit
            yield {
                "sha": commit.id.decode("ascii"),
                "message": commit.message.decode("utf-8", errors="replace").strip(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(commit.commit_time)),
                "author": commit.author.decode("utf-8", errors="replace"),
                "parents": [parent.decode("ascii") for parent in commit.parents],
            }

    def materialize_worktree(self, *, remove_extra: bool = False) -> None:
        if self._root is None:
            return
        try:
            head = self._repo.head().decode("ascii")
        except KeyError:
            return
        self.materialize(
            commit=head,
            root=self._root,
            clean=remove_extra,
            ignored_path=self._is_ignored_runtime_path,
            force=True,
        )
        self._refresh_on_disk_index()

    def sync_worktree(self) -> None:
        self.materialize_worktree(remove_extra=True)

    def materialize(
        self,
        *,
        root: str | Path,
        commit: str | None = None,
        branch: str | None = None,
        clean: bool = False,
        clean_roots: Sequence[str | Path] = (),
        ignored_path: Callable[[str], bool] | None = None,
        force: bool = False,
    ) -> MaterializeReport:
        if commit is not None and branch is not None:
            raise ValueError("materialize accepts either commit or branch, not both")
        with self.head_bound_transaction(branch) if branch is not None else nullcontext() as head_txn:
            target_commit = commit
            if branch is not None:
                if not isinstance(head_txn, HeadBoundTransaction):
                    raise TypeError("branch materialization requires a head-bound transaction")
                target_commit = head_txn.expected_head
                if target_commit is None:
                    raise ValueError(f"Branch {branch!r} has no commit")
            if target_commit is None:
                target_commit = self.head_sha()
            if target_commit is None:
                return MaterializeReport()

            target_root = Path(root)
            tree_files = tuple(self.iter_tree_files(commit=target_commit))
            if head_txn is not None:
                head_txn.assert_current()
            conflicts: list[str] = []
            skipped: list[str] = []
            for tree_file in tree_files:
                destination = target_root / PurePosixPath(tree_file.relpath)
                if destination.exists() and destination.is_dir():
                    conflicts.append(tree_file.relpath)
                    continue
                if destination.exists() and destination.read_bytes() == tree_file.content:
                    skipped.append(tree_file.relpath)
                    continue
                if destination.exists() and not force:
                    conflicts.append(tree_file.relpath)
            if conflicts:
                raise MaterializeConflictError(conflicts)

            written: list[str] = []
            for tree_file in tree_files:
                destination = target_root / PurePosixPath(tree_file.relpath)
                if tree_file.relpath in skipped:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(tree_file.content)
                written.append(tree_file.relpath)

            deleted: tuple[str, ...] = ()
            if clean:
                deleted = self._delete_stale_materialized_files(
                    target_root,
                    tracked_paths={tree_file.relpath for tree_file in tree_files},
                    clean_roots=clean_roots,
                    ignored_path=ignored_path,
                )

            return MaterializeReport(
                written_paths=tuple(sorted(written)),
                deleted_paths=deleted,
                skipped_paths=tuple(sorted(skipped)),
            )

    def _refresh_on_disk_index(self) -> None:
        """Rewrite the on-disk git index so it matches HEAD's tree.

        Dulwich does not touch the index during commit-object creation;
        without this step, ``git status`` in the worktree reports every
        tracked file as staged-for-deletion (empty index vs populated
        HEAD tree) and every on-disk file as untracked. A subsequent
        ``git commit`` would then silently wipe the tree.

        Skipped for in-memory repositories, which have no on-disk index.
        """
        if self._root is None:
            return
        if not isinstance(self._repo, Repo):
            return
        try:
            head = self._repo.head()
        except KeyError:
            return
        commit = self._commit_object(head)
        build_index_from_tree(
            self._repo.path,
            self._repo.index_path(),
            self._repo.object_store,
            commit.tree,
        )

    def diff_commits(
        self,
        commit1: str | None = None,
        commit2: str | None = None,
    ) -> dict[str, list[str]]:
        if commit1 is None:
            try:
                commit1 = self._repo.head().decode("ascii")
            except KeyError:
                return {"added": [], "modified": [], "deleted": []}

        if commit2 is None:
            commit1_obj = self._commit_object(commit1.encode("ascii"))
            commit2 = commit1_obj.parents[0].decode("ascii") if commit1_obj.parents else None

        entries1: dict[str, bytes] = {}
        tree1 = self._get_tree(commit1)
        if tree1 is not None:
            self._flatten_tree(tree1, "", entries1)

        entries2: dict[str, bytes] = {}
        if commit2 is not None:
            tree2 = self._get_tree(commit2)
            if tree2 is not None:
                self._flatten_tree(tree2, "", entries2)

        return {
            "added": sorted(path for path in entries1 if path not in entries2),
            "modified": sorted(
                path for path in entries1 if path in entries2 and entries1[path] != entries2[path]
            ),
            "deleted": sorted(path for path in entries2 if path not in entries1),
        }

    def show_commit(self, sha: str) -> dict[str, object]:
        commit = self._commit_object(sha.encode("ascii"))
        parent_sha = commit.parents[0].decode("ascii") if commit.parents else None
        diff = self.diff_commits(sha, parent_sha)
        return {
            "sha": sha,
            "message": commit.message.decode("utf-8", errors="replace").strip(),
            "author": commit.author.decode("utf-8", errors="replace"),
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(commit.commit_time)),
            "added": diff["added"],
            "modified": diff["modified"],
            "deleted": diff["deleted"],
        }

    def _resolve_read_branch_name(self, branch: str | None = None) -> str | None:
        if branch:
            return branch
        current = self.current_branch_name()
        if current:
            return current
        primary = self.primary_branch_name()
        if self.branch_sha(primary) is not None:
            return primary
        return None

    def _resolve_write_branch_name(self, branch: str | None = None) -> str:
        if branch:
            return branch
        current = self.current_branch_name()
        if current:
            return current
        return self.primary_branch_name()

    def _commit(
        self,
        adds: Mapping[Any, bytes],
        deletes: Sequence[str | Path],
        message: str,
        branch: str | None,
        expected_head: str | None,
    ) -> str:
        branch_name = self._resolve_write_branch_name(branch)
        branch_ref = f"refs/heads/{branch_name}".encode()
        store = self._repo.object_store
        tip_sha = _ref_get(self._repo.refs, branch_ref)
        if expected_head is not None:
            _assert_ref_equals(self._repo.refs, branch_name, branch_ref, expected_head.encode("ascii"))
        if tip_sha is None:
            base_tree = None
            parents: list[ObjectID] = []
        else:
            parent_commit = self._commit_object(tip_sha)
            base_tree = self._tree_object(parent_commit.tree)
            parents = [ObjectID(tip_sha)]

        add_blobs: dict[tuple[str, ...], Blob] = {}
        for path, content in adds.items():
            blob = Blob.from_string(content)
            normalized = _normalize_path(path)
            parts = PurePosixPath(normalized).parts
            if not parts:
                raise ValueError("Tree entry path must not be empty")
            add_blobs[parts] = blob
        delete_parts = [
            PurePosixPath(normalized).parts
            for path in deletes
            if (normalized := _normalize_path(path))
        ]

        _assert_ref_equals(self._repo.refs, branch_name, branch_ref, tip_sha)
        for blob in add_blobs.values():
            store.add_object(blob)
        add_blob_ids = {parts: blob.id for parts, blob in add_blobs.items()}
        root_tree = self._apply_tree_changes(base_tree, add_blob_ids, delete_parts)
        commit = Commit()
        commit.tree = root_tree.id
        commit.author = self._policy.author
        commit.committer = self._policy.author
        commit.encoding = b"UTF-8"
        commit.message = message.encode("utf-8")
        now = int(time.time())
        commit.commit_time = now
        commit.author_time = now
        commit.commit_timezone = 0
        commit.author_timezone = 0
        commit.parents = parents
        store.add_object(commit)
        self._remember_object(commit)
        if not _ref_set_if_equals(self._repo.refs, branch_ref, tip_sha, commit.id):
            actual_head = _ref_get(self._repo.refs, branch_ref)
            raise HeadMismatchError(
                branch=branch_name,
                expected_head=_format_ref_value(tip_sha),
                actual_head=_format_ref_value(actual_head),
            )
        if _symref_get(self._repo.refs, b"HEAD") is None and self.head_sha() is None:
            _set_symbolic_ref(self._repo.refs, b"HEAD", branch_ref)
        return commit.id.decode("ascii")

    def _read_branch_meta(self, name: str) -> dict[str, str | int]:
        cached = self._branch_meta.get(name)
        if cached is not None:
            return cached
        payload = self.read_blob_ref(_branch_meta_ref(name))
        if payload is None:
            return {}
        try:
            loaded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        parent_branch = loaded.get("parent_branch", "")
        created_at = loaded.get("created_at", 0)
        meta: dict[str, str | int] = {
            "parent_branch": parent_branch if isinstance(parent_branch, str) else "",
            "created_at": created_at if isinstance(created_at, int) else 0,
        }
        self._branch_meta[name] = meta
        return meta

    def _subtree(self, subdir: str | Path, *, commit: str | None) -> Tree | None:
        subdir = _normalize_path(subdir)
        tree = self._get_tree(commit)
        if tree is None:
            return None
        if not subdir:
            return tree
        obj = self._walk_tree(tree, PurePosixPath(subdir).parts)
        if obj is None or not isinstance(obj, Tree):
            return None
        return obj

    def _get_tree(self, commit: str | None = None) -> Tree | None:
        try:
            if commit is not None:
                commit_obj = self._commit_object(commit.encode("ascii"))
            else:
                commit_obj = self._commit_object(self._repo.head())
        except KeyError:
            return None
        return self._tree_object(commit_obj.tree)

    def _walk_tree(self, tree: Tree | None, parts: tuple[str, ...]) -> Blob | Tree | None:
        if tree is None:
            return None
        obj: Blob | Tree = tree
        for part in parts:
            if not isinstance(obj, Tree):
                return None
            try:
                _mode, sha = obj[part.encode("utf-8")]
            except KeyError:
                return None
            next_obj = self._cached_object(sha)
            if not isinstance(next_obj, (Blob, Tree)):
                return None
            obj = next_obj
        return obj

    def _flatten_tree(self, tree: Tree, prefix: str, out: dict[str, bytes]) -> None:
        stack: list[tuple[str, Tree]] = [(prefix, tree)]
        while stack:
            current_prefix, current_tree = stack.pop()
            for entry in reversed(list(current_tree.items())):
                name = entry.path.decode("utf-8")
                path = f"{current_prefix}/{name}" if current_prefix else name
                obj = self._cached_object(entry.sha)
                if isinstance(obj, Tree):
                    stack.append((path, obj))
                elif isinstance(obj, Blob):
                    out[path] = entry.sha

    def _apply_tree_changes(
        self,
        base_tree: Tree | None,
        adds: Mapping[tuple[str, ...], bytes],
        deletes: Sequence[tuple[str, ...]],
    ) -> Tree:
        self._check_add_path_conflicts(adds)
        touched_dirs: set[tuple[str, ...]] = {()}
        add_parent_dirs: set[tuple[str, ...]] = set()
        for parts in adds:
            for index in range(len(parts)):
                directory = parts[:index]
                touched_dirs.add(directory)
                add_parent_dirs.add(directory)

        effective_deletes: list[tuple[str, ...]] = []
        for parts in deletes:
            if not parts:
                continue
            parent = parts[:-1]
            parent_obj = self._tree_at(base_tree, parent)
            if isinstance(parent_obj, Blob) and parent not in add_parent_dirs:
                continue
            effective_deletes.append(parts)
            for index in range(len(parts)):
                touched_dirs.add(parts[:index])

        entries_by_dir: dict[tuple[str, ...], dict[str, tuple[int, bytes]]] = {}
        for directory in sorted(touched_dirs, key=lambda item: (len(item), item)):
            obj = self._tree_at(base_tree, directory)
            if isinstance(obj, Blob):
                raise ValueError(f"path conflict at {'/'.join(directory)}")
            entries: dict[str, tuple[int, bytes]] = {}
            if isinstance(obj, Tree):
                entries = {
                    entry.path.decode("utf-8"): (entry.mode, entry.sha)
                    for entry in obj.items()
                }
            entries_by_dir[directory] = entries

        for parts, sha in adds.items():
            parent = parts[:-1]
            name = parts[-1]
            entries = entries_by_dir[parent]
            existing = entries.get(name)
            if existing is not None and isinstance(self._cached_object(existing[1]), Tree):
                raise ValueError(f"path conflict at {'/'.join(parts)}")
            entries[name] = (0o100644, sha)

        for parts in effective_deletes:
            parent = parts[:-1]
            name = parts[-1]
            delete_entries = entries_by_dir.get(parent)
            if delete_entries is None:
                continue
            existing = delete_entries.get(name)
            if existing is not None and not isinstance(self._cached_object(existing[1]), Tree):
                delete_entries.pop(name)

        root_tree: Tree | None = None
        for directory in sorted(touched_dirs, key=lambda item: (len(item), item), reverse=True):
            entries = entries_by_dir[directory]
            if directory and not entries:
                parent = directory[:-1]
                if parent in entries_by_dir:
                    entries_by_dir[parent].pop(directory[-1], None)
                continue

            tree = Tree()
            for name, (mode, sha) in sorted(entries.items()):
                _tree_add(tree, name.encode("utf-8"), mode, sha)
            self._repo.object_store.add_object(tree)
            self._remember_object(tree)
            if not directory:
                root_tree = tree
                continue
            parent = directory[:-1]
            if parent in entries_by_dir:
                entries_by_dir[parent][directory[-1]] = (0o040000, tree.id)

        if root_tree is None:
            raise RuntimeError("tree edit did not produce a root tree")
        return root_tree

    def _check_add_path_conflicts(self, adds: Mapping[tuple[str, ...], bytes]) -> None:
        add_paths = set(adds)
        for parts in add_paths:
            if not parts:
                raise ValueError("Tree entry path must not be empty")
            for index in range(1, len(parts)):
                if parts[:index] in add_paths:
                    raise ValueError(f"path conflict at {'/'.join(parts[:index])}")

    def _tree_at(self, base_tree: Tree | None, directory: tuple[str, ...]) -> Blob | Tree | None:
        if base_tree is None:
            return None
        obj: Blob | Tree = base_tree
        for part in directory:
            if not isinstance(obj, Tree):
                return obj
            try:
                _mode, sha = obj[part.encode("utf-8")]
            except KeyError:
                return None
            next_obj = self._cached_object(sha)
            if not isinstance(next_obj, (Blob, Tree)):
                return None
            obj = next_obj
        return obj

    def _build_tree_from_flat(self, entries: dict[str, bytes]) -> Tree:
        direct_blobs: dict[tuple[str, ...], list[tuple[str, bytes]]] = {(): []}
        child_dirs: dict[tuple[str, ...], set[str]] = {(): set()}

        for path, sha in entries.items():
            parts = PurePosixPath(path).parts
            if not parts:
                raise ValueError("Tree entry path must not be empty")

            parent = tuple(parts[:-1])
            filename = parts[-1]
            direct_blobs.setdefault(parent, []).append((filename, sha))
            child_dirs.setdefault(parent, set())

            for index in range(len(parent) + 1):
                current = tuple(parts[:index])
                child_dirs.setdefault(current, set())
                if index < len(parent):
                    child_dirs[current].add(parts[index])

        built_trees: dict[tuple[str, ...], Tree] = {}
        directories = sorted(child_dirs, key=lambda directory: (len(directory), directory), reverse=True)
        for directory in directories:
            tree = Tree()
            for name, sha in sorted(direct_blobs.get(directory, [])):
                _tree_add(tree, name.encode("utf-8"), 0o100644, sha)
            for dirname in sorted(child_dirs[directory]):
                subtree = built_trees[(*directory, dirname)]
                _tree_add(tree, dirname.encode("utf-8"), 0o040000, subtree.id)
            self._repo.object_store.add_object(tree)
            self._remember_object(tree)
            built_trees[directory] = tree

        return built_trees[()]

    def _collect_tree_paths(self, tree: Tree, prefix: str, out: set[str]) -> None:
        stack: list[tuple[str, Tree]] = [(prefix, tree)]
        while stack:
            current_prefix, current_tree = stack.pop()
            for entry in reversed(list(current_tree.items())):
                name = entry.path.decode("utf-8")
                path = f"{current_prefix}/{name}" if current_prefix else name
                obj = self._cached_object(entry.sha)
                if isinstance(obj, Tree):
                    stack.append((path, obj))
                elif isinstance(obj, Blob):
                    out.add(path)

    def _cached_object(self, object_id: bytes) -> Blob | Tree | Commit:
        with self._mutation_lock:
            cached = self._object_cache.get(object_id)
            if cached is not None:
                self._object_cache.move_to_end(object_id)
                return cached
        obj = _repo_object(self._repo, object_id)
        self._remember_object(obj)
        return obj

    def _remember_object(self, obj: Blob | Tree | Commit) -> None:
        with self._mutation_lock:
            object_id = obj.id
            self._object_cache[object_id] = obj
            self._object_cache.move_to_end(object_id)
            while len(self._object_cache) > self._OBJECT_CACHE_LIMIT:
                self._object_cache.popitem(last=False)

    def _commit_object(self, object_id: bytes) -> Commit:
        obj = self._cached_object(object_id)
        if isinstance(obj, Commit):
            return obj
        raise TypeError(f"Expected commit object, got {type(obj).__name__}")

    def _tree_object(self, object_id: bytes) -> Tree:
        obj = self._cached_object(object_id)
        if isinstance(obj, Tree):
            return obj
        raise TypeError(f"Expected tree object, got {type(obj).__name__}")

    def _remove_extra_worktree_files(self, tracked_paths: set[str]) -> None:
        if self._root is None:
            return
        self._delete_stale_materialized_files(
            self._root,
            tracked_paths=tracked_paths,
            clean_roots=(),
            ignored_path=self._is_ignored_runtime_path,
        )

    def _delete_stale_materialized_files(
        self,
        root: Path,
        *,
        tracked_paths: set[str],
        clean_roots: Sequence[str | Path],
        ignored_path: Callable[[str], bool] | None,
    ) -> tuple[str, ...]:
        prune_candidates: set[Path] = set()
        deleted: list[str] = []
        roots = tuple(_normalize_path(clean_root) for clean_root in clean_roots)
        search_roots = tuple(root / PurePosixPath(clean_root) for clean_root in roots) if roots else (root,)
        for search_root in search_roots:
            if not search_root.exists():
                continue
            candidates = search_root.rglob("*") if search_root.is_dir() else (search_root,)
            for disk_file in candidates:
                if not disk_file.is_file():
                    continue
                rel = disk_file.relative_to(root).as_posix()
                if rel.startswith(".git/") or rel == ".git":
                    continue
                if ignored_path is not None and ignored_path(rel):
                    continue
                if rel not in tracked_paths:
                    disk_file.unlink()
                    deleted.append(rel)
                    parent = disk_file.parent
                    while parent != root:
                        prune_candidates.add(parent)
                        parent = parent.parent
        for directory in sorted(
            prune_candidates,
            key=lambda path: len(path.relative_to(root).parts),
            reverse=True,
        ):
            rel = directory.relative_to(root).as_posix()
            if rel.startswith(".git/") or rel == ".git":
                continue
            if ignored_path is not None and ignored_path(rel):
                continue
            try:
                directory.rmdir()
            except OSError:
                continue
        return tuple(sorted(deleted))

    def _is_ignored_runtime_path(self, relpath: str) -> bool:
        return self._policy.ignores_path(relpath)

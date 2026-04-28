from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import quote

from dulwich.index import build_index_from_tree
from dulwich.objects import Blob, Commit, Tree
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


@dataclass(frozen=True)
class GitBranch:
    name: str
    tip_sha: str
    parent_branch: str = ""
    created_at: int = 0


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
        raise ValueError(
            f"Branch {branch_name!r} head mismatch: "
            f"expected {_format_ref_value(expected)}, got {_format_ref_value(actual)}"
        )
    return actual


def _symref_get(refs: Any, ref_name: bytes) -> bytes | None:
    return refs.get_symrefs().get(ref_name)


def _set_symbolic_ref(refs: Any, name: bytes, target: bytes) -> None:
    refs.set_symbolic_ref(name, target)


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
                bool(entry.mode & 0o040000),
            )

    def commit_files(
        self,
        changes: Mapping[str | Path, bytes],
        message: str,
        *,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self._mutation_lock:
            return self._commit(adds=changes, deletes=(), message=message, branch=branch, expected_head=expected_head)

    def commit_deletes(
        self,
        paths: Sequence[str | Path],
        message: str,
        *,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self._mutation_lock:
            return self._commit(adds={}, deletes=paths, message=message, branch=branch, expected_head=expected_head)

    def commit_batch(
        self,
        adds: Mapping[str | Path, bytes],
        deletes: Sequence[str | Path],
        message: str,
        *,
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self._mutation_lock:
            return self._commit(adds=adds, deletes=deletes, message=message, branch=branch, expected_head=expected_head)

    def flat_tree_entries(self, commit: str | None = None) -> dict[str, str]:
        tree = self._get_tree(commit)
        if tree is None:
            return {}
        entries: dict[str, bytes] = {}
        self._flatten_tree(tree, "", entries)
        return {path: sha.decode("ascii") for path, sha in entries.items()}

    def store_blob(self, payload: bytes) -> str:
        with self._mutation_lock:
            blob = Blob.from_string(payload)
            self._repo.object_store.add_object(blob)
            self._remember_object(blob)
            return blob.id.decode("ascii")

    def commit_flat_tree(
        self,
        entries: Mapping[str, str | bytes],
        message: str,
        *,
        parents: Sequence[str | bytes],
        branch: str | None = None,
        expected_head: str | None = None,
    ) -> str:
        with self._mutation_lock:
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
                parent if isinstance(parent, bytes) else parent.encode("ascii")
                for parent in parents
            ]
            self._repo.object_store.add_object(commit)
            self._remember_object(root_tree)
            self._remember_object(commit)

            if not _ref_set_if_equals(self._repo.refs, branch_ref, current_head, commit.id):
                actual_head = _ref_get(self._repo.refs, branch_ref)
                actual = None if actual_head is None else actual_head.decode("ascii")
                expected = None if current_head is None else current_head.decode("ascii")
                raise ValueError(
                    f"Branch {branch_name!r} head mismatch: expected {expected}, got {actual}"
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
        with self._mutation_lock:
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
        with self._mutation_lock:
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

    def commit_parent_shas(self, commit: str) -> list[str]:
        commit_obj = self._commit_object(commit.encode("ascii"))
        return [parent.decode("ascii") for parent in commit_obj.parents]

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

        with self._mutation_lock:
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
            for parent_sha in self.commit_parent_shas(current):
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

        distances_a = self.ancestor_distances(sha_a)
        distances_b = self.ancestor_distances(sha_b)
        common_ancestors = set(distances_a) & set(distances_b)
        if not common_ancestors:
            raise ValueError(f"No common ancestor between {branch_a!r} and {branch_b!r}")

        ancestor_cache = {
            ancestor_sha: self.ancestor_distances(ancestor_sha)
            for ancestor_sha in common_ancestors
        }
        best_common_ancestors = {
            candidate
            for candidate in common_ancestors
            if not any(
                other != candidate and candidate in ancestor_cache[other]
                for other in common_ancestors
            )
        }

        return min(
            best_common_ancestors,
            key=lambda sha: (
                max(distances_a[sha], distances_b[sha]),
                distances_a[sha] + distances_b[sha],
                sha,
            ),
        )

    def read_ref(self, ref: RefName) -> str | None:
        sha = _ref_get(self._repo.refs, ref.as_bytes())
        if sha is None:
            return None
        return sha.decode("ascii")

    def write_ref(self, ref: RefName, object_id: str | bytes) -> None:
        with self._mutation_lock:
            sha = object_id if isinstance(object_id, bytes) else object_id.encode("ascii")
            _ref_set(self._repo.refs, ref.as_bytes(), sha)

    def delete_ref(self, ref: RefName) -> None:
        with self._mutation_lock:
            if self.read_ref(ref) is not None:
                _ref_delete(self._repo.refs, ref.as_bytes())

    def write_blob_ref(self, ref: RefName, payload: bytes) -> str:
        with self._mutation_lock:
            blob = Blob.from_string(payload)
            self._repo.object_store.add_object(blob)
            self.write_ref(ref, blob.id)
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
        with self._mutation_lock:
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
        with self._mutation_lock:
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

    def log(self, max_count: int = 50, *, branch: str | None = None) -> list[dict[str, object]]:
        branch_name = self._resolve_read_branch_name(branch)
        if branch_name is None:
            return []
        tip = _ref_get(self._repo.refs, f"refs/heads/{branch_name}".encode())
        if tip is None:
            return []
        result: list[dict[str, object]] = []
        for entry in self._repo.get_walker(include=cast(Any, [tip]), max_entries=max_count):
            commit = entry.commit
            result.append({
                "sha": commit.id.decode("ascii"),
                "message": commit.message.decode("utf-8", errors="replace").strip(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(commit.commit_time)),
                "author": commit.author.decode("utf-8", errors="replace"),
                "parents": [parent.decode("ascii") for parent in commit.parents],
            })
        return result

    def materialize_worktree(self, *, remove_extra: bool = False) -> None:
        if self._root is None:
            return
        try:
            head = self._repo.head()
        except KeyError:
            return
        commit = self._commit_object(head)
        tree = self._tree_object(commit.tree)
        paths: set[str] = set()
        self._collect_tree_paths(tree, "", paths)
        for rel_path in paths:
            abs_path = self._root / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            blob = self._walk_tree(tree, PurePosixPath(rel_path).parts)
            if isinstance(blob, Blob):
                abs_path.write_bytes(blob.data)
        if remove_extra:
            self._remove_extra_worktree_files(paths)
        self._refresh_on_disk_index()

    def sync_worktree(self) -> None:
        self.materialize_worktree(remove_extra=True)

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
        adds: Mapping[str | Path, bytes],
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
            parents: list[bytes] = []
        else:
            parent_commit = self._commit_object(tip_sha)
            base_tree = self._tree_object(parent_commit.tree)
            parents = [tip_sha]

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
            actual = None if actual_head is None else actual_head.decode("ascii")
            expected = None if tip_sha is None else tip_sha.decode("ascii")
            raise ValueError(
                f"Branch {branch_name!r} head mismatch: expected {expected}, got {actual}"
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
            if existing is not None and existing[0] & 0o040000:
                raise ValueError(f"path conflict at {'/'.join(parts)}")
            entries[name] = (0o100644, sha)

        for parts in effective_deletes:
            parent = parts[:-1]
            name = parts[-1]
            entries = entries_by_dir.get(parent)
            if entries is None:
                continue
            existing = entries.get(name)
            if existing is not None and not existing[0] & 0o040000:
                entries.pop(name)

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
        prune_candidates: set[Path] = set()
        for disk_file in self._root.rglob("*"):
            if not disk_file.is_file():
                continue
            rel = disk_file.relative_to(self._root).as_posix()
            if rel.startswith(".git/") or rel == ".git":
                continue
            if self._is_ignored_runtime_path(rel):
                continue
            if rel not in tracked_paths:
                disk_file.unlink()
                parent = disk_file.parent
                while parent != self._root:
                    prune_candidates.add(parent)
                    parent = parent.parent
        for directory in sorted(
            prune_candidates,
            key=lambda path: len(path.relative_to(self._root).parts),
            reverse=True,
        ):
            rel = directory.relative_to(self._root).as_posix()
            if rel.startswith(".git/") or rel == ".git":
                continue
            if self._is_ignored_runtime_path(rel):
                continue
            try:
                directory.rmdir()
            except OSError:
                continue

    def _is_ignored_runtime_path(self, relpath: str) -> bool:
        normalized = relpath.replace("\\", "/")
        return normalized.startswith(self._policy.ignored_path_prefixes) or normalized.endswith(
            self._policy.ignored_path_suffixes
        )

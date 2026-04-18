from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

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


def _normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")


def _ref_get(refs: Any, ref_name: bytes) -> bytes | None:
    try:
        return refs[ref_name]
    except KeyError:
        return None


def _ref_set(refs: Any, ref_name: bytes, object_id: bytes) -> None:
    refs[ref_name] = object_id


def _ref_delete(refs: Any, ref_name: bytes) -> None:
    del refs[ref_name]


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


class GitStore:
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
        store = cls(Repo.init(str(root)), root, policy=resolved_policy)
        if resolved_policy.initial_files:
            store.commit_files(resolved_policy.initial_files, "Initialize repository")
        return store

    @classmethod
    def init_memory(cls, *, policy: GitStorePolicy | None = None) -> GitStore:
        resolved_policy = policy or GitStorePolicy()
        store = cls(MemoryRepo(), policy=resolved_policy)
        if resolved_policy.initial_files:
            store.commit_files(resolved_policy.initial_files, "Initialize repository")
        return store

    @classmethod
    def open(cls, root: Path, *, policy: GitStorePolicy | None = None) -> GitStore:
        return cls(Repo(str(root)), root, policy=policy)

    @staticmethod
    def is_repo(root: Path) -> bool:
        return (root / ".git").is_dir()

    def primary_branch_name(self) -> str:
        return self._policy.primary_branch

    def current_branch_name(self) -> str | None:
        head_target = _symref_get(self._repo.refs, b"HEAD")
        if head_target is None or not head_target.startswith(b"refs/heads/"):
            return None
        return head_target[len(b"refs/heads/"):].decode("utf-8")

    def tree(self, commit: str | None = None) -> GitTreePath:
        return GitTreePath(self, commit=commit)

    def read_file(self, path: str | Path, commit: str | None = None) -> bytes:
        path = _normalize_path(path)
        tree = self._get_tree(commit)
        obj = self._walk_tree(tree, PurePosixPath(path).parts)
        if obj is None or not isinstance(obj, Blob):
            raise FileNotFoundError(path)
        return obj.data

    def list_dir(self, subdir: str | Path, commit: str | None = None) -> list[str]:
        subtree = self._subtree(subdir, commit=commit)
        if subtree is None:
            return []
        return sorted(
            entry.path.decode("utf-8")
            for entry in subtree.items()
            if entry.mode & 0o100000
        )

    def list_dir_entries(
        self,
        subdir: str | Path,
        commit: str | None = None,
    ) -> list[tuple[str, bool]]:
        subtree = self._subtree(subdir, commit=commit)
        if subtree is None:
            return []
        return sorted(
            (
                entry.path.decode("utf-8"),
                bool(entry.mode & 0o040000),
            )
            for entry in subtree.items()
        )

    def commit_files(
        self,
        changes: Mapping[str | Path, bytes],
        message: str,
        *,
        branch: str | None = None,
    ) -> str:
        return self._commit(adds=changes, deletes=(), message=message, branch=branch)

    def commit_deletes(
        self,
        paths: Sequence[str | Path],
        message: str,
        *,
        branch: str | None = None,
    ) -> str:
        return self._commit(adds={}, deletes=paths, message=message, branch=branch)

    def commit_batch(
        self,
        adds: Mapping[str | Path, bytes],
        deletes: Sequence[str | Path],
        message: str,
        *,
        branch: str | None = None,
    ) -> str:
        return self._commit(adds=adds, deletes=deletes, message=message, branch=branch)

    def head_sha(self) -> str | None:
        try:
            return self._repo.head().decode("ascii")
        except KeyError:
            return None

    def branch_sha(self, name: str) -> str | None:
        return self.read_ref(RefName(f"refs/heads/{name}"))

    def read_ref(self, ref: RefName) -> str | None:
        sha = _ref_get(self._repo.refs, ref.as_bytes())
        if sha is None:
            return None
        return sha.decode("ascii")

    def write_ref(self, ref: RefName, object_id: str | bytes) -> None:
        sha = object_id if isinstance(object_id, bytes) else object_id.encode("ascii")
        _ref_set(self._repo.refs, ref.as_bytes(), sha)

    def delete_ref(self, ref: RefName) -> None:
        if self.read_ref(ref) is not None:
            _ref_delete(self._repo.refs, ref.as_bytes())

    def write_note(self, ref: NotesRef, object_sha: str | bytes, payload: bytes) -> str:
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
        for entry in self._repo.get_walker(include=[tip], max_entries=max_count):
            commit = entry.commit
            result.append({
                "sha": commit.id.decode("ascii"),
                "message": commit.message.decode("utf-8", errors="replace").strip(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(commit.commit_time)),
                "author": commit.author.decode("utf-8", errors="replace"),
                "parents": [parent.decode("ascii") for parent in commit.parents],
            })
        return result

    def materialize_worktree(self) -> None:
        if self._root is None:
            return
        try:
            head = self._repo.head()
        except KeyError:
            return
        commit = _commit_object(self._repo, head)
        tree = _tree_object(self._repo, commit.tree)
        paths: set[str] = set()
        self._collect_tree_paths(tree, "", paths)
        for rel_path in paths:
            abs_path = self._root / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            blob = self._walk_tree(tree, PurePosixPath(rel_path).parts)
            if isinstance(blob, Blob):
                abs_path.write_bytes(blob.data)

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
    ) -> str:
        branch_name = self._resolve_write_branch_name(branch)
        branch_ref = f"refs/heads/{branch_name}".encode()
        store = self._repo.object_store
        tip_sha = _ref_get(self._repo.refs, branch_ref)
        if tip_sha is None:
            base_tree = None
            parents: list[bytes] = []
        else:
            parent_commit = _commit_object(self._repo, tip_sha)
            base_tree = _tree_object(self._repo, parent_commit.tree)
            parents = [tip_sha]

        entries: dict[str, bytes] = {}
        if base_tree is not None:
            self._flatten_tree(base_tree, "", entries)

        for path, content in adds.items():
            blob = Blob.from_string(content)
            store.add_object(blob)
            entries[_normalize_path(path)] = blob.id
        for path in deletes:
            entries.pop(_normalize_path(path), None)

        root_tree = self._build_tree_from_flat(entries)
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
        _ref_set(self._repo.refs, branch_ref, commit.id)
        if _symref_get(self._repo.refs, b"HEAD") is None and self.head_sha() is None:
            _set_symbolic_ref(self._repo.refs, b"HEAD", branch_ref)
        return commit.id.decode("ascii")

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
                commit_obj = _commit_object(self._repo, commit.encode("ascii"))
            else:
                commit_obj = _commit_object(self._repo, self._repo.head())
        except KeyError:
            return None
        return _tree_object(self._repo, commit_obj.tree)

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
            obj = _repo_object(self._repo, sha)
        return obj

    def _flatten_tree(self, tree: Tree, prefix: str, out: dict[str, bytes]) -> None:
        for entry in tree.items():
            name = entry.path.decode("utf-8")
            path = f"{prefix}/{name}" if prefix else name
            obj = _repo_object(self._repo, entry.sha)
            if isinstance(obj, Tree):
                self._flatten_tree(obj, path, out)
            elif isinstance(obj, Blob):
                out[path] = entry.sha

    def _build_tree_from_flat(self, entries: dict[str, bytes]) -> Tree:
        children: dict[str, list[tuple[str, bytes]]] = {}
        direct_blobs: list[tuple[str, bytes]] = []
        for path, sha in entries.items():
            parts = path.split("/", 1)
            if len(parts) == 1:
                direct_blobs.append((parts[0], sha))
            else:
                children.setdefault(parts[0], []).append((parts[1], sha))

        tree = Tree()
        for name, sha in sorted(direct_blobs):
            tree.add(name.encode("utf-8"), 0o100644, sha)
        for dirname in sorted(children):
            sub_entries = {rest: sha for rest, sha in children[dirname]}
            subtree = self._build_tree_from_flat(sub_entries)
            self._repo.object_store.add_object(subtree)
            tree.add(dirname.encode("utf-8"), 0o040000, subtree.id)
        self._repo.object_store.add_object(tree)
        return tree

    def _collect_tree_paths(self, tree: Tree, prefix: str, out: set[str]) -> None:
        for entry in tree.items():
            name = entry.path.decode("utf-8")
            path = f"{prefix}/{name}" if prefix else name
            obj = _repo_object(self._repo, entry.sha)
            if isinstance(obj, Tree):
                self._collect_tree_paths(obj, path, out)
            elif isinstance(obj, Blob):
                out.add(path)

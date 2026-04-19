from __future__ import annotations

from abc import ABC, abstractmethod
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator, Protocol, Self, TextIO


class TreePath(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def stem(self) -> str: ...
    @property
    def suffix(self) -> str: ...
    @property
    def parent(self) -> TreePath: ...
    def joinpath(self, *parts: str) -> TreePath: ...
    def __truediv__(self, part: str) -> TreePath: ...
    def exists(self) -> bool: ...
    def is_dir(self) -> bool: ...
    def is_file(self) -> bool: ...
    def iterdir(self) -> Iterator[TreePath]: ...
    def read_bytes(self) -> bytes: ...
    def read_text(self, encoding: str = "utf-8") -> str: ...
    def open(self, mode: str = "r", encoding: str = "utf-8") -> TextIO | BinaryIO: ...
    def as_posix(self) -> str: ...
    def cache_key(self) -> str: ...


class _BaseTreePath(ABC):
    def __init__(self, relative_path: PurePosixPath | None = None) -> None:
        self._relative_path = PurePosixPath() if relative_path is None else relative_path

    @property
    def name(self) -> str:
        return self._relative_path.name

    @property
    def stem(self) -> str:
        return self._relative_path.stem

    @property
    def suffix(self) -> str:
        return self._relative_path.suffix

    @property
    def parent(self) -> Self:
        if not self._relative_path.parts:
            return self
        return self._with_relative_path(self._relative_path.parent)

    def joinpath(self, *parts: str) -> Self:
        path = self._relative_path
        for part in parts:
            path /= PurePosixPath(str(part).replace("\\", "/"))
        return self._with_relative_path(path)

    def __truediv__(self, part: str) -> Self:
        return self.joinpath(part)

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding)

    def open(self, mode: str = "r", encoding: str = "utf-8") -> TextIO | BinaryIO:
        if mode == "rb":
            return BytesIO(self.read_bytes())
        if mode == "r":
            return StringIO(self.read_text(encoding=encoding))
        raise ValueError(f"TreePath.open only supports 'r' and 'rb', got {mode!r}")

    def as_posix(self) -> str:
        if not self._relative_path.parts:
            return ""
        return self._relative_path.as_posix()

    @abstractmethod
    def cache_key(self) -> str: ...
    @abstractmethod
    def _with_relative_path(self, path: PurePosixPath) -> Self: ...
    @abstractmethod
    def exists(self) -> bool: ...
    @abstractmethod
    def is_dir(self) -> bool: ...
    @abstractmethod
    def is_file(self) -> bool: ...
    @abstractmethod
    def iterdir(self) -> Iterator[Self]: ...
    @abstractmethod
    def read_bytes(self) -> bytes: ...


class FilesystemTreePath(_BaseTreePath):
    @classmethod
    def from_filesystem_path(cls, path: Path) -> FilesystemTreePath:
        absolute = path.resolve()
        anchor = Path(absolute.anchor)
        relative_path = PurePosixPath(*absolute.relative_to(anchor).parts)
        return cls(anchor, relative_path)

    def __init__(self, root: Path, relative_path: PurePosixPath | None = None) -> None:
        super().__init__(relative_path)
        self._root = root

    def _with_relative_path(self, path: PurePosixPath) -> FilesystemTreePath:
        return FilesystemTreePath(self._root, path)

    def concrete_path(self) -> Path:
        path = self._root
        if self._relative_path.parts:
            path /= Path(*self._relative_path.parts)
        return path

    def cache_key(self) -> str:
        return f"fs:{self.concrete_path().resolve().as_posix()}"

    def exists(self) -> bool:
        return self.concrete_path().exists()

    def is_dir(self) -> bool:
        return self.concrete_path().is_dir()

    def is_file(self) -> bool:
        return self.concrete_path().is_file()

    def iterdir(self) -> Iterator[FilesystemTreePath]:
        path = self.concrete_path()
        if not path.is_dir():
            raise NotADirectoryError(self.as_posix())
        for child in sorted(path.iterdir(), key=lambda entry: entry.name):
            yield self / child.name

    def read_bytes(self) -> bytes:
        path = self.concrete_path()
        if not path.is_file():
            raise FileNotFoundError(self.as_posix())
        return path.read_bytes()


class GitTreePath(_BaseTreePath):
    def __init__(
        self,
        store: object,
        commit: str | None = None,
        relative_path: PurePosixPath | None = None,
    ) -> None:
        super().__init__(relative_path)
        self._store = store
        self._commit = commit

    def _with_relative_path(self, path: PurePosixPath) -> GitTreePath:
        return GitTreePath(self._store, self._commit, path)

    def exists(self) -> bool:
        return self._store.exists(self.as_posix(), commit=self._commit) is not None

    def is_dir(self) -> bool:
        res = self._store.exists(self.as_posix(), commit=self._commit)
        return res is not None and bool(res[0] & 0o040000)

    def is_file(self) -> bool:
        res = self._store.exists(self.as_posix(), commit=self._commit)
        return res is not None and bool(res[0] & 0o100000)

    def iterdir(self) -> Iterator[GitTreePath]:
        if not self.is_dir():
            raise NotADirectoryError(self.as_posix())
        for name, _is_dir in self._store.iter_dir_entries(self.as_posix(), commit=self._commit):
            yield self / name

    def read_bytes(self) -> bytes:
        return self._store.read_file(self.as_posix(), commit=self._commit)

    def cache_key(self) -> str:
        commit = self._commit or "HEAD"
        root = getattr(self._store, "root", None)
        if root is None:
            return f"git-memory:{commit}:{self.as_posix()}"
        return f"git:{root.resolve().as_posix()}:{commit}:{self.as_posix()}"


def coerce_tree_path(path: TreePath | Path) -> TreePath:
    if isinstance(path, Path):
        return FilesystemTreePath.from_filesystem_path(path)
    return path

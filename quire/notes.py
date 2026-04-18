from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dulwich.notes import Notes

from quire.refs import RefName


@dataclass(frozen=True)
class NotesRef:
    value: str
    ref_name: RefName = field(init=False)

    def __post_init__(self) -> None:
        ref = RefName(self.value)
        if not ref.value.startswith("refs/notes/"):
            raise ValueError(f"Notes ref must start with 'refs/notes/': {self.value!r}")
        object.__setattr__(self, "ref_name", ref)
        object.__setattr__(self, "value", ref.value)

    def as_ref(self) -> RefName:
        return self.ref_name

    def as_bytes(self) -> bytes:
        return self.value.encode("utf-8")

    def __str__(self) -> str:
        return self.value


def write_git_note(
    repo: Any,
    ref: NotesRef,
    object_sha: bytes | str,
    payload: bytes,
    *,
    author: bytes | None = None,
    committer: bytes | None = None,
    message: bytes | None = None,
) -> bytes:
    sha = object_sha if isinstance(object_sha, bytes) else object_sha.encode("ascii")
    notes = Notes(repo.object_store, repo.refs)
    return notes.set_note(
        sha,
        payload,
        notes_ref=ref.as_bytes(),
        author=author,
        committer=committer,
        message=message,
    )


def read_git_note(repo: Any, ref: NotesRef, object_sha: bytes | str) -> bytes | None:
    sha = object_sha if isinstance(object_sha, bytes) else object_sha.encode("ascii")
    notes = Notes(repo.object_store, repo.refs)
    return notes.get_note(sha, notes_ref=ref.as_bytes())


def remove_git_note(
    repo: Any,
    ref: NotesRef,
    object_sha: bytes | str,
    *,
    author: bytes | None = None,
    committer: bytes | None = None,
    message: bytes | None = None,
) -> bytes | None:
    sha = object_sha if isinstance(object_sha, bytes) else object_sha.encode("ascii")
    notes = Notes(repo.object_store, repo.refs)
    return notes.remove_note(
        sha,
        notes_ref=ref.as_bytes(),
        author=author,
        committer=committer,
        message=message,
    )

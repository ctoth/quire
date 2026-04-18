from __future__ import annotations

from dataclasses import dataclass

from quire.refs import RefName


@dataclass(frozen=True)
class NotesRef:
    value: str

    def __post_init__(self) -> None:
        ref = RefName(self.value)
        if not ref.value.startswith("refs/notes/"):
            raise ValueError(f"Notes ref must start with 'refs/notes/': {self.value!r}")
        object.__setattr__(self, "value", ref.value)

    def as_ref(self) -> RefName:
        return RefName(self.value)

    def as_bytes(self) -> bytes:
        return self.value.encode("utf-8")

    def __str__(self) -> str:
        return self.value

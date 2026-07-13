"""Runnable version of the README quick start."""

from dataclasses import dataclass

from quire import DocumentFamilyStore, GitStore, VersionId, registry_from_charters
from quire.charter_class import CharterDoc, charter


@charter(
    key="notes",
    name="notes",
    contract_version="2026.07.01",
    placement="notes",
    identity_field="note_id",
)
class Note(CharterDoc):
    note_id: str
    title: str
    body: str = ""


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


version = VersionId("2026.07.01")
registry = registry_from_charters(
    Note.__charter__,
    name="notebook",
    contract_version=version,
)
owner = Owner()
store = DocumentFamilyStore(owner=owner, backend=GitStore.init_memory())
bound = registry.bind(owner, store)

with bound.transact(message="seed notebook") as transaction:
    transaction.notes.save("welcome", Note("welcome", "Welcome"))
    transaction.notes.save("todo", Note("todo", "Next steps", "Write more."))

assert list(bound.notes.iter_refs()) == ["todo", "welcome"]
assert bound.notes.require("welcome").title == "Welcome"

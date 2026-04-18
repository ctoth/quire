from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar, overload

import msgspec

from quire.documents.loaded import LoadedDocument
from quire.tree_path import TreePath, coerce_tree_path

TDocument = TypeVar("TDocument")
TLoaded = TypeVar("TLoaded")


class DocumentStruct(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """Base type for strict authored YAML/JSON document schemas."""


class DocumentSchemaError(ValueError):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message


def to_document_builtins(value: object) -> object:
    if isinstance(value, msgspec.Struct):
        return to_document_builtins(msgspec.to_builtins(value))
    if isinstance(value, Mapping):
        return {key: to_document_builtins(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_document_builtins(item) for item in value]
    if isinstance(value, list):
        return [to_document_builtins(item) for item in value]
    return value


def _source_label(path: TreePath | Path) -> str:
    if isinstance(path, Path):
        return str(path)
    rendered = path.as_posix()
    return rendered if rendered else path.cache_key()


def decode_document_bytes(
    payload: bytes,
    document_type: type[TDocument],
    *,
    source: str,
) -> TDocument:
    try:
        return msgspec.yaml.decode(payload, type=document_type, strict=True)
    except msgspec.DecodeError as exc:
        raise DocumentSchemaError(source, str(exc)) from exc


def convert_document_value(
    payload: object,
    document_type: type[TDocument],
    *,
    source: str,
) -> TDocument:
    try:
        return msgspec.convert(payload, type=document_type, strict=True)
    except (msgspec.ValidationError, TypeError) as exc:
        raise DocumentSchemaError(source, str(exc)) from exc


def decode_document_path(path: TreePath | Path, document_type: type[TDocument]) -> TDocument:
    tree_path = coerce_tree_path(path)
    return decode_document_bytes(
        tree_path.read_bytes(),
        document_type,
        source=_source_label(tree_path),
    )


def load_document(
    path: TreePath | Path,
    document_type: type[TDocument],
    *,
    knowledge_root: TreePath | Path | None = None,
) -> LoadedDocument[TDocument]:
    source_path = coerce_tree_path(path)
    root_path = None if knowledge_root is None else coerce_tree_path(knowledge_root)
    return LoadedDocument(
        filename=source_path.stem,
        source_path=source_path,
        knowledge_root=root_path,
        document=decode_document_path(source_path, document_type),
    )


@overload
def load_document_dir(
    directory: TreePath | Path | None,
    document_type: type[TDocument],
) -> list[LoadedDocument[TDocument]]: ...


@overload
def load_document_dir(
    directory: TreePath | Path | None,
    document_type: type[TDocument],
    *,
    wrapper: Callable[[LoadedDocument[TDocument]], TLoaded],
) -> list[TLoaded]: ...


def load_document_dir(
    directory: TreePath | Path | None,
    document_type: type[TDocument],
    *,
    wrapper: Callable[[LoadedDocument[TDocument]], TLoaded] | None = None,
) -> list[LoadedDocument[TDocument]] | list[TLoaded]:
    if directory is None:
        return []

    documents_dir = coerce_tree_path(directory)
    if not documents_dir.is_dir():
        return []

    knowledge_root = documents_dir.parent if documents_dir.name else documents_dir
    entries = sorted(
        (
            entry
            for entry in documents_dir.iterdir()
            if entry.is_file() and entry.suffix == ".yaml"
        ),
        key=lambda entry: entry.as_posix(),
    )
    loaded = [
        load_document(entry, document_type, knowledge_root=knowledge_root)
        for entry in entries
    ]
    if wrapper is None:
        return loaded
    return [wrapper(document) for document in loaded]

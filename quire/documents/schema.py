from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import TypeVar, overload

import msgspec

from quire.documents._paths import _source_label
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
    store_root: TreePath | Path | None = None,
) -> LoadedDocument[TDocument]:
    artifact_path = coerce_tree_path(path)
    root_path = None if store_root is None else coerce_tree_path(store_root)
    return LoadedDocument(
        filename=artifact_path.stem,
        artifact_path=artifact_path,
        store_root=root_path,
        document=decode_document_path(artifact_path, document_type),
    )


@overload
def iter_document_dir(
    directory: TreePath | Path | None,
    document_type: type[TDocument],
) -> Iterator[LoadedDocument[TDocument]]: ...


@overload
def iter_document_dir(
    directory: TreePath | Path | None,
    document_type: type[TDocument],
    *,
    wrapper: Callable[[LoadedDocument[TDocument]], TLoaded],
) -> Iterator[TLoaded]: ...


def iter_document_dir(
    directory: TreePath | Path | None,
    document_type: type[TDocument],
    *,
    wrapper: Callable[[LoadedDocument[TDocument]], TLoaded] | None = None,
) -> Iterator[LoadedDocument[TDocument] | TLoaded]:
    if directory is None:
        return

    documents_dir = coerce_tree_path(directory)
    if not documents_dir.is_dir():
        return

    store_root = documents_dir.parent if documents_dir.name else documents_dir
    entries = sorted(
        (
            entry
            for entry in documents_dir.iterdir()
            if entry.is_file() and entry.suffix == ".yaml"
        ),
        key=lambda entry: entry.as_posix(),
    )
    for entry in entries:
        loaded = load_document(entry, document_type, store_root=store_root)
        yield loaded if wrapper is None else wrapper(loaded)

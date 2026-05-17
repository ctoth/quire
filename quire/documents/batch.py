from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

import msgspec

from quire.documents.codecs import document_to_payload
from quire.documents.schema import DocumentSchemaError, convert_document_value
from quire.tree_path import TreePath, coerce_tree_path

TDocument = TypeVar("TDocument")


@dataclass(frozen=True)
class DocumentBatchSpec(Generic[TDocument]):
    batch_name: str
    item_type: type[TDocument]
    items_field: str
    inherited_item_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadedBatchItem(Generic[TDocument]):
    filename: str
    item_index: int
    artifact_path: TreePath | None
    store_root: TreePath | None
    document: TDocument


def _source_label(path: TreePath | Path) -> str:
    if isinstance(path, Path):
        return str(path)
    rendered = path.as_posix()
    return rendered if rendered else path.cache_key()


def _decode_batch_mapping(payload: bytes, *, source: str) -> Mapping[str, object]:
    try:
        decoded = msgspec.yaml.decode(payload)
    except msgspec.DecodeError as exc:
        raise DocumentSchemaError(source, str(exc)) from exc

    if not isinstance(decoded, Mapping):
        raise DocumentSchemaError(source, "expected batch envelope mapping")
    return decoded


def _validate_envelope_fields(
    envelope: Mapping[str, object],
    spec: DocumentBatchSpec[Any],
    *,
    source: str,
) -> None:
    allowed_fields = {spec.items_field, *spec.inherited_item_fields}
    for field in envelope:
        if not isinstance(field, str):
            raise DocumentSchemaError(source, "expected string envelope field")
        if field not in allowed_fields:
            raise DocumentSchemaError(source, f"unknown envelope field: {field}")


def _items_value(
    envelope: Mapping[str, object],
    spec: DocumentBatchSpec[Any],
    *,
    source: str,
) -> Sequence[object]:
    if spec.items_field not in envelope:
        raise DocumentSchemaError(
            source,
            f"missing required items field: {spec.items_field}",
        )

    items = envelope[spec.items_field]
    if isinstance(items, (str, bytes, bytearray)) or not isinstance(items, Sequence):
        raise DocumentSchemaError(
            source,
            f"expected items field {spec.items_field!r} to be a sequence",
        )
    return items


def _item_payload(
    item: object,
    envelope: Mapping[str, object],
    spec: DocumentBatchSpec[Any],
    *,
    source: str,
) -> Mapping[str, object]:
    if not isinstance(item, Mapping):
        raise DocumentSchemaError(source, "expected batch item mapping")

    payload = dict(item)
    for inherited_field in spec.inherited_item_fields:
        if inherited_field not in payload and inherited_field in envelope:
            payload[inherited_field] = envelope[inherited_field]
    return payload


def decode_document_batch_bytes(
    payload: bytes,
    spec: DocumentBatchSpec[TDocument],
    *,
    source: str,
) -> tuple[TDocument, ...]:
    envelope = _decode_batch_mapping(payload, source=source)
    _validate_envelope_fields(envelope, spec, source=source)
    items = _items_value(envelope, spec, source=source)

    documents: list[TDocument] = []
    for index, item in enumerate(items, start=1):
        item_source = f"{source}#{index}"
        documents.append(
            convert_document_value(
                _item_payload(item, envelope, spec, source=item_source),
                spec.item_type,
                source=item_source,
            )
        )
    return tuple(documents)


def load_document_batch(
    path: TreePath | Path,
    spec: DocumentBatchSpec[TDocument],
    *,
    store_root: TreePath | Path | None = None,
) -> tuple[LoadedBatchItem[TDocument], ...]:
    artifact_path = coerce_tree_path(path)
    root_path = None if store_root is None else coerce_tree_path(store_root)
    documents = decode_document_batch_bytes(
        artifact_path.read_bytes(),
        spec,
        source=_source_label(artifact_path),
    )
    return tuple(
        LoadedBatchItem(
            filename=f"{artifact_path.name}#{index}",
            item_index=index,
            artifact_path=artifact_path,
            store_root=root_path,
            document=document,
        )
        for index, document in enumerate(documents, start=1)
    )


def load_document_batch_dir(
    directory: TreePath | Path | None,
    spec: DocumentBatchSpec[TDocument],
) -> list[LoadedBatchItem[TDocument]]:
    if directory is None:
        return []

    documents_dir = coerce_tree_path(directory)
    if not documents_dir.is_dir():
        return []

    store_root = documents_dir.parent if documents_dir.name else documents_dir
    entries = sorted(
        (
            entry
            for entry in documents_dir.iterdir()
            if entry.is_file() and entry.suffix == ".yaml"
        ),
        key=lambda entry: entry.as_posix(),
    )
    loaded: list[LoadedBatchItem[TDocument]] = []
    for entry in entries:
        loaded.extend(load_document_batch(entry, spec, store_root=store_root))
    return loaded


def _batch_item_payload(
    document: object,
    inherited_item_values: Mapping[str, object],
) -> Mapping[str, object]:
    payload = document_to_payload(document)
    if not isinstance(payload, Mapping):
        raise TypeError(f"Document {type(document).__name__} did not render to a mapping")

    item = dict(payload)
    for field, value in inherited_item_values.items():
        if item.get(field) == value:
            del item[field]
    return item


def render_document_batch(
    items: Sequence[TDocument],
    spec: DocumentBatchSpec[TDocument],
    *,
    inherited_item_values: Mapping[str, object] | None = None,
) -> str:
    inherited_values = {} if inherited_item_values is None else dict(inherited_item_values)
    unknown_inherited = set(inherited_values) - set(spec.inherited_item_fields)
    if unknown_inherited:
        fields = ", ".join(sorted(unknown_inherited))
        raise ValueError(f"unknown inherited item field for {spec.batch_name}: {fields}")

    envelope: dict[str, object] = dict(inherited_values)
    envelope[spec.items_field] = [
        _batch_item_payload(item, inherited_values)
        for item in items
    ]
    return msgspec.yaml.encode(envelope).decode("utf-8").rstrip()

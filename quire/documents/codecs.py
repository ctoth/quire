from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any, TypeVar

import msgspec

from quire.documents.schema import convert_document_value, decode_document_bytes

TDocument = TypeVar("TDocument")


def _prune_none(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _prune_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_prune_none(item) for item in value]
    if isinstance(value, tuple):
        return [_prune_none(item) for item in value]
    return value


def document_to_payload(document: object) -> object:
    to_payload = getattr(document, "to_payload", None)
    if callable(to_payload):
        return _prune_none(to_payload())
    if isinstance(document, msgspec.Struct):
        return _prune_none(msgspec.to_builtins(document))
    raise TypeError(f"Document {type(document).__name__} is not serializable")


def encode_document(document: object) -> bytes:
    return msgspec.yaml.encode(document_to_payload(document))


def render_document(document: object) -> str:
    return encode_document(document).decode("utf-8").rstrip()


def encode_yaml_value(value: object) -> bytes:
    return msgspec.yaml.encode(value)


def render_yaml_value(value: object) -> str:
    return encode_yaml_value(value).decode("utf-8").rstrip()


def decode_yaml_value(payload: bytes, *, source: str) -> object:
    try:
        return msgspec.yaml.decode(payload)
    except msgspec.DecodeError as exc:
        raise ValueError(f"{source}: invalid YAML payload") from exc


def decode_yaml_mapping(payload: bytes, *, source: str) -> dict[str, Any]:
    decoded = decode_yaml_value(payload, source=source)
    if not isinstance(decoded, dict):
        raise ValueError(f"{source}: expected a YAML mapping")
    return decoded


def coerce_text_document(payload: object, source: str) -> str:
    if isinstance(payload, str):
        return payload
    raise TypeError(f"{source}: expected UTF-8 text payload")


def decode_text_document(payload: bytes, source: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{source}: expected UTF-8 text payload") from exc


def encode_text_document(document: str) -> bytes:
    return document.encode("utf-8")


def identity_text_document(document: str) -> str:
    return document


def coerce_json_mapping(payload: object, source: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"{source}: expected JSON object payload")


def decode_json_mapping(payload: bytes, source: str) -> dict[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source}: expected JSON object payload") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{source}: expected JSON object payload")
    return decoded


def encode_json_mapping(document: dict[str, Any]) -> bytes:
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")


def render_json_mapping(document: dict[str, Any]) -> str:
    return encode_json_mapping(document).decode("utf-8")


def identity_json_mapping(document: dict[str, Any]) -> dict[str, Any]:
    return document


def convert_document(
    payload: object,
    document_type: type[TDocument],
    *,
    source: str,
) -> TDocument:
    return convert_document_value(payload, document_type, source=source)


def decode_document(
    payload: bytes,
    document_type: type[TDocument],
    *,
    source: str,
) -> TDocument:
    return decode_document_bytes(payload, document_type, source=source)


@dataclass(frozen=True)
class DocumentCodec:
    convert_document: Callable[..., object]
    decode_document: Callable[..., object]
    encode_document: Callable[[object], bytes]
    render_document: Callable[[object], str]
    document_to_payload: Callable[[object], object]

    def convert(
        self,
        payload: object,
        document_type: type[TDocument],
        *,
        source: str,
    ) -> TDocument:
        return self.convert_document(payload, document_type, source=source)  # type: ignore[return-value]

    def decode(
        self,
        payload: bytes,
        document_type: type[TDocument],
        *,
        source: str,
    ) -> TDocument:
        return self.decode_document(payload, document_type, source=source)  # type: ignore[return-value]

    def encode(self, document: object) -> bytes:
        return self.encode_document(document)

    def render(self, document: object) -> str:
        return self.render_document(document)

    def payload(self, document: object) -> object:
        return self.document_to_payload(document)


DEFAULT_DOCUMENT_CODEC = DocumentCodec(
    convert_document=convert_document,
    decode_document=decode_document,
    encode_document=encode_document,
    render_document=render_document,
    document_to_payload=document_to_payload,
)

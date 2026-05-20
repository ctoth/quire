from __future__ import annotations

from typing import cast

import pytest

from quire.documents import (
    DEFAULT_DOCUMENT_CODEC,
    DocumentCodec,
    DocumentSchemaError,
    DocumentStruct,
    decode_document_bytes,
    decode_json_mapping,
    decode_text_document,
    encode_json_mapping,
    encode_text_document,
    load_document,
    load_document_dir,
)
from quire.documents._paths import _source_label
from quire.tree_path import FilesystemTreePath


def test_quire_documents_is_public() -> None:
    import quire

    from quire import documents

    assert "documents" in quire.__all__
    assert documents is quire.documents


class ExampleDocument(DocumentStruct):
    name: str
    value: int


def test_decode_document_bytes_is_strict():
    with pytest.raises(DocumentSchemaError, match="extra"):
        decode_document_bytes(
            b"name: demo\nvalue: 3\nextra: nope\n",
            ExampleDocument,
            source="example.yaml",
        )


def test_load_document_captures_artifact_metadata(tmp_path):
    path = tmp_path / "demo.yaml"
    path.write_text("name: demo\nvalue: 3\n", encoding="utf-8")

    loaded = load_document(path, ExampleDocument, store_root=tmp_path)

    assert loaded.filename == "demo"
    assert loaded.document == ExampleDocument(name="demo", value=3)
    assert loaded.artifact_path is not None
    assert loaded.artifact_path.as_posix().endswith("demo.yaml")
    assert loaded.store_root is not None


def test_load_document_dir_loads_direct_yaml_children_deterministically(tmp_path):
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    (documents_dir / "b.yaml").write_text("name: beta\nvalue: 2\n", encoding="utf-8")
    (documents_dir / "a.yaml").write_text("name: alpha\nvalue: 1\n", encoding="utf-8")
    (documents_dir / "ignored.txt").write_text("name: ignored\nvalue: 0\n", encoding="utf-8")

    loaded = load_document_dir(documents_dir, ExampleDocument)

    assert [document.filename for document in loaded] == ["a", "b"]
    assert [document.document.name for document in loaded] == ["alpha", "beta"]


def test_text_document_codec_round_trips_utf8_text():
    document = "notes\nwith unicode: cafe\n"

    encoded = encode_text_document(document)

    assert decode_text_document(encoded, source="notes.md") == document


def test_text_document_codec_rejects_non_utf8_bytes():
    with pytest.raises(ValueError, match="UTF-8 text payload"):
        decode_text_document(b"\xff", source="notes.md")


def test_json_mapping_codec_round_trips_json_object():
    document = {"name": "demo", "value": 3}

    encoded = encode_json_mapping(document)

    assert decode_json_mapping(encoded, source="metadata.json") == document


def test_json_mapping_codec_rejects_non_object_payload():
    with pytest.raises(ValueError, match="JSON object payload"):
        decode_json_mapping(b"[1, 2, 3]", source="metadata.json")


def test_default_document_codec_round_trips_struct_documents():
    document = ExampleDocument(name="demo", value=3)

    encoded = DEFAULT_DOCUMENT_CODEC.encode(document)

    assert DEFAULT_DOCUMENT_CODEC.decode(encoded, ExampleDocument, source="demo.yaml") == document
    assert DEFAULT_DOCUMENT_CODEC.convert({"name": "demo", "value": 3}, ExampleDocument, source="input") == document
    assert DEFAULT_DOCUMENT_CODEC.payload(document) == {"name": "demo", "value": 3}
    assert "name: demo" in DEFAULT_DOCUMENT_CODEC.render(document)


def test_source_label_returns_str_for_pathlib_path(tmp_path):
    path = tmp_path / "demo.yaml"
    assert _source_label(path) == str(path)


def test_source_label_uses_as_posix_for_tree_path(tmp_path):
    tree_path = FilesystemTreePath.from_filesystem_path(tmp_path) / "demo.yaml"
    rendered = tree_path.as_posix()
    assert rendered
    assert _source_label(tree_path) == rendered


def test_document_codec_can_group_custom_document_operations():
    def convert_custom(payload: object, document_type: type[ExampleDocument], *, source: str) -> ExampleDocument:
        return document_type(**cast(dict[str, object], payload))

    def decode_custom(payload: bytes, document_type: type[ExampleDocument], *, source: str) -> ExampleDocument:
        raw_items = (item.split("=", 1) for item in payload.decode("utf-8").splitlines())
        return document_type(**dict(raw_items))

    def encode_custom(document: object) -> bytes:
        typed = cast(ExampleDocument, document)
        return f"name={typed.name}\nvalue={typed.value}".encode("utf-8")

    def render_custom(document: object) -> str:
        return f"name={cast(ExampleDocument, document).name}"

    def payload_custom(document: object) -> dict[str, object]:
        return {"custom": cast(ExampleDocument, document).name}

    codec = DocumentCodec(
        convert_document=convert_custom,
        decode_document=decode_custom,
        encode_document=encode_custom,
        render_document=render_custom,
        document_to_payload=payload_custom,
    )
    document = ExampleDocument(name="demo", value="3")  # type: ignore[arg-type]

    assert codec.decode(codec.encode(document), ExampleDocument, source="custom.txt") == document
    assert codec.render(document) == "name=demo"
    assert codec.payload(document) == {"custom": "demo"}

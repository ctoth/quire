from __future__ import annotations

import pytest

from quire.documents import (
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


def test_load_document_captures_source_metadata(tmp_path):
    path = tmp_path / "demo.yaml"
    path.write_text("name: demo\nvalue: 3\n", encoding="utf-8")

    loaded = load_document(path, ExampleDocument, knowledge_root=tmp_path)

    assert loaded.filename == "demo"
    assert loaded.document == ExampleDocument(name="demo", value=3)
    assert loaded.source_path is not None
    assert loaded.source_path.as_posix().endswith("demo.yaml")
    assert loaded.knowledge_root is not None


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

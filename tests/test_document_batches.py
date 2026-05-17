from __future__ import annotations

import pytest

from quire.documents import (
    DocumentBatchSpec,
    DocumentSchemaError,
    DocumentStruct,
    decode_document_batch_bytes,
    load_document_batch,
    load_document_batch_dir,
    render_document_batch,
)


class BatchExampleDocument(DocumentStruct):
    name: str
    value: int
    source: str | None = None


EXAMPLE_BATCH_SPEC = DocumentBatchSpec(
    batch_name="examples",
    item_type=BatchExampleDocument,
    items_field="examples",
)


INHERITING_BATCH_SPEC = DocumentBatchSpec(
    batch_name="examples",
    item_type=BatchExampleDocument,
    items_field="examples",
    inherited_item_fields=("source",),
)


def test_batch_spec_decodes_items_field_to_typed_items() -> None:
    documents = decode_document_batch_bytes(
        b"examples:\n  - name: alpha\n    value: 1\n",
        EXAMPLE_BATCH_SPEC,
        source="examples.yaml",
    )

    assert documents == (BatchExampleDocument(name="alpha", value=1),)


def test_batch_spec_inherits_declared_field_into_each_item() -> None:
    documents = decode_document_batch_bytes(
        b"source: paper-a\nexamples:\n  - name: alpha\n    value: 1\n",
        INHERITING_BATCH_SPEC,
        source="examples.yaml",
    )

    assert documents == (
        BatchExampleDocument(name="alpha", value=1, source="paper-a"),
    )


def test_batch_spec_item_field_wins_over_inherited_field() -> None:
    documents = decode_document_batch_bytes(
        b"source: paper-a\nexamples:\n  - name: alpha\n    value: 1\n    source: paper-b\n",
        INHERITING_BATCH_SPEC,
        source="examples.yaml",
    )

    assert documents == (
        BatchExampleDocument(name="alpha", value=1, source="paper-b"),
    )


def test_batch_spec_rejects_unknown_envelope_field() -> None:
    with pytest.raises(DocumentSchemaError, match="unknown envelope field"):
        decode_document_batch_bytes(
            b"extra: no\nexamples:\n  - name: alpha\n    value: 1\n",
            EXAMPLE_BATCH_SPEC,
            source="examples.yaml",
        )


def test_batch_spec_rejects_missing_items_field() -> None:
    with pytest.raises(DocumentSchemaError, match="missing required items field"):
        decode_document_batch_bytes(
            b"source: paper-a\n",
            INHERITING_BATCH_SPEC,
            source="examples.yaml",
        )


def test_batch_spec_rejects_non_sequence_items_field() -> None:
    with pytest.raises(DocumentSchemaError, match="expected items field"):
        decode_document_batch_bytes(
            b"examples:\n  name: alpha\n  value: 1\n",
            EXAMPLE_BATCH_SPEC,
            source="examples.yaml",
        )


def test_load_document_batch_labels_items_with_batch_index(tmp_path) -> None:
    path = tmp_path / "batch.yaml"
    path.write_text(
        "examples:\n"
        "  - name: alpha\n"
        "    value: 1\n"
        "  - name: beta\n"
        "    value: 2\n",
        encoding="utf-8",
    )

    loaded = load_document_batch(path, EXAMPLE_BATCH_SPEC, store_root=tmp_path)

    assert [item.filename for item in loaded] == ["batch#1", "batch#2"]
    assert [item.item_index for item in loaded] == [1, 2]
    assert [item.document.name for item in loaded] == ["alpha", "beta"]
    assert all(item.artifact_path is not None for item in loaded)
    assert all(item.store_root is not None for item in loaded)


def test_load_document_batch_dir_orders_yaml_children_then_items(tmp_path) -> None:
    batches_dir = tmp_path / "batches"
    batches_dir.mkdir()
    (batches_dir / "b.yaml").write_text(
        "examples:\n  - name: beta\n    value: 2\n",
        encoding="utf-8",
    )
    (batches_dir / "a.yaml").write_text(
        "examples:\n"
        "  - name: alpha\n"
        "    value: 1\n"
        "  - name: atom\n"
        "    value: 3\n",
        encoding="utf-8",
    )
    (batches_dir / "ignored.txt").write_text(
        "examples:\n  - name: ignored\n    value: 0\n",
        encoding="utf-8",
    )

    loaded = load_document_batch_dir(batches_dir, EXAMPLE_BATCH_SPEC)

    assert [item.filename for item in loaded] == ["a#1", "a#2", "b#1"]
    assert [item.document.name for item in loaded] == ["alpha", "atom", "beta"]


def test_render_document_batch_round_trips_items() -> None:
    documents = (
        BatchExampleDocument(name="alpha", value=1, source="paper-a"),
        BatchExampleDocument(name="beta", value=2, source="paper-a"),
    )

    rendered = render_document_batch(
        documents,
        INHERITING_BATCH_SPEC,
        inherited_item_values={"source": "paper-a"},
    )

    assert "source: paper-a" in rendered
    assert "examples:" in rendered
    assert rendered.count("source: paper-a") == 1
    assert (
        decode_document_batch_bytes(
            rendered.encode("utf-8"),
            INHERITING_BATCH_SPEC,
            source="rendered.yaml",
        )
        == documents
    )


def test_quire_documents_exports_batch_api() -> None:
    from quire import documents

    assert documents.DocumentBatchSpec is DocumentBatchSpec
    assert documents.decode_document_batch_bytes is decode_document_batch_bytes
    assert documents.load_document_batch is load_document_batch
    assert documents.load_document_batch_dir is load_document_batch_dir
    assert documents.render_document_batch is render_document_batch

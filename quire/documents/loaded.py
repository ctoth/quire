from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar, cast

from quire.tree_path import TreePath, coerce_tree_path

TDocument = TypeVar("TDocument")


@dataclass(init=False)
class LoadedDocument(Generic[TDocument]):
    filename: str
    artifact_path: TreePath | None
    store_root: TreePath | None
    document: TDocument

    def __init__(
        self,
        filename: str,
        artifact_path: TreePath | Path | None = None,
        document: TDocument | None = None,
        store_root: TreePath | Path | None = None,
    ) -> None:
        self.filename = filename
        self.artifact_path = None if artifact_path is None else coerce_tree_path(artifact_path)
        self.store_root = None if store_root is None else coerce_tree_path(store_root)
        self.document = cast(TDocument, document)

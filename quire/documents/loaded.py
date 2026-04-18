from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar, cast

from quire.tree_path import TreePath, coerce_tree_path

TDocument = TypeVar("TDocument")


@dataclass(init=False)
class LoadedDocument(Generic[TDocument]):
    filename: str
    source_path: TreePath | None
    knowledge_root: TreePath | None
    document: TDocument

    def __init__(
        self,
        filename: str,
        source_path: TreePath | Path | None = None,
        document: TDocument | None = None,
        knowledge_root: TreePath | Path | None = None,
    ) -> None:
        self.filename = filename
        self.source_path = None if source_path is None else coerce_tree_path(source_path)
        self.knowledge_root = None if knowledge_root is None else coerce_tree_path(knowledge_root)
        self.document = cast(TDocument, document)

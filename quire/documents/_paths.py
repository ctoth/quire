from __future__ import annotations

from pathlib import Path

from quire.tree_path import TreePath


def _source_label(path: TreePath | Path) -> str:
    if isinstance(path, Path):
        return str(path)
    rendered = path.as_posix()
    return rendered if rendered else path.cache_key()

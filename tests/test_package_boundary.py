from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).parents[1]
SQL_DISTRIBUTIONS = {"sqlalchemy", "sqlalchemy-fts5", "sqlite-vec"}


def _dependency_name(requirement: str) -> str:
    return requirement.split("[", 1)[0].split("@", 1)[0].split(">", 1)[0].strip()


def test_sql_dependencies_are_explicit_extras() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    core = {_dependency_name(item) for item in project["dependencies"]}
    extras = project["optional-dependencies"]
    sql = {_dependency_name(item) for item in extras["sql"]}
    vector = {_dependency_name(item) for item in extras["vector"]}

    assert core.isdisjoint(SQL_DISTRIBUTIONS)
    assert set(extras) == {"sql", "vector"}
    assert sql == {"sqlalchemy", "sqlalchemy-fts5"}
    assert vector == SQL_DISTRIBUTIONS

    dev = {_dependency_name(item) for item in metadata["dependency-groups"]["dev"]}
    assert {"pyright", "ruff"}.issubset(dev)


def test_core_package_imports_without_sql_or_vector_dependencies(tmp_path: Path) -> None:
    probe = tmp_path / "core_import_probe.py"
    probe.write_text(
        """
import importlib.abc
import sys

BLOCKED = ("sqlalchemy", "sqlalchemy_fts5", "sqlite_vec")

class BlockSqlImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith(BLOCKED):
            raise ModuleNotFoundError(f"blocked optional dependency: {fullname}")
        return None

sys.meta_path.insert(0, BlockSqlImports())
import quire

assert not hasattr(quire, "SqlAlchemySchema")
assert not hasattr(quire, "SqlAlchemyVecRegistry")
""".lstrip(),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

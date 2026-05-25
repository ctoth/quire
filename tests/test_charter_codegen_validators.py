from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

import msgspec
import pytest

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter
from quire.families import FamilyDefinition
from quire.versions import VersionId


class DemoFamily(str, Enum):
    DEMOS = "demos"


class DemoDoc(msgspec.Struct):
    id: str


@dataclass
class Demo:
    id: str
    claims: tuple[str, ...]


def _raise_empty_claims(document: msgspec.Struct) -> None:
    if not cast(Any, document).claims:
        raise ValueError("empty claims")


def _minimal_family() -> FamilyDefinition[object, DemoFamily, str, DemoDoc]:
    version = VersionId("2026.05.25", allow_placeholder=False)
    return FamilyDefinition(
        key=DemoFamily.DEMOS,
        name="demos",
        contract_version=version,
        artifact_family=ArtifactFamily(
            name="demo_artifact",
            contract_version=version,
            doc_type=DemoDoc,
            placement=FlatYamlPlacement("demos", str),
        ),
        identity_field="id",
    )


def _claims_charter() -> FamilyCharter:
    return FamilyCharter(
        family=_minimal_family(),
        model=Demo,
        fields=(
            CharterField("id", str, primary_key=True),
            CharterField("claims", tuple[str, ...]),
        ),
        validators=(_raise_empty_claims,),
    )


def test_generated_document_validator_rejects_empty_claims() -> None:
    document_type = _claims_charter().generated_document()

    with pytest.raises(ValueError, match="empty claims"):
        document_type(id="demo-1", claims=())


def test_generated_document_validator_accepts_non_empty_claims() -> None:
    document_type = _claims_charter().generated_document()

    document = document_type(id="demo-1", claims=("claim-1",))

    assert cast(Any, document).claims == ("claim-1",)


def test_generated_document_validator_runs_after_unknown_field_check() -> None:
    document_type = _claims_charter().generated_document()

    with pytest.raises(TypeError, match="Unexpected keyword argument"):
        document_type(id="demo-1", claims=(), unexpected=True)

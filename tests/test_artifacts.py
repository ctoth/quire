from __future__ import annotations

import msgspec

from quire.artifacts import ArtifactFamily, ResolvedArtifact
from quire.versions import VersionId


class DemoDocument(msgspec.Struct):
    name: str


def test_artifact_family_declares_contract_version_and_resolution():
    family = ArtifactFamily[str, DemoDocument](
        name="demo",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        resolve_ref=lambda _repo, ref: ResolvedArtifact(branch="master", relpath=f"demo/{ref}.yaml"),
    )

    assert family.contract_version == VersionId("2026.04.18")
    assert family.resolve_ref(None, "example") == ResolvedArtifact(
        branch="master",
        relpath="demo/example.yaml",
    )

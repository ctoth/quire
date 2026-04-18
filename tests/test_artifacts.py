from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import msgspec
import pytest

from quire.artifacts import (
    ArtifactAddress,
    ArtifactFamily,
    BranchPlacement,
    FixedFilePlacement,
    FlatYamlPlacement,
    HashScatteredYamlPlacement,
    PathArtifactLocator,
    SingletonFilePlacement,
    TemplateFilePlacement,
)
from quire.documents.loaded import LoadedDocument
from quire.versions import VersionId


class DemoDocument(msgspec.Struct):
    name: str


@dataclass(frozen=True)
class Owner:
    branch: str = "master"


class MethodOwner:
    def primary_branch_name(self) -> str:
        return "main"

    def current_branch_name(self) -> str:
        return "topic"


@dataclass(frozen=True)
class DemoRef:
    name: str


def test_artifact_family_declares_contract_version_and_placement():
    family = ArtifactFamily[Owner, DemoRef, DemoDocument](
        name="demo",
        contract_version=VersionId("2026.04.18", allow_placeholder=False),
        doc_type=DemoDocument,
        placement=FlatYamlPlacement("demo", DemoRef, ref_field="name"),
    )

    assert family.contract_version == VersionId("2026.04.18")
    assert family.address_for(Owner(), DemoRef("example")) == ArtifactAddress(
        branch="master",
        locator=PathArtifactLocator("demo/example.yaml"),
    )
    assert family.contract_body()["placement"] == {
        "kind": "flat-yaml",
        "namespace": "demo",
        "extension": ".yaml",
        "ref_field": "name",
        "codec": "stem",
        "branch": {"policy": "owner"},
    }


def test_hash_scattered_yaml_uses_opaque_deterministic_address():
    placement = HashScatteredYamlPlacement[Owner, DemoRef](
        namespace="claims",
        ref_factory=DemoRef,
        ref_field="name",
        fanout=(2, 2),
        filename_mode="digest",
    )
    address = placement.address_for(Owner(), DemoRef("paper:claim"))
    path = address.require_path()
    digest = Path(path).stem

    assert address.branch == "master"
    assert path == f"claims/{digest[:2]}/{digest[2:4]}/{digest}.yaml"
    assert "paper" not in path
    with pytest.raises(TypeError, match="cannot recover refs"):
        placement.ref_from_locator(address.locator)


def test_hash_scattered_yaml_can_use_reversible_encoded_filename():
    placement = HashScatteredYamlPlacement[Owner, DemoRef](
        namespace="stances",
        ref_factory=DemoRef,
        ref_field="name",
        codec="colon_to_double_underscore",
        filename_mode="encoded_ref",
    )
    address = placement.address_for(Owner(), DemoRef("claim:a"))

    assert address.require_path().endswith("/claim__a.yaml")
    assert placement.ref_from_locator(address.locator) == DemoRef("claim:a")


def test_fixed_template_and_singleton_placements_have_contract_bodies():
    source_branch = BranchPlacement(
        policy="template",
        template="source/{stem}",
        ref_field="name",
        codec="slug",
    )
    fixed = FixedFilePlacement[Owner, DemoRef]("source.yaml", branch=source_branch)
    templated = TemplateFilePlacement[Owner, DemoRef](
        "merge/finalize/{stem}.yaml",
        ref_field="name",
        codec="slug",
        branch=source_branch,
    )
    singleton = SingletonFilePlacement[Owner, str](
        "merge/manifest.yaml",
        ref_factory=lambda: "manifest",
        branch=BranchPlacement(policy="fixed", fixed_branch="master"),
    )

    assert fixed.address_for(Owner(), DemoRef("Paper A")).require_path() == "source.yaml"
    assert fixed.address_for(Owner(), DemoRef("Paper A")).branch == "source/paper_a"
    assert templated.address_for(Owner(), DemoRef("Paper A")).require_path() == "merge/finalize/paper_a.yaml"
    assert singleton.address_for(Owner(), "manifest").require_path() == "merge/manifest.yaml"
    assert fixed.contract_body()["kind"] == "fixed-file"
    assert templated.contract_body()["kind"] == "template-file"
    assert singleton.contract_body()["kind"] == "singleton-file"


def test_branch_template_can_preserve_case_for_safe_source_slugs():
    placement = BranchPlacement(
        policy="template",
        template="source/{stem}",
        ref_field="name",
        codec="safe_slug",
    )

    assert placement.branch_name(Owner(), DemoRef("Smith 2024.TestPaper")) == "source/Smith_2024.TestPaper"


def test_branch_placement_accepts_owner_protocol_methods():
    assert BranchPlacement(policy="primary").branch_name(MethodOwner()) == "main"
    assert BranchPlacement(policy="current").branch_name(MethodOwner()) == "topic"


def test_branch_placement_rejects_unknown_policy_and_codec_at_construction():
    with pytest.raises(ValueError, match="unknown branch policy"):
        BranchPlacement(policy="unknown")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unknown ref codec"):
        BranchPlacement(policy="template", template="source/{stem}", codec="unknown")  # type: ignore[arg-type]


def test_reversible_path_placements_reject_one_way_codecs_at_construction():
    with pytest.raises(ValueError, match="requires a reversible ref codec"):
        FlatYamlPlacement("claims", DemoRef, ref_field="name", codec="slug")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="requires a reversible ref codec"):
        HashScatteredYamlPlacement(
            "claims",
            DemoRef,
            ref_field="name",
            codec="safe_slug",  # type: ignore[arg-type]
            filename_mode="encoded_ref",
        )


def test_hash_scattered_yaml_rejects_unknown_filename_mode_at_construction():
    with pytest.raises(ValueError, match="unknown hash-scattered filename_mode"):
        HashScatteredYamlPlacement(
            "claims",
            DemoRef,
            ref_field="name",
            filename_mode="unknown",  # type: ignore[arg-type]
        )


def test_flat_yaml_ref_from_loaded_anchors_to_knowledge_root(tmp_path):
    placement = FlatYamlPlacement("claims", DemoRef, ref_field="name")
    root = tmp_path / "repo" / "claims" / "stuff"
    source_path = root / "claims" / "alpha.yaml"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("name: alpha\n")

    loaded = LoadedDocument(
        filename="alpha.yaml",
        source_path=source_path,
        knowledge_root=root,
    )

    assert placement.ref_from_loaded(loaded) == DemoRef("alpha")


def test_flat_yaml_ref_from_loaded_rejects_nested_path_relative_to_root(tmp_path):
    placement = FlatYamlPlacement("claims", DemoRef, ref_field="name")
    root = tmp_path / "repo"
    source_path = root / "claims" / "stuff" / "claims" / "alpha.yaml"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("name: alpha\n")

    loaded = LoadedDocument(
        filename="alpha.yaml",
        source_path=source_path,
        knowledge_root=root,
    )

    with pytest.raises(ValueError, match="expected direct child"):
        placement.ref_from_loaded(loaded)


def test_hash_scattered_encoded_ref_recovers_from_loaded_path(tmp_path):
    placement = HashScatteredYamlPlacement[Owner, DemoRef](
        namespace="stances",
        ref_factory=DemoRef,
        ref_field="name",
        codec="colon_to_double_underscore",
        filename_mode="encoded_ref",
    )
    path = tmp_path / "repo" / "stances" / "aa" / "bb" / "claim__a.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("name: claim:a\n")

    loaded = LoadedDocument(
        filename="claim__a.yaml",
        source_path=path,
        knowledge_root=tmp_path / "repo",
    )

    assert placement.ref_from_loaded(loaded) == DemoRef("claim:a")


def test_hash_scattered_digest_loaded_recovery_requires_document_ref(tmp_path):
    placement = HashScatteredYamlPlacement[Owner, DemoRef](
        namespace="claims",
        ref_factory=DemoRef,
        ref_field="name",
        filename_mode="digest",
    )
    path = tmp_path / "repo" / "claims" / "aa" / "bb" / "opaque.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("name: alpha\n")

    assert placement.ref_from_loaded(
        LoadedDocument(
            filename="opaque.yaml",
            source_path=path,
            knowledge_root=tmp_path / "repo",
            document=DemoRef("alpha"),
        )
    ) == DemoRef("alpha")

    with pytest.raises(TypeError, match="cannot recover refs"):
        placement.ref_from_loaded(
            LoadedDocument(
                filename="opaque.yaml",
                source_path=path,
                knowledge_root=tmp_path / "repo",
            )
        )

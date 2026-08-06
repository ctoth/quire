"""Regression tests for declarative charter lifecycle projections."""

from __future__ import annotations

from typing import Annotated

import pytest

from quire.charter_class import CharterDoc, charter, charter_field, column
from quire.charters import FamilyCharter
from quire.documents.schema import DocumentSchemaError
from quire.lifecycle import ConflictPolicy, FamilyState, FamilyTransition


# --- charter WITH lifecycle states but NO field-level states ----------------


@charter(
    key="rule_proposal",
    name="rule_proposal",
    contract_version="2026.05.25",
    placement=".derived/rule_proposal",
    identity_field="id",
    semantic="propstore.world",
    extra_columns=(column("id", str, primary_key=True, nullable=False),),
    states=(
        FamilyState("proposed", document_label="proposal"),
        FamilyState("canonical", document_label="canonical", terminal=True),
    ),
    transitions=(
        FamilyTransition(
            name="promote_proposal",
            source="proposed",
            target="canonical",
            conflict_policy=ConflictPolicy.REPLACE,
        ),
    ),
)
class RuleProposalDocument(CharterDoc):
    predicate: str
    body: str


def test_lifecycle_state_returns_decorated_class_for_any_state() -> None:
    charter_obj: FamilyCharter = RuleProposalDocument.__charter__
    none_doc = charter_obj.generated_document(None)
    proposed_doc = charter_obj.generated_document("proposed")
    canonical_doc = charter_obj.generated_document("canonical")
    # All three are literally the decorated class.
    assert none_doc is RuleProposalDocument
    assert proposed_doc is RuleProposalDocument
    assert canonical_doc is RuleProposalDocument
    assert proposed_doc is none_doc is canonical_doc


def test_lifecycle_state_document_has_unfiltered_fields() -> None:
    charter_obj: FamilyCharter = RuleProposalDocument.__charter__
    proposed_doc = charter_obj.generated_document("proposed")
    assert proposed_doc.__struct_fields__ == ("predicate", "body")


# --- charter WITH a genuine field-level state projection --------------------


@charter(
    key="projected",
    name="projected",
    contract_version="2026.05.25",
    placement=".derived/projected",
    identity_field="id",
    semantic="propstore.world",
    extra_columns=(column("id", str, primary_key=True, nullable=False),),
    states=(FamilyState("proposed"), FamilyState("canonical")),
)
class ProjectedDocument(CharterDoc):
    always: str
    proposed_only: Annotated[
        str | None, charter_field(states=frozenset({"proposed"}))
    ] = None
    canonical_only: Annotated[
        str | None, charter_field(states=frozenset({"canonical"}))
    ] = None


def test_field_level_state_still_returns_class_for_none() -> None:
    charter_obj: FamilyCharter = ProjectedDocument.__charter__
    assert charter_obj.generated_document(None) is ProjectedDocument


@pytest.mark.parametrize(
    ("state", "expected_fields"),
    [
        ("proposed", ("always", "proposed_only")),
        ("canonical", ("always", "canonical_only")),
    ],
)
def test_field_level_state_projects_declarative_document(
    state: str,
    expected_fields: tuple[str, ...],
) -> None:
    charter_obj: FamilyCharter = ProjectedDocument.__charter__
    assert charter_obj.generated_document(state).__struct_fields__ == expected_fields


def test_state_specific_codecs_round_trip_strictly() -> None:
    charter_obj: FamilyCharter = ProjectedDocument.__charter__

    proposed_type = charter_obj.generated_document("proposed")
    proposed_codec = charter_obj.document_codec("proposed")
    proposed = proposed_type(always="shared", proposed_only="draft")
    proposed_payload = proposed_codec.encode(proposed)
    assert proposed_codec.decode(
        proposed_payload,
        proposed_type,
        source="proposed.yaml",
    ) == proposed

    canonical_type = charter_obj.generated_document("canonical")
    canonical_codec = charter_obj.document_codec("canonical")
    canonical = canonical_type(always="shared", canonical_only="published")
    canonical_payload = canonical_codec.encode(canonical)
    assert canonical_codec.decode(
        canonical_payload,
        canonical_type,
        source="canonical.yaml",
    ) == canonical

    with pytest.raises(DocumentSchemaError, match="unknown field"):
        proposed_codec.decode(
            canonical_payload,
            proposed_type,
            source="proposed.yaml",
        )


def test_declarative_state_projection_matches_imperative_charter() -> None:
    declarative: FamilyCharter = ProjectedDocument.__charter__
    imperative = FamilyCharter(
        family=declarative.family,
        model=declarative.model,
        fields=declarative.fields,
        states=declarative.states,
    )

    for state in ("proposed", "canonical"):
        assert (
            declarative.generated_document(state).__struct_fields__
            == imperative.generated_document(state).__struct_fields__
        )


def test_unknown_lifecycle_state_fails_at_charter_boundary() -> None:
    declarative: FamilyCharter = ProjectedDocument.__charter__
    imperative = FamilyCharter(
        family=declarative.family,
        model=declarative.model,
        fields=declarative.fields,
        states=declarative.states,
    )

    for charter_obj in (declarative, imperative):
        with pytest.raises(KeyError, match="unknown lifecycle state: missing"):
            charter_obj.generated_document("missing")
        with pytest.raises(KeyError, match="unknown lifecycle state: missing"):
            charter_obj.document_codec("missing")

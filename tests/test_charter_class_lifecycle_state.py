"""Regression tests for declarative-charter generated_document(state=...).

propstore's proposal->canonical lifecycle (rules/predicates/stances) calls
``generated_document(state="proposed")`` via ``lifecycle._bind_context``. No
propstore CharterField is state-conditional (none declares ``states=``), so the
old hand-written charter returned a document IDENTICAL to ``state=None`` for any
state. A declarative charter must match that parity:

- ``state=None`` -> the decorated class;
- ``state="..."`` with NO field-level ``states`` -> STILL the decorated class
  (no field is filtered, so the projected document equals the full document);
- ``state="..."`` with a field that DOES declare ``states`` -> NotImplementedError
  (genuine field-level projection; propstore never does this).
"""

from __future__ import annotations

from typing import Annotated

import pytest

from quire.charter_class import CharterDoc, charter, charter_field, column
from quire.charters import FamilyCharter
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
)
class ProjectedDocument(CharterDoc):
    always: str
    canonical_only: Annotated[
        str | None, charter_field(states=frozenset({"canonical"}))
    ] = None


def test_field_level_state_still_returns_class_for_none() -> None:
    charter_obj: FamilyCharter = ProjectedDocument.__charter__
    assert charter_obj.generated_document(None) is ProjectedDocument


def test_field_level_state_raises_for_non_none_state() -> None:
    charter_obj: FamilyCharter = ProjectedDocument.__charter__
    with pytest.raises(NotImplementedError):
        charter_obj.generated_document("canonical")

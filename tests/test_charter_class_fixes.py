"""Regression tests for the three @charter fixes surfaced by the `sameas` family.

Fix 1: generated_document(state=None) returns the DECORATED CLASS itself (so the
       contract-manifest document_schema label equals the public class name).
Fix 2: the generated SQLAlchemy model is bound into the defining module's globals
       under its __qualname__ (so a dotted-path loader can resolve it).
Fix 3: __charter__ / __charter_model__ are typed ClassVars (no # type: ignore
       needed at the call site).
"""

from __future__ import annotations

import sys
from typing import Annotated

import msgspec
import pytest

from quire.charter_class import CharterDoc, charter, charter_field, column
from quire.charters import FamilyCharter


# A realistic stand-in for the propstore `sameas` family: a public class name
# (SameAsAssertionDocument) distinct from the family name (sameas_assertion).
@charter(
    key="sameas_assertion",
    name="sameas_assertion",
    contract_version="2026.05.25",
    placement=".derived/sameas_assertion",
    identity_field="id",
    semantic="propstore.world",
    extra_columns=(column("id", str, primary_key=True, nullable=False),),
)
class SameAsAssertionDocument(CharterDoc):
    left: str
    right: str
    payload: Annotated[dict[str, str] | None, charter_field(json=True)] = None


# --- Fix 1: generated_document returns the decorated class -------------------


def test_generated_document_is_the_decorated_class() -> None:
    charter_obj: FamilyCharter = SameAsAssertionDocument.__charter__
    generated = charter_obj.generated_document()
    # The strongest acceptance: it is literally the class.
    assert generated is SameAsAssertionDocument
    # ...therefore the manifest label equals the public class name (no drift to
    # a family-name-derived "Sameas_assertionDocument").
    assert generated.__name__ == "SameAsAssertionDocument"


def test_generated_document_fields_match_the_class() -> None:
    charter_obj: FamilyCharter = SameAsAssertionDocument.__charter__
    generated = charter_obj.generated_document()
    assert generated.__struct_fields__ == SameAsAssertionDocument.__struct_fields__
    gen_fields = {f.name: (f.type, f.default) for f in msgspec.structs.fields(generated)}
    cls_fields = {
        f.name: (f.type, f.default)
        for f in msgspec.structs.fields(SameAsAssertionDocument)
    }
    assert gen_fields == cls_fields


def test_document_codec_uses_the_decorated_class() -> None:
    charter_obj: FamilyCharter = SameAsAssertionDocument.__charter__
    codec = charter_obj.document_codec()
    doc = SameAsAssertionDocument(left="a", right="b", payload={"k": "v"})
    encoded = codec.encode(doc)
    decoded = codec.decode(encoded, SameAsAssertionDocument, source="x.yaml")
    assert decoded == doc
    assert type(decoded) is SameAsAssertionDocument


def test_generated_document_with_state_raises() -> None:
    charter_obj: FamilyCharter = SameAsAssertionDocument.__charter__
    with pytest.raises(NotImplementedError):
        charter_obj.generated_document("proposed")


def test_validators_still_run_on_decoded_class() -> None:
    def _reject_equal(document: msgspec.Struct) -> None:
        if getattr(document, "left") == getattr(document, "right"):
            raise ValueError("left and right must differ")

    @charter(
        key="distinct_pair",
        name="distinct_pair",
        contract_version="2026.05.25",
        placement=".derived/distinct_pair",
        identity_field="left",
        semantic="propstore.world",
        validators=(_reject_equal,),
    )
    class DistinctPairDocument(CharterDoc):
        left: str
        right: str

    charter_obj: FamilyCharter = DistinctPairDocument.__charter__
    assert charter_obj.generated_document() is DistinctPairDocument
    codec = charter_obj.document_codec()

    good = DistinctPairDocument(left="a", right="b")
    assert codec.decode(codec.encode(good), DistinctPairDocument, source="x.yaml") == good

    bad = msgspec.yaml.encode({"left": "a", "right": "a"})
    with pytest.raises(ValueError, match="must differ"):
        codec.decode(bad, DistinctPairDocument, source="x.yaml")


def test_user_post_init_runs_validators_via_super() -> None:
    # msgspec captures __post_init__ at class creation, so a subclass that
    # defines its own __post_init__ must call super().__post_init__() for the
    # charter validators to run — the idiomatic Python contract.
    calls: list[str] = []

    def _charter_validator(document: msgspec.Struct) -> None:
        calls.append("charter_validator")

    @charter(
        key="logged_pair",
        name="logged_pair",
        contract_version="2026.05.25",
        placement=".derived/logged_pair",
        identity_field="left",
        semantic="propstore.world",
        validators=(_charter_validator,),
    )
    class LoggedPairDocument(CharterDoc):
        left: str
        right: str

        def __post_init__(self) -> None:
            super().__post_init__()
            calls.append("user_post_init")

    LoggedPairDocument(left="a", right="b")
    assert calls == ["charter_validator", "user_post_init"]


# --- Fix 2: model bound into the defining module's globals -------------------


def test_model_is_bound_into_defining_module() -> None:
    model = SameAsAssertionDocument.__charter_model__
    module = sys.modules[SameAsAssertionDocument.__module__]
    resolved = getattr(module, model.__qualname__)
    assert resolved is model
    # Dotted-path resolution mirrors propstore's schema loader.
    from importlib import import_module

    loaded = getattr(import_module(model.__module__), model.__qualname__)
    assert loaded is model


# --- Fix 3: typed ClassVars (compile-time; this exercises the access) --------


def test_charter_classvars_accessible_without_ignore() -> None:
    # If __charter__/__charter_model__ were untyped, pyright would require a
    # # type: ignore here. The runtime access also confirms they are set.
    charter_obj: FamilyCharter = SameAsAssertionDocument.__charter__
    model_type: type = SameAsAssertionDocument.__charter_model__
    assert isinstance(charter_obj, FamilyCharter)
    assert isinstance(model_type, type)

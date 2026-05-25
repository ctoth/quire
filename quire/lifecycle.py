from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Literal

import msgspec


class ConflictPolicy(StrEnum):
    FAIL = "fail"
    SKIP = "skip"
    REPLACE = "replace"
    MERGE = "merge"


@dataclass(frozen=True)
class FamilyState:
    name: str
    document_label: str | None = None
    terminal: bool = False


@dataclass(frozen=True)
class FamilyTransition:
    name: str
    source: str
    target: str
    guard: str | None = None
    materializer: str | None = None
    merge: str | None = None
    conflict_policy: ConflictPolicy = ConflictPolicy.FAIL


@dataclass(frozen=True)
class FamilyRecordWrite:
    family: str
    identity: str
    state: str
    record: object


@dataclass(frozen=True)
class FamilyRecordDelete:
    family: str
    identity: str
    state: str | None = None


@dataclass(frozen=True)
class TransitionDiagnostic:
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    field: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class TransitionContext:
    source_state: str | None = None
    target_state: str | None = None
    transition: FamilyTransition | None = None
    source_document_type: type[msgspec.Struct] | None = None
    target_document_type: type[msgspec.Struct] | None = None
    repository_id: str | None = None
    actor: str | None = None
    references: object | None = None
    existing_target_identities: frozenset[str] = frozenset()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TransitionGuardResult:
    is_allowed: bool
    diagnostics: tuple[TransitionDiagnostic, ...] = ()

    @classmethod
    def allowed_result(
        cls,
        *diagnostics: TransitionDiagnostic,
    ) -> TransitionGuardResult:
        return cls(is_allowed=True, diagnostics=diagnostics)

    @classmethod
    def allowed(cls, *diagnostics: TransitionDiagnostic) -> TransitionGuardResult:
        return cls.allowed_result(*diagnostics)

    @classmethod
    def blocked(
        cls,
        *diagnostics: TransitionDiagnostic,
    ) -> TransitionGuardResult:
        return cls(is_allowed=False, diagnostics=diagnostics)


@dataclass(frozen=True)
class TransitionPlan:
    writes: tuple[FamilyRecordWrite, ...]
    deletes: tuple[FamilyRecordDelete, ...] = ()
    diagnostics: tuple[TransitionDiagnostic, ...] = ()


@dataclass(frozen=True)
class TransitionResult:
    transition: FamilyTransition
    source_record: object
    target_records: tuple[FamilyRecordWrite, ...]
    plan: TransitionPlan
    diagnostics: tuple[TransitionDiagnostic, ...] = ()
    skipped: bool = False

    @property
    def succeeded(self) -> bool:
        return (
            not self.skipped
            and not any(diagnostic.severity == "error" for diagnostic in self.diagnostics)
        )


@dataclass(frozen=True)
class TransitionBatchResult:
    items: tuple[TransitionResult, ...]

    @property
    def succeeded(self) -> bool:
        return all(item.succeeded for item in self.items)


TransitionGuard = Callable[[object, TransitionContext], TransitionGuardResult]
TransitionMaterializer = Callable[[object, TransitionContext], TransitionPlan]
TransitionMerge = Callable[[object, TransitionContext, TransitionPlan], TransitionPlan]


@dataclass(frozen=True)
class LifecycleCallbacks:
    guards: Mapping[str, TransitionGuard] = field(default_factory=dict)
    materializers: Mapping[str, TransitionMaterializer] = field(default_factory=dict)
    mergers: Mapping[str, TransitionMerge] = field(default_factory=dict)


class LifecycleError(ValueError):
    pass


def run_transition(
    *,
    charter: Any,
    transition: str,
    record: object,
    context: TransitionContext,
    callbacks: LifecycleCallbacks,
) -> TransitionResult:
    lifecycle_transition = _transition(charter, transition)
    bound_context = _bind_context(charter, lifecycle_transition, context)

    guard_result = _run_guard(
        lifecycle_transition,
        record,
        bound_context,
        callbacks,
    )
    if not guard_result.is_allowed:
        return _blocked_result(
            lifecycle_transition,
            record,
            guard_result.diagnostics,
        )

    conflict_identity = _record_identity(charter, record)
    if conflict_identity in bound_context.existing_target_identities:
        conflict_result = _handle_conflict(
            charter,
            lifecycle_transition,
            record,
            bound_context,
            callbacks,
        )
        if conflict_result is not None:
            return conflict_result

    plan = _run_materializer(
        charter,
        lifecycle_transition,
        record,
        bound_context,
        callbacks,
    )
    return TransitionResult(
        transition=lifecycle_transition,
        source_record=record,
        target_records=plan.writes,
        plan=plan,
        diagnostics=plan.diagnostics,
    )


def run_transition_batch(
    *,
    charter: Any,
    transition: str,
    records: Sequence[object],
    context: TransitionContext,
    callbacks: LifecycleCallbacks,
) -> TransitionBatchResult:
    return TransitionBatchResult(
        items=tuple(
            run_transition(
                charter=charter,
                transition=transition,
                record=record,
                context=context,
                callbacks=callbacks,
            )
            for record in records
        )
    )


def validate_lifecycle_definition(
    states: tuple[FamilyState, ...],
    transitions: tuple[FamilyTransition, ...],
) -> None:
    state_names = [state.name for state in states]
    duplicate_states = _duplicates(state_names)
    if duplicate_states:
        raise ValueError(f"duplicate lifecycle state: {duplicate_states[0]}")

    transition_names = [transition.name for transition in transitions]
    duplicate_transitions = _duplicates(transition_names)
    if duplicate_transitions:
        raise ValueError(f"duplicate lifecycle transition: {duplicate_transitions[0]}")

    known_states = frozenset(state_names)
    for transition in transitions:
        if transition.source not in known_states:
            raise ValueError(
                f"unknown source state {transition.source!r} for lifecycle transition "
                f"{transition.name!r}"
            )
        if transition.target not in known_states:
            raise ValueError(
                f"unknown target state {transition.target!r} for lifecycle transition "
                f"{transition.name!r}"
            )


def _transition(charter: Any, name: str) -> FamilyTransition:
    if hasattr(charter, "transition"):
        return charter.transition(name)
    for transition in charter.transitions:
        if transition.name == name:
            return transition
    raise LifecycleError(f"unknown lifecycle transition: {name}")


def _bind_context(
    charter: Any,
    transition: FamilyTransition,
    context: TransitionContext,
) -> TransitionContext:
    return replace(
        context,
        source_state=transition.source,
        target_state=transition.target,
        transition=transition,
        source_document_type=charter.generated_document(transition.source),
        target_document_type=charter.generated_document(transition.target),
    )


def _run_guard(
    transition: FamilyTransition,
    record: object,
    context: TransitionContext,
    callbacks: LifecycleCallbacks,
) -> TransitionGuardResult:
    if transition.guard is None:
        return TransitionGuardResult.allowed()
    guard = callbacks.guards.get(transition.guard)
    if guard is None:
        raise LifecycleError(f"missing lifecycle guard callback: {transition.guard}")
    result = guard(record, context)
    if not isinstance(result, TransitionGuardResult):
        raise LifecycleError(f"lifecycle guard {transition.guard} returned {type(result).__name__}")
    return result


def _run_materializer(
    charter: Any,
    transition: FamilyTransition,
    record: object,
    context: TransitionContext,
    callbacks: LifecycleCallbacks,
) -> TransitionPlan:
    if transition.materializer is None:
        identity = _record_identity(charter, record)
        return TransitionPlan(
            writes=(
                FamilyRecordWrite(
                    family=charter.family.name,
                    identity=identity,
                    state=transition.target,
                    record=record,
                ),
            )
        )
    materializer = callbacks.materializers.get(transition.materializer)
    if materializer is None:
        raise LifecycleError(
            f"missing lifecycle materializer callback: {transition.materializer}"
        )
    plan = materializer(record, context)
    if not isinstance(plan, TransitionPlan):
        raise LifecycleError(
            f"lifecycle materializer {transition.materializer} returned "
            f"{type(plan).__name__}"
        )
    return plan


def _handle_conflict(
    charter: Any,
    transition: FamilyTransition,
    record: object,
    context: TransitionContext,
    callbacks: LifecycleCallbacks,
) -> TransitionResult | None:
    diagnostic = TransitionDiagnostic(
        code="lifecycle.conflict",
        severity="error",
        message=(
            f"target identity already exists for {charter.family.name}: "
            f"{_record_identity(charter, record)}"
        ),
    )
    if transition.conflict_policy is ConflictPolicy.FAIL:
        return _blocked_result(transition, record, (diagnostic,))
    if transition.conflict_policy is ConflictPolicy.SKIP:
        return TransitionResult(
            transition=transition,
            source_record=record,
            target_records=(),
            plan=TransitionPlan(writes=()),
            diagnostics=(replace(diagnostic, severity="info"),),
            skipped=True,
        )
    if transition.conflict_policy is ConflictPolicy.REPLACE:
        return None
    if transition.merge is None:
        raise LifecycleError(
            f"lifecycle transition {transition.name!r} requires merge callback "
            "for MERGE conflict policy"
        )
    merge = callbacks.mergers.get(transition.merge)
    if merge is None:
        raise LifecycleError(f"missing lifecycle merge callback: {transition.merge}")
    plan = _run_materializer(charter, transition, record, context, callbacks)
    merged_plan = merge(record, context, plan)
    if not isinstance(merged_plan, TransitionPlan):
        raise LifecycleError(
            f"lifecycle merge {transition.merge} returned {type(merged_plan).__name__}"
        )
    return TransitionResult(
        transition=transition,
        source_record=record,
        target_records=merged_plan.writes,
        plan=merged_plan,
        diagnostics=merged_plan.diagnostics,
    )


def _blocked_result(
    transition: FamilyTransition,
    record: object,
    diagnostics: tuple[TransitionDiagnostic, ...],
) -> TransitionResult:
    return TransitionResult(
        transition=transition,
        source_record=record,
        target_records=(),
        plan=TransitionPlan(writes=()),
        diagnostics=diagnostics,
    )


def _record_identity(charter: Any, record: object) -> str:
    identity_field = charter.family.identity_field
    if identity_field is None:
        raise LifecycleError(f"family {charter.family.name} has no identity field")
    identity = getattr(record, identity_field, None)
    if not isinstance(identity, str):
        raise LifecycleError(
            f"record identity field {identity_field!r} must be a string"
        )
    return identity


def _duplicates(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)

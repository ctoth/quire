from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pytest

from quire.artifacts import ArtifactFamily, FlatYamlPlacement
from quire.charters import CharterField, FamilyCharter
from quire.families import FamilyDefinition
from quire.lifecycle import (
    ConflictPolicy,
    FamilyRecordWrite,
    FamilyState,
    FamilyTransition,
    LifecycleCallbacks,
    LifecycleError,
    TransitionContext,
    TransitionDiagnostic,
    TransitionGuardResult,
    TransitionPlan,
    run_transition,
    run_transition_batch,
)
from quire.versions import VersionId


class DemoFamily(str, Enum):
    TASKS = "tasks"
    NOTES = "notes"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    draft_note: str | None = None
    canonical_title: str | None = None


@dataclass(frozen=True)
class Note:
    id: str
    body: str


def _family(name: DemoFamily) -> FamilyDefinition[object, DemoFamily, str, object]:
    version = VersionId("2026.05.25", allow_placeholder=False)
    return FamilyDefinition(
        key=name,
        name=name.value,
        contract_version=version,
        artifact_family=ArtifactFamily(
            name=f"{name.value}_artifact",
            contract_version=version,
            doc_type=object,
            placement=FlatYamlPlacement(name.value, str),
        ),
        identity_field="id",
    )


def _task_charter() -> FamilyCharter:
    return FamilyCharter(
        family=_family(DemoFamily.TASKS),
        model=Task,
        states=(
            FamilyState("draft"),
            FamilyState("canonical"),
            FamilyState("archived", terminal=True),
        ),
        transitions=(
            FamilyTransition(
                "publish",
                source="draft",
                target="canonical",
                guard="task.publish.guard",
                materializer="task.publish.materialize",
            ),
            FamilyTransition(
                "archive",
                source="canonical",
                target="archived",
                conflict_policy=ConflictPolicy.SKIP,
            ),
            FamilyTransition(
                "replace",
                source="draft",
                target="canonical",
                materializer="task.publish.materialize",
                conflict_policy=ConflictPolicy.REPLACE,
            ),
            FamilyTransition(
                "merge",
                source="draft",
                target="canonical",
                materializer="task.publish.materialize",
                conflict_policy=ConflictPolicy.MERGE,
            ),
        ),
        fields=(
            CharterField("id", str, primary_key=True),
            CharterField("title", str, states=frozenset({"draft"})),
            CharterField("draft_note", str | None, states=frozenset({"draft"})),
            CharterField("canonical_title", str | None, states=frozenset({"canonical"})),
        ),
    )


def _note_charter() -> FamilyCharter:
    return FamilyCharter(
        family=_family(DemoFamily.NOTES),
        model=Note,
        states=(FamilyState("draft"), FamilyState("published")),
        transitions=(FamilyTransition("publish", source="draft", target="published"),),
        fields=(
            CharterField("id", str, primary_key=True),
            CharterField("body", str),
        ),
    )


def test_family_charter_rejects_invalid_lifecycle_metadata() -> None:
    with pytest.raises(ValueError, match="duplicate lifecycle state"):
        FamilyCharter(
            family=_family(DemoFamily.TASKS),
            model=Task,
            states=(FamilyState("draft"), FamilyState("draft")),
            fields=(CharterField("id", str),),
        )

    with pytest.raises(ValueError, match="unknown source state"):
        FamilyCharter(
            family=_family(DemoFamily.TASKS),
            model=Task,
            states=(FamilyState("draft"),),
            transitions=(FamilyTransition("publish", source="missing", target="draft"),),
            fields=(CharterField("id", str),),
        )

    with pytest.raises(ValueError, match="unknown target state"):
        FamilyCharter(
            family=_family(DemoFamily.TASKS),
            model=Task,
            states=(FamilyState("draft"),),
            transitions=(FamilyTransition("publish", source="draft", target="missing"),),
            fields=(CharterField("id", str),),
        )

    with pytest.raises(ValueError, match="duplicate lifecycle transition"):
        FamilyCharter(
            family=_family(DemoFamily.TASKS),
            model=Task,
            states=(FamilyState("draft"), FamilyState("canonical")),
            transitions=(
                FamilyTransition("publish", source="draft", target="canonical"),
                FamilyTransition("publish", source="draft", target="canonical"),
            ),
            fields=(CharterField("id", str),),
        )

    assert not any(key.endswith("_states") for key in vars(_task_charter()))


def test_run_transition_guard_failure_blocks_materializer() -> None:
    calls: list[str] = []

    def guard(record: object, context: TransitionContext) -> TransitionGuardResult:
        assert context.transition is not None
        calls.append(f"guard:{context.transition.name}")
        return TransitionGuardResult.blocked(
            TransitionDiagnostic(
                code="task.title.empty",
                severity="error",
                message="title is required",
                field="title",
            )
        )

    def materializer(record: object, context: TransitionContext) -> TransitionPlan:
        calls.append("materializer")
        return TransitionPlan(writes=())

    result = run_transition(
        charter=_task_charter(),
        transition="publish",
        record=Task(id="task-1", title=""),
        context=TransitionContext(),
        callbacks=LifecycleCallbacks(
            guards={"task.publish.guard": guard},
            materializers={"task.publish.materialize": materializer},
        ),
    )

    assert calls == ["guard:publish"]
    assert result.succeeded is False
    assert result.plan.writes == ()
    assert result.diagnostics[0].code == "task.title.empty"


def test_run_transition_materializes_typed_plan_and_state_documents() -> None:
    def guard(record: object, context: TransitionContext) -> TransitionGuardResult:
        assert context.source_document_type is not None
        assert context.target_document_type is not None
        assert context.source_document_type.__struct_fields__ == ("id", "title", "draft_note")
        assert context.target_document_type.__struct_fields__ == ("id", "canonical_title")
        return TransitionGuardResult.allowed()

    def materializer(record: object, context: TransitionContext) -> TransitionPlan:
        task = record
        assert isinstance(task, Task)
        assert context.target_state is not None
        return TransitionPlan(
            writes=(
                FamilyRecordWrite(
                    family="tasks",
                    identity=task.id,
                    state=context.target_state,
                    record=Task(
                        id=task.id,
                        title=task.title,
                        canonical_title=task.title.upper(),
                    ),
                ),
            )
        )

    result = run_transition(
        charter=_task_charter(),
        transition="publish",
        record=Task(id="task-1", title="ship"),
        context=TransitionContext(actor="tester"),
        callbacks=LifecycleCallbacks(
            guards={"task.publish.guard": guard},
            materializers={"task.publish.materialize": materializer},
        ),
    )

    assert result.succeeded is True
    assert result.plan.writes[0].identity == "task-1"
    assert result.target_records == result.plan.writes


def test_run_transition_rejects_missing_callback_ids() -> None:
    with pytest.raises(LifecycleError, match="missing lifecycle guard callback"):
        run_transition(
            charter=_task_charter(),
            transition="publish",
            record=Task(id="task-1", title="ship"),
            context=TransitionContext(),
            callbacks=LifecycleCallbacks(),
        )


def test_conflict_policies_are_deterministic() -> None:
    charter = _task_charter()
    existing = frozenset({"task-1"})
    record = Task(id="task-1", title="ship")

    fail_result = run_transition(
        charter=charter,
        transition="publish",
        record=record,
        context=TransitionContext(existing_target_identities=existing),
        callbacks=LifecycleCallbacks(
            guards={"task.publish.guard": lambda _record, _context: TransitionGuardResult.allowed()},
            materializers={"task.publish.materialize": _publish_plan},
        ),
    )
    assert fail_result.succeeded is False
    assert fail_result.diagnostics[0].code == "lifecycle.conflict"

    skip_result = run_transition(
        charter=charter,
        transition="archive",
        record=record,
        context=TransitionContext(existing_target_identities=existing),
        callbacks=LifecycleCallbacks(),
    )
    assert skip_result.skipped is True

    replace_result = run_transition(
        charter=charter,
        transition="replace",
        record=record,
        context=TransitionContext(existing_target_identities=existing),
        callbacks=LifecycleCallbacks(materializers={"task.publish.materialize": _publish_plan}),
    )
    assert replace_result.succeeded is True

    with pytest.raises(LifecycleError, match="requires merge callback"):
        run_transition(
            charter=charter,
            transition="merge",
            record=record,
            context=TransitionContext(existing_target_identities=existing),
            callbacks=LifecycleCallbacks(materializers={"task.publish.materialize": _publish_plan}),
        )


def test_run_transition_batch_reports_item_outcomes() -> None:
    def guard(record: object, context: TransitionContext) -> TransitionGuardResult:
        task = record
        assert isinstance(task, Task)
        if not task.title:
            return TransitionGuardResult.blocked(
                TransitionDiagnostic(
                    code="task.title.empty",
                    severity="error",
                    message="title is required",
                )
            )
        return TransitionGuardResult.allowed()

    result = run_transition_batch(
        charter=_task_charter(),
        transition="publish",
        records=(
            Task(id="task-1", title="ship"),
            Task(id="task-2", title=""),
            Task(id="task-3", title="skip"),
        ),
        context=TransitionContext(existing_target_identities=frozenset({"task-3"})),
        callbacks=LifecycleCallbacks(
            guards={"task.publish.guard": guard},
            materializers={"task.publish.materialize": _publish_plan},
        ),
    )

    assert [item.succeeded for item in result.items] == [True, False, False]
    assert result.items[1].diagnostics[0].code == "task.title.empty"
    assert result.items[2].skipped is False
    assert result.items[2].diagnostics[0].code == "lifecycle.conflict"
    assert result.succeeded is False


def test_two_non_claim_families_can_declare_transitions() -> None:
    assert _task_charter().transition("publish").target == "canonical"
    assert _note_charter().transition("publish").target == "published"


def _publish_plan(record: object, context: TransitionContext) -> TransitionPlan:
    task = record
    assert isinstance(task, Task)
    assert context.target_state is not None
    return TransitionPlan(
        writes=(
            FamilyRecordWrite(
                family="tasks",
                identity=task.id,
                state=context.target_state,
                record=Task(
                    id=task.id,
                    title=task.title,
                    canonical_title=task.title,
                ),
            ),
        )
    )

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentdeck.domain.mission import (
    AttemptDecision,
    RecoveryContext,
    RuntimeFact,
    TaskRuntimeState,
    TaskSpec,
    record_handoff,
    record_worker_event,
    release_ready_tasks,
    start_attempt,
)
from agentdeck.domain.verification import (
    EvidenceFact,
    VerificationGrade,
    verify_task,
)


def _task(task_id: str = "build", *, dependencies: tuple[str, ...] = ()) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        objective=f"Complete {task_id}",
        role="worker",
        scope=("src",),
        acceptance_contribution=(f"{task_id} exists",),
        acceptance_criteria=(f"{task_id} tests pass",),
        dependencies=dependencies,
        retry_limit=1,
    )


def test_dependency_release_requires_completed_dependency_and_accepted_handoff() -> None:
    tasks = (_task(), _task("review", dependencies=("build",)))
    states = {"build": "completed", "review": "pending"}

    assert release_ready_tasks(tasks, states, ()) == ()
    assert release_ready_tasks(tasks, states, (("build", "review"),)) == ("review",)
    assert (
        release_ready_tasks(
            tasks,
            {"build": "running", "review": "pending"},
            (("build", "review"),),
        )
        == ()
    )
    assert release_ready_tasks(tasks, {"build": "pending", "review": "pending"}, ()) == ("build",)


def test_attempt_numbers_are_distinct_and_retry_limit_is_bounded() -> None:
    task = _task()

    first = start_attempt(task, "ready", ())
    second = start_attempt(task, "ready", (1,))

    assert first == AttemptDecision(1, TaskRuntimeState.RUNNING, "running")
    assert second.attempt_number == 2
    with pytest.raises(ValueError, match="^task retry limit reached$"):
        start_attempt(task, "ready", (1, 2))
    with pytest.raises(FrozenInstanceError):
        first.attempt_number = 9  # type: ignore[misc]


def test_worker_text_and_turn_completion_never_complete_a_task() -> None:
    text = record_worker_event("running", "running", "worker_message")
    turn = record_worker_event("running", "running", "turn_completed")

    assert text.task_state is TaskRuntimeState.RUNNING
    assert turn.task_state is TaskRuntimeState.AWAITING_VERIFICATION
    assert turn.attempt_state == "awaiting_verification"
    assert text.task_state is not TaskRuntimeState.COMPLETED
    assert turn.task_state is not TaskRuntimeState.COMPLETED
    assert record_worker_event(
        "paused", "paused", "worker_message"
    ).task_state is TaskRuntimeState.PAUSED
    assert record_worker_event(
        "awaiting_verification", "awaiting_verification", "progress"
    ).task_state is TaskRuntimeState.AWAITING_VERIFICATION


def test_safest_precedence_retains_all_facts_and_terminal_is_absorbing() -> None:
    facts = (
        RuntimeFact("task", "permission_conflict", "new operation requested"),
        RuntimeFact("mission", "ambiguous_effect", "effect outcome unknown"),
        RuntimeFact("task", "task_local_pause", "workspace busy"),
    )
    decision = record_worker_event("running", "running", "progress", facts=facts)

    assert decision.mission_state == "paused"
    assert decision.task_state is TaskRuntimeState.PAUSED
    assert decision.facts == facts
    assert decision.reasons == (
        "new operation requested",
        "effect outcome unknown",
        "workspace busy",
    )

    terminal = record_worker_event(
        "failed", "failed", "worker_message", facts=facts
    )
    assert terminal.task_state is TaskRuntimeState.FAILED
    assert terminal.attempt_state == "failed"
    assert terminal.mission_state == "failed"
    assert terminal.facts == facts


def test_worker_completion_fact_is_rejected_and_only_verification_can_complete() -> None:
    with pytest.raises(ValueError, match="^runtime fact invalid$"):
        RuntimeFact("task", "terminal_completed", "worker claims done")
    with pytest.raises(ValueError, match="^worker event invalid$"):
        record_worker_event("running", "running", "completed")


def test_failed_attempt_retries_only_with_durable_no_effect_and_all_bounds() -> None:
    available = RecoveryContext(
        attempt_number=1,
        task_retry_limit=1,
        mission_attempt_count=1,
        mission_max_attempts=4,
        mission_retry_count=0,
        mission_max_retries=2,
        mission_recovery_count=0,
        mission_max_recoveries=1,
        task_budget_used=4,
        task_budget_limit=10,
        mission_budget_used=4,
        mission_budget_limit=20,
    )
    safe = record_worker_event(
        "running",
        "running",
        "failed",
        facts=(RuntimeFact("task", "proven_no_effect", "protocol receipt"),),
        effect_status="proven_no_effect",
        recovery=available,
    )
    assert safe.task_state is TaskRuntimeState.READY
    assert safe.attempt_state == "failed"
    assert safe.mission_state == "running"
    assert safe.recovery_allowed is True

    exhausted = record_worker_event(
        "running",
        "running",
        "failed",
        facts=(RuntimeFact("task", "proven_no_effect", "protocol receipt"),),
        effect_status="proven_no_effect",
        recovery=RecoveryContext(
            1, 1, 1, 4, 0, 2, 1, 1, 4, 10, 4, 20
        ),
    )
    assert exhausted.task_state is TaskRuntimeState.FAILED
    assert exhausted.recovery_allowed is False

    ambiguous = record_worker_event(
        "running",
        "running",
        "failed",
        facts=(RuntimeFact("mission", "ambiguous_effect", "unknown effect"),),
        effect_status="ambiguous_effect",
        recovery=available,
    )
    assert ambiguous.task_state is TaskRuntimeState.PAUSED
    assert ambiguous.mission_state == "paused"
    assert ambiguous.recovery_allowed is False

    absorbed = record_worker_event(
        "paused",
        "paused",
        "failed",
        facts=(
            RuntimeFact("mission", "permission_conflict", "scope conflict"),
            RuntimeFact("task", "proven_no_effect", "protocol receipt"),
        ),
        effect_status="proven_no_effect",
        recovery=available,
    )
    assert absorbed.task_state is TaskRuntimeState.PAUSED
    assert absorbed.attempt_state == "paused"
    assert absorbed.mission_state == "paused"
    assert absorbed.recovery_allowed is False


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            (RuntimeFact("session", "session_takeover", "human owns session"),),
            (TaskRuntimeState.PAUSED, "paused", "running", "session", False, False),
        ),
        (
            (
                RuntimeFact("session", "session_takeover", "human owns session"),
                RuntimeFact("mission", "permission_conflict", "scope conflict"),
            ),
            (TaskRuntimeState.PAUSED, "paused", "paused", "mission", False, False),
        ),
        (
            (
                RuntimeFact("session", "session_takeover", "human owns session"),
                RuntimeFact("task", "terminal_failed", "attempt failed"),
            ),
            (TaskRuntimeState.FAILED, "failed", "failed", "mission", False, False),
        ),
    ],
)
def test_takeover_scope_and_terminal_precedence(facts, expected) -> None:
    decision = record_worker_event("running", "running", "progress", facts=facts)
    assert (
        decision.task_state,
        decision.attempt_state,
        decision.mission_state,
        decision.effective_scope,
        decision.dispatch_allowed,
        decision.automation_allowed,
    ) == expected
    assert decision.facts == facts


def test_handoff_is_only_accepted_from_completed_declared_dependency() -> None:
    destination = _task("review", dependencies=("build",))

    assert record_handoff("build", "completed", destination).accepted is True
    with pytest.raises(ValueError, match="^handoff source incomplete$"):
        record_handoff("build", "awaiting_verification", destination)
    with pytest.raises(ValueError, match="^handoff dependency invalid$"):
        record_handoff("other", "completed", destination)


def test_verification_requires_durable_facts_for_every_frozen_criterion() -> None:
    task = _task()
    passed = verify_task(
        task,
        (
            EvidenceFact(
                criterion="build tests pass",
                fact="check_passed",
                reason="pytest exit zero",
            ),
        ),
    )
    failed = verify_task(
        task,
        (
            EvidenceFact(
                criterion="build tests pass",
                fact="check_failed",
                reason="pytest failed",
            ),
        ),
    )
    unavailable = verify_task(task, ())

    assert passed.aggregate_state is TaskRuntimeState.COMPLETED
    assert passed.criteria[0].grade is VerificationGrade.PASS
    assert failed.aggregate_state is TaskRuntimeState.FAILED
    assert failed.criteria[0].grade is VerificationGrade.FAIL
    assert unavailable.aggregate_state is TaskRuntimeState.PAUSED
    assert unavailable.criteria[0].grade is VerificationGrade.UNAVAILABLE
    with pytest.raises(ValueError, match="^verification evidence invalid$"):
        EvidenceFact(
            criterion="build tests pass",
            fact="reviewer_approved",
            reason="review text says done",
        )


def test_runtime_and_verification_fact_batches_are_bounded() -> None:
    runtime_fact = RuntimeFact("task", "task_local_pause", "bounded")
    with pytest.raises(ValueError, match="^worker event invalid$"):
        record_worker_event(
            "running",
            "running",
            "progress",
            facts=(runtime_fact,) * 4_097,
        )

    evidence = EvidenceFact(
        criterion="build tests pass",
        fact="check_passed",
        reason="bounded",
    )
    with pytest.raises(ValueError, match="^verification input invalid$"):
        verify_task(_task(), (evidence,) * 2_049)

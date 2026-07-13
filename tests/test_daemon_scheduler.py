from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from agentdeck.daemon.scheduler import (
    ATTEMPT_STATES,
    DECISION_KINDS,
    HANDOFF_STATES,
    LINEAGE_STATES,
    MISSION_STATES,
    OWNERSHIP_STATES,
    PERMISSION_STATES,
    REPLY_STATES,
    SNAPSHOT_STATES,
    STEP_STATES,
    SchedulerDecision,
    SchedulerEffects,
    SchedulerError,
    SchedulerFacts,
    run_scheduler_once,
    schedule_gate,
)


MISSION_ID = "mis_0123456789ab"
STEP_ID = "step_1"
ATTEMPT_ID = "mat_0123456789ab"


def facts(**changes: object) -> SchedulerFacts:
    values: dict[str, object] = {
        "mission_id": MISSION_ID,
        "mission_state": "running",
        "step_id": STEP_ID,
        "step_state": "active",
        "attempt_id": ATTEMPT_ID,
        "attempt_state": "running",
        "reply_state": "none",
        "handoff_state": "none",
        "permission_state": "none",
        "worker_ready": True,
        "next_step_eligible": False,
        "all_steps_completed": False,
        "snapshot_state": "valid",
        "lineage_state": "valid",
        "ownership_state": "owned",
        "active_attempt_count": 1,
        "blocker": None,
    }
    values.update(changes)
    return SchedulerFacts(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scheduler_facts", "expected"),
    [
        (
            facts(
                step_state="pending",
                attempt_id=None,
                attempt_state="none",
                active_attempt_count=0,
            ),
            "prepare_dispatch",
        ),
        (facts(attempt_state="prepared"), "dispatch_prepared"),
        (facts(attempt_state="submitted"), "await_worker"),
        (facts(attempt_state="running"), "await_worker"),
        (
            facts(
                attempt_state="succeeded",
                active_attempt_count=0,
                reply_state="received",
            ),
            "validate_reply",
        ),
        (
            facts(
                attempt_state="succeeded",
                active_attempt_count=0,
                reply_state="validated",
                handoff_state="pending",
            ),
            "record_handoff",
        ),
        (
            facts(
                attempt_state="succeeded",
                active_attempt_count=0,
                reply_state="validated",
                handoff_state="recorded",
                next_step_eligible=True,
            ),
            "activate_next",
        ),
        (facts(permission_state="pending"), "wait_human"),
        (
            facts(
                mission_state="pending_confirmation",
                step_id=None,
                step_state="none",
                attempt_id=None,
                attempt_state="none",
                active_attempt_count=0,
                snapshot_state="missing",
            ),
            "wait_human",
        ),
        (facts(attempt_state="ambiguous", active_attempt_count=0), "wait_ambiguity"),
        (
            facts(
                mission_state="running",
                step_id=None,
                step_state="none",
                attempt_id=None,
                attempt_state="none",
                active_attempt_count=0,
                all_steps_completed=True,
            ),
            "complete_mission",
        ),
        (
            facts(
                mission_state="idle",
                mission_id=None,
                step_id=None,
                step_state="none",
                attempt_id=None,
                attempt_state="none",
                active_attempt_count=0,
            ),
            "idle",
        ),
    ],
)
def test_scheduler_selects_exactly_one_transition(
    scheduler_facts: SchedulerFacts, expected: str
) -> None:
    assert schedule_gate(scheduler_facts).kind == expected


@pytest.mark.parametrize(
    ("changes", "blocker"),
    [
        ({"snapshot_state": "drift"}, "frozen snapshot drift"),
        ({"lineage_state": "missing"}, "missing Mission lineage"),
        ({"ownership_state": "conflict"}, "Worker ownership conflict"),
        ({"active_attempt_count": 2}, "conflicting active attempts"),
        ({"blocker": "policy denied"}, "policy denied"),
        ({"step_state": "failed", "blocker": "step failed"}, "step failed"),
        (
            {"attempt_state": "failed", "active_attempt_count": 0},
            "Mission attempt failed",
        ),
        ({"reply_state": "invalid"}, "Worker reply is invalid"),
        ({"permission_state": "denied"}, "permission denied"),
    ],
)
def test_scheduler_fails_closed_on_blockers(
    changes: dict[str, object], blocker: str
) -> None:
    decision = schedule_gate(facts(**changes))
    assert decision.kind == "blocked"
    assert decision.blocker == blocker


def test_integrity_failures_take_priority_over_waiting_states() -> None:
    decision = schedule_gate(
        facts(
            permission_state="pending",
            snapshot_state="drift",
            active_attempt_count=3,
        )
    )
    assert decision.kind == "blocked"
    assert decision.blocker == "frozen snapshot drift"


def test_scheduler_decision_carries_exact_lineage() -> None:
    decision = schedule_gate(facts(attempt_state="prepared"))
    assert decision == SchedulerDecision(
        kind="dispatch_prepared",
        mission_id=MISSION_ID,
        step_id=STEP_ID,
        attempt_id=ATTEMPT_ID,
        blocker=None,
    )


def test_scheduler_state_enums_match_durable_mission_and_attempt_records() -> None:
    assert MISSION_STATES == frozenset(
        {
            "idle",
            "pending_confirmation",
            "preparing",
            "running",
            "completed",
            "stopped",
            "interrupted",
        }
    )
    assert ATTEMPT_STATES == frozenset(
        {
            "none",
            "prepared",
            "submitted",
            "running",
            "completed",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "ambiguous",
        }
    )
    assert STEP_STATES == frozenset(
        {"none", "pending", "active", "completed", "failed", "blocked"}
    )
    assert REPLY_STATES == frozenset(
        {"none", "pending", "received", "validated", "invalid"}
    )
    assert HANDOFF_STATES == frozenset({"none", "pending", "recorded"})
    assert PERMISSION_STATES == frozenset(
        {"none", "pending", "approved", "denied", "expired"}
    )
    assert SNAPSHOT_STATES == frozenset({"valid", "missing", "drift"})
    assert LINEAGE_STATES == frozenset({"valid", "missing"})
    assert OWNERSHIP_STATES == frozenset({"owned", "conflict"})
    assert DECISION_KINDS == frozenset(
        {
            "prepare_dispatch",
            "dispatch_prepared",
            "await_worker",
            "validate_reply",
            "record_handoff",
            "activate_next",
            "wait_human",
            "wait_ambiguity",
            "blocked",
            "complete_mission",
            "idle",
        }
    )


def test_scheduler_does_not_mutate_input_mapping_or_nested_values() -> None:
    raw: dict[str, Any] = {
        "mission_id": MISSION_ID,
        "mission_state": "running",
        "step_id": STEP_ID,
        "step_state": "pending",
        "attempt_id": None,
        "attempt_state": "none",
        "reply_state": "none",
        "handoff_state": "none",
        "permission_state": "none",
        "worker_ready": True,
        "next_step_eligible": False,
        "all_steps_completed": False,
        "snapshot_state": "valid",
        "lineage_state": "valid",
        "ownership_state": "owned",
        "active_attempt_count": 0,
        "blocker": None,
    }
    before = deepcopy(raw)
    parsed = SchedulerFacts.from_mapping(raw)
    assert raw == before
    assert schedule_gate(parsed).kind == "prepare_dispatch"
    assert raw == before
    raw["mission_state"] = "blocked"
    assert parsed.mission_state == "running"


def test_scheduler_rejects_mutable_nested_wrong_type_without_mutation() -> None:
    raw = facts().summary()
    nested: dict[str, list[str]] = {"unexpected": []}
    raw["worker_ready"] = nested
    before = deepcopy(raw)
    with pytest.raises(SchedulerError, match="invalid scheduler facts"):
        SchedulerFacts.from_mapping(raw)
    assert raw == before


def test_scheduler_facts_and_decisions_are_immutable() -> None:
    scheduler_facts = facts()
    decision = schedule_gate(scheduler_facts)
    with pytest.raises(FrozenInstanceError):
        scheduler_facts.mission_state = "blocked"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.kind = "idle"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mission_state", "unknown"),
        ("step_state", "unknown"),
        ("attempt_state", "unknown"),
        ("reply_state", "unknown"),
        ("handoff_state", "unknown"),
        ("permission_state", "unknown"),
        ("snapshot_state", "unknown"),
        ("lineage_state", "unknown"),
        ("ownership_state", "unknown"),
        ("worker_ready", 1),
        ("next_step_eligible", 0),
        ("all_steps_completed", "false"),
        ("active_attempt_count", True),
        ("active_attempt_count", -1),
        ("blocker", ""),
    ],
)
def test_scheduler_facts_reject_unknown_states_and_wrong_types(
    field: str, value: object
) -> None:
    with pytest.raises(SchedulerError, match="invalid scheduler facts"):
        replace(facts(), **{field: value})


def test_scheduler_facts_mapping_requires_exact_fields() -> None:
    raw = facts().summary()
    assert list(raw) == [
        "mission_id",
        "mission_state",
        "step_id",
        "step_state",
        "attempt_id",
        "attempt_state",
        "reply_state",
        "handoff_state",
        "permission_state",
        "worker_ready",
        "next_step_eligible",
        "all_steps_completed",
        "snapshot_state",
        "lineage_state",
        "ownership_state",
        "active_attempt_count",
        "blocker",
    ]
    for changed in (
        {key: value for key, value in raw.items() if key != "worker_ready"},
        {**raw, "extra": "no"},
    ):
        with pytest.raises(SchedulerError, match="invalid scheduler facts"):
            SchedulerFacts.from_mapping(changed)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"mission_id": None}, "mission identity"),
        ({"step_id": None}, "step identity"),
        ({"attempt_id": None}, "attempt identity"),
        ({"attempt_state": "none"}, "active attempt count"),
    ],
)
def test_scheduler_rejects_incomplete_active_lineage(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(SchedulerError, match=message):
        facts(**changes)


def test_scheduler_rejects_attempt_without_step() -> None:
    with pytest.raises(SchedulerError, match="step identity"):
        facts(step_id=None, step_state="none")


def test_unconfirmed_mission_rejects_step_or_attempt_facts() -> None:
    with pytest.raises(SchedulerError, match="unconfirmed Mission"):
        facts(mission_state="pending_confirmation")


def test_scheduler_rejects_ids_with_wrong_shape() -> None:
    for field, value in (
        ("mission_id", "../mission"),
        ("step_id", "step_0"),
        ("attempt_id", "mat_nothex"),
    ):
        with pytest.raises(SchedulerError, match="identity"):
            replace(facts(), **{field: value})


@pytest.mark.parametrize(
    "mission_state",
    ["completed", "stopped", "interrupted"],
)
def test_terminal_mission_has_no_next_transition(mission_state: str) -> None:
    terminal = facts(
        mission_state=mission_state,
        step_id=None,
        step_state="none",
        attempt_id=None,
        attempt_state="none",
        active_attempt_count=0,
    )
    decision = schedule_gate(terminal)
    assert decision.kind == "idle"
    assert decision.mission_id == MISSION_ID


def test_success_without_a_reply_fails_closed() -> None:
    decision = schedule_gate(
        facts(attempt_state="succeeded", active_attempt_count=0)
    )
    assert decision.kind == "blocked"
    assert decision.blocker == "Worker result is missing"


def test_unready_worker_fails_closed_before_preparation() -> None:
    decision = schedule_gate(
        facts(
            step_state="pending",
            attempt_id=None,
            attempt_state="none",
            active_attempt_count=0,
            worker_ready=False,
        )
    )
    assert decision.kind == "blocked"
    assert decision.blocker == "Worker is not ready"


def test_completion_cannot_skip_an_active_attempt() -> None:
    decision = schedule_gate(facts(all_steps_completed=True))
    assert decision.kind == "blocked"
    assert decision.blocker == "completion conflicts with active attempt"


@pytest.mark.parametrize(
    ("changes", "blocker"),
    [
        (
            {
                "attempt_state": "succeeded",
                "active_attempt_count": 0,
                "reply_state": "none",
            },
            "completion has unverified Worker result",
        ),
        (
            {
                "step_state": "active",
                "attempt_id": None,
                "attempt_state": "none",
                "active_attempt_count": 0,
            },
            "completion conflicts with current step",
        ),
        (
            {
                "attempt_state": "succeeded",
                "active_attempt_count": 0,
                "reply_state": "received",
            },
            "completion has unverified Worker result",
        ),
        (
            {
                "attempt_state": "succeeded",
                "active_attempt_count": 0,
                "reply_state": "validated",
                "handoff_state": "pending",
            },
            "completion has unrecorded handoff",
        ),
        (
            {
                "attempt_state": "succeeded",
                "active_attempt_count": 0,
                "reply_state": "validated",
                "handoff_state": "recorded",
            },
            "completion conflicts with current step",
        ),
    ],
)
def test_completion_requires_cleared_verified_step_facts(
    changes: dict[str, object], blocker: str
) -> None:
    decision = schedule_gate(facts(all_steps_completed=True, **changes))
    assert decision.kind == "blocked"
    assert decision.blocker == blocker


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_state": "prepared", "reply_state": "received"},
        {"attempt_state": "submitted", "reply_state": "received"},
        {"attempt_state": "running", "reply_state": "received"},
        {
            "attempt_state": "submitted",
            "reply_state": "validated",
            "handoff_state": "pending",
        },
        {
            "attempt_state": "running",
            "reply_state": "validated",
            "handoff_state": "recorded",
            "next_step_eligible": True,
        },
    ],
)
def test_nonterminal_attempt_cannot_advance_reply_or_handoff(
    changes: dict[str, object],
) -> None:
    decision = schedule_gate(facts(**changes))
    assert decision.kind == "blocked"
    assert decision.blocker == "Worker reply precedes successful terminal attempt"


@pytest.mark.parametrize(
    "changes",
    [
        {
            "step_state": "pending",
            "attempt_id": None,
            "attempt_state": "none",
            "active_attempt_count": 0,
            "reply_state": "received",
        },
        {"handoff_state": "recorded"},
        {"next_step_eligible": True},
    ],
)
def test_scheduler_rejects_incoherent_reply_and_handoff_facts(
    changes: dict[str, object],
) -> None:
    with pytest.raises(SchedulerError, match="invalid scheduler facts"):
        facts(**changes)


def test_scheduler_is_deterministic() -> None:
    scheduler_facts = facts(
        attempt_state="succeeded",
        active_attempt_count=0,
        reply_state="received",
    )
    assert {schedule_gate(scheduler_facts) for _ in range(100)} == {
        SchedulerDecision(
            kind="validate_reply",
            mission_id=MISSION_ID,
            step_id=STEP_ID,
            attempt_id=ATTEMPT_ID,
            blocker=None,
        )
    }


class RecordingEffects(SchedulerEffects[str]):
    def __init__(self, *, error: Exception | None = None) -> None:
        self.decisions: list[SchedulerDecision] = []
        self.error = error

    def apply(self, decision: SchedulerDecision) -> str:
        self.decisions.append(decision)
        if self.error is not None:
            raise self.error
        return decision.kind


def test_runner_applies_exactly_one_decision() -> None:
    effects = RecordingEffects()
    result = run_scheduler_once(facts(attempt_state="prepared"), effects)
    assert result == "dispatch_prepared"
    assert [item.kind for item in effects.decisions] == ["dispatch_prepared"]


def test_runner_does_not_retry_effect_exception() -> None:
    effects = RecordingEffects(error=RuntimeError("effect failed"))
    with pytest.raises(RuntimeError, match="effect failed"):
        run_scheduler_once(facts(attempt_state="prepared"), effects)
    assert len(effects.decisions) == 1


def test_scheduler_decision_rejects_unknown_kind_and_incoherent_blocker() -> None:
    with pytest.raises(SchedulerError, match="invalid scheduler decision"):
        SchedulerDecision("unknown", None, None, None, None)  # type: ignore[arg-type]
    with pytest.raises(SchedulerError, match="invalid scheduler decision"):
        SchedulerDecision("idle", None, None, None, "unexpected")
    with pytest.raises(SchedulerError, match="invalid scheduler decision"):
        SchedulerDecision("blocked", MISSION_ID, STEP_ID, ATTEMPT_ID, None)
    with pytest.raises(SchedulerError, match="invalid scheduler decision"):
        SchedulerDecision("complete_mission", None, None, None, None)
    with pytest.raises(SchedulerError, match="invalid scheduler decision"):
        SchedulerDecision("await_worker", MISSION_ID, None, ATTEMPT_ID, None)
    with pytest.raises(SchedulerError, match="invalid scheduler decision"):
        SchedulerDecision(
            "complete_mission", MISSION_ID, STEP_ID, ATTEMPT_ID, None
        )
